"""Strict normalized row reconstruction for SKLegal domain entities."""

from .mapping import (
    MAPPINGS,
    AuxiliaryWrite,
    DecomposedEntity,
    DecompositionWriteContract,
    MappingContractError,
    PersistenceMetadata,
    Reconstruction,
    decompose,
    reconstruct,
    reconstruct_with_metadata,
    validate_mapping_contract,
)

__all__ = [
    "AuxiliaryWrite",
    "DecomposedEntity",
    "DecompositionWriteContract",
    "MAPPINGS",
    "MappingContractError",
    "PersistenceMetadata",
    "Reconstruction",
    "decompose",
    "reconstruct",
    "reconstruct_with_metadata",
    "validate_mapping_contract",
]
