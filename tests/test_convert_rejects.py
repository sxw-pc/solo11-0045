"""Strict conversion (``--rejected-out``) and rejected-detail tests."""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path

from unitcontract.cli import main
from unitcontract.contract.loader import load_contract
from unitcontract.engine.converter import convert_records, convert_value
from unitcontract.engine.reader import read_rows
from unitcontract.engine.validator import validate_records
from unitcontract.pipeline import execute
from unitcontract.units.registry import UnitRegistry

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "unitcontract" / "fixtures" / "contracts" / "demo_energy.yaml"
DATA = ROOT / "unitcontract" / "fixtures" / "data" / "demo_readings.csv"

# SHA-256 of `convert` output on the fixture data before the feature existed;
# running convert without --rejected-out must keep producing exactly these bytes.
LEGACY_NORMALIZED_SHA256 = "77c0bc08eeec9fad65eef90cbb92e89340abc296d2132216648624dd6ab492d9"

REJECTED_KEYS = {
    "row",
    "source",
    "field",
    "dimension",
    "code",
    "message",
    "raw_value",
    "raw_unit",
    "canonical_unit",
    "canonical_value",
}

NORMALIZED_KEYS = {
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

CUSTOM_CSV = "\n".join(
    [
        "record_id,site,energy,energy__unit,power,power__unit,mass,mass__unit,"
        "length,length__unit,temperature,temperature__unit,volume,volume__unit,"
        "count,count__unit",
        "ok-1,site-a,1.00,kWh,1.00,kW,1.000,kg,1.00,m,20.0,C,1.00,L,1,count",
        "ok-2,site-a,2.00,kWh,2.00,kW,2.000,kg,2.00,m,68.0,F,2.00,L,2,count",
        "bad-empty,site-a,,kWh,1.00,kW,1.000,kg,1.00,m,20.0,C,1.00,L,1,count",
        "bad-nounit,site-a,1.00,kWh,1.00,kW,1.000,,1.00,m,20.0,C,1.00,L,1,count",
        "bad-badunit,site-a,1.00,kWh,1.00,kW,1.000,kg,1.00,m,20.0,BTU,1.00,L,1,count",
        "bad-range,site-a,1.00,kWh,1.00,kW,1.000,kg,1.00,m,99.0,C,1.00,L,1,count",
        "",
    ]
)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_strict_partition_matches_validate(contract, rows, registry):
    validation = validate_records(contract, rows, registry)
    conversion = convert_records(
        contract, rows, registry, strict=True, source_name="demo_readings.csv"
    )
    error_cells = {(f.row_number, f.field): f for f in validation.error_findings}
    rejected_cells = {(r.row_number, r.field): r for r in conversion.rejected}

    # Same cells, same codes, same messages as validate.
    assert set(rejected_cells) == set(error_cells)
    for key, finding in error_cells.items():
        assert rejected_cells[key].code == finding.code
        assert rejected_cells[key].message == finding.message

    # skipped is exactly the rejected line count; nothing is dropped silently.
    assert conversion.skipped == len(conversion.rejected) == 15

    # normalized holds exactly the cells validate passes; the two partition all cells.
    normalized_cells = {(r.row_number, r.field) for r in conversion.records}
    all_cells = {(row.row_number, f.field) for row in rows for f in contract.fields}
    assert normalized_cells == all_cells - set(error_cells)
    assert normalized_cells.isdisjoint(rejected_cells)


def test_strict_out_of_range_temperature_matches_validate(contract, rows, registry):
    conversion = convert_records(
        contract, rows, registry, strict=True, source_name="demo_readings.csv"
    )
    validation = validate_records(contract, rows, registry)
    entry = next(
        r for r in conversion.rejected if r.row_number == 9 and r.field == "temperature"
    )
    finding = next(
        f
        for f in validation.error_findings
        if f.row_number == 9 and f.field == "temperature"
    )
    assert entry.code == finding.code == "E_OUT_OF_RANGE"
    assert entry.message == finding.message
    assert entry.raw_value == "85.0"
    assert entry.raw_unit == "C"
    assert entry.source == "demo_readings.csv"
    # Canonical value comes from the exact normalized-path implementation.
    field = contract.field_map["temperature"]
    _, rendered = convert_value(field, Fraction("85.0"), "C", registry)
    assert entry.canonical_unit == "C"
    assert entry.canonical_value == rendered == "85.0"


def test_strict_covers_missing_and_unit_failures(contract, rows, registry):
    conversion = convert_records(contract, rows, registry, strict=True, source_name="x.csv")
    by_cell = {(r.row_number, r.field): r for r in conversion.rejected}

    missing = by_cell[(4, "energy")]
    assert missing.code == "E_MISSING"
    assert missing.raw_value is None
    assert missing.canonical_value is None

    unit_missing = by_cell[(11, "length")]
    assert unit_missing.code == "E_UNIT_MISSING"
    assert unit_missing.raw_value == "3"
    assert unit_missing.raw_unit is None
    assert unit_missing.canonical_value is None

    unknown = by_cell[(10, "length")]
    assert unknown.code == "E_UNIT_UNKNOWN"
    assert unknown.raw_unit == "furlong"
    assert unknown.canonical_value is None

    mismatch = by_cell[(12, "volume")]
    assert mismatch.code == "E_UNIT_DIM_MISMATCH"

    not_allowed = by_cell[(5, "energy")]
    assert not_allowed.code == "E_UNIT_NOT_ALLOWED"

    below_absolute = by_cell[(7, "temperature")]
    assert below_absolute.code == "E_TEMP_BELOW_ABSOLUTE"
    assert below_absolute.canonical_value is None

    # Only out-of-range cells carry a converted canonical value.
    with_canonical = {
        (r.row_number, r.field) for r in conversion.rejected if r.canonical_value is not None
    }
    assert with_canonical == {(3, "energy"), (9, "temperature"), (14, "count")}


def test_legacy_mode_collects_no_rejected_and_is_unchanged(contract, rows, registry):
    legacy = convert_records(contract, rows, registry)
    assert legacy.rejected == ()
    assert len(legacy.records) == 114
    assert legacy.skipped == 12
    explicit_off = convert_records(
        contract, rows, registry, strict=False, source_name="demo_readings.csv"
    )
    assert explicit_off.as_dicts() == legacy.as_dicts()
    assert explicit_off.skipped == legacy.skipped
    assert explicit_off.rejected == ()


def test_cli_convert_rejected_out_on_demo_data(tmp_path, capsys):
    normalized = tmp_path / "normalized.jsonl"
    rejected = tmp_path / "rejected.jsonl"
    code = main(
        [
            "convert",
            "--contract",
            str(CONTRACT),
            "--data",
            str(DATA),
            "--out",
            str(normalized),
            "--rejected-out",
            str(rejected),
        ]
    )
    assert code == 0
    stderr = capsys.readouterr().err
    assert "records=111 skipped=15" in stderr
    assert "rejected=15" in stderr

    normalized_rows = _read_jsonl(normalized)
    rejected_rows = _read_jsonl(rejected)
    assert len(normalized_rows) == 111
    assert len(rejected_rows) == 15
    # Normalized record structure is untouched by the new artifact.
    assert all(set(row) == NORMALIZED_KEYS for row in normalized_rows)
    assert all(set(row) == REJECTED_KEYS for row in rejected_rows)
    assert {row["source"] for row in rejected_rows} == {"demo_readings.csv"}

    # Rejected lines agree with validate on the same data, cell by cell.
    result = execute(CONTRACT, DATA, no_timestamp=True)
    error_cells = {
        (f.row_number, f.field): f.code for f in result.validation.error_findings
    }
    assert {(row["row"], row["field"]): row["code"] for row in rejected_rows} == error_cells


def test_cli_convert_without_flag_is_byte_identical(tmp_path):
    output = tmp_path / "normalized.jsonl"
    assert (
        main(
            ["convert", "--contract", str(CONTRACT), "--data", str(DATA), "--out", str(output)]
        )
        == 0
    )
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    assert digest == LEGACY_NORMALIZED_SHA256
    assert not (tmp_path / "rejected.jsonl").exists()


def test_cli_convert_custom_row_set_acceptance(tmp_path, capsys):
    """Demo contract + rows covering empty/missing-unit/illegal-unit/over-range."""
    data = tmp_path / "custom.csv"
    data.write_text(CUSTOM_CSV, encoding="utf-8")
    normalized = tmp_path / "normalized.jsonl"
    rejected = tmp_path / "rejected.jsonl"
    code = main(
        [
            "convert",
            "--contract",
            str(CONTRACT),
            "--data",
            str(data),
            "--out",
            str(normalized),
            "--rejected-out",
            str(rejected),
        ]
    )
    assert code == 0
    stderr = capsys.readouterr().err
    assert "skipped=4" in stderr
    assert "rejected=4" in stderr

    normalized_rows = _read_jsonl(normalized)
    rejected_rows = _read_jsonl(rejected)

    # normalized count == cells validate passes; rejected == validate error cells.
    result = execute(CONTRACT, DATA, no_timestamp=True)
    registry = UnitRegistry.bundled()
    contract = load_contract(CONTRACT, registry=registry)
    validation = validate_records(contract, read_rows(data), registry)
    total_cells = validation.rows_total * len(contract.fields)
    assert len(normalized_rows) == total_cells - len(validation.error_findings) == 38
    assert len(rejected_rows) == len(validation.error_findings) == 4

    by_row = {row["row"]: row for row in rejected_rows}
    assert by_row[3]["code"] == "E_MISSING"
    assert by_row[3]["raw_value"] is None
    assert by_row[4]["code"] == "E_UNIT_MISSING"
    assert by_row[4]["raw_unit"] is None
    assert by_row[5]["code"] == "E_UNIT_UNKNOWN"
    assert by_row[5]["raw_unit"] == "BTU"

    # The over-range temperature row: same code and canonical value on both sides.
    finding = next(
        f
        for f in validation.error_findings
        if f.row_number == 6 and f.field == "temperature"
    )
    entry = by_row[6]
    assert entry["field"] == "temperature"
    assert entry["code"] == finding.code == "E_OUT_OF_RANGE"
    assert entry["message"] == finding.message
    assert entry["raw_value"] == "99.0"
    assert entry["raw_unit"] == "C"
    assert entry["canonical_unit"] == "C"
    assert entry["canonical_value"] == "99.0"
    assert entry["source"] == "custom.csv"

    # Rows validate passes must never appear in the rejected detail.
    rejected_row_numbers = {row["row"] for row in rejected_rows}
    assert rejected_row_numbers.isdisjoint({1, 2})


def test_cli_convert_rejects_same_path(tmp_path):
    output = tmp_path / "same.jsonl"
    code = main(
        [
            "convert",
            "--contract",
            str(CONTRACT),
            "--data",
            str(DATA),
            "--out",
            str(output),
            "--rejected-out",
            str(output),
        ]
    )
    assert code == 2
    assert not output.exists()
