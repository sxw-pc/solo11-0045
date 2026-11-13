"""Affine temperature conversions, kept separate from ratio units."""

from __future__ import annotations

from fractions import Fraction

SUPPORTED_TEMPERATURE_UNITS = ("C", "K", "F")

_CELSIUS_OFFSET = Fraction(5463, 20)  # 273.15
_FAHRENHEIT_OFFSET = Fraction(32)
_FAHRENHEIT_SCALE = Fraction(5, 9)


class TemperatureError(ValueError):
    """Raised when a temperature is not physically meaningful."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _ensure_unit(unit: str) -> None:
    if unit not in SUPPORTED_TEMPERATURE_UNITS:
        raise TemperatureError("E_UNIT_UNKNOWN", f"unknown temperature unit: {unit}")


def to_kelvin(value: Fraction, unit: str) -> Fraction:
    """Convert a temperature to kelvin using exact rational arithmetic."""
    _ensure_unit(unit)
    if unit == "K":
        kelvin = value
    elif unit == "C":
        kelvin = value + _CELSIUS_OFFSET
    else:
        kelvin = (value - _FAHRENHEIT_OFFSET) * _FAHRENHEIT_SCALE + _CELSIUS_OFFSET
    if kelvin < 0:
        raise TemperatureError(
            "E_TEMP_BELOW_ABSOLUTE",
            f"{value} {unit} is below absolute zero",
        )
    return kelvin


def from_kelvin(kelvin: Fraction, unit: str) -> Fraction:
    """Convert a kelvin value to the requested temperature unit."""
    _ensure_unit(unit)
    if kelvin < 0:
        raise TemperatureError(
            "E_TEMP_BELOW_ABSOLUTE",
            "kelvin value is below absolute zero",
        )
    if unit == "K":
        return kelvin
    if unit == "C":
        return kelvin - _CELSIUS_OFFSET
    return (kelvin - _CELSIUS_OFFSET) * Fraction(9, 5) + _FAHRENHEIT_OFFSET


def convert_temperature(value: Fraction, from_unit: str, to_unit: str) -> Fraction:
    """Convert between C, K and F, rejecting values below absolute zero."""
    if from_unit == to_unit:
        # Still run the absolute-zero guard for K-like values.
        return from_kelvin(to_kelvin(value, from_unit), to_unit)
    kelvin = to_kelvin(value, from_unit)
    return from_kelvin(kelvin, to_unit)


def check_absolute_zero(value: Fraction, unit: str) -> None:
    """Raise TemperatureError when *value* lies below absolute zero."""
    to_kelvin(value, unit)
