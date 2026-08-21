"""Protected API, tool, model, and connector boundary tests."""

from __future__ import annotations

import asyncio
import json
import traceback
import unittest
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sklegal_api.capauth import (
    ProtectedRouteDependency,
    ResolutionBackendUnavailable,
    ResolutionDenied,
)
from sklegal_capauth import (
    ApiCapabilityBoundary,
    Audience,
    AuthorizationDenied,
    AuthorizedContext,
    Capability,
    CapabilityAuthorizer,
    CapabilityGrant,
    ConnectorCapabilityBoundary,
    InMemoryAuditSink,
    InMemoryReplayBackend,
    ModelCapabilityBoundary,
    PresentedCapability,
    PrincipalContext,
    PrincipalType,
    ProtectedBoundary,
    Purpose,
    ToolCapabilityBoundary,
    export_authorization_bearer,
    parse_presented_token,
)

from tests.support.capauth_contract import (
    CapabilityTestRig,
    boundary_scope,
    raw_leaf,
)


class CapabilityBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = CapabilityTestRig()

    def tearDown(self) -> None:
        self.rig.close()

    def test_each_closed_audience_authorizes_only_its_exact_target(self) -> None:
        cases: tuple[
            tuple[PrincipalType, CapabilityGrant, ProtectedBoundary[object]], ...
        ] = (
            (
                PrincipalType.HUMAN,
                self.rig.grant(audience=Audience.API, target="api:matter.get"),
                ApiCapabilityBoundary(
                    authorizer=self.rig.authorizer,
                    route_name="matter.get",
                    capability=Capability.MATTER_READ,
                    purpose=Purpose.MATTER_MANAGEMENT,
                ),
            ),
            (
                PrincipalType.AGENT,
                self.rig.grant(audience=Audience.TOOL, target="tool:corpus.search"),
                ToolCapabilityBoundary(
                    authorizer=self.rig.authorizer,
                    tool_name="corpus.search",
                    capability=Capability.MATTER_READ,
                    purpose=Purpose.MATTER_MANAGEMENT,
                ),
            ),
            (
                PrincipalType.AGENT,
                self.rig.grant(
                    audience=Audience.MODEL,
                    target="model:qwen.generate",
                    capability=Capability.CORPUS_SEARCH,
                    purpose=Purpose.LEGAL_RESEARCH,
                ),
                ModelCapabilityBoundary(
                    authorizer=self.rig.authorizer,
                    model_target="qwen.generate",
                    capability=Capability.CORPUS_SEARCH,
                    purpose=Purpose.LEGAL_RESEARCH,
                ),
            ),
            (
                PrincipalType.CONNECTOR,
                self.rig.grant(
                    audience=Audience.CONNECTOR,
                    target="connector:email.dispatch",
                    capability=Capability.ACTION_EMAIL_DISPATCH,
                    purpose=Purpose.EXTERNAL_ACTION_DISPATCH,
                ),
                ConnectorCapabilityBoundary(
                    authorizer=self.rig.authorizer,
                    connector_name="email.dispatch",
                    capability=Capability.ACTION_EMAIL_DISPATCH,
                    purpose=Purpose.EXTERNAL_ACTION_DISPATCH,
                ),
            ),
        )
        for principal_type, grant, boundary in cases:
            with self.subTest(audience=grant.audience):
                principal = self.rig.principal(principal_type)
                presented = self.rig.issue(principal, grant)
                result = boundary.invoke(
                    principal=principal,
                    scope=boundary_scope(grant),
                    correlation_id=uuid4(),
                    presented=presented,
                    handler=lambda context: context,
                )
                self.assertIsInstance(result, AuthorizedContext)
                assert isinstance(result, AuthorizedContext)
                decision = result.decision
                self.assertTrue(decision.allow)
                self.assertEqual(decision.principal_id, principal.principal_id)
                self.assertEqual(decision.tenant_id, grant.tenant_id)
                self.assertEqual(decision.matter_id, grant.matter_id)
                self.assertEqual(decision.capability, grant.capability)
                self.assertEqual(decision.audience, grant.audience)
                self.assertEqual(decision.target, grant.target)
                self.assertEqual(decision.resource_type, grant.resource_type)
                self.assertEqual(decision.resource_id, grant.resource_id)
                self.assertEqual(decision.resource_version, grant.resource_version)
                self.assertEqual(decision.resource_sha256, grant.resource_sha256)
                self.assertEqual(decision.operation, grant.operation)
                self.assertEqual(decision.purpose, grant.purpose)
                self.assertEqual(decision.model_route, grant.model_route)
                self.assertEqual(decision.workflow_run_id, grant.workflow_run_id)
                self.assertEqual(decision.delegation_depth, 0)
                self.assertEqual(decision.ancestor_credential_digests, ())
                self.assertEqual(
                    decision.credential_digest,
                    parse_presented_token(raw_leaf(presented)).credential_digest,
                )
                self.assertEqual(
                    tuple(
                        reference.principal_id
                        for reference in decision.principal_policy_revisions
                    ),
                    (principal.principal_id,),
                )
                rendered = decision.model_dump_json()
                self.assertNotIn(raw_leaf(presented), rendered)
                self.assertNotIn("BEGIN PGP SIGNATURE", rendered)
                self.assertEqual(self.rig.audit.decisions()[-1], decision)

    def test_missing_capability_stops_handler_before_invocation(self) -> None:
        principal = self.rig.principal()
        grant = self.rig.grant(target="api:matter.get")
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=self.rig.authorizer,
            route_name="matter.get",
            capability=Capability.MATTER_READ,
            purpose=Purpose.MATTER_MANAGEMENT,
        )
        invoked = False

        def handler(_: object) -> None:
            nonlocal invoked
            invoked = True

        with self.assertRaises(AuthorizationDenied):
            boundary.invoke(
                principal=principal,
                scope=boundary_scope(grant),
                correlation_id=uuid4(),
                presented=None,
                handler=handler,
            )
        self.assertFalse(invoked)

    def test_cross_boundary_token_cannot_be_reused(self) -> None:
        principal = self.rig.principal(PrincipalType.AGENT)
        tool_grant = self.rig.grant(
            audience=Audience.TOOL,
            target="tool:corpus.search",
            capability=Capability.CORPUS_SEARCH,
            purpose=Purpose.LEGAL_RESEARCH,
        )
        model_grant = self.rig.grant(
            audience=Audience.MODEL,
            target="model:qwen.generate",
            capability=Capability.CORPUS_SEARCH,
            purpose=Purpose.LEGAL_RESEARCH,
        )
        model_boundary: ModelCapabilityBoundary[object] = ModelCapabilityBoundary(
            authorizer=self.rig.authorizer,
            model_target="qwen.generate",
            capability=Capability.CORPUS_SEARCH,
            purpose=Purpose.LEGAL_RESEARCH,
        )
        with self.assertRaises(AuthorizationDenied) as raised:
            model_boundary.authorize(
                principal=principal,
                scope=boundary_scope(model_grant),
                correlation_id=uuid4(),
                presented=self.rig.issue(principal, tool_grant),
            )
        self.assertEqual(raised.exception.decision.reason_code.value, "wrong_audience")


class ProtectedRouteDependencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = CapabilityTestRig()
        self.principal = self.rig.principal()
        self.grant = self.rig.grant(target="api:matter.get")
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=self.rig.authorizer,
            route_name="matter.get",
            capability=Capability.MATTER_READ,
            purpose=Purpose.MATTER_MANAGEMENT,
        )

        def principal_resolver(_: Request) -> PrincipalContext:
            return self.principal

        def scope_resolver(_: Request):  # type: ignore[no-untyped-def]
            return boundary_scope(self.grant)

        dependency = ProtectedRouteDependency(
            boundary=boundary,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        )
        self.dependency = dependency
        app = FastAPI()

        @app.get("/matter")
        async def get_matter(
            authorized: object = Depends(dependency),
        ) -> dict[str, str]:
            del authorized
            return {"status": "ok"}

        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.rig.close()

    def test_missing_valid_and_replayed_route_invocations(self) -> None:
        missing = self.client.get("/matter")
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(missing.json()["detail"]["code"], "capability_denied")

        token = self.rig.issue(self.principal, self.grant)
        raw = raw_leaf(token)
        allowed = self.client.get(
            "/matter",
            headers={"Authorization": f"Bearer {raw}"},
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json(), {"status": "ok"})

        replayed = self.client.get(
            "/matter",
            headers={"Authorization": f"Bearer {raw}"},
        )
        self.assertEqual(replayed.status_code, 403)
        self.assertNotIn(raw, replayed.text)

        malformed = self.client.get(
            "/matter",
            headers={"Authorization": "Bearer {}"},
        )
        self.assertEqual(malformed.status_code, 403)

    def test_resolution_denial_and_outage_statuses_are_not_conflated(self) -> None:
        sentinel = "SYNTHETIC_RESOLVER_CREDENTIAL_SENTINEL"

        def make_client(resolver):
            boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
                authorizer=self.rig.authorizer,
                route_name="matter.get",
                capability=Capability.MATTER_READ,
                purpose=Purpose.MATTER_MANAGEMENT,
            )
            dependency = ProtectedRouteDependency(
                boundary=boundary,
                principal_resolver=resolver,
                scope_resolver=lambda _: boundary_scope(self.grant),
            )
            app = FastAPI()

            @app.get("/matter")
            async def get_matter(
                authorized: object = Depends(dependency),
            ) -> dict[str, str]:
                del authorized
                return {"status": "ok"}

            return TestClient(app)

        def http_unauthorized(_: Request) -> PrincipalContext:
            raise HTTPException(status_code=401, detail=sentinel)

        def http_denied(_: Request) -> PrincipalContext:
            raise HTTPException(status_code=400, detail=sentinel)

        def http_outage(_: Request) -> PrincipalContext:
            raise HTTPException(status_code=502, detail=sentinel)

        def typed_denied(_: Request) -> PrincipalContext:
            raise ResolutionDenied(status_code=403)

        def typed_outage(_: Request) -> PrincipalContext:
            raise ResolutionBackendUnavailable(sentinel)

        def unknown_outage(_: Request) -> PrincipalContext:
            raise RuntimeError(sentinel)

        cases = (
            (http_unauthorized, 401, "authentication_required"),
            (http_denied, 403, "identity_resolution_denied"),
            (http_outage, 503, "authorization_backend_unavailable"),
            (typed_denied, 403, "identity_resolution_denied"),
            (typed_outage, 503, "authorization_backend_unavailable"),
            (unknown_outage, 503, "authorization_backend_unavailable"),
        )
        for resolver, status_code, code in cases:
            with self.subTest(status_code=status_code, code=code):
                client = make_client(resolver)
                try:
                    response = client.get(
                        "/matter",
                        headers={"Authorization": f"Bearer {sentinel}"},
                    )
                finally:
                    client.close()
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.json()["detail"]["code"], code)
                self.assertNotIn(sentinel, response.text)

    def test_delegated_bearer_chain_matches_generic_authorization(self) -> None:
        parent = self.rig.principal(PrincipalType.SERVICE)
        child_principal = self.rig.principal(PrincipalType.SERVICE)
        grant = self.rig.grant(
            audience=Audience.API,
            target="api:matter.get",
        )
        root = self.rig.issue(parent, grant, max_delegation_depth=1)
        parsed_root = parse_presented_token(raw_leaf(root))
        presented = root.with_child(
            self.rig.issuer._issue_child(
                parent=parsed_root,
                principal=child_principal,
                grant=grant,
                ttl_seconds=60,
                max_depth=1,
            )
        )
        generic_authorizer = self.rig.new_authorizer(
            replay=InMemoryReplayBackend(clock=self.rig.clock),
            audit=InMemoryAuditSink(),
        )
        generic = generic_authorizer.authorize(
            presented,
            self.rig.request(child_principal, grant),
        )

        api_authorizer: CapabilityAuthorizer = self.rig.new_authorizer(
            replay=InMemoryReplayBackend(clock=self.rig.clock),
            audit=InMemoryAuditSink(),
        )
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=api_authorizer,
            route_name="matter.get",
            capability=Capability.MATTER_READ,
            purpose=Purpose.MATTER_MANAGEMENT,
        )
        dependency = ProtectedRouteDependency(
            boundary=boundary,
            principal_resolver=lambda _: child_principal,
            scope_resolver=lambda _: boundary_scope(grant),
        )
        app = FastAPI()

        @app.get("/delegated")
        async def delegated(
            authorized: object = Depends(dependency),
        ) -> dict[str, object]:
            assert isinstance(authorized, AuthorizedContext)
            decision = authorized.decision
            return {
                "allow": decision.allow,
                "credential_digest": decision.credential_digest,
                "principal_id": str(decision.principal_id),
                "tenant_id": str(decision.tenant_id),
                "matter_id": str(decision.matter_id),
                "capability": decision.capability.value,
                "audience": decision.audience.value,
                "target": decision.target,
                "resource_type": decision.resource_type.value,
                "resource_id": decision.resource_id,
                "resource_version": decision.resource_version,
                "resource_sha256": decision.resource_sha256,
                "operation": decision.operation.value,
                "purpose": decision.purpose.value,
                "model_route": decision.model_route,
                "workflow_run_id": decision.workflow_run_id,
                "delegation_depth": decision.delegation_depth,
                "ancestor_credential_digests": list(
                    decision.ancestor_credential_digests
                ),
            }

        client = TestClient(app)
        self.addCleanup(client.close)
        bearer = export_authorization_bearer(presented)
        allowed = client.get(
            "/delegated",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        self.assertEqual(allowed.status_code, 200)
        expected = {
            "allow": generic.decision.allow,
            "credential_digest": generic.decision.credential_digest,
            "principal_id": str(generic.decision.principal_id),
            "tenant_id": str(generic.decision.tenant_id),
            "matter_id": str(generic.decision.matter_id),
            "capability": generic.decision.capability.value,
            "audience": generic.decision.audience.value,
            "target": generic.decision.target,
            "resource_type": generic.decision.resource_type.value,
            "resource_id": generic.decision.resource_id,
            "resource_version": generic.decision.resource_version,
            "resource_sha256": generic.decision.resource_sha256,
            "operation": generic.decision.operation.value,
            "purpose": generic.decision.purpose.value,
            "model_route": generic.decision.model_route,
            "workflow_run_id": generic.decision.workflow_run_id,
            "delegation_depth": generic.decision.delegation_depth,
            "ancestor_credential_digests": list(
                generic.decision.ancestor_credential_digests
            ),
        }
        self.assertEqual(allowed.json(), expected)
        for raw_credential in presented.credentials_for_verification():
            self.assertNotIn(raw_credential, allowed.text)
        self.assertNotIn("BEGIN PGP SIGNATURE", allowed.text)

        replayed = client.get(
            "/delegated",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        self.assertEqual(replayed.status_code, 403)
        self.assertNotIn(bearer, replayed.text)

        chain = presented.credentials_for_verification()
        missing_link = export_authorization_bearer(
            PresentedCapability.single(chain[-1])
        )
        reordered = export_authorization_bearer(
            PresentedCapability.delegated(leaf=chain[0], ancestors=(chain[-1],))
        )
        for invalid_bearer in (missing_link, reordered):
            response = client.get(
                "/delegated",
                headers={"Authorization": f"Bearer {invalid_bearer}"},
            )
            self.assertEqual(response.status_code, 403)
            self.assertNotIn(invalid_bearer, response.text)

        tool_grant = self.rig.grant(
            audience=Audience.TOOL,
            capability=Capability.CORPUS_SEARCH,
            purpose=Purpose.LEGAL_RESEARCH,
        )
        tool_root = self.rig.issue(parent, tool_grant, max_delegation_depth=1)
        parsed_tool_root = parse_presented_token(raw_leaf(tool_root))
        tool_chain = tool_root.with_child(
            self.rig.issuer._issue_child(
                parent=parsed_tool_root,
                principal=child_principal,
                grant=tool_grant,
                ttl_seconds=60,
                max_depth=1,
            )
        )
        cross_audience = export_authorization_bearer(tool_chain)
        denied = client.get(
            "/delegated",
            headers={"Authorization": f"Bearer {cross_audience}"},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn(cross_audience, denied.text)

    def test_authorization_wire_rejects_duplicates_and_over_depth(self) -> None:
        sentinel = "SYNTHETIC_WIRE_CREDENTIAL_SENTINEL"
        raw = raw_leaf(self.rig.issue(self.principal, self.grant))
        valid = export_authorization_bearer(PresentedCapability.single(raw))
        duplicate_outer = (
            valid[:-1] + ',"chain":{"leaf":"' + sentinel + '","ancestors":[]}}'
        )
        duplicate_nested = valid.replace(
            '"leaf":',
            f'"leaf":"{sentinel}","leaf":',
            1,
        )
        duplicate_chain = json.dumps(
            {
                "sklegal_presented_capability": "1.0",
                "chain": {"leaf": raw, "ancestors": [raw]},
            },
            separators=(",", ":"),
        )
        over_depth = json.dumps(
            {
                "sklegal_presented_capability": "1.0",
                "chain": {
                    "leaf": raw,
                    "ancestors": ["synthetic:a", "synthetic:b", "synthetic:c"],
                },
            },
            separators=(",", ":"),
        )
        for malformed in (
            duplicate_outer,
            duplicate_nested,
            duplicate_chain,
            over_depth,
        ):
            with self.subTest(prefix=malformed[:48]):
                response = self.client.get(
                    "/matter",
                    headers={"Authorization": f"Bearer {malformed}"},
                )
                self.assertEqual(response.status_code, 403)
                self.assertNotIn(sentinel, response.text)
                self.assertNotIn(malformed, response.text)

    def test_http_exception_trace_excludes_leaf_and_ancestor_wire_values(self) -> None:
        sentinel = "SYNTHETIC_HTTP_WIRE_CREDENTIAL_SENTINEL"
        valid_leaf = raw_leaf(self.rig.issue(self.principal, self.grant))
        malformed_chain = json.dumps(
            {
                "sklegal_presented_capability": "1.0",
                "chain": {"leaf": valid_leaf, "ancestors": [sentinel]},
            },
            separators=(",", ":"),
        )
        for raw_bearer in (sentinel, malformed_chain):
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/matter",
                    "headers": [
                        (
                            b"authorization",
                            f"Bearer {raw_bearer}".encode("ascii"),
                        )
                    ],
                }
            )
            try:
                asyncio.run(self.dependency(request))
            except HTTPException as exc:
                rendered = "".join(traceback.format_exception(exc))
                self.assertNotIn(sentinel, rendered)
                self.assertNotIn(valid_leaf, rendered)
                self.assertNotIn("BEGIN PGP SIGNATURE", rendered)
                self.assertNotIn(sentinel, repr(exc))
            else:
                self.fail("malformed HTTP capability unexpectedly succeeded")


if __name__ == "__main__":
    unittest.main()
