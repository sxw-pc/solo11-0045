from __future__ import annotations

from fractions import Fraction

import pytest

from unitcontract.units.registry import (
    UnitRegistry,
    UnknownDimensionError,
    UnknownUnitError,
)


def test_catalogue_contains_required_dimensions_and_units(registry):
    assert set(registry.dimensions) == {
        "length",
        "mass",
        "energy",
        "power",
        "time",
        "temperature",
        "volume",
        "count",
    }
    expected = {
        "length": {"mm", "cm", "m", "km", "inch", "ft", "mile"},
        "mass": {"g", "kg", "t", "lb"},
        "energy": {"J", "kJ", "kWh", "MWh", "GJ"},
        "power": {"W", "kW", "MW"},
        "time": {"s", "min", "h", "day"},
        "volume": {"mL", "L", "m3"},
        "count": {"count"},
        "temperature": {"C", "K", "F"},
    }
    for dimension, units in expected.items():
        assert units <= set(registry.units_for(dimension))


@pytest.mark.parametrize(
    ("dimension", "unit"),
    [
        ("length", "mm"),
        ("length", "inch"),
        ("length", "mile"),
        ("mass", "g"),
        ("mass", "lb"),
        ("energy", "kWh"),
        ("energy", "GJ"),
        ("power", "MW"),
        ("time", "day"),
        ("volume", "m3"),
        ("count", "count"),
    ],
)
def test_ratio_round_trip_is_exact(registry, dimension, unit):
    value = Fraction(37, 8)
    base = registry.convert(value, dimension, unit, registry.base_unit(dimension))
    restored = registry.convert(base, dimension, registry.base_unit(dimension), unit)
    assert restored == value


def test_exact_factors_are_not_floats(registry):
    inch = registry.ratio("length", "inch", "m")
    assert inch == Fraction(127, 5000)
    kwh = registry.ratio("energy", "kWh", "J")
    assert kwh == 3_600_000
    lb = registry.ratio("mass", "lb", "kg")
    assert lb == Fraction(45359237, 100000000)
    assert isinstance(kwh, Fraction)


def test_unknown_dimension_and_unit_raise(registry):
    with pytest.raises(UnknownDimensionError):
        registry.units_for("currency")
    with pytest.raises(UnknownUnitError):
        registry.get_unit("energy", "stone")
    assert registry.known_dimensions_for("kg") == ("mass",)
    assert registry.known_dimensions_for("furlong") == ()
    assert registry.has_unit("energy", "kWh")
    assert not registry.has_unit("mass", "kWh")


def test_every_contract_unit_resolves(contract, registry):
    for field in contract.fields:
        assert registry.has_unit(field.dimension, field.canonical_unit)
        for unit in field.allowed_units:
            assert registry.has_unit(field.dimension, unit)


def test_registry_can_be_loaded_twice(registry):
    again = UnitRegistry.bundled()
    assert again.dimensions == registry.dimensions
    assert again.base_unit("energy") == "J"
