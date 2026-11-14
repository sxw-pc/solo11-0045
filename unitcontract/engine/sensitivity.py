"""Sensitivity trials for precision, rounding and range contract changes.

A trial document follows the overlay YAML shape but may only touch the three
soft contract keys (``precision``, ``rounding`` and ``range``). Each trial is
merged onto an independently parsed copy of the baseline payload and rebuilt
through the regular :func:`build_contract` path, so value-level validation
(error codes such as ``E_PRECISION_INVALID``) stays identical to
``validate``. The parsed rows, registry and baseline results are read once
and reused; every trial still produces its own immutable contract and fresh
validation/conversion results, so trials never share mutable state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from unitcontract.contract.loader import merge_contract_documents, read_yaml_document
from unitcontract.contract.schema import (
    Contract,
    ContractField,
    ContractIssue,
    ContractValidationError,
    build_contract,
)
from unitcontract.engine.converter import convert_records
from unitcontract.engine.reader import DataRow, detect_format, read_rows
from unitcontract.engine.validator import validate_records
from unitcontract.report.json_report import dump_json
from unitcontract.units.registry import UnitRegistry

SENSITIVITY_SCHEMA_VERSION = "1"

# The trial document mirrors an overlay document, but these are the only
# locations/keys it is allowed to touch.
ALLOWED_TOP_LEVEL_KEYS = frozenset({"fields"})
ALLOWED_FIELD_KEYS = frozenset({"field", "precision", "rounding", "range"})
TRIALABLE_KEYS = ("precision", "rounding", "range")

BUCKET_PASS_TO_VIOLATION = "pass_to_violation"
BUCKET_VIOLATION_TO_PASS = "violation_to_pass"
BUCKET_ROUNDING_TEXT_ONLY = "rounding_text_only"
BUCKET_UNCHANGED = "unchanged"


def _issue(code: str, message: str, location: str, field: str | None = None) -> ContractIssue:
    return ContractIssue(code, message, location, field)


def validate_trial_structure(
    payload: Any,
    baseline: Contract,
    source_name: str,
) -> None:
    """Reject anything outside the precision/rounding/range trial surface."""
    issues: list[ContractIssue] = []
    if not isinstance(payload, dict):
        raise ContractValidationError(
            [_issue("E_SCHEMA_ROOT", "trial document root must be a mapping", source_name)]
        )

    for key in payload:
        if key not in ALLOWED_TOP_LEVEL_KEYS:
            issues.append(
                _issue(
                    "E_SCHEMA_ROOT",
                    (
                        "trial documents may only override precision, rounding and range; "
                        f"top-level key {key!r} is not permitted"
                    ),
                    f"{source_name}#{key}",
                )
            )

    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        issues.append(
            _issue(
                "E_FIELDS_MISSING",
                "trial fields must be a non-empty list",
                f"{source_name}#fields",
            )
        )
        raise ContractValidationError(issues)

    baseline_names = set(baseline.field_names())
    seen: set[str] = set()
    for index, item in enumerate(raw_fields):
        location = f"{source_name}#fields[{index}]"
        if not isinstance(item, dict):
            issues.append(
                _issue("E_SCHEMA_ROOT", "each trial field declaration must be a mapping", location)
            )
            continue
        for key in item:
            if key not in ALLOWED_FIELD_KEYS:
                issues.append(
                    _issue(
                        "E_SCHEMA_ROOT",
                        (
                            "trial fields may only change precision, rounding or range; "
                            f"key {key!r} is not permitted"
                        ),
                        f"{location}#{key}",
                        item.get("field") if isinstance(item.get("field"), str) else None,
                    )
                )
        name = item.get("field")
        if not isinstance(name, str) or not name.strip():
            issues.append(
                _issue("E_FIELD_NAME", "trial field must be a non-empty string", location)
            )
            continue
        name = name.strip()
        if name in seen:
            issues.append(
                _issue("E_FIELD_DUPLICATE", f"duplicate trial field: {name}", location, name)
            )
            continue
        seen.add(name)
        if name not in baseline_names:
            issues.append(
                _issue(
                    "E_SCHEMA_ROOT",
                    (
                        f"trial field {name!r} is not declared in the baseline contract; "
                        "trials may only adjust existing fields"
                    ),
                    location,
                    name,
                )
            )

    if issues:
        raise ContractValidationError(issues)


def load_trial_contract(
    trial_path: str | Path,
    baseline_payload: dict[str, Any],
    baseline: Contract,
    registry: UnitRegistry,
) -> Contract:
    """Validate the restricted trial shape and rebuild via the normal builder."""
    path = Path(trial_path)
    source_name = str(path)
    payload = read_yaml_document(path)
    validate_trial_structure(payload, baseline, source_name)
    merged = merge_contract_documents(baseline_payload, payload)
    # The builder supplies the regular value-level checks and error codes
    # (E_PRECISION_INVALID, E_ROUNDING_UNKNOWN, E_RANGE_INVALID, ...).
    return build_contract(merged, source_name, registry=registry)


def _field_settings(field: ContractField) -> dict[str, Any]:
    """The three trialable keys in the same shape as ``ContractField.as_dict``."""
    return {
        "precision": field.precision,
        "rounding": field.rounding,
        "range": field.as_dict()["range"],
    }


def _bucket(
    rows: list[int],
    total_rows: int,
    max_rows: int,
    codes: dict[str, int] | None = None,
) -> dict[str, Any]:
    ordered = sorted(rows)
    payload: dict[str, Any] = {
        "count": len(ordered),
        "rows_total": total_rows,
        "rows": ordered[:max_rows],
        "truncated": len(ordered) > max_rows,
    }
    if codes is not None:
        payload["codes"] = codes
    return payload


def _rendered_text_index(records: Any) -> dict[tuple[int, str], str]:
    index: dict[tuple[int, str], str] = {}
    for record in records:
        index[(record.row_number, record.field)] = record.canonical_value
    return index


@dataclass(frozen=True)
class FieldImpact:
    field: str
    changed_keys: tuple[str, ...]
    before: dict[str, Any]
    after: dict[str, Any]
    pass_to_violation: dict[str, Any]
    violation_to_pass: dict[str, Any]
    rounding_text_only: dict[str, Any]
    unchanged: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "changed_keys": list(self.changed_keys),
            "before": self.before,
            "after": self.after,
            BUCKET_PASS_TO_VIOLATION: self.pass_to_violation,
            BUCKET_VIOLATION_TO_PASS: self.violation_to_pass,
            BUCKET_ROUNDING_TEXT_ONLY: self.rounding_text_only,
            BUCKET_UNCHANGED: self.unchanged,
        }


@dataclass(frozen=True)
class TrialResult:
    index: int
    trial_path: str
    summary_path: str | None
    touched_fields: tuple[str, ...]
    impacts: tuple[FieldImpact, ...]
    rows_total: int
    totals: dict[str, Any]
    baseline_violation_rows: int
    trial_violation_rows: int
    baseline_code_counts: dict[str, int]
    trial_code_counts: dict[str, int]

    @property
    def affected_rows(self) -> int:
        return self.totals["affected_rows"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SENSITIVITY_SCHEMA_VERSION,
            "trial_index": self.index,
            "trial_path": self.trial_path,
            "baseline_contract": self.totals["baseline_contract"],
            "data_path": self.totals["data_path"],
            "rows_total": self.rows_total,
            "touched_fields": list(self.touched_fields),
            "totals": self.totals,
            "baseline": {
                "violation_rows": self.baseline_violation_rows,
                "code_counts": self.baseline_code_counts,
            },
            "trial": {
                "violation_rows": self.trial_violation_rows,
                "code_counts": self.trial_code_counts,
            },
            "fields": [impact.as_dict() for impact in self.impacts],
        }


class SensitivityRunner:
    """Run independent trials against a single read-only baseline snapshot."""

    def __init__(
        self,
        contract_path: str | Path,
        data_path: str | Path,
        data_format: str = "auto",
        registry: UnitRegistry | None = None,
    ) -> None:
        self.registry = registry or UnitRegistry.bundled()
        self.contract_path = Path(contract_path)
        self.data_path = Path(data_path)
        # Read and parse each input exactly once; rows are never mutated.
        self.baseline_payload = read_yaml_document(self.contract_path)
        self.baseline = build_contract(
            self.baseline_payload, str(self.contract_path), registry=self.registry
        )
        fmt = detect_format(self.data_path) if data_format == "auto" else data_format.lower()
        self.data_format = fmt
        self.rows: list[DataRow] = read_rows(self.data_path, data_format=fmt)
        self._baseline_validation = validate_records(self.baseline, self.rows, self.registry)
        self._baseline_conversion = convert_records(self.baseline, self.rows, self.registry)
        self._baseline_error_rows = self._error_rows_by_field(self._baseline_validation)
        self._baseline_rendered = _rendered_text_index(self._baseline_conversion.records)
        self._row_numbers = [row.row_number for row in self.rows]

    @staticmethod
    def _error_rows_by_field(validation: Any) -> dict[str, dict[int, list[Any]]]:
        errors: dict[str, dict[int, list[Any]]] = {}
        for finding in validation.error_findings:
            errors.setdefault(finding.field, {}).setdefault(finding.row_number, []).append(finding)
        return errors

    def run_trial(self, trial_path: str | Path, index: int, max_rows: int = 100) -> TrialResult:
        """Build one fresh trial contract and compute row-level field impacts."""
        trial_path = Path(trial_path)
        trial_contract = load_trial_contract(
            trial_path, self.baseline_payload, self.baseline, self.registry
        )
        # Independent validation/conversion outputs per trial; no state from
        # any previous trial is reused beyond the immutable baseline snapshot.
        validation = validate_records(trial_contract, self.rows, self.registry)
        conversion = convert_records(trial_contract, self.rows, self.registry)
        trial_error_rows = self._error_rows_by_field(validation)
        trial_rendered = _rendered_text_index(conversion.records)

        base_fields = self.baseline.field_map
        trial_fields = trial_contract.field_map
        row_numbers = self._row_numbers
        total = len(row_numbers)

        impacts: list[FieldImpact] = []
        union: dict[str, set[int]] = {
            BUCKET_PASS_TO_VIOLATION: set(),
            BUCKET_VIOLATION_TO_PASS: set(),
            BUCKET_ROUNDING_TEXT_ONLY: set(),
        }

        for field_name in self.baseline.field_names():
            base_field = base_fields[field_name]
            trial_field = trial_fields[field_name]
            changed_keys = tuple(
                key
                for key in TRIALABLE_KEYS
                if _field_settings(base_field)[key] != _field_settings(trial_field)[key]
            )
            if not changed_keys:
                continue

            base_errors = self._baseline_error_rows.get(field_name, {})
            new_errors = trial_error_rows.get(field_name, {})
            pass_to_violation: list[int] = []
            violation_to_pass: list[int] = []
            rounding_text_only: list[int] = []
            unchanged: list[int] = []
            p2v_codes: dict[str, int] = {}
            v2p_codes: dict[str, int] = {}

            for row_number in row_numbers:
                was_error = row_number in base_errors
                is_error = row_number in new_errors
                if not was_error and is_error:
                    pass_to_violation.append(row_number)
                    for finding in new_errors[row_number]:
                        p2v_codes[finding.code] = p2v_codes.get(finding.code, 0) + 1
                elif was_error and not is_error:
                    violation_to_pass.append(row_number)
                    for finding in base_errors[row_number]:
                        v2p_codes[finding.code] = v2p_codes.get(finding.code, 0) + 1
                elif not was_error and not is_error:
                    before_text = self._baseline_rendered.get((row_number, field_name))
                    after_text = trial_rendered.get((row_number, field_name))
                    # Rows that stay compliant but whose rendered canonical
                    # value changed textually are tracked separately; they are
                    # never counted as pass-to-violation.
                    if (
                        before_text is not None
                        and after_text is not None
                        and before_text != after_text
                    ):
                        rounding_text_only.append(row_number)
                    else:
                        unchanged.append(row_number)
                else:
                    unchanged.append(row_number)

            for bucket_name, rows_in_bucket in (
                (BUCKET_PASS_TO_VIOLATION, pass_to_violation),
                (BUCKET_VIOLATION_TO_PASS, violation_to_pass),
                (BUCKET_ROUNDING_TEXT_ONLY, rounding_text_only),
            ):
                union[bucket_name].update(rows_in_bucket)

            impacts.append(
                FieldImpact(
                    field=field_name,
                    changed_keys=changed_keys,
                    before=_field_settings(base_field),
                    after=_field_settings(trial_field),
                    pass_to_violation=_bucket(
                        pass_to_violation, total, max_rows, dict(sorted(p2v_codes.items()))
                    ),
                    violation_to_pass=_bucket(
                        violation_to_pass, total, max_rows, dict(sorted(v2p_codes.items()))
                    ),
                    rounding_text_only=_bucket(rounding_text_only, total, max_rows),
                    unchanged=_bucket(unchanged, total, max_rows),
                )
            )

        affected_union = (
            union[BUCKET_PASS_TO_VIOLATION]
            | union[BUCKET_VIOLATION_TO_PASS]
            | union[BUCKET_ROUNDING_TEXT_ONLY]
        )
        totals = {
            "baseline_contract": str(self.contract_path),
            "data_path": str(self.data_path),
            "rows_total": total,
            "pass_to_violation_rows": len(union[BUCKET_PASS_TO_VIOLATION]),
            "violation_to_pass_rows": len(union[BUCKET_VIOLATION_TO_PASS]),
            "rounding_text_only_rows": len(union[BUCKET_ROUNDING_TEXT_ONLY]),
            "unchanged_rows": total - len(affected_union),
            "affected_rows": len(affected_union),
        }
        return TrialResult(
            index=index,
            trial_path=str(trial_path),
            summary_path=None,
            touched_fields=tuple(impact.field for impact in impacts),
            impacts=tuple(impacts),
            rows_total=total,
            totals=totals,
            baseline_violation_rows=self._baseline_validation.violating_rows,
            trial_violation_rows=validation.violating_rows,
            baseline_code_counts=self._baseline_validation.counts_by_code(),
            trial_code_counts=validation.counts_by_code(),
        )


def run_sensitivity(
    contract_path: str | Path,
    data_path: str | Path,
    trial_paths: list[str | Path],
    out_dir: str | Path,
    data_format: str = "auto",
    max_rows: int = 100,
) -> tuple[list[TrialResult], Path]:
    """Run every trial independently on one baseline snapshot and persist JSON.

    Returns the ordered trial results plus the aggregate summary path. Each
    trial writes its own summary file; an aggregate table ranking the trials
    is written alongside them.
    """
    if max_rows < 0:
        raise ValueError("max_rows must be non-negative")
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    runner = SensitivityRunner(
        contract_path, data_path, data_format=data_format
    )
    results: list[TrialResult] = []
    aggregate_entries: list[dict[str, Any]] = []
    for index, trial_path in enumerate(trial_paths):
        result = runner.run_trial(trial_path, index=index, max_rows=max_rows)
        stem = Path(trial_path).stem
        summary_name = f"sensitivity_{index:02d}_{stem}.json"
        summary_path = directory / summary_name
        payload = result.as_dict()
        payload["summary_file"] = summary_name
        summary_path.write_text(dump_json(payload), encoding="utf-8", newline="\n")
        result = replace(result, summary_path=str(summary_path))
        results.append(result)
        aggregate_entries.append(
            {
                "trial_index": index,
                "trial_path": str(Path(trial_path)),
                "summary_file": summary_name,
                "touched_fields": list(result.touched_fields),
                "baseline_violation_rows": result.baseline_violation_rows,
                "trial_violation_rows": result.trial_violation_rows,
                "pass_to_violation_rows": result.totals["pass_to_violation_rows"],
                "violation_to_pass_rows": result.totals["violation_to_pass_rows"],
                "rounding_text_only_rows": result.totals["rounding_text_only_rows"],
                "unchanged_rows": result.totals["unchanged_rows"],
                "affected_rows": result.totals["affected_rows"],
            }
        )

    aggregate = {
        "schema_version": SENSITIVITY_SCHEMA_VERSION,
        "baseline_contract": str(Path(contract_path)),
        "data_path": str(Path(data_path)),
        "data_format": runner.data_format,
        "rows_total": len(runner.rows),
        "max_rows_per_bucket": max_rows,
        "trials": aggregate_entries,
    }
    master_path = directory / "sensitivity_summary.json"
    master_path.write_text(dump_json(aggregate), encoding="utf-8", newline="\n")
    return results, master_path


# Re-exported for callers that only need fraction text parity with reports.
__all__ = [
    "SENSITIVITY_SCHEMA_VERSION",
    "ALLOWED_TOP_LEVEL_KEYS",
    "ALLOWED_FIELD_KEYS",
    "TRIALABLE_KEYS",
    "FieldImpact",
    "SensitivityRunner",
    "TrialResult",
    "load_trial_contract",
    "run_sensitivity",
    "validate_trial_structure",
]
