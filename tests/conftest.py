from __future__ import annotations

from pathlib import Path

import pytest

from unitcontract.contract.loader import load_contract
from unitcontract.engine.reader import read_rows
from unitcontract.pipeline import execute
from unitcontract.units.registry import UnitRegistry

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "unitcontract" / "fixtures"
CONTRACT_PATH = FIXTURES / "contracts" / "demo_energy.yaml"
DATA_PATH = FIXTURES / "data" / "demo_readings.csv"
EXPECTED = FIXTURES / "expected"


@pytest.fixture(scope="session")
def registry() -> UnitRegistry:
    return UnitRegistry.bundled()


@pytest.fixture(scope="session")
def contract(registry: UnitRegistry):
    return load_contract(CONTRACT_PATH, registry=registry)


@pytest.fixture(scope="session")
def rows():
    return read_rows(DATA_PATH)


@pytest.fixture(scope="session")
def result():
    return execute(CONTRACT_PATH, DATA_PATH, no_timestamp=True)
