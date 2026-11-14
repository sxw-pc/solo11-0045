"""Cross-site merging of normalized conversion records.

The merge command consumes an *unordered* set of ``(site, contract, data)``
triples. Each site is normalized with the existing converter (the same exact
``Fraction`` arithmetic and affine temperature path used by ``convert``), then
records are grouped by the business key ``(record_id, field)``.

Conflict semantics
------------------
A pair of observations for the same business key is a *true conflict* when the
exact converted quantities disagree. Quantities that use different canonical
units are compared by converting both to the dimension's base unit with the
existing registry (never a second conversion formula). Therefore:

* different canonical unit + unequal exact base quantity -> conflict;
* different canonical unit + equal exact base quantity      -> equivalent;
* same canonical unit + unequal exact quantity              -> conflict;
* same canonical unit + equal exact quantity but different
  rendered text (precision/rounding)                       -> precision drift.

Precision drift is counted once per business key and never appears in the
conflict list. A conflicting business key is removed from the merged output in
full and written once to the conflict list with every site observation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

from unitcontract.contract.loader import load_contract
from unitcontract.contract.schema import Contract, ContractValidationError
from unitcontract.engine.converter import ConversionRecord, convert_records
from unitcontract.engine.reader import read_rows
from unitcontract.units.registry import UnitRegistry, UnitRegistryError
from unitcontract.units.rounding import parse_fraction

DEFAULT_BUSINESS_KEY = "record_id"

# Empty ``allowed_units`` is the field-level degeneracy that is skipped; the
# schema may also report the dependent canonical-unit codes for the same field,
# which are recorded on the skipped site alongside ``E_ALLOWED_MISSING``.

# Existing schema code used to record a missing top-level ``defaults`` block.
MISSING_DEFAULTS_CODE = "E_SCHEMA_ROOT"

# Schema failures that an absent ``defaults`` block could plausibly explain.
# When those are the only failures, the site is degenerate and skipped.
_DEFAULTS_DEPENDENT_CODES = frozenset(
    {"E_DIM_UNKNOWN", "E_CANON_MISSING", "E_ALLOWED_MISSING", "E_FIELD_NAME"}
)


class MergeError(ValueError):
    """Raised when merge inputs cannot be interpreted as site pairs."""


@dataclass(frozen=True)
class SiteSpec:
    """One site's contract/data pair before it is loaded."""

    name: str
    contract_path: str | Path
    data_path: str | Path
    data_format: str = "auto"


@dataclass(frozen=True)
class SkippedSite:
    """A degenerate site that was skipped instead of failing the batch."""

    name: str
    contract_path: str
    data_path: str
    codes: tuple[str, ...]
    messages: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "site": self.name,
            "contract": self.contract_path,
            "data": self.data_path,
            "status": "skipped",
            "codes": list(self.codes),
            "messages": list(self.messages),
        }


@dataclass(frozen=True)
class SiteResult:
    """A successfully loaded and normalized site."""

    name: str
    contract_path: str
    data_path: str
    contract_name: str
    rows_total: int
    records: tuple[ConversionRecord, ...]
    conversion_skipped: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "site": self.name,
            "contract": self.contract_path,
            "data": self.data_path,
            "contract_name": self.contract_name,
            "status": "ok",
            "rows": self.rows_total,
            "records": len(self.records),
            "conversion_skipped": self.conversion_skipped,
        }


@dataclass(frozen=True)
class Observation:
    """One site's converted record for one business key."""

    site: str
    record: ConversionRecord

    def as_dict(self) -> dict[str, Any]:
        record = self.record
        return {
            "site": self.site,
            "row": record.row_number,
            "field": record.field,
            "dimension": record.dimension,
            "raw_value": record.raw_value,
            "source_unit": record.source_unit,
            "canonical_unit": record.canonical_unit,
            "canonical_value": record.canonical_value,
            "exact_value": _fraction_text(record.exact_value),
            "precision": record.precision,
            "rounding": record.rounding,
        }


