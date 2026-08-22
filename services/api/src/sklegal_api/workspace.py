"""CapAuth-gated client and matter workspace read API (SKL-S4-02).

Read side of the client and matter workspace: clients, engagements,
matters, parties, timeline events, fact assertions, tension groups,
evidence items, communications, record gaps, execution states, version
lineage, audit, and provenance. Every route runs inside the S1-03
CapAuth protected-route boundary with an exact fixed capability
contract, the tenant scope comes only from the authenticated principal,
and matter membership is enforced before any matter record is read.
All failures are sanitized and fail closed: an unavailable store yields
503, a missing record yields 404, and a membership or capability denial
yields 403 with no record detail.

Hidden UI never substitutes for this authorization: the web shell gates
routes for usability only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sklegal_capauth import (
    ApiCapabilityBoundary,
    AuthorizedContext,
    Capability,
    CapabilityAuthorizer,
    Purpose,
)

from .capauth import (
    PrincipalResolver,
    ProtectedRouteDependency,
    ScopeResolver,
)


class WorkspaceStoreUnavailable(RuntimeError):
    """The workspace read store has no current answer; fail closed."""


class WorkspaceReadModel(BaseModel):
    """Base read model: snake_case internally, camelCase on the wire."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ClientSummaryRead(WorkspaceReadModel):
    id: UUID
    tenant_id: UUID
    display_name: str
    matter_count: int


class MatterSummaryRead(WorkspaceReadModel):
    id: UUID
    tenant_id: UUID
    client_id: UUID
    client_display_name: str
    title: str
    status: str
    """Lifecycle status value from the domain MatterStatus vocabulary."""


class ClientDetailRead(ClientSummaryRead):
    matters: tuple[MatterSummaryRead, ...]


class MatterDetailRead(MatterSummaryRead):
    engagement_id: UUID | None = None
    summary: str = ""
    opened_on: str | None = None
    legacy_aliases: tuple[str, ...] = ()


class WorkspaceMatterRead(WorkspaceReadModel):
    """Matter header carried inside the workspace aggregate."""

    matter_id: UUID
    client_id: UUID
    client_display_name: str
    engagement_id: UUID | None = None
    title: str
    summary: str
    status: str
    opened_at: datetime | None = None
    legacy_aliases: tuple[str, ...] = ()


class PartyRead(WorkspaceReadModel):
    party_id: UUID
    display_name: str
    party_kind: str
    roles: tuple[str, ...]
    status: str
    """Verification status value from the domain VerificationStatus vocabulary."""


class TimelineEventRead(WorkspaceReadModel):
    event_id: UUID
    event_type: str
    description: str
    occurred_at: datetime | None = None
    observed_at: datetime
    status: str
    """Matter event status value from the domain MatterEventStatus vocabulary."""
    source_path: str | None = None
    legacy_aliases: tuple[str, ...] = ()


class FactAssertionRead(WorkspaceReadModel):
    fact_assertion_id: UUID
    predicate: str
    asserted_value: Any
    value_type: str
    review_status: str
    """Review status value from the domain FactReviewStatus vocabulary."""
    source_path: str | None = None
    source_locator: str
    source_missing: bool = False
    tension_group_key: str | None = None


class TensionGroupRead(WorkspaceReadModel):
    tension_key: str
    status: str
    """Tension status value from the domain TensionStatus vocabulary."""
    assertion_ids: tuple[UUID, ...]
    review_required: bool


class EvidenceItemRead(WorkspaceReadModel):
    evidence_item_id: UUID
    title: str
    media_type: str
    content_sha256: str
    status: str
    """Evidence status value from the domain EvidenceStatus vocabulary."""
    source_path: str | None = None
    source_missing: bool = False


class CommunicationRead(WorkspaceReadModel):
    communication_id: UUID
    channel: str
    summary: str
    occurred_at: datetime | None = None
    status: str
    """Communication status value from the domain CommunicationStatus vocabulary."""
    source_path: str | None = None
    source_missing: bool = False


class DraftSentenceRead(WorkspaceReadModel):
    """One factual sentence and its exact claim-ledger grounding state."""

    sentence_key: str
    text: str
    grounding_status: Literal[
        "grounded",
        "ungrounded",
        "deferred_unknown",
        "claim_withdrawn",
        "claim_missing",
    ]
    claim_id: UUID | None = None
    claim_statement: str | None = None
    claim_status: str | None = None
    warning: str | None = None


