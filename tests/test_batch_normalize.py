from __future__ import annotations

import json
from pathlib import Path

import pytest

from unitcontract.batch import (
    SitePair,
    parse_target_overrides,
    resolve_target_units,
    run_batch,
)
from unitcontract.cli import main
from unitcontract.contract.loader import load_contract
from unitcontract.engine.converter import convert_records
from unitcontract.engine.reader import read_rows

ALPHA_CONTRACT = """\
schema_version: "1"
name: site-alpha-contract
description: alpha site with defaults block
defaults:
  precision: 2
  rounding: half_even
fields:
  - field: energy
    dimension: energy
    canonical_unit: J
    allowed_units: [J, kJ, kWh]
    range: {min: 0, max: 5000000000}
  - field: temperature
    dimension: temperature
    canonical_unit: C
    allowed_units: [C, K, F]
    precision: 1
    rounding: half_even
"""

# No defaults block, different declaration order, different canonical units,
# different precision/rounding, and a length field the data file lacks.
BETA_CONTRACT = """\
schema_version: "1"
name: site-beta-contract
fields:
  - field: temperature
    dimension: temperature
    canonical_unit: K
    allowed_units: [C, K, F]
    precision: 2
    rounding: half_up
  - field: energy
    dimension: energy
    canonical_unit: kWh
    allowed_units: [J, kWh, MWh]
    precision: 4
    rounding: half_up
  - field: length
    dimension: length
    canonical_unit: m
    allowed_units: [m, cm]
    precision: 1
    rounding: truncate
"""

ALPHA_CSV = """\
record_id,energy,energy__unit,temperature,temperature__unit
a1,12.5,kWh,32,F
a2,1000,J,300,K
a3,2.5,kWh,,
"""

BETA_JSONL = """\
{"record_id": "b1", "energy": {"value": "2.5", "unit": "kWh"}, "temperature": {"value": "300", "unit": "K"}}
{"record_id": "b2", "energy": {"value": "1.5", "unit": "MWh"}, "temperature": {"value": "26.85", "unit": "C"}}
"""


@pytest.fixture()
def site_files(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "alpha_contract": tmp_path / "alpha.yaml",
        "alpha_data": tmp_path / "alpha.csv",
        "beta_contract": tmp_path / "beta.yaml",
        "beta_data": tmp_path / "beta.jsonl",
    }
    paths["alpha_contract"].write_text(ALPHA_CONTRACT, encoding="utf-8")
    paths["alpha_data"].write_text(ALPHA_CSV, encoding="utf-8")
    paths["beta_contract"].write_text(BETA_CONTRACT, encoding="utf-8")
    paths["beta_data"].write_text(BETA_JSONL, encoding="utf-8")
    return paths


def _pairs(site_files: dict[str, Path], reverse: bool = False) -> list[SitePair]:
    pairs = [
        SitePair("site-alpha", site_files["alpha_contract"], site_files["alpha_data"]),
        SitePair("site-beta", site_files["beta_contract"], site_files["beta_data"]),
    ]
    return list(reversed(pairs)) if reverse else pairs


def _run(site_files: dict[str, Path], reverse: bool = False):
    return run_batch(
        _pairs(site_files, reverse=reverse),
        parse_target_overrides(["energy=kWh", "temperature=K"]),
    )


def test_batch_per_site_stats_and_missing_counts(site_files):
    result = _run(site_files)
    assert [site.site for site in result.sites] == ["site-alpha", "site-beta"]

    alpha, beta = result.sites
    assert alpha.rows_total == 3
    assert len(alpha.records) == 5
    assert alpha.skipped == 1
    assert alpha.total == 6
    assert alpha.code_counts == {"E_MISSING": 1}

    # beta declares a length field that its data file does not carry: every
    # row is judged E_MISSING for that field, skipped and counted, without
    # failing the batch.
    assert beta.rows_total == 2
    assert len(beta.records) == 4
    assert beta.skipped == 2
    assert beta.total == 6
    assert beta.code_counts == {"E_MISSING": 2}

    assert len(result.records) == 9
    assert result.skipped == 3
    assert result.total == 12
    assert result.target_units == {"energy": "kWh", "length": "m", "temperature": "K"}


def test_batch_records_carry_site_and_global_values(site_files):
    result = _run(site_files)
    records = result.records

    first = records[0]
    assert first.site == "site-alpha"
    assert first.field == "energy"
    assert first.raw_value == "12.5"
    assert first.source_unit == "kWh"
    assert first.canonical_unit == "J"
    assert first.canonical_value == "45000000.00"
    assert first.target_unit == "kWh"
    assert first.target_value == "12.50"
    assert first.precision == 2
    assert first.rounding == "half_even"

    by_key = {(r.site, r.row_number, r.field): r for r in records}
    # Affine temperature path: 32 F -> 0 C (site canonical) -> 273.15 K (global).
    temp = by_key[("site-alpha", 1, "temperature")]
    assert temp.canonical_unit == "C"
    assert temp.canonical_value == "0.0"
    assert temp.target_unit == "K"
    assert temp.target_value == "273.2"
    # Exact compose keeps 300 K exact even though the site rendering is 26.8 C.
    temp2 = by_key[("site-alpha", 2, "temperature")]
    assert temp2.canonical_value == "26.8"
    assert temp2.target_value == "300.0"

    # beta keeps its own canonical unit and its own precision/rounding.
    beta_energy = by_key[("site-beta", 1, "energy")]
    assert beta_energy.canonical_unit == "kWh"
    assert beta_energy.canonical_value == "2.5000"
    assert beta_energy.target_value == "2.5000"
    assert beta_energy.precision == 4
    assert beta_energy.rounding == "half_up"
    beta_energy2 = by_key[("site-beta", 2, "energy")]
    assert beta_energy2.canonical_value == "1500.0000"
    beta_temp = by_key[("site-beta", 2, "temperature")]
    assert beta_temp.canonical_unit == "K"
    assert beta_temp.canonical_value == "300.00"
    assert beta_temp.target_value == "300.00"


