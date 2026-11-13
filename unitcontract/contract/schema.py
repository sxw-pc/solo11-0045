"""Contract data model and strict schema validation."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from unitcontract.units.registry import UnitRegistry
from unitcontract.units.rounding import SUPPORTED_ROUNDING

SUPPORTED_SCHEMA_VERSION = "1"
DIMENSIONS = (
    "length",
    "mass",
    "energy",
    "power",
    "time",
    "temperature",
    "volume",
    "count",
)


@dataclass(frozen=True)
class ContractIssue:
    code: str
    message: str
    location: str
    field: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "location": self.location,
            "field": self.field,
        }

    def __str__(self) -> str:
        prefix = f"{self.location}: " if self.location else ""
        return f"{prefix}[{self.code}] {self.message}"


class ContractValidationError(ValueError):
    """Aggregates all schema errors found in a contract document."""

    def __init__(self, issues: list[ContractIssue]) -> None:
        self.issues = issues
        summary = "; ".join(str(issue) for issue in issues) or "invalid contract"
        super().__init__(summary)


@dataclass(frozen=True)
class ContractField:
    field: str
    dimension: str
    canonical_unit: str
    allowed_units: tuple[str, ...]
    precision: int
    rounding: str
    range_min: Fraction | None = None
    range_max: Fraction | None = None

    def as_dict(self) -> dict[str, Any]:
        range_dict = None
        if self.range_min is not None or self.range_max is not None:
            range_dict = {
                "min": _fraction_text(self.range_min),
                "max": _fraction_text(self.range_max),
            }
        return {
            "field": self.field,
            "dimension": self.dimension,
            "canonical_unit": self.canonical_unit,
            "allowed_units": list(self.allowed_units),
            "precision": self.precision,
            "rounding": self.rounding,
            "range": range_dict,
        }


@dataclass(frozen=True)
class Contract:
    name: str
    description: str
    fields: tuple[ContractField, ...]
    schema_version: str = SUPPORTED_SCHEMA_VERSION
    source_name: str = "<memory>"

    @property
    def field_map(self) -> dict[str, ContractField]:
        return {field.field: field for field in self.fields}

    def field_names(self) -> tuple[str, ...]:
        return tuple(field.field for field in self.fields)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "schema_version": self.schema_version,
            "fields": [field.as_dict() for field in self.fields],
        }


def _fraction_text(value: Fraction | None) -> str | None:
    if value is None:
        return None
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _bound(value: Any) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("range bound must be numeric")
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("range bound must be numeric") from exc


def _location(source_name: str, index: int, name: str | None = None) -> str:
    suffix = f"[{index}]"
    if name:
        suffix += f"(field={name})"
    return f"{source_name}#fields{suffix}"


def build_contract(
    payload: Any,
    source_name: str,
    registry: UnitRegistry | None = None,
) -> Contract:
    """Validate a parsed YAML payload and return an immutable contract."""
    registry = registry or UnitRegistry.bundled()
    issues: list[ContractIssue] = []

    def add(code: str, message: str, location: str, field: str | None = None) -> None:
        issues.append(ContractIssue(code, message, location, field))

    if not isinstance(payload, dict):
        raise ContractValidationError(
            [ContractIssue("E_SCHEMA_ROOT", "contract root must be a mapping", source_name)]
        )

    schema_version = payload.get("schema_version", SUPPORTED_SCHEMA_VERSION)
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        add(
            "E_SCHEMA_VERSION",
            f"unsupported schema_version: {schema_version!r}",
            f"{source_name}#schema_version",
        )
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        add("E_CONTRACT_NAME", "contract name must be a non-empty string", f"{source_name}#name")
        name = "<invalid>"
    description = payload.get("description", "")
    if not isinstance(description, str):
        add("E_SCHEMA_ROOT", "description must be a string", f"{source_name}#description")
        description = ""

    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        add("E_FIELDS_MISSING", "fields must be a non-empty list", f"{source_name}#fields")
        raise ContractValidationError(issues)

    defaults = payload.get("defaults")
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, dict):
        add("E_SCHEMA_ROOT", "defaults must be a mapping", f"{source_name}#defaults")
        defaults = {}

    fields: list[ContractField] = []
    seen: set[str] = set()
    for index, raw_item in enumerate(raw_fields):
        loc = _location(source_name, index)
        if not isinstance(raw_item, dict):
            add("E_SCHEMA_ROOT", "each field declaration must be a mapping", loc)
            continue
        merged = dict(defaults)
        merged.update(raw_item)
        field_name = merged.get("field")
        field_label = field_name if isinstance(field_name, str) else None
        loc = _location(source_name, index, field_label)
        if not isinstance(field_name, str) or not field_name.strip():
            add("E_FIELD_NAME", "field must be a non-empty string", loc)
            continue
        field_name = field_name.strip()
        if field_name in seen:
            add("E_FIELD_DUPLICATE", f"duplicate field: {field_name}", loc, field_name)
            continue
        seen.add(field_name)

        dimension = merged.get("dimension")
        if not isinstance(dimension, str) or not dimension:
            add("E_DIM_UNKNOWN", "dimension must be declared", loc, field_name)
            continue
        if not registry.has_dimension(dimension):
            add("E_DIM_UNKNOWN", f"unknown dimension: {dimension}", loc, field_name)
            continue

        canonical = merged.get("canonical_unit")
        if not isinstance(canonical, str) or not canonical:
            add("E_CANON_MISSING", "canonical_unit must be declared", loc, field_name)
            canonical = None
        else:
            canonical = canonical.strip()
            if not registry.has_unit(dimension, canonical):
                other = registry.known_dimensions_for(canonical)
                code = "E_UNIT_DIM_MISMATCH" if other else "E_UNIT_UNKNOWN"
                add(code, f"canonical unit {canonical} does not belong to {dimension}", loc, field_name)
                canonical = None

        raw_allowed = merged.get("allowed_units")
        if not isinstance(raw_allowed, list) or not raw_allowed:
            add("E_ALLOWED_MISSING", "allowed_units must be a non-empty list", loc, field_name)
            raw_allowed = []
        allowed: list[str] = []
        for unit in raw_allowed:
            if not isinstance(unit, str) or not unit:
                add("E_ALLOWED_ITEM", "allowed unit must be a non-empty string", loc, field_name)
                continue
            if unit in allowed:
                continue
            if not registry.has_unit(dimension, unit):
                other = registry.known_dimensions_for(unit)
                code = "E_UNIT_DIM_MISMATCH" if other else "E_UNIT_UNKNOWN"
                add(code, f"allowed unit {unit} does not belong to {dimension}", loc, field_name)
                continue
            allowed.append(unit)
        if canonical is not None and canonical not in allowed:
            add(
                "E_CANON_NOT_IN_ALLOWED",
                f"canonical unit {canonical} is absent from allowed_units",
                loc,
                field_name,
            )

        precision = merged.get("precision", 2)
        if isinstance(precision, bool) or not isinstance(precision, int) or precision < 0:
            add("E_PRECISION_INVALID", "precision must be a non-negative integer", loc, field_name)
            precision = 2
        rounding = merged.get("rounding", "half_even")
        if rounding not in SUPPORTED_ROUNDING:
            add(
                "E_ROUNDING_UNKNOWN",
                f"rounding must be one of {', '.join(SUPPORTED_ROUNDING)}",
                loc,
                field_name,
            )
            rounding = "half_even"

        range_min: Fraction | None = None
        range_max: Fraction | None = None
        raw_range = merged.get("range")
        if raw_range is not None:
            if not isinstance(raw_range, dict):
                add("E_RANGE_INVALID", "range must be a mapping with min/max", loc, field_name)
            else:
                try:
                    if raw_range.get("min") is not None:
                        range_min = _bound(raw_range["min"])
                    if raw_range.get("max") is not None:
                        range_max = _bound(raw_range["max"])
                except ValueError as exc:
                    add("E_RANGE_INVALID", str(exc), loc, field_name)
                if range_min is not None and range_max is not None and range_min > range_max:
                    add("E_RANGE_INVALID", "range.min must not exceed range.max", loc, field_name)

        if canonical is None or not allowed:
            continue
        fields.append(
            ContractField(
                field=field_name,
                dimension=dimension,
                canonical_unit=canonical,
                allowed_units=tuple(allowed),
                precision=precision,
                rounding=rounding,
                range_min=range_min,
                range_max=range_max,
            )
        )

    if issues:
        raise ContractValidationError(issues)
    return Contract(
        name=name.strip(),
        description=description,
        fields=tuple(fields),
        schema_version=str(schema_version),
        source_name=source_name,
    )
