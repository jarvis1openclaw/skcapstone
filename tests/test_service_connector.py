"""SKL-S5-03B service and mailing connector qualification matrix.

Every case runs against the simulation registry only: no carrier,
process server, or postal endpoint exists in the connector, tracking
numbers are SIM-prefixed, and a carrier that returns no delivery receipt
fails closed instead of reaching receipt_verified.
"""

from __future__ import annotations

import hashlib
import unittest

from sklegal_connectors import (
    ActionStatus,
    ApprovalBinding,
    ConnectorInvariantError,
)
from sklegal_service import (
    ServiceAction,
    ServiceMailingConnector,
    ServiceProviderResponse,
    ServiceReceipt,
    ServiceReceiptStatus,
)


class Verifier:
    def __init__(self, allowed: bool = True):
        self.allowed = allowed

    def verify(self, *, action, capability_ref: str) -> bool:
        return self.allowed and capability_ref == "cap:service"


ADDRESS = "123 Main St, Chicago, IL"


def simulate_kwargs(**overrides) -> dict:
    kwargs = dict(
        tenant_id="tenant-1",
        matter_id="matter-1",
        action_id="action-1",
        artifact_id="filing-package",
        artifact_version=2,
        artifact="approved package",
        address=ADDRESS,
        approval=ApprovalBinding(
            "approval-1",
            "filing-package",
            2,
            _artifact_digest("approved package"),
        ),
        capability_ref="cap:service",
        capability_verifier=Verifier(),
    )
    kwargs.update(overrides)
    return kwargs


def _artifact_digest(artifact: str) -> str:
    return hashlib.sha256(artifact.encode("utf-8")).hexdigest()


def approved(connector: ServiceMailingConnector, **overrides) -> ServiceAction:
    kwargs = simulate_kwargs(**overrides)
    artifact = kwargs["artifact"]
    action = connector.begin(
        tenant_id=kwargs["tenant_id"],
        matter_id=kwargs["matter_id"],
        action_id=kwargs["action_id"],
        artifact_id=kwargs["artifact_id"],
        artifact_version=kwargs["artifact_version"],
        artifact=artifact,
        address=kwargs["address"],
    )
    binding = ApprovalBinding(
        kwargs["approval"].approval_id,
        kwargs["artifact_id"],
        kwargs["artifact_version"],
        _artifact_digest(artifact),
    )
    return connector.approve(connector.validate(action), binding)


def queued(connector: ServiceMailingConnector, **overrides) -> ServiceAction:
    kwargs = simulate_kwargs(**overrides)
    return connector.queue(
        approved(connector, **overrides),
        capability_ref=kwargs["capability_ref"],
        capability_verifier=kwargs["capability_verifier"],
    )


class ReceiptMatrixTests(unittest.TestCase):
    def test_delivery_reaches_receipt_verified_in_simulation_only(self):
        connector = ServiceMailingConnector()
        action, receipt = connector.simulate(**simulate_kwargs())
        self.assertIs(ActionStatus.RECEIPT_VERIFIED, action.status)
        self.assertIs(ServiceReceiptStatus.DELIVERED, receipt.status)
        self.assertIsNotNone(receipt.simulation_receipt)
        self.assertTrue(receipt.simulation_receipt.simulated)
        self.assertTrue(receipt.tracking_number.startswith("SIM-"))
        self.assertEqual(len(receipt.affidavit_sha256), 64)
        self.assertTrue(receipt.address_verification.verified)
        self.assertEqual(ADDRESS, receipt.address_verification.address)
        self.assertEqual(action.receipt, receipt.simulation_receipt)
        self.assertEqual(
            receipt.simulation_receipt.idempotency_key, action.idempotency_key
        )
        self.assertEqual(1, connector.dispatch_count)

    def test_missing_receipt_fails_closed_without_simulation_dispatch(self):
        connector = ServiceMailingConnector()
        response = ServiceProviderResponse(
            ServiceReceiptStatus.MISSING_RECEIPT,
            carrier_detail="carrier scan never confirmed delivery",
        )
        action, receipt = connector.simulate(
            **{**simulate_kwargs(), "response": response}
        )
        self.assertIs(ActionStatus.FAILED, action.status)
        self.assertIsNone(action.receipt)
        self.assertIn("no delivery receipt", action.failure_reason)
        self.assertIn("carrier scan never confirmed", action.failure_reason)
        self.assertIs(ServiceReceiptStatus.MISSING_RECEIPT, receipt.status)
        self.assertIsNone(receipt.simulation_receipt)
        self.assertIsNone(receipt.tracking_number)
        self.assertIsNone(receipt.affidavit_sha256)
        self.assertIsNone(receipt.address_verification)
        self.assertEqual(0, connector.dispatch_count)

    def test_missing_receipt_response_requires_carrier_detail(self):
        with self.assertRaises(ConnectorInvariantError):
            ServiceProviderResponse(ServiceReceiptStatus.MISSING_RECEIPT)

    def test_reconcile_rejects_receipt_bound_to_another_action(self):
        connector = ServiceMailingConnector()
        first = connector.dispatch(queued(connector, action_id="action-1"))
        second = connector.dispatch(queued(connector, action_id="action-2"))
        with self.assertRaises(ConnectorInvariantError) as raised:
            connector.reconcile(first[0], second[1])
        self.assertIn("exact action", str(raised.exception))

    def test_reconcile_rejects_receipt_without_address_binding(self):
        connector = ServiceMailingConnector()
        dispatched, receipt = connector.dispatch(queued(connector))
        unbound = ServiceReceipt(
            simulation_receipt=receipt.simulation_receipt,
            status=receipt.status,
            tracking_number=receipt.tracking_number,
            affidavit_sha256=receipt.affidavit_sha256,
            address_verification=None,
            carrier_detail=receipt.carrier_detail,
        )
        with self.assertRaises(ConnectorInvariantError) as raised:
            connector.reconcile(dispatched, unbound)
        self.assertIn("verified service destination", str(raised.exception))


