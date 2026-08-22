"""HTTP boundary tests for the corpus research read API (SKL-S4-03A).

Covers the full S2-10 retrieval trace on the wire, matter membership
enforcement, capability scoping, the exact source-span viewer with its
denial state, stale projection generation reporting, and fail-closed
behavior. All fixtures are synthetic: no real matter content, no real
citation, no legacy identifiers, and no HammerTime paths.
"""

from __future__ import annotations

import unittest
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sklegal_api.corpus import (
    CorpusResultRead,
    CorpusScopeOptionRead,
    CorpusSearchResponseRead,
    CorpusSpanAvailableRead,
    CorpusSpanDeniedRead,
    CorpusTraceRead,
    InMemoryCorpusResearchStore,
    build_corpus_research_router,
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
MATTER_ID = UUID("20000000-0000-4000-8000-0000000000a1")
SECOND_MATTER_ID = UUID("20000000-0000-4000-8000-0000000000a2")
UNRECORDED_MATTER_ID = UUID("20000000-0000-4000-8000-0000000000c1")

QUERY = "synthetic surplus funds statute"
SOURCE_ID = "synthetic-source-1"
DENIED_SOURCE_ID = "synthetic-source-denied-1"
UNKNOWN_SOURCE_ID = "synthetic-source-unknown-1"

SOURCE_SHA = "b" * 64
TRACE_QUERY_SHA = "c" * 64


def scope_options() -> tuple[CorpusScopeOptionRead, ...]:
    return (
        CorpusScopeOptionRead(
            scope="this_matter",
            state="active",
        ),
        CorpusScopeOptionRead(
            scope="tenant_corpus",
            state="unavailable",
            reason="Tenant corpus scope is pending retrieval policy approval.",
        ),
        CorpusScopeOptionRead(
            scope="official_sources",
            state="unavailable",
            reason="Official-source verification is a separate approved lane.",
        ),
    )


def trace(*, generation: int = 4, current_generation: int = 4) -> CorpusTraceRead:
    return CorpusTraceRead(
        scope_kind="matter",
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        release_id="synthetic-release-1",
        projection_generation=generation,
        current_projection_generation=current_generation,
        projection_stale=generation != current_generation,
        backend_watermark=10,
        lag_events=0,
        lag_seconds=0.0,
        query_template_id="lexical.search.v1",
        query_template_version="1.0.0",
        query_template_sha256=TRACE_QUERY_SHA,
        rank_path=("lexical_rank", "scope_aggregate"),
        retrieval_adapter_version="1.0.0",
        source_ids=(SOURCE_ID,),
        source_hashes=(SOURCE_SHA,),
    )


def results() -> tuple[CorpusResultRead, ...]:
    return (
        CorpusResultRead(
            rank=1,
            score=1.5,
            snippet="Synthetic corpus snippet about the queried statute.",
            source_id=SOURCE_ID,
            title="Synthetic research note",
            citation="SYN 100 ILCS 1/2",
            classification="protected",
            origin="matter_corpus",
            verification_state="unverified_research_proposal",
            source_version="1",
            source_sha256=SOURCE_SHA,
            document_id="synthetic-document-1",
            chunk_id="synthetic-chunk-1",
            chunk_sha256="d" * 64,
            source_locator="fixture/synthetic-source-1",
            span_kind="text",
            span_start=0,
            span_end=10,
            span_page=1,
            supersession_status="current",
        ),
    )


def search_response(
    *, generation: int = 4, current_generation: int = 4
) -> CorpusSearchResponseRead:
    return CorpusSearchResponseRead(
        matter_id=MATTER_ID,
        query=QUERY,
        scope_options=scope_options(),
        results=results(),
        trace=trace(generation=generation, current_generation=current_generation),
    )


def available_span() -> CorpusSpanAvailableRead:
    return CorpusSpanAvailableRead(
        source_id=SOURCE_ID,
        source_version="1",
        source_sha256=SOURCE_SHA,
        document_id="synthetic-document-1",
        citation="SYN 100 ILCS 1/2",
        title="Synthetic research note",
        classification="protected",
        source_locator="fixture/synthetic-source-1",
        span_kind="text",
        span_start=0,
        span_end=10,
        span_page=1,
        span_text="Exact synthetic span text used for the viewer test.",
        supersession_status="current",
        jurisdiction="Synthetic Jurisdiction",
    )


def denied_span() -> CorpusSpanDeniedRead:
    return CorpusSpanDeniedRead(
        source_id=DENIED_SOURCE_ID,
        denial_reason="source_not_accessible",
        denial_message=(
            "The source artifact is not accessible under the current "
            "matter policy. No span content is available."
        ),
    )


class CorpusApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = CapabilityTestRig()
        self.principal = self.rig.principal()
        self.store = InMemoryCorpusResearchStore()
        self.store.add_search(TENANT_ID, MATTER_ID, QUERY, search_response())
        self.store.add_span(TENANT_ID, MATTER_ID, available_span())
        self.store.add_span(TENANT_ID, MATTER_ID, denied_span())
        self.store.set_matter_members(
            TENANT_ID, MATTER_ID, frozenset({self.principal.principal_id})
        )
        self.store.set_matter_members(
            TENANT_ID, UNRECORDED_MATTER_ID, frozenset({self.principal.principal_id})
        )

        def principal_resolver(_: Request) -> PrincipalContext:
            return self.principal

        def scope_resolver(request: Request) -> BoundaryScope:
            matter = request.path_params.get("matter_id")
            source = request.path_params.get("source_id")
            matter_uuid = UUID(str(matter)) if matter is not None else None
            return BoundaryScope(
                tenant_id=self.principal.tenant_id,
                matter_id=matter_uuid,
                resource_id=str(source)
                if source is not None
                else (str(matter_uuid) if matter_uuid is not None else None),
            )

        app = FastAPI()
        app.include_router(
            build_corpus_research_router(
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
        resource_id: str | None = None,
    ) -> str:
        grant = self.rig.grant(
            capability=capability,
            purpose=purpose,
            target=f"api:corpus.{route}",
            matter_id=matter_id,
            resource_id=resource_id
            if resource_id is not None
            else (str(matter_id) if matter_id is not None else None),
        )
        return raw_leaf(self.rig.issue(self.principal, grant))

    def search_token(self, matter_id: UUID = MATTER_ID) -> str:
        return self.token(
            "search",
            Capability.CORPUS_SEARCH,
            Purpose.LEGAL_RESEARCH,
            matter_id=matter_id,
        )

    def span_token(
        self, source_id: str = SOURCE_ID, matter_id: UUID = MATTER_ID
    ) -> str:
        # The span viewer's capability resource is the exact source
        # artifact, so the grant pins the source identifier.
        return self.token(
            "span",
            Capability.CORPUS_ARTIFACT_READ,
            Purpose.LEGAL_RESEARCH,
            matter_id=matter_id,
            resource_id=source_id,
        )

    def test_search_returns_full_trace_and_camel_case_fields(self) -> None:
        response = self.client.post(
            f"/v1/matters/{MATTER_ID}/corpus/search",
            json={"query": QUERY},
            headers={"Authorization": f"Bearer {self.search_token()}"},
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(QUERY, body["query"])
        self.assertEqual(str(MATTER_ID), body["matterId"])
        trace_body = body["trace"]
        self.assertEqual("matter", trace_body["scopeKind"])
        self.assertEqual(str(TENANT_ID), trace_body["tenantId"])
        self.assertEqual("synthetic-release-1", trace_body["releaseId"])
        self.assertEqual(4, trace_body["projectionGeneration"])
        self.assertEqual(4, trace_body["currentProjectionGeneration"])
        self.assertFalse(trace_body["projectionStale"])
        self.assertEqual(10, trace_body["backendWatermark"])
        self.assertEqual("lexical.search.v1", trace_body["queryTemplateId"])
        self.assertEqual(["lexical_rank", "scope_aggregate"], trace_body["rankPath"])
        self.assertEqual([SOURCE_ID], trace_body["sourceIds"])
        self.assertEqual([SOURCE_SHA], trace_body["sourceHashes"])
        self.assertEqual(
            "unverified_research_proposal",
            body["results"][0]["verificationState"],
        )
        self.assertEqual("matter_corpus", body["results"][0]["origin"])

    def test_search_reports_stale_projection_generation(self) -> None:
        stale_query = "synthetic stale generation query"
        self.store.add_search(
            TENANT_ID,
            MATTER_ID,
            stale_query,
            search_response(generation=3, current_generation=4),
        )
        response = self.client.post(
            f"/v1/matters/{MATTER_ID}/corpus/search",
            json={"query": stale_query},
            headers={"Authorization": f"Bearer {self.search_token()}"},
        )
        self.assertEqual(200, response.status_code)
        trace_body = response.json()["trace"]
        self.assertEqual(3, trace_body["projectionGeneration"])
        self.assertEqual(4, trace_body["currentProjectionGeneration"])
        # Staleness is reported, never harmonized: both generations survive.
        self.assertTrue(trace_body["projectionStale"])

    def test_search_scope_options_are_explicitly_governed(self) -> None:
        response = self.client.post(
            f"/v1/matters/{MATTER_ID}/corpus/search",
            json={"query": QUERY},
            headers={"Authorization": f"Bearer {self.search_token()}"},
        )
        options = response.json()["scopeOptions"]
        self.assertEqual(
            [
                ("this_matter", "active"),
                ("tenant_corpus", "unavailable"),
                ("official_sources", "unavailable"),
            ],
            [(item["scope"], item["state"]) for item in options],
        )
        # Unavailable scopes carry a reason instead of vanishing.
        self.assertTrue(all(item["reason"] for item in options[1:]))

    def test_missing_credential_is_denied(self) -> None:
        response = self.client.post(
            f"/v1/matters/{MATTER_ID}/corpus/search", json={"query": QUERY}
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("capability_denied", response.json()["detail"]["code"])

    def test_capability_scoped_to_another_matter_is_denied(self) -> None:
        response = self.client.post(
            f"/v1/matters/{MATTER_ID}/corpus/search",
            json={"query": QUERY},
            headers={"Authorization": f"Bearer {self.search_token(SECOND_MATTER_ID)}"},
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("capability_denied", response.json()["detail"]["code"])

    def test_span_capability_cannot_drive_search(self) -> None:
        response = self.client.post(
            f"/v1/matters/{MATTER_ID}/corpus/search",
            json={"query": QUERY},
            headers={"Authorization": f"Bearer {self.span_token()}"},
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("capability_denied", response.json()["detail"]["code"])

    def test_non_member_with_valid_capability_is_denied(self) -> None:
        # The second matter has no recorded roster for this principal.
        response = self.client.post(
            f"/v1/matters/{SECOND_MATTER_ID}/corpus/search",
            json={"query": QUERY},
            headers={"Authorization": f"Bearer {self.search_token(SECOND_MATTER_ID)}"},
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("matter_membership_denied", response.json()["detail"]["code"])

    def test_unknown_matter_is_not_found_for_members(self) -> None:
        response = self.client.post(
            f"/v1/matters/{UNRECORDED_MATTER_ID}/corpus/search",
            json={"query": QUERY},
            headers={
                "Authorization": f"Bearer {self.search_token(UNRECORDED_MATTER_ID)}"
            },
        )
        self.assertEqual(404, response.status_code)
        self.assertEqual("not_found", response.json()["detail"]["code"])

    def test_unknown_query_is_not_found(self) -> None:
        response = self.client.post(
            f"/v1/matters/{MATTER_ID}/corpus/search",
            json={"query": "synthetic query with no recorded response"},
            headers={"Authorization": f"Bearer {self.search_token()}"},
        )
        self.assertEqual(404, response.status_code)

    def test_empty_query_is_rejected(self) -> None:
        response = self.client.post(
            f"/v1/matters/{MATTER_ID}/corpus/search",
            json={"query": ""},
            headers={"Authorization": f"Bearer {self.search_token()}"},
        )
        self.assertEqual(422, response.status_code)

    def test_span_returns_exact_text_and_locator(self) -> None:
        response = self.client.get(
            f"/v1/matters/{MATTER_ID}/corpus/sources/{SOURCE_ID}/span",
            headers={"Authorization": f"Bearer {self.span_token()}"},
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("available", body["state"])
        self.assertEqual(
            "Exact synthetic span text used for the viewer test.",
            body["spanText"],
        )
        self.assertEqual("fixture/synthetic-source-1", body["sourceLocator"])
        self.assertEqual(SOURCE_SHA, body["sourceSha256"])
        self.assertEqual("current", body["supersessionStatus"])
        self.assertEqual("Synthetic Jurisdiction", body["jurisdiction"])

    def test_inaccessible_source_renders_denial_without_content(self) -> None:
        response = self.client.get(
            f"/v1/matters/{MATTER_ID}/corpus/sources/{DENIED_SOURCE_ID}/span",
            headers={"Authorization": f"Bearer {self.span_token(DENIED_SOURCE_ID)}"},
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("denied", body["state"])
        self.assertEqual("source_not_accessible", body["denialReason"])
        # The denial structurally carries no span content, locator, or hashes.
        self.assertNotIn("spanText", body)
        self.assertNotIn("sourceLocator", body)
        self.assertNotIn("sourceSha256", body)

    def test_unknown_source_is_not_found(self) -> None:
        response = self.client.get(
            f"/v1/matters/{MATTER_ID}/corpus/sources/{UNKNOWN_SOURCE_ID}/span",
            headers={"Authorization": f"Bearer {self.span_token(UNKNOWN_SOURCE_ID)}"},
        )
        self.assertEqual(404, response.status_code)
        self.assertEqual("not_found", response.json()["detail"]["code"])

    def test_search_capability_cannot_drive_span_viewer(self) -> None:
        response = self.client.get(
            f"/v1/matters/{MATTER_ID}/corpus/sources/{SOURCE_ID}/span",
            headers={"Authorization": f"Bearer {self.search_token()}"},
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("capability_denied", response.json()["detail"]["code"])

    def test_token_for_another_tenant_is_denied(self) -> None:
        other_principal = self.rig.principal(tenant_id=OTHER_TENANT_ID)
        grant = self.rig.grant(
            capability=Capability.CORPUS_SEARCH,
            purpose=Purpose.LEGAL_RESEARCH,
            target="api:corpus.search",
            tenant_id=OTHER_TENANT_ID,
            matter_id=MATTER_ID,
            resource_id=str(MATTER_ID),
        )
        token = raw_leaf(self.rig.issue(other_principal, grant))
        response = self.client.post(
            f"/v1/matters/{MATTER_ID}/corpus/search",
            json={"query": QUERY},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("capability_denied", response.json()["detail"]["code"])

    def test_store_outage_fails_closed_with_503(self) -> None:
        self.store.available = False
        search_response_http = self.client.post(
            f"/v1/matters/{MATTER_ID}/corpus/search",
            json={"query": QUERY},
            headers={"Authorization": f"Bearer {self.search_token()}"},
        )
        self.assertEqual(503, search_response_http.status_code)
        self.assertEqual(
            "corpus_unavailable", search_response_http.json()["detail"]["code"]
        )
        span_response = self.client.get(
            f"/v1/matters/{MATTER_ID}/corpus/sources/{SOURCE_ID}/span",
            headers={"Authorization": f"Bearer {self.span_token()}"},
        )
        self.assertEqual(503, span_response.status_code)
        self.assertEqual("corpus_unavailable", span_response.json()["detail"]["code"])


if __name__ == "__main__":
    unittest.main()
