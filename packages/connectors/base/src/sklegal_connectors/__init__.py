"""Shared simulation-first connector action foundation."""

from .base import (
    Action,
    ActionStatus,
    ApprovalBinding,
    CapabilityVerifier,
    ConnectorInvariantError,
    SimulationReceipt,
    SimulationRegistry,
    canonical_idempotency_key,
)

__all__ = [
    "Action",
    "ActionStatus",
    "ApprovalBinding",
    "CapabilityVerifier",
    "ConnectorInvariantError",
    "SimulationReceipt",
    "SimulationRegistry",
    "canonical_idempotency_key",
]
