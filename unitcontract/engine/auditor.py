"""Fixed attribution catalogue and audit summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .validator import Finding, ValidationResult

ERROR_CATALOG: dict[str, dict[str, str]] = {
    "E_MISSING": {
        "category": "completeness",
        "cause": "A declared field has no value in the source row.",
        "suggestion": "Populate the value or make the field optional upstream.",
    },
    "E_UNIT_MISSING": {
        "category": "metadata",
        "cause": "A value is present but its unit is absent.",
        "suggestion": "Add a unit column or nested unit object to the record.",
    },
    "E_NOT_NUMERIC": {
        "category": "type",
        "cause": "The source text is not a finite decimal number.",
        "suggestion": "Normalize placeholders such as n/a to empty values or numbers.",
    },
    "E_UNIT_UNKNOWN": {
        "category": "unit",
        "cause": "The unit is not in the local registry.",
        "suggestion": "Use a registered unit or add an explicit catalogue entry.",
    },
    "E_UNIT_DIM_MISMATCH": {
        "category": "dimension",
        "cause": "The unit exists but measures a different physical dimension.",
        "suggestion": "Correct the unit or move the value to the matching field.",
    },
    "E_UNIT_NOT_ALLOWED": {
        "category": "contract",
        "cause": "The unit is registered but excluded by the field contract.",
        "suggestion": "Convert upstream or extend allowed_units deliberately.",
    },
    "E_OUT_OF_RANGE": {
        "category": "range",
        "cause": "The canonical value lies outside the declared physical range.",
        "suggestion": "Investigate the source reading before accepting the value.",
    },
    "E_TEMP_BELOW_ABSOLUTE": {
        "category": "temperature",
        "cause": "The temperature is below absolute zero after affine conversion.",
        "suggestion": "Check the sign, unit label and sensor calibration.",
    },
    "W_PRECISION_EXCEEDED": {
        "category": "precision",
        "cause": "The source has more decimal places than the contract allows.",
        "suggestion": "Confirm this is intentional; output will be rounded deterministically.",
    },
}

_FALLBACK_ENTRY = {
    "category": "other",
    "cause": "Finding is not present in the built-in attribution catalogue.",
    "suggestion": "Review the raw finding and extend the catalogue deliberately.",
}


def catalog_for(code: str) -> dict[str, str]:
    return dict(ERROR_CATALOG.get(code, _FALLBACK_ENTRY))


def enrich_finding(finding: Finding) -> dict[str, Any]:
    entry = catalog_for(finding.code)
    return {
        **finding.as_dict(),
        "category": entry["category"],
        "cause": entry["cause"],
        "suggestion": entry["suggestion"],
    }


@dataclass(frozen=True)
class AuditReport:
    contract_name: str
    rows_total: int
    passed_rows: int
    violation_rows: int
    warning_rows: int
    error_count: int
    warning_count: int
    code_counts: dict[str, int]
    field_violations: dict[str, int]
    top_fields: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows_total": self.rows_total,
            "passed_rows": self.passed_rows,
            "violation_rows": self.violation_rows,
            "warning_rows": self.warning_rows,
            "error_findings": self.error_count,
            "warning_findings": self.warning_count,
            "code_counts": dict(sorted(self.code_counts.items())),
            "field_violations": dict(self.field_violations),
            "top_fields": [dict(item) for item in self.top_fields],
        }


def audit_validation(result: ValidationResult, top_n: int = 5) -> AuditReport:
    code_counts: dict[str, int] = {}
    for finding in result.findings:
        code_counts[finding.code] = code_counts.get(finding.code, 0) + 1
    field_violations = result.counts_by_field()
    top_fields = tuple(
        {"field": field, "count": count}
        for field, count in list(field_violations.items())[:top_n]
    )
    return AuditReport(
        contract_name=result.contract_name,
        rows_total=result.rows_total,
        passed_rows=result.passed_rows,
        violation_rows=result.violating_rows,
        warning_rows=result.warning_rows,
        error_count=len(result.error_findings),
        warning_count=len(result.warning_findings),
        code_counts=dict(sorted(code_counts.items())),
        field_violations=field_violations,
        top_fields=top_fields,
    )
