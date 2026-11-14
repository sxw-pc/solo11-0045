from __future__ import annotations

from copy import deepcopy

import pytest

from unitcontract.contract.loader import load_contract, merge_contract_documents
from unitcontract.contract.schema import ContractValidationError, build_contract


def valid_payload() -> dict:
    return {
        "schema_version": "1",
        "name": "test-contract",
        "description": "schema test",
        "fields": [
            {
                "field": "energy",
                "dimension": "energy",
                "canonical_unit": "J",
                "allowed_units": ["J", "kWh"],
                "precision": 2,
                "rounding": "half_even",
                "range": {"min": 0, "max": 1000},
            }
        ],
    }


def mutate(**changes) -> dict:
    payload = deepcopy(valid_payload())
    payload["fields"][0].update(changes)
    return payload


def codes(payload: dict) -> set[str]:
    with pytest.raises(ContractValidationError) as caught:
        build_contract(payload, "bad.yaml")
    return {issue.code for issue in caught.value.issues}


def test_valid_contract_builds(contract):
    assert contract.name == "synthetic-facility-readings"
    assert contract.field_names() == (
        "energy",
        "power",
        "mass",
        "length",
        "temperature",
        "volume",
        "count",
    )
    energy = contract.field_map["energy"]
    assert energy.canonical_unit == "J"
    assert energy.range_max.numerator == 5000000000
    assert build_contract(valid_payload(), "ok.yaml").source_name == "ok.yaml"


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"dimension": "currency"}, "E_DIM_UNKNOWN"),
        ({"canonical_unit": "m"}, "E_UNIT_DIM_MISMATCH"),
        ({"canonical_unit": "stone"}, "E_UNIT_UNKNOWN"),
        ({"allowed_units": ["J", "stone"]}, "E_UNIT_UNKNOWN"),
        ({"allowed_units": ["J", "m"]}, "E_UNIT_DIM_MISMATCH"),
        ({"allowed_units": ["kWh"]}, "E_CANON_NOT_IN_ALLOWED"),
        ({"precision": -1}, "E_PRECISION_INVALID"),
        ({"precision": True}, "E_PRECISION_INVALID"),
        ({"rounding": "bankers"}, "E_ROUNDING_UNKNOWN"),
        ({"range": {"min": 10, "max": 1}}, "E_RANGE_INVALID"),
        ({"range": {"min": "low"}}, "E_RANGE_INVALID"),
        ({"range": [0, 1]}, "E_RANGE_INVALID"),
    ],
)
def test_field_errors_have_stable_codes(changes, expected):
    assert expected in codes(mutate(**changes))


def test_required_contract_parts_have_codes():
    payload = valid_payload()
    payload["name"] = ""
    with pytest.raises(ContractValidationError) as caught:
        build_contract(payload, "no-name.yaml")
    assert "E_CONTRACT_NAME" in {issue.code for issue in caught.value.issues}

    payload = valid_payload()
    payload["fields"][0].pop("canonical_unit")
    with pytest.raises(ContractValidationError) as caught:
        build_contract(payload, "no-canonical.yaml")
    assert "E_CANON_MISSING" in {issue.code for issue in caught.value.issues}

    payload = valid_payload()
    payload["fields"][0]["allowed_units"] = []
    with pytest.raises(ContractValidationError) as caught:
        build_contract(payload, "no-allowed.yaml")
    assert "E_ALLOWED_MISSING" in {issue.code for issue in caught.value.issues}

    payload = valid_payload()
    payload["fields"][0]["allowed_units"] = ["J", 42]
    with pytest.raises(ContractValidationError) as caught:
        build_contract(payload, "bad-allowed.yaml")
    assert "E_ALLOWED_ITEM" in {issue.code for issue in caught.value.issues}


def test_duplicate_and_missing_names_are_located():
    payload = valid_payload()
    payload["fields"].append(deepcopy(payload["fields"][0]))
    with pytest.raises(ContractValidationError) as caught:
        build_contract(payload, "duplicate.yaml")
    assert "E_FIELD_DUPLICATE" in {issue.code for issue in caught.value.issues}
    assert all("duplicate.yaml" in issue.location for issue in caught.value.issues)

    payload = valid_payload()
    payload["fields"][0].pop("field")
    with pytest.raises(ContractValidationError) as caught:
        build_contract(payload, "unnamed.yaml")
    assert caught.value.issues[0].code == "E_FIELD_NAME"
    assert "fields[0]" in caught.value.issues[0].location


