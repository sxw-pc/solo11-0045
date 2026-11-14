"""YAML contract loading, optional overlay merging and hashing."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

import yaml

from unitcontract.units.registry import UnitRegistry

from .schema import Contract, ContractIssue, ContractValidationError, build_contract


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractValidationError(
            [ContractIssue("E_YAML_INVALID", f"cannot read file: {exc}", str(path))]
        ) from exc
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f"{path}#line={mark.line + 1}" if mark else str(path)
        raise ContractValidationError(
            [ContractIssue("E_YAML_INVALID", f"invalid YAML: {exc}", location)]
        ) from exc
    return payload


def merge_contract_documents(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge an overlay contract document.

    Top-level scalars from *overlay* replace base values. Fields are merged by
    their ``field`` key; overlay declarations may override individual keys or
    add new fields. This is intentionally a small, predictable merge, not a
    general YAML merge implementation.
    """
    merged: dict[str, Any] = dict(base)
    if "defaults" in overlay:
        defaults = dict(base.get("defaults") or {})
        defaults.update(overlay.get("defaults") or {})
        merged["defaults"] = defaults

    overlay_fields = overlay.get("fields")
    if isinstance(overlay_fields, list):
        base_fields = base.get("fields") or []
        by_name: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        anonymous: list[dict[str, Any]] = []
        for item in base_fields:
            if isinstance(item, dict) and isinstance(item.get("field"), str):
                name = item["field"]
                if name not in by_name:
                    order.append(name)
                by_name[name] = dict(item)
            elif isinstance(item, dict):
                anonymous.append(dict(item))
        for item in overlay_fields:
            if not isinstance(item, dict):
                anonymous.append(item)
                continue
            name = item.get("field")
            if isinstance(name, str) and name in by_name:
                by_name[name].update(item)
            elif isinstance(name, str):
                order.append(name)
                by_name[name] = dict(item)
            else:
                anonymous.append(item)
        merged["fields"] = [by_name[name] for name in order] + anonymous

    for key, value in overlay.items():
        if key not in {"fields", "defaults"}:
            merged[key] = value
    return merged


def compute_field_key_sources(
    base_label: str,
    base: dict[str, Any],
    overlays: list[tuple[str, dict[str, Any]]],
) -> dict[str, dict[str, str]]:
    """Track which document supplied each field's effective keys.

    ``overlays`` are ``(label, document)`` pairs in application order. The
    result maps field name -> key -> source label and mirrors the merge
    semantics exactly: a key explicitly set in a field entry wins over
    ``defaults`` regardless of position; among field entries (or among
    ``defaults``) the later document wins. Keys absent from every document are
    omitted here and reported as built-in fallbacks by the schema builder.
    """
    chain: list[tuple[str, dict[str, Any]]] = [(base_label, base), *overlays]
    sources: dict[str, dict[str, str]] = {}
    field_level: dict[str, set[str]] = {}
    for label, document in chain:
        fields = document.get("fields") if isinstance(document, dict) else None
        if not isinstance(fields, list):
            continue
        for item in fields:
            if not isinstance(item, dict):
                continue
            name = item.get("field")
            if not isinstance(name, str):
                continue
            entry = sources.setdefault(name, {})
            explicit = field_level.setdefault(name, set())
            entry.setdefault("field", label)  # first declarer owns the name
            for key in item:
                if key != "field":
                    entry[key] = label  # last explicit writer wins
                    explicit.add(key)
    for label, document in chain:
        defaults = document.get("defaults") if isinstance(document, dict) else None
        if not isinstance(defaults, dict):
            continue
        for key in defaults:
            for name, entry in sources.items():
                # defaults only supply keys no field entry set explicitly;
                # among defaults the later document wins
                if key not in field_level.get(name, ()):
                    entry[key] = label
    return sources


def _read_overlay(path: str | Path) -> dict[str, Any]:
    overlay = _read_yaml(Path(path))
    if not isinstance(overlay, dict):
        raise ContractValidationError(
            [
                ContractIssue(
                    "E_SCHEMA_ROOT",
                    "overlay root must be a mapping",
                    str(path),
                )
            ]
        )
    return overlay


def load_contract(
    path: str | Path,
    registry: UnitRegistry | None = None,
    overlay_path: str | Path | None = None,
    overlay_paths: Iterable[str | Path] | None = None,
) -> Contract:
    """Load and validate a contract, optionally chaining overlay YAML files.

    Overlays merge in the given order; on overlapping keys the later overlay
    wins. ``overlay_path`` (singular) is the legacy single-overlay form and is
    applied first when both forms are given. Folding the chain of overlays one
    by one is equivalent to manually composing them into a single overlay.
    """
    contract_path = Path(path)
    payload = _read_yaml(contract_path)
    if not isinstance(payload, dict):
        raise ContractValidationError(
            [ContractIssue("E_SCHEMA_ROOT", "contract root must be a mapping", str(contract_path))]
        )
    ordered: list[str | Path] = []
    if overlay_path is not None:
        ordered.append(overlay_path)
    if overlay_paths:
        ordered.extend(overlay_paths)
    if not ordered:
        return build_contract(payload, str(contract_path), registry=registry)

    # Provenance labels are file names (not paths) so reports stay
    # byte-identical regardless of the checkout directory.
    chain: list[tuple[str, dict[str, Any]]] = [
        (Path(item).name, _read_overlay(item)) for item in ordered
    ]
    key_sources = compute_field_key_sources(Path(contract_path).name, payload, chain)
    merged = payload
    for _, document in chain:
        merged = merge_contract_documents(merged, document)
    return build_contract(
        merged,
        str(contract_path),
        registry=registry,
        key_sources=key_sources,
        overlays=tuple(label for label, _ in chain),
    )


def contract_sha256(path: str | Path) -> str:
    """Hash the contract bytes so every ledger row can be reproduced exactly."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def data_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
