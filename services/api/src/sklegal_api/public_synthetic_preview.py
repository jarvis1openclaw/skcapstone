"""Explicit process-local composition for the loopback MVP preview only."""

from __future__ import annotations

import hashlib
import json
from contextlib import AbstractContextManager
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from capauth.testing import STUB_ISSUER_FPR, signing_stub, stub_signature_for
from fastapi import Request
from sklegal_capauth import (
    CAPABILITY_RULES,
    Audience,
    BoundaryScope,
    Capability,
    CapabilityAuthorizer,
    CapabilityGrant,
    CapabilityIssuer,
    InMemoryAuditSink,
    InMemoryPrincipalPolicyBackend,
    InMemoryReplayBackend,
    InMemoryRevocationBackend,
    PrincipalContext,
    PrincipalType,
    Purpose,
    SignatureVerificationCache,
    StaticTrustedIssuerBackend,
)
from sklegal_policies import PolicyGovernanceService

from .app import (
    REQUIRED_DEPENDENCIES,
    DependencyProbe,
    MvpApiComposition,
    create_mvp_app,
)
from .browser_sessions import (
    BrowserSessionAuthentication,
    BrowserTenant,
    InMemoryPublicSyntheticSessionAuditSink,
    InMemoryPublicSyntheticSessionBackend,
)
from .claims import ClaimLedgerRead, InMemoryClaimLedgerStore
from .corpus import (
    CorpusResultRead,
    CorpusScopeOptionRead,
    CorpusSearchResponseRead,
    CorpusSpanAvailableRead,
    CorpusTraceRead,
    InMemoryCorpusResearchStore,
)
from .workspace import (
    ClientSummaryRead,
    InMemoryWorkspaceReadStore,
    MatterDetailRead,
    MatterWorkspaceRead,
)

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
PRINCIPAL_ID = UUID("22222222-2222-4222-8222-222222222221")
CLIENT_ID = UUID("33333333-3333-4333-8333-333333333331")
MATTER_ID = UUID("44444444-4444-4444-8444-444444444441")
FIXTURE_PATH = (
    Path(__file__).resolve().parents[4]
    / "tests/fixtures/mvp/public-synthetic-mvp-v1.json"
)
FIXTURE_SHA256 = "4b14516539289255daca9065dd28060a9e96aea365faec9ecbd1a679bdb47742"
CORPUS_QUERY = "invented delivery date"
CORPUS_SOURCE_ID = "public-synthetic-authority-primary"


def _load_fixture() -> dict[str, Any]:
    raw = FIXTURE_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256:
        raise RuntimeError("public synthetic preview fixture hash mismatch")
    fixture = json.loads(raw)
    meta = fixture.get("meta", {})
    identifiers = fixture.get("identifiers", {})
    if (
        meta.get("publicSynthetic") is not True
        or meta.get("classification") != "public"
        or meta.get("simulationOnly") is not True
        or identifiers.get("tenantId") != str(TENANT_ID)
        or identifiers.get("principalId") != str(PRINCIPAL_ID)
        or identifiers.get("clientId") != str(CLIENT_ID)
        or identifiers.get("matterId") != str(MATTER_ID)
    ):
        raise RuntimeError("public synthetic preview fixture boundary mismatch")
    return fixture


class _SyntheticSigner:
    @property
    def issuer_fingerprint(self) -> str:
        return STUB_ISSUER_FPR

    def sign(self, payload_bytes: bytes) -> str:
        return stub_signature_for(payload_bytes)