def test_root_and_version_errors():
    with pytest.raises(ContractValidationError) as caught:
        build_contract([], "root.yaml")
    assert caught.value.issues[0].code == "E_SCHEMA_ROOT"

    payload = valid_payload()
    payload["schema_version"] = "99"
    payload["fields"] = []
    with pytest.raises(ContractValidationError) as caught:
        build_contract(payload, "version.yaml")
    assert {"E_SCHEMA_VERSION", "E_FIELDS_MISSING"} <= {
        issue.code for issue in caught.value.issues
    }


def test_defaults_are_merged_into_fields(contract, registry):
    payload = valid_payload()
    payload["defaults"] = {"precision": 4, "rounding": "half_up"}
    payload["fields"][0].pop("precision")
    payload["fields"][0]["rounding"] = "truncate"
    built = build_contract(payload, "defaults.yaml", registry=registry)
    field = built.field_map["energy"]
    assert field.precision == 4
    assert field.rounding == "truncate"


def test_overlay_merge_by_field_name(registry):
    base = valid_payload()
    overlay = {
        "name": "overlaid",
        "fields": [
            {"field": "energy", "precision": 5},
            {"field": "count", "dimension": "count", "canonical_unit": "count",
             "allowed_units": ["count"], "precision": 0},
        ],
    }
    merged = merge_contract_documents(base, overlay)
    built = build_contract(merged, "overlay.yaml", registry=registry)
    assert built.name == "overlaid"
    assert built.field_map["energy"].precision == 5
    assert built.field_map["energy"].allowed_units == ("J", "kWh")
    assert "count" in built.field_map


def test_overlay_only_overrides_explicit_keys(registry):
    base = valid_payload()
    overlay = {"fields": [{"field": "energy", "precision": 4, "rounding": "half_up"}]}
    built = build_contract(merge_contract_documents(base, overlay), "ov.yaml", registry=registry)
    field = built.field_map["energy"]
    assert (field.precision, field.rounding) == (4, "half_up")
    # Untouched keys, including the list-valued allowed_units, are inherited.
    assert field.dimension == "energy"
    assert field.canonical_unit == "J"
    assert field.allowed_units == ("J", "kWh")
    assert field.range_min == 0 and field.range_max == 1000


def test_overlay_list_key_keeps_list_semantics(registry):
    base = valid_payload()
    # A scalar or mapping must not be coerced into the inherited list key.
    built = build_contract(
        merge_contract_documents(base, {"fields": [{"field": "energy", "allowed_units": ["J"]}]}),
        "ov.yaml",
        registry=registry,
    )
    assert built.field_map["energy"].allowed_units == ("J",)


def test_overlay_range_is_merged_key_by_key(registry):
    base = valid_payload()
    built = build_contract(
        merge_contract_documents(base, {"fields": [{"field": "energy", "range": {"max": 10}}]}),
        "ov.yaml",
        registry=registry,
    )
    field = built.field_map["energy"]
    assert field.range_min == 0
    assert field.range_max == 10


def test_overlay_mapping_form_is_supported(registry):
    base = valid_payload()
    overlay = {"fields": {"energy": {"precision": 6, "rounding": "truncate"}}}
    built = build_contract(merge_contract_documents(base, overlay), "ov.yaml", registry=registry)
    field = built.field_map["energy"]
    assert (field.precision, field.rounding) == (6, "truncate")
    assert field.allowed_units == ("J", "kWh")


def test_invalid_yaml_reports_location(tmp_path):
    bad = tmp_path / "broken.yaml"
    bad.write_text("fields: [\n", encoding="utf-8")
    with pytest.raises(ContractValidationError) as caught:
        load_contract(bad)
    issue = caught.value.issues[0]
    assert issue.code == "E_YAML_INVALID"
    assert "broken.yaml" in issue.location
