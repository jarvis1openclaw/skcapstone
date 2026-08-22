from __future__ import annotations

import hashlib
import unittest

from sklegal_service import ServiceMailingConnector


class Verifier:
    def verify(self, *, action, capability_ref: str) -> bool:
        return capability_ref == "cap:service"


class ServiceConnectorTests(unittest.TestCase):
    def test_simulation_reaches_receipt_verified_with_tracking_and_affidavit(self):
        connector = ServiceMailingConnector()
        action, receipt = connector.simulate(
            tenant_id="tenant-1", matter_id="matter-1", action_id="action-1",
            artifact_id="filing-package", artifact_version=2,
            artifact="approved package", address="123 Main St, Chicago, IL",
            capability_ref="cap:service", capability_verifier=Verifier(),
        )
        self.assertEqual(action.status.value, "receipt_verified")
        self.assertTrue(receipt.tracking_number.startswith("SIM-"))
        self.assertEqual(len(receipt.affidavit_sha256), 64)
        self.assertTrue(receipt.address_verification.verified)

    def test_repeated_dispatch_is_idempotent(self):
        connector = ServiceMailingConnector()
        kwargs = dict(
            tenant_id="tenant-1", matter_id="matter-1", action_id="action-1",
            artifact_id="package", artifact_version=1, artifact="same",
            address="123 Main St, Chicago, IL", capability_ref="cap:service",
            capability_verifier=Verifier(),
        )
        _, first = connector.simulate(**kwargs)
        _, second = connector.simulate(**kwargs)
        self.assertEqual(first, second)

    def test_invalid_address_and_capability_fail_closed(self):
        connector = ServiceMailingConnector()
        kwargs = dict(
            tenant_id="tenant-1", matter_id="matter-1", action_id="action-1",
            artifact_id="package", artifact_version=1, artifact="same",
            address="unknown", capability_ref="cap:service", capability_verifier=Verifier(),
        )
        with self.assertRaises(ValueError):
            connector.simulate(**kwargs)
        kwargs["address"] = "123 Main St, Chicago, IL"
        kwargs["capability_ref"] = "forged"
        with self.assertRaises(ValueError):
            connector.simulate(**kwargs)
