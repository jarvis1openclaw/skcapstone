"""Simulation-only client communication connector."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sklegal_connectors import (
    Action,
    ApprovalBinding,
    CapabilityVerifier,
    SimulationRegistry,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CommunicationReceipt:
    receipt_id: str
    thread_id: str
    delivery_sha256: str
    privilege_label: str
    simulated: bool = True


class ClientCommunicationConnector:
    """Deliver a client message only through the governed simulation path."""

    connector_name = "client-communication"

    def __init__(self, *, registry: SimulationRegistry | None = None) -> None:
        self._registry = registry or SimulationRegistry()

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
        capability_ref: str,
        capability_verifier: CapabilityVerifier,
    ) -> tuple[Action, CommunicationReceipt]:
        if not matter_id:
            raise ValueError("client communication requires a matter scope")
        if not message.strip() or "@" not in recipient or not privilege_label.strip():
            raise ValueError("message, recipient, and privilege label are required")
        thread_id = _sha(tenant_id + "\0" + matter_id + "\0" + recipient)[:24]
        artifact_sha256 = _sha(message)
        destination_sha256 = _sha(recipient.strip().lower())
        action = Action(
            tenant_id=tenant_id,
            matter_id=matter_id,
            action_id=action_id,
            connector=self.connector_name,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            artifact_sha256=artifact_sha256,
            destination_sha256=destination_sha256,
        )
        action = (
            action.validate(artifact_sha256=artifact_sha256)
            .approve(
                ApprovalBinding(
                    action_id, artifact_id, artifact_version, artifact_sha256
                )
            )
            .queue(
                destination_sha256=destination_sha256,
                capability_ref=capability_ref,
                capability_verifier=capability_verifier,
            )
            .dispatch()
        )
        simulation_receipt = self._registry.dispatch(action)
        action = action.verify_receipt(simulation_receipt)
        return action, CommunicationReceipt(
            receipt_id=simulation_receipt.receipt_id,
            thread_id=thread_id,
            delivery_sha256=_sha(simulation_receipt.receipt_id + "\0" + recipient),
            privilege_label=privilege_label.strip(),
        )
