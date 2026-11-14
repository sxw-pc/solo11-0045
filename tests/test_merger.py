from __future__ import annotations

import json
from pathlib import Path

import pytest

from unitcontract.cli import main
from unitcontract.engine.converter import convert_records
from unitcontract.engine.merger import (
    MergeError,
    SiteSpec,
    merge_sites,
)
from unitcontract.engine.reader import DataRow, read_rows
from unitcontract.pipeline import execute
from unitcontract.units.registry import UnitRegistry

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "unitcontract" / "fixtures"
MERGE = FIXTURES / "merge"
DEMO_CONTRACT = FIXTURES / "contracts" / "demo_energy.yaml"
DEMO_DATA = FIXTURES / "data" / "demo_readings.csv"

SITE_A = SiteSpec("a", MERGE / "site_a_contract.yaml", MERGE / "site_a_data.csv")
SITE_B = SiteSpec("b", MERGE / "site_b_contract.yaml", MERGE / "site_b_data.csv")


def merged_lines(result) -> list[dict]:
    return [item.as_dict() for item in result.merged]


def test_merge_reports_true_conflicts_only():
    result = merge_sites([SITE_A, SITE_B])

    # Business keys: site A has 6 rows x 5 fields, site B has 5 x 5; every B key
    # is shared with A, so there are 30 groups.
    assert result.business_keys == 30
    assert result.summary()["conflicts"] == 3
    assert {(item.record_id, item.field) for item in result.conflicts} == {
        ("m002", "energy"),  # 1 kWh vs 2 kWh
        ("m004", "mass"),  # 2 kg vs 3000 g
        ("m006", "volume"),  # 1.0 L vs 2.0 L
    }

    # A conflicting business key is never emitted on either side.
    merged_keys = {(item.record_id, item.field) for item in result.merged}
    assert not merged_keys & {(c.record_id, c.field) for c in result.conflicts}
    assert len(result.merged) == 30 - 3


def test_conflict_entries_carry_both_sites_and_exact_values():
    result = merge_sites([SITE_A, SITE_B])
    conflict = next(item for item in result.conflicts if item.record_id == "m002")
    payload = conflict.as_dict()

    assert payload["reason"] == "canonical_unit_conflict"
    sides = {side["site"]: side for side in payload["sites"]}
    assert sides["a"]["raw_value"] == "1"
    assert sides["a"]["source_unit"] == "kWh"
    assert sides["a"]["canonical_unit"] == "J"
    assert sides["a"]["exact_value"] == "3600000"
    assert sides["b"]["canonical_unit"] == "kJ"
    assert sides["b"]["exact_value"] == "7200"
    # Exact base-unit values make human adjudication unambiguous.
    pair = payload["pairs"][0]
    assert pair["left_exact_base"] == "3600000"
    assert pair["right_exact_base"] == "7200000"


def test_precision_drift_is_counted_but_not_conflicted():
    result = merge_sites([SITE_A, SITE_B])
    drift_keys = {(item.record_id, item.field) for item in result.drift}

    # Hand count: m001 volume (2.5 L rounded 3 vs 2) and count for
    # m001/m002/m003/m004/m006 (integer text vs two-decimal text).
    assert drift_keys == {
        ("m001", "volume"),
        ("m001", "count"),
        ("m002", "count"),
        ("m003", "count"),
        ("m004", "count"),
        ("m006", "count"),
    }
    assert result.precision_drift == 6
    # Drift rows survive in the merged output and never enter the conflict list.
    assert not drift_keys & {(c.record_id, c.field) for c in result.conflicts}
    merged_keys = {(item.record_id, item.field) for item in result.merged}
    assert drift_keys <= merged_keys


def test_different_canonical_units_with_equal_quantities_are_not_conflicts():
    result = merge_sites([SITE_A, SITE_B])
    merged_keys = {(item.record_id, item.field) for item in result.merged}
    # Mass 2000 g == 2 kg and temperature 20 C == 293.15 K.
    assert ("m001", "mass") in merged_keys
    assert ("m001", "temperature") in merged_keys
    # Energy 1.5 kWh == 5400 kJ.
    assert ("m001", "energy") in merged_keys


