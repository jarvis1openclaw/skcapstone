"""Composition tests for the internal public-synthetic MVP API."""

from __future__ import annotations

import unittest
from time import monotonic, sleep
from typing import cast
from uuid import UUID

from fastapi import Request
from fastapi.testclient import TestClient
from sklegal_api.app import (
    REQUIRED_DEPENDENCIES,
    DependencyProbe,
    MvpApiComposition,
    MvpCompositionUnavailable,
    RuntimeMode,
    create_mvp_app,
)
from sklegal_api.claims import InMemoryClaimLedgerStore
from sklegal_api.corpus import InMemoryCorpusResearchStore
from sklegal_api.workspace import (
    ClientSummaryRead,
    InMemoryWorkspaceReadStore,
    MatterDetailRead,
)
from sklegal_capauth import BoundaryScope, Capability, PrincipalContext, Purpose
from sklegal_policies import PolicyGovernanceService

from tests.support.capauth_contract import TENANT_ID, CapabilityTestRig, raw_leaf

CLIENT_ONE = UUID("40000000-0000-4000-8000-000000000101")
CLIENT_TWO = UUID("40000000-0000-4000-8000-000000000102")
MATTER_ONE = UUID("20000000-0000-4000-8000-000000000101")
MATTER_TWO = UUID("20000000-0000-4000-8000-000000000102")


class CountingWorkspaceStore(InMemoryWorkspaceReadStore):
    def __init__(self) -> None:
        super().__init__()
        self.list_calls = 0

    def list_clients(
        self, tenant_id: UUID, principal_id: UUID
    ) -> tuple[ClientSummaryRead, ...]:
        self.list_calls += 1
        return super().list_clients(tenant_id, principal_id)


class MvpApiCompositionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = CapabilityTestRig()
        self.principal = self.rig.principal()
        self.workspace = CountingWorkspaceStore()
        self.claims = InMemoryClaimLedgerStore()
        self.corpus = InMemoryCorpusResearchStore()
        self.readiness = {name: True for name in REQUIRED_DEPENDENCIES}
        self._seed_workspace()

    def tearDown(self) -> None:
        self.rig.close()

    def _seed_workspace(self) -> None:
        for client_id, matter_id, name in (
            (CLIENT_ONE, MATTER_ONE, "Synthetic Client One"),
            (CLIENT_TWO, MATTER_TWO, "Synthetic Client Two"),
        ):
            self.workspace.add_client(
                TENANT_ID,
                ClientSummaryRead(
                    id=client_id,
                    tenant_id=TENANT_ID,
                    display_name=name,
                    matter_count=1,
                ),
            )
            self.workspace.add_matter(
                TENANT_ID,
                MatterDetailRead(
                    id=matter_id,
                    tenant_id=TENANT_ID,
                    client_id=client_id,
                    client_display_name=name,
                    title=f"{name} Matter",
                    status="open",
                ),
            )
            self.workspace.set_matter_members(
                TENANT_ID, matter_id, frozenset({self.principal.principal_id})
            )

    def probes(self, *, omit: str | None = None) -> tuple[DependencyProbe, ...]:
        probes: list[DependencyProbe] = []
        for name in sorted(REQUIRED_DEPENDENCIES):
            if name == omit:
                continue

            def check(component: str = name) -> bool:
                return self.readiness[component]

            probes.append(DependencyProbe(name=name, check=check, synthetic=True))
        return tuple(probes)

    def composition(
        self,
        *,
        mode: RuntimeMode = "development",
        probes: tuple[DependencyProbe, ...] | None = None,
    ) -> MvpApiComposition:
        def principal_resolver(_: Request) -> PrincipalContext:
            return self.principal

        def scope_resolver(request: Request) -> BoundaryScope:
            matter = request.path_params.get("matter_id")
            matter_id = UUID(str(matter)) if matter is not None else None
            return BoundaryScope(
                tenant_id=self.principal.tenant_id,
                matter_id=matter_id,
                resource_id=str(matter_id) if matter_id is not None else None,
            )

        return MvpApiComposition(
            workspace_store=self.workspace,
            claim_store=self.claims,
            corpus_store=self.corpus,
            governance_service=cast(PolicyGovernanceService, object()),
            authorizer=self.rig.authorizer,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
            probes=self.probes() if probes is None else probes,
            mode=mode,
        )

    def clients_token(self) -> str:
        grant = self.rig.grant(
            capability=Capability.CLIENT_READ,
            purpose=Purpose.CLIENT_SERVICE,
            target="api:workspace.clients.list",
            tenant_id=TENANT_ID,
        )
        return raw_leaf(self.rig.issue(self.principal, grant))

    def test_factory_mounts_only_the_reviewed_router_inventory(self) -> None:
        app = create_mvp_app(self.composition())
        operation_ids = {
            operation["operationId"]
            for path in app.openapi()["paths"].values()
            for operation in path.values()
        }
        self.assertIn("mvp_health", operation_ids)
        self.assertIn("workspace_matters_workspace", operation_ids)
        self.assertIn("claims_ledger", operation_ids)
        self.assertIn("corpus_search", operation_ids)
        self.assertIn("governance_open_ethical_wall", operation_ids)
        self.assertFalse(any("skgateway" in item for item in operation_ids))

    def test_missing_required_dependency_probe_fails_construction(self) -> None:
        with self.assertRaisesRegex(
            MvpCompositionUnavailable, "required dependency probes are incomplete"
        ):
            create_mvp_app(self.composition(probes=self.probes(omit="provenance")))

    def test_unavailable_dependency_fails_construction_and_startup(self) -> None:
        self.readiness["audit"] = False
        with self.assertRaisesRegex(
            MvpCompositionUnavailable, "required dependency is unavailable"
        ):
            create_mvp_app(self.composition())

        self.readiness["audit"] = True
        app = create_mvp_app(self.composition())
        self.readiness["audit"] = False
        with self.assertRaisesRegex(
            MvpCompositionUnavailable, "required dependency is unavailable"
        ):
            with TestClient(app):
                pass

    def test_production_rejects_synthetic_composition(self) -> None:
        with self.assertRaisesRegex(
            MvpCompositionUnavailable, "synthetic dependencies are forbidden"
        ):
            create_mvp_app(self.composition(mode="production"))

    def test_health_is_bounded_and_fails_closed_after_dependency_outage(self) -> None:
        app = create_mvp_app(self.composition())
        with TestClient(app) as client:
            ready = client.get("/healthz")
            self.assertEqual(200, ready.status_code)
            self.assertEqual(100, ready.json()["budgetMs"])
            self.assertEqual(
                set(REQUIRED_DEPENDENCIES), set(ready.json()["components"])
            )
            self.readiness["persistence"] = False
            unavailable = client.get("/healthz")
            self.assertEqual(503, unavailable.status_code)
            self.assertEqual("unavailable", unavailable.json()["status"])
            self.assertNotIn("detail", unavailable.json())

    def test_health_times_out_a_slow_probe_inside_the_fixed_budget(self) -> None:
        def slow_check() -> bool:
            sleep(0.5)
            return True

        probes = tuple(
            DependencyProbe(
                name=name,
                check=slow_check if name == "provenance" else (lambda: True),
                synthetic=True,
            )
            for name in sorted(REQUIRED_DEPENDENCIES)
        )
        app = create_mvp_app(self.composition(probes=probes))
        with TestClient(app) as client:
            started = monotonic()
            response = client.get("/healthz")
            elapsed = monotonic() - started
        self.assertEqual(503, response.status_code)
        self.assertLess(elapsed, 0.3)
        self.assertEqual("unavailable", response.json()["components"]["provenance"])

    def test_missing_authentication_is_denied_before_store_lookup(self) -> None:
        app = create_mvp_app(self.composition())
        with TestClient(app) as client:
            response = client.get("/v1/clients")
        self.assertEqual(403, response.status_code)
        self.assertEqual(0, self.workspace.list_calls)
        UUID(response.headers["X-Correlation-ID"])

    def test_list_pagination_is_bounded_and_preserves_correlation(self) -> None:
        app = create_mvp_app(self.composition())
        correlation_id = UUID("70000000-0000-4000-8000-000000000001")
        with TestClient(app) as client:
            response = client.get(
                "/v1/clients?offset=1&limit=1",
                headers={
                    "Authorization": f"Bearer {self.clients_token()}",
                    "X-Correlation-ID": str(correlation_id),
                },
            )
            over_limit = client.get(
                "/v1/clients?limit=101",
                headers={"Authorization": f"Bearer {self.clients_token()}"},
            )
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(response.json()))
        self.assertEqual(str(correlation_id), response.headers["X-Correlation-ID"])
        self.assertEqual(422, over_limit.status_code)

    def test_invalid_correlation_is_sanitized_before_route_execution(self) -> None:
        app = create_mvp_app(self.composition())
        with TestClient(app) as client:
            response = client.get(
                "/v1/clients",
                headers={"X-Correlation-ID": "not-a-uuid"},
            )
        self.assertEqual(400, response.status_code)
        self.assertEqual("correlation_id_invalid", response.json()["detail"]["code"])
        self.assertEqual(0, self.workspace.list_calls)
        UUID(response.headers["X-Correlation-ID"])


if __name__ == "__main__":
    unittest.main()
