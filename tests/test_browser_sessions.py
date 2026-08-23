"""Live API session tests using public-synthetic CapAuth fixtures only."""

from __future__ import annotations

import unittest
from typing import cast
from uuid import UUID

from fastapi import Request
from fastapi.testclient import TestClient
from sklegal_api.app import REQUIRED_DEPENDENCIES, DependencyProbe, MvpApiComposition, create_mvp_app
from sklegal_api.browser_sessions import (
    PUBLIC_SYNTHETIC_CREDENTIAL_REFERENCE,
    BrowserTenant,
    InMemoryPublicSyntheticSessionBackend,
)
from sklegal_api.claims import InMemoryClaimLedgerStore
from sklegal_api.corpus import InMemoryCorpusResearchStore
from sklegal_api.workspace import ClientSummaryRead, InMemoryWorkspaceReadStore
from sklegal_capauth import BoundaryScope, Capability, PrincipalContext, Purpose
from sklegal_policies import PolicyGovernanceService

from tests.support.capauth_contract import TENANT_ID, CapabilityTestRig, raw_leaf

CLIENT_ID = UUID("40000000-0000-4000-8000-000000000101")
WRONG_TENANT_ID = UUID("10000000-0000-4000-8000-000000000099")


class BrowserSessionIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = CapabilityTestRig()
        self.principal = self.rig.principal()
        self.workspace = InMemoryWorkspaceReadStore()
        self.workspace.add_client(
            TENANT_ID,
            ClientSummaryRead(
                id=CLIENT_ID,
                tenant_id=TENANT_ID,
                display_name="Public Synthetic Client",
                matter_count=0,
            ),
        )
        grant = self.rig.grant(
            capability=Capability.CLIENT_READ,
            purpose=Purpose.CLIENT_SERVICE,
            target="api:workspace.clients.list",
            tenant_id=TENANT_ID,
        )
        raw_capability = raw_leaf(self.rig.issue(self.principal, grant))
        self.sessions = InMemoryPublicSyntheticSessionBackend(
            principal=self.principal,
            display_name="Public Synthetic Reviewer",
            tenants=(BrowserTenant(id=TENANT_ID, display_name="Synthetic Tenant"),),
            capability_names=("tenant.read", "client.read", "matter.read"),
            capabilities_by_request={("GET", "/v1/clients"): raw_capability},
        )

        def principal_resolver(request: Request) -> PrincipalContext:
            return request.state.browser_session.principal

        def scope_resolver(request: Request) -> BoundaryScope:
            return BoundaryScope(tenant_id=request.state.browser_session.active_tenant_id)

        composition = MvpApiComposition(
            workspace_store=self.workspace,
            claim_store=InMemoryClaimLedgerStore(),
            corpus_store=InMemoryCorpusResearchStore(),
            governance_service=cast(PolicyGovernanceService, object()),
            authorizer=self.rig.authorizer,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
            probes=tuple(
                DependencyProbe(name=name, check=lambda: True, synthetic=True)
                for name in sorted(REQUIRED_DEPENDENCIES)
            ),
            mode="development",
            browser_sessions=self.sessions,
        )
        self.client = TestClient(create_mvp_app(composition), base_url="https://testserver")

    def tearDown(self) -> None:
        self.client.close()
        self.rig.close()

    def bootstrap(self):
        return self.client.post(
            "/v1/session/bootstrap",
            json={
                "credentialReference": PUBLIC_SYNTHETIC_CREDENTIAL_REFERENCE,
                "tenantId": str(TENANT_ID),
            },
            headers={"Origin": "https://testserver"},
        )

    def test_sign_in_live_request_and_sign_out_use_secure_cookie(self) -> None:
        signed_in = self.bootstrap()
        self.assertEqual(200, signed_in.status_code)
        self.assertNotIn("authorization", signed_in.text.lower())
        cookie = signed_in.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=strict", cookie)
        csrf = signed_in.json()["csrfToken"]

        clients = self.client.get(
            "/v1/clients",
            headers={"X-SKLegal-Tenant": str(TENANT_ID)},
        )
        self.assertEqual(200, clients.status_code)
        self.assertEqual("Public Synthetic Client", clients.json()[0]["displayName"])
        UUID(clients.headers["X-Correlation-ID"])

        signed_out = self.client.delete(
            "/v1/session", headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(204, signed_out.status_code)
        denied = self.client.get("/v1/clients")
        self.assertIn(denied.status_code, {401, 403, 503})

    def test_expiry_revocation_wrong_tenant_and_csrf_fail_closed(self) -> None:
        signed_in = self.bootstrap()
        csrf = signed_in.json()["csrfToken"]
        session_id = self.client.cookies.get("__Host-sklegal_session")
        assert session_id is not None

        wrong_tenant = self.client.get(
            "/v1/clients",
            headers={"X-SKLegal-Tenant": str(WRONG_TENANT_ID)},
        )
        self.assertEqual(403, wrong_tenant.status_code)
        self.assertEqual("tenant_context_denied", wrong_tenant.json()["detail"]["code"])

        missing_csrf = self.client.post("/v1/session/refresh")
        self.assertEqual(403, missing_csrf.status_code)
        self.assertEqual("csrf_denied", missing_csrf.json()["detail"]["code"])

        self.sessions.expire(session_id)
        expired = self.client.get("/v1/session")
        self.assertEqual(401, expired.status_code)

        renewed = self.bootstrap()
        session_id = self.client.cookies.get("__Host-sklegal_session")
        assert session_id is not None
        self.sessions.revoke(session_id)
        revoked = self.client.get("/v1/session")
        self.assertEqual(401, revoked.status_code)

        refreshed = self.bootstrap()
        csrf = refreshed.json()["csrfToken"]
        switched = self.client.post(
            "/v1/session/tenant",
            json={"tenantId": str(WRONG_TENANT_ID)},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(403, switched.status_code)
        self.assertEqual("tenant_switch_denied", switched.json()["detail"]["code"])

    def test_cors_is_deny_by_default_and_security_headers_are_present(self) -> None:
        denied = self.client.post(
            "/v1/session/bootstrap",
            json={
                "credentialReference": PUBLIC_SYNTHETIC_CREDENTIAL_REFERENCE,
                "tenantId": str(TENANT_ID),
            },
            headers={"Origin": "https://untrusted.invalid"},
        )
        self.assertEqual(403, denied.status_code)
        self.assertNotIn("access-control-allow-origin", denied.headers)
        self.assertIn("default-src 'self'", denied.headers["content-security-policy"])
        self.assertEqual("no-referrer", denied.headers["referrer-policy"])
        self.assertEqual("nosniff", denied.headers["x-content-type-options"])


if __name__ == "__main__":
    unittest.main()