class DraftCompareRowRead(WorkspaceReadModel):
    """One sentence-level row in the previous-to-current version compare."""

    change: Literal["unchanged", "added", "removed"]
    previous_text: str | None = None
    current_text: str | None = None


class WorkProductVersionRead(WorkspaceReadModel):
    version_id: UUID
    version_number: int
    content_sha256: str
    status: str
    content: str
    sentences: tuple[DraftSentenceRead, ...] = ()
    compare_rows: tuple[DraftCompareRowRead, ...] = ()


class ApprovalBindingRead(WorkspaceReadModel):
    """Exact version triple named by a recorded Approval."""

    version_id: UUID
    version_number: int
    content_sha256: str


class WorkProductRead(WorkspaceReadModel):
    work_product_id: UUID
    title: str
    work_product_kind: str
    status: str
    current_version: WorkProductVersionRead
    previous_version_number: int | None = None
    approval_binding: ApprovalBindingRead | None = None


class VersionLineageRead(WorkspaceReadModel):
    packet_version: int
    source_path: str
    source_sha256: str
    historical: bool
    current_review_baseline: bool


class ExecutionStateRead(WorkspaceReadModel):
    """One approval or execution state for a workspace target.

    Negative states (``pending_review``, ``not_started``) are first-class
    read data and are always returned; the workspace never hides them.
    """

    target_type: str
    target_id: UUID
    state_kind: str
    state_value: str


class WorkspaceGapRead(WorkspaceReadModel):
    """An explicit incomplete state; gaps are rendered, never hidden."""

    gap_id: str
    kind: str
    description: str


class AuditEntryRead(WorkspaceReadModel):
    audit_id: str
    action: str
    actor: str
    occurred_at: datetime
    outcome: str
    detail: str | None = None


class SourceFileRead(WorkspaceReadModel):
    relative_path: str
    content_sha256: str
    observed_at: datetime
    byte_count: int | None = None


class WorkspaceProvenanceRead(WorkspaceReadModel):
    source_snapshot: str
    current_source_snapshot: str | None = None
    adapter_version: str
    observed_at: datetime
    stale: bool
    source_files: tuple[SourceFileRead, ...]


class MatterWorkspaceRead(WorkspaceReadModel):
    """Full matter workspace aggregate in legal-domain terminology."""

    matter: WorkspaceMatterRead
    engagement_display_name: str | None = None
    parties: tuple[PartyRead, ...] = ()
    timeline: tuple[TimelineEventRead, ...] = ()
    facts: tuple[FactAssertionRead, ...] = ()
    tensions: tuple[TensionGroupRead, ...] = ()
    evidence: tuple[EvidenceItemRead, ...] = ()
    communications: tuple[CommunicationRead, ...] = ()
    work_products: tuple[WorkProductRead, ...] = ()
    version_lineage: tuple[VersionLineageRead, ...] = ()
    execution_states: tuple[ExecutionStateRead, ...] = ()
    gaps: tuple[WorkspaceGapRead, ...] = ()
    audit: tuple[AuditEntryRead, ...] = ()
    provenance: WorkspaceProvenanceRead


class WorkspaceReadStore(Protocol):
    """Tenant-scoped read contract behind the workspace routes.

    Every method is keyed by the authenticated principal's tenant, never
    by request content. Membership-aware reads take the principal id and
    must return only records the principal is a member of. Implementations
    raise WorkspaceStoreUnavailable when they have no current answer.
    """

    def list_clients(
        self, tenant_id: UUID, principal_id: UUID
    ) -> tuple[ClientSummaryRead, ...]: ...

    def get_client(
        self, tenant_id: UUID, client_id: UUID, principal_id: UUID
    ) -> ClientDetailRead | None: ...

    def list_matters(
        self, tenant_id: UUID, principal_id: UUID
    ) -> tuple[MatterSummaryRead, ...]: ...

    def get_matter(
        self, tenant_id: UUID, matter_id: UUID
    ) -> MatterDetailRead | None: ...

    def get_matter_workspace(
        self, tenant_id: UUID, matter_id: UUID
    ) -> MatterWorkspaceRead | None: ...

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool: ...


