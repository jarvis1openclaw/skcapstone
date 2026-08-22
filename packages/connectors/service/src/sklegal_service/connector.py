"""Service-of-process and mailing delivery in simulation mode only.

Qualification covers the approval, destination, duplicate, and receipt
matrix: an exact human approval is required input, the verified service
address pins the destination digest, repeated dispatches reconcile to one
immutable simulation receipt, and a carrier that returns no delivery
receipt fails closed.
"""

from __future__ import annotations

import hashlib
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


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_address(address: str) -> str:
    normalized = " ".join(address.split())
    if not normalized or normalized.count(",") < 2:
        raise ValueError("service destination requires street, locality, and region")
    return normalized


@dataclass(frozen=True, slots=True)
class AddressVerification:
    address: str
    verified: bool
    verification_sha256: str


class ServiceReceiptStatus(StrEnum):
    """Carrier outcome reported for one dispatched service or mailing."""

    DELIVERED = "delivered"
    MISSING_RECEIPT = "missing_receipt"


@dataclass(frozen=True, slots=True)
class ServiceProviderResponse:
    """Synthetic carrier response used to qualify service reconciliation."""

    status: ServiceReceiptStatus
    carrier_detail: str = ""

    @classmethod
    def delivered(
        cls, detail: str = "carrier confirmed delivery"
    ) -> ServiceProviderResponse:
        return cls(ServiceReceiptStatus.DELIVERED, detail)

    def __post_init__(self) -> None:
        if self.status is ServiceReceiptStatus.MISSING_RECEIPT and not (
            self.carrier_detail.strip()
        ):
            raise ConnectorInvariantError(
                "missing-receipt response requires a carrier detail"
            )


@dataclass(frozen=True, slots=True)
class ServiceAction:
    """Connector action bound to the verified service address it dispatches."""

    action: Action
    address: str


@dataclass(frozen=True, slots=True)
class ServiceReceipt:
    """Immutable simulated carrier receipt and reconciliation outcome."""

    simulation_receipt: SimulationReceipt | None
    status: ServiceReceiptStatus
    tracking_number: str | None
    affidavit_sha256: str | None
    address_verification: AddressVerification | None
    carrier_detail: str

    @property
    def simulation_receipt_id(self) -> str | None:
        return (
            None
            if self.simulation_receipt is None
            else self.simulation_receipt.receipt_id
        )


class ServiceMailingConnector:
    """Build, dispatch, and reconcile a simulated service or mailing action.

    No network client or live dispatch method is exposed. Address
    normalization, exact artifact approval, destination verification,
    capability verification, and immutable simulation receipts are all
    required, and a missing carrier receipt fails closed.
    """

    connector_name = "service-mailing"

    def __init__(self, *, registry: SimulationRegistry | None = None) -> None:
        self._registry = registry or SimulationRegistry()

    @property
    def dispatch_count(self) -> int:
        return self._registry.receipt_count

    def begin(
        self,
        *,
        tenant_id: str,
        matter_id: str | None,
        action_id: str,
        artifact_id: str,
        artifact_version: int,
        artifact: str,
        address: str,
    ) -> ServiceAction:
        normalized = _normalized_address(address)
        action = Action(
            tenant_id=tenant_id,
            matter_id=matter_id,
            action_id=action_id,
            connector=self.connector_name,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            artifact_sha256=_sha(artifact),
            destination_sha256=_sha(normalized),
        )
        return ServiceAction(action, normalized)

    def validate(self, service: ServiceAction) -> ServiceAction:
        _normalized_address(service.address)
        return ServiceAction(
            service.action.validate(artifact_sha256=service.action.artifact_sha256),
            service.address,
        )

    def approve(
        self, service: ServiceAction, approval: ApprovalBinding
    ) -> ServiceAction:
        return ServiceAction(service.action.approve(approval), service.address)

    def queue(
        self,
        service: ServiceAction,
        *,
        capability_ref: str,
        capability_verifier: CapabilityVerifier,
    ) -> ServiceAction:
        return ServiceAction(
            service.action.queue(
                destination_sha256=service.action.destination_sha256,
                capability_ref=capability_ref,
                capability_verifier=capability_verifier,
            ),
            service.address,
        )

    def dispatch(
        self,
        service: ServiceAction,
        *,
        response: ServiceProviderResponse | None = None,
    ) -> tuple[ServiceAction, ServiceReceipt]:
        if service.action.status is not ActionStatus.QUEUED:
            raise ConnectorInvariantError("service dispatch requires a queued action")
        provider_response = response or ServiceProviderResponse.delivered()
        action = service.action.dispatch()
        if provider_response.status is ServiceReceiptStatus.MISSING_RECEIPT:
            receipt = ServiceReceipt(
                simulation_receipt=None,
                status=provider_response.status,
                tracking_number=None,
                affidavit_sha256=None,
                address_verification=None,
                carrier_detail=provider_response.carrier_detail,
            )
            return ServiceAction(action, service.address), receipt
        simulation_receipt = self._registry.dispatch(action)
        verification = AddressVerification(
            service.address, True, _sha("address:" + service.address)
        )
        affidavit_sha256 = _sha("affidavit:" + action.action_id + ":" + service.address)
        receipt = ServiceReceipt(
            simulation_receipt=simulation_receipt,
            status=provider_response.status,
            tracking_number="SIM-" + simulation_receipt.receipt_id[:16].upper(),
            affidavit_sha256=affidavit_sha256,
            address_verification=verification,
            carrier_detail=provider_response.carrier_detail,
        )
        return ServiceAction(action, service.address), receipt

    def reconcile(
        self, service: ServiceAction, receipt: ServiceReceipt
    ) -> ServiceAction:
        """Verify the carrier outcome, failing closed on a lost receipt."""

        if service.action.status is not ActionStatus.DISPATCHED:
            raise ConnectorInvariantError(
                "service reconciliation requires a dispatched action"
            )
        if receipt.status is ServiceReceiptStatus.MISSING_RECEIPT:
            return ServiceAction(
                service.action.fail(
                    reason=(
                        "carrier returned no delivery receipt: "
                        + receipt.carrier_detail
                    )
                ),
                service.address,
            )
        verification = receipt.address_verification
        if (
            verification is None
            or not verification.verified
            or verification.address != service.address
        ):
            raise ConnectorInvariantError(
                "receipt does not bind the verified service destination"
            )
        if receipt.simulation_receipt is None:
            raise ConnectorInvariantError(
                "delivered receipt requires simulation evidence"
            )
        return ServiceAction(
            service.action.verify_receipt(receipt.simulation_receipt),
            service.address,
        )

    def simulate(
        self,
        *,
        tenant_id: str,
        matter_id: str | None,
        action_id: str,
        artifact_id: str,
        artifact_version: int,
        artifact: str,
        address: str,
        approval: ApprovalBinding,
        capability_ref: str,
        capability_verifier: CapabilityVerifier,
        response: ServiceProviderResponse | None = None,
    ) -> tuple[Action, ServiceReceipt]:
        service = self.begin(
            tenant_id=tenant_id,
            matter_id=matter_id,
            action_id=action_id,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            artifact=artifact,
            address=address,
        )
        service = self.validate(service)
        service = self.approve(service, approval)
        service = self.queue(
            service,
            capability_ref=capability_ref,
            capability_verifier=capability_verifier,
        )
        service, receipt = self.dispatch(service, response=response)
        service = self.reconcile(service, receipt)
        return service.action, receipt
