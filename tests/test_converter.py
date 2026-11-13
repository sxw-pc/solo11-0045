from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import pytest

from unitcontract.contract.schema import ContractField
from unitcontract.engine.converter import (
    ConversionError,
    convert_records,
    convert_value,
)
from unitcontract.engine.reader import DataRow
from unitcontract.units.rounding import decimal_places, format_fraction, parse_fraction


def make_field(**overrides) -> ContractField:
    values = {
        "field": "length",
        "dimension": "length",
        "canonical_unit": "m",
        "allowed_units": ("mm", "cm", "m", "km", "inch"),
        "precision": 4,
        "rounding": "half_even",
    }
    values.update(overrides)
    return ContractField(**values)


def test_exact_conversion_and_rendering(registry):
    field = make_field()
    value, rendered = convert_value(field, Fraction(1), "inch", registry)
    assert value == Fraction(127, 5000)
    assert rendered == "0.0254"


def test_temperature_conversion_uses_affine_path(contract, registry):
    field = contract.field_map["temperature"]
    value, rendered = convert_value(field, Fraction(70), "F", registry)
    assert round(float(value), 6) == 21.111111
    assert rendered == "21.1"


def test_disallowed_and_unknown_units_raise(registry):
    field = make_field(allowed_units=("m",))
    with pytest.raises(ConversionError) as caught:
        convert_value(field, Fraction(1), "inch", registry)
    assert caught.value.code == "E_UNIT_NOT_ALLOWED"
    with pytest.raises(ConversionError) as caught:
        convert_value(field, Fraction(1), "furlong", registry)
    assert caught.value.code == "E_UNIT_UNKNOWN"


@pytest.mark.parametrize(
    ("value", "mode", "expected"),
    [
        ("2.5", "half_up", "3"),
        ("2.5", "half_even", "2"),
        ("3.5", "half_even", "4"),
        ("2.5", "truncate", "2"),
        ("-2.5", "half_up", "-3"),
        ("-2.5", "truncate", "-2"),
    ],
)
def test_rounding_modes_differ(value, mode, expected):
    assert format_fraction(parse_fraction(value), 0, mode) == expected


def test_rounding_precision_and_exact_third():
    assert format_fraction(Fraction(1, 3), 4, "half_up") == "0.3333"
    assert format_fraction(Fraction(12345, 1000), 2, "half_even") == "12.34"
    assert format_fraction(Fraction(12355, 1000), 2, "half_up") == "12.36"


def test_decimal_places_is_lexical():
    assert decimal_places("1.230") == 3
    assert decimal_places("1") == 0
    assert decimal_places("1.2e-3") == 4
    assert decimal_places("1.2e3") == 0


def test_conversion_is_deterministic(contract, rows, registry):
    first = convert_records(contract, rows, registry)
    second = convert_records(contract, rows, registry)
    assert first.as_dicts() == second.as_dicts()
    assert len(first.records) == 114
    assert first.skipped == 12


def test_invalid_rows_are_skipped_not_invented(contract, registry):
    rows = [
        DataRow(1, {"energy": "n/a", "energy__unit": "kWh"}),
        DataRow(2, {"energy": "1.00", "energy__unit": "cal"}),
        DataRow(3, {"energy": "1.00", "energy__unit": "kWh"}),
    ]
    energy_only = replace(contract, fields=(contract.field_map["energy"],))
    converted = convert_records(energy_only, rows, registry)
    assert [record.row_number for record in converted.records] == [3]
    assert converted.records[0].canonical_value == "3600000.00"
    assert converted.skipped == 2


def test_count_only_rounds(contract, registry):
    record = convert_records(
        contract, [DataRow(1, {"count": "42", "count__unit": "count"})], registry
    ).records[-1]
    assert record.canonical_value == "42"
    assert record.canonical_unit == "count"


def test_record_shape(contract, rows, registry):
    record = convert_records(contract, rows[:1], registry).records[0]
    payload = record.as_dict()
    assert payload == {
        "row": 1,
        "field": "energy",
        "dimension": "energy",
        "raw_value": "12.50",
        "source_unit": "kWh",
        "canonical_unit": "J",
        "canonical_value": "45000000.00",
        "precision": 2,
        "rounding": "half_even",
    }
