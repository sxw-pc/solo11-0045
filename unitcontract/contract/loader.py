"""YAML contract loading, optional overlay merging and hashing."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

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


def load_contract(
    path: str | Path,
    registry: UnitRegistry | None = None,
    overlay_path: str | Path | None = None,
) -> Contract:
    """Load and validate a contract, optionally merging an overlay YAML file."""
    contract_path = Path(path)
    payload = _read_yaml(contract_path)
    if not isinstance(payload, dict):
        raise ContractValidationError(
            [ContractIssue("E_SCHEMA_ROOT", "contract root must be a mapping", str(contract_path))]
        )
    if overlay_path is not None:
        overlay = _read_yaml(Path(overlay_path))
        if not isinstance(overlay, dict):
            raise ContractValidationError(
                [
                    ContractIssue(
                        "E_SCHEMA_ROOT",
                        "overlay root must be a mapping",
                        str(overlay_path),
                    )
                ]
            )
        payload = merge_contract_documents(payload, overlay)
    return build_contract(payload, str(contract_path), registry=registry)


def contract_sha256(path: str | Path, overlay_path: str | Path | None = None) -> str:
    """Hash the contract source bytes.

    Without an overlay this is exactly the historical ``sha256(file bytes)``,
    so existing ledger rows stay comparable.  With an overlay the base and
    overlay bytes are hashed together with a domain separator: two different
    overlays over the same base then have different raw hashes, and the
    combined hash can never collide with a plain-file hash.
    """
    base_bytes = Path(path).read_bytes()
    if overlay_path is None:
        return hashlib.sha256(base_bytes).hexdigest()
    overlay_bytes = Path(overlay_path).read_bytes()
    digest = hashlib.sha256()
    digest.update(b"unitcontract/contract-raw+overlay/v1\0")
    digest.update(base_bytes)
    digest.update(b"\0")
    digest.update(overlay_bytes)
    return digest.hexdigest()


def data_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
