from __future__ import annotations

import hashlib
import unittest

from sklegal_connectors import ApprovalBinding, ConnectorInvariantError
from sklegal_email import EmailSimulation, EmailValidationError, ReceiptStatus

ARTIFACT = hashlib.sha256(b"email-artifact").hexdigest()


class Capability:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def verify(self, *, action, capability_ref: str) -> bool:
        return self.allowed and capability_ref == "cap:email"


def approved_email(simulation: EmailSimulation):
    draft = simulation.draft(
        tenant_id="tenant-1",
        matter_id="matter-1",
        action_id="email-1",
        artifact_id="work-product-1",
        artifact_version=2,
        artifact_sha256=ARTIFACT,
        recipients=("Counsel@Example.com",),
        subject="Review",
    )
    validated = simulation.validate(draft)
    return simulation.approve(
        validated,
        ApprovalBinding("approval-1", "work-product-1", 2, ARTIFACT),
    )


class EmailConnectorTests(unittest.TestCase):
    def test_simulation_reaches_receipt_verified_with_immutable_receipt(self) -> None:
        simulation = EmailSimulation()
        queued = simulation.queue(
            approved_email(simulation),
            capability_ref="cap:email",
            capability_verifier=Capability(),
        )
        dispatched, receipt = simulation.dispatch(queued)
        verified = simulation.reconcile(dispatched, receipt)
        self.assertEqual("receipt_verified", verified.action.status)
        self.assertEqual(1, simulation.dispatch_count)
        self.assertEqual(1, receipt.recipient_count)

    def test_validation_rejects_bad_and_duplicate_recipients(self) -> None:
        simulation = EmailSimulation()
        with self.assertRaises(EmailValidationError):
            simulation.draft(
                tenant_id="tenant-1",
                matter_id=None,
                action_id="email-1",
                artifact_id="work-product-1",
                artifact_version=1,
                artifact_sha256=ARTIFACT,
                recipients=("not-an-email",),
                subject="Review",
            )
        with self.assertRaises(EmailValidationError):
            simulation.draft(
                tenant_id="tenant-1",
                matter_id=None,
                action_id="email-1",
                artifact_id="work-product-1",
                artifact_version=1,
                artifact_sha256=ARTIFACT,
                recipients=("a@example.com", "A@example.com"),
                subject="Review",
            )

    def test_capability_revocation_fails_before_queue(self) -> None:
        simulation = EmailSimulation()
        with self.assertRaises(ConnectorInvariantError):
            simulation.queue(
                approved_email(simulation),
                capability_ref="cap:email",
                capability_verifier=Capability(allowed=False),
            )
        self.assertEqual(0, simulation.dispatch_count)

    def test_duplicate_dispatch_is_idempotent_and_bounce_fails_action(self) -> None:
        simulation = EmailSimulation()
        queued = simulation.queue(
            approved_email(simulation),
            capability_ref="cap:email",
            capability_verifier=Capability(),
        )
        dispatched, first = simulation.dispatch(queued)
        duplicate, second = simulation.dispatch(queued)
        self.assertEqual(first, second)
        self.assertEqual(dispatched.action, duplicate.action)
        bounced = simulation.reconcile(
            dispatched,
            second.__class__(second.simulation_receipt, ReceiptStatus.BOUNCED, 1),
        )
        self.assertEqual("failed", bounced.action.status)
        self.assertEqual(
            "email provider reported a bounce", bounced.action.failure_reason
        )


if __name__ == "__main__":
    unittest.main()
