"""Declarative field-dimension contracts."""

from .fingerprint import (
    FINGERPRINT_ALGORITHM,
    FINGERPRINT_SPEC,
    FINGERPRINT_VERSION,
    build_fingerprint_payload,
    compute_fingerprint,
)
from .loader import (
    contract_sha256,
    data_sha256,
    load_contract,
    merge_contract_documents,
)
from .schema import (
    DIMENSIONS,
    SUPPORTED_SCHEMA_VERSION,
    Contract,
    ContractField,
    ContractIssue,
    ContractValidationError,
    build_contract,
)

__all__ = [
    "DIMENSIONS",
    "FINGERPRINT_ALGORITHM",
    "FINGERPRINT_SPEC",
    "FINGERPRINT_VERSION",
    "SUPPORTED_SCHEMA_VERSION",
    "Contract",
    "ContractField",
    "ContractIssue",
    "ContractValidationError",
    "build_contract",
    "build_fingerprint_payload",
    "compute_fingerprint",
    "contract_sha256",
    "data_sha256",
    "load_contract",
    "merge_contract_documents",
]
