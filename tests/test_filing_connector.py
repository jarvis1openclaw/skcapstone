"""SKL-S5-03B court filing connector qualification matrix.

Every case runs against the simulation registry only: no provider, court,
or clerk endpoint exists in the connector, receipts are marked simulated,
and clerk identifiers are SIM-prefixed.  Partial filing acceptance and
missing clerk receipts fail closed instead of reaching receipt_verified.
"""

from __future__ import annotations

import unittest

from sklegal_connectors import (
    ActionStatus,
    ApprovalBinding,
    ConnectorInvariantError,
)
from sklegal_filing import (
    CourtFilingConnector,
    FilingAction,
    FilingPlan,
    FilingProviderResponse,
    FilingReceiptStatus,
)


class Capability:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def verify(self, *, action, capability_ref: str) -> bool:
        return self.allowed and capability_ref == "cap:court-filing"


def plan() -> FilingPlan:
    return FilingPlan(
        forum="federal",
        court="N.D. Illinois",
        case_number="1:26-cv-00001",
        documents={
            "complaint.pdf": b"synthetic complaint",
            "exhibit-a.pdf": b"exhibit",
        },
    )


def approval_for(a_plan: FilingPlan, version: int = 2) -> ApprovalBinding:
    return ApprovalBinding("approval-1", "draft-1", version, a_plan.package_sha256)


def simulate_kwargs(a_plan: FilingPlan) -> dict:
    return dict(
        tenant_id="tenant-1",
        matter_id="matter-1",
        action_id="filing-1",
        artifact_id="draft-1",
        artifact_version=2,
        filing_plan=a_plan,
        approval=approval_for(a_plan),
        capability_ref="cap:court-filing",
        capability_verifier=Capability(),
    )


def queued(
    connector: CourtFilingConnector, a_plan: FilingPlan, action_id: str = "filing-1"
):
    filing = connector.begin(
        tenant_id="tenant-1",
        matter_id="matter-1",
        action_id=action_id,
        artifact_id="draft-1",
        artifact_version=2,
        filing_plan=a_plan,
    )
    return connector.queue(
        connector.approve(connector.validate(filing), approval_for(a_plan)),
        capability_ref="cap:court-filing",
        capability_verifier=Capability(),
    )


