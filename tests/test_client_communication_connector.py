"""SKL-S5-03C client Communication and cross-connector qualification."""

from __future__ import annotations

import ast
import unittest
from dataclasses import replace
from pathlib import Path

from sklegal_client_communication import (
    ClientCommunicationConnector,
    CommunicationAction,
    CommunicationProviderResponse,
    CommunicationReceipt,
    CommunicationReceiptStatus,
)
from sklegal_connectors import (
    ActionStatus,
    ApprovalBinding,
    ConnectorInvariantError,
    SimulationRegistry,
    replay_action_audit,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BANNED_MODULES = {
    "aiohttp",
    "ftplib",
    "http",
    "httpx",
    "imaplib",
    "poplib",
    "requests",
    "smtplib",
    "socket",
    "ssl",
    "subprocess",
    "telnetlib",
    "urllib",
    "urllib3",
}
BANNED_CALLS = {"__import__", "compile", "eval", "exec", "input", "open"}
BANNED_ATTR_CALLS = {("os", "popen"), ("os", "system")}
EXTERNAL_ACTION_PACKAGES = (
    "base",
    "calendar",
    "client_communication",
    "email",
    "filing",
    "service",
)


class Verifier:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def verify(self, *, action, capability_ref: str) -> bool:
        return self.allowed and capability_ref == "cap:client-communication"


def begun(
    connector: ClientCommunicationConnector,
    *,
    action_id: str = "message-1",
    message: str = "Approved status update",
    recipient: str = "Client@Example.test",
    privilege_label: str = "attorney-client",
) -> CommunicationAction:
    return connector.begin(
        tenant_id="tenant-1",
        matter_id="matter-1",
        action_id=action_id,
        artifact_id="message-draft",
        artifact_version=3,
        message=message,
        recipient=recipient,
        privilege_label=privilege_label,
    )


def approved(
    connector: ClientCommunicationConnector, **overrides: str
) -> CommunicationAction:
    communication = begun(connector, **overrides)
    validated = connector.validate(communication)
    return connector.approve(
        validated,
        ApprovalBinding(
            "approval-1",
            communication.action.artifact_id,
            communication.action.artifact_version,
            communication.action.artifact_sha256,
        ),
    )


def queued(
    connector: ClientCommunicationConnector, **overrides: str
) -> CommunicationAction:
    return connector.queue(
        approved(connector, **overrides),
        capability_ref="cap:client-communication",
        capability_verifier=Verifier(),
    )


class ClientCommunicationReceiptMatrixTests(unittest.TestCase):
    def test_delivery_reaches_receipt_verified_in_simulation_only(self) -> None:
        connector = ClientCommunicationConnector()
        communication = begun(connector)
        action, receipt = connector.simulate(
            tenant_id=communication.action.tenant_id,
            matter_id=communication.action.matter_id or "",
            action_id=communication.action.action_id,
            artifact_id=communication.action.artifact_id,
            artifact_version=communication.action.artifact_version,
            message="Approved status update",
            recipient="Client@Example.test",
            privilege_label="attorney-client",
            approval=ApprovalBinding(
                "approval-1", "message-draft", 3, communication.action.artifact_sha256
            ),
            capability_ref="cap:client-communication",
            capability_verifier=Verifier(),
        )

        self.assertIs(ActionStatus.RECEIPT_VERIFIED, action.status)
        self.assertIs(CommunicationReceiptStatus.DELIVERED, receipt.status)
        self.assertTrue(receipt.simulated)
        self.assertIsNotNone(receipt.delivery_sha256)
        self.assertEqual("attorney-client", receipt.privilege_label)
        self.assertEqual(action.receipt, receipt.simulation_receipt)
        self.assertEqual(1, connector.dispatch_count)

    def test_missing_receipt_fails_closed_without_simulation_dispatch(self) -> None:
        connector = ClientCommunicationConnector()
        dispatched, receipt = connector.dispatch(
            queued(connector),
            response=CommunicationProviderResponse(
                CommunicationReceiptStatus.MISSING_RECEIPT,
                "provider timed out before delivery confirmation",
            ),
        )
        failed = connector.reconcile(dispatched, receipt)

        self.assertIs(ActionStatus.FAILED, failed.action.status)
        self.assertIsNone(failed.action.receipt)
        self.assertIn("no delivery receipt", failed.action.failure_reason or "")
        self.assertIsNone(receipt.simulation_receipt)
        self.assertFalse(receipt.simulated)
        self.assertEqual(0, connector.dispatch_count)

    def test_missing_receipt_requires_provider_detail(self) -> None:
        with self.assertRaises(ConnectorInvariantError):
            CommunicationProviderResponse(CommunicationReceiptStatus.MISSING_RECEIPT)

    def test_foreign_or_changed_receipt_fails_closed(self) -> None:
        connector = ClientCommunicationConnector()
        first, first_receipt = connector.dispatch(
            queued(connector, action_id="message-1")
        )
        _, second_receipt = connector.dispatch(queued(connector, action_id="message-2"))

        with self.assertRaises(ConnectorInvariantError):
            connector.reconcile(first, second_receipt)
        with self.assertRaises(ConnectorInvariantError):
            connector.reconcile(
                first,
                replace(first_receipt, privilege_label="unlabeled"),
            )
        with self.assertRaises(ConnectorInvariantError):
            connector.reconcile(
                first,
                CommunicationReceipt(
                    None,
                    CommunicationReceiptStatus.DELIVERED,
                    first_receipt.thread_id,
                    None,
                    first_receipt.privilege_label,
                    "claimed delivery without evidence",
                ),
            )


class ClientCommunicationGateMatrixTests(unittest.TestCase):
    def test_exact_version_approval_is_required(self) -> None:
        connector = ClientCommunicationConnector()
        draft = begun(connector)
        validated = connector.validate(draft)

        for binding in (
            ApprovalBinding("approval-1", "other", 3, draft.action.artifact_sha256),
            ApprovalBinding(
                "approval-1", "message-draft", 2, draft.action.artifact_sha256
            ),
            ApprovalBinding("approval-1", "message-draft", 3, "f" * 64),
        ):
            with self.subTest(binding=binding):
                with self.assertRaises(ConnectorInvariantError):
                    connector.approve(validated, binding)

        with self.assertRaises(ConnectorInvariantError):
            connector.queue(
                validated,
                capability_ref="cap:client-communication",
                capability_verifier=Verifier(),
            )

    def test_changed_message_invalidates_approval(self) -> None:
        connector = ClientCommunicationConnector()
        original = begun(connector, message="Original")
        changed = begun(connector, message="Changed")
        with self.assertRaises(ConnectorInvariantError):
            connector.approve(
                connector.validate(changed),
                ApprovalBinding(
                    "approval-1", "message-draft", 3, original.action.artifact_sha256
                ),
            )

    def test_invalid_scope_recipient_label_and_message_fail_before_dispatch(
        self,
    ) -> None:
        connector = ClientCommunicationConnector()
        base = dict(
            tenant_id="tenant-1",
            matter_id="matter-1",
            action_id="message-1",
            artifact_id="message-draft",
            artifact_version=3,
            message="Approved status update",
            recipient="client@example.test",
            privilege_label="attorney-client",
        )
        for changes in (
            {"matter_id": ""},
            {"recipient": "not-an-address"},
            {"message": "  "},
            {"privilege_label": "  "},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ConnectorInvariantError):
                    connector.begin(**{**base, **changes})
        self.assertEqual(0, connector.dispatch_count)

    def test_revoked_capability_and_unqueued_dispatch_fail_closed(self) -> None:
        connector = ClientCommunicationConnector()
        with self.assertRaises(ConnectorInvariantError):
            connector.queue(
                approved(connector),
                capability_ref="cap:client-communication",
                capability_verifier=Verifier(allowed=False),
            )
        with self.assertRaises(ConnectorInvariantError):
            connector.dispatch(approved(connector))
        self.assertEqual(0, connector.dispatch_count)

    def test_destination_change_rekeys_and_digest_drift_fails(self) -> None:
        connector = ClientCommunicationConnector()
        first = approved(connector, recipient="client@example.test")
        other = approved(connector, recipient="other@example.test")

        self.assertNotEqual(
            first.action.destination_sha256, other.action.destination_sha256
        )
        self.assertNotEqual(first.action.idempotency_key, other.action.idempotency_key)
        with self.assertRaises(ConnectorInvariantError):
            first.action.queue(
                destination_sha256=other.action.destination_sha256,
                capability_ref="cap:client-communication",
                capability_verifier=Verifier(),
            )

    def test_recipient_normalization_deduplicates_exact_action(self) -> None:
        registry = SimulationRegistry()
        connector = ClientCommunicationConnector(registry=registry)
        first = connector.dispatch(queued(connector, recipient=" Client@Example.test "))
        second = connector.dispatch(queued(connector, recipient="client@example.test"))

        self.assertEqual(first, second)
        self.assertEqual(1, registry.receipt_count)


class RecoveryAuditAndBoundaryTests(unittest.TestCase):
    def test_failed_action_retries_to_verified_with_complete_audit_replay(self) -> None:
        connector = ClientCommunicationConnector()
        dispatched, missing = connector.dispatch(
            queued(connector),
            response=CommunicationProviderResponse(
                CommunicationReceiptStatus.MISSING_RECEIPT,
                "provider confirmation unavailable",
            ),
        )
        failed = connector.reconcile(dispatched, missing)
        retried = CommunicationAction(
            failed.action.retry(),
            failed.recipient,
            failed.privilege_label,
            failed.thread_id,
        )
        redispatched, delivered = connector.dispatch(retried)
        verified = connector.reconcile(redispatched, delivered)

        expected = (
            ActionStatus.VALIDATED,
            ActionStatus.APPROVED,
            ActionStatus.QUEUED,
            ActionStatus.DISPATCHED,
            ActionStatus.FAILED,
            ActionStatus.QUEUED,
            ActionStatus.DISPATCHED,
            ActionStatus.RECEIPT_VERIFIED,
        )
        self.assertEqual(expected, verified.action.events)
        replay = replay_action_audit(verified.action)
        self.assertEqual(expected, replay.events)
        self.assertIs(ActionStatus.RECEIPT_VERIFIED, replay.final_status)
        self.assertTrue(replay.simulation_receipt_verified)
        self.assertEqual(64, len(replay.replay_sha256))
        self.assertEqual(replay, replay_action_audit(verified.action))

    def test_audit_replay_rejects_tampered_history_and_live_receipt_marker(
        self,
    ) -> None:
        connector = ClientCommunicationConnector()
        dispatched, receipt = connector.dispatch(queued(connector))
        verified = connector.reconcile(dispatched, receipt).action

        with self.assertRaises(ConnectorInvariantError):
            replay_action_audit(
                replace(
                    verified,
                    events=(ActionStatus.VALIDATED, ActionStatus.RECEIPT_VERIFIED),
                )
            )
        self.assertIsNotNone(verified.receipt)
        with self.assertRaises(ConnectorInvariantError):
            replay_action_audit(
                replace(
                    verified,
                    receipt=replace(verified.receipt, simulated=False),
                )
            )

    def test_every_external_action_connector_has_no_live_transport_surface(
        self,
    ) -> None:
        for package in EXTERNAL_ACTION_PACKAGES:
            root = REPO_ROOT / "packages" / "connectors" / package / "src"
            self.assertTrue(root.is_dir(), str(root))
            for path in sorted(root.rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertNotIn(alias.name.split(".")[0], BANNED_MODULES)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        self.assertNotIn(node.module.split(".")[0], BANNED_MODULES)
                    elif isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Name):
                            self.assertNotIn(node.func.id, BANNED_CALLS)
                        elif isinstance(node.func, ast.Attribute) and isinstance(
                            node.func.value, ast.Name
                        ):
                            self.assertNotIn(
                                (node.func.value.id, node.func.attr),
                                BANNED_ATTR_CALLS,
                            )


if __name__ == "__main__":
    unittest.main()
