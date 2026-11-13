from __future__ import annotations

import json

from unitcontract.engine.reader import DataRow, read_rows
from unitcontract.engine.validator import validate_field, validate_records


def codes_for(contract, registry, field_name: str, **values) -> list[str]:
    row = DataRow(1, values)
    return [item.code for item in validate_field(row, contract.field_map[field_name], registry)]


def test_missing_value(contract, registry):
    assert codes_for(contract, registry, "energy", energy__unit="kWh") == ["E_MISSING"]


def test_missing_unit(contract, registry):
    assert codes_for(contract, registry, "energy", energy="1.00") == ["E_UNIT_MISSING"]


def test_non_numeric(contract, registry):
    assert codes_for(contract, registry, "mass", mass="n/a", mass__unit="kg") == ["E_NOT_NUMERIC"]


def test_unknown_unit(contract, registry):
    assert codes_for(
        contract, registry, "length", length="3", length__unit="furlong"
    ) == ["E_UNIT_UNKNOWN"]


def test_registered_but_not_allowed(contract, registry):
    assert codes_for(
        contract, registry, "energy", energy="12", energy__unit="cal"
    ) == ["E_UNIT_NOT_ALLOWED"]


def test_dimension_mismatch(contract, registry):
    assert codes_for(
        contract, registry, "volume", volume="3", volume__unit="kg"
    ) == ["E_UNIT_DIM_MISMATCH"]


def test_out_of_range_in_canonical_unit(contract, registry):
    assert codes_for(
        contract, registry, "temperature", temperature="85.0", temperature__unit="C"
    ) == ["E_OUT_OF_RANGE"]


def test_below_absolute_zero(contract, registry):
    assert codes_for(
        contract, registry, "temperature", temperature="-5", temperature__unit="K"
    ) == ["E_TEMP_BELOW_ABSOLUTE"]


def test_precision_is_a_warning(contract, registry):
    findings = validate_field(
        DataRow(1, {"energy": "1.234", "energy__unit": "kWh"}),
        contract.field_map["energy"],
        registry,
    )
    assert [item.code for item in findings] == ["W_PRECISION_EXCEEDED"]
    assert findings[0].severity == "warning"


def test_clean_and_edge_values_pass(contract, registry):
    assert codes_for(contract, registry, "count", count="0", count__unit="count") == []
    assert codes_for(contract, registry, "temperature", temperature="60.0", temperature__unit="C") == []
    assert codes_for(contract, registry, "energy", energy="5.00", energy__unit="GJ") == []


def test_validation_aggregates_rows_and_fields(contract, registry, rows):
    result = validate_records(contract, rows, registry)
    assert result.rows_total == 18
    assert result.violating_rows == 14
    assert result.passed_rows == 4
    assert result.warning_rows == 2
    assert len(result.error_findings) == 15
    assert len(result.warning_findings) == 2
    assert result.counts_by_code()["E_UNIT_NOT_ALLOWED"] == 4
    assert result.counts_by_field()["energy"] == 3


def test_nested_jsonl_value_unit(tmp_path, contract, registry):
    path = tmp_path / "nested.jsonl"
    path.write_text(
        json.dumps(
            {
                "energy": {"value": 1, "unit": "kWh"},
                "power": {"value": 2, "unit": "kW"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rows = read_rows(path)
    assert rows[0].cell("energy").value_text == "1"
    findings = validate_records(contract, rows, registry).findings
    # Other contract fields are absent, so they are reported as missing.
    assert ("E_MISSING" not in {item.code for item in findings if item.field == "energy"})
    assert all(item.row_number == 1 for item in findings)
