from __future__ import annotations

from fractions import Fraction

import pytest

from unitcontract.engine.reader import DataRow
from unitcontract.engine.validator import validate_field
from unitcontract.units.temperature import (
    TemperatureError,
    check_absolute_zero,
    convert_temperature,
    from_kelvin,
    to_kelvin,
)


def approx(value: Fraction, expected: float, tolerance: float = 1e-12) -> bool:
    return abs(float(value) - expected) <= tolerance


@pytest.mark.parametrize(
    ("value", "source", "target", "expected"),
    [
        (0, "C", "K", 273.15),
        (100, "C", "K", 373.15),
        (273.15, "K", "C", 0.0),
        (32, "F", "C", 0.0),
        (212, "F", "C", 100.0),
        (0, "C", "F", 32.0),
        (100, "C", "F", 212.0),
        (273.15, "K", "F", 32.0),
        (32, "F", "K", 273.15),
        (-40, "C", "F", -40.0),
    ],
)
def test_pairwise_temperature_conversions(value, source, target, expected):
    converted = convert_temperature(Fraction(str(value)), source, target)
    assert approx(converted, expected)


def test_absolute_zero_is_valid_but_below_is_not():
    assert to_kelvin(Fraction(-27315, 100), "C") == 0
    with pytest.raises(TemperatureError) as caught:
        to_kelvin(Fraction(-27316, 100), "C")
    assert caught.value.code == "E_TEMP_BELOW_ABSOLUTE"
    with pytest.raises(TemperatureError) as caught:
        convert_temperature(Fraction(-1), "K", "C")
    assert caught.value.code == "E_TEMP_BELOW_ABSOLUTE"
    with pytest.raises(TemperatureError):
        check_absolute_zero(Fraction(-45968, 100), "F")


def test_from_kelvin_rejects_negative():
    with pytest.raises(TemperatureError):
        from_kelvin(Fraction(-1), "F")


def test_unknown_temperature_unit():
    with pytest.raises(TemperatureError) as caught:
        convert_temperature(Fraction(10), "C", "R")
    assert caught.value.code == "E_UNIT_UNKNOWN"


def test_validator_reports_absolute_zero(contract, registry):
    field = contract.field_map["temperature"]
    row = DataRow(1, {"temperature": "-5", "temperature__unit": "K"})
    findings = validate_field(row, field, registry)
    assert [item.code for item in findings] == ["E_TEMP_BELOW_ABSOLUTE"]


def test_temperature_range_uses_canonical_celsius(contract, registry):
    field = contract.field_map["temperature"]
    row = DataRow(1, {"temperature": "150", "temperature__unit": "F"})
    findings = validate_field(row, field, registry)
    assert "E_OUT_OF_RANGE" in [item.code for item in findings]

    warm = DataRow(2, {"temperature": "100", "temperature__unit": "F"})
    assert validate_field(warm, field, registry) == []
