"""Deterministic conversion of contract fields to canonical units."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable

from unitcontract.contract.schema import Contract, ContractField
from unitcontract.engine.reader import DataRow, FieldCell
from unitcontract.engine.validator import validate_field
from unitcontract.units.registry import UnitRegistry, UnknownUnitError
from unitcontract.units.rounding import format_fraction, parse_fraction
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
class RejectedRecord:
    """One source cell rejected during strict conversion, with full context.

    ``canonical_value`` is populated only when the value itself converts
    cleanly under the contract (the out-of-range case); it stays ``None``
    for missing values, missing/unknown units and non-numeric text.
    """

    row_number: int
    source: str
    field: str
    dimension: str
    code: str
    message: str
    raw_value: str | None
    raw_unit: str | None
    canonical_unit: str
    canonical_value: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "row": self.row_number,
            "source": self.source,
            "field": self.field,
            "dimension": self.dimension,
            "code": self.code,
            "message": self.message,
            "raw_value": self.raw_value,
            "raw_unit": self.raw_unit,
            "canonical_unit": self.canonical_unit,
            "canonical_value": self.canonical_value,
        }


@dataclass(frozen=True)
class ConversionResult:
    records: tuple[ConversionRecord, ...]
    skipped: int
    rejected: tuple[RejectedRecord, ...] = ()

    def as_dicts(self) -> list[dict[str, object]]:
        return [record.as_dict() for record in self.records]

    def rejected_as_dicts(self) -> list[dict[str, object]]:
        return [record.as_dict() for record in self.rejected]


def convert_value(
    field: ContractField,
    value: Fraction,
    unit: str,
    registry: UnitRegistry,
) -> tuple[Fraction, str]:
    """Convert one value and render it with the contract's rounding policy."""
    try:
        registry.get_unit(field.dimension, unit)
    except UnknownUnitError as exc:
        raise ConversionError("E_UNIT_UNKNOWN", f"unit {unit} is not registered") from exc
    if unit not in field.allowed_units:
        raise ConversionError("E_UNIT_NOT_ALLOWED", f"unit {unit} is not allowed")
    try:
        converted = registry.convert(value, field.dimension, unit, field.canonical_unit)
    except TemperatureError as exc:
        raise ConversionError(exc.code, exc.message) from exc
    rendered = format_fraction(converted, field.precision, field.rounding)
    return converted, rendered


def _record_for(row: DataRow, field: ContractField, cell: FieldCell, rendered: str) -> ConversionRecord:
    return ConversionRecord(
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


def _canonical_rendering_or_none(
    field: ContractField,
    cell: FieldCell,
    registry: UnitRegistry,
) -> str | None:
    """Best-effort canonical rendering for a rejected cell.

    Reuses the exact normalized-path implementation (``parse_fraction`` +
    ``convert_value``, including the affine temperature path). Only the
    out-of-range failure converts cleanly; every other error code fails
    here and yields ``None``.
    """
    if not cell.has_value or not cell.has_unit:
        return None
    try:
        value = parse_fraction(cell.value_text)
        _, rendered = convert_value(field, value, cell.unit_text, registry)
    except (ValueError, ConversionError):
        return None
    return rendered


def _convert_strict(
    contract: Contract,
    rows: Iterable[DataRow],
    registry: UnitRegistry,
    source_name: str,
) -> ConversionResult:
    """Split cells into normalized records and rejected detail lines.

    Classification reuses ``validate_field`` itself, so a cell lands in
    ``rejected`` with exactly the error code ``validate`` reports for it,
    and every cell validate passes is normalized. ``skipped`` is therefore
    always equal to the number of rejected lines — nothing is dropped
    silently.
    """
    records: list[ConversionRecord] = []
    rejected: list[RejectedRecord] = []
    for row in rows:
        for field in contract.fields:
            cell = row.cell(field.field)
            findings = validate_field(row, field, registry)
            error = next((item for item in findings if item.severity == "error"), None)
            if error is not None:
                rejected.append(
                    RejectedRecord(
                        row_number=row.row_number,
                        source=source_name,
                        field=field.field,
                        dimension=field.dimension,
                        code=error.code,
                        message=error.message,
                        raw_value=cell.value_text or None,
                        raw_unit=cell.unit_text or None,
                        canonical_unit=field.canonical_unit,
                        canonical_value=_canonical_rendering_or_none(field, cell, registry),
                    )
                )
                continue
            # validate_field passed every guard convert_value checks (value,
            # unit, registry, allowed set, numeric parse, temperature floor),
            # so this is the same conversion the legacy path would run.
            value = parse_fraction(cell.value_text)
            _, rendered = convert_value(field, value, cell.unit_text, registry)
            records.append(_record_for(row, field, cell, rendered))
    return ConversionResult(
        records=tuple(records),
        skipped=len(rejected),
        rejected=tuple(rejected),
    )


def convert_records(
    contract: Contract,
    rows: Iterable[DataRow],
    registry: UnitRegistry | None = None,
    *,
    strict: bool = False,
    source_name: str = "",
) -> ConversionResult:
    """Emit normalized records; malformed source fields are skipped.

    Validation owns error reporting. Conversion deliberately never invents a
    value for a field that cannot be converted.

    With ``strict=True`` the classification is driven by ``validate_field``:
    every rejected cell is recorded as a :class:`RejectedRecord` carrying the
    validator's error code, and ``skipped`` equals ``len(rejected)``. The
    default legacy mode is unchanged and collects no rejected detail.
    """
    registry = registry or UnitRegistry.bundled()
    if strict:
        return _convert_strict(contract, rows, registry, source_name)
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
            records.append(_record_for(row, field, cell, rendered))
    return ConversionResult(records=tuple(records), skipped=skipped)