class ReceiptMatrixTests(unittest.TestCase):
    def test_full_acceptance_reaches_receipt_verified_in_simulation_only(self):
        connector = CourtFilingConnector()
        action, receipt = connector.simulate(**simulate_kwargs(plan()))
        self.assertIs(ActionStatus.RECEIPT_VERIFIED, action.status)
        self.assertIs(FilingReceiptStatus.ACCEPTED, receipt.status)
        self.assertIsNotNone(receipt.simulation_receipt)
        self.assertTrue(receipt.simulation_receipt.simulated)
        self.assertTrue(receipt.clerk_receipt_id.startswith("SIM-CLERK-"))
        self.assertEqual(plan().document_names, receipt.accepted_documents)
        self.assertEqual(frozenset(), receipt.rejected_documents)
        self.assertEqual(action.receipt, receipt.simulation_receipt)
        self.assertEqual(
            receipt.simulation_receipt.idempotency_key, action.idempotency_key
        )
        self.assertTrue(connector.reconcile_receipt(action, plan(), receipt))
        self.assertEqual(1, connector.dispatch_count)

    def test_partial_filing_fails_closed(self):
        connector = CourtFilingConnector()
        response = FilingProviderResponse(
            status=FilingReceiptStatus.PARTIAL,
            accepted_documents=frozenset({"complaint.pdf"}),
            rejected_documents=frozenset({"exhibit-a.pdf"}),
            clerk_detail="exhibit rejected: wrong pagination",
        )
        action, receipt = connector.simulate(
            **{**simulate_kwargs(plan()), "response": response}
        )
        self.assertIs(ActionStatus.FAILED, action.status)
        self.assertIsNone(action.receipt)
        self.assertIn("partial filing acceptance", action.failure_reason)
        self.assertIn("exhibit-a.pdf", action.failure_reason)
        self.assertIs(FilingReceiptStatus.PARTIAL, receipt.status)
        self.assertIsNotNone(receipt.simulation_receipt)
        self.assertEqual(frozenset({"complaint.pdf"}), receipt.accepted_documents)
        self.assertEqual(frozenset({"exhibit-a.pdf"}), receipt.rejected_documents)
        self.assertFalse(connector.reconcile_receipt(action, plan(), receipt))

    def test_missing_receipt_fails_closed_without_simulation_dispatch(self):
        connector = CourtFilingConnector()
        response = FilingProviderResponse(
            status=FilingReceiptStatus.MISSING_RECEIPT,
            clerk_detail="clerk portal timed out before confirmation",
        )
        action, receipt = connector.simulate(
            **{**simulate_kwargs(plan()), "response": response}
        )
        self.assertIs(ActionStatus.FAILED, action.status)
        self.assertIsNone(action.receipt)
        self.assertIn("no clerk receipt", action.failure_reason)
        self.assertIsNone(receipt.simulation_receipt)
        self.assertIsNone(receipt.clerk_receipt_id)
        self.assertEqual(0, connector.dispatch_count)

    def test_reconcile_rejects_receipt_bound_to_another_action(self):
        connector = CourtFilingConnector()
        first = connector.dispatch(queued(connector, plan(), "filing-1"))
        second = connector.dispatch(queued(connector, plan(), "filing-2"))
        with self.assertRaises(ConnectorInvariantError) as raised:
            connector.reconcile(first[0], second[1])
        self.assertIn("receipt does not bind the exact action", str(raised.exception))

    def test_reconcile_rejects_receipt_bound_to_another_plan(self):
        connector = CourtFilingConnector()
        other_plan = FilingPlan(
            "state", "Circuit Court of Cook County", "2026-MR-1", plan().documents
        )
        filed, receipt = connector.dispatch(queued(connector, other_plan))
        misbound = FilingAction(filed.action, plan())
        with self.assertRaises(ConnectorInvariantError) as raised:
            connector.reconcile(misbound, receipt)
        self.assertIn(
            "receipt does not bind the exact filing plan", str(raised.exception)
        )

    def test_provider_response_shape_fails_closed(self):
        with self.assertRaises(ConnectorInvariantError):
            FilingProviderResponse(
                FilingReceiptStatus.PARTIAL,
                accepted_documents=frozenset({"complaint.pdf"}),
                rejected_documents=frozenset({"complaint.pdf"}),
                clerk_detail="same document in both sets",
            )
        with self.assertRaises(ConnectorInvariantError):
            FilingProviderResponse(
                FilingReceiptStatus.PARTIAL,
                accepted_documents=frozenset({"complaint.pdf"}),
                rejected_documents=frozenset({"exhibit-a.pdf"}),
                clerk_detail="  ",
            )
        with self.assertRaises(ConnectorInvariantError):
            FilingProviderResponse(
                FilingReceiptStatus.MISSING_RECEIPT,
                accepted_documents=plan().document_names,
            )
        with self.assertRaises(ConnectorInvariantError):
            FilingProviderResponse(
                FilingReceiptStatus.ACCEPTED,
                rejected_documents=frozenset({"exhibit-a.pdf"}),
            )
        connector = CourtFilingConnector()
        unknown_document = FilingProviderResponse(
            FilingReceiptStatus.PARTIAL,
            accepted_documents=frozenset({"complaint.pdf"}),
            rejected_documents=frozenset({"missing-motion.pdf"}),
            clerk_detail="document outside the plan",
        )
        with self.assertRaises(ConnectorInvariantError):
            connector.dispatch(queued(connector, plan()), response=unknown_document)
        subset_acceptance = FilingProviderResponse(
            FilingReceiptStatus.ACCEPTED,
            accepted_documents=frozenset({"complaint.pdf"}),
        )
        with self.assertRaises(ConnectorInvariantError):
            connector.dispatch(queued(connector, plan()), response=subset_acceptance)
        with self.assertRaises(ConnectorInvariantError):
            FilingProviderResponse(
                FilingReceiptStatus.PARTIAL,
                accepted_documents=frozenset({"complaint.pdf"}),
                rejected_documents=frozenset(),
                clerk_detail="one document unaccounted for",
            )


