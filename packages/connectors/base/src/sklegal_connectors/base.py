"""Connector-agnostic external-action state and simulation primitives.

Temporal owns orchestration and provider invocation. This package contains
the typed action contract and a development-only registry that makes repeated
dispatches return the original immutable receipt.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from enum import StrEnum
from threading import RLock
from typing import Protocol


class ConnectorInvariantError(ValueError):
    """Raised when an action would bypass a connector gate or state edge."""


class ActionStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RECEIPT_VERIFIED = "receipt_verified"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TRANSITIONS: dict[ActionStatus, frozenset[ActionStatus]] = {
    ActionStatus.DRAFT: frozenset({ActionStatus.VALIDATED, ActionStatus.CANCELLED}),
    ActionStatus.VALIDATED: frozenset(
        {ActionStatus.APPROVED, ActionStatus.CANCELLED, ActionStatus.DRAFT}
    ),
    ActionStatus.APPROVED: frozenset({ActionStatus.QUEUED, ActionStatus.CANCELLED}),
    ActionStatus.QUEUED: frozenset(
        {ActionStatus.DISPATCHED, ActionStatus.FAILED, ActionStatus.CANCELLED}
    ),
    ActionStatus.DISPATCHED: frozenset(
        {ActionStatus.RECEIPT_VERIFIED, ActionStatus.FAILED}
    ),
    ActionStatus.RECEIPT_VERIFIED: frozenset(),
    ActionStatus.FAILED: frozenset({ActionStatus.QUEUED}),
    ActionStatus.CANCELLED: frozenset(),
}


def _sha256(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _digest(value: str, field: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ConnectorInvariantError(f"{field} must be a lowercase SHA-256 digest")
    return value


def canonical_idempotency_key(
    *,
    tenant_id: str,
    matter_id: str | None,
    action_id: str,
    destination_sha256: str,
    artifact_sha256: str,
) -> str:
    """Derive the provider deduplication key from immutable action identity."""

    _digest(destination_sha256, "destination_sha256")
    _digest(artifact_sha256, "artifact_sha256")
    return _sha256(
        "sklegal-connector-dispatch-v1",
        tenant_id,
        matter_id or "",
        action_id,
        destination_sha256,
        artifact_sha256,
    )


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    """Human approval bound to one exact artifact version and digest."""

    approval_id: str
    artifact_id: str
    artifact_version: int
    artifact_sha256: str

    def __post_init__(self) -> None:
        if not self.approval_id or not self.artifact_id or self.artifact_version < 1:
            raise ConnectorInvariantError("approval binding identity is invalid")
        _digest(self.artifact_sha256, "artifact_sha256")


class CapabilityVerifier(Protocol):
    """Verify an opaque, scoped capability at the effect boundary."""

    def verify(self, *, action: Action, capability_ref: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class SimulationReceipt:
    """Immutable receipt returned by the simulation registry."""

    receipt_id: str
    idempotency_key: str
    action_id: str
    artifact_sha256: str
    destination_sha256: str
    receipt_sha256: str
    simulated: bool = True


@dataclass(frozen=True, slots=True)
class Action:
    """Immutable legal external action with append-only transition history."""

    tenant_id: str
    matter_id: str | None
    action_id: str
    connector: str
    artifact_id: str
    artifact_version: int
    artifact_sha256: str
    destination_sha256: str
    status: ActionStatus = ActionStatus.DRAFT
    events: tuple[ActionStatus, ...] = ()
    approval: ApprovalBinding | None = None
    destination_verified: bool = False
    capability_verified: bool = False
    receipt: SimulationReceipt | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if (
            not all(
                value
                for value in (
                    self.tenant_id,
                    self.action_id,
                    self.connector,
                    self.artifact_id,
                )
            )
            or self.artifact_version < 1
        ):
            raise ConnectorInvariantError("action identity is invalid")
        _digest(self.artifact_sha256, "artifact_sha256")
        _digest(self.destination_sha256, "destination_sha256")
        expected = canonical_idempotency_key(
            tenant_id=self.tenant_id,
            matter_id=self.matter_id,
            action_id=self.action_id,
            destination_sha256=self.destination_sha256,
            artifact_sha256=self.artifact_sha256,
        )
        if self.idempotency_key != expected:
            raise ConnectorInvariantError("idempotency key is not canonical")
        if self.events and self.events[-1] is not self.status:
            raise ConnectorInvariantError("event history must end at current status")
        if self.status is ActionStatus.RECEIPT_VERIFIED and self.receipt is None:
            raise ConnectorInvariantError(
                "receipt_verified requires an immutable receipt"
            )

    @property
    def idempotency_key(self) -> str:
        return canonical_idempotency_key(
            tenant_id=self.tenant_id,
            matter_id=self.matter_id,
            action_id=self.action_id,
            destination_sha256=self.destination_sha256,
            artifact_sha256=self.artifact_sha256,
        )

    def _move(self, target: ActionStatus, **changes: object) -> Action:
        if target not in _TRANSITIONS[self.status]:
            raise ConnectorInvariantError(
                f"invalid connector transition {self.status} -> {target}"
            )
        return replace(self, status=target, events=(*self.events, target), **changes)

    def validate(self, *, artifact_sha256: str) -> Action:
        if artifact_sha256 != self.artifact_sha256:
            raise ConnectorInvariantError(
                "validation must bind the exact artifact digest"
            )
        return self._move(ActionStatus.VALIDATED)

    def approve(self, binding: ApprovalBinding) -> Action:
        if (
            binding.artifact_id != self.artifact_id
            or binding.artifact_version != self.artifact_version
            or binding.artifact_sha256 != self.artifact_sha256
        ):
            raise ConnectorInvariantError(
                "approval does not bind the exact artifact version"
            )
        return self._move(ActionStatus.APPROVED, approval=binding)

    def queue(
        self,
        *,
        destination_sha256: str,
        capability_ref: str,
        capability_verifier: CapabilityVerifier,
    ) -> Action:
        if destination_sha256 != self.destination_sha256:
            raise ConnectorInvariantError("destination digest changed before dispatch")
        if self.approval is None or not capability_ref:
            raise ConnectorInvariantError(
                "queue requires exact approval and capability"
            )
        if not capability_verifier.verify(action=self, capability_ref=capability_ref):
            raise ConnectorInvariantError("capability verification failed closed")
        return self._move(
            ActionStatus.QUEUED,
            destination_verified=True,
            capability_verified=True,
        )

    def dispatch(self) -> Action:
        if not self.destination_verified or not self.capability_verified:
            raise ConnectorInvariantError(
                "dispatch requires verified destination and capability"
            )
        return self._move(ActionStatus.DISPATCHED)

    def verify_receipt(self, receipt: SimulationReceipt) -> Action:
        if self.status is not ActionStatus.DISPATCHED:
            raise ConnectorInvariantError(
                "receipt verification requires dispatched action"
            )
        if (
            receipt.idempotency_key != self.idempotency_key
            or receipt.action_id != self.action_id
            or receipt.artifact_sha256 != self.artifact_sha256
            or receipt.destination_sha256 != self.destination_sha256
        ):
            raise ConnectorInvariantError("receipt does not bind the exact action")
        return self._move(ActionStatus.RECEIPT_VERIFIED, receipt=receipt)

    def fail(self, *, reason: str) -> Action:
        if not reason:
            raise ConnectorInvariantError("failure requires a reason")
        return self._move(ActionStatus.FAILED, failure_reason=reason)

    def retry(self) -> Action:
        """Requeue a failed action without changing its immutable identity."""

        return self._move(ActionStatus.QUEUED, failure_reason=None)

    def cancel(self) -> Action:
        return self._move(ActionStatus.CANCELLED)


class SimulationRegistry:
    """Development dispatch owner with immutable, idempotent receipts."""

    def __init__(self) -> None:
        self._receipts: dict[str, SimulationReceipt] = {}
        self._lock = RLock()

    @property
    def receipt_count(self) -> int:
        return len(self._receipts)

    def dispatch(self, action: Action) -> SimulationReceipt:
        if action.status is not ActionStatus.DISPATCHED:
            raise ConnectorInvariantError(
                "simulation dispatch requires dispatched action"
            )
        receipt_id = _sha256("simulation-receipt", action.idempotency_key)
        receipt = SimulationReceipt(
            receipt_id=receipt_id,
            idempotency_key=action.idempotency_key,
            action_id=action.action_id,
            artifact_sha256=action.artifact_sha256,
            destination_sha256=action.destination_sha256,
            receipt_sha256=_sha256(
                receipt_id,
                action.action_id,
                action.artifact_sha256,
                action.destination_sha256,
            ),
        )
        with self._lock:
            existing = self._receipts.get(action.idempotency_key)
            if existing is not None:
                if existing != receipt:
                    raise ConnectorInvariantError(
                        "idempotency key has conflicting receipt"
                    )
                return existing
            self._receipts[action.idempotency_key] = receipt
            return receipt
