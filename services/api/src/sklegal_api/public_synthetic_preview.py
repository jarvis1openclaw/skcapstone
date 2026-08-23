"""Explicit process-local composition for the loopback MVP preview only."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import UTC, datetime
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
from .claims import InMemoryClaimLedgerStore
from .corpus import InMemoryCorpusResearchStore
from .workspace import (
    ClientSummaryRead,
    InMemoryWorkspaceReadStore,
    MatterDetailRead,
    MatterWorkspaceRead,
    WorkspaceMatterRead,
    WorkspaceProvenanceRead,
)

TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
PRINCIPAL_ID = UUID("30000000-0000-4000-8000-000000000001")
CLIENT_ID = UUID("40000000-0000-4000-8000-000000000101")
MATTER_ID = UUID("20000000-0000-4000-8000-000000000101")
OBSERVED_AT = datetime(2026, 8, 23, 9, 0, tzinfo=UTC)


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
        )
        targets = {
            "/v1/clients": "api:workspace.clients.list",
            f"/v1/clients/{CLIENT_ID}": "api:workspace.clients.get",
            "/v1/matters": "api:workspace.matters.list",
            f"/v1/matters/{MATTER_ID}": "api:workspace.matters.get",
            f"/v1/matters/{MATTER_ID}/workspace": "api:workspace.matters.workspace",
        }
        return {
            ("GET", path): self._issuer.issue_root(
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
    workspace = InMemoryWorkspaceReadStore()
    workspace.add_client(
        TENANT_ID,
        ClientSummaryRead(
            id=CLIENT_ID,
            tenant_id=TENANT_ID,
            display_name="Public Synthetic Client",
            matter_count=1,
        ),
    )
    workspace.add_matter(
        TENANT_ID,
        MatterDetailRead(
            id=MATTER_ID,
            tenant_id=TENANT_ID,
            client_id=CLIENT_ID,
            client_display_name="Public Synthetic Client",
            title="Public Synthetic Matter",
            status="open",
            summary="Synthetic demonstration records only.",
            opened_on="2026-08-23",
        ),
    )
    workspace.add_workspace(
        TENANT_ID,
        MatterWorkspaceRead(
            matter=WorkspaceMatterRead(
                matter_id=MATTER_ID,
                client_id=CLIENT_ID,
                client_display_name="Public Synthetic Client",
                title="Public Synthetic Matter",
                summary="Synthetic demonstration records only.",
                status="open",
                opened_at=OBSERVED_AT,
            ),
            provenance=WorkspaceProvenanceRead(
                source_snapshot="public-synthetic-preview-v1",
                current_source_snapshot="public-synthetic-preview-v1",
                adapter_version="public-synthetic-preview/v1",
                observed_at=OBSERVED_AT,
                stale=False,
                source_files=(),
            ),
        ),
    )
    workspace.set_matter_members(TENANT_ID, MATTER_ID, frozenset({PRINCIPAL_ID}))
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
        claim_store=InMemoryClaimLedgerStore(),
        corpus_store=InMemoryCorpusResearchStore(),
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
    app.state.signing_stub = stub
    return app


app = build_public_synthetic_preview_app()
