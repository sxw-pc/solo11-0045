"""Field provenance in report extensions; golden compatibility guarantees."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from unitcontract.pipeline import execute
from unitcontract.report import build_json_report, render_json, render_markdown
from unitcontract.report.html_report import render_html

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "unitcontract" / "fixtures"
BASE_CONTRACT = FIXTURES / "contracts" / "demo_energy.yaml"
DATA = FIXTURES / "data" / "demo_readings.csv"


def write_yaml(path: Path, payload: dict) -> Path:
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture()
def overlays(tmp_path: Path) -> tuple[Path, Path]:
    team = write_yaml(
        tmp_path / "team.yaml",
        {"fields": [{"field": "energy", "precision": 3}]},
    )
    site = write_yaml(
        tmp_path / "site.yaml",
        {"fields": [{"field": "energy", "canonical_unit": "kWh",
                     "allowed_units": ["J", "kJ", "kWh", "MWh", "GJ"]}]},
    )
    return team, site


def test_json_extension_records_field_sources(overlays):
    team, site = overlays
    result = execute(BASE_CONTRACT, DATA, no_timestamp=True, overlay_paths=[team, site])
    report = build_json_report(result)
    extension = report["extensions"]["field_sources"]
    assert [item["name"] for item in extension["overlays"]] == ["team.yaml", "site.yaml"]
    assert all(len(item["sha256"]) == 64 for item in extension["overlays"])
    energy = extension["fields"]["energy"]
    assert energy["precision"] == "team.yaml"
    assert energy["canonical_unit"] == "site.yaml"
    assert energy["dimension"] == "demo_energy.yaml"
    # the stable contract section keeps its exact legacy shape
    assert set(report["contract"]["fields"][0]) == {
        "field", "dimension", "canonical_unit", "allowed_units",
        "precision", "rounding", "range",
    }


def test_json_extension_present_without_overlays():
    result = execute(BASE_CONTRACT, DATA, no_timestamp=True)
    report = build_json_report(result)
    extension = report["extensions"]["field_sources"]
    assert extension["overlays"] == []
    energy = extension["fields"]["energy"]
    assert set(energy.values()) == {"demo_energy.yaml"}


def test_markdown_provenance_section_only_with_overlays(overlays):
    team, site = overlays
    plain = execute(BASE_CONTRACT, DATA, no_timestamp=True)
    assert "字段来源" not in render_markdown(plain)
    overlaid = execute(BASE_CONTRACT, DATA, no_timestamp=True, overlay_paths=[team, site])
    text = render_markdown(overlaid)
    assert "## 字段来源（overlay 链）" in text
    assert "`team.yaml` → `site.yaml`" in text
    assert "| energy | precision | team.yaml |" in text
    assert "| energy | canonical_unit | site.yaml |" in text


def test_html_provenance_section_only_with_overlays(overlays):
    team, site = overlays
    plain = execute(BASE_CONTRACT, DATA, no_timestamp=True)
    assert "字段来源" not in render_html(plain)
    overlaid = execute(BASE_CONTRACT, DATA, no_timestamp=True, overlay_paths=[team, site])
    html = render_html(overlaid)
    assert "字段来源" in html
    assert "team.yaml" in html and "site.yaml" in html
    lowered = html.lower()
    assert "http://" not in lowered and "https://" not in lowered


def test_reports_are_byte_identical_across_directories(tmp_path, overlays):
    team, site = overlays
    one = tmp_path / "one"
    two = tmp_path / "two"
    for directory in (one, two):
        directory.mkdir()
        shutil.copy(BASE_CONTRACT, directory / "contract.yaml")
        shutil.copy(DATA, directory / "data.csv")
    result_one = execute(one / "contract.yaml", one / "data.csv",
                         no_timestamp=True, overlay_paths=[team, site])
    result_two = execute(two / "contract.yaml", two / "data.csv",
                         no_timestamp=True, overlay_paths=[team, site])
    assert render_json(result_one) == render_json(result_two)
    assert render_markdown(result_one) == render_markdown(result_two)
    assert result_one.run_id == result_two.run_id


def test_run_metadata_unchanged_by_extension(overlays):
    team, _ = overlays
    plain = execute(BASE_CONTRACT, DATA, no_timestamp=True)
    overlaid = execute(BASE_CONTRACT, DATA, no_timestamp=True, overlay_paths=[team])
    # run metadata keeps the legacy key set in both modes
    assert set(plain.run_metadata()) == set(overlaid.run_metadata())
    payload = json.loads(render_json(overlaid))
    assert "generated_at" not in payload["run"]
