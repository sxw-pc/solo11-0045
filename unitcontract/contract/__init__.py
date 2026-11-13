"""Declarative field-dimension contracts."""

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
    "SUPPORTED_SCHEMA_VERSION",
    "Contract",
    "ContractField",
    "ContractIssue",
    "ContractValidationError",
    "build_contract",
    "contract_sha256",
    "data_sha256",
    "load_contract",
    "merge_contract_documents",
]
