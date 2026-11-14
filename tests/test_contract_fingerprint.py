"""Semantic contract fingerprint: normalisation rules and equivalence classes."""

from __future__ import annotations

import json
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

import pytest

from unitcontract.contract.fingerprint import (
    FINGERPRINT_VERSION,
    build_fingerprint_payload,
    compute_fingerprint,
)
from unitcontract.contract.loader import load_contract, merge_contract_documents
from unitcontract.contract.schema import Contract, ContractField, build_contract
from unitcontract.pipeline import execute

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "unitcontract" / "fixtures" / "contracts" / "demo_energy.yaml"
DATA_PATH = ROOT / "unitcontract" / "fixtures" / "data" / "demo_readings.csv"


def fingerprint_of(payload: dict, registry) -> str:
    return compute_fingerprint(build_contract(payload, "mem.yaml", registry=registry), registry)


def basic_payload() -> dict:
    return {
        "schema_version": "1",
        "name": "fingerprint-test",
        "description": "one field",
        "defaults": {"precision": 2, "rounding": "half_even"},
        "fields": [
            {
                "field": "energy",
                "dimension": "energy",
                "canonical_unit": "J",
                "allowed_units": ["J", "kJ", "kWh", "MWh"],
                "range": {"min": 0, "max": 10},
            }
        ],
    }


def test_fingerprint_is_stable_across_repeated_computation(registry, contract):
    assert compute_fingerprint(contract, registry) == compute_fingerprint(contract, registry)
    first = fingerprint_of(basic_payload(), registry)
    second = fingerprint_of(basic_payload(), registry)
    assert first == second
    assert len(first) == 64
    int(first, 16)


def test_byte_differences_do_not_change_fingerprint(tmp_path, registry):
    base_text = CONTRACT_PATH.read_text(encoding="utf-8")
    # Same semantics, different bytes: comments, blank lines, flow style and a
    # shuffled field list plus reordered allowed_units.
    noisy = """\
# leading comment that did not exist before

schema_version: "1"   # inline comment
name: synthetic-facility-readings
description: >
  Offline synthetic contract covering energy, power, mass, length,
  temperature, volume and count. All units are declared locally.
defaults:
  rounding: half_even
  precision: 2
fields:
  - {field: count, dimension: count, canonical_unit: count, allowed_units: [count], precision: 0, rounding: half_up, range: {min: 0, max: 100000}}
  - field: volume
    dimension: volume
    canonical_unit: L
    allowed_units: [m3, L, mL]
    range: {min: 0, max: 5000}
  - field: temperature
    dimension: temperature
    canonical_unit: C
    allowed_units: [F, K, C]
    precision: 1
    rounding: half_even
    range: {min: -50, max: 60}
  - field: length
    dimension: length
    canonical_unit: m
    allowed_units: [km, m, cm, mm]
    range: {min: 0, max: 10000}
  - field: mass
    dimension: mass
    canonical_unit: kg
    allowed_units: [t, kg, g]
    precision: 3
    rounding: half_up
    range: {min: 0, max: 100000}
  - field: power
    dimension: power
    canonical_unit: W
    allowed_units: [MW, kW, W]
    range: {min: 0, max: 2000000}
  - field: energy
    dimension: energy
    canonical_unit: J
    allowed_units: [GJ, MWh, kWh, kJ, J]
    range: {min: 0, max: 5000000000}
"""
    noisy_path = tmp_path / "noisy.yaml"
    noisy_path.write_text(noisy, encoding="utf-8")
    assert noisy_path.read_bytes() != CONTRACT_PATH.read_bytes()

    original = load_contract(CONTRACT_PATH, registry=registry)
    shuffled = load_contract(noisy_path, registry=registry)
    assert compute_fingerprint(original, registry) == compute_fingerprint(shuffled, registry)

    # Raw byte hashes genuinely differ, which is why the fingerprint is needed.
    from unitcontract.contract.loader import contract_sha256

    assert contract_sha256(CONTRACT_PATH) != contract_sha256(noisy_path)


def test_allowed_units_order_and_duplicates_are_equivalent(registry):
    first = basic_payload()
    second = deepcopy(first)
    second["fields"][0]["allowed_units"] = ["kWh", "J", "MWh", "kJ", "J"]
    assert fingerprint_of(first, registry) == fingerprint_of(second, registry)


def test_field_declaration_order_is_equivalent(registry):
    first = basic_payload()
    other = deepcopy(first["fields"][0])
    other.update(
        {
            "field": "power",
            "dimension": "power",
            "canonical_unit": "W",
            "allowed_units": ["W", "kW"],
        }
    )
    first["fields"].append(other)
    second = deepcopy(first)
    second["fields"].reverse()
    assert fingerprint_of(first, registry) == fingerprint_of(second, registry)


