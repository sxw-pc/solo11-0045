"""Cross-site batch normalization to global target units.

Each site's data is first validated and converted against its own contract,
exactly like the single-contract pipeline. Every converted record is then
converted on to the global target unit for its dimension. Exact ``Fraction``
arithmetic is carried through both hops and rounding happens only when
rendering, using each site field's own precision and rounding policy, so
per-site precision differences are preserved instead of flattened.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from unitcontract.contract.loader import load_contract
from unitcontract.contract.schema import Contract
from unitcontract.engine.converter import ConversionError, convert_value
from unitcontract.engine.reader import DataRow, read_rows
from unitcontract.engine.validator import validate_records
from unitcontract.units.registry import UnitRegistry
from unitcontract.units.rounding import format_fraction, parse_fraction


@dataclass(frozen=True)
class SitePair:
    """One site label bound to its own contract and data file."""

    site: str
    contract_path: str | Path
    data_path: str | Path


@dataclass(frozen=True)
class BatchRecord:
    """One normalized field value with site-local and global renderings."""

    site: str
    row_number: int
    field: str
    dimension: str
    raw_value: str
    source_unit: str
    canonical_unit: str
    canonical_value: str
    target_unit: str
    target_value: str
    precision: int
    rounding: str

    def as_dict(self) -> dict[str, object]:
        return {
            "site": self.site,
            "row": self.row_number,
            "field": self.field,
            "dimension": self.dimension,
            "raw_value": self.raw_value,
            "source_unit": self.source_unit,
            "canonical_unit": self.canonical_unit,
            "canonical_value": self.canonical_value,
            "target_unit": self.target_unit,
            "target_value": self.target_value,
            "precision": self.precision,
            "rounding": self.rounding,
        }


@dataclass(frozen=True)
class SiteBatchResult:
    site: str
    rows_total: int
    records: tuple[BatchRecord, ...]
    skipped: int
    code_counts: dict[str, int]

    @property
    def total(self) -> int:
        return len(self.records) + self.skipped


@dataclass(frozen=True)
class BatchResult:
    target_units: dict[str, str]
    sites: tuple[SiteBatchResult, ...]

    @property
    def records(self) -> tuple[BatchRecord, ...]:
        return tuple(record for site in self.sites for record in site.records)

    @property
    def skipped(self) -> int:
        return sum(site.skipped for site in self.sites)

    @property
    def total(self) -> int:
        return sum(site.total for site in self.sites)


def parse_target_overrides(raw_targets: Iterable[str]) -> dict[str, str]:
    """Parse ``DIMENSION=UNIT`` overrides; registry checks happen at resolve."""
    overrides: dict[str, str] = {}
    for item in raw_targets:
        dimension, sep, unit = item.partition("=")
        dimension = dimension.strip()
        unit = unit.strip()
        if not sep or not dimension or not unit:
            raise ValueError(f"invalid target {item!r}: expected DIMENSION=UNIT")
        if dimension in overrides:
            raise ValueError(f"duplicate target unit for dimension {dimension}")
        overrides[dimension] = unit
    return overrides


def resolve_target_units(
    dimensions: Iterable[str],
    overrides: dict[str, str],
    registry: UnitRegistry,
) -> dict[str, str]:
    """Resolve and validate the global target unit for every dimension.

    Every dimension used by any site contract must end up with a unit that is
    registered for that dimension. Dimensions without an explicit override
    fall back to the registry base unit. Any illegal target raises ValueError
    before data processing starts.
    """
    for dimension, unit in sorted(overrides.items()):
        if not registry.has_dimension(dimension):
            raise ValueError(f"target dimension {dimension} is not registered")
        if not registry.has_unit(dimension, unit):
            raise ValueError(
                f"target unit {unit} is not registered for dimension {dimension}"
            )
    resolved: dict[str, str] = {}
    for dimension in sorted(set(dimensions)):
        unit = overrides.get(dimension, registry.base_unit(dimension))
        registry.get_unit(dimension, unit)
        resolved[dimension] = unit
    return resolved


def _convert_site_records(
    site: str,
    contract: Contract,
    rows: Iterable[DataRow],
    target_units: dict[str, str],
    registry: UnitRegistry,
) -> tuple[tuple[BatchRecord, ...], int]:
    """Convert one site's rows, mirroring convert_records skip semantics.

    The local hop reuses convert_value so the site-canonical rendering is
    identical to the single-contract convert command. The exact Fraction is
    then carried into the global-target hop (including the affine temperature
    path inside registry.convert) and rendered with the same field precision
    and rounding, so no site-specific precision is lost.
    """
    records: list[BatchRecord] = []
    skipped = 0
    for row in rows:
        for field in contract.fields:
            cell = row.cell(field.field)
            if not cell.has_value or not cell.has_unit:
                skipped += 1
                continue
            try:
                value = parse_fraction(cell.value_text)
                canonical_exact, canonical_rendered = convert_value(
                    field, value, cell.unit_text, registry
                )
                target_unit = target_units[field.dimension]
                target_exact = registry.convert(
                    canonical_exact,
                    field.dimension,
                    field.canonical_unit,
                    target_unit,
                )
                target_rendered = format_fraction(
                    target_exact, field.precision, field.rounding
                )
            except (ValueError, ConversionError):
                skipped += 1
                continue
            records.append(
                BatchRecord(
                    site=site,
                    row_number=row.row_number,
                    field=field.field,
                    dimension=field.dimension,
                    raw_value=cell.value_text,
                    source_unit=cell.unit_text,
                    canonical_unit=field.canonical_unit,
                    canonical_value=canonical_rendered,
                    target_unit=target_unit,
                    target_value=target_rendered,
                    precision=field.precision,
                    rounding=field.rounding,
                )
            )
    return tuple(records), skipped


def run_batch(
    pairs: Iterable[SitePair],
    target_overrides: dict[str, str] | None = None,
    registry: UnitRegistry | None = None,
) -> BatchResult:
    """Validate and normalize every site pair to global target units.

    Contracts are all loaded and the target units fully validated before any
    data file is read, so an illegal target aborts the batch immediately.
    Sites are processed in sorted label order for deterministic output no
    matter what order the pairs arrive in.
    """
    registry = registry or UnitRegistry.bundled()
    overrides = dict(target_overrides or {})
    pair_list = list(pairs)
    if not pair_list:
        raise ValueError("at least one site pair is required")

    seen: set[str] = set()
    loaded: list[tuple[str, Contract, Path]] = []
    for pair in pair_list:
        site = pair.site.strip()
        if not site:
            raise ValueError("site label must be non-empty")
        if site in seen:
            raise ValueError(f"duplicate site label: {site}")
        seen.add(site)
        contract = load_contract(Path(pair.contract_path), registry=registry)
        loaded.append((site, contract, Path(pair.data_path)))

    dimensions = [
        field.dimension for _, contract, _ in loaded for field in contract.fields
    ]
    target_units = resolve_target_units(dimensions, overrides, registry)

    sites: list[SiteBatchResult] = []
    for site, contract, data_path in sorted(loaded, key=lambda item: item[0]):
        rows = read_rows(data_path)
        validation = validate_records(contract, rows, registry=registry)
        records, skipped = _convert_site_records(
            site, contract, rows, target_units, registry
        )
        sites.append(
            SiteBatchResult(
                site=site,
                rows_total=len(rows),
                records=records,
                skipped=skipped,
                code_counts=validation.counts_by_code(),
            )
        )
    return BatchResult(target_units=target_units, sites=tuple(sites))
