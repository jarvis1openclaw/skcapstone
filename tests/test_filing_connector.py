from __future__ import annotations

import unittest

from sklegal_connectors import Action, ConnectorInvariantError
from sklegal_filing import CourtFilingConnector, FilingPlan


class Capability:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def verify(self, *, action: Action, capability_ref: str) -> bool:
        return self.allowed and capability_ref == "cap:court-filing"


def plan() -> FilingPlan:
    return FilingPlan(
        forum="federal",
        court="N.D. Illinois",
        case_number="1:26-cv-00001",
        documents={"complaint.pdf": b"synthetic complaint", "exhibit-a.pdf": b"exhibit"},
    )


class CourtFilingConnectorTests(unittest.TestCase):
    def test_simulation_reconciles_clerk_receipt(self) -> None:
        connector = CourtFilingConnector()
        action, receipt = connector.simulate(
            tenant_id="tenant-1",
            matter_id="matter-1",
            action_id="filing-1",
            artifact_id="draft-1",
            artifact_version=2,
            filing_plan=plan(),
            capability_ref="cap:court-filing",
            capability_verifier=Capability(),
        )
        self.assertEqual("receipt_verified", action.status)
        self.assertTrue(connector.reconcile_receipt(action, plan(), receipt))

    def test_forum_and_package_validation_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            FilingPlan("unsupported", "court", "case", {"complaint.pdf": b"x"})
        with self.assertRaises(ValueError):
            FilingPlan("state", "court", "case", {})
        with self.assertRaises(ValueError):
            FilingPlan("state", "court", "case", {"../secret": b"x"})

    def test_revoked_capability_blocks_before_dispatch(self) -> None:
        with self.assertRaises(ConnectorInvariantError):
            CourtFilingConnector().simulate(
                tenant_id="tenant-1",
                matter_id="matter-1",
                action_id="filing-2",
                artifact_id="draft-1",
                artifact_version=2,
                filing_plan=plan(),
                capability_ref="cap:court-filing",
                capability_verifier=Capability(allowed=False),
            )

    def test_duplicate_simulation_is_idempotent(self) -> None:
        connector = CourtFilingConnector()
        kwargs = dict(
            tenant_id="tenant-1",
            matter_id="matter-1",
            action_id="filing-3",
            artifact_id="draft-1",
            artifact_version=2,
            filing_plan=plan(),
            capability_ref="cap:court-filing",
            capability_verifier=Capability(),
        )
        first = connector.simulate(**kwargs)
        second = connector.simulate(**kwargs)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