def test_batch_canonical_values_match_standalone_convert(site_files):
    result = _run(site_files)
    batch_canonical = {
        (r.site, r.row_number, r.field): r.canonical_value for r in result.records
    }
    for site, contract_key, data_key in (
        ("site-alpha", "alpha_contract", "alpha_data"),
        ("site-beta", "beta_contract", "beta_data"),
    ):
        contract = load_contract(site_files[contract_key])
        rows = read_rows(site_files[data_key])
        standalone = convert_records(contract, rows)
        site_records = [r for r in result.records if r.site == site]
        assert len(site_records) == len(standalone.records)
        for record in standalone.records:
            key = (site, record.row_number, record.field)
            assert batch_canonical[key] == record.canonical_value


def test_batch_preserves_per_site_precision(site_files):
    result = _run(site_files)
    for record in result.records:
        if record.field != "energy":
            continue
        decimals = len(record.target_value.partition(".")[2])
        assert decimals == (2 if record.site == "site-alpha" else 4)


def test_batch_default_targets_are_registry_base_units(site_files):
    result = run_batch(_pairs(site_files))
    assert result.target_units == {"energy": "J", "length": "m", "temperature": "K"}
    beta_energy = next(
        r for r in result.records if r.site == "site-beta" and r.field == "energy"
    )
    assert beta_energy.target_unit == "J"
    assert beta_energy.target_value == "9000000.0000"


def test_batch_unordered_pairs_are_deterministic(site_files):
    forward = _run(site_files)
    reverse = _run(site_files, reverse=True)
    assert [r.as_dict() for r in forward.records] == [
        r.as_dict() for r in reverse.records
    ]


def test_parse_target_overrides_rejects_bad_input():
    assert parse_target_overrides(["energy=kWh"]) == {"energy": "kWh"}
    with pytest.raises(ValueError):
        parse_target_overrides(["energy"])
    with pytest.raises(ValueError):
        parse_target_overrides(["=kWh"])
    with pytest.raises(ValueError):
        parse_target_overrides(["energy=kWh", "energy=J"])


def test_resolve_target_units_validates_against_registry(registry):
    with pytest.raises(ValueError):
        resolve_target_units(["energy"], {"energy": "kg"}, registry)
    with pytest.raises(ValueError):
        resolve_target_units(["energy"], {"bogus": "J"}, registry)
    resolved = resolve_target_units(["energy", "temperature"], {"energy": "kWh"}, registry)
    assert resolved == {"energy": "kWh", "temperature": "K"}


def test_run_batch_rejects_duplicate_sites(site_files):
    pairs = [
        SitePair("site-alpha", site_files["alpha_contract"], site_files["alpha_data"]),
        SitePair("site-alpha", site_files["beta_contract"], site_files["beta_data"]),
    ]
    with pytest.raises(ValueError):
        run_batch(pairs)


def test_cli_batch_normalize_writes_jsonl_and_summary(site_files, tmp_path, capsys):
    out = tmp_path / "normalized.jsonl"
    code = main(
        [
            "batch-normalize",
            "--pair", "site-beta", str(site_files["beta_contract"]), str(site_files["beta_data"]),
            "--pair", "site-alpha", str(site_files["alpha_contract"]), str(site_files["alpha_data"]),
            "--target", "energy=kWh",
            "--target", "temperature=K",
            "--out", str(out),
        ]
    )
    assert code == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 9
    first = json.loads(lines[0])
    assert first["site"] == "site-alpha"
    assert first["canonical_value"] == "45000000.00"
    assert first["target_value"] == "12.50"
    assert json.loads(lines[-1])["site"] == "site-beta"

    captured = capsys.readouterr()
    assert "site=site-alpha rows=3 normalized=5 skipped=1 total=6" in captured.err
    assert "site=site-beta rows=2 normalized=4 skipped=2 total=6" in captured.err
    assert "codes=E_MISSING:2" in captured.err
    assert "batch sites=2 records=9 skipped=3 total=12" in captured.err
    assert "targets=energy:kWh length:m temperature:K" in captured.err


def test_cli_batch_normalize_stdout_mode(site_files, capsys):
    code = main(
        [
            "batch-normalize",
            "--pair", "site-alpha", str(site_files["alpha_contract"]), str(site_files["alpha_data"]),
            "--target", "energy=kWh",
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 5
    assert all(json.loads(line)["site"] == "site-alpha" for line in lines)
    assert "site=site-alpha" in captured.err


def test_cli_batch_normalize_invalid_target_exits_2(site_files, capsys):
    base = [
        "batch-normalize",
        "--pair", "site-alpha", str(site_files["alpha_contract"]), str(site_files["alpha_data"]),
    ]
    assert main([*base, "--target", "energy=kg"]) == 2
    assert main([*base, "--target", "bogus=J"]) == 2
    assert main([*base, "--target", "energy"]) == 2
    err = capsys.readouterr().err
    assert "not registered for dimension energy" in err
    assert "not registered" in err


def test_cli_batch_normalize_missing_contract_exits_2(site_files, tmp_path):
    code = main(
        [
            "batch-normalize",
            "--pair", "site-alpha", str(tmp_path / "nope.yaml"), str(site_files["alpha_data"]),
        ]
    )
    assert code == 2
