"""Loopback-only durable public-synthetic MVP composition.

The public fixture is the only synthetic data source. Session, identity,
authorization replay, revocation, audit, workspace, claim, outbox, policy
watermark, and retrieval projection state live in two independent PostgreSQL
clusters. Raw capability credentials are minted per request and never stored.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, local
from typing import Any, cast
from uuid import UUID

import psycopg
from fastapi import Request
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from sklegal_capauth import (
    CAPABILITY_RULES,
    Audience,
    AuthorizationDecision,
    BoundaryScope,
    Capability,
    CapabilityAuthorizer,
    CapabilityGrant,
    CapabilityIssuer,
    FileTrustedIssuerBackend,
    GpgAgentSigningHandle,
    IssuerCustodyPolicy,
    PrincipalContext,
    PrincipalPolicySnapshot,
    PrincipalType,
    Purpose,
    ReplayBackend,
    RevocationSnapshot,
    SignatureVerificationCache,
)
from sklegal_policies import PolicyGovernanceService, PolicyGovernanceUnavailable

from .app import (
    REQUIRED_DEPENDENCIES,
    DependencyProbe,
    MvpApiComposition,
    create_mvp_app,
)
from .browser_sessions import (
    PUBLIC_SYNTHETIC_CREDENTIAL_REFERENCE,
    SESSION_TTL,
    BrowserSessionAuditEvent,
    BrowserSessionAuthentication,
    BrowserSessionBackendUnavailable,
    BrowserTenant,
)
from .claims import ClaimLedgerRead, ClaimLedgerStoreUnavailable
from .corpus import (
    CorpusResearchStore,
    CorpusSearchResponseRead,
    CorpusSpanAvailableRead,
    CorpusSpanRead,
    CorpusStoreUnavailable,
)
from .workspace import (
    ClientDetailRead,
    ClientSummaryRead,
    MatterDetailRead,
    MatterSummaryRead,
    MatterWorkspaceRead,
    WorkspaceStoreUnavailable,
)

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
PRINCIPAL_ID = UUID("22222222-2222-4222-8222-222222222221")
MATTER_ID = UUID("44444444-4444-4444-8444-444444444441")
CLIENT_ID = UUID("33333333-3333-4333-8333-333333333331")
CORPUS_SOURCE_ID = "public-synthetic-authority-primary"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


class PostgresBoundary:
    """Small synchronous PostgreSQL boundary with transaction-local RLS scope."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._local = local()

    @contextmanager
    def transaction(
        self, tenant_id: UUID, matter_id: UUID | None = None
    ) -> Iterator[psycopg.Connection[Mapping[str, Any]]]:
        current = getattr(self._local, "connection", None)
        if current is not None:
            yield current
            return
        try:
            with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
                with connection.transaction():
                    connection.execute(
                        "SELECT set_config('sklegal.tenant_id', %s, true)",
                        (str(tenant_id),),
                    )
                    if matter_id is not None:
                        connection.execute(
                            "SELECT set_config('sklegal.matter_id', %s, true)",
                            (str(matter_id),),
                        )
                    self._local.connection = connection
                    try:
                        yield connection
                    finally:
                        self._local.connection = None
        except psycopg.Error:
            raise BrowserSessionBackendUnavailable(
                "PostgreSQL boundary unavailable"
            ) from None

    def one(
        self,
        tenant_id: UUID,
        query: str,
        parameters: tuple[object, ...] = (),
        *,
        matter_id: UUID | None = None,
    ) -> Mapping[str, Any] | None:
        with self.transaction(tenant_id, matter_id) as connection:
            return connection.execute(query, parameters).fetchone()

    def all(
        self,
        tenant_id: UUID,
        query: str,
        parameters: tuple[object, ...] = (),
        *,
        matter_id: UUID | None = None,
    ) -> list[Mapping[str, Any]]:
        with self.transaction(tenant_id, matter_id) as connection:
            return list(connection.execute(query, parameters).fetchall())

    def execute(
        self,
        tenant_id: UUID,
        query: str,
        parameters: tuple[object, ...] = (),
        *,
        matter_id: UUID | None = None,
    ) -> int:
        with self.transaction(tenant_id, matter_id) as connection:
            return connection.execute(query, parameters).rowcount

    def ready(self) -> bool:
        try:
            with psycopg.connect(self._dsn, connect_timeout=2) as connection:
                return connection.execute("SELECT 1").fetchone() == (1,)
        except Exception:
            return False


