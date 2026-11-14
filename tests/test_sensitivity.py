"""Tests for the ``sensitive`` trial command and its row-level impact buckets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from unitcontract.cli import main
from unitcontract.contract.loader import read_yaml_document
from unitcontract.contract.schema import ContractValidationError
from unitcontract.engine.sensitivity import (
    BUCKET_PASS_TO_VIOLATION,
    BUCKET_ROUNDING_TEXT_ONLY,
    BUCKET_UNCHANGED,
    BUCKET_VIOLATION_TO_PASS,
    SensitivityRunner,
    load_trial_contract,
    run_sensitivity,
    validate_trial_structure,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "unitcontract" / "fixtures" / "contracts" / "demo_energy.yaml"
DATA = ROOT / "unitcontract" / "fixtures" / "data" / "demo_readings.csv"


def write_trial(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


ENERGY_PRECISION_TRIAL = """
fields:
  - field: energy
    precision: 0
"""

TEMPERATURE_RANGE_TRIAL = """
fields:
  - field: temperature
    range: {min: -50, max: 20}
"""


@pytest.fixture(scope="module")
def runner() -> SensitivityRunner:
    return SensitivityRunner(CONTRACT, DATA)


def test_energy_precision_trial_only_changes_rendered_text(runner, tmp_path):
    trial = write_trial(tmp_path, "energy.yaml", ENERGY_PRECISION_TRIAL)
    result = runner.run_trial(trial, index=0)
    assert result.touched_fields == ("energy",)
    impact = result.impacts[0]
    assert impact.changed_keys == ("precision",)
    assert impact.before["precision"] == 2
    assert impact.after["precision"] == 0
    # Precision cannot introduce error findings; every affected clean row is
    # tracked in the dedicated rounding-text bucket, never pass-to-violation.
    assert impact.pass_to_violation["count"] == 0
    assert impact.violation_to_pass["count"] == 0
    text_only = impact.rounding_text_only
    assert text_only["count"] == 15
    assert text_only["rows"] == [1, 2, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18]
    assert text_only["truncated"] is False
    # Buckets partition all 18 rows for the touched field.
    assert (
        impact.pass_to_violation["count"]
        + impact.violation_to_pass["count"]
        + impact.rounding_text_only["count"]
        + impact.unchanged["count"]
        == 18
    )
    assert result.totals["affected_rows"] == 15
    assert result.baseline_violation_rows == result.trial_violation_rows == 14


def test_temperature_range_trial_marks_pass_to_violation_rows(runner, tmp_path):
    trial = write_trial(tmp_path, "temperature.yaml", TEMPERATURE_RANGE_TRIAL)
    result = runner.run_trial(trial, index=0)
    impact = result.impacts[0]
    assert impact.changed_keys == ("range",)
    assert impact.before["range"] == {"min": "-50", "max": "60"}
    assert impact.after["range"] == {"min": "-50", "max": "20"}
    p2v = impact.pass_to_violation
    assert p2v["count"] == 5
    assert p2v["rows"] == [1, 2, 5, 6, 16]
    assert p2v["codes"] == {"E_OUT_OF_RANGE": 5}
    assert impact.violation_to_pass["count"] == 0
    assert impact.rounding_text_only["count"] == 0
    assert impact.unchanged["count"] == 13
    assert result.trial_violation_rows == 17
    assert result.totals["pass_to_violation_rows"] == 5


def test_loosened_range_moves_violating_rows_back_to_passing(runner, tmp_path):
    trial = write_trial(
        tmp_path,
        "loose.yaml",
        "fields:\n  - field: temperature\n    range: {min: -50, max: 100}\n",
    )
    result = runner.run_trial(trial, index=0)
    impact = result.impacts[0]
    v2p = impact.violation_to_pass
    # Row 9 (85.0 C) was E_OUT_OF_RANGE against the 60 C ceiling and now passes.
    assert v2p["count"] == 1
    assert v2p["rows"] == [9]
    assert v2p["codes"] == {"E_OUT_OF_RANGE": 1}
    assert impact.pass_to_violation["count"] == 0
    assert result.trial_violation_rows == 13


def test_rounding_mode_change_is_text_only_when_still_compliant(
    contract, registry, tmp_path
):
    # A clean in-range fractional length row rendered via half_even vs
    # truncate at precision 2: the compliance verdict is unchanged, only the
    # converter's rendered text moves.
    data = tmp_path / "rows.csv"
    data.write_text(
        "length,length__unit\n1.006,m\n",
        encoding="utf-8",
    )
    trial = write_trial(
        tmp_path,
        "rounding.yaml",
        "fields:\n  - field: length\n    rounding: truncate\n",
    )
    local_runner = SensitivityRunner(CONTRACT, data, registry=registry)
    baseline_text = local_runner._baseline_rendered[(1, "length")]
    result = local_runner.run_trial(trial, index=0)
    impact = result.impacts[0]
    assert impact.changed_keys == ("rounding",)
    assert impact.before["rounding"] == "half_even"
    assert impact.after["rounding"] == "truncate"
    assert impact.pass_to_violation["count"] == 0
    assert impact.violation_to_pass["count"] == 0
    assert impact.rounding_text_only["count"] == 1
    assert impact.rounding_text_only["rows"] == [1]
    assert baseline_text == "1.01"  # half_even
    # The baseline snapshot is never mutated by running a trial.
    assert local_runner._baseline_rendered[(1, "length")] == "1.01"


def test_trials_are_independent_and_deterministic(runner, tmp_path):
    energy = write_trial(tmp_path, "energy.yaml", ENERGY_PRECISION_TRIAL)
    temperature = write_trial(tmp_path, "temperature.yaml", TEMPERATURE_RANGE_TRIAL)

    first = runner.run_trial(temperature, index=0).as_dict()
    # Run the unrelated energy trial in between; baseline state must not move.
    runner.run_trial(energy, index=1)
    second = runner.run_trial(temperature, index=2).as_dict()
    # Index differs by design; every impact-relevant section stays identical.
    for payload in (first, second):
        payload.pop("trial_index")
        payload.pop("trial_path")
    assert first == second

    # Repeating the same trial yields byte-identical JSON summaries on disk.
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    results_a, master_a = run_sensitivity(
        CONTRACT, DATA, [energy, temperature], out_a
    )
    results_b, master_b = run_sensitivity(
        CONTRACT, DATA, [energy, temperature], out_b
    )
    for name in (
        "sensitivity_00_energy.json",
        "sensitivity_01_temperature.json",
        "sensitivity_summary.json",
    ):
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes()
    assert master_a.read_bytes() == master_b.read_bytes()
    assert [r.touched_fields for r in results_a] == [
        ("energy",),
        ("temperature",),
    ]


def test_row_lists_are_truncated_but_counts_stay_exact(runner, tmp_path):
    trial = write_trial(tmp_path, "energy.yaml", ENERGY_PRECISION_TRIAL)
    result = runner.run_trial(trial, index=0, max_rows=5)
    text_only = result.impacts[0].rounding_text_only
    assert text_only["count"] == 15
    assert len(text_only["rows"]) == 5
    assert text_only["rows"] == [1, 2, 6, 7, 8]
    assert text_only["truncated"] is True
    assert text_only["rows_total"] == 18


def test_trial_rejects_keys_outside_the_three_allowed(contract, tmp_path):
    trial = write_trial(
        tmp_path,
        "bad.yaml",
        "fields:\n  - field: energy\n    dimension: energy\n",
    )
    with pytest.raises(ContractValidationError) as caught:
        validate_trial_structure(
            read_yaml_document(trial), contract, str(trial)
        )
    codes = {issue.code for issue in caught.value.issues}
    assert codes == {"E_SCHEMA_ROOT"}
    assert all("dimension" in issue.message for issue in caught.value.issues)


def test_trial_rejects_foreign_top_level_and_unknown_field(contract, tmp_path):
    trial = write_trial(
        tmp_path,
        "bad.yaml",
        "name: hijacked\nfields:\n  - field: ghost\n    precision: 1\n",
    )
    with pytest.raises(ContractValidationError) as caught:
        validate_trial_structure(
            read_yaml_document(trial), contract, str(trial)
        )
    messages = " ".join(issue.message for issue in caught.value.issues)
    locations = " ".join(issue.location for issue in caught.value.issues)
    assert "name" in messages
    assert "ghost" in messages
    assert str(trial) in locations


@pytest.mark.parametrize(
    ("text", "expected_code"),
    [
        ("fields:\n  - field: energy\n    precision: -1\n", "E_PRECISION_INVALID"),
        ("fields:\n  - field: energy\n    rounding: bankers\n", "E_ROUNDING_UNKNOWN"),
        (
            "fields:\n  - field: energy\n    range: {min: 9, max: 1}\n",
            "E_RANGE_INVALID",
        ),
    ],
)
def test_trial_value_errors_keep_existing_codes(
    runner, contract, tmp_path, text, expected_code
):
    trial = write_trial(tmp_path, "bad_value.yaml", text)
    with pytest.raises(ContractValidationError) as caught:
        load_trial_contract(trial, runner.baseline_payload, contract, runner.registry)
    assert expected_code in {issue.code for issue in caught.value.issues}


def test_trial_requires_non_empty_field_list(contract, tmp_path):
    trial = write_trial(tmp_path, "empty.yaml", "fields: []\n")
    with pytest.raises(ContractValidationError) as caught:
        validate_trial_structure(
            read_yaml_document(trial), contract, str(trial)
        )
    assert caught.value.issues[0].code == "E_FIELDS_MISSING"


def test_trial_duplicate_field_is_rejected(contract, tmp_path):
    trial = write_trial(
        tmp_path,
        "dup.yaml",
        "fields:\n  - field: energy\n    precision: 1\n  - field: energy\n    precision: 0\n",
    )
    with pytest.raises(ContractValidationError) as caught:
        validate_trial_structure(
            read_yaml_document(trial), contract, str(trial)
        )
    assert "E_FIELD_DUPLICATE" in {issue.code for issue in caught.value.issues}


def test_run_sensitivity_writes_per_trial_and_aggregate_json(runner, tmp_path):
    energy = tmp_path / "energy.yaml"
    energy.write_text(ENERGY_PRECISION_TRIAL, encoding="utf-8")
    temperature = tmp_path / "temperature.yaml"
    temperature.write_text(TEMPERATURE_RANGE_TRIAL, encoding="utf-8")
    out = tmp_path / "summaries"
    results, master_path = run_sensitivity(
        CONTRACT, DATA, [energy, temperature], out, max_rows=100
    )
    assert master_path == out / "sensitivity_summary.json"
    assert len(results) == 2
    files = {path.name for path in out.glob("*.json")}
    assert files == {
        "sensitivity_00_energy.json",
        "sensitivity_01_temperature.json",
        "sensitivity_summary.json",
    }
    master = json.loads(master_path.read_text(encoding="utf-8"))
    assert master["rows_total"] == 18
    assert [entry["trial_index"] for entry in master["trials"]] == [0, 1]
    # Aggregate keeps trials independently comparable for picking the smallest
    # impact surface.
    entries = {entry["summary_file"]: entry for entry in master["trials"]}
    assert entries["sensitivity_00_energy.json"]["affected_rows"] == 15
    assert entries["sensitivity_01_temperature.json"]["affected_rows"] == 5
    first = json.loads((out / "sensitivity_00_energy.json").read_text(encoding="utf-8"))
    assert first["schema_version"] == "1"
    field_summary = first["fields"][0]
    for bucket in (
        BUCKET_PASS_TO_VIOLATION,
        BUCKET_VIOLATION_TO_PASS,
        BUCKET_ROUNDING_TEXT_ONLY,
        BUCKET_UNCHANGED,
    ):
        assert {"count", "rows", "rows_total", "truncated"} <= set(
            field_summary[bucket]
        )


def test_cli_sensitive_exit_codes_and_files(tmp_path):
    energy = tmp_path / "energy.yaml"
    energy.write_text(ENERGY_PRECISION_TRIAL, encoding="utf-8")
    temperature = tmp_path / "temperature.yaml"
    temperature.write_text(TEMPERATURE_RANGE_TRIAL, encoding="utf-8")
    out = tmp_path / "cli_out"
    code = main(
        [
            "sensitive",
            "--contract",
            str(CONTRACT),
            "--data",
            str(DATA),
            "--trial",
            str(energy),
            "--trial",
            str(temperature),
            "--out",
            str(out),
        ]
    )
    # Affected rows were found in both trials.
    assert code == 1
    assert (out / "sensitivity_summary.json").is_file()

    # A no-op trial (same values as the baseline) exits 0.
    noop = tmp_path / "noop.yaml"
    noop.write_text(
        "fields:\n  - field: energy\n    precision: 2\n    rounding: half_even\n",
        encoding="utf-8",
    )
    noop_out = tmp_path / "noop_out"
    code = main(
        [
            "sensitive",
            "--contract",
            str(CONTRACT),
            "--data",
            str(DATA),
            "--trial",
            str(noop),
            "--out",
            str(noop_out),
        ]
    )
    assert code == 0
    noop_summary = json.loads(
        (noop_out / "sensitivity_00_noop.json").read_text(encoding="utf-8")
    )
    assert noop_summary["touched_fields"] == []
    assert noop_summary["totals"]["affected_rows"] == 0

    # Structural contract errors keep the shared exit code 2.
    bad = tmp_path / "bad.yaml"
    bad.write_text("fields:\n  - field: energy\n    allowed_units: [J]\n", encoding="utf-8")
    assert main(
        [
            "sensitive",
            "--contract",
            str(CONTRACT),
            "--data",
            str(DATA),
            "--trial",
            str(bad),
            "--out",
            str(tmp_path / "bad_out"),
        ]
    ) == 2


def test_cli_sensitive_reproduces_validate_error_codes(tmp_path):
    """The pass-to-violation rows must reproduce under a standalone validate."""
    from unitcontract.engine.validator import validate_records

    trial = tmp_path / "temperature.yaml"
    trial.write_text(TEMPERATURE_RANGE_TRIAL, encoding="utf-8")
    out = tmp_path / "summaries"
    run_sensitivity(CONTRACT, DATA, [trial], out)
    summary = json.loads(
        (out / "sensitivity_00_temperature.json").read_text(encoding="utf-8")
    )
    field_summary = next(
        item for item in summary["fields"] if item["field"] == "temperature"
    )
    p2v_rows = field_summary[BUCKET_PASS_TO_VIOLATION]["rows"]
    assert p2v_rows == [1, 2, 5, 6, 16]

    # Rebuild exactly the contract the trial used and run the normal validator.
    runner = SensitivityRunner(CONTRACT, DATA)
    merged_contract = load_trial_contract(
        trial, runner.baseline_payload, runner.baseline, runner.registry
    )
    validation = validate_records(merged_contract, runner.rows, runner.registry)
    reproduced = {
        finding.row_number: finding.code
        for finding in validation.error_findings
        if finding.field == "temperature" and finding.row_number in p2v_rows
    }
    assert reproduced == {row: "E_OUT_OF_RANGE" for row in p2v_rows}
