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


def _fingerprint_of(out_dir: Path) -> str:
    payload = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    return payload["extensions"]["contract_fingerprint"]["value"]


def test_equivalent_contracts_share_fingerprint_end_to_end(tmp_path):
    """Demo contract, a comment/order-only copy, an overlay rewrite and a
    hand-composed equivalent must all produce one fingerprint, while the raw
    byte hash still distinguishes them."""
    import yaml

    base_text = CONTRACT.read_text(encoding="utf-8")
    base_payload = yaml.safe_load(base_text)
    base_path = tmp_path / "base.yaml"
    base_path.write_text(base_text, encoding="utf-8")

    only_cosmetics = tmp_path / "copy.yaml"
    reordered = dict(base_payload)
    reordered["fields"] = list(reversed(base_payload["fields"]))
    only_cosmetics.write_text(
        "# only comments and field order differ\n\n"
        + yaml.safe_dump(reordered, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        yaml.safe_dump(
            {
                "fields": [
                    {
                        "field": field["field"],
                        "allowed_units": list(reversed(field["allowed_units"])),
                    }
                    for field in reversed(base_payload["fields"])
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    composed = tmp_path / "composed.yaml"
    composed.write_text(
        "# hand composed, defaults expanded, fields and units reordered\n"
        + yaml.safe_dump(
            {
                "schema_version": "1",
                "name": "hand-composed",
                "description": "independently written equivalent",
                "fields": [
                    dict(field, allowed_units=list(reversed(field["allowed_units"])))
                    for field in reversed(base_payload["fields"])
                ],
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    db_path = tmp_path / "ledger.db"
    fingerprints = {}
    raw_hashes = {}
    for name, contract, extra in (
        ("base", base_path, []),
        ("copy", only_cosmetics, []),
        ("overlay", base_path, ["--overlay", str(overlay)]),
        ("composed", composed, []),
    ):
        out_dir = tmp_path / f"out_{name}"
        argv = [
            "report", "--contract", str(contract), *extra, "--data", str(DATA),
            "--out", str(out_dir), "--no-timestamp", "--max-details", "0",
            "--db", str(db_path),
        ]
        assert main(argv) == 0
        # validate must run cleanly too (exit 1 because the fixture has violations)
        validate_argv = [
            "validate", "--contract", str(contract), *extra, "--data", str(DATA),
            "--no-timestamp", "--max-details", "0",
        ]
        assert main(validate_argv) == 1
        fingerprints[name] = _fingerprint_of(out_dir)
        raw_hashes[name] = json.loads(
            (out_dir / "report.json").read_text(encoding="utf-8")
        )["run"]["contract_hash"]

    assert len(set(fingerprints.values())) == 1
    assert fingerprints["base"] != raw_hashes["base"]  # different algorithms/spaces
    assert len(set(raw_hashes.values())) == 4  # byte-level provenance preserved

    # Repeat one contract: identical fingerprint and identical JSON report.
    repeat_dir = tmp_path / "out_repeat"
    assert main([
        "report", "--contract", str(base_path), "--data", str(DATA),
        "--out", str(repeat_dir), "--no-timestamp", "--max-details", "0",
    ]) == 0
    assert _fingerprint_of(repeat_dir) == fingerprints["base"]
    assert (repeat_dir / "report.json").read_bytes() == (
        tmp_path / "out_base" / "report.json"
    ).read_bytes()

    # Ledger groups all four equivalent runs under one fingerprint.
    from unitcontract.ledger.storage import find_runs_by_fingerprint

    grouped = find_runs_by_fingerprint(db_path, fingerprints["base"])
    assert {row["contract_hash"] for row in grouped} == set(raw_hashes.values())
