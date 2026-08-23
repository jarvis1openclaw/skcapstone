from __future__ import annotations

import hashlib
import unittest

from sklegal_connectors import (
    Action,
    ActionStatus,
    ApprovalBinding,
    ConnectorInvariantError,
    SimulationRegistry,
    canonical_idempotency_key,
    replay_action_audit,
)

ARTIFACT = hashlib.sha256(b"artifact").hexdigest()
DESTINATION = hashlib.sha256(b"destination").hexdigest()


class AllowCapability:
    def verify(self, *, action: Action, capability_ref: str) -> bool:
        return capability_ref == f"cap:{action.connector}"


def action() -> Action:
    return Action(
        tenant_id="tenant-1",
        matter_id="matter-1",
        action_id="action-1",
        connector="email",
        artifact_id="artifact-1",
        artifact_version=3,
        artifact_sha256=ARTIFACT,
        destination_sha256=DESTINATION,
    )


class ConnectorBaseTests(unittest.TestCase):
    def test_full_simulation_path_binds_approval_capability_and_receipt(self) -> None:
        current = action().validate(artifact_sha256=ARTIFACT)
        current = current.approve(
            ApprovalBinding("approval-1", "artifact-1", 3, ARTIFACT)
        )
        current = current.queue(
            destination_sha256=DESTINATION,
            capability_ref="cap:email",
            capability_verifier=AllowCapability(),
        )
        current = current.dispatch()
        receipt = SimulationRegistry().dispatch(current)
        current = current.verify_receipt(receipt)
        self.assertEqual(ActionStatus.RECEIPT_VERIFIED, current.status)
        self.assertEqual(5, len(current.events))
        replay = replay_action_audit(current)
        self.assertTrue(replay.simulation_receipt_verified)
        self.assertEqual(current.events, replay.events)

    def test_repeated_simulation_dispatch_returns_one_immutable_receipt(self) -> None:
        current = (
            action()
            .validate(artifact_sha256=ARTIFACT)
            .approve(ApprovalBinding("approval-1", "artifact-1", 3, ARTIFACT))
        )
        current = current.queue(
            destination_sha256=DESTINATION,
            capability_ref="cap:email",
            capability_verifier=AllowCapability(),
        ).dispatch()
        registry = SimulationRegistry()
        first = registry.dispatch(current)
        second = registry.dispatch(current)
        self.assertEqual(first, second)
        self.assertEqual(1, registry.receipt_count)

    def test_invalid_edges_and_bindings_fail_closed(self) -> None:
        with self.assertRaises(ConnectorInvariantError):
            action().dispatch()
        with self.assertRaises(ConnectorInvariantError):
            action().validate(artifact_sha256=DESTINATION)
        with self.assertRaises(ConnectorInvariantError):
            action().validate(artifact_sha256=ARTIFACT).approve(
                ApprovalBinding("approval-1", "artifact-1", 4, ARTIFACT)
            )

    def test_failed_action_retries_only_to_queue(self) -> None:
        current = (
            action()
            .validate(artifact_sha256=ARTIFACT)
            .approve(ApprovalBinding("approval-1", "artifact-1", 3, ARTIFACT))
        )
        current = current.queue(
            destination_sha256=DESTINATION,
            capability_ref="cap:email",
            capability_verifier=AllowCapability(),
        ).dispatch()
        current = current.fail(reason="provider timeout")
        with self.assertRaises(ConnectorInvariantError):
            current.verify_receipt(SimulationRegistry().dispatch(current))
        retried = current.retry()
        self.assertEqual(ActionStatus.QUEUED, retried.status)
        self.assertEqual(current.idempotency_key, retried.idempotency_key)

    def test_key_is_stable_and_scoped_to_immutable_inputs(self) -> None:
        expected = canonical_idempotency_key(
            tenant_id="tenant-1",
            matter_id="matter-1",
            action_id="action-1",
            destination_sha256=DESTINATION,
            artifact_sha256=ARTIFACT,
        )
        self.assertEqual(expected, action().idempotency_key)
        self.assertNotEqual(
            expected,
            canonical_idempotency_key(
                tenant_id="tenant-2",
                matter_id="matter-1",
                action_id="action-1",
                destination_sha256=DESTINATION,
                artifact_sha256=ARTIFACT,
            ),
        )


if __name__ == "__main__":
    unittest.main()
