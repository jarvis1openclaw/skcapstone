"""HTTP boundary tests for the client and matter workspace read API.

Covers tenant isolation, matter membership enforcement, capability
scoping, and fail-closed behavior. All fixtures are synthetic; no real
matter content, real legacy identifiers, or HammerTime paths are used.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sklegal_api.workspace import (
    ClientSummaryRead,
    InMemoryWorkspaceReadStore,
    MatterDetailRead,
    MatterWorkspaceRead,
    WorkspaceMatterRead,
    WorkspaceProvenanceRead,
    build_workspace_router,
)
from sklegal_capauth import (
    BoundaryScope,
    Capability,
    PrincipalContext,
    Purpose,
)

from tests.support.capauth_contract import (
    TENANT_ID,
    CapabilityTestRig,
    raw_leaf,
)

OTHER_TENANT_ID = UUID("30000000-0000-4000-8000-000000000009")
CLIENT_ID = UUID("40000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("20000000-0000-4000-8000-0000000000a1")
SECOND_MATTER_ID = UUID("20000000-0000-4000-8000-0000000000a2")
FOREIGN_MATTER_ID = UUID("20000000-0000-4000-8000-0000000000b1")
UNRECORDED_MATTER_ID = UUID("20000000-0000-4000-8000-0000000000c1")

STAMP = datetime(2099, 1, 2, 3, 4, 5, tzinfo=UTC)


def matter_detail(
    matter_id: UUID,
    *,
    tenant_id: UUID = TENANT_ID,
    client_id: UUID = CLIENT_ID,
    title: str = "Synthetic matter",
) -> MatterDetailRead:
    return MatterDetailRead(
        id=matter_id,
        tenant_id=tenant_id,
        client_id=client_id,
        client_display_name="Synthetic Client",
        title=title,
        status="open",
        summary="Synthetic matter summary.",
        opened_on="2099-01-01",
        legacy_aliases=("synthetic-legacy-container-1",),
    )


def workspace_view(matter_id: UUID) -> MatterWorkspaceRead:
    return MatterWorkspaceRead(
        matter=WorkspaceMatterRead(
            matter_id=matter_id,
            client_id=CLIENT_ID,
            client_display_name="Synthetic Client",
            title="Synthetic matter",
            summary="Synthetic matter summary.",
            status="open",
            opened_at=STAMP,
            legacy_aliases=("synthetic-legacy-container-1",),
        ),
        provenance=WorkspaceProvenanceRead(
            source_snapshot="synthetic-snapshot-1",
            current_source_snapshot="synthetic-snapshot-1",
            adapter_version="0.1.0",
            observed_at=STAMP,
            stale=False,
            source_files=(),
        ),
    )


class WorkspaceApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = CapabilityTestRig()
        self.principal = self.rig.principal()
        self.store = InMemoryWorkspaceReadStore()
        self.store.add_client(
            TENANT_ID,
            ClientSummaryRead(
                id=CLIENT_ID,
                tenant_id=TENANT_ID,
                display_name="Synthetic Client",
                matter_count=2,
            ),
        )
        self.store.add_matter(TENANT_ID, matter_detail(MATTER_ID))
        self.store.add_matter(
            TENANT_ID, matter_detail(SECOND_MATTER_ID, title="Second matter")
        )
        self.store.add_workspace(TENANT_ID, workspace_view(MATTER_ID))
        self.store.set_matter_members(
            TENANT_ID, MATTER_ID, frozenset({self.principal.principal_id})
        )
        self.store.set_matter_members(
            TENANT_ID, UNRECORDED_MATTER_ID, frozenset({self.principal.principal_id})
        )
        # A matter recorded only under a different tenant.
        self.store.add_matter(
            OTHER_TENANT_ID, matter_detail(FOREIGN_MATTER_ID, tenant_id=OTHER_TENANT_ID)
        )
        self.store.set_matter_members(
            OTHER_TENANT_ID,
            FOREIGN_MATTER_ID,
            frozenset({self.principal.principal_id}),
        )

        def principal_resolver(_: Request) -> PrincipalContext:
            return self.principal

        def scope_resolver(request: Request) -> BoundaryScope:
            matter = request.path_params.get("matter_id")
            return BoundaryScope(
                tenant_id=self.principal.tenant_id,
                matter_id=UUID(str(matter)) if matter is not None else None,
                resource_id=str(matter) if matter is not None else None,
            )

        app = FastAPI()
        app.include_router(
            build_workspace_router(
                store=self.store,
                authorizer=self.rig.authorizer,
                principal_resolver=principal_resolver,
                scope_resolver=scope_resolver,
            )
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.rig.close()

    def token(
        self,
        route: str,
        capability: Capability,
        purpose: Purpose,
        *,
        matter_id: UUID | None = None,
        tenant_id: UUID = TENANT_ID,
    ) -> str:
        grant = self.rig.grant(
            capability=capability,
            purpose=purpose,
            target=f"api:workspace.{route}",
            tenant_id=tenant_id,
            matter_id=matter_id,
            resource_id=str(matter_id) if matter_id is not None else None,
        )
        return raw_leaf(self.rig.issue(self.principal, grant))

    def client_token(self, route: str = "clients.list", **kwargs: object) -> str:
        return self.token(route, Capability.CLIENT_READ, Purpose.CLIENT_SERVICE)

    def matter_token(self, matter_id: UUID, route: str) -> str:
        return self.token(
            route,
            Capability.MATTER_READ,
            Purpose.MATTER_MANAGEMENT,
            matter_id=matter_id,
        )

    def test_list_clients_returns_camel_case_summaries(self) -> None:
        response = self.client.get(
            "/v1/clients",
            headers={"Authorization": f"Bearer {self.client_token()}"},
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(1, len(body))
        self.assertEqual(str(CLIENT_ID), body[0]["id"])
        self.assertEqual(str(TENANT_ID), body[0]["tenantId"])
        self.assertEqual("Synthetic Client", body[0]["displayName"])
        # Only the member matter is counted; the second matter is hidden.
        self.assertEqual(1, body[0]["matterCount"])

    def test_client_detail_lists_member_matters_only(self) -> None:
        response = self.client.get(
            f"/v1/clients/{CLIENT_ID}",
            headers={"Authorization": f"Bearer {self.client_token('clients.get')}"},
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual([str(MATTER_ID)], [item["id"] for item in body["matters"]])

    def test_list_matters_is_membership_filtered(self) -> None:
        response = self.client.get(
            "/v1/matters",
            headers={"Authorization": f"Bearer {self.client_token('matters.list')}"},
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual([str(MATTER_ID)], [item["id"] for item in body])
        self.assertEqual("open", body[0]["status"])

    def test_matter_workspace_returns_full_aggregate(self) -> None:
        response = self.client.get(
            f"/v1/matters/{MATTER_ID}/workspace",
            headers={
                "Authorization": f"Bearer {self.matter_token(MATTER_ID, 'matters.workspace')}"
            },
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(str(MATTER_ID), body["matter"]["matterId"])
        self.assertEqual("Synthetic matter", body["matter"]["title"])
        self.assertEqual("synthetic-snapshot-1", body["provenance"]["sourceSnapshot"])
        self.assertIn("tensions", body)
        self.assertIn("executionStates", body)
        self.assertIn("gaps", body)
        self.assertEqual([], body["workProducts"])

    def test_matter_detail_requires_membership(self) -> None:
        response = self.client.get(
            f"/v1/matters/{MATTER_ID}",
            headers={
                "Authorization": f"Bearer {self.matter_token(MATTER_ID, 'matters.get')}"
            },
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(str(MATTER_ID), response.json()["id"])

    def test_missing_credential_is_denied(self) -> None:
        response = self.client.get(f"/v1/matters/{MATTER_ID}/workspace")
        self.assertEqual(403, response.status_code)
        self.assertEqual("capability_denied", response.json()["detail"]["code"])

    def test_capability_scoped_to_another_matter_is_denied(self) -> None:
        response = self.client.get(
            f"/v1/matters/{MATTER_ID}/workspace",
            headers={
                "Authorization": f"Bearer {self.matter_token(SECOND_MATTER_ID, 'matters.workspace')}"
            },
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("capability_denied", response.json()["detail"]["code"])

    def test_non_member_with_valid_capability_is_denied(self) -> None:
        # The second matter has no recorded roster for this principal.
        response = self.client.get(
            f"/v1/matters/{SECOND_MATTER_ID}/workspace",
            headers={
                "Authorization": f"Bearer {self.matter_token(SECOND_MATTER_ID, 'matters.workspace')}"
            },
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("matter_membership_denied", response.json()["detail"]["code"])

    def test_token_for_another_tenant_is_denied(self) -> None:
        other_principal = self.rig.principal(tenant_id=OTHER_TENANT_ID)
        grant = self.rig.grant(
            capability=Capability.MATTER_READ,
            purpose=Purpose.MATTER_MANAGEMENT,
            target="api:workspace.matters.workspace",
            tenant_id=OTHER_TENANT_ID,
            matter_id=MATTER_ID,
            resource_id=str(MATTER_ID),
        )
        token = raw_leaf(self.rig.issue(other_principal, grant))
        response = self.client.get(
            f"/v1/matters/{MATTER_ID}/workspace",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("capability_denied", response.json()["detail"]["code"])

    def test_cross_tenant_matter_is_not_visible(self) -> None:
        # Capability matches the path matter, but the record lives under
        # another tenant: membership fails closed and nothing is leaked.
        response = self.client.get(
            f"/v1/matters/{FOREIGN_MATTER_ID}/workspace",
            headers={
                "Authorization": f"Bearer {self.matter_token(FOREIGN_MATTER_ID, 'matters.workspace')}"
            },
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("matter_membership_denied", response.json()["detail"]["code"])

    def test_unrecorded_matter_is_not_found_for_members(self) -> None:
        response = self.client.get(
            f"/v1/matters/{UNRECORDED_MATTER_ID}/workspace",
            headers={
                "Authorization": f"Bearer {self.matter_token(UNRECORDED_MATTER_ID, 'matters.workspace')}"
            },
        )
        self.assertEqual(404, response.status_code)
        self.assertEqual("not_found", response.json()["detail"]["code"])

    def test_store_outage_fails_closed_with_503(self) -> None:
        self.store.available = False
        response = self.client.get(
            "/v1/clients",
            headers={"Authorization": f"Bearer {self.client_token()}"},
        )
        self.assertEqual(503, response.status_code)
        self.assertEqual("workspace_unavailable", response.json()["detail"]["code"])

    def test_client_route_rejects_matter_capability(self) -> None:
        token = self.matter_token(MATTER_ID, "clients.list")
        response = self.client.get(
            "/v1/clients",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(403, response.status_code)


if __name__ == "__main__":
    unittest.main()
