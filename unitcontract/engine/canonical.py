"""Single source of truth for canonical field values.

Both range validation and normalized JSONL output must agree on the physical
value of a source cell.  This module converts exactly once and applies the
contract's rounding exactly once, so validation and conversion can never drift
apart (notably on the affine C/K/F temperature path).
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from unitcontract.contract.schema import ContractField
from unitcontract.units.registry import UnitRegistry
from unitcontract.units.rounding import format_fraction, round_fraction


@dataclass(frozen=True)
class CanonicalValue:
    """One source value resolved into the field's canonical unit.

    ``exact`` keeps the full rational result, ``rounded`` is that value under
    the contract's ``precision``/``rounding`` policy, and ``rendered`` is the
    canonical text written to normalized output. ``rounded`` is what range
    checks compare, so a row's verdict matches the value that was emitted.
    """

    exact: Fraction
    rounded: Fraction
    rendered: str


def resolve_canonical(
    field: ContractField,
    value: Fraction,
    unit: str,
    registry: UnitRegistry,
) -> CanonicalValue:
    """Convert *value* from *unit* to ``field.canonical_unit`` exactly once."""
    converted = registry.convert(value, field.dimension, unit, field.canonical_unit)
    rounded = round_fraction(converted, field.precision, field.rounding)
    rendered = format_fraction(rounded, field.precision, field.rounding)
    return CanonicalValue(exact=converted, rounded=rounded, rendered=rendered)
