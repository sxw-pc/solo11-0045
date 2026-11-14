"""Compare subcommand: field-level spec diffs and row-level conclusion diffs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from unitcontract.cli import main
from unitcontract.compare import run_compare, write_compare_reports


def write_yaml(path: Path, payload: dict) -> Path:
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture()
def compare_inputs(tmp_path: Path) -> dict[str, Path]:
    base = write_yaml(
        tmp_path / "base.yaml",
        {
            "name": "compare-base",
            "fields": [
                {
                    "field": "energy",
                    "dimension": "energy",
                    "canonical_unit": "J",
                    "allowed_units": ["J", "kWh"],
                    "precision": 2,
                    "rounding": "half_even",
                    "range": {"min": 0, "max": 100000},
                },
                {
                    "field": "count",
                    "dimension": "count",
                    "canonical_unit": "count",
                    "allowed_units": ["count"],
                    "precision": 0,
                },
            ],
        },
    )
    left = write_yaml(
        tmp_path / "left.yaml",
        {"fields": [{"field": "energy", "precision": 4}]},
    )
    right = write_yaml(
        tmp_path / "right.yaml",
        {"fields": [{"field": "energy", "range": {"min": 0, "max": 1000}}]},
    )
    data = tmp_path / "data.csv"
    data.write_text(
        "energy,energy__unit,count,count__unit\n"
        "5000,J,3,count\n"       # passes left range, violates right range (max 1000)
        "100,J,4,count\n"        # passes both, rendered with different precision
        "bad,kWh,5,count\n",     # E_NOT_NUMERIC on both sides
        encoding="utf-8",
    )
    return {"base": base, "left": left, "right": right, "data": data}


def test_compare_field_diffs_include_sources(compare_inputs, tmp_path):
    right_extra = write_yaml(
        tmp_path / "right_extra.yaml",
        {
            "fields": [
                {"field": "energy", "range": {"min": 0, "max": 1000}},
                {
                    "field": "duration",
                    "dimension": "time",
                    "canonical_unit": "s",
                    "allowed_units": ["s"],
                    "precision": 0,
                },
            ]
        },
    )
    result = run_compare(
        compare_inputs["base"],
        compare_inputs["data"],
        left_overlays=[compare_inputs["left"]],
        right_overlays=[right_extra],
    )
    diffs = {(item["field"], item["key"]): item for item in result.summary["field_diffs"]}
    assert ("energy", "precision") in diffs
    precision = diffs[("energy", "precision")]
    assert precision["left"] == 4
    assert precision["right"] == 2
    assert precision["left_source"] == "left.yaml"
    assert precision["right_source"] == "base.yaml"
    energy_range = diffs[("energy", "range")]
    assert energy_range["right"] == {"min": "0", "max": "1000"}
    assert energy_range["right_source"] == "right_extra.yaml"
    # field introduced only on the right side
    introduced = diffs[("duration", "field")]
    assert introduced["left"] == "<absent>"
    assert introduced["right"] == "present"
    assert introduced["right_source"] == "right_extra.yaml"
    assert result.summary["field_diff_count"] == len(result.summary["field_diffs"])


def test_compare_row_level_conclusion_counts(compare_inputs):
    result = run_compare(
        compare_inputs["base"],
        compare_inputs["data"],
        left_overlays=[compare_inputs["left"]],
        right_overlays=[compare_inputs["right"]],
    )
    validation = result.summary["validation_diff"]
    assert validation["rows_total"] == 3
    # row 1: 5000 J violates only the right side's tightened range
    assert validation["rows_with_different_status"] == 1
    assert validation["status_mismatch_rows"] == [1]
    assert validation["left"]["violating_rows"] == 1  # only row 3 (E_NOT_NUMERIC)
    assert validation["right"]["violating_rows"] == 2
    delta = validation["code_count_delta"]
    assert delta["E_OUT_OF_RANGE"] == {"left": 0, "right": 1, "delta": 1}
    assert "E_NOT_NUMERIC" not in delta  # identical on both sides


def test_compare_conversion_value_mismatches(compare_inputs):
    result = run_compare(
        compare_inputs["base"],
        compare_inputs["data"],
        left_overlays=[compare_inputs["left"]],
        right_overlays=[compare_inputs["right"]],
    )
    conversion = result.summary["conversion_diff"]
    # precision 4 vs 2 changes the rendered value for both numeric rows
    assert conversion["value_mismatches"] == 2
    pairs = {(item["row"], item["field"]): item for item in conversion["mismatched_pairs"]}
    assert pairs[(2, "energy")]["left"] == "100.0000"
    assert pairs[(2, "energy")]["right"] == "100.00"
    assert conversion["left"]["records"] == conversion["right"]["records"]


def test_compare_identical_sides_have_no_diffs(compare_inputs):
    result = run_compare(
        compare_inputs["base"],
        compare_inputs["data"],
        left_overlays=[compare_inputs["left"]],
        right_overlays=[compare_inputs["left"]],
    )
    summary = result.summary
    assert summary["field_diffs"] == []
    assert summary["validation_diff"]["rows_with_different_status"] == 0
    assert summary["validation_diff"]["rows_with_different_findings"] == 0
    assert summary["conversion_diff"]["value_mismatches"] == 0


def test_compare_supports_overlay_chains_per_side(compare_inputs, tmp_path):
    extra = write_yaml(tmp_path / "extra.yaml", {"fields": [{"field": "energy", "rounding": "truncate"}]})
    result = run_compare(
        compare_inputs["base"],
        compare_inputs["data"],
        left_overlays=[compare_inputs["left"], extra],
        right_overlays=[],
    )
    assert [item["name"] for item in result.summary["left"]["overlays"]] == ["left.yaml", "extra.yaml"]
    assert result.summary["right"]["overlays"] == []
    keys = {(item["field"], item["key"]) for item in result.summary["field_diffs"]}
    assert ("energy", "precision") in keys
    assert ("energy", "rounding") in keys


def test_compare_cli_writes_reports(tmp_path, compare_inputs):
    out_dir = tmp_path / "compare_out"
    code = main([
        "compare",
        "--contract", str(compare_inputs["base"]),
        "--data", str(compare_inputs["data"]),
        "--left-overlay", str(compare_inputs["left"]),
        "--right-overlay", str(compare_inputs["right"]),
        "--out", str(out_dir),
    ])
    assert code == 0
    summary = json.loads((out_dir / "compare.json").read_text(encoding="utf-8"))
    assert summary["schema_version"] == "1"
    assert summary["data"]["rows_total"] == 3
    assert summary["validation_diff"]["rows_with_different_status"] == 1
    markdown = (out_dir / "compare.md").read_text(encoding="utf-8")
    assert "字段级口径差异" in markdown
    assert "行级通过/违规结论不一致行数: 1" in markdown
    assert "left.yaml" in markdown and "right.yaml" in markdown


def test_compare_cli_rejects_bad_contract(tmp_path, compare_inputs):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: broken\nfields: []\n", encoding="utf-8")
    code = main([
        "compare",
        "--contract", str(bad),
        "--data", str(compare_inputs["data"]),
        "--left-overlay", str(compare_inputs["left"]),
        "--right-overlay", str(compare_inputs["right"]),
        "--out", str(tmp_path / "out"),
    ])
    assert code == 2  # same error semantics as validate/report


def test_compare_reports_are_deterministic(tmp_path, compare_inputs):
    first = run_compare(
        compare_inputs["base"], compare_inputs["data"],
        left_overlays=[compare_inputs["left"]], right_overlays=[compare_inputs["right"]],
    )
    second = run_compare(
        compare_inputs["base"], compare_inputs["data"],
        left_overlays=[compare_inputs["left"]], right_overlays=[compare_inputs["right"]],
    )
    assert first.summary == second.summary
    one = write_compare_reports(first.summary, tmp_path / "one")
    two = write_compare_reports(second.summary, tmp_path / "two")
    assert one["json"].read_bytes() == two["json"].read_bytes()
    assert one["md"].read_bytes() == two["md"].read_bytes()
