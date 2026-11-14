"""Stable JSON report construction."""

from __future__ import annotations

import json
from typing import Any

from unitcontract.engine.auditor import enrich_finding
from unitcontract.pipeline import RunResult

REPORT_SCHEMA_VERSION = "1"


def build_field_sources_extension(result: RunResult) -> dict[str, Any]:
    """Provenance extension: where each field's effective keys came from.

    Lives outside the stable ``contract`` section so existing consumers and
    golden reports keep their exact shape.
    """
    hashes = dict(result.overlay_hashes)
    return {
        "overlays": [
            {"name": label, "sha256": hashes.get(label)}
            for label in result.contract.overlays
        ],
        "fields": result.contract.field_sources_dict(),
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
        "extensions": {
            "field_sources": build_field_sources_extension(result),
        },
    }


def dump_json(payload: dict[str, Any]) -> str:
    """Canonical JSON: sorted keys, fixed indent and a trailing newline."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_json(result: RunResult) -> str:
    return dump_json(build_json_report(result))