def test_merge_is_independent_of_pair_order():
    forward = merge_sites([SITE_A, SITE_B])
    reverse = merge_sites([SITE_B, SITE_A])
    assert merged_lines(forward) == merged_lines(reverse)
    assert [c.as_dict() for c in forward.conflicts] == [
        c.as_dict() for c in reverse.conflicts
    ]
    assert forward.summary() == reverse.summary()


def test_merged_values_reproduce_single_site_convert_bytes():
    result = merge_sites([SITE_A, SITE_B])
    site_a = execute(SITE_A.contract_path, SITE_A.data_path, no_timestamp=True)
    site_b = execute(SITE_B.contract_path, SITE_B.data_path, no_timestamp=True)
    known = {
        json.dumps(record.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for record in (*site_a.conversion.records, *site_b.conversion.records)
    }
    for line in merged_lines(result):
        encoded = json.dumps(line, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert encoded in known
        assert set(line) == {
            "row",
            "field",
            "dimension",
            "raw_value",
            "source_unit",
            "canonical_unit",
            "canonical_value",
            "precision",
            "rounding",
        }


def test_single_site_merge_equals_convert_output(tmp_path):
    out = tmp_path / "merged.jsonl"
    conflicts = tmp_path / "conflicts.json"
    assert main([
        "merge",
        "--site", "a", str(MERGE / "site_a_contract.yaml"), str(MERGE / "site_a_data.csv"),
        "--out", str(out),
        "--conflicts", str(conflicts),
    ]) == 0
    convert_out = tmp_path / "convert.jsonl"
    assert main([
        "convert",
        "--contract", str(MERGE / "site_a_contract.yaml"),
        "--data", str(MERGE / "site_a_data.csv"),
        "--out", str(convert_out),
    ]) == 0
    # No conflicts and no precision drift, so the merged record set is exactly
    # the single-site convert record set (merge groups by business key, convert
    # is row-major, hence set rather than line-position equality).
    merged = out.read_text(encoding="utf-8").splitlines()
    converted = convert_out.read_text(encoding="utf-8").splitlines()
    # No conflicts and no precision drift: the merged stream is byte-for-byte
    # the single-site convert stream, in the same row/field order.
    assert merged == converted
    payload = json.loads(conflicts.read_text(encoding="utf-8"))
    assert payload["conflicts"] == []
    assert payload["precision_drift"] == []
    assert payload["summary"]["records_normalized"] == 30


def test_degenerate_sites_are_skipped_and_counted(tmp_path):
    out = tmp_path / "merged.jsonl"
    conflicts = tmp_path / "conflicts.json"
    code = main([
        "merge",
        "--site", "a", str(SITE_A.contract_path), str(SITE_A.data_path),
        "--site", "empty", str(MERGE / "degenerate_allowed_contract.yaml"), str(MERGE / "degenerate_data.csv"),
        "--site", "nodefaults", str(MERGE / "degenerate_no_defaults_contract.yaml"), str(MERGE / "degenerate_data.csv"),
        "--out", str(out),
        "--conflicts", str(conflicts),
    ])
    assert code == 0
    payload = json.loads(conflicts.read_text(encoding="utf-8"))
    summary = payload["summary"]
    assert summary["sites_ok"] == 1
    assert summary["sites_skipped"] == 2
    assert summary["skipped_site_codes"] == {
        "E_ALLOWED_MISSING": 1,
        "E_CANON_NOT_IN_ALLOWED": 1,
        "E_SCHEMA_ROOT": 1,
    }
    skipped = {item["site"]: item for item in summary["skipped_sites"]}
    assert skipped["empty"]["codes"] == ["E_ALLOWED_MISSING", "E_CANON_NOT_IN_ALLOWED"]
    assert skipped["nodefaults"]["codes"] == ["E_SCHEMA_ROOT"]


def test_degenerate_site_does_not_hide_other_schema_errors(tmp_path):
    # An empty allowed_units site is skipped, and every schema code is recorded.
    result = merge_sites(
        [SiteSpec("empty", MERGE / "degenerate_allowed_contract.yaml", MERGE / "degenerate_data.csv")]
    )
    assert result.skipped_sites[0].codes == (
        "E_ALLOWED_MISSING",
        "E_CANON_NOT_IN_ALLOWED",
    )

    # A contract with an unrelated fatal schema error still aborts the batch.
    fatal = tmp_path / "fatal.yaml"
    fatal.write_text(
        "schema_version: \"1\"\nname: fatal\ndefaults: {precision: 2}\n"
        "fields:\n"
        "  - {field: energy, dimension: currency, canonical_unit: J, "
        "allowed_units: [J]}\n",
        encoding="utf-8",
    )
    from unitcontract.contract.schema import ContractValidationError

    with pytest.raises(ContractValidationError):
        merge_sites([SiteSpec("fatal", fatal, MERGE / "degenerate_data.csv")])

    # A missing data file for an otherwise valid site is a data error, not a
    # silent skip.
    from unitcontract.engine.reader import DataReadError

    with pytest.raises(DataReadError):
        merge_sites([SiteSpec("missing", SITE_A.contract_path, MERGE / "absent.csv")])


def test_null_defaults_block_is_treated_as_degenerate(tmp_path):
    contract = tmp_path / "null_defaults.yaml"
    contract.write_text(
        "schema_version: \"1\"\nname: null-defaults\ndefaults:\n"
        "fields:\n"
        "  - {field: energy, dimension: energy, canonical_unit: J, "
        "allowed_units: [J, kWh], precision: 2, rounding: half_even}\n",
        encoding="utf-8",
    )
    result = merge_sites([SiteSpec("null", contract, MERGE / "degenerate_data.csv")])
    assert result.sites == ()
    assert result.skipped_sites[0].codes == ("E_SCHEMA_ROOT",)


def test_unknown_business_key_does_not_merge_rows():
    registry = UnitRegistry.bundled()
    contract = execute(SITE_A.contract_path, SITE_A.data_path, no_timestamp=True).contract
    rows = [
        DataRow(1, {"energy": "1", "energy__unit": "kWh"}),
        DataRow(2, {"energy": "1", "energy__unit": "kWh"}),
    ]
    converted = convert_records(contract, rows, registry=registry)
    assert all(record.record_id is None for record in converted.records)


def test_duplicate_site_names_are_rejected():
    with pytest.raises(MergeError):
        merge_sites([SITE_A, SiteSpec("a", SITE_B.contract_path, SITE_B.data_path)])


def test_merge_requires_at_least_one_site():
    with pytest.raises(MergeError):
        merge_sites([])


def test_disjoint_keys_merge_equals_union_of_convert_records(tmp_path):
    """No shared business keys and no conflicts: merged == union of converts."""
    site_b_data = tmp_path / "site_b_disjoint.csv"
    original = (MERGE / "site_b_data.csv").read_text(encoding="utf-8")
    site_b_data.write_text(
        original.replace("m0", "n0"), encoding="utf-8"
    )  # rename keys so nothing overlaps site A

    out = tmp_path / "merged.jsonl"
    conflicts = tmp_path / "conflicts.json"
    assert main([
        "merge",
        "--site", "a", str(SITE_A.contract_path), str(SITE_A.data_path),
        "--site", "b", str(SITE_B.contract_path), str(site_b_data),
        "--out", str(out),
        "--conflicts", str(conflicts),
    ]) == 0

    convert_a = tmp_path / "a.jsonl"
    convert_b = tmp_path / "b.jsonl"
    assert main(["convert", "--contract", str(SITE_A.contract_path),
                 "--data", str(SITE_A.data_path), "--out", str(convert_a)]) == 0
    assert main(["convert", "--contract", str(SITE_B.contract_path),
                 "--data", str(site_b_data), "--out", str(convert_b)]) == 0

    merged = out.read_text(encoding="utf-8").splitlines()
    union = (
        convert_a.read_text(encoding="utf-8").splitlines()
        + convert_b.read_text(encoding="utf-8").splitlines()
    )
    assert len(merged) == len(union) == 55
    assert set(merged) == set(union)


def test_cli_pair_form_derives_site_names(tmp_path):
    out = tmp_path / "merged.jsonl"
    conflicts = tmp_path / "conflicts.json"
    assert main([
        "merge",
        "--pair", str(SITE_A.contract_path), str(SITE_A.data_path),
        "--pair", str(SITE_B.contract_path), str(SITE_B.data_path),
        "--out", str(out),
        "--conflicts", str(conflicts),
    ]) == 1
    payload = json.loads(conflicts.read_text(encoding="utf-8"))
    assert {item["site"] for item in payload["summary"]["site_details"]} == {
        "merge-site-a",
        "merge-site-b",
    }


def test_cli_exit_codes(tmp_path):
    out = tmp_path / "merged.jsonl"
    conflicts = tmp_path / "conflicts.json"
    base = ["merge", "--out", str(out), "--conflicts", str(conflicts)]

    assert main(base + [
        "--site", "a", str(SITE_A.contract_path), str(SITE_A.data_path),
    ]) == 0
    assert main(base + [
        "--site", "a", str(SITE_A.contract_path), str(SITE_A.data_path),
        "--site", "b", str(SITE_B.contract_path), str(SITE_B.data_path),
    ]) == 1

    bad = tmp_path / "bad.yaml"
    bad.write_text("name: broken\nfields: []\n", encoding="utf-8")
    assert main(base + ["--site", "bad", str(bad), str(MERGE / "degenerate_data.csv")]) == 2
    assert main(base + [
        "--site", "a", str(SITE_A.contract_path), str(tmp_path / "absent.csv"),
    ]) == 2


def test_demo_data_with_two_canonical_unit_contracts(tmp_path):
    """Completion check: same demo rows under J and kJ canonical contracts."""
    unit_contract = tmp_path / "energy_kj.yaml"
    unit_contract.write_text(
        (MERGE / "site_b_contract.yaml")
        .read_text(encoding="utf-8")
        .replace("merge-site-b", "demo-energy-kj"),
        encoding="utf-8",
    )
    # Restrict site B's contract to the fields present in the demo fixture.
    unit_contract.write_text(
        """
schema_version: "1"
name: demo-energy-kj
defaults:
  precision: 4
  rounding: half_up
fields:
  - field: energy
    dimension: energy
    canonical_unit: kJ
    allowed_units: [J, kJ, kWh, MWh, GJ]
    precision: 4
  - field: power
    dimension: power
    canonical_unit: W
    allowed_units: [W, kW, MW]
  - field: mass
    dimension: mass
    canonical_unit: g
    allowed_units: [g, kg, t]
  - field: length
    dimension: length
    canonical_unit: m
    allowed_units: [mm, cm, m, km]
  - field: temperature
    dimension: temperature
    canonical_unit: K
    allowed_units: [C, K, F]
    precision: 2
  - field: volume
    dimension: volume
    canonical_unit: L
    allowed_units: [mL, L, m3]
  - field: count
    dimension: count
    canonical_unit: count
    allowed_units: [count]
    precision: 2
""",
        encoding="utf-8",
    )
    out = tmp_path / "merged.jsonl"
    conflicts = tmp_path / "conflicts.json"
    code = main([
        "merge",
        "--site", "j", str(DEMO_CONTRACT), str(DEMO_DATA),
        "--site", "kj", str(unit_contract), str(DEMO_DATA),
        "--out", str(out),
        "--conflicts", str(conflicts),
    ])
    assert code == 0
    payload = json.loads(conflicts.read_text(encoding="utf-8"))
    # Identical source rows convert to identical exact quantities regardless of
    # canonical unit, so there are no true conflicts. Text differences are
    # precision drift only.
    assert payload["conflicts"] == []
    assert payload["summary"]["precision_drift"] > 0

    conv_j = tmp_path / "j.jsonl"
    conv_kj = tmp_path / "kj.jsonl"
    assert main([
        "convert", "--contract", str(DEMO_CONTRACT), "--data", str(DEMO_DATA),
        "--out", str(conv_j),
    ]) == 0
    assert main([
        "convert", "--contract", str(unit_contract), "--data", str(DEMO_DATA),
        "--out", str(conv_kj),
    ]) == 0
    known = set(conv_j.read_text(encoding="utf-8").splitlines()) | set(
        conv_kj.read_text(encoding="utf-8").splitlines()
    )
    merged = out.read_text(encoding="utf-8").splitlines()
    assert merged  # demo data has convertible records
    # Every merged line is exactly one of the two sites' convert records,
    # verifying that cross-site canonical values are byte-reproducible.
    assert all(line in known for line in merged)
    # Each business key appears at most once.
    keys = [
        (json.loads(line)["row"], json.loads(line)["field"])
        for line in merged
    ]
    assert len(keys) == len(set(keys))