@dataclass(frozen=True)
class ConflictPair:
    left_site: str
    right_site: str
    reason: str
    left_exact_base: str | None
    right_exact_base: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "left_site": self.left_site,
            "right_site": self.right_site,
            "reason": self.reason,
            "left_exact_base": self.left_exact_base,
            "right_exact_base": self.right_exact_base,
        }


@dataclass(frozen=True)
class Conflict:
    """One business key whose site observations cannot be merged safely."""

    record_id: str | None
    field: str
    dimension: str
    reason: str
    observations: tuple[Observation, ...]
    pairs: tuple[ConflictPair, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "field": self.field,
            "dimension": self.dimension,
            "reason": self.reason,
            "sites": [observation.as_dict() for observation in self.observations],
            "pairs": [pair.as_dict() for pair in self.pairs],
        }


@dataclass(frozen=True)
class MergedRecord:
    """A representative conversion record that survived the merge."""

    record_id: str | None
    field: str
    site: str
    record: ConversionRecord

    def as_dict(self) -> dict[str, Any]:
        # Exactly the single-site ``convert`` shape: every field is copied
        # verbatim and no merge metadata is injected, so the canonical values
        # are byte-for-byte reproducible.
        return self.record.as_dict()


@dataclass(frozen=True)
class DriftRecord:
    """A business key whose sites agree exactly but render different text."""

    record_id: str | None
    field: str
    dimension: str
    observations: tuple[Observation, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "field": self.field,
            "dimension": self.dimension,
            "exact_value": _fraction_text(self.observations[0].record.exact_value),
            "canonical_unit": self.observations[0].record.canonical_unit,
            "sites": [observation.as_dict() for observation in self.observations],
        }


@dataclass(frozen=True)
class MergeResult:
    sites: tuple[SiteResult, ...]
    skipped_sites: tuple[SkippedSite, ...]
    merged: tuple[MergedRecord, ...]
    conflicts: tuple[Conflict, ...]
    drift: tuple[DriftRecord, ...]
    business_keys: int
    records_without_key: int

    @property
    def conflicts_found(self) -> bool:
        return bool(self.conflicts)

    @property
    def precision_drift(self) -> int:
        return len(self.drift)

    @property
    def conversion_skipped(self) -> int:
        return sum(site.conversion_skipped for site in self.sites)

    @property
    def skipped_site_codes(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for site in self.skipped_sites:
            for code in site.codes:
                counts[code] += 1
        return dict(sorted(counts.items()))

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "sites_total": len(self.sites) + len(self.skipped_sites),
            "sites_ok": len(self.sites),
            "sites_skipped": len(self.skipped_sites),
            "skipped_site_codes": self.skipped_site_codes,
            "skipped_sites": [site.as_dict() for site in self.skipped_sites],
            "site_details": [site.as_dict() for site in self.sites],
            "records_total": sum(len(site.records) for site in self.sites),
            "records_normalized": len(self.merged),
            "conversion_skipped": self.conversion_skipped,
            "business_keys": self.business_keys,
            "records_without_key": self.records_without_key,
            "precision_drift": self.precision_drift,
            "conflicts": len(self.conflicts),
            "conflict_rows": len(self.conflicts),
        }


def _fraction_text(value: Fraction | None) -> str | None:
    if value is None:
        return None
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _defaults_absent(document: Any) -> bool:
    """A missing or null top-level ``defaults`` block is degenerate."""
    return isinstance(document, dict) and not isinstance(document.get("defaults"), dict)


def _read_contract_document(path: Path) -> Any:
    """Read the raw YAML only to detect an absent ``defaults`` block.

    Read errors and YAML syntax errors are left to :func:`load_contract`, which
    reports them with stable codes and correct locations.
    """
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _skippable_codes(codes: Sequence[str], defaults_absent: bool) -> bool:
    """True for the two documented degenerate site shapes.

    * empty ``allowed_units`` (the schema may also report its dependent
      canonical-unit code for the same field);
    * an absent ``defaults`` block whose only failures are the attributes a
      defaults block could have supplied.

    Every code is still recorded on the skipped site, and any other schema
    error (bad YAML, unknown dimension, invalid precision, ...) stays fatal.
    """
    code_set = set(codes)
    if "E_ALLOWED_MISSING" in code_set:
        return True
    if defaults_absent and code_set <= _DEFAULTS_DEPENDENT_CODES:
        return True
    return False


