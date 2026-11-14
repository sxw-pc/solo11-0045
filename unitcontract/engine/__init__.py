"""Reading, validation and deterministic conversion."""

from .canonical import CanonicalValue, resolve_canonical
from .converter import (
    ConversionError,
    ConversionRecord,
    ConversionResult,
    convert_records,
    convert_value,
)
from .reader import DataReadError, DataRow, FieldCell, detect_format, read_rows, row_is_blank
from .validator import Finding, ValidationResult, validate_field, validate_records

__all__ = [
    "CanonicalValue",
    "ConversionError",
    "ConversionRecord",
    "ConversionResult",
    "DataReadError",
    "DataRow",
    "FieldCell",
    "Finding",
    "ValidationResult",
    "convert_records",
    "convert_value",
    "detect_format",
    "read_rows",
    "resolve_canonical",
    "row_is_blank",
    "validate_field",
    "validate_records",
]