class ApprovalMatrixTests(unittest.TestCase):
    def approved(self, connector: CourtFilingConnector, a_plan: FilingPlan):
        return connector.approve(
            connector.validate(
                connector.begin(
                    tenant_id="tenant-1",
                    matter_id="matter-1",
                    action_id="filing-1",
                    artifact_id="draft-1",
                    artifact_version=2,
                    filing_plan=a_plan,
                )
            ),
            approval_for(a_plan),
        )

    def test_wrong_artifact_identity_or_version_fails_closed(self):
        connector = CourtFilingConnector()
        a_plan = plan()
        filed = connector.validate(
            connector.begin(
                tenant_id="tenant-1",
                matter_id="matter-1",
                action_id="filing-1",
                artifact_id="draft-1",
                artifact_version=2,
                filing_plan=a_plan,
            )
        )
        with self.assertRaises(ConnectorInvariantError):
            connector.approve(
                filed,
                ApprovalBinding("a", "other-draft", 2, a_plan.package_sha256),
            )
        with self.assertRaises(ConnectorInvariantError):
            connector.approve(
                filed, ApprovalBinding("a", "draft-1", 1, a_plan.package_sha256)
            )
        with self.assertRaises(ConnectorInvariantError):
            connector.approve(filed, ApprovalBinding("a", "draft-1", 2, "b" * 64))

    def test_queue_requires_exact_prior_approval(self):
        connector = CourtFilingConnector()
        filed = connector.validate(
            connector.begin(
                tenant_id="tenant-1",
                matter_id="matter-1",
                action_id="filing-1",
                artifact_id="draft-1",
                artifact_version=2,
                filing_plan=plan(),
            )
        )
        with self.assertRaises(ConnectorInvariantError) as raised:
            connector.queue(
                filed,
                capability_ref="cap:court-filing",
                capability_verifier=Capability(),
            )
        self.assertIn("queue requires exact approval", str(raised.exception))

    def test_changed_package_after_approval_fails_validation(self):
        connector = CourtFilingConnector()
        approved = self.approved(connector, plan())
        changed = FilingPlan(
            "federal",
            "N.D. Illinois",
            "1:26-cv-00001",
            {"complaint.pdf": b"edited after approval"},
        )
        with self.assertRaises(ConnectorInvariantError) as raised:
            approved.action.validate(artifact_sha256=changed.package_sha256)
        self.assertIn("exact artifact digest", str(raised.exception))

    def test_revoked_capability_blocks_before_dispatch(self):
        with self.assertRaises(ConnectorInvariantError):
            CourtFilingConnector().simulate(
                **{
                    **simulate_kwargs(plan()),
                    "capability_verifier": Capability(allowed=False),
                }
            )


class DestinationMatrixTests(unittest.TestCase):
    def test_destination_change_rekeys_and_does_not_dedupe(self):
        connector = CourtFilingConnector()
        base = queued(connector, plan(), "filing-1")
        other_court = FilingPlan(
            "federal", "C.D. Illinois", "1:26-cv-00001", plan().documents
        )
        other = queued(connector, other_court, "filing-1")
        self.assertNotEqual(
            base.action.destination_sha256, other.action.destination_sha256
        )
        self.assertNotEqual(base.action.idempotency_key, other.action.idempotency_key)
        first = connector.dispatch(base)
        second = connector.dispatch(other)
        self.assertNotEqual(first[1].simulation_receipt, second[1].simulation_receipt)
        self.assertEqual(2, connector.dispatch_count)

    def test_drifted_destination_digest_fails_queue(self):
        connector = CourtFilingConnector()
        approved = connector.approve(
            connector.validate(
                connector.begin(
                    tenant_id="tenant-1",
                    matter_id="matter-1",
                    action_id="filing-1",
                    artifact_id="draft-1",
                    artifact_version=2,
                    filing_plan=plan(),
                )
            ),
            approval_for(plan()),
        )
        with self.assertRaises(ConnectorInvariantError) as raised:
            approved.action.queue(
                destination_sha256="f" * 64,
                capability_ref="cap:court-filing",
                capability_verifier=Capability(),
            )
        self.assertIn("destination digest changed", str(raised.exception))


