"""Deterministic synthetic demo data and end-to-end demo command."""

from __future__ import annotations

import csv
import logging
import random
from pathlib import Path
from typing import Any

from unitcontract.ledger.storage import save_run
from unitcontract.pipeline import execute
from unitcontract.report import write_reports

LOGGER = logging.getLogger("unitcontract.demo")
PACKAGE_ROOT = Path(__file__).resolve().parent
CONTRACT_FIXTURE = PACKAGE_ROOT / "fixtures" / "contracts" / "demo_energy.yaml"
HEADERS = [
    "record_id",
    "site",
    "energy",
    "energy__unit",
    "power",
    "power__unit",
    "mass",
    "mass__unit",
    "length",
    "length__unit",
    "temperature",
    "temperature__unit",
    "volume",
    "volume__unit",
    "count",
    "count__unit",
]


def _normal_row(rng: random.Random, index: int) -> dict[str, str]:
    energy_choice = rng.choice(
        [("kWh", rng.uniform(0.1, 900)), ("MWh", rng.uniform(0.01, 1.0)), ("GJ", rng.uniform(0.1, 4.0))]
    )
    power_choice = rng.choice(
        [("kW", rng.uniform(0.1, 900)), ("W", rng.uniform(50, 20000)), ("MW", rng.uniform(0.01, 1.0))]
    )
    mass_choice = rng.choice(
        [("kg", rng.uniform(0.1, 500)), ("g", rng.uniform(10, 50000)), ("t", rng.uniform(0.01, 2))]
    )
    length_choice = rng.choice(
        [("m", rng.uniform(0.1, 1000)), ("mm", rng.uniform(10, 500000)), ("km", rng.uniform(0.01, 5))]
    )
    temperature_choice = rng.choice(
        [("C", rng.uniform(-40, 55)), ("K", rng.uniform(235, 325)), ("F", rng.uniform(-40, 130))]
    )
    volume_choice = rng.choice(
        [("L", rng.uniform(0.1, 4000)), ("mL", rng.uniform(10, 500000)), ("m3", rng.uniform(0.01, 4))]
    )
    return {
        "record_id": f"demo-{index:04d}",
        "site": rng.choice(["site-alpha", "site-beta", "site-gamma", "site-delta"]),
        "energy": f"{energy_choice[1]:.2f}",
        "energy__unit": energy_choice[0],
        "power": f"{power_choice[1]:.2f}",
        "power__unit": power_choice[0],
        "mass": f"{mass_choice[1]:.3f}",
        "mass__unit": mass_choice[0],
        "length": f"{length_choice[1]:.2f}",
        "length__unit": length_choice[0],
        "temperature": f"{temperature_choice[1]:.1f}",
        "temperature__unit": temperature_choice[0],
        "volume": f"{volume_choice[1]:.2f}",
        "volume__unit": volume_choice[0],
        "count": str(rng.randint(0, 90000)),
        "count__unit": "count",
    }


def _scenario_rows() -> list[dict[str, str]]:
    """Rows chosen to exercise every error and warning path deterministically."""
    base = _normal_row(random.Random(7), 0)
    scenarios: list[dict[str, str]] = []

    def row(**overrides: str) -> dict[str, str]:
        value = dict(base)
        value.update(overrides)
        scenarios.append(value)
        return value

    row(record_id="scenario-001")
    row(record_id="scenario-002", mass="500", mass__unit="lb", length="12", length__unit="inch")
    row(record_id="scenario-003", energy="1000", energy__unit="GJ", power="1.00", power__unit="kW")
    row(record_id="scenario-004", energy="", energy__unit="kWh")
    row(record_id="scenario-005", energy="12", energy__unit="cal")
    row(record_id="scenario-006", energy="1.234", energy__unit="kWh")
    row(record_id="scenario-007", temperature="-5", temperature__unit="K")
    row(record_id="scenario-008", temperature="-300", temperature__unit="C")
    row(record_id="scenario-009", temperature="85.0", temperature__unit="C")
    row(record_id="scenario-010", length="3", length__unit="furlong")
    row(record_id="scenario-011", length="3.00", length__unit="")
    row(record_id="scenario-012", volume="3", volume__unit="kg")
    row(record_id="scenario-013", mass="n/a")
    row(record_id="scenario-014", count="200000")
    row(record_id="scenario-015", power="", power__unit="kW")
    row(record_id="scenario-016", temperature="70.0", temperature__unit="F")
    row(record_id="scenario-017", mass="500", mass__unit="lb")
    row(record_id="scenario-018", energy="1.2345", energy__unit="kWh")
    return scenarios


def generate_rows(total: int = 240, seed: int = 11) -> list[dict[str, str]]:
    if total < 18:
        raise ValueError("demo requires at least 18 rows")
    rng = random.Random(seed)
    rows = _scenario_rows()
    for index in range(len(rows), total):
        rows.append(_normal_row(rng, index + 1))
    return rows[:total]


def write_demo_inputs(out_dir: Path, total: int = 240, seed: int = 11) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    contract_path = out_dir / "contract.yaml"
    contract_path.write_bytes(CONTRACT_FIXTURE.read_bytes())
    data_path = out_dir / "data.csv"
    with data_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(generate_rows(total=total, seed=seed))
    return contract_path, data_path


def run_demo(
    out_dir: str | Path,
    total: int = 240,
    seed: int = 11,
    no_timestamp: bool = False,
    max_details: int = 50,
) -> dict[str, Any]:
    """Generate inputs, run the pipeline, write reports and persist the ledger."""
    directory = Path(out_dir)
    contract_path, data_path = write_demo_inputs(directory, total=total, seed=seed)
    LOGGER.info("demo inputs: %s rows, contract=%s", total, contract_path.name)
    result = execute(contract_path, data_path, no_timestamp=no_timestamp)
    run_dir = directory / "runs" / result.run_id
    run_reports = write_reports(result, run_dir, max_details=max_details)
    root_reports = write_reports(result, directory, max_details=max_details)
    ledger_path = directory / "ledger.db"
    save_run(result, ledger_path, report_dir=run_dir)
    LOGGER.info("demo run %s complete: %s", result.run_id, result.audit.as_dict())
    output = {
        "out_dir": str(directory.resolve()),
        "run_id": result.run_id,
        "contract_path": str(contract_path.resolve()),
        "data_path": str(data_path.resolve()),
        "ledger_path": str(ledger_path.resolve()),
        "run_report_dir": str(run_dir.resolve()),
        "reports": {name: str(path) for name, path in root_reports.items()},
        "run_reports": {name: str(path) for name, path in run_reports.items()},
        "summary": result.audit.as_dict(),
        "normalized_records": len(result.conversion.records),
        "conversion_skipped": result.conversion.skipped,
        "generated_at": result.generated_at,
    }
    return output


def format_demo_summary(output: dict[str, Any]) -> str:
    summary = output["summary"]
    return "\n".join(
        [
            f"Demo complete: {output['out_dir']}",
            f"run_id={output['run_id']}",
            f"rows={summary['rows_total']} passed={summary['passed_rows']} "
            f"violating={summary['violation_rows']}",
            f"errors={summary['error_findings']} warnings={summary['warning_findings']} "
            f"normalized={output['normalized_records']} skipped={output['conversion_skipped']}",
            f"ledger={output['ledger_path']}",
            f"reports={output['reports']['json']}, {output['reports']['md']}, "
            f"{output['reports']['html']}",
            "serve: python -m unitcontract serve --db "
            f'"{output["ledger_path"]}"',
        ]
    )