def _load_site(
    spec: SiteSpec,
    registry: UnitRegistry,
    business_key: str,
) -> SiteResult | SkippedSite:
    contract_path = Path(spec.contract_path)
    document = _read_contract_document(contract_path)
    defaults_absent = _defaults_absent(document)

    try:
        contract: Contract = load_contract(contract_path, registry=registry)
    except ContractValidationError as exc:
        codes = [issue.code for issue in exc.issues]
        if not _skippable_codes(codes, defaults_absent):
            raise
        skipped_codes = list(dict.fromkeys(codes))
        if defaults_absent:
            skipped_codes.append(MISSING_DEFAULTS_CODE)
        return SkippedSite(
            name=spec.name,
            contract_path=str(contract_path.resolve()),
            data_path=str(Path(spec.data_path).resolve()),
            codes=tuple(dict.fromkeys(skipped_codes)),
            messages=tuple(str(issue) for issue in exc.issues),
        )

    if defaults_absent:
        return SkippedSite(
            name=spec.name,
            contract_path=str(contract_path.resolve()),
            data_path=str(Path(spec.data_path).resolve()),
            codes=(MISSING_DEFAULTS_CODE,),
            messages=(
                f"{contract_path}: [E_SCHEMA_ROOT] top-level defaults is missing; "
                "site skipped by merge",
            ),
        )

    rows = read_rows(spec.data_path, data_format=spec.data_format)
    conversion = convert_records(contract, rows, registry=registry, business_key=business_key)
    return SiteResult(
        name=spec.name,
        contract_path=str(contract_path.resolve()),
        data_path=str(Path(spec.data_path).resolve()),
        contract_name=contract.name,
        rows_total=len(rows),
        records=conversion.records,
        conversion_skipped=conversion.skipped,
    )


def _exact_base_value(observation: Observation, registry: UnitRegistry) -> Fraction:
    """Exact quantity in the dimension base unit, via the shared registry.

    ``exact_value`` is already the exact result of the single-site conversion;
    this second call only changes the unit, so the affine temperature path and
    ratio factors are literally the ones used by ``convert``.
    """
    record = observation.record
    exact = record.exact_value
    if exact is None:  # defensive: manually constructed record
        exact = parse_fraction(record.canonical_value)
    return registry.convert(
        exact,
        record.dimension,
        record.canonical_unit,
        registry.base_unit(record.dimension),
    )


def _pair_verdict(
    left: Observation,
    right: Observation,
    registry: UnitRegistry,
) -> tuple[str | None, str | None, str | None]:
    """Return ``(reason, left_base, right_base)``; ``None`` means equivalent."""
    left_record = left.record
    right_record = right.record
    if left_record.dimension != right_record.dimension:
        return "dimension_mismatch", None, None

    try:
        left_base = _exact_base_value(left, registry)
        right_base = _exact_base_value(right, registry)
    except (UnitRegistryError, ValueError):
        return "conversion_mismatch", None, None

    if left_base != right_base:
        reason = (
            "value_mismatch"
            if left_record.canonical_unit == right_record.canonical_unit
            else "canonical_unit_conflict"
        )
        return reason, _fraction_text(left_base), _fraction_text(right_base)
    return None, _fraction_text(left_base), _fraction_text(right_base)


def _is_precision_drift(observations: Sequence[Observation]) -> bool:
    """Same canonical unit, equal exact quantity, different rendered text."""
    if len(observations) < 2:
        return False
    first = observations[0].record
    for observation in observations[1:]:
        record = observation.record
        if record.canonical_unit != first.canonical_unit:
            return False
        if record.exact_value != first.exact_value:
            return False
    return len({observation.record.canonical_value for observation in observations}) > 1


