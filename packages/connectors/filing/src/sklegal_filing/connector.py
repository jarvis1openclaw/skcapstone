"""Court filing external-action connector.

The connector deliberately exposes only a simulation path.  Temporal or a
future human-approved follow-up owns any real provider integration; this
package can therefore be used to qualify filing plans without side effects.
Qualification covers the approval, destination, duplicate, and receipt
matrix: an exact human approval is required input, partial clerk acceptance
and missing clerk receipts fail closed, and repeated dispatches reconcile to
one immutable simulation receipt.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
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

SUPPORTED_FORUMS = frozenset({"federal", "state", "local"})


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class FilingPlan:
    """Immutable filing metadata and the exact documents to be filed."""

    forum: str
    court: str
    case_number: str
    documents: Mapping[str, bytes]

    def __post_init__(self) -> None:
        if self.forum not in SUPPORTED_FORUMS:
            raise ValueError(f"unsupported filing forum: {self.forum}")
        if not self.court.strip() or not self.case_number.strip():
            raise ValueError("court and case number are required")
        if not self.documents:
            raise ValueError("filing package must contain a document")
        names = tuple(self.documents)
        if any(not name.strip() or "/" in name or "\\" in name for name in names):
            raise ValueError("filing document names must be plain filenames")
        if len(set(names)) != len(names):
            raise ValueError("filing document names must be unique")

    @property
    def document_names(self) -> frozenset[str]:
        return frozenset(self.documents)

    @property
    def package_sha256(self) -> str:
        manifest = [
            {"name": name, "sha256": _sha256(self.documents[name])}
            for name in sorted(self.documents)
        ]
        payload = json.dumps(
            {
                "forum": self.forum,
                "court": self.court.strip(),
                "case_number": self.case_number.strip(),
                "documents": manifest,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return _sha256(payload)

    @property
    def destination_sha256(self) -> str:
        return _sha256(
            "\0".join(
                (self.forum, self.court.strip(), self.case_number.strip())
            ).encode()
        )


class FilingReceiptStatus(StrEnum):
    """Clerk outcome reported for one dispatched filing package."""

    ACCEPTED = "accepted"
    PARTIAL = "partial"
    MISSING_RECEIPT = "missing_receipt"


@dataclass(frozen=True, slots=True)
class FilingProviderResponse:
    """Synthetic clerk response used to qualify filing reconciliation."""

    status: FilingReceiptStatus
    accepted_documents: frozenset[str] = frozenset()
    rejected_documents: frozenset[str] = frozenset()
    clerk_detail: str = ""

    @classmethod
    def full_acceptance(
        cls, detail: str = "clerk accepted the full package"
    ) -> FilingProviderResponse:
        return cls(FilingReceiptStatus.ACCEPTED, clerk_detail=detail)

    def __post_init__(self) -> None:
        if self.accepted_documents & self.rejected_documents:
            raise ConnectorInvariantError(
                "provider response accepts and rejects the same document"
            )
        if self.status is FilingReceiptStatus.ACCEPTED and self.rejected_documents:
            raise ConnectorInvariantError(
                "accepted response cannot report rejected documents"
            )
        if self.status is FilingReceiptStatus.PARTIAL and (
            not self.accepted_documents
            or not self.rejected_documents
            or not self.clerk_detail.strip()
        ):
            raise ConnectorInvariantError(
                "partial response must split the package and give a clerk reason"
            )
        if self.status is FilingReceiptStatus.MISSING_RECEIPT and (
            self.accepted_documents
            or self.rejected_documents
            or not self.clerk_detail.strip()
        ):
            raise ConnectorInvariantError(
                "missing-receipt response cannot report acceptance and needs detail"
            )


def _validated_response(
    plan: FilingPlan, response: FilingProviderResponse
) -> frozenset[str]:
    """Validate the response against the plan and return accepted documents."""

    names = plan.document_names
    if not names >= response.accepted_documents | response.rejected_documents:
        raise ConnectorInvariantError(
            "provider response names documents outside the filing plan"
        )
    if response.status is FilingReceiptStatus.ACCEPTED:
        if response.accepted_documents and response.accepted_documents != names:
            raise ConnectorInvariantError(
                "accepted response must accept exactly the full package"
            )
        return names
    if (
        response.status is FilingReceiptStatus.PARTIAL
        and response.accepted_documents | response.rejected_documents != names
    ):
        raise ConnectorInvariantError(
            "partial response must account for every filed document"
        )
    return frozenset(response.accepted_documents)


@dataclass(frozen=True, slots=True)
class FilingAction:
    """Connector action bound to the exact filing plan it dispatches."""

    action: Action
    filing_plan: FilingPlan


@dataclass(frozen=True, slots=True)
class CourtFilingReceipt:
    """Receipt evidence reconciled to one simulated filing action."""

    simulation_receipt: SimulationReceipt | None
    status: FilingReceiptStatus
    forum: str
    court: str
    case_number: str
    package_sha256: str
    destination_sha256: str
    clerk_receipt_id: str | None
    accepted_documents: frozenset[str]
    rejected_documents: frozenset[str]
    clerk_detail: str


class CourtFilingConnector:
    """Construct, dispatch, and reconcile a simulated approval-gated filing."""

    connector_name = "court-filing"

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
        filing_plan: FilingPlan,
    ) -> FilingAction:
        action = Action(
            tenant_id=tenant_id,
            matter_id=matter_id,
            action_id=action_id,
            connector=self.connector_name,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            artifact_sha256=filing_plan.package_sha256,
            destination_sha256=filing_plan.destination_sha256,
        )
        return FilingAction(action, filing_plan)

    def validate(self, filing: FilingAction) -> FilingAction:
        return FilingAction(
            filing.action.validate(artifact_sha256=filing.filing_plan.package_sha256),
            filing.filing_plan,
        )

    def approve(self, filing: FilingAction, approval: ApprovalBinding) -> FilingAction:
        return FilingAction(filing.action.approve(approval), filing.filing_plan)

    def queue(
        self,
        filing: FilingAction,
        *,
        capability_ref: str,
        capability_verifier: CapabilityVerifier,
    ) -> FilingAction:
        return FilingAction(
            filing.action.queue(
                destination_sha256=filing.filing_plan.destination_sha256,
                capability_ref=capability_ref,
                capability_verifier=capability_verifier,
            ),
            filing.filing_plan,
        )

    def dispatch(
        self,
        filing: FilingAction,
        *,
        response: FilingProviderResponse | None = None,
    ) -> tuple[FilingAction, CourtFilingReceipt]:
        if filing.action.status is not ActionStatus.QUEUED:
            raise ConnectorInvariantError("filing dispatch requires a queued action")
        provider_response = response or FilingProviderResponse.full_acceptance()
        plan = filing.filing_plan
        accepted = _validated_response(plan, provider_response)
        action = filing.action.dispatch()
        if provider_response.status is FilingReceiptStatus.MISSING_RECEIPT:
            simulation_receipt = None
            clerk_receipt_id = None
        else:
            simulation_receipt = self._registry.dispatch(action)
            clerk_receipt_id = "SIM-CLERK-" + simulation_receipt.receipt_id[:16].upper()
        receipt = CourtFilingReceipt(
            simulation_receipt=simulation_receipt,
            status=provider_response.status,
            forum=plan.forum,
            court=plan.court.strip(),
            case_number=plan.case_number.strip(),
            package_sha256=plan.package_sha256,
            destination_sha256=plan.destination_sha256,
            clerk_receipt_id=clerk_receipt_id,
            accepted_documents=accepted,
            rejected_documents=frozenset(provider_response.rejected_documents),
            clerk_detail=provider_response.clerk_detail,
        )
        return FilingAction(action, plan), receipt

    def reconcile(
        self, filing: FilingAction, receipt: CourtFilingReceipt
    ) -> FilingAction:
        """Verify the clerk outcome, failing closed on partial or lost results."""

        if filing.action.status is not ActionStatus.DISPATCHED:
            raise ConnectorInvariantError(
                "filing reconciliation requires a dispatched action"
            )
        if (
            receipt.package_sha256 != filing.filing_plan.package_sha256
            or receipt.destination_sha256 != filing.filing_plan.destination_sha256
        ):
            raise ConnectorInvariantError("receipt does not bind the exact filing plan")
        if receipt.status is FilingReceiptStatus.MISSING_RECEIPT:
            return FilingAction(
                filing.action.fail(
                    reason=(
                        "court filing returned no clerk receipt: "
                        + receipt.clerk_detail
                    )
                ),
                filing.filing_plan,
            )
        if receipt.status is FilingReceiptStatus.PARTIAL:
            rejected = ", ".join(sorted(receipt.rejected_documents))
            return FilingAction(
                filing.action.fail(
                    reason="partial filing acceptance; rejected documents: " + rejected
                ),
                filing.filing_plan,
            )
        if receipt.simulation_receipt is None:
            raise ConnectorInvariantError(
                "accepted receipt requires simulation evidence"
            )
        return FilingAction(
            filing.action.verify_receipt(receipt.simulation_receipt),
            filing.filing_plan,
        )

    def simulate(
        self,
        *,
        tenant_id: str,
        matter_id: str | None,
        action_id: str,
        artifact_id: str,
        artifact_version: int,
        filing_plan: FilingPlan,
        approval: ApprovalBinding,
        capability_ref: str,
        capability_verifier: CapabilityVerifier,
        response: FilingProviderResponse | None = None,
    ) -> tuple[Action, CourtFilingReceipt]:
        filed = self.begin(
            tenant_id=tenant_id,
            matter_id=matter_id,
            action_id=action_id,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            filing_plan=filing_plan,
        )
        filed = self.validate(filed)
        filed = self.approve(filed, approval)
        filed = self.queue(
            filed,
            capability_ref=capability_ref,
            capability_verifier=capability_verifier,
        )
        filed, receipt = self.dispatch(filed, response=response)
        filed = self.reconcile(filed, receipt)
        return filed.action, receipt

    @staticmethod
    def reconcile_receipt(
        action: Action, filing_plan: FilingPlan, receipt: CourtFilingReceipt
    ) -> bool:
        """Confirm clerk evidence is bound to the exact action and package."""

        return (
            action.connector == "court-filing"
            and receipt.simulation_receipt is not None
            and action.receipt == receipt.simulation_receipt
            and receipt.status is FilingReceiptStatus.ACCEPTED
            and receipt.package_sha256 == filing_plan.package_sha256
            and receipt.destination_sha256 == filing_plan.destination_sha256
            and receipt.forum == filing_plan.forum
            and receipt.court == filing_plan.court.strip()
            and receipt.case_number == filing_plan.case_number.strip()
            and receipt.clerk_receipt_id is not None
            and receipt.clerk_receipt_id.startswith("SIM-CLERK-")
        )
