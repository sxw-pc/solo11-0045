"""Semantic fingerprint for field contracts.

The ledger keeps the raw contract byte hash for provenance and backward
compatibility.  The *fingerprint* answers a different question: "do these two
contract documents describe the same effective field configuration?"  It is a
SHA-256 over a canonical JSON document built from the fully merged, validated
contract model, not from the source bytes.

Normalisation rules (also documented in README):

* ``defaults`` are expanded into every field before hashing, so a value that is
  inherited from ``defaults`` is indistinguishable from the same value written
  out explicitly.
* Fields are ordered by field name.  YAML/overlay declaration order, comments,
  blank lines and other byte-level differences do not matter.
* ``allowed_units`` is de-duplicated and sorted, so list order does not matter.
* Numeric values are represented as exact rational text (``n`` or ``n/d``)
  parsed through :class:`fractions.Fraction`; ``60``, ``60.0`` and ``6e1`` are
  the same value.
* Range bounds are converted to the dimension's base unit with exact ratio /
  affine arithmetic.  ``canonical_unit`` itself remains part of the hash
  because it determines validation and normalised output semantics.

Temperature range criterion (explicitly chosen and documented):

    A contract with ``canonical_unit: C`` and ``range: {min: -50, max: 60}``
    is **not** equivalent to one with ``canonical_unit: K`` and
    ``range: {min: 223.15, max: 333.15}``.  The physical intervals match, but
    ``canonical_unit`` is part of the contract configuration: it decides which
    unit the report emits and how ``range`` is interpreted during validation.
    Range endpoints themselves carry no unit annotation, so we never guess the
    author's unit.  Two fields are range-equivalent only when the interval is
    identical after conversion to the dimension base unit *and* the rest of the
    field configuration (including ``canonical_unit``) matches.

Descriptive metadata (contract ``name``, ``description``) and unknown top-level
keys are excluded: the fingerprint describes field configuration semantics, not
the document's identity.  ``schema_version`` is included because it changes how
the fields are interpreted.
"""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from typing import Any

from unitcontract.units.registry import UnitRegistry, UnitRegistryError
from unitcontract.units.temperature import to_kelvin_unchecked

from .schema import Contract, ContractField

FINGERPRINT_VERSION = 1
FINGERPRINT_ALGORITHM = "sha256"

# Domain separator so this digest can never be confused with a raw byte hash
# produced by ``contract_sha256`` or any other SHA-256 use in the project.
_DOMAIN = b"unitcontract/contract-fingerprint/v1\n"

#: Machine-readable description of the criteria above.  Reports expose this in
#: their extension section so a ledger reader can tell what was hashed.
FINGERPRINT_SPEC: dict[str, Any] = {
    "version": FINGERPRINT_VERSION,
    "algorithm": FINGERPRINT_ALGORITHM,
    "included": [
        "schema_version",
        "fields[].field",
        "fields[].dimension",
        "fields[].canonical_unit",
        "fields[].allowed_units (de-duplicated, sorted)",
        "fields[].precision",
        "fields[].rounding",
        "fields[].range (exact rationals in the dimension base unit)",
    ],
    "excluded": [
        "contract name",
        "contract description",
        "comments, blank lines and byte formatting",
        "field declaration order",
        "allowed_units order and duplicate entries",
        "top-level keys not part of the schema",
    ],
    "defaults": "expanded into each field before hashing",
    "field_order": "ascending by field name",
    "number_format": "exact rational: <numerator> or <numerator>/<denominator>",
    "range_unit": "dimension base unit (temperature: K)",
    "canonical_unit_included": True,
    "temperature_range_equivalence": (
        "not_equivalent_when_canonical_unit_differs: ranges are compared after "
        "exact conversion to K, but canonical_unit remains part of the "
        "fingerprint because it changes validation and output semantics"
    ),
}


