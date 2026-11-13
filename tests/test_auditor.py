from __future__ import annotations

from unitcontract.engine.auditor import (
    ERROR_CATALOG,
    audit_validation,
    catalog_for,
    enrich_finding,
)
from unitcontract.engine.validator import ValidationResult
from unitcontract.pipeline import execute


def test_audit_summary_counts(result):
    summary = result.audit.as_dict()
    assert summary["rows_total"] == 18
    assert summary["passed_rows"] == 4
    assert summary["violation_rows"] == 14
    assert summary["warning_rows"] == 2
    assert summary["error_findings"] == 15
    assert summary["warning_findings"] == 2
    assert summary["code_counts"]["E_MISSING"] == 2
    assert summary["code_counts"]["W_PRECISION_EXCEEDED"] == 2
    assert summary["field_violations"]["energy"] == 3
    assert summary["top_fields"][0]["count"] == 3


def test_every_observed_code_has_attribution(result):
    observed = {finding.code for finding in result.validation.findings}
    assert observed <= set(ERROR_CATALOG)
    for code in observed:
        entry = catalog_for(code)
        assert entry["category"]
        assert entry["cause"]
        assert entry["suggestion"]
    enriched = enrich_finding(result.validation.findings[0])
    assert {"category", "cause", "suggestion"} <= set(enriched)


def test_unknown_code_falls_back():
    entry = catalog_for("E_FUTURE")
    assert entry["category"] == "other"
    assert entry["suggestion"]


def test_top_n_and_empty_result():
    empty = ValidationResult("empty", 0, ())
    summary = audit_validation(empty).as_dict()
    assert summary["passed_rows"] == 0
    assert summary["top_fields"] == []
    assert summary["code_counts"] == {}

    result = execute(
        "unitcontract/fixtures/contracts/demo_energy.yaml",
        "unitcontract/fixtures/data/demo_readings.csv",
        no_timestamp=True,
    )
    limited = audit_validation(result.validation, top_n=2)
    assert len(limited.top_fields) == 2
