"""Multi-overlay chaining, provenance tracking and backwards compatibility."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from unitcontract.contract.loader import (
    compute_field_key_sources,
    load_contract,
    merge_contract_documents,
)
from unitcontract.contract.schema import (
    BUILTIN_SOURCE,
    ContractValidationError,
    build_contract,
)
from unitcontract.pipeline import execute

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "unitcontract" / "fixtures"
BASE_CONTRACT = FIXTURES / "contracts" / "demo_energy.yaml"
DATA = FIXTURES / "data" / "demo_readings.csv"


def base_payload() -> dict:
    return yaml.safe_load(BASE_CONTRACT.read_text(encoding="utf-8"))


def write_yaml(path: Path, payload: dict) -> Path:
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture()
def team_overlay(tmp_path: Path) -> Path:
    return write_yaml(
        tmp_path / "team.yaml",
        {
            "defaults": {"precision": 4},
            "fields": [
                {"field": "energy", "precision": 3},
                {"field": "mass", "rounding": "truncate"},
            ],
        },
    )


@pytest.fixture()
def site_overlay(tmp_path: Path) -> Path:
    return write_yaml(
        tmp_path / "site.yaml",
        {
            "defaults": {"rounding": "half_up"},
            "fields": [
                {
                    "field": "energy",
                    "canonical_unit": "kWh",
                    "allowed_units": ["J", "kJ", "kWh", "MWh", "GJ"],
                    "range": {"min": 0, "max": 2000},
                },
                {"field": "temperature", "precision": 2},
            ],
        },
    )


def test_chained_overlays_apply_in_order(team_overlay, site_overlay):
    contract = load_contract(BASE_CONTRACT, overlay_paths=[team_overlay, site_overlay])
    energy = contract.field_map["energy"]
    assert energy.canonical_unit == "kWh"  # site overlay wins
    assert energy.precision == 3  # only team sets it
    assert energy.range_max == 2000
    assert contract.field_map["mass"].rounding == "truncate"
    assert contract.field_map["temperature"].precision == 2
    assert contract.overlays == ("team.yaml", "site.yaml")


def test_later_overlay_wins_on_overlapping_keys(tmp_path):
    first = write_yaml(tmp_path / "a.yaml", {"fields": [{"field": "energy", "precision": 7}]})
    second = write_yaml(tmp_path / "b.yaml", {"fields": [{"field": "energy", "precision": 9}]})
    contract = load_contract(BASE_CONTRACT, overlay_paths=[first, second])
    assert contract.field_map["energy"].precision == 9
    contract = load_contract(BASE_CONTRACT, overlay_paths=[second, first])
    assert contract.field_map["energy"].precision == 7


def test_chained_merge_equals_manual_composition(team_overlay, site_overlay):
    chained = load_contract(BASE_CONTRACT, overlay_paths=[team_overlay, site_overlay])
    composed_overlay = merge_contract_documents(
        yaml.safe_load(team_overlay.read_text(encoding="utf-8")),
        yaml.safe_load(site_overlay.read_text(encoding="utf-8")),
    )
    manual_doc = merge_contract_documents(base_payload(), composed_overlay)
    manual = build_contract(manual_doc, str(BASE_CONTRACT))
    assert chained.as_dict() == manual.as_dict()


def test_overlay_only_overrides_explicit_keys(team_overlay):
    contract = load_contract(BASE_CONTRACT, overlay_paths=[team_overlay])
    energy = contract.field_map["energy"]
    assert energy.precision == 3  # overridden
    assert energy.canonical_unit == "J"  # inherited from baseline
    assert energy.allowed_units == ("J", "kJ", "kWh", "MWh", "GJ")
    assert energy.rounding == "half_even"


@pytest.fixture()
def sparse_base(tmp_path: Path) -> Path:
    """Baseline where some keys only live in defaults, not in field entries."""
    return write_yaml(
        tmp_path / "sparse.yaml",
        {
            "name": "defaults-test",
            "defaults": {"precision": 2, "rounding": "half_even"},
            "fields": [
                {
                    "field": "energy",
                    "dimension": "energy",
                    "canonical_unit": "J",
                    "allowed_units": ["J", "kWh"],
                },
                {
                    "field": "count",
                    "dimension": "count",
                    "canonical_unit": "count",
                    "allowed_units": ["count"],
                    "precision": 0,
                },
            ],
        },
    )


def test_defaults_merge_order_is_deterministic(tmp_path, sparse_base):
    first = write_yaml(tmp_path / "a.yaml", {"defaults": {"precision": 6, "rounding": "truncate"}})
    second = write_yaml(tmp_path / "b.yaml", {"defaults": {"precision": 8}})
    contract = load_contract(sparse_base, overlay_paths=[first, second])
    # energy has no field-level precision/rounding anywhere, so the merged
    # defaults apply and the later overlay wins per key.
    energy = contract.field_map["energy"]
    assert energy.precision == 8
    assert energy.rounding == "truncate"
    # field-level entries still beat every defaults layer
    assert contract.field_map["count"].precision == 0


def test_defaults_provenance_follows_the_winning_document(tmp_path, sparse_base):
    first = write_yaml(tmp_path / "a.yaml", {"defaults": {"precision": 6, "rounding": "truncate"}})
    second = write_yaml(tmp_path / "b.yaml", {"defaults": {"precision": 8}})
    contract = load_contract(sparse_base, overlay_paths=[first, second])
    energy = contract.field_map["energy"]
    assert energy.source_for("precision") == "b.yaml"
    assert energy.source_for("rounding") == "a.yaml"
    assert energy.source_for("dimension") == "sparse.yaml"
    # baseline field-level precision beats overlay defaults
    assert contract.field_map["count"].source_for("precision") == "sparse.yaml"


def test_field_key_sources_track_the_chain(team_overlay, site_overlay):
    contract = load_contract(BASE_CONTRACT, overlay_paths=[team_overlay, site_overlay])
    energy = contract.field_map["energy"]
    assert energy.source_for("canonical_unit") == "site.yaml"
    assert energy.source_for("range") == "site.yaml"
    assert energy.source_for("precision") == "team.yaml"
    assert energy.source_for("dimension") == "demo_energy.yaml"
    assert energy.source_for("field") == "demo_energy.yaml"
    mass = contract.field_map["mass"]
    assert mass.source_for("rounding") == "team.yaml"
    assert mass.source_for("precision") == "demo_energy.yaml"
    power = contract.field_map["power"]
    assert set(power.sources_dict().values()) == {"demo_energy.yaml"}


def test_builtin_source_for_keys_absent_everywhere():
    payload = {
        "name": "minimal",
        "fields": [
            {
                "field": "count",
                "dimension": "count",
                "canonical_unit": "count",
                "allowed_units": ["count"],
            }
        ],
    }
    contract = build_contract(payload, "minimal.yaml")
    field = contract.field_map["count"]
    assert field.source_for("precision") == BUILTIN_SOURCE
    assert field.source_for("rounding") == BUILTIN_SOURCE
    assert field.source_for("range") == BUILTIN_SOURCE
    assert field.source_for("dimension") == "minimal.yaml"


def test_new_field_introduced_by_overlay(tmp_path):
    overlay = write_yaml(
        tmp_path / "extra.yaml",
        {
            "fields": [
                {
                    "field": "duration",
                    "dimension": "time",
                    "canonical_unit": "s",
                    "allowed_units": ["s", "min"],
                    "precision": 1,
                }
            ]
        },
    )
    contract = load_contract(BASE_CONTRACT, overlay_paths=[overlay])
    duration = contract.field_map["duration"]
    assert duration.source_for("field") == "extra.yaml"
    assert duration.source_for("dimension") == "extra.yaml"
    # baseline defaults still supply keys the overlay field entry omits
    assert duration.source_for("rounding") == "demo_energy.yaml"
    assert duration.source_for("range") == BUILTIN_SOURCE
    # baseline fields still come first, overlay-only fields append
    assert contract.field_names()[-1] == "duration"


def test_single_overlay_path_remains_supported(team_overlay):
    contract = load_contract(BASE_CONTRACT, overlay_path=team_overlay)
    assert contract.field_map["energy"].precision == 3
    assert contract.overlays == ("team.yaml",)
    # singular form is applied before any plural entries
    second = team_overlay.with_name("second.yaml")
    write_yaml(second, {"fields": [{"field": "energy", "precision": 9}]})
    contract = load_contract(BASE_CONTRACT, overlay_path=team_overlay, overlay_paths=[second])
    assert contract.field_map["energy"].precision == 9
    assert contract.overlays == ("team.yaml", "second.yaml")


def test_no_overlay_behaves_as_before():
    contract = load_contract(BASE_CONTRACT)
    assert contract.overlays == ()
    energy = contract.field_map["energy"]
    assert set(energy.sources_dict().values()) == {"demo_energy.yaml"}


def test_overlay_root_must_be_mapping(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ContractValidationError) as caught:
        load_contract(BASE_CONTRACT, overlay_paths=[bad])
    issue = caught.value.issues[0]
    assert issue.code == "E_SCHEMA_ROOT"
    assert "bad.yaml" in issue.location


def test_invalid_overlay_yaml_reports_location(tmp_path):
    broken = tmp_path / "broken.yaml"
    broken.write_text("fields: [\n", encoding="utf-8")
    with pytest.raises(ContractValidationError) as caught:
        load_contract(BASE_CONTRACT, overlay_paths=[broken])
    assert caught.value.issues[0].code == "E_YAML_INVALID"


def test_schema_version_semantics_unchanged_by_overlays(tmp_path):
    overlay = write_yaml(tmp_path / "version.yaml", {"schema_version": "99"})
    with pytest.raises(ContractValidationError) as caught:
        load_contract(BASE_CONTRACT, overlay_paths=[overlay])
    assert "E_SCHEMA_VERSION" in {issue.code for issue in caught.value.issues}


def test_compute_field_key_sources_matches_merge_semantics():
    base = {
        "name": "sparse",
        "defaults": {"rounding": "half_even"},
        "fields": [
            {"field": "energy", "dimension": "energy", "canonical_unit": "J",
             "allowed_units": ["J"]},
        ],
    }
    overlays = [
        ("one.yaml", {"defaults": {"precision": 5}, "fields": [{"field": "energy", "rounding": "truncate"}]}),
        ("two.yaml", {"defaults": {"precision": 7, "rounding": "half_up"}}),
    ]
    sources = compute_field_key_sources("sparse.yaml", base, overlays)
    # field-level beats defaults regardless of document order
    assert sources["energy"]["rounding"] == "one.yaml"
    # pure defaults key: later document wins
    assert sources["energy"]["precision"] == "two.yaml"
    assert sources["energy"]["dimension"] == "sparse.yaml"
    assert sources["energy"]["field"] == "sparse.yaml"


def test_run_id_stable_without_overlays_and_distinct_with(team_overlay):
    plain_one = execute(BASE_CONTRACT, DATA, no_timestamp=True)
    plain_two = execute(BASE_CONTRACT, DATA, no_timestamp=True)
    assert plain_one.run_id == plain_two.run_id
    overlaid = execute(BASE_CONTRACT, DATA, no_timestamp=True, overlay_paths=[team_overlay])
    assert overlaid.run_id != plain_one.run_id
    repeat = execute(BASE_CONTRACT, DATA, no_timestamp=True, overlay_paths=[team_overlay])
    assert repeat.run_id == overlaid.run_id
    # data hash stays on the original pipeline either way
    assert overlaid.data_hash == plain_one.data_hash
    assert overlaid.contract_hash == plain_one.contract_hash


def test_cli_validate_convert_report_with_overlay_chain(tmp_path, team_overlay, site_overlay):
    from unitcontract.cli import main

    out_jsonl = tmp_path / "normalized.jsonl"
    code = main([
        "convert",
        "--contract", str(BASE_CONTRACT),
        "--data", str(DATA),
        "--overlay", str(team_overlay),
        "--overlay", str(site_overlay),
        "--out", str(out_jsonl),
    ])
    assert code == 0
    first = json.loads(out_jsonl.read_text(encoding="utf-8").splitlines()[0])
    # convert output format is unchanged; values follow the chained contract
    assert set(first) == {
        "row", "field", "dimension", "raw_value", "source_unit",
        "canonical_unit", "canonical_value", "precision", "rounding",
    }
    assert first["canonical_unit"] == "kWh"
    assert first["precision"] == 3

    code = main([
        "validate",
        "--contract", str(BASE_CONTRACT),
        "--data", str(DATA),
        "--overlay", str(team_overlay),
        "--no-timestamp",
    ])
    assert code == 1  # violations still drive the exit code

    report_dir = tmp_path / "reports"
    code = main([
        "report",
        "--contract", str(BASE_CONTRACT),
        "--data", str(DATA),
        "--overlay", str(team_overlay),
        "--overlay", str(site_overlay),
        "--out", str(report_dir),
        "--no-timestamp",
    ])
    assert code == 0
    payload = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    sources = payload["extensions"]["field_sources"]
    assert [item["name"] for item in sources["overlays"]] == ["team.yaml", "site.yaml"]
    assert sources["fields"]["energy"]["canonical_unit"] == "site.yaml"
    assert payload["schema_version"] == "1"
