from __future__ import annotations

import json

import pytest

from unitcontract.contract.loader import load_contract
from unitcontract.engine.converter import convert_records
from unitcontract.engine.reader import read_rows
from unitcontract.engine.validator import validate_records
from unitcontract.units.registry import UnitRegistry


@pytest.fixture()
def energy_contract(registry: UnitRegistry):
    return load_contract(
        "unitcontract/fixtures/contracts/demo_energy.yaml", registry=registry
    )


def summarize(path, contract, registry: UnitRegistry) -> tuple[int, int, int, int]:
    rows = read_rows(path)
    conversion = convert_records(contract, rows, registry)
    validation = validate_records(contract, rows, registry)
    return (
        len(rows),
        len(conversion.records),
        conversion.skipped,
        validation.violating_rows,
    )


@pytest.mark.parametrize(
    ("csv_text", "jsonl_text"),
    [
        (
            "energy,energy__unit\n1.0,kWh\n,\n2.0,kWh\n",
            '{"energy":{"value":"1.0","unit":"kWh"}}\n'
            '{"energy":{"value":null,"unit":null}}\n'
            '{"energy":{"value":"2.0","unit":"kWh"}}\n',
        ),
        (
            "energy,energy__unit\n1.0,kWh\n   \n2.0,kWh\n",
            '{"energy":{"value":"1.0","unit":"kWh"}}\n'
            '{"energy":{}}\n'
            '{"energy":{"value":"2.0","unit":"kWh"}}\n',
        ),
        (
            'energy,energy__unit\n1.0,kWh\n"",""\n2.0,kWh\n',
            '{"energy":{"value":"1.0","unit":"kWh"}}\n'
            "{}\n"
            '{"energy":{"value":"2.0","unit":"kWh"}}\n',
        ),
    ],
)
def test_blank_records_are_treated_identically_across_formats(
    tmp_path, energy_contract, registry, csv_text, jsonl_text
):
    csv_path = tmp_path / "data.csv"
    jsonl_path = tmp_path / "data.jsonl"
    csv_path.write_text(csv_text, encoding="utf-8")
    jsonl_path.write_text(jsonl_text, encoding="utf-8")

    from_csv = summarize(csv_path, energy_contract, registry)
    from_jsonl = summarize(jsonl_path, energy_contract, registry)
    assert from_csv == from_jsonl
    # The all-empty record is dropped before numbering, not counted as skipped.
    assert from_csv[0] == 2
    assert from_csv[2] == 12


def test_partial_blank_fields_keep_row_in_both_formats(
    tmp_path, energy_contract, registry
):
    csv_path = tmp_path / "data.csv"
    jsonl_path = tmp_path / "data.jsonl"
    csv_path.write_text("energy,energy__unit\n1.0,kWh\n,kWh\n2.0,kWh\n", encoding="utf-8")
    jsonl_path.write_text(
        '{"energy":{"value":"1.0","unit":"kWh"}}\n'
        '{"energy":{"unit":"kWh"}}\n'
        '{"energy":{"value":"2.0","unit":"kWh"}}\n',
        encoding="utf-8",
    )
    assert summarize(csv_path, energy_contract, registry) == summarize(
        jsonl_path, energy_contract, registry
    )
    # A row with a unit but no value is a real E_MISSING finding in both.
    rows = read_rows(csv_path)
    findings = validate_records(energy_contract, rows, registry).findings
    assert [(item.row_number, item.code) for item in findings if item.field == "energy"] == [
        (2, "E_MISSING")
    ]


def test_equivalent_csv_and_jsonl_emit_same_records(
    tmp_path, energy_contract, registry
):
    csv_path = tmp_path / "data.csv"
    jsonl_path = tmp_path / "data.jsonl"
    csv_path.write_text(
        "energy,energy__unit,temperature,temperature__unit\n"
        "1.0,kWh,70.0,F\n"
        ",,,\n"
        "2.0,GJ,300,K\n",
        encoding="utf-8",
    )
    jsonl_path.write_text(
        json.dumps({"energy": {"value": "1.0", "unit": "kWh"},
                    "temperature": {"value": "70.0", "unit": "F"}}) + "\n"
        + json.dumps({"energy": {"value": None, "unit": None},
                      "temperature": {"value": None, "unit": None}}) + "\n"
        + json.dumps({"energy": {"value": "2.0", "unit": "GJ"},
                      "temperature": {"value": "300", "unit": "K"}}) + "\n",
        encoding="utf-8",
    )
    csv_records = convert_records(energy_contract, read_rows(csv_path), registry)
    jsonl_records = convert_records(energy_contract, read_rows(jsonl_path), registry)
    assert csv_records.as_dicts() == jsonl_records.as_dicts()
    assert csv_records.skipped == jsonl_records.skipped