class InMemoryWorkspaceReadStore:
    """Deterministic in-memory workspace store for composition and tests.

    Membership fails closed: a matter with no recorded roster denies every
    principal. Set ``available = False`` to simulate a backend outage; all
    reads then raise WorkspaceStoreUnavailable.
    """

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self._clients: dict[tuple[UUID, UUID], ClientSummaryRead] = {}
        self._matters: dict[tuple[UUID, UUID], MatterDetailRead] = {}
        self._workspaces: dict[tuple[UUID, UUID], MatterWorkspaceRead] = {}
        self._members: dict[tuple[UUID, UUID], frozenset[UUID]] = {}

    def _require_available(self) -> None:
        if not self.available:
            raise WorkspaceStoreUnavailable("workspace read store unavailable")

    def add_client(self, tenant_id: UUID, client: ClientSummaryRead) -> None:
        self._clients[(tenant_id, client.id)] = client

    def add_matter(self, tenant_id: UUID, matter: MatterDetailRead) -> None:
        self._matters[(tenant_id, matter.id)] = matter

    def add_workspace(self, tenant_id: UUID, view: MatterWorkspaceRead) -> None:
        self._workspaces[(tenant_id, view.matter.matter_id)] = view

    def set_matter_members(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        principal_ids: frozenset[UUID],
    ) -> None:
        self._members[(tenant_id, matter_id)] = principal_ids

    def _member_matters(
        self, tenant_id: UUID, principal_id: UUID
    ) -> list[MatterDetailRead]:
        return [
            matter
            for (tenant, matter_id), matter in self._matters.items()
            if tenant == tenant_id
            and self.is_matter_member(tenant_id, matter_id, principal_id)
        ]

    def list_clients(
        self, tenant_id: UUID, principal_id: UUID
    ) -> tuple[ClientSummaryRead, ...]:
        self._require_available()
        visible = self._member_matters(tenant_id, principal_id)
        clients: list[ClientSummaryRead] = []
        for (tenant, _client_id), client in sorted(
            self._clients.items(), key=lambda item: str(item[0][1])
        ):
            if tenant != tenant_id:
                continue
            count = sum(1 for matter in visible if matter.client_id == client.id)
            clients.append(
                ClientSummaryRead(
                    id=client.id,
                    tenant_id=client.tenant_id,
                    display_name=client.display_name,
                    matter_count=count,
                )
            )
        return tuple(clients)

    def get_client(
        self, tenant_id: UUID, client_id: UUID, principal_id: UUID
    ) -> ClientDetailRead | None:
        self._require_available()
        client = self._clients.get((tenant_id, client_id))
        if client is None:
            return None
        matters = tuple(
            MatterSummaryRead(
                id=matter.id,
                tenant_id=matter.tenant_id,
                client_id=matter.client_id,
                client_display_name=matter.client_display_name,
                title=matter.title,
                status=matter.status,
            )
            for matter in self._member_matters(tenant_id, principal_id)
            if matter.client_id == client_id
        )
        return ClientDetailRead(
            id=client.id,
            tenant_id=client.tenant_id,
            display_name=client.display_name,
            matter_count=len(matters),
            matters=matters,
        )

    def list_matters(
        self, tenant_id: UUID, principal_id: UUID
    ) -> tuple[MatterSummaryRead, ...]:
        self._require_available()
        return tuple(
            MatterSummaryRead(
                id=matter.id,
                tenant_id=matter.tenant_id,
                client_id=matter.client_id,
                client_display_name=matter.client_display_name,
                title=matter.title,
                status=matter.status,
            )
            for matter in self._member_matters(tenant_id, principal_id)
        )

    def get_matter(self, tenant_id: UUID, matter_id: UUID) -> MatterDetailRead | None:
        self._require_available()
        return self._matters.get((tenant_id, matter_id))

    def get_matter_workspace(
        self, tenant_id: UUID, matter_id: UUID
    ) -> MatterWorkspaceRead | None:
        self._require_available()
        return self._workspaces.get((tenant_id, matter_id))

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        self._require_available()
        roster = self._members.get((tenant_id, matter_id))
        return roster is not None and principal_id in roster


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "workspace_unavailable"},
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "not_found"},
    )


def _membership_denied() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "matter_membership_denied"},
    )


def _path_uuid(request: Request, name: str) -> UUID:
    try:
        return UUID(str(request.path_params[name]))
    except (KeyError, ValueError):
        raise _not_found() from None


