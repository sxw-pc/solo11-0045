"""Exact-rational rounding with explicit decimal output."""

from __future__ import annotations

import re
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction

SUPPORTED_ROUNDING = ("half_up", "half_even", "truncate")

_ROUNDING_MODES = {
    "half_up": ROUND_HALF_UP,
    "half_even": ROUND_HALF_EVEN,
    "truncate": ROUND_DOWN,
}

_NUMERIC_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)


def is_numeric_text(text: str) -> bool:
    """Return True for a plain decimal/scientific number (no NaN/Inf)."""
    return bool(_NUMERIC_RE.fullmatch(text.strip()))


def parse_fraction(text: str) -> Fraction:
    """Parse numeric text exactly, rejecting NaN, infinity and blank values."""
    cleaned = text.strip()
    if not is_numeric_text(cleaned):
        raise ValueError(f"not a finite decimal number: {text!r}")
    return Fraction(Decimal(cleaned))


def decimal_places(text: str) -> int:
    """Count written decimal places, accounting for scientific notation.

    ``1.230`` has three decimal places. ``1.2e-3`` has four. The function is
    intentionally lexical: it reports precision as observed in the source.
    """
    cleaned = text.strip().lower().lstrip("+-")
    mantissa, _, exponent_text = cleaned.partition("e")
    exponent = int(exponent_text) if exponent_text else 0
    _, dot, fraction = mantissa.partition(".")
    places = len(fraction) if dot else 0
    if exponent < 0:
        places += -exponent
    elif exponent > 0:
        places = max(0, places - exponent)
    return places


def _quantize(value: Fraction, precision: int, rounding: str) -> Decimal:
    if rounding not in _ROUNDING_MODES:
        raise ValueError(f"unsupported rounding mode: {rounding}")
    if precision < 0:
        raise ValueError("precision must be non-negative")
    quantum = Decimal(1).scaleb(-precision)
    with localcontext() as context:
        context.prec = max(80, len(str(abs(value.numerator))) + precision + 20)
        decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
        rounded = decimal_value.quantize(quantum, rounding=_ROUNDING_MODES[rounding])
    if rounded == 0:
        return abs(rounded)
    return rounded


def round_fraction(value: Fraction, precision: int, rounding: str) -> Fraction:
    """Round an exact fraction and return it as an exact fraction."""
    return Fraction(_quantize(value, precision, rounding))


def format_fraction(value: Fraction, precision: int, rounding: str) -> str:
    """Render with exactly *precision* digits after the decimal point."""
    rounded = _quantize(value, precision, rounding)
    return format(rounded, f".{precision}f")
