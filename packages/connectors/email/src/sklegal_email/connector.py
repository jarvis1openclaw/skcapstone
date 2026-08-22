"""Simulation-only email action adapter built on the shared connector base."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from sklegal_connectors import (
    Action,
    ActionStatus,
    ApprovalBinding,
    CapabilityVerifier,
    ConnectorInvariantError,
    SimulationReceipt,
    SimulationRegistry,
)


class EmailValidationError(ConnectorInvariantError):
    """Raised when an email draft cannot be safely simulated."""


class ReceiptStatus(StrEnum):
    DELIVERED = "delivered"
    BOUNCED = "bounced"


_EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _recipients(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(value.strip().lower() for value in values)
    if not normalized or any(not _EMAIL.fullmatch(value) for value in normalized):
        raise EmailValidationError("all recipients must be valid email addresses")
    if len(set(normalized)) != len(normalized):
        raise EmailValidationError("duplicate recipients are not allowed")
    return normalized


@dataclass(frozen=True, slots=True)
class EmailAction:
    """Action plus non-secret metadata needed for simulation and review."""

    action: Action
    recipients: tuple[str, ...]
    subject_digest: str


@dataclass(frozen=True, slots=True)
class EmailReceipt:
    """Immutable simulated provider receipt and reconciliation outcome."""

    simulation_receipt: SimulationReceipt
    status: ReceiptStatus
    recipient_count: int


class EmailSimulation:
    """Email connector with no live transport and an approval gate by default."""

    def __init__(self, *, registry: SimulationRegistry | None = None) -> None:
        self._registry = registry or SimulationRegistry()

    @property
    def dispatch_count(self) -> int:
        return self._registry.receipt_count

    def draft(
        self,
        *,
        tenant_id: str,
        matter_id: str | None,
        action_id: str,
        artifact_id: str,
        artifact_version: int,
        artifact_sha256: str,
        recipients: tuple[str, ...],
        subject: str,
    ) -> EmailAction:
        normalized = _recipients(recipients)
        if not subject.strip():
            raise EmailValidationError("subject cannot be empty")
        destination_sha256 = _digest("email-destination-v1", *normalized)
        action = Action(
            tenant_id=tenant_id,
            matter_id=matter_id,
            action_id=action_id,
            connector="email",
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            artifact_sha256=artifact_sha256,
            destination_sha256=destination_sha256,
        )
        return EmailAction(action, normalized, _digest("email-subject-v1", subject))

    def validate(self, email: EmailAction) -> EmailAction:
        _recipients(email.recipients)
        return EmailAction(
            email.action.validate(artifact_sha256=email.action.artifact_sha256),
            email.recipients,
            email.subject_digest,
        )

    def approve(self, email: EmailAction, approval: ApprovalBinding) -> EmailAction:
        return EmailAction(
            email.action.approve(approval), email.recipients, email.subject_digest
        )

    def queue(
        self,
        email: EmailAction,
        *,
        capability_ref: str,
        capability_verifier: CapabilityVerifier,
    ) -> EmailAction:
        return EmailAction(
            email.action.queue(
                destination_sha256=email.action.destination_sha256,
                capability_ref=capability_ref,
                capability_verifier=capability_verifier,
            ),
            email.recipients,
            email.subject_digest,
        )

    def dispatch(
        self,
        email: EmailAction,
        *,
        status: ReceiptStatus = ReceiptStatus.DELIVERED,
    ) -> tuple[EmailAction, EmailReceipt]:
        if email.action.status is not ActionStatus.QUEUED:
            raise EmailValidationError("email dispatch requires a queued action")
        dispatched = EmailAction(
            email.action.dispatch(), email.recipients, email.subject_digest
        )
        simulation_receipt = self._registry.dispatch(dispatched.action)
        return dispatched, EmailReceipt(
            simulation_receipt=simulation_receipt,
            status=status,
            recipient_count=len(dispatched.recipients),
        )

    def reconcile(self, email: EmailAction, receipt: EmailReceipt) -> EmailAction:
        if receipt.recipient_count != len(email.recipients):
            raise EmailValidationError("receipt recipient count does not match action")
        if receipt.status is ReceiptStatus.BOUNCED:
            return EmailAction(
                email.action.fail(reason="email provider reported a bounce"),
                email.recipients,
                email.subject_digest,
            )
        return EmailAction(
            email.action.verify_receipt(receipt.simulation_receipt),
            email.recipients,
            email.subject_digest,
        )
