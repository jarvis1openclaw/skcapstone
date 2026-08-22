from __future__ import annotations

import unittest

from sklegal_client_communication import ClientCommunicationConnector


class Verifier:
    def verify(self, *, action, capability_ref: str) -> bool:
        return capability_ref == "cap:client-communication"


class ClientCommunicationTests(unittest.TestCase):
    def _kwargs(self):
        return dict(
            tenant_id="tenant-1", matter_id="matter-1", action_id="message-1",
            artifact_id="message-draft", artifact_version=3,
            message="Approved status update", recipient="client@example.test",
            privilege_label="attorney-client", capability_ref="cap:client-communication",
            capability_verifier=Verifier(),
        )

    def test_simulation_is_matter_scoped_labeled_and_receipt_verified(self):
        action, receipt = ClientCommunicationConnector().simulate(**self._kwargs())
        self.assertEqual(action.status.value, "receipt_verified")
        self.assertEqual(action.matter_id, "matter-1")
        self.assertEqual(receipt.privilege_label, "attorney-client")
        self.assertTrue(receipt.simulated)
        self.assertEqual(len(receipt.delivery_sha256), 64)

    def test_repeated_message_is_idempotent(self):
        connector = ClientCommunicationConnector()
        _, first = connector.simulate(**self._kwargs())
        _, second = connector.simulate(**self._kwargs())
        self.assertEqual(first, second)

    def test_scope_and_capability_fail_closed(self):
        connector = ClientCommunicationConnector()
        kwargs = self._kwargs()
        kwargs["matter_id"] = ""
        with self.assertRaises(ValueError):
            connector.simulate(**kwargs)
        kwargs["matter_id"] = "matter-1"
        kwargs["capability_ref"] = "forged"
        with self.assertRaises(ValueError):
            connector.simulate(**kwargs)
