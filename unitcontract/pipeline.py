"""Single deterministic pipeline shared by CLI, demo, reports and tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from unitcontract.contract.loader import contract_sha256, load_contract
from unitcontract.contract.schema import Contract
from unitcontract.engine.auditor import AuditReport, audit_validation
from unitcontract.engine.converter import ConversionResult, convert_records
from unitcontract.engine.reader import DataRow, detect_format, read_rows
from unitcontract.engine.validator import ValidationResult, validate_records
from unitcontract.units.registry import UnitRegistry


@dataclass
class RunResult:
    run_id: str
    contract: Contract
    contract_path: str
    data_path: str
    contract_hash: str
    data_hash: str
    source_name: str
    data_format: str
    no_timestamp: bool
    generated_at: str | None
    rows: list[DataRow]
    validation: ValidationResult
    conversion: ConversionResult
    audit: AuditReport

    def run_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "contract_name": self.contract.name,
            "contract_hash": self.contract_hash,
            "data_hash": self.data_hash,
            "source_name": self.source_name,
            "data_format": self.data_format,
            "no_timestamp": self.no_timestamp,
            "rows_total": self.validation.rows_total,
            "passed_rows": self.validation.passed_rows,
            "violation_rows": self.validation.violating_rows,
            "warning_rows": self.validation.warning_rows,
        }
        if self.generated_at is not None:
            payload["generated_at"] = self.generated_at
        return payload


def _run_id(contract_hash: str, data_hash: str, contract: Contract, source_name: str) -> str:
    material = json.dumps(
        {
            "contract_hash": contract_hash,
            "data_hash": data_hash,
            "contract_name": contract.name,
            "schema_version": contract.schema_version,
            "source_name": source_name,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


def execute(
    contract_path: str | Path,
    data_path: str | Path,
    data_format: str = "auto",
    no_timestamp: bool = False,
    registry: UnitRegistry | None = None,
    limit: int | None = None,
) -> RunResult:
    """Run the full read → validate → convert → audit pipeline."""
    registry = registry or UnitRegistry.bundled()
    contract_file = Path(contract_path)
    data_file = Path(data_path)
    contract = load_contract(contract_file, registry=registry)
    fmt = detect_format(data_file) if data_format == "auto" else data_format.lower()
    rows = read_rows(data_file, data_format=fmt, limit=limit)
    contract_hash = contract_sha256(contract_file)
    data_hash = hashlib.sha256(data_file.read_bytes()).hexdigest()
    source_name = data_file.name

    validation = validate_records(contract, rows, registry=registry)
    conversion = convert_records(contract, rows, registry=registry)
    audit = audit_validation(validation)
    generated_at = None
    if not no_timestamp:
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return RunResult(
        run_id=_run_id(contract_hash, data_hash, contract, source_name),
        contract=contract,
        contract_path=str(contract_file.resolve()),
        data_path=str(data_file.resolve()),
        contract_hash=contract_hash,
        data_hash=data_hash,
        source_name=source_name,
        data_format=fmt,
        no_timestamp=no_timestamp,
        generated_at=generated_at,
        rows=rows,
        validation=validation,
        conversion=conversion,
        audit=audit,
    )
