"""Court filing external-action connector.

The connector deliberately exposes only a simulation path.  Temporal or a
future human-approved follow-up owns any real provider integration; this
package can therefore be used to qualify filing plans without side effects.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from sklegal_connectors import (
    Action,
    ApprovalBinding,
    CapabilityVerifier,
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
            "\0".join((self.forum, self.court.strip(), self.case_number.strip())).encode()
        )


@dataclass(frozen=True, slots=True)
class CourtFilingReceipt:
    """Receipt evidence reconciled to one simulated filing action."""

    simulation_receipt: SimulationReceipt
    forum: str
    court: str
    case_number: str
    package_sha256: str
    destination_sha256: str
    clerk_receipt_id: str


class CourtFilingConnector:
    """Construct and simulate an approval-gated court filing."""

    connector_name = "court-filing"

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
        filing_plan: FilingPlan,
        capability_ref: str,
        capability_verifier: CapabilityVerifier,
    ) -> tuple[Action, CourtFilingReceipt]:
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
        action = (
            action.validate(artifact_sha256=filing_plan.package_sha256)
            .approve(
                ApprovalBinding(
                    action_id,
                    artifact_id,
                    artifact_version,
                    filing_plan.package_sha256,
                )
            )
            .queue(
                destination_sha256=filing_plan.destination_sha256,
                capability_ref=capability_ref,
                capability_verifier=capability_verifier,
            )
            .dispatch()
        )
        simulation_receipt = self._registry.dispatch(action)
        action = action.verify_receipt(simulation_receipt)
        receipt = CourtFilingReceipt(
            simulation_receipt=simulation_receipt,
            forum=filing_plan.forum,
            court=filing_plan.court.strip(),
            case_number=filing_plan.case_number.strip(),
            package_sha256=filing_plan.package_sha256,
            destination_sha256=filing_plan.destination_sha256,
            clerk_receipt_id="SIM-CLERK-" + simulation_receipt.receipt_id[:16].upper(),
        )
        return action, receipt

    @staticmethod
    def reconcile_receipt(
        action: Action, filing_plan: FilingPlan, receipt: CourtFilingReceipt
    ) -> bool:
        """Confirm clerk evidence is bound to the exact action and package."""

        return (
            action.connector == "court-filing"
            and action.receipt == receipt.simulation_receipt
            and receipt.package_sha256 == filing_plan.package_sha256
            and receipt.destination_sha256
            == filing_plan.destination_sha256
            and receipt.forum == filing_plan.forum
            and receipt.court == filing_plan.court.strip()
            and receipt.case_number == filing_plan.case_number.strip()
            and receipt.clerk_receipt_id.startswith("SIM-CLERK-")
        )