class ApprovalMatrixTests(unittest.TestCase):
    def test_wrong_artifact_identity_or_version_fails_closed(self):
        connector = ServiceMailingConnector()
        filed = connector.validate(
            connector.begin(
                tenant_id="tenant-1",
                matter_id="matter-1",
                action_id="action-1",
                artifact_id="filing-package",
                artifact_version=2,
                artifact="approved package",
                address=ADDRESS,
            )
        )
        with self.assertRaises(ConnectorInvariantError):
            connector.approve(
                filed,
                ApprovalBinding(
                    "a", "other-package", 2, _artifact_digest("approved package")
                ),
            )
        with self.assertRaises(ConnectorInvariantError):
            connector.approve(
                filed,
                ApprovalBinding(
                    "a", "filing-package", 1, _artifact_digest("approved package")
                ),
            )
        with self.assertRaises(ConnectorInvariantError):
            connector.approve(
                filed, ApprovalBinding("a", "filing-package", 2, "b" * 64)
            )

    def test_queue_requires_exact_prior_approval(self):
        connector = ServiceMailingConnector()
        filed = connector.validate(
            connector.begin(
                tenant_id="tenant-1",
                matter_id="matter-1",
                action_id="action-1",
                artifact_id="filing-package",
                artifact_version=2,
                artifact="approved package",
                address=ADDRESS,
            )
        )
        with self.assertRaises(ConnectorInvariantError) as raised:
            connector.queue(
                filed, capability_ref="cap:service", capability_verifier=Verifier()
            )
        self.assertIn("queue requires exact approval", str(raised.exception))

    def test_changed_artifact_after_approval_fails_validation(self):
        connector = ServiceMailingConnector()
        filed = approved(connector)
        with self.assertRaises(ConnectorInvariantError) as raised:
            filed.action.validate(artifact_sha256=_artifact_digest("edited text"))
        self.assertIn("exact artifact digest", str(raised.exception))

    def test_revoked_capability_blocks_before_dispatch(self):
        connector = ServiceMailingConnector()
        with self.assertRaises(ConnectorInvariantError):
            connector.simulate(
                **{
                    **simulate_kwargs(),
                    "capability_verifier": Verifier(allowed=False),
                }
            )
        self.assertEqual(0, connector.dispatch_count)


class DestinationMatrixTests(unittest.TestCase):
    def test_address_change_rekeys_and_does_not_dedupe(self):
        connector = ServiceMailingConnector()
        base = queued(connector, address=ADDRESS)
        other = queued(connector, address="456 Oak Ave, Springfield, IL 62701")
        self.assertNotEqual(
            base.action.destination_sha256, other.action.destination_sha256
        )
        self.assertNotEqual(base.action.idempotency_key, other.action.idempotency_key)
        first = connector.dispatch(base)
        second = connector.dispatch(other)
        self.assertNotEqual(first[1].simulation_receipt, second[1].simulation_receipt)
        self.assertEqual(2, connector.dispatch_count)

    def test_whitespace_and_case_variations_of_one_address_dedupe(self):
        connector = ServiceMailingConnector()
        base = connector.dispatch(queued(connector, address="123 Main St, Chicago, IL"))
        variant = connector.dispatch(
            queued(connector, address="  123   Main St,  Chicago,  IL  ")
        )
        self.assertEqual(base[1].simulation_receipt, variant[1].simulation_receipt)
        self.assertEqual(1, connector.dispatch_count)

    def test_invalid_address_fails_closed_before_any_dispatch(self):
        connector = ServiceMailingConnector()
        with self.assertRaises(ValueError):
            connector.simulate(**{**simulate_kwargs(), "address": "unknown"})
        with self.assertRaises(ValueError):
            connector.simulate(**{**simulate_kwargs(), "address": "   "})
        self.assertEqual(0, connector.dispatch_count)


class DuplicateMatrixTests(unittest.TestCase):
    def test_duplicate_dispatch_returns_the_same_immutable_receipt(self):
        connector = ServiceMailingConnector()
        service = queued(connector)
        first, first_receipt = connector.dispatch(service)
        second, second_receipt = connector.dispatch(service)
        self.assertEqual(first, second)
        self.assertEqual(first_receipt, second_receipt)
        self.assertEqual(1, connector.dispatch_count)

    def test_same_action_with_changed_artifact_is_not_a_duplicate(self):
        connector = ServiceMailingConnector()
        original = connector.dispatch(
            queued(connector, artifact="approved package", artifact_version=1)
        )
        resubmitted = connector.dispatch(
            queued(connector, artifact="revised package", artifact_version=1)
        )
        self.assertNotEqual(
            original[1].simulation_receipt, resubmitted[1].simulation_receipt
        )
        self.assertEqual(2, connector.dispatch_count)


class RecoveryAndAuditMatrixTests(unittest.TestCase):
    def test_failed_action_retries_and_completes_with_history(self):
        connector = ServiceMailingConnector()
        service = queued(connector)
        dispatched, receipt = connector.dispatch(
            service,
            response=ServiceProviderResponse(
                ServiceReceiptStatus.MISSING_RECEIPT,
                carrier_detail="carrier scan never confirmed delivery",
            ),
        )
        failed = connector.reconcile(dispatched, receipt)
        self.assertIs(ActionStatus.FAILED, failed.action.status)
        retried = type(failed)(failed.action.retry(), failed.address)
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


if __name__ == "__main__":
    unittest.main()
