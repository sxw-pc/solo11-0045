"""Stable JSON report construction."""

from __future__ import annotations

import json
from typing import Any

from unitcontract.contract.fingerprint import (
    FINGERPRINT_ALGORITHM,
    FINGERPRINT_SPEC,
    FINGERPRINT_VERSION,
)
from unitcontract.engine.auditor import enrich_finding
from unitcontract.pipeline import RunResult

REPORT_SCHEMA_VERSION = "1"


def build_extensions(result: RunResult) -> dict[str, Any]:
    """Additive section: never renames or removes fields used by older readers.

    The existing ``run.contract_hash`` remains the raw contract byte SHA-256
    for ledger compatibility; the semantic fingerprint lives here only.
    """
    return {
        "contract_fingerprint": {
            "value": result.contract_fingerprint,
            "algorithm": FINGERPRINT_ALGORITHM,
            "version": FINGERPRINT_VERSION,
            "raw_contract_hash": result.contract_hash,
            "raw_contract_hash_algorithm": "sha256",
            "spec": FINGERPRINT_SPEC,
        }
    }


def build_json_report(result: RunResult) -> dict[str, Any]:
    findings = [enrich_finding(item) for item in result.validation.findings]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run": result.run_metadata(),
        "contract": result.contract.as_dict(),
        "summary": result.audit.as_dict(),
        "findings": findings,
        "normalized": result.conversion.as_dicts(),
        "counts": {
            "findings_total": len(findings),
            "normalized_total": len(result.conversion.records),
            "conversion_skipped": result.conversion.skipped,
        },
        "extensions": build_extensions(result),
    }


def dump_json(payload: dict[str, Any]) -> str:
    """Canonical JSON: sorted keys, fixed indent and a trailing newline."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_json(result: RunResult) -> str:
    return dump_json(build_json_report(result))