class DuplicateMatrixTests(unittest.TestCase):
    def test_duplicate_dispatch_returns_the_same_immutable_receipt(self):
        connector = CourtFilingConnector()
        filing = queued(connector, plan())
        first, first_receipt = connector.dispatch(filing)
        second, second_receipt = connector.dispatch(filing)
        self.assertEqual(first, second)
        self.assertEqual(first_receipt, second_receipt)
        self.assertEqual(1, connector.dispatch_count)

    def test_same_action_with_changed_package_is_not_a_duplicate(self):
        connector = CourtFilingConnector()
        corrected = FilingPlan(
            "federal",
            "N.D. Illinois",
            "1:26-cv-00001",
            {
                "complaint.pdf": b"synthetic complaint",
                "exhibit-a.pdf": b"corrected exhibit",
            },
        )
        original = connector.dispatch(queued(connector, plan(), "filing-1"))
        resubmitted = connector.dispatch(queued(connector, corrected, "filing-1"))
        self.assertNotEqual(
            original[1].simulation_receipt, resubmitted[1].simulation_receipt
        )
        self.assertEqual(2, connector.dispatch_count)


class RecoveryAndAuditMatrixTests(unittest.TestCase):
    def test_failed_action_retries_and_completes_with_history(self):
        connector = CourtFilingConnector()
        filing = queued(connector, plan())
        dispatched, receipt = connector.dispatch(
            filing,
            response=FilingProviderResponse(
                FilingReceiptStatus.MISSING_RECEIPT, clerk_detail="portal timeout"
            ),
        )
        failed = connector.reconcile(dispatched, receipt)
        self.assertIs(ActionStatus.FAILED, failed.action.status)
        retried = type(failed)(failed.action.retry(), plan())
        redispatched, second_receipt = connector.dispatch(retried)
        verified = connector.reconcile(redispatched, second_receipt)
        self.assertIs(ActionStatus.RECEIPT_VERIFIED, verified.action.status)
        self.assertEqual(
            (
                ActionStatus.VALIDATED,
                ActionStatus.APPROVED,
                ActionStatus.QUEUED,
                ActionStatus.DISPATCHED,
                ActionStatus.FAILED,
                ActionStatus.QUEUED,
                ActionStatus.DISPATCHED,
                ActionStatus.RECEIPT_VERIFIED,
            ),
            verified.action.events,
        )
        self.assertIsNone(verified.action.failure_reason)

    def test_corrected_package_after_partial_filing_is_a_new_action(self):
        connector = CourtFilingConnector()
        partial_response = FilingProviderResponse(
            FilingReceiptStatus.PARTIAL,
            accepted_documents=frozenset({"complaint.pdf"}),
            rejected_documents=frozenset({"exhibit-a.pdf"}),
            clerk_detail="exhibit rejected: wrong pagination",
        )
        dispatched, receipt = connector.dispatch(
            queued(connector, plan(), "filing-1"), response=partial_response
        )
        failed = connector.reconcile(dispatched, receipt)
        self.assertIs(ActionStatus.FAILED, failed.action.status)
        self.assertIn("partial filing acceptance", failed.action.failure_reason)
        corrected = FilingPlan(
            "federal",
            "N.D. Illinois",
            "1:26-cv-00001",
            {
                "complaint.pdf": b"synthetic complaint",
                "exhibit-a.pdf": b"corrected exhibit pagination",
            },
        )
        action, corrected_receipt = connector.simulate(**simulate_kwargs(corrected))
        self.assertIs(ActionStatus.RECEIPT_VERIFIED, action.status)
        self.assertNotEqual(failed.action.idempotency_key, action.idempotency_key)
        self.assertNotEqual(
            receipt.simulation_receipt, corrected_receipt.simulation_receipt
        )

    def test_forum_and_package_validation_fail_closed(self):
        with self.assertRaises(ValueError):
            FilingPlan("unsupported", "court", "case", {"complaint.pdf": b"x"})
        with self.assertRaises(ValueError):
            FilingPlan("state", "court", "case", {})
        with self.assertRaises(ValueError):
            FilingPlan("state", "court", "case", {"../secret": b"x"})


if __name__ == "__main__":
    unittest.main()
