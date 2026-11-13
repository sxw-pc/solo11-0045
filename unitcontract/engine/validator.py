"""Contract validation for individual fields and complete datasets."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable

from unitcontract.contract.schema import Contract, ContractField
from unitcontract.engine.reader import DataRow, FieldCell
from unitcontract.units.registry import UnitRegistry, UnknownUnitError
from unitcontract.units.rounding import decimal_places, parse_fraction
from unitcontract.units.temperature import TemperatureError

WARNING_CODES = frozenset({"W_PRECISION_EXCEEDED"})


@dataclass(frozen=True)
class Finding:
    row_number: int
    field: str
    code: str
    message: str
    raw_value: str | None
    unit: str | None
    dimension: str
    severity: str = "error"

    def as_dict(self) -> dict[str, object]:
        return {
            "row": self.row_number,
            "field": self.field,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "raw_value": self.raw_value,
            "unit": self.unit,
            "dimension": self.dimension,
        }


@dataclass(frozen=True)
class ValidationResult:
    contract_name: str
    rows_total: int
    findings: tuple[Finding, ...]

    @property
    def error_findings(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.severity == "error")

    @property
    def warning_findings(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.severity == "warning")

    @property
    def warning_rows(self) -> int:
        return len({item.row_number for item in self.warning_findings})

    @property
    def violating_rows(self) -> int:
        return len({item.row_number for item in self.error_findings})

    @property
    def passed_rows(self) -> int:
        return self.rows_total - self.violating_rows

    def counts_by_code(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.code] = counts.get(finding.code, 0) + 1
        return dict(sorted(counts.items()))

    def counts_by_field(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.error_findings:
            counts[finding.field] = counts.get(finding.field, 0) + 1
        return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))


def _finding(
    row: DataRow,
    field: ContractField,
    code: str,
    message: str,
    raw_value: str | None = None,
    unit: str | None = None,
) -> Finding:
    severity = "warning" if code in WARNING_CODES else "error"
    return Finding(
        row_number=row.row_number,
        field=field.field,
        code=code,
        message=message,
        raw_value=raw_value,
        unit=unit,
        dimension=field.dimension,
        severity=severity,
    )


def _bound_text(value: Fraction | None) -> str:
    if value is None:
        return "-"
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def validate_field(
    row: DataRow,
    field: ContractField,
    registry: UnitRegistry,
) -> list[Finding]:
    """Validate one field in one row. Errors and warnings are ordered."""
    cell: FieldCell = row.cell(field.field)
    if not cell.has_value:
        return [_finding(row, field, "E_MISSING", "value is missing", cell.value_text or None, cell.unit_text or None)]

    value_text = cell.value_text
    unit = cell.unit_text
    if not cell.has_unit:
        return [
            _finding(
                row,
                field,
                "E_UNIT_MISSING",
                "unit is missing; declare a unit column or nested unit",
                value_text,
                None,
            )
        ]

    try:
        registry.get_unit(field.dimension, unit)
    except UnknownUnitError:
        other_dimensions = registry.known_dimensions_for(unit)
        if other_dimensions:
            return [
                _finding(
                    row,
                    field,
                    "E_UNIT_DIM_MISMATCH",
                    f"unit {unit} belongs to {', '.join(other_dimensions)}, not {field.dimension}",
                    value_text,
                    unit,
                )
            ]
        return [
            _finding(
                row,
                field,
                "E_UNIT_UNKNOWN",
                f"unit {unit} is not in the local registry",
                value_text,
                unit,
            )
        ]

    if unit not in field.allowed_units:
        return [
            _finding(
                row,
                field,
                "E_UNIT_NOT_ALLOWED",
                f"unit {unit} is registered but not allowed by this contract",
                value_text,
                unit,
            )
        ]

    try:
        numeric_value = parse_fraction(value_text)
    except ValueError:
        return [
            _finding(
                row,
                field,
                "E_NOT_NUMERIC",
                "value cannot be parsed as a finite decimal number",
                value_text,
                unit,
            )
        ]

    findings: list[Finding] = []
    try:
        canonical_value = registry.convert(
            numeric_value, field.dimension, unit, field.canonical_unit
        )
    except TemperatureError as exc:
        findings.append(
            _finding(row, field, exc.code, exc.message, value_text, unit)
        )
        canonical_value = None

    if canonical_value is not None:
        if field.range_min is not None and canonical_value < field.range_min:
            findings.append(
                _finding(
                    row,
                    field,
                    "E_OUT_OF_RANGE",
                    (
                        f"canonical value {canonical_value} {field.canonical_unit} "
                        f"is below minimum {_bound_text(field.range_min)}"
                    ),
                    value_text,
                    unit,
                )
            )
        if field.range_max is not None and canonical_value > field.range_max:
            findings.append(
                _finding(
                    row,
                    field,
                    "E_OUT_OF_RANGE",
                    (
                        f"canonical value {canonical_value} {field.canonical_unit} "
                        f"exceeds maximum {_bound_text(field.range_max)}"
                    ),
                    value_text,
                    unit,
                )
            )

    places = decimal_places(value_text)
    if places > field.precision:
        findings.append(
            _finding(
                row,
                field,
                "W_PRECISION_EXCEEDED",
                f"{places} decimal places exceed declared precision {field.precision}",
                value_text,
                unit,
            )
        )
    return findings


def validate_records(
    contract: Contract,
    rows: Iterable[DataRow],
    registry: UnitRegistry | None = None,
) -> ValidationResult:
    """Validate every declared field in every row."""
    registry = registry or UnitRegistry.bundled()
    row_list = list(rows)
    findings: list[Finding] = []
    for row in row_list:
        for field in contract.fields:
            findings.extend(validate_field(row, field, registry))
    return ValidationResult(
        contract_name=contract.name,
        rows_total=len(row_list),
        findings=tuple(findings),
    )