def merge_sites(
    specs: Iterable[SiteSpec],
    registry: UnitRegistry | None = None,
    business_key: str = DEFAULT_BUSINESS_KEY,
) -> MergeResult:
    """Merge an unordered set of site contract/data pairs deterministically.

    Sites are sorted by name before loading, so the result is independent of
    the order in which the pairs were supplied. Grouped business keys are
    emitted in sorted ``(record_id, field)`` order; observations within a key
    keep the converter's per-site order.
    """
    registry = registry or UnitRegistry.bundled()
    spec_list = list(specs)
    if not spec_list:
        raise MergeError("merge requires at least one site contract/data pair")

    names = [spec.name for spec in spec_list]
    if any(not name or not name.strip() for name in names):
        raise MergeError("every site must have a non-empty name")
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        raise MergeError(f"duplicate site name(s): {', '.join(duplicates)}")

    ordered = sorted(spec_list, key=lambda spec: spec.name)
    sites: list[SiteResult] = []
    skipped: list[SkippedSite] = []
    for spec in ordered:
        loaded = _load_site(spec, registry, business_key)
        if isinstance(loaded, SkippedSite):
            skipped.append(loaded)
        else:
            sites.append(loaded)

    records_without_key = 0
    by_key: dict[tuple[str, str], list[Observation]] = {}
    anonymous: list[Observation] = []
    for site in sites:
        for record in site.records:
            if record.record_id is None:
                records_without_key += 1
                anonymous.append(Observation(site=site.name, record=record))
                continue
            by_key.setdefault((record.record_id, record.field), []).append(
                Observation(site=site.name, record=record)
            )

    # Sites are already sorted by name and each site's records keep the
    # converter's row/field order, so iterating the insertion-ordered groups
    # makes the merged output independent of argv order. When a single site
    # supplies every key this reproduces that site's ``convert`` order exactly.
    rows: list[list[Observation]] = list(by_key.values())

    merged: list[MergedRecord] = []
    conflicts: list[Conflict] = []
    drift: list[DriftRecord] = []
    business_keys = 0

    for observations in rows:
        business_keys += 1
        pairs: list[ConflictPair] = []
        reasons: list[str] = []
        for index, left in enumerate(observations):
            for right in observations[index + 1 :]:
                reason, left_base, right_base = _pair_verdict(left, right, registry)
                if reason is None:
                    continue
                reasons.append(reason)
                pairs.append(
                    ConflictPair(
                        left_site=left.site,
                        right_site=right.site,
                        reason=reason,
                        left_exact_base=left_base,
                        right_exact_base=right_base,
                    )
                )

        first = observations[0].record
        if pairs:
            conflicts.append(
                Conflict(
                    record_id=first.record_id,
                    field=first.field,
                    dimension=first.dimension,
                    reason="+".join(sorted(set(reasons))),
                    observations=tuple(observations),
                    pairs=tuple(pairs),
                )
            )
            continue

        if _is_precision_drift(observations):
            drift.append(
                DriftRecord(
                    record_id=first.record_id,
                    field=first.field,
                    dimension=first.dimension,
                    observations=tuple(observations),
                )
            )
        representative = observations[0]
        merged.append(
            MergedRecord(
                record_id=representative.record.record_id,
                field=representative.record.field,
                site=representative.site,
                record=representative.record,
            )
        )

    # Rows without a record_id cannot be keyed against other sites; emit them
    # unchanged, one per source record, rather than merging unrelated data.
    for observation in anonymous:
        merged.append(
            MergedRecord(
                record_id=None,
                field=observation.record.field,
                site=observation.site,
                record=observation.record,
            )
        )

    return MergeResult(
        sites=tuple(sites),
        skipped_sites=tuple(skipped),
        merged=tuple(merged),
        conflicts=tuple(conflicts),
        drift=tuple(drift),
        business_keys=business_keys,
        records_without_key=records_without_key,
    )