def test_defaults_expansion_matches_explicit_values(registry):
    expanded = basic_payload()
    explicit = deepcopy(expanded)
    for field in explicit["fields"]:
        field.setdefault("precision", 2)
        field.setdefault("rounding", "half_even")
    explicit.pop("defaults")
    assert fingerprint_of(expanded, registry) == fingerprint_of(explicit, registry)


def test_numeric_notation_is_normalised(registry):
    first = basic_payload()
    second = deepcopy(first)
    second["fields"][0]["range"] = {"min": "0.00", "max": "10/1"}
    assert fingerprint_of(first, registry) == fingerprint_of(second, registry)


def test_name_and_description_are_excluded(registry):
    first = basic_payload()
    second = deepcopy(first)
    second["name"] = "another-name"
    second["description"] = "different prose"
    assert fingerprint_of(first, registry) == fingerprint_of(second, registry)


def test_precision_and_rounding_change_fingerprint(registry):
    base = fingerprint_of(basic_payload(), registry)
    changed = basic_payload()
    changed["defaults"]["precision"] = 3
    assert fingerprint_of(changed, registry) != base
    changed = basic_payload()
    changed["defaults"]["rounding"] = "truncate"
    assert fingerprint_of(changed, registry) != base


def test_temperature_range_compared_after_conversion_to_kelvin(registry):
    """Explicit criterion: identical physical interval in K is range-equivalent.

    ``range`` bounds are unit-less in the schema and are interpreted in
    ``canonical_unit``.  The fingerprint converts them to the dimension base
    unit (K) before hashing, so two *samely-configured* fields whose bounds
    differ only by exact C→K re-expression collide only if ``canonical_unit``
    also matches.  canonical_unit remains part of the fingerprint because it
    changes validation and output semantics, so a C contract and a K contract
    over the same physical interval are deliberately **not** equivalent.
    """
    celsius = ContractField(
        field="temperature",
        dimension="temperature",
        canonical_unit="C",
        allowed_units=("C", "K", "F"),
        precision=1,
        rounding="half_even",
        range_min=Fraction(-50),
        range_max=Fraction(60),
    )
    # Same physical interval, expressed in the base unit through a K canonical.
    kelvin = ContractField(
        field="temperature",
        dimension="temperature",
        canonical_unit="K",
        allowed_units=("K", "C", "F"),
        precision=1,
        rounding="half_even",
        range_min=Fraction(4463, 20),  # 223.15
        range_max=Fraction(6663, 20),  # 333.15
    )
    payload_c = build_fingerprint_payload(
        Contract(name="t", description="", fields=(celsius,)), registry
    )
    payload_k = build_fingerprint_payload(
        Contract(name="t", description="", fields=(kelvin,)), registry
    )
    assert payload_c["fields"][0]["range"] == {"min": "4463/20", "max": "6663/20"}
    assert payload_k["fields"][0]["range"] == {"min": "4463/20", "max": "6663/20"}
    assert payload_c["fields"][0]["range"] == payload_k["fields"][0]["range"]

    # The documented rule is "not equivalent when canonical_unit differs".
    assert compute_fingerprint(
        Contract(name="t", description="", fields=(celsius,)), registry
    ) != compute_fingerprint(
        Contract(name="t", description="", fields=(kelvin,)), registry
    )

    # Under the same canonical unit, bounds written with different numeric
    # notation normalise to one K representation.
    celsius_decimal = ContractField(
        field="temperature",
        dimension="temperature",
        canonical_unit="C",
        allowed_units=("C", "K"),
        precision=1,
        rounding="half_even",
        range_min=Fraction("-50.0"),
        range_max=Fraction("60.00"),
    )
    same = build_fingerprint_payload(
        Contract(name="t", description="", fields=(celsius_decimal,)), registry
    )
    assert same["fields"][0]["range"] == {"min": "4463/20", "max": "6663/20"}


def test_fahrenheit_range_normalises_to_kelvin(registry):
    field = ContractField(
        field="temperature",
        dimension="temperature",
        canonical_unit="F",
        allowed_units=("F", "C", "K"),
        precision=1,
        rounding="half_even",
        range_min=Fraction(-58),
        range_max=Fraction(140),
    )
    payload = build_fingerprint_payload(
        Contract(name="t", description="", fields=(field,)), registry
    )
    # -58 F = 223.15 K, 140 F = 333.15 K
    assert payload["fields"][0]["range"] == {"min": "4463/20", "max": "6663/20"}


def test_ratio_range_normalises_to_base_unit(registry):
    field = ContractField(
        field="energy",
        dimension="energy",
        canonical_unit="MWh",
        allowed_units=("MWh", "J"),
        precision=2,
        rounding="half_even",
        range_min=Fraction(0),
        range_max=Fraction(1),
    )
    payload = build_fingerprint_payload(
        Contract(name="t", description="", fields=(field,)), registry
    )
    assert payload["fields"][0]["range"] == {"min": "0", "max": "3600000000"}


