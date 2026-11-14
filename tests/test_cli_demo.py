from __future__ import annotations

import csv
import json
from pathlib import Path

from unitcontract.cli import main
from unitcontract.ledger.storage import get_run


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "unitcontract" / "fixtures" / "contracts" / "demo_energy.yaml"
DATA = ROOT / "unitcontract" / "fixtures" / "data" / "demo_readings.csv"


def test_demo_end_to_end_and_deterministic(tmp_path):
    out_one = tmp_path / "one"
    out_two = tmp_path / "two"
    assert main(["demo", "--out", str(out_one), "--no-timestamp", "--rows", "220"]) == 0
    assert main(["demo", "--out", str(out_two), "--no-timestamp", "--rows", "220"]) == 0
    for directory in (out_one, out_two):
        for name in ("contract.yaml", "data.csv", "ledger.db", "report.json", "report.md", "report.html"):
            assert (directory / name).is_file()
        assert (directory / "unitcontract.log").is_file()
        with (directory / "data.csv").open(encoding="utf-8", newline="") as handle:
            assert len(list(csv.DictReader(handle))) == 220
    for name in ("report.json", "report.md", "report.html"):
        assert (out_one / name).read_bytes() == (out_two / name).read_bytes()
    payload = json.loads((out_one / "report.json").read_text(encoding="utf-8"))
    assert payload["run"]["rows_total"] == 220
    assert payload["summary"]["violation_rows"] >= 10
    assert payload["counts"]["findings_total"] > 0
    run_id = payload["run"]["run_id"]
    stored = get_run(out_one / "ledger.db", run_id)
    assert stored is not None
    assert stored["contract_hash"] == payload["run"]["contract_hash"]
    assert (out_one / "runs" / run_id / "report.html").is_file()
    assert len(list((out_one / "runs" / run_id).glob("report.*"))) == 3


def test_cli_convert_and_validate(tmp_path):
    output = tmp_path / "normalized.jsonl"
    assert main([
        "convert", "--contract", str(CONTRACT), "--data", str(DATA), "--out", str(output)
    ]) == 0
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 114
    first = json.loads(lines[0])
    assert first["canonical_value"] == "45000000.00"
    assert first["canonical_unit"] == "J"

    db_path = tmp_path / "validate.db"
    code = main([
        "validate", "--contract", str(CONTRACT), "--data", str(DATA),
        "--no-timestamp", "--db", str(db_path), "--max-details", "2",
    ])
    assert code == 1
    assert db_path.is_file()


def test_cli_overlay_only_touches_explicit_field_keys(tmp_path):
    overlay = tmp_path / "tuning.yaml"
    overlay.write_text(
        "schema_version: '1'\n"
        "name: synthetic-facility-readings\n"
        "fields:\n"
        "  - field: energy\n"
        "    precision: 4\n"
        "    rounding: half_up\n",
        encoding="utf-8",
    )
    out = tmp_path / "overlay_report"
    code = main([
        "report", "--contract", str(CONTRACT), "--overlay", str(overlay),
        "--data", str(DATA), "--out", str(out), "--no-timestamp",
    ])
    assert code == 0
    payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
    energy = next(f for f in payload["contract"]["fields"] if f["field"] == "energy")
    # Overlay changes only precision/rounding; list-valued keys are inherited.
    assert energy["precision"] == 4
    assert energy["rounding"] == "half_up"
    assert energy["allowed_units"] == ["J", "kJ", "kWh", "MWh", "GJ"]
    assert energy["canonical_unit"] == "J"
    # Inherited contract stays valid: no E_ALLOWED_MISSING style schema error.
    assert "E_ALLOWED_MISSING" not in {item["code"] for item in payload["findings"]}
    assert payload["counts"]["normalized_total"] == 114

    converted = tmp_path / "overlay_normalized.jsonl"
    assert main([
        "convert", "--contract", str(CONTRACT), "--overlay", str(overlay),
        "--data", str(DATA), "--out", str(converted),
    ]) == 0
    records = [json.loads(line) for line in converted.read_text(encoding="utf-8").splitlines()]
    assert records[0]["precision"] == 4
    assert records[0]["rounding"] == "half_up"


def test_cli_report_command(tmp_path):
    out = tmp_path / "reports"
    db_path = tmp_path / "reports.db"
    code = main([
        "report", "--contract", str(CONTRACT), "--data", str(DATA),
        "--out", str(out), "--no-timestamp", "--max-details", "3", "--db", str(db_path),
    ])
    assert code == 0
    for name in ("report.json", "report.md", "report.html", "unitcontract.log"):
        assert (out / name).is_file()
    payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1"
    stored = get_run(db_path, payload["run"]["run_id"])
    assert stored is not None


def test_cli_rejects_bad_contract(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: broken\nfields: []\n", encoding="utf-8")
    assert main(["validate", "--contract", str(bad), "--data", str(DATA)]) == 2
