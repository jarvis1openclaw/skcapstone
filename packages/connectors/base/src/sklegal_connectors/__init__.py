"""Shared simulation-first connector action foundation."""

from .base import (
    Action,
    ActionAuditReplay,
    ActionStatus,
    ApprovalBinding,
    CapabilityVerifier,
    ConnectorInvariantError,
    SimulationReceipt,
    SimulationRegistry,
    canonical_idempotency_key,
    replay_action_audit,
)

__all__ = [
    "Action",
    "ActionAuditReplay",
    "ActionStatus",
    "ApprovalBinding",
    "CapabilityVerifier",
    "ConnectorInvariantError",
    "SimulationReceipt",
    "SimulationRegistry",
    "canonical_idempotency_key",
    "replay_action_audit",
]