def test_degenerate_contracts_do_not_crash(registry):
    empty_units = ContractField(
        field="bare",
        dimension="count",
        canonical_unit="count",
        allowed_units=(),
        precision=0,
        rounding="half_up",
        range_min=None,
        range_max=None,
    )
    contract = Contract(name="", description="", fields=(empty_units,))
    payload = build_fingerprint_payload(contract, registry)
    assert payload["fields"][0]["allowed_units"] == []
    assert payload["fields"][0]["range"] == {"min": None, "max": None}
    assert len(compute_fingerprint(contract, registry)) == 64

    no_fields = Contract(name="", description="", fields=())
    assert len(compute_fingerprint(no_fields, registry)) == 64

    no_range = ContractField(
        field="counted",
        dimension="count",
        canonical_unit="count",
        allowed_units=("count",),
        precision=0,
        rounding="half_up",
    )
    assert build_fingerprint_payload(
        Contract(name="", description="", fields=(no_range,)), registry
    )["fields"][0]["range"] == {"min": None, "max": None}


def test_payload_is_canonical_json(registry, contract):
    payload = build_fingerprint_payload(contract, registry)
    assert payload["fingerprint_version"] == FINGERPRINT_VERSION
    first = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    second = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert first == second
    assert [item["field"] for item in payload["fields"]] == sorted(
        item["field"] for item in payload["fields"]
    )


def test_overlay_equivalent_to_hand_composed_contract(tmp_path, registry):
    base = basic_payload()
    base_path = tmp_path / "base.yaml"
    base_path.write_text(json.dumps(base), encoding="utf-8")

    overlay = {
        "defaults": {"precision": 3},
        "fields": [
            {"field": "energy", "allowed_units": ["kWh", "MWh", "kJ", "J"]},
            {
                "field": "count",
                "dimension": "count",
                "canonical_unit": "count",
                "allowed_units": ["count"],
                "precision": 0,
                "rounding": "half_up",
            },
        ],
    }
    overlay_path = tmp_path / "overlay.yaml"
    overlay_path.write_text(json.dumps(overlay), encoding="utf-8")
    overlaid = load_contract(base_path, registry=registry, overlay_path=overlay_path)

    # Hand-composed equivalent document: same final field configuration,
    # written independently with different ordering/style.
    composed = {
        "schema_version": "1",
        "name": "composed",
        "description": "hand written equivalent",
        "defaults": {"rounding": "half_even", "precision": 3},
        "fields": [
            {
                "field": "count",
                "dimension": "count",
                "canonical_unit": "count",
                "allowed_units": ["count"],
                "precision": 0,
                "rounding": "half_up",
            },
            {
                "field": "energy",
                "dimension": "energy",
                "canonical_unit": "J",
                "allowed_units": ["J", "kJ", "MWh", "kWh"],
                "range": {"max": 10, "min": 0},
            },
        ],
    }
    composed_path = tmp_path / "composed.yaml"
    composed_path.write_text(json.dumps(composed), encoding="utf-8")
    composed_contract = load_contract(composed_path, registry=registry)

    assert compute_fingerprint(overlaid, registry) == compute_fingerprint(
        composed_contract, registry
    )


def test_execute_exposes_fingerprint_and_preserves_raw_hash(tmp_path):
    plain = execute(CONTRACT_PATH, DATA_PATH, no_timestamp=True)
    assert plain.contract_fingerprint == compute_fingerprint(plain.contract)
    from unitcontract.contract.loader import contract_sha256

    assert plain.contract_hash == contract_sha256(CONTRACT_PATH)

    # Semantically equivalent copy with comments and a shuffled field order must
    # yield the same fingerprint but a different raw hash.
    import yaml

    payload = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    payload["fields"] = list(reversed(payload["fields"]))
    payload["description"] = "rewritten prose"
    copy_path = tmp_path / "copy.yaml"
    copy_path.write_text(
        "# a new comment\n" + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    copied = execute(copy_path, DATA_PATH, no_timestamp=True)
    assert copied.contract_fingerprint == plain.contract_fingerprint
    assert copied.contract_hash != plain.contract_hash


def test_overlay_and_composed_equivalence_through_merge(registry):
    base = basic_payload()
    overlay = {"defaults": {"precision": 5}}
    merged = merge_contract_documents(base, overlay)
    direct = build_contract(merged, "direct.yaml", registry=registry)
    rebuilt = build_contract(deepcopy(merged), "other.yaml", registry=registry)
    assert compute_fingerprint(direct, registry) == compute_fingerprint(rebuilt, registry)
