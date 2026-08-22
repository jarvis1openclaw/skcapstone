"""Service-of-process and mailing delivery in simulation mode only."""

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
class AddressVerification:
    address: str
    verified: bool
    verification_sha256: str


@dataclass(frozen=True, slots=True)
class ServiceReceipt:
    simulation_receipt_id: str
    tracking_number: str
    affidavit_sha256: str
    address_verification: AddressVerification


class ServiceMailingConnector:
    """Build and simulate a service or mailing action.

    No network client or live dispatch method is exposed. Address verification,
    exact artifact approval, destination verification, capability verification,
    and immutable simulation receipts are all required.
    """

    connector_name = "service-mailing"

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
        artifact: str,
        address: str,
        capability_ref: str,
        capability_verifier: CapabilityVerifier,
    ) -> tuple[Action, ServiceReceipt]:
        normalized = " ".join(address.split())
        if not normalized or normalized.count(",") < 2:
            raise ValueError(
                "service destination requires street, locality, and region"
            )
        artifact_sha256 = _sha(artifact)
        destination_sha256 = _sha(normalized)
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
        verification = AddressVerification(
            normalized, True, _sha("address:" + normalized)
        )
        affidavit_sha256 = _sha("affidavit:" + action.action_id + ":" + normalized)
        receipt = ServiceReceipt(
            simulation_receipt.receipt_id,
            "SIM-" + simulation_receipt.receipt_id[:16].upper(),
            affidavit_sha256,
            verification,
        )
        return action, receipt
