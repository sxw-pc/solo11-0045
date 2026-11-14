from __future__ import annotations

import json
from pathlib import Path

from unitcontract.report import render_json, render_markdown
from unitcontract.report.html_report import render_html
from unitcontract.report.json_report import build_json_report

EXPECTED = Path(__file__).resolve().parents[1] / "unitcontract" / "fixtures" / "expected"


def test_json_snippet_matches_golden(result):
    report = build_json_report(result)
    snippet = {
        "schema_version": report["schema_version"],
        "run": report["run"],
        "contract": report["contract"],
        "summary": report["summary"],
        "counts": report["counts"],
        "findings": report["findings"][:5],
        "normalized": report["normalized"][:3],
    }
    expected = json.loads((EXPECTED / "report_snippet.json").read_text(encoding="utf-8"))
    assert snippet == expected


def test_markdown_matches_golden(result):
    expected = (EXPECTED / "report.md").read_text(encoding="utf-8")
    assert render_markdown(result, max_details=50) == expected


def test_report_rendering_is_deterministic(result):
    assert render_json(result) == render_json(result)
    assert render_markdown(result) == render_markdown(result)
    assert render_html(result) == render_html(result)


def test_json_has_stable_schema_and_all_details(result):
    payload = json.loads(render_json(result))
    assert payload["schema_version"] == "1"
    assert payload["run"]["rows_total"] == 18
    assert "generated_at" not in payload["run"]
    assert len(payload["findings"]) == 17
    assert len(payload["normalized"]) == 114
    first = payload["normalized"][0]
    assert first["canonical_value"] == "45000000.00"
    assert {"category", "cause", "suggestion"} <= set(payload["findings"][0])


def test_html_is_self_contained_and_safe(result):
    html = render_html(result, max_details=5)
    lowered = html.lower()
    assert "<style>" in lowered
    assert "<script" not in lowered
    assert "<link" not in lowered
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "仅显示前 5 条" in html
    assert "E_UNIT_NOT_ALLOWED" in html


def test_markdown_detail_is_truncated(result):
    text = render_markdown(result, max_details=2)
    assert "仅显示前 2 条，共 17 条" in text
    assert "仅显示前 2 条，共 114 条规范化记录" in text
    assert "| 2 | mass | E_UNIT_NOT_ALLOWED |" in text
    assert "| 3 | energy | E_OUT_OF_RANGE |" not in text


def test_fingerprint_lives_only_in_extension_section(result):
    """Legacy `run` fields are byte-for-byte unchanged; new data is additive."""
    report = build_json_report(result)
    assert "contract_fingerprint" not in report["run"]
    assert set(report["run"]) == set(
        json.loads((EXPECTED / "report_snippet.json").read_text(encoding="utf-8"))["run"]
    )
    extension = report["extensions"]["contract_fingerprint"]
    assert extension["value"] == result.contract_fingerprint
    assert extension["raw_contract_hash"] == result.contract_hash
    assert extension["algorithm"] == "sha256"
    assert extension["spec"]["temperature_range_equivalence"].startswith(
        "not_equivalent_when_canonical_unit_differs"
    )
    # Markdown and HTML golden layouts stay untouched: no new sections.
    markdown = render_markdown(result)
    assert "fingerprint" not in markdown.lower()
    html = render_html(result)
    assert "fingerprint" not in html.lower()
