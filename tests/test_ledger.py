from __future__ import annotations

import sqlite3

from unitcontract.ledger.storage import (
    count_runs,
    delete_run,
    get_findings,
    get_run,
    init_db,
    list_runs,
    save_run,
)


def test_save_and_read_run(result, tmp_path):
    db_path = tmp_path / "nested" / "ledger.db"
    report_dir = tmp_path / "reports"
    run_id = save_run(result, db_path, report_dir=report_dir)
    assert run_id == result.run_id
    assert db_path.exists()

    stored = get_run(db_path, run_id)
    assert stored is not None
    assert stored["contract_hash"] == result.contract_hash
    assert stored["data_hash"] == result.data_hash
    assert stored["rows_total"] == 18
    assert stored["violation_rows"] == 14
    assert stored["summary"]["error_findings"] == 15
    assert len(stored["contract"]["fields"]) == 7
    assert stored["report_dir"].endswith("reports")

    findings, total = get_findings(db_path, run_id, limit=5, offset=0)
    assert total == 17
    assert len(findings) == 5
    assert findings[0]["code"] == "E_UNIT_NOT_ALLOWED"
    assert findings[0]["category"] == "contract"
    assert findings[0]["suggestion"]
    later, _ = get_findings(db_path, run_id, limit=50, offset=2)
    assert later[0]["row"] == 3


def test_save_is_idempotent(result, tmp_path):
    db_path = tmp_path / "ledger.db"
    save_run(result, db_path)
    save_run(result, db_path)
    assert count_runs(db_path) == 1
    findings, total = get_findings(db_path, result.run_id, limit=100)
    assert total == len(findings) == 17
    assert list_runs(db_path)[0]["run_id"] == result.run_id


def test_schema_and_delete(result, tmp_path):
    db_path = tmp_path / "ledger.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"runs", "findings"} <= tables
    save_run(result, db_path)
    assert delete_run(db_path, result.run_id) is True
    assert get_run(db_path, result.run_id) is None
    assert get_findings(db_path, result.run_id)[1] == 0
    assert count_runs(db_path) == 0


def test_missing_run(result, tmp_path):
    db_path = tmp_path / "ledger.db"
    init_db(db_path)
    assert get_run(db_path, "not-a-run") is None
    assert list_runs(db_path) == []
    assert delete_run(db_path, "not-a-run") is False


def test_fingerprint_is_stored_next_to_raw_hash(result, tmp_path):
    db_path = tmp_path / "ledger.db"
    run_id = save_run(result, db_path)
    stored = get_run(db_path, run_id)
    assert stored["contract_hash"] == result.contract_hash
    assert stored["contract_fingerprint"] == result.contract_fingerprint
    assert stored["contract_fingerprint"] != stored["contract_hash"]
    listed = list_runs(db_path)[0]
    assert listed["contract_fingerprint"] == result.contract_fingerprint


def test_old_ledger_without_fingerprint_column_is_migrated(result, tmp_path):
    """A ledger created before the column existed must open and keep old rows."""
    db_path = tmp_path / "old.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY, created_at TEXT, contract_name TEXT NOT NULL,
            contract_hash TEXT NOT NULL, data_hash TEXT NOT NULL,
            source_name TEXT NOT NULL, data_format TEXT NOT NULL,
            rows_total INTEGER NOT NULL, passed_rows INTEGER NOT NULL,
            violation_rows INTEGER NOT NULL, warning_rows INTEGER NOT NULL,
            error_findings INTEGER NOT NULL, warning_findings INTEGER NOT NULL,
            summary_json TEXT NOT NULL, contract_json TEXT NOT NULL,
            report_dir TEXT, schema_version TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-run", None, "legacy", "legacy-hash", "legacy-data",
            "old.csv", "csv", 1, 1, 0, 0, 0, 0, "{}", "{}", None, "1",
        ),
    )
    connection.commit()
    connection.close()

    init_db(db_path)  # must add the column without error
    legacy = get_run(db_path, "legacy-run")
    assert legacy is not None
    assert legacy["contract_hash"] == "legacy-hash"
    assert legacy["contract_fingerprint"] is None

    save_run(result, db_path)  # new runs populate the new column
    stored = get_run(db_path, result.run_id)
    assert stored["contract_fingerprint"] == result.contract_fingerprint