class DurablePrincipalBackend:
    def __init__(self, core: PostgresBoundary) -> None:
        self._core = core

    def snapshot(self, principal: PrincipalContext) -> PrincipalPolicySnapshot:
        row = self._core.one(
            principal.tenant_id,
            """SELECT principal_type, subject, active, revision
               FROM sklegal_mvp.principals
               WHERE tenant_id = %s AND principal_id = %s""",
            (principal.tenant_id, principal.principal_id),
        )
        if row is None:
            raise BrowserSessionBackendUnavailable("principal binding unavailable")
        current = PrincipalContext(
            principal_id=principal.principal_id,
            principal_type=PrincipalType(str(row["principal_type"])),
            subject=str(row["subject"]),
            tenant_id=principal.tenant_id,
        )
        return PrincipalPolicySnapshot(
            revision=str(row["revision"]), principal=current, active=bool(row["active"])
        )


class DurableRevocationBackend:
    def __init__(self, core: PostgresBoundary, tenant_id: UUID) -> None:
        self._core = core
        self._tenant_id = tenant_id

    def snapshot(self, credential_digests: tuple[str, ...]) -> RevocationSnapshot:
        rows = self._core.all(
            self._tenant_id,
            """SELECT credential_sha256 FROM sklegal_mvp.revocations
               WHERE tenant_id = %s ORDER BY credential_sha256""",
            (self._tenant_id,),
        )
        all_revoked = tuple(str(row["credential_sha256"]) for row in rows)
        requested = frozenset(credential_digests)
        return RevocationSnapshot(
            revision=hashlib.sha256(":".join(all_revoked).encode()).hexdigest(),
            revoked_credential_digests=frozenset(requested.intersection(all_revoked)),
        )


