"""Local SQLite ledger for runs and their findings."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from unitcontract.engine.auditor import catalog_for

SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT,
    contract_name TEXT NOT NULL,
    contract_hash TEXT NOT NULL,
    contract_fingerprint TEXT,
    data_hash TEXT NOT NULL,
    source_name TEXT NOT NULL,
    data_format TEXT NOT NULL,
    rows_total INTEGER NOT NULL,
    passed_rows INTEGER NOT NULL,
    violation_rows INTEGER NOT NULL,
    warning_rows INTEGER NOT NULL,
    error_findings INTEGER NOT NULL,
    warning_findings INTEGER NOT NULL,
    summary_json TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    report_dir TEXT,
    schema_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    row_num INTEGER NOT NULL,
    field TEXT NOT NULL,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    raw_value TEXT,
    unit TEXT,
    dimension TEXT NOT NULL,
    category TEXT NOT NULL,
    cause TEXT NOT NULL,
    suggestion TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id, row_num, id);
"""

#: Indexes over columns added after schema version "1".  They are created only
#: after the in-place migration below, so old ledgers without those columns
#: still open cleanly.
_POST_MIGRATION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_runs_contract_fingerprint "
    "ON runs(contract_fingerprint);",
)

#: Columns added after schema version "1" was first shipped.  Existing local
#: ledgers are migrated in place so old rows keep working; their
#: ``contract_fingerprint`` stays NULL until a run is repeated.
_ADDED_COLUMNS = {
    "runs": {"contract_fingerprint": "TEXT"},
}


def _migrate(connection: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        for name, decl in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(db_path: str | Path) -> None:
    with _connect(db_path) as connection:
        connection.executescript(_SCHEMA)
        _migrate(connection)
        for statement in _POST_MIGRATION_INDEXES:
            connection.execute(statement)


def save_run(result: Any, db_path: str | Path, report_dir: str | Path | None = None) -> str:
    """Insert or replace a run and its findings atomically."""
    init_db(db_path)
    summary = result.audit.as_dict()
    contract_json = result.contract.as_dict()
    created_at = getattr(result, "generated_at", None)
    report_value = str(Path(report_dir).resolve()) if report_dir is not None else None
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO runs (
                run_id, created_at, contract_name, contract_hash,
                contract_fingerprint, data_hash,
                source_name, data_format, rows_total, passed_rows,
                violation_rows, warning_rows, error_findings, warning_findings,
                summary_json, contract_json, report_dir, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.run_id,
                created_at,
                result.contract.name,
                result.contract_hash,
                getattr(result, "contract_fingerprint", None),
                result.data_hash,
                result.source_name,
                result.data_format,
                result.validation.rows_total,
                result.validation.passed_rows,
                result.validation.violating_rows,
                result.validation.warning_rows,
                len(result.validation.error_findings),
                len(result.validation.warning_findings),
                json.dumps(summary, ensure_ascii=False, sort_keys=True),
                json.dumps(contract_json, ensure_ascii=False, sort_keys=True),
                report_value,
                SCHEMA_VERSION,
            ),
        )
        connection.execute("DELETE FROM findings WHERE run_id = ?", (result.run_id,))
        for finding in result.validation.findings:
            entry = catalog_for(finding.code)
            connection.execute(
                """
                INSERT INTO findings (
                    run_id, row_num, field, code, severity, message,
                    raw_value, unit, dimension, category, cause, suggestion
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    finding.row_number,
                    finding.field,
                    finding.code,
                    finding.severity,
                    finding.message,
                    finding.raw_value,
                    finding.unit,
                    finding.dimension,
                    entry["category"],
                    entry["cause"],
                    entry["suggestion"],
                ),
            )
    return result.run_id


def _summary_row(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    return {
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "contract_name": row["contract_name"],
        "contract_hash": row["contract_hash"],
        "contract_fingerprint": (
            row["contract_fingerprint"] if "contract_fingerprint" in keys else None
        ),
        "data_hash": row["data_hash"],
        "source_name": row["source_name"],
        "data_format": row["data_format"],
        "rows_total": row["rows_total"],
        "passed_rows": row["passed_rows"],
        "violation_rows": row["violation_rows"],
        "warning_rows": row["warning_rows"],
        "error_findings": row["error_findings"],
        "warning_findings": row["warning_findings"],
    }


def list_runs(
    db_path: str | Path,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    init_db(db_path)
    limit = max(0, min(limit, 200))
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM runs
            ORDER BY COALESCE(created_at, '') DESC, run_id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, max(0, offset)),
        ).fetchall()
    return [_summary_row(row) for row in rows]


def count_runs(db_path: str | Path) -> int:
    init_db(db_path)
    with _connect(db_path) as connection:
        row = connection.execute("SELECT COUNT(*) AS n FROM runs").fetchone()
    return int(row["n"])


def get_run(db_path: str | Path, run_id: str) -> dict[str, Any] | None:
    init_db(db_path)
    with _connect(db_path) as connection:
        row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    payload = _summary_row(row)
    payload.update(
        {
            "summary": json.loads(row["summary_json"]),
            "contract": json.loads(row["contract_json"]),
            "report_dir": row["report_dir"],
            "schema_version": row["schema_version"],
        }
    )
    return payload


def find_runs_by_fingerprint(
    db_path: str | Path,
    contract_fingerprint: str,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List runs that share a semantic contract fingerprint.

    Byte-different but semantically equivalent contracts still have distinct
    ``run_id`` values (``run_id`` is derived from the raw hash for backward
    compatibility); this lookup is how historical runs are grouped.
    """
    init_db(db_path)
    limit = max(0, min(limit, 200))
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM runs
            WHERE contract_fingerprint = ?
            ORDER BY COALESCE(created_at, '') DESC, run_id DESC
            LIMIT ? OFFSET ?
            """,
            (contract_fingerprint, limit, max(0, offset)),
        ).fetchall()
    return [_summary_row(row) for row in rows]


def get_findings(
    db_path: str | Path,
    run_id: str,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    init_db(db_path)
    limit = max(0, min(limit, 500))
    with _connect(db_path) as connection:
        total_row = connection.execute(
            "SELECT COUNT(*) AS n FROM findings WHERE run_id = ?", (run_id,)
        ).fetchone()
        rows = connection.execute(
            """
            SELECT row_num, field, code, severity, message, raw_value, unit,
                   dimension, category, cause, suggestion
            FROM findings WHERE run_id = ?
            ORDER BY row_num ASC, id ASC
            LIMIT ? OFFSET ?
            """,
            (run_id, limit, max(0, offset)),
        ).fetchall()
    findings = [
        {
            "row": row["row_num"],
            "field": row["field"],
            "code": row["code"],
            "severity": row["severity"],
            "message": row["message"],
            "raw_value": row["raw_value"],
            "unit": row["unit"],
            "dimension": row["dimension"],
            "category": row["category"],
            "cause": row["cause"],
            "suggestion": row["suggestion"],
        }
        for row in rows
    ]
    return findings, int(total_row["n"])


def delete_run(db_path: str | Path, run_id: str) -> bool:
    init_db(db_path)
    with _connect(db_path) as connection:
        connection.execute("DELETE FROM findings WHERE run_id = ?", (run_id,))
        cursor = connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
    return cursor.rowcount > 0
