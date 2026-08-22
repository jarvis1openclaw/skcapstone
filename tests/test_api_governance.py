"""HTTP boundary tests for the governed human policy-decision APIs."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx import Response
from sklegal_api.governance import (
    AuditLedgerGovernanceSink,
    build_governance_router,
)
from sklegal_audit import InMemoryAuditLedger, verify_event_chain
from sklegal_capauth import BoundaryScope, PrincipalContext, Purpose
from sklegal_policies import (
    GOVERNANCE_REQUIREMENTS,
    CapAuthCurrentStateVerifier,
    GovernanceAction,
    InMemoryAuthorizationUseBackend,
    InMemoryPolicyGovernanceStore,
    PolicyGovernanceService,
    UnavailableGovernanceAuditSink,
)

from tests.support.capauth_contract import (
    MATTER_ID,
    CapabilityTestRig,
    raw_leaf,
)

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


class GovernanceApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = CapabilityTestRig()
        self.ledger = InMemoryAuditLedger(clock=self.rig.clock)
        self.sink = AuditLedgerGovernanceSink(self.ledger)
        self.store = InMemoryPolicyGovernanceStore(audit=self.sink)
        self.service = PolicyGovernanceService(
            store=self.store,
            current_authorization=CapAuthCurrentStateVerifier(
                trusted_issuers=self.rig.trusted,
                principals=self.rig.principals,
                revocations=self.rig.revocations,
                uses=InMemoryAuthorizationUseBackend(),
            ),
            clock=self.rig.clock,
        )
        self.principal = self.rig.principal()
        self.other = self.rig.principal()

        def principal_resolver(_: Request) -> PrincipalContext:
            return self.principal

        def scope_resolver(request: Request) -> BoundaryScope:
            matter = UUID(str(request.path_params["matter_id"]))
            return BoundaryScope(
                tenant_id=self.principal.tenant_id,
                matter_id=matter,
                resource_id=str(matter),
            )

        app = FastAPI()
        app.include_router(
            build_governance_router(
                service=self.service,
                authorizer=self.rig.authorizer,
                principal_resolver=principal_resolver,
                scope_resolver=scope_resolver,
            )
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.rig.close()

    def token(self, action: GovernanceAction) -> str:
        requirement = GOVERNANCE_REQUIREMENTS[action]
        grant = self.rig.grant(
            capability=requirement.capability,
            purpose=requirement.purpose,
            target=requirement.target,
            tenant_id=self.principal.tenant_id,
            matter_id=MATTER_ID,
            resource_id=str(MATTER_ID),
        )
        return raw_leaf(self.rig.issue(self.principal, grant))

    def post(
        self,
        path: str,
        action: GovernanceAction,
        body: dict[str, object],
        *,
        key: str | None = None,
        token: str | None = None,
    ) -> Response:
        headers = {"Authorization": f"Bearer {token or self.token(action)}"}
        if key is not None:
            headers["Idempotency-Key"] = key
        return self.client.post(path, json=body, headers=headers)

    def open_wall_body(self) -> dict[str, object]:
        return {
            "wall_id": str(uuid4()),
            "name": f"wall-{uuid4().hex[:8]}",
            "effective_from": (T0 - timedelta(hours=1)).isoformat(),
        }

    def test_open_wall_commits_with_audit_outbox_and_receipt(self) -> None:
        response = self.post(
            f"/matters/{MATTER_ID}/ethical-walls",
            GovernanceAction.OPEN_ETHICAL_WALL,
            self.open_wall_body(),
            key=str(uuid4()),
        )
        self.assertEqual(200, response.status_code)
        receipt = response.json()
        self.assertEqual(1, receipt["policy_change_id"])
        self.assertEqual(1, receipt["record_version"])
        self.assertEqual(64, len(receipt["receipt_sha256"]))
        pending = self.ledger.pending_outbox(tenant_id=self.principal.tenant_id)
        self.assertEqual(1, len(pending))
        message = pending[0]
        events = self.ledger.replay(
            tenant_id=self.principal.tenant_id, run_id=message.run_id
        )
        self.assertTrue(verify_event_chain(events))
        self.assertEqual("policy.open_ethical_wall", events[0].action)
        self.assertEqual("human", events[0].boundary.value)
        first = self.ledger.deliver(
            outbox_id=message.outbox_id,
            destination="audit.local",
            handler=lambda _message: None,
        )
        second = self.ledger.deliver(
            outbox_id=message.outbox_id,
            destination="audit.local",
            handler=lambda _message: None,
        )
        self.assertTrue(first.first_delivery)
        self.assertFalse(second.first_delivery)
        self.assertEqual(first.idempotency_key, second.idempotency_key)

    def test_missing_and_malformed_idempotency_key_rejected(self) -> None:
        missing = self.post(
            f"/matters/{MATTER_ID}/ethical-walls",
            GovernanceAction.OPEN_ETHICAL_WALL,
            self.open_wall_body(),
        )
        self.assertEqual(400, missing.status_code)
        malformed = self.post(
            f"/matters/{MATTER_ID}/ethical-walls",
            GovernanceAction.OPEN_ETHICAL_WALL,
            self.open_wall_body(),
            key="not-a-uuid",
        )
        self.assertEqual(400, malformed.status_code)

    def test_invalid_command_body_rejected(self) -> None:
        response = self.post(
            f"/matters/{MATTER_ID}/ethical-walls",
            GovernanceAction.OPEN_ETHICAL_WALL,
            {"wall_id": str(uuid4()), "name": "x"},
            key=str(uuid4()),
        )
        self.assertEqual(422, response.status_code)

    def test_missing_and_replayed_credentials_denied(self) -> None:
        path = f"/matters/{MATTER_ID}/ethical-walls"
        missing = self.client.post(path, json=self.open_wall_body())
        self.assertEqual(403, missing.status_code)
        token = self.token(GovernanceAction.OPEN_ETHICAL_WALL)
        first = self.post(
            path,
            GovernanceAction.OPEN_ETHICAL_WALL,
            self.open_wall_body(),
            key=str(uuid4()),
            token=token,
        )
        self.assertEqual(200, first.status_code)
        replayed = self.post(
            path,
            GovernanceAction.OPEN_ETHICAL_WALL,
            self.open_wall_body(),
            key=str(uuid4()),
            token=token,
        )
        self.assertEqual(403, replayed.status_code)
        self.assertNotIn(token, replayed.text)

    def test_wrong_capability_token_denied_before_handler(self) -> None:
        requirement = GOVERNANCE_REQUIREMENTS[GovernanceAction.ISSUE_LEGAL_HOLD]
        grant = self.rig.grant(
            capability=requirement.capability,
            purpose=requirement.purpose,
            target=requirement.target,
            tenant_id=self.principal.tenant_id,
            matter_id=MATTER_ID,
            resource_id=str(MATTER_ID),
        )
        wrong = raw_leaf(self.rig.issue(self.principal, grant))
        response = self.post(
            f"/matters/{MATTER_ID}/ethical-walls",
            GovernanceAction.OPEN_ETHICAL_WALL,
            self.open_wall_body(),
            key=str(uuid4()),
            token=wrong,
        )
        self.assertEqual(403, response.status_code)
        detail = response.json()["detail"]
        self.assertEqual("capability_denied", detail["code"])

    def test_grant_revoke_and_stale_version_conflict_over_http(self) -> None:
        granted = self.post(
            f"/matters/{MATTER_ID}/protected-access-grants",
            GovernanceAction.GRANT_PROTECTED_ACCESS,
            {
                "grant_id": str(uuid4()),
                "grantee_principal_id": str(self.other.principal_id),
                "access_level": "privileged_work_product",
                "purpose": Purpose.EVIDENCE_REVIEW.value,
                "effective_from": (T0 - timedelta(hours=1)).isoformat(),
            },
            key=str(uuid4()),
        )
        self.assertEqual(200, granted.status_code)
        grant_id = granted.json()["record_id"]

        stale = self.post(
            f"/matters/{MATTER_ID}/protected-access-grants/{grant_id}/revoke",
            GovernanceAction.REVOKE_PROTECTED_ACCESS,
            {
                "grant_id": grant_id,
                "expected_version": 5,
                "revoked_at": T0.isoformat(),
            },
            key=str(uuid4()),
        )
        self.assertEqual(409, stale.status_code)
        detail = stale.json()["detail"]
        self.assertEqual("policy_governance_conflict", detail["code"])
        self.assertEqual("version_conflict", detail["reason"])

        revoked = self.post(
            f"/matters/{MATTER_ID}/protected-access-grants/{grant_id}/revoke",
            GovernanceAction.REVOKE_PROTECTED_ACCESS,
            {
                "grant_id": grant_id,
                "expected_version": 1,
                "revoked_at": T0.isoformat(),
            },
            key=str(uuid4()),
        )
        self.assertEqual(200, revoked.status_code)
        self.assertEqual(2, revoked.json()["record_version"])

    def test_separation_of_duties_maps_to_forbidden(self) -> None:
        response = self.post(
            f"/matters/{MATTER_ID}/protected-access-grants",
            GovernanceAction.GRANT_PROTECTED_ACCESS,
            {
                "grant_id": str(uuid4()),
                "grantee_principal_id": str(self.principal.principal_id),
                "access_level": "highly_restricted",
                "purpose": Purpose.EVIDENCE_REVIEW.value,
                "effective_from": (T0 - timedelta(hours=1)).isoformat(),
            },
            key=str(uuid4()),
        )
        self.assertEqual(403, response.status_code)
        detail = response.json()["detail"]
        self.assertEqual("policy_governance_denied", detail["code"])
        self.assertEqual("separation_of_duties", detail["reason"])

    def test_legal_hold_release_separation_of_duties_over_http(self) -> None:
        issued = self.post(
            f"/matters/{MATTER_ID}/legal-holds",
            GovernanceAction.ISSUE_LEGAL_HOLD,
            {
                "legal_hold_id": str(uuid4()),
                "scope": "matter",
                "effective_from": (T0 - timedelta(days=1)).isoformat(),
            },
            key=str(uuid4()),
        )
        self.assertEqual(200, issued.status_code)
        hold_id = issued.json()["record_id"]
        released = self.post(
            f"/matters/{MATTER_ID}/legal-holds/{hold_id}/release",
            GovernanceAction.RELEASE_LEGAL_HOLD,
            {
                "release_id": str(uuid4()),
                "legal_hold_id": hold_id,
                "released_at": T0.isoformat(),
            },
            key=str(uuid4()),
        )
        self.assertEqual(403, released.status_code)
        detail = released.json()["detail"]
        self.assertEqual("separation_of_duties", detail["reason"])

    def test_retention_and_labels_over_http(self) -> None:
        retention = self.post(
            f"/matters/{MATTER_ID}/retention-policies",
            GovernanceAction.SET_RETENTION_POLICY,
            {
                "retention_policy_id": str(uuid4()),
                "retain_for_days": 365,
                "effective_from": (T0 - timedelta(days=30)).isoformat(),
            },
            key=str(uuid4()),
        )
        self.assertEqual(200, retention.status_code)
        label = self.post(
            f"/matters/{MATTER_ID}/protection-labels",
            GovernanceAction.SET_PROTECTION_LABEL,
            {
                "label_id": str(uuid4()),
                "label_family": "privilege",
                "label_code": "attorney_client",
                "material_id": str(uuid4()),
                "material_version": 1,
                "active": True,
            },
            key=str(uuid4()),
        )
        self.assertEqual(200, label.status_code)

    def test_store_outage_maps_to_unavailable(self) -> None:
        self.store._audit = UnavailableGovernanceAuditSink()  # noqa: SLF001
        response = self.post(
            f"/matters/{MATTER_ID}/ethical-walls",
            GovernanceAction.OPEN_ETHICAL_WALL,
            self.open_wall_body(),
            key=str(uuid4()),
        )
        self.assertEqual(503, response.status_code)
        detail = response.json()["detail"]
        self.assertEqual("policy_governance_unavailable", detail["code"])


if __name__ == "__main__":
    unittest.main()