class _RefreshingPreviewSessions(InMemoryPublicSyntheticSessionBackend):
    """Mint one-use public-synthetic capabilities for each resolved request."""

    def __init__(self, *, issuer: CapabilityIssuer, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._issuer = issuer

    def _capabilities(self) -> dict[tuple[str, str], str]:
        definitions = (
            ("/v1/clients", Capability.CLIENT_READ, Purpose.CLIENT_SERVICE, None),
            (
                f"/v1/clients/{CLIENT_ID}",
                Capability.CLIENT_READ,
                Purpose.CLIENT_SERVICE,
                None,
            ),
            ("/v1/matters", Capability.CLIENT_READ, Purpose.CLIENT_SERVICE, None),
            (
                f"/v1/matters/{MATTER_ID}",
                Capability.MATTER_READ,
                Purpose.MATTER_MANAGEMENT,
                MATTER_ID,
            ),
            (
                f"/v1/matters/{MATTER_ID}/workspace",
                Capability.MATTER_READ,
                Purpose.MATTER_MANAGEMENT,
                MATTER_ID,
            ),
            (
                f"/v1/matters/{MATTER_ID}/claims",
                Capability.CLAIM_REVIEW,
                Purpose.CLAIM_REVIEW,
                MATTER_ID,
            ),
            (
                f"/v1/matters/{MATTER_ID}/corpus/search",
                Capability.CORPUS_SEARCH,
                Purpose.LEGAL_RESEARCH,
                MATTER_ID,
            ),
            (
                f"/v1/matters/{MATTER_ID}/corpus/sources/{CORPUS_SOURCE_ID}/span",
                Capability.CORPUS_ARTIFACT_READ,
                Purpose.LEGAL_RESEARCH,
                MATTER_ID,
            ),
        )
        targets = {
            "/v1/clients": "api:workspace.clients.list",
            f"/v1/clients/{CLIENT_ID}": "api:workspace.clients.get",
            "/v1/matters": "api:workspace.matters.list",
            f"/v1/matters/{MATTER_ID}": "api:workspace.matters.get",
            f"/v1/matters/{MATTER_ID}/workspace": "api:workspace.matters.workspace",
            f"/v1/matters/{MATTER_ID}/claims": "api:claims.ledger",
            f"/v1/matters/{MATTER_ID}/corpus/search": "api:corpus.search",
            f"/v1/matters/{MATTER_ID}/corpus/sources/{CORPUS_SOURCE_ID}/span": "api:corpus.span",
        }
        return {
            (
                "POST" if path.endswith("/corpus/search") else "GET",
                path,
            ): self._issuer.issue_root(
                principal=self._principal,
                grant=CapabilityGrant(
                    audience=Audience.API,
                    target=targets[path],
                    capability=capability,
                    tenant_id=TENANT_ID,
                    matter_id=matter_id,
                    resource_type=CAPABILITY_RULES[capability].resource_type,
                    resource_id=(str(matter_id) if matter_id is not None else None),
                    operation=CAPABILITY_RULES[capability].operation,
                    purpose=purpose,
                ),
                ttl_seconds=300,
                max_delegation_depth=0,
            ).credentials_for_verification()[-1]
            for path, capability, purpose, matter_id in definitions
        }

    def _with_fresh_capabilities(
        self, session: BrowserSessionAuthentication
    ) -> BrowserSessionAuthentication:
        return replace(session, capabilities_by_request=self._capabilities())

    def bootstrap(
        self, credential_reference: str, tenant_id: UUID
    ) -> BrowserSessionAuthentication:
        return self._with_fresh_capabilities(
            super().bootstrap(credential_reference, tenant_id)
        )

    def resolve(self, session_id: str) -> BrowserSessionAuthentication | None:
        session = super().resolve(session_id)
        return None if session is None else self._with_fresh_capabilities(session)


def build_public_synthetic_preview_app():
    """Build the deterministic development-only preview composition."""

    stub: AbstractContextManager[Any] = signing_stub()
    stub.__enter__()
    principals = InMemoryPrincipalPolicyBackend()
    principal = PrincipalContext(
        principal_id=PRINCIPAL_ID,
        principal_type=PrincipalType.HUMAN,
        subject="public-synthetic:mvp-preview-reviewer",
        tenant_id=TENANT_ID,
    )
    principals.set(principal, active=True)
    authorizer = CapabilityAuthorizer(
        trusted_issuers=StaticTrustedIssuerBackend({STUB_ISSUER_FPR}),
        principals=principals,
        revocations=InMemoryRevocationBackend(),
        replay=InMemoryReplayBackend(),
        audit=InMemoryAuditSink(),
        signature_cache=SignatureVerificationCache(),
    )
    issuer = CapabilityIssuer(_SyntheticSigner())
    fixture = _load_fixture()
    workspace = InMemoryWorkspaceReadStore()
    workspace.add_client(
        TENANT_ID,
        ClientSummaryRead.model_validate(fixture["clients"][0]),
    )
    workspace.add_matter(
        TENANT_ID,
        MatterDetailRead.model_validate(fixture["matters"][0]),
    )
    workspace.add_workspace(
        TENANT_ID,
        MatterWorkspaceRead.model_validate(fixture["workspace"]),
    )
    workspace.set_matter_members(TENANT_ID, MATTER_ID, frozenset({PRINCIPAL_ID}))
    claims = InMemoryClaimLedgerStore()
    claims.add_ledger(
        TENANT_ID,
        MATTER_ID,
        ClaimLedgerRead.model_validate(fixture["claimLedger"]),
    )
    claims.set_matter_members(TENANT_ID, MATTER_ID, frozenset({PRINCIPAL_ID}))
    authority = fixture["authorities"][0]
    corpus = InMemoryCorpusResearchStore()
    corpus.add_search(
        TENANT_ID,
        MATTER_ID,
        CORPUS_QUERY,
        CorpusSearchResponseRead(
            matter_id=MATTER_ID,
            query=CORPUS_QUERY,
            scope_options=(
                CorpusScopeOptionRead(scope="this_matter", state="active"),
                CorpusScopeOptionRead(
                    scope="tenant_corpus",
                    state="unavailable",
                    reason="Public synthetic preview is Matter scoped.",
                ),
                CorpusScopeOptionRead(
                    scope="official_sources",
                    state="unavailable",
                    reason="No external source retrieval is enabled.",
                ),
            ),
            results=(
                CorpusResultRead(
                    rank=1,
                    score=1.0,
                    snippet="Invented fixture authority for the invented delivery date.",
                    source_id=CORPUS_SOURCE_ID,
                    title=authority["title"],
                    citation=authority["citation"],
                    classification="public",
                    origin="matter_corpus",
                    verification_state=authority["verificationState"],
                    source_version=authority["sourceVersion"],
                    source_sha256=authority["contentSha256"],
                    document_id="public-synthetic-authority-document",
                    chunk_id="public-synthetic-authority-chunk-1",
                    chunk_sha256=authority["contentSha256"],
                    source_locator="fixture:authority:1",
                    span_kind="text_offset",
                    span_start=0,
                    span_end=67,
                    supersession_status="current",
                ),
            ),
            trace=CorpusTraceRead(
                scope_kind="this_matter",
                tenant_id=TENANT_ID,
                matter_id=MATTER_ID,
                release_id="public-synthetic-release-v1",
                projection_generation=1,
                current_projection_generation=1,
                projection_stale=False,
                backend_watermark=1,
                lag_events=0,
                lag_seconds=0.0,
                query_template_id="public-synthetic-query",
                query_template_version="v1",
                query_template_sha256="f" * 64,
                rank_path=("fixture_rank",),
                retrieval_adapter_version="public-synthetic-fixture-v1",
                source_ids=(CORPUS_SOURCE_ID,),
                source_hashes=(authority["contentSha256"],),
            ),
        ),
    )
    corpus.add_span(
        TENANT_ID,
        MATTER_ID,
        CorpusSpanAvailableRead(
            source_id=CORPUS_SOURCE_ID,
            source_version=authority["sourceVersion"],
            source_sha256=authority["contentSha256"],
            document_id="public-synthetic-authority-document",
            citation=authority["citation"],
            title=authority["title"],
            classification="public",
            source_locator="fixture:authority:1",
            span_kind="text_offset",
            span_start=0,
            span_end=67,
            span_text="Invented fixture authority for the invented delivery date only.",
            supersession_status="current",
            jurisdiction=authority["jurisdiction"],
        ),
    )
    corpus.set_matter_members(TENANT_ID, MATTER_ID, frozenset({PRINCIPAL_ID}))
    sessions = _RefreshingPreviewSessions(
        issuer=issuer,
        principal=principal,
        display_name="Public Synthetic Reviewer",
        tenants=(BrowserTenant(id=TENANT_ID, display_name="Synthetic Tenant"),),
        capability_names=("tenant.read", "client.read", "matter.read"),
        capabilities_by_request={},
    )

    def principal_resolver(request: Request) -> PrincipalContext:
        return request.state.browser_session.principal

    def scope_resolver(request: Request) -> BoundaryScope:
        raw_matter = request.path_params.get("matter_id")
        matter_id = UUID(str(raw_matter)) if raw_matter is not None else None
        return BoundaryScope(
            tenant_id=request.state.browser_session.active_tenant_id,
            matter_id=matter_id,
            resource_id=str(matter_id) if matter_id is not None else None,
        )

    composition = MvpApiComposition(
        workspace_store=workspace,
        claim_store=claims,
        corpus_store=corpus,
        governance_service=cast(PolicyGovernanceService, object()),
        authorizer=authorizer,
        principal_resolver=principal_resolver,
        scope_resolver=scope_resolver,
        probes=tuple(
            DependencyProbe(name=name, check=lambda: True, synthetic=True)
            for name in sorted(REQUIRED_DEPENDENCIES)
        ),
        mode="development",
        browser_sessions=sessions,
        browser_session_audit=InMemoryPublicSyntheticSessionAuditSink(),
    )
    app = create_mvp_app(composition)
    app.state.public_synthetic_preview = True
    app.state.public_synthetic_workspace_store = workspace
    app.state.public_synthetic_claim_store = claims
    app.state.public_synthetic_corpus_store = corpus
    app.state.signing_stub = stub
    return app


app = build_public_synthetic_preview_app()