def _fraction_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _as_fraction(value: Any) -> Fraction:
    """Coerce a bound to an exact rational.

    Validated contracts already hold ``Fraction`` values; this keeps directly
    constructed degenerate ``ContractField`` objects from raising.
    """
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):
        raise ValueError("range bound must be numeric")
    return Fraction(str(value))


def _bound_to_base(
    value: Fraction | None,
    field: ContractField,
    registry: UnitRegistry,
) -> str | None:
    """Return an exact rational string in the dimension base unit.

    Bounds that are already on the base unit are returned unchanged.  Affine
    dimensions use the guard-free temperature conversion so even a bizarre
    range such as ``-300 C`` (allowed by schema validation, rejected only for
    data rows) can still be fingerprinted without raising.
    """
    if value is None:
        return None
    amount = _as_fraction(value)
    base = registry.base_unit(field.dimension)
    if field.canonical_unit == base:
        return _fraction_text(amount)
    if registry.is_affine(field.dimension):
        if base != "K":
            raise UnitRegistryError(
                f"affine dimension {field.dimension!r} must use K as base unit"
            )
        converted = to_kelvin_unchecked(amount, field.canonical_unit)
    else:
        converted = registry.convert(
            amount, field.dimension, field.canonical_unit, base
        )
    return _fraction_text(converted)


def _normalized_units(field: ContractField) -> list[str]:
    # ``sorted`` on the de-duplicated set makes order and duplicates irrelevant.
    # ``key=str`` keeps a hand-constructed degenerate field with a non-string
    # entry from raising; validated contracts only contain strings.
    return sorted({unit for unit in (field.allowed_units or ())}, key=str)


def _normalized_precision(value: Any) -> int:
    """Schema-valid contracts always carry a non-negative ``int``.

    Coercion only guards hand-built degenerate fields; ``True`` deliberately
    stays ``True`` so a malformed object cannot silently collide with ``1``.
    """
    if isinstance(value, bool):
        return value  # pragma: no cover - only reachable via direct construction
    if isinstance(value, int):
        return value
    return int(value)


def build_fingerprint_payload(
    contract: Contract,
    registry: UnitRegistry | None = None,
) -> dict[str, Any]:
    """Build the canonical, JSON-serialisable form that gets hashed."""
    registry = registry or UnitRegistry.bundled()
    fields: list[dict[str, Any]] = []
    for field in sorted(contract.fields, key=lambda item: item.field):
        fields.append(
            {
                "field": field.field,
                "dimension": field.dimension,
                "canonical_unit": field.canonical_unit,
                "allowed_units": _normalized_units(field),
                "precision": _normalized_precision(field.precision),
                "rounding": field.rounding,
                "range": {
                    "min": _bound_to_base(field.range_min, field, registry),
                    "max": _bound_to_base(field.range_max, field, registry),
                },
            }
        )
    return {
        "fingerprint_version": FINGERPRINT_VERSION,
        "schema_version": contract.schema_version,
        "fields": fields,
    }


def serialize_fingerprint_payload(payload: dict[str, Any]) -> bytes:
    """Canonical JSON bytes: sorted keys, no whitespace, UTF-8."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def compute_fingerprint(
    contract: Contract,
    registry: UnitRegistry | None = None,
) -> str:
    """Return the stable semantic SHA-256 fingerprint for *contract*.

    Degenerate but directly constructed contracts are accepted: an empty
    ``allowed_units`` tuple serialises as ``[]`` and missing/``None`` range
    bounds serialise as ``null``.  (``load_contract`` still rejects an empty
    ``allowed_units`` list with ``E_ALLOWED_MISSING`` before this point.)
    """
    payload = build_fingerprint_payload(contract, registry)
    digest = hashlib.sha256(_DOMAIN + serialize_fingerprint_payload(payload))
    return digest.hexdigest()


__all__ = [
    "FINGERPRINT_ALGORITHM",
    "FINGERPRINT_SPEC",
    "FINGERPRINT_VERSION",
    "build_fingerprint_payload",
    "compute_fingerprint",
    "serialize_fingerprint_payload",
]
