"""Deterministic conversion of contract fields to canonical units."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable

from unitcontract.contract.schema import Contract, ContractField
from unitcontract.engine.canonical import resolve_canonical
from unitcontract.engine.reader import DataRow
from unitcontract.units.registry import UnitRegistry, UnknownUnitError
from unitcontract.units.rounding import parse_fraction
from unitcontract.units.temperature import TemperatureError


class ConversionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ConversionRecord:
    row_number: int
    field: str
    dimension: str
    raw_value: str
    source_unit: str
    canonical_unit: str
    canonical_value: str
    precision: int
    rounding: str

    def as_dict(self) -> dict[str, object]:
        return {
            "row": self.row_number,
            "field": self.field,
            "dimension": self.dimension,
            "raw_value": self.raw_value,
            "source_unit": self.source_unit,
            "canonical_unit": self.canonical_unit,
            "canonical_value": self.canonical_value,
            "precision": self.precision,
            "rounding": self.rounding,
        }


@dataclass(frozen=True)
class ConversionResult:
    records: tuple[ConversionRecord, ...]
    skipped: int

    def as_dicts(self) -> list[dict[str, object]]:
        return [record.as_dict() for record in self.records]


def convert_value(
    field: ContractField,
    value: Fraction,
    unit: str,
    registry: UnitRegistry,
) -> tuple[Fraction, str]:
    """Convert one value and render it with the contract's rounding policy.

    Returns the exact canonical rational and the canonical text. The exact
    value is the same one validation compares against the declared range.
    """
    try:
        registry.get_unit(field.dimension, unit)
    except UnknownUnitError as exc:
        raise ConversionError("E_UNIT_UNKNOWN", f"unit {unit} is not registered") from exc
    if unit not in field.allowed_units:
        raise ConversionError("E_UNIT_NOT_ALLOWED", f"unit {unit} is not allowed")
    try:
        canonical = resolve_canonical(field, value, unit, registry)
    except TemperatureError as exc:
        raise ConversionError(exc.code, exc.message) from exc
    return canonical.exact, canonical.rendered


def convert_records(
    contract: Contract,
    rows: Iterable[DataRow],
    registry: UnitRegistry | None = None,
) -> ConversionResult:
    """Emit normalized records; malformed source fields are skipped.

    Validation owns error reporting. Conversion deliberately never invents a
    value for a field that cannot be converted.
    """
    registry = registry or UnitRegistry.bundled()
    records: list[ConversionRecord] = []
    skipped = 0
    for row in rows:
        for field in contract.fields:
            cell = row.cell(field.field)
            if not cell.has_value or not cell.has_unit:
                skipped += 1
                continue
            try:
                value = parse_fraction(cell.value_text)
                _, rendered = convert_value(field, value, cell.unit_text, registry)
            except (ValueError, ConversionError):
                skipped += 1
                continue
            records.append(
                ConversionRecord(
                    row_number=row.row_number,
                    field=field.field,
                    dimension=field.dimension,
                    raw_value=cell.value_text,
                    source_unit=cell.unit_text,
                    canonical_unit=field.canonical_unit,
                    canonical_value=rendered,
                    precision=field.precision,
                    rounding=field.rounding,
                )
            )
    return ConversionResult(records=tuple(records), skipped=skipped)