def _dump(model: WorkspaceReadModel | tuple[WorkspaceReadModel, ...]) -> Any:
    if isinstance(model, tuple):
        return [item.model_dump(mode="json", by_alias=True) for item in model]
    return model.model_dump(mode="json", by_alias=True)


def build_workspace_router(
    *,
    store: WorkspaceReadStore,
    authorizer: CapabilityAuthorizer,
    principal_resolver: PrincipalResolver,
    scope_resolver: ScopeResolver,
) -> APIRouter:
    """Build the client and matter workspace read router.

    Client list and detail routes require ``client.read``; the matter
    list is a tenant-level client-service read; matter detail and the
    matter workspace require a ``matter.read`` capability scoped to the
    exact matter in the path, plus recorded matter membership. The scope
    resolver supplies trusted tenant and matter scope only; request
    content never selects a tenant.
    """

    router = APIRouter()

    def dependency(capability: Capability, purpose: Purpose, name: str) -> Any:
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=authorizer,
            route_name=f"workspace.{name}",
            capability=capability,
            purpose=purpose,
        )
        return ProtectedRouteDependency(
            boundary=boundary,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        )

    def require_membership(authorized: AuthorizedContext, matter_id: UUID) -> None:
        try:
            member = store.is_matter_member(
                authorized.principal.tenant_id,
                matter_id,
                authorized.principal.principal_id,
            )
        except Exception:
            raise _unavailable() from None
        if not member:
            raise _membership_denied() from None

    async def list_clients(
        authorized: AuthorizedContext = Depends(
            dependency(Capability.CLIENT_READ, Purpose.CLIENT_SERVICE, "clients.list")
        ),
    ) -> Any:
        try:
            clients = store.list_clients(
                authorized.principal.tenant_id, authorized.principal.principal_id
            )
        except Exception:
            raise _unavailable() from None
        return _dump(clients)

    async def get_client(
        request: Request,
        authorized: AuthorizedContext = Depends(
            dependency(Capability.CLIENT_READ, Purpose.CLIENT_SERVICE, "clients.get")
        ),
    ) -> Any:
        client_id = _path_uuid(request, "client_id")
        try:
            client = store.get_client(
                authorized.principal.tenant_id,
                client_id,
                authorized.principal.principal_id,
            )
        except Exception:
            raise _unavailable() from None
        if client is None:
            raise _not_found() from None
        return _dump(client)

    async def list_matters(
        authorized: AuthorizedContext = Depends(
            dependency(Capability.CLIENT_READ, Purpose.CLIENT_SERVICE, "matters.list")
        ),
    ) -> Any:
        try:
            matters = store.list_matters(
                authorized.principal.tenant_id, authorized.principal.principal_id
            )
        except Exception:
            raise _unavailable() from None
        return _dump(matters)

    async def get_matter(
        request: Request,
        authorized: AuthorizedContext = Depends(
            dependency(Capability.MATTER_READ, Purpose.MATTER_MANAGEMENT, "matters.get")
        ),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        require_membership(authorized, matter_id)
        try:
            matter = store.get_matter(authorized.principal.tenant_id, matter_id)
        except Exception:
            raise _unavailable() from None
        if matter is None:
            raise _not_found() from None
        return _dump(matter)

    async def get_matter_workspace(
        request: Request,
        authorized: AuthorizedContext = Depends(
            dependency(
                Capability.MATTER_READ,
                Purpose.MATTER_MANAGEMENT,
                "matters.workspace",
            )
        ),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        require_membership(authorized, matter_id)
        try:
            view = store.get_matter_workspace(authorized.principal.tenant_id, matter_id)
        except Exception:
            raise _unavailable() from None
        if view is None:
            raise _not_found() from None
        return _dump(view)

    router.get("/v1/clients", operation_id="workspace_clients_list")(list_clients)
    router.get("/v1/clients/{client_id}", operation_id="workspace_clients_get")(
        get_client
    )
    router.get("/v1/matters", operation_id="workspace_matters_list")(list_matters)
    router.get("/v1/matters/{matter_id}", operation_id="workspace_matters_get")(
        get_matter
    )
    router.get(
        "/v1/matters/{matter_id}/workspace",
        operation_id="workspace_matters_workspace",
    )(get_matter_workspace)

    return router
