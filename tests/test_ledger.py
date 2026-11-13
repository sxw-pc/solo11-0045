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