class DurableReplayBackend(ReplayBackend):
    def __init__(self, core: PostgresBoundary, tenant_id: UUID) -> None:
        self._core = core
        self._tenant_id = tenant_id

    def reserve(
        self, *, credential_digest: str, decision_id: str, expires_at: datetime
    ) -> bool:
        self._core.execute(
            self._tenant_id,
            "DELETE FROM sklegal_mvp.replay_reservations WHERE expires_at < now()",
        )
        return bool(
            self._core.execute(
                self._tenant_id,
                """INSERT INTO sklegal_mvp.replay_reservations
                       (tenant_id, credential_sha256, decision_id, expires_at)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (self._tenant_id, credential_digest, UUID(decision_id), expires_at),
            )
        )


class DurableAuthorizationAuditSink:
    def __init__(self, core: PostgresBoundary) -> None:
        self._core = core

    def record(self, decision: AuthorizationDecision) -> None:
        self._core.execute(
            decision.tenant_id,
            """INSERT INTO sklegal_mvp.audit_events
                   (tenant_id, matter_id, event_kind, event_id, correlation_id,
                    payload, occurred_at)
               VALUES (%s, %s, 'capauth', %s, %s, %s::jsonb, %s)""",
            (
                decision.tenant_id,
                decision.matter_id,
                decision.decision_id,
                decision.correlation_id,
                Jsonb(decision.model_dump(mode="json", exclude_none=True)),
                datetime.now(UTC),
            ),
        )


class DurableSessionAuditSink:
    synthetic = False

    def __init__(self, core: PostgresBoundary) -> None:
        self._core = core

    def execute(
        self,
        event_factory: Callable[..., BrowserSessionAuditEvent],
        mutation: Callable[[], Any],
    ) -> Any:
        event = event_factory("success", "session_operation_succeeded")
        try:
            with self._core.transaction(event.tenant_id) as connection:
                result = mutation()
                connection.execute(
                    """INSERT INTO sklegal_mvp.audit_events
                           (tenant_id, matter_id, event_kind, event_id, correlation_id,
                            payload, occurred_at)
                       VALUES (%s, NULL, 'session', %s, %s, %s::jsonb, %s)""",
                    (
                        event.tenant_id,
                        event.event_id,
                        event.correlation_id,
                        Jsonb(
                            {
                                "operation": event.operation,
                                "outcome": event.outcome,
                                "tenant_id": str(event.tenant_id),
                                "principal_id": str(event.principal_id)
                                if event.principal_id
                                else None,
                                "session_sha256": event.session_sha256,
                                "reason_code": event.reason_code,
                                "provenance_revision": event.provenance_revision,
                            }
                        ),
                        event.occurred_at,
                    ),
                )
                return result
        except BrowserSessionBackendUnavailable:
            raise
        except Exception:
            denied = event_factory("deny", "session_operation_denied")
            self._core.execute(
                denied.tenant_id,
                """INSERT INTO sklegal_mvp.audit_events
                       (tenant_id, matter_id, event_kind, event_id, correlation_id,
                        payload, occurred_at)
                   VALUES (%s, NULL, 'session', %s, %s, %s::jsonb, %s)""",
                (
                    denied.tenant_id,
                    denied.event_id,
                    denied.correlation_id,
                    Jsonb(
                        {
                            "operation": denied.operation,
                            "outcome": denied.outcome,
                            "tenant_id": str(denied.tenant_id),
                            "principal_id": str(denied.principal_id)
                            if denied.principal_id
                            else None,
                            "session_sha256": denied.session_sha256,
                            "reason_code": denied.reason_code,
                            "provenance_revision": denied.provenance_revision,
                        }
                    ),
                    denied.occurred_at,
                ),
            )
            raise


class DurableBrowserSessions:
    synthetic = False

    def __init__(self, core: PostgresBoundary, issuer: CapabilityIssuer) -> None:
        self._core = core
        self._issuer = issuer
        # A browser Matter load requests workspace and claims concurrently.
        # Keep gpg-agent signing serialized so both requests receive complete,
        # valid request-bound capability sets.
        self._capability_lock = Lock()
        self._principal = PrincipalContext(
            principal_id=PRINCIPAL_ID,
            principal_type=PrincipalType.HUMAN,
            subject="public-synthetic:durable-reviewer",
            tenant_id=TENANT_ID,
        )
        self._tenants = (BrowserTenant(id=TENANT_ID, display_name="Synthetic Tenant"),)

    def _capabilities(self) -> dict[tuple[str, str], str]:
        definitions = (
            (
                "GET",
                "/v1/clients",
                "api:workspace.clients.list",
                Capability.CLIENT_READ,
                Purpose.CLIENT_SERVICE,
                None,
            ),
            (
                "GET",
                f"/v1/clients/{CLIENT_ID}",
                "api:workspace.clients.get",
                Capability.CLIENT_READ,
                Purpose.CLIENT_SERVICE,
                None,
            ),
            (
                "GET",
                "/v1/matters",
                "api:workspace.matters.list",
                Capability.CLIENT_READ,
                Purpose.CLIENT_SERVICE,
                None,
            ),
            (
                "GET",
                f"/v1/matters/{MATTER_ID}",
                "api:workspace.matters.get",
                Capability.MATTER_READ,
                Purpose.MATTER_MANAGEMENT,
                MATTER_ID,
            ),
            (
                "GET",
                f"/v1/matters/{MATTER_ID}/workspace",
                "api:workspace.matters.workspace",
                Capability.MATTER_READ,
                Purpose.MATTER_MANAGEMENT,
                MATTER_ID,
            ),
            (
                "GET",
                f"/v1/matters/{MATTER_ID}/claims",
                "api:claims.ledger",
                Capability.CLAIM_REVIEW,
                Purpose.CLAIM_REVIEW,
                MATTER_ID,
            ),
            (
                "POST",
                f"/v1/matters/{MATTER_ID}/corpus/search",
                "api:corpus.search",
                Capability.CORPUS_SEARCH,
                Purpose.LEGAL_RESEARCH,
                MATTER_ID,
            ),
            (
                "GET",
                f"/v1/matters/{MATTER_ID}/corpus/sources/{CORPUS_SOURCE_ID}/span",
                "api:corpus.span",
                Capability.CORPUS_ARTIFACT_READ,
                Purpose.LEGAL_RESEARCH,
                MATTER_ID,
            ),
        )
        return {
            (method, path): self._issuer.issue_root(
                principal=self._principal,
                grant=CapabilityGrant(
                    audience=Audience.API,
                    target=target,
                    capability=capability,
                    tenant_id=TENANT_ID,
                    matter_id=matter_id,
                    resource_type=CAPABILITY_RULES[capability].resource_type,
                    resource_id=str(matter_id) if matter_id else None,
                    operation=CAPABILITY_RULES[capability].operation,
                    purpose=purpose,
                ),
                ttl_seconds=300,
                max_delegation_depth=0,
            ).credentials_for_verification()[-1]
            for method, path, target, capability, purpose, matter_id in definitions
        }

    def _authentication(
        self, session_id: str, csrf_token: str, expires_at: datetime
    ) -> BrowserSessionAuthentication:
        with self._capability_lock:
            capabilities = self._capabilities()
        return BrowserSessionAuthentication(
            session_id=session_id,
            principal=self._principal,
            display_name="Public Synthetic Reviewer",
            tenants=self._tenants,
            active_tenant_id=TENANT_ID,
            capability_names=(
                "tenant.read",
                "client.read",
                "matter.read",
                "claim.review",
                "corpus.read",
            ),
            capabilities_by_request=capabilities,
            csrf_digest=_digest(csrf_token),
            csrf_token=csrf_token,
            expires_at=expires_at,
        )

    def _new(self, tenant_id: UUID) -> BrowserSessionAuthentication:
        if tenant_id != TENANT_ID:
            raise PermissionError("tenant membership is absent")
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + SESSION_TTL
        self._core.execute(
            TENANT_ID,
            """INSERT INTO sklegal_mvp.browser_sessions
                   (session_sha256, tenant_id, principal_id, csrf_sha256,
                    csrf_token, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                _digest(session_id),
                TENANT_ID,
                PRINCIPAL_ID,
                _digest(csrf_token),
                csrf_token,
                expires_at,
            ),
        )
        return self._authentication(session_id, csrf_token, expires_at)

    def bootstrap(
        self, credential_reference: str, tenant_id: UUID
    ) -> BrowserSessionAuthentication:
        if credential_reference != PUBLIC_SYNTHETIC_CREDENTIAL_REFERENCE:
            raise PermissionError("credential reference is not approved")
        return self._new(tenant_id)

    def resolve(self, session_id: str) -> BrowserSessionAuthentication | None:
        row = self._core.one(
            TENANT_ID,
            """SELECT csrf_token, expires_at FROM sklegal_mvp.browser_sessions
               WHERE session_sha256 = %s AND tenant_id = %s AND expires_at > now()""",
            (_digest(session_id), TENANT_ID),
        )
        if row is None:
            return None
        return self._authentication(
            session_id, str(row["csrf_token"]), cast(datetime, row["expires_at"])
        )

    def rotate(
        self, session: BrowserSessionAuthentication
    ) -> BrowserSessionAuthentication:
        self.revoke(session.session_id)
        return self._new(session.active_tenant_id)

    def switch_tenant(
        self, session: BrowserSessionAuthentication, tenant_id: UUID
    ) -> BrowserSessionAuthentication:
        if tenant_id != TENANT_ID:
            raise PermissionError("tenant membership is absent")
        return self.rotate(session)

    def revoke(self, session_id: str) -> None:
        self._core.execute(
            TENANT_ID,
            "DELETE FROM sklegal_mvp.browser_sessions WHERE session_sha256 = %s",
            (_digest(session_id),),
        )


class DurableWorkspaceStore:
    def __init__(self, core: PostgresBoundary) -> None:
        self._core = core

    def _require_current_policy(self, tenant_id: UUID) -> None:
        row = self._core.one(
            tenant_id,
            """SELECT stale FROM sklegal_mvp.policy_state
               WHERE tenant_id = %s""",
            (tenant_id,),
        )
        if row is None or row["stale"] is not False:
            raise WorkspaceStoreUnavailable("current policy is unavailable")

    def _member(self, tenant_id: UUID, matter_id: UUID, principal_id: UUID) -> bool:
        self._require_current_policy(tenant_id)
        return (
            self._core.one(
                tenant_id,
                """SELECT 1 FROM sklegal_mvp.matter_memberships
               WHERE tenant_id = %s AND matter_id = %s AND principal_id = %s""",
                (tenant_id, matter_id, principal_id),
            )
            is not None
        )

    def _records(self, tenant_id: UUID, kind: str) -> list[Mapping[str, Any]]:
        try:
            self._require_current_policy(tenant_id)
            return self._core.all(
                tenant_id,
                """SELECT payload FROM sklegal_mvp.records
                   WHERE tenant_id = %s AND record_kind = %s ORDER BY record_id""",
                (tenant_id, kind),
            )
        except BrowserSessionBackendUnavailable:
            raise WorkspaceStoreUnavailable("workspace unavailable") from None

    def list_clients(
        self, tenant_id: UUID, principal_id: UUID
    ) -> tuple[ClientSummaryRead, ...]:
        matters = self.list_matters(tenant_id, principal_id)
        return tuple(
            ClientSummaryRead.model_validate(
                {
                    **row["payload"],
                    "matterCount": sum(
                        m.client_id == UUID(str(row["payload"]["id"])) for m in matters
                    ),
                }
            )
            for row in self._records(tenant_id, "client")
        )

    def get_client(
        self, tenant_id: UUID, client_id: UUID, principal_id: UUID
    ) -> ClientDetailRead | None:
        clients = [
            item
            for item in self.list_clients(tenant_id, principal_id)
            if item.id == client_id
        ]
        if not clients:
            return None
        matters = tuple(
            item
            for item in self.list_matters(tenant_id, principal_id)
            if item.client_id == client_id
        )
        return ClientDetailRead(**clients[0].model_dump(), matters=matters)

    def list_matters(
        self, tenant_id: UUID, principal_id: UUID
    ) -> tuple[MatterSummaryRead, ...]:
        return tuple(
            MatterSummaryRead.model_validate(row["payload"])
            for row in self._records(tenant_id, "matter")
            if self._member(tenant_id, UUID(str(row["payload"]["id"])), principal_id)
        )

    def get_matter(self, tenant_id: UUID, matter_id: UUID) -> MatterDetailRead | None:
        self._require_current_policy(tenant_id)
        row = self._core.one(
            tenant_id,
            """SELECT payload FROM sklegal_mvp.records
               WHERE tenant_id = %s AND record_kind = 'matter' AND record_id = %s""",
            (tenant_id, str(matter_id)),
        )
        return None if row is None else MatterDetailRead.model_validate(row["payload"])

    def get_matter_workspace(
        self, tenant_id: UUID, matter_id: UUID
    ) -> MatterWorkspaceRead | None:
        self._require_current_policy(tenant_id)
        row = self._core.one(
            tenant_id,
            """SELECT payload FROM sklegal_mvp.records
               WHERE tenant_id = %s AND record_kind = 'workspace' AND record_id = %s""",
            (tenant_id, str(matter_id)),
        )
        return (
            None if row is None else MatterWorkspaceRead.model_validate(row["payload"])
        )

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        return self._member(tenant_id, matter_id, principal_id)


class DurableClaimStore:
    def __init__(
        self, core: PostgresBoundary, workspace: DurableWorkspaceStore
    ) -> None:
        self._core = core
        self._workspace = workspace

    def ledger(self, tenant_id: UUID, matter_id: UUID) -> ClaimLedgerRead | None:
        try:
            row = self._core.one(
                tenant_id,
                """SELECT payload FROM sklegal_mvp.records
                   WHERE tenant_id = %s AND record_kind = 'claims' AND record_id = %s""",
                (tenant_id, str(matter_id)),
            )
        except BrowserSessionBackendUnavailable:
            raise ClaimLedgerStoreUnavailable("claim ledger unavailable") from None
        return None if row is None else ClaimLedgerRead.model_validate(row["payload"])

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        return self._workspace.is_matter_member(tenant_id, matter_id, principal_id)


class DurableCorpusStore(CorpusResearchStore):
    def __init__(
        self,
        core: PostgresBoundary,
        retrieval: PostgresBoundary,
        workspace: DurableWorkspaceStore,
    ) -> None:
        self._core = core
        self._retrieval = retrieval
        self._workspace = workspace

    def search(
        self, tenant_id: UUID, matter_id: UUID, query: str
    ) -> CorpusSearchResponseRead | None:
        try:
            row = self._retrieval.one(
                tenant_id,
                """SELECT search_payload FROM sklegal_retrieval.projections
                   WHERE tenant_id = %s AND matter_id = %s AND query = %s
                   ORDER BY generation DESC LIMIT 1""",
                (tenant_id, matter_id, query),
                matter_id=matter_id,
            )
        except BrowserSessionBackendUnavailable:
            raise CorpusStoreUnavailable("retrieval unavailable") from None
        return (
            None
            if row is None
            else CorpusSearchResponseRead.model_validate(row["search_payload"])
        )

    def get_span(
        self, tenant_id: UUID, matter_id: UUID, source_id: str
    ) -> CorpusSpanRead | None:
        if source_id != CORPUS_SOURCE_ID:
            return None
        try:
            row = self._retrieval.one(
                tenant_id,
                """SELECT span_payload FROM sklegal_retrieval.projections
                   WHERE tenant_id = %s AND matter_id = %s
                   ORDER BY generation DESC LIMIT 1""",
                (tenant_id, matter_id),
                matter_id=matter_id,
            )
        except BrowserSessionBackendUnavailable:
            raise CorpusStoreUnavailable("retrieval unavailable") from None
        return (
            None
            if row is None
            else cast(
                CorpusSpanRead,
                CorpusSpanAvailableRead.model_validate(row["span_payload"]),
            )
        )

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        return self._workspace.is_matter_member(tenant_id, matter_id, principal_id)


class ReadOnlyDurableGovernance:
    requirement_for = staticmethod(PolicyGovernanceService.requirement_for)

    def execute(self, *_: object) -> None:
        raise PolicyGovernanceUnavailable(
            "public-synthetic policy mutations are disabled"
        )


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def build_durable_public_synthetic_app():
    core = PostgresBoundary(_required("SKLEGAL_MVP_CORE_DSN"))
    retrieval = PostgresBoundary(_required("SKLEGAL_MVP_RETRIEVAL_DSN"))
    custody = IssuerCustodyPolicy(
        issuer_fingerprint=_required("SKLEGAL_MVP_ISSUER_FINGERPRINT"),
        custody="gpg-agent",
        key_home=_required("SKLEGAL_MVP_GNUPGHOME"),
    )
    issuer = CapabilityIssuer(GpgAgentSigningHandle(custody))
    trusted_issuers = FileTrustedIssuerBackend(
        Path(_required("SKLEGAL_MVP_ISSUER_POLICY"))
    )
    authorizer = CapabilityAuthorizer(
        trusted_issuers=trusted_issuers,
        principals=DurablePrincipalBackend(core),
        revocations=DurableRevocationBackend(core, TENANT_ID),
        replay=DurableReplayBackend(core, TENANT_ID),
        audit=DurableAuthorizationAuditSink(core),
        signature_cache=SignatureVerificationCache(),
    )
    workspace = DurableWorkspaceStore(core)
    sessions = DurableBrowserSessions(core, issuer)

    def principal_resolver(request: Request) -> PrincipalContext:
        return request.state.browser_session.principal

    def scope_resolver(request: Request) -> BoundaryScope:
        raw = request.path_params.get("matter_id")
        matter_id = UUID(str(raw)) if raw is not None else None
        return BoundaryScope(
            tenant_id=request.state.browser_session.active_tenant_id,
            matter_id=matter_id,
            resource_id=str(matter_id) if matter_id else None,
        )

    def policy_ready() -> bool:
        row = core.one(
            TENANT_ID,
            "SELECT stale FROM sklegal_mvp.policy_state WHERE tenant_id = %s",
            (TENANT_ID,),
        )
        return row is not None and row["stale"] is False

    probes = {
        "authentication": core.ready,
        "policy": policy_ready,
        "audit": core.ready,
        "persistence": core.ready,
        "provenance": retrieval.ready,
    }
    composition = MvpApiComposition(
        workspace_store=workspace,
        claim_store=DurableClaimStore(core, workspace),
        corpus_store=DurableCorpusStore(core, retrieval, workspace),
        governance_service=cast(PolicyGovernanceService, ReadOnlyDurableGovernance()),
        authorizer=authorizer,
        principal_resolver=principal_resolver,
        scope_resolver=scope_resolver,
        probes=tuple(
            DependencyProbe(name=name, check=probes[name])
            for name in sorted(REQUIRED_DEPENDENCIES)
        ),
        mode="production",
        browser_sessions=sessions,
        browser_session_audit=DurableSessionAuditSink(core),
    )
    app = create_mvp_app(composition)
    app.state.durable_public_synthetic = True
    return app


app = build_durable_public_synthetic_app()
