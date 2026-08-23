"""Simulation-only, Matter-scoped client communication connector."""

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

_EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


def _sha(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _recipient(value: str) -> str:
    normalized = value.strip().lower()
    if not _EMAIL.fullmatch(normalized):
        raise ConnectorInvariantError(
            "client communication requires a valid client recipient"
        )
    return normalized


class CommunicationReceiptStatus(StrEnum):
    """Synthetic provider outcome for one client Communication."""

    DELIVERED = "delivered"
    MISSING_RECEIPT = "missing_receipt"


@dataclass(frozen=True, slots=True)
class CommunicationProviderResponse:
    """Synthetic delivery response used only for qualification."""

    status: CommunicationReceiptStatus
    provider_detail: str = ""

    @classmethod
    def delivered(
        cls, detail: str = "simulation confirmed client delivery"
    ) -> CommunicationProviderResponse:
        return cls(CommunicationReceiptStatus.DELIVERED, detail)

    def __post_init__(self) -> None:
        if self.status is CommunicationReceiptStatus.MISSING_RECEIPT and not (
            self.provider_detail.strip()
        ):
            raise ConnectorInvariantError(
                "missing client Communication receipt requires provider detail"
            )


@dataclass(frozen=True, slots=True)
class CommunicationAction:
    """Action plus non-secret client Communication routing metadata."""

    action: Action
    recipient: str
    privilege_label: str
    thread_id: str


@dataclass(frozen=True, slots=True)
class CommunicationReceipt:
    """Immutable synthetic receipt and reconciliation evidence."""

    simulation_receipt: SimulationReceipt | None
    status: CommunicationReceiptStatus
    thread_id: str
    delivery_sha256: str | None
    privilege_label: str
    provider_detail: str

    @property
    def receipt_id(self) -> str | None:
        return (
            None
            if self.simulation_receipt is None
            else self.simulation_receipt.receipt_id
        )

    @property
    def simulated(self) -> bool:
        return bool(self.simulation_receipt and self.simulation_receipt.simulated)


class ClientCommunicationConnector:
    """Build, dispatch, and reconcile a simulated client Communication.

    This connector has no live transport or provider client. Exact Approval,
    recipient destination, capability, and synthetic receipt evidence are
    required before a Communication can reach ``receipt_verified``.
    """

    connector_name = "client-communication"
    simulation_only = True

    def __init__(self, *, registry: SimulationRegistry | None = None) -> None:
        self._registry = registry or SimulationRegistry()

    @property
    def dispatch_count(self) -> int:
        return self._registry.receipt_count

    def begin(
        self,
        *,
        tenant_id: str,
        matter_id: str,
        action_id: str,
        artifact_id: str,
        artifact_version: int,
        message: str,
        recipient: str,
        privilege_label: str,
    ) -> CommunicationAction:
        if not matter_id:
            raise ConnectorInvariantError(
                "client Communication requires a Matter scope"
            )
        if not message.strip() or not privilege_label.strip():
            raise ConnectorInvariantError(
                "client Communication message and privilege label are required"
            )
        normalized_recipient = _recipient(recipient)
        normalized_label = privilege_label.strip()
        artifact_sha256 = _sha("client-communication-artifact-v1", message)
        destination_sha256 = _sha(
            "client-communication-destination-v1", normalized_recipient
        )
        thread_id = _sha(
            "client-communication-thread-v1",
            tenant_id,
            matter_id,
            normalized_recipient,
        )[:24]
        return CommunicationAction(
            Action(
                tenant_id=tenant_id,
                matter_id=matter_id,
                action_id=action_id,
                connector=self.connector_name,
                artifact_id=artifact_id,
                artifact_version=artifact_version,
                artifact_sha256=artifact_sha256,
                destination_sha256=destination_sha256,
            ),
            normalized_recipient,
            normalized_label,
            thread_id,
        )

    def validate(self, communication: CommunicationAction) -> CommunicationAction:
        if communication.action.connector != self.connector_name:
            raise ConnectorInvariantError("action is not a client Communication action")
        _recipient(communication.recipient)
        return CommunicationAction(
            communication.action.validate(
                artifact_sha256=communication.action.artifact_sha256
            ),
            communication.recipient,
            communication.privilege_label,
            communication.thread_id,
        )

    def approve(
        self, communication: CommunicationAction, approval: ApprovalBinding
    ) -> CommunicationAction:
        return CommunicationAction(
            communication.action.approve(approval),
            communication.recipient,
            communication.privilege_label,
            communication.thread_id,
        )

    def queue(
        self,
        communication: CommunicationAction,
        *,
        capability_ref: str,
        capability_verifier: CapabilityVerifier,
    ) -> CommunicationAction:
        return CommunicationAction(
            communication.action.queue(
                destination_sha256=communication.action.destination_sha256,
                capability_ref=capability_ref,
                capability_verifier=capability_verifier,
            ),
            communication.recipient,
            communication.privilege_label,
            communication.thread_id,
        )

    def dispatch(
        self,
        communication: CommunicationAction,
        *,
        response: CommunicationProviderResponse | None = None,
    ) -> tuple[CommunicationAction, CommunicationReceipt]:
        if communication.action.status is not ActionStatus.QUEUED:
            raise ConnectorInvariantError(
                "client Communication dispatch requires a queued action"
            )
        provider_response = response or CommunicationProviderResponse.delivered()
        action = communication.action.dispatch()
        if provider_response.status is CommunicationReceiptStatus.MISSING_RECEIPT:
            receipt = CommunicationReceipt(
                simulation_receipt=None,
                status=provider_response.status,
                thread_id=communication.thread_id,
                delivery_sha256=None,
                privilege_label=communication.privilege_label,
                provider_detail=provider_response.provider_detail,
            )
            return CommunicationAction(
                action,
                communication.recipient,
                communication.privilege_label,
                communication.thread_id,
            ), receipt

        simulation_receipt = self._registry.dispatch(action)
        receipt = CommunicationReceipt(
            simulation_receipt=simulation_receipt,
            status=provider_response.status,
            thread_id=communication.thread_id,
            delivery_sha256=_sha(
                "client-communication-delivery-v1",
                simulation_receipt.receipt_id,
                communication.recipient,
                communication.privilege_label,
            ),
            privilege_label=communication.privilege_label,
            provider_detail=provider_response.provider_detail,
        )
        return CommunicationAction(
            action,
            communication.recipient,
            communication.privilege_label,
            communication.thread_id,
        ), receipt

    def reconcile(
        self, communication: CommunicationAction, receipt: CommunicationReceipt
    ) -> CommunicationAction:
        if communication.action.status is not ActionStatus.DISPATCHED:
            raise ConnectorInvariantError(
                "client Communication reconciliation requires a dispatched action"
            )
        if (
            receipt.thread_id != communication.thread_id
            or receipt.privilege_label != communication.privilege_label
        ):
            raise ConnectorInvariantError(
                "receipt does not bind the exact client Communication"
            )
        if receipt.status is CommunicationReceiptStatus.MISSING_RECEIPT:
            return CommunicationAction(
                communication.action.fail(
                    reason=(
                        "client Communication returned no delivery receipt: "
                        + receipt.provider_detail
                    )
                ),
                communication.recipient,
                communication.privilege_label,
                communication.thread_id,
            )
        if receipt.simulation_receipt is None or receipt.delivery_sha256 is None:
            raise ConnectorInvariantError(
                "delivered client Communication requires simulation evidence"
            )
        return CommunicationAction(
            communication.action.verify_receipt(receipt.simulation_receipt),
            communication.recipient,
            communication.privilege_label,
            communication.thread_id,
        )

    def simulate(
        self,
        *,
        tenant_id: str,
        matter_id: str,
        action_id: str,
        artifact_id: str,
        artifact_version: int,
        message: str,
        recipient: str,
        privilege_label: str,
        approval: ApprovalBinding,
        capability_ref: str,
        capability_verifier: CapabilityVerifier,
        response: CommunicationProviderResponse | None = None,
    ) -> tuple[Action, CommunicationReceipt]:
        communication = self.begin(
            tenant_id=tenant_id,
            matter_id=matter_id,
            action_id=action_id,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            message=message,
            recipient=recipient,
            privilege_label=privilege_label,
        )
        communication = self.validate(communication)
        communication = self.approve(communication, approval)
        communication = self.queue(
            communication,
            capability_ref=capability_ref,
            capability_verifier=capability_verifier,
        )
        communication, receipt = self.dispatch(communication, response=response)
        communication = self.reconcile(communication, receipt)
        return communication.action, receipt
