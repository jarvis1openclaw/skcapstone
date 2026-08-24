from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sklegal_api.features.governed_corpus.router import (
    _context,
    build_governed_corpus_router,
)
from sklegal_api.features.governed_corpus.service import GovernedCorpusService
from sklegal_capauth import BoundaryScope, Capability, PrincipalContext, Purpose
from sklegal_persistence.features.governed_corpus.models import (
    CorpusProjectionCommand,
)

from tests.support.capauth_contract import CapabilityTestRig, raw_leaf

from .helpers import (
    CURSOR_KEY,
    MATTER,
    OTHER_MATTER,
    OTHER_TENANT,
    composition,
    fixture,
    source_command,
)


def test_real_capauth_search_route_is_exact_and_sanitized() -> None:
    rig = CapabilityTestRig()
    principal = rig.principal()
    service, repository, policy = composition(seed=False)
    service = GovernedCorpusService(
        core_repository=repository,
        retrieval_repository=repository,
        policy=policy,
        clock=rig.clock,
        cursor_signing_key=CURSOR_KEY,
    )
    repository.set_matter_members(principal.tenant_id, MATTER, {principal.principal_id})
    policy.grants.add(
        (
            principal.tenant_id,
            MATTER,
            principal.principal_id,
            "corpus.search",
            "legal_research",
        )
    )

    def principal_resolver(_: Request) -> PrincipalContext:
        return principal

    def scope_resolver(request: Request) -> BoundaryScope:
        matter_id = UUID(str(request.path_params["matter_id"]))
        return BoundaryScope(
            tenant_id=principal.tenant_id,
            matter_id=matter_id,
            resource_id=str(matter_id),
        )

    app = FastAPI()
    app.include_router(
        build_governed_corpus_router(
            service=service,
            authorizer=rig.authorizer,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        )
    )
    client = TestClient(app)
    try:
        grant = rig.grant(
            capability=Capability.CORPUS_SEARCH,
            purpose=Purpose.LEGAL_RESEARCH,
            target="api:governed_corpus.search",
            matter_id=MATTER,
            resource_id=str(MATTER),
        )
        token = raw_leaf(rig.issue(principal, grant))
        response = client.post(
            f"/v1/matters/{MATTER}/corpus/search",
            json={
                "query": "public synthetic",
                "mode": "full_text",
                "expectedReleaseId": "synthetic-release-1",
                "expectedProjectionGeneration": 3,
                "requiredCoreWatermark": 42,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["noAnswer"] is True
        assert "credential" not in response.text.casefold()

        missing = client.post(
            f"/v1/matters/{MATTER}/corpus/search",
            json={
                "query": "public synthetic",
                "mode": "full_text",
                "expectedReleaseId": "synthetic-release-1",
                "expectedProjectionGeneration": 3,
                "requiredCoreWatermark": 42,
            },
        )
        assert missing.status_code == 403
        assert missing.json()["detail"]["code"] == "capability_denied"
    finally:
        client.close()
        rig.close()


def test_real_capauth_fresh_token_paginates_and_every_mismatch_closes() -> None:
    rig = CapabilityTestRig()
    principal = rig.principal()
    resolved_principal = [principal]
    service, repository, policy = composition(seed=False, clock=rig.clock)
    repository.set_matter_members(principal.tenant_id, MATTER, {principal.principal_id})
    search_grant = rig.grant(
        capability=Capability.CORPUS_SEARCH,
        purpose=Purpose.LEGAL_RESEARCH,
        target="api:governed_corpus.search",
        matter_id=MATTER,
        resource_id=str(MATTER),
    )
    ingest_grant = rig.grant(
        capability=Capability.CORPUS_INGEST_SUBMIT,
        purpose=Purpose.CORPUS_INGESTION,
        target="api:governed_corpus.record_source",
        matter_id=MATTER,
        resource_id=str(MATTER),
    )
    for capability, purpose in (
        ("corpus.search", "legal_research"),
        ("corpus.ingest.submit", "corpus_ingestion"),
    ):
        policy.grants.add(
            (
                principal.tenant_id,
                MATTER,
                principal.principal_id,
                capability,
                purpose,
            )
        )
    _, sources = fixture()
    for index, source in enumerate(sources, start=1):
        values = source_command(source).model_dump(mode="python")
        values["permitted_principal_ids"] = {principal.principal_id}
        ingest_context = _context(
            rig.authorize(
                principal,
                ingest_grant,
                rig.issue(principal, ingest_grant),
            )
        )
        receipt = service.record_source(
            context=ingest_context,
            matter_id=MATTER,
            idempotency_key=f"real-fastapi-source-{index}",
            command=type(source_command(source)).model_validate(values),
        )
        repository.apply_projection(
            CorpusProjectionCommand(
                command_id=receipt.outbox_id,
                operation="create",
                source=receipt.source,
                payload_sha256=repository.outbox_records[-1].payload_sha256,
                core_watermark=42,
                projected_at=receipt.source.recorded_at,
            )
        )

    def principal_resolver(_: Request) -> PrincipalContext:
        return resolved_principal[0]

    def scope_resolver(request: Request) -> BoundaryScope:
        matter_id = UUID(str(request.path_params["matter_id"]))
        return BoundaryScope(
            tenant_id=resolved_principal[0].tenant_id,
            matter_id=matter_id,
            resource_id=str(matter_id),
        )

    def token(grant=search_grant, *, owner=principal) -> str:
        return raw_leaf(rig.issue(owner, grant))

    def body(cursor: str | None = None) -> dict[str, object]:
        result: dict[str, object] = {
            "query": "public synthetic",
            "mode": "full_text",
            "maxResults": 1,
            "expectedReleaseId": "synthetic-release-1",
            "expectedProjectionGeneration": 3,
            "requiredCoreWatermark": 42,
        }
        if cursor is not None:
            result["continuationCursor"] = cursor
        return result

    def post(client: TestClient, bearer: str, cursor: str | None = None):
        return client.post(
            f"/v1/matters/{MATTER}/corpus/search",
            json=body(cursor),
            headers={"Authorization": f"Bearer {bearer}"},
        )

    def assert_closed(response, status_code: int) -> None:
        assert response.status_code == status_code, response.text
        assert '"result"' not in response.text
        assert "synthetic-filing-record" not in response.text
        assert "synthetic-response-rule" not in response.text

    app = FastAPI()
    app.include_router(
        build_governed_corpus_router(
            service=service,
            authorizer=rig.authorizer,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        )
    )
    client = TestClient(app)
    try:
        token_one = token()
        first = post(client, token_one)
        assert first.status_code == 200, first.text
        cursor = first.json()["result"]["continuationCursor"]
        assert cursor

        replay = post(client, token_one, cursor)
        assert_closed(replay, 403)

        second = post(client, token(), cursor)
        assert second.status_code == 200, second.text
        first_id = first.json()["result"]["hits"][0]["source"]["sourceVersionId"]
        second_id = second.json()["result"]["hits"][0]["source"]["sourceVersionId"]
        assert first_id != second_id
        assert second.json()["result"]["continuationCursor"] is None

        other_principal = rig.principal()
        resolved_principal[0] = other_principal
        assert_closed(post(client, token()), 403)
        resolved_principal[0] = principal

        other_tenant_principal = rig.principal(tenant_id=OTHER_TENANT)
        other_tenant_grant = rig.grant(
            capability=Capability.CORPUS_SEARCH,
            purpose=Purpose.LEGAL_RESEARCH,
            target="api:governed_corpus.search",
            tenant_id=OTHER_TENANT,
            matter_id=MATTER,
            resource_id=str(MATTER),
        )
        resolved_principal[0] = other_tenant_principal
        assert_closed(
            post(
                client,
                token(other_tenant_grant, owner=other_tenant_principal),
            ),
            403,
        )
        resolved_principal[0] = principal

        wrong_scope = client.post(
            f"/v1/matters/{OTHER_MATTER}/corpus/search",
            json=body(),
            headers={"Authorization": f"Bearer {token()}"},
        )
        assert_closed(wrong_scope, 403)

        wrong_capability_grant = rig.grant(
            capability=Capability.CORPUS_ARTIFACT_READ,
            purpose=Purpose.LEGAL_RESEARCH,
            target="api:governed_corpus.span",
            matter_id=MATTER,
            resource_id=str(MATTER),
        )
        assert_closed(post(client, token(wrong_capability_grant)), 403)

        wrong_purpose_grant = rig.grant(
            capability=Capability.CORPUS_ARTIFACT_READ,
            purpose=Purpose.ISOLATED_QUARANTINE_REVIEW,
            target="api:governed_corpus.span",
            matter_id=MATTER,
            resource_id=str(MATTER),
        )
        assert_closed(post(client, token(wrong_purpose_grant)), 403)

        replacement = "A" if cursor[-1] != "A" else "B"
        assert_closed(post(client, token(), cursor[:-1] + replacement), 422)

        fresh_first = post(client, token())
        fresh_cursor = fresh_first.json()["result"]["continuationCursor"]
        policy.revision = "6" * 64
        assert_closed(post(client, token(), fresh_cursor), 422)
    finally:
        client.close()
        rig.close()
