"""CapAuth-gated corpus research read API (SKL-S4-03A).

Read side of the /corpus research surface: a governed matter-scoped search
over the retrieval corpus and the exact source-span viewer. Search results
carry the full S2-10 retrieval trace (tenant and matter scope, release,
source hashes, projection generation, query template, backend watermark,
and rank path) so every row can be traced to the projection generation
and release that produced it.

Every route runs inside the S1-03 CapAuth protected-route boundary with an
exact fixed capability contract: corpus search requires ``corpus.search``
for legal research and the span viewer requires ``corpus.artifact.read``.
The tenant scope comes only from the authenticated principal, matter
membership is enforced before any corpus record is read, and all failures
are sanitized and fail closed: an unavailable store yields 503, an
unknown matter or source yields 404, and a membership or capability denial
yields 403 with no record detail.

Denial states are structural, not cosmetic: an inaccessible source is
recorded in the store as a denial descriptor with no span text, so the
span viewer can render why access was refused without any possibility of
leaking protected content. Corpus results are research proposals, not
verified Authority; the origin and verification state of every row says
so explicitly and official-source verification stays a separate lane.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
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


class CorpusStoreUnavailable(RuntimeError):
    """The corpus research read store has no current answer; fail closed."""


class CorpusReadModel(BaseModel):
    """Base read model: snake_case internally, camelCase on the wire."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CorpusSearchRequest(CorpusReadModel):
    """Governed search request: the query text only.

    Scope, tenant, and matter come from the authenticated principal and
    the route path, never from the body, so no request field can widen
    the governed matter scope.
    """

    query: str = Field(min_length=1, max_length=2048)


class CorpusScopeOptionRead(CorpusReadModel):
    """One selectable scope chip in the governed search bar.

    Gated scopes are listed with an explicit unavailable state and reason
    instead of being silently dropped, so the bar always shows the full
    scope model the policy knows about.
    """

    scope: str
    """Scope key: this_matter, tenant_corpus, or official_sources."""
    state: str
    """Selection state: active or unavailable."""
    reason: str | None = None


class CorpusTraceRead(CorpusReadModel):
    """The full S2-10 retrieval trace behind one search response.

    Projection staleness is reported, never harmonized: the generation the
    results were read from and the current generation are both carried so
    the UI can render a caution when they disagree.
    """

    scope_kind: str
    tenant_id: UUID
    matter_id: UUID
    release_id: str
    projection_generation: int
    current_projection_generation: int
    projection_stale: bool
    backend_watermark: int
    lag_events: int
    lag_seconds: float
    query_template_id: str
    """Query template identifier from the retrieval vocabulary."""
    query_template_version: str
    query_template_sha256: str
    rank_path: tuple[str, ...]
    """Ordered rank signals, for example lexical_rank then scope_aggregate."""
    retrieval_adapter_version: str
    source_ids: tuple[str, ...]
    source_hashes: tuple[str, ...]


class CorpusResultRead(CorpusReadModel):
    """One ranked corpus row with its exact source locator."""

    rank: int
    score: float
    snippet: str
    source_id: str
    title: str
    citation: str
    classification: str
    origin: str
    """Retrieval lane this row came from; this slice only serves matter_corpus."""
    verification_state: str
    """A corpus row is an unverified research proposal, never Authority."""
    source_version: str
    source_sha256: str
    document_id: str
    chunk_id: str
    chunk_sha256: str
    source_locator: str
    span_kind: str
    span_start: int
    span_end: int
    span_page: int | None = None
    supersession_status: str
    """Supersession status value: current or superseded."""


class CorpusSearchResponseRead(CorpusReadModel):
    matter_id: UUID
    query: str
    scope_options: tuple[CorpusScopeOptionRead, ...]
    results: tuple[CorpusResultRead, ...]
    trace: CorpusTraceRead


class CorpusSpanAvailableRead(CorpusReadModel):
    """An accessible exact source span with its provenance.

    ``span_text`` is the exact bytes-backed text of the pinned locator;
    the UI renders it highlighted against the recorded hashes.
    """

    state: str = "available"
    source_id: str
    source_version: str
    source_sha256: str
    document_id: str
    citation: str
    title: str
    classification: str
    source_locator: str
    span_kind: str
    span_start: int
    span_end: int
    span_page: int | None = None
    span_text: str
    supersession_status: str
    jurisdiction: str | None = None


class CorpusSpanDeniedRead(CorpusReadModel):
    """A refusal to show one source span, with no span content at all.

    The model intentionally has no span text, locator, document, or chunk
    fields: a denial can never carry protected content, only the reason
    category and a generic sanitized message.
    """

    state: str = "denied"
    source_id: str
    denial_reason: str
    """Reason category, for example source_not_accessible."""
    denial_message: str


CorpusSpanRead = CorpusSpanAvailableRead | CorpusSpanDeniedRead


class CorpusResearchStore(Protocol):
    """Tenant-scoped read contract behind the corpus research routes.

    Every method is keyed by the authenticated principal's tenant, never
    by request content. Membership-aware reads must return only material
    the principal may see for that matter. Implementations raise
    CorpusStoreUnavailable when they have no current answer and return
    None for an unknown matter or source.
    """

    def search(
        self, tenant_id: UUID, matter_id: UUID, query: str
    ) -> CorpusSearchResponseRead | None: ...

    def get_span(
        self, tenant_id: UUID, matter_id: UUID, source_id: str
    ) -> CorpusSpanRead | None: ...

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool: ...


class InMemoryCorpusResearchStore:
    """Deterministic in-memory corpus research store for tests.

    Membership fails closed: a matter with no recorded roster denies every
    principal. Set ``available = False`` to simulate a backend outage; all
    reads then raise CorpusStoreUnavailable. Span text exists in the store
    only for accessible sources; inaccessible sources are recorded as
    denial descriptors that structurally cannot hold span content.
    """

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self._searches: dict[tuple[UUID, UUID, str], CorpusSearchResponseRead] = {}
        self._known_matters: set[tuple[UUID, UUID]] = set()
        self._spans: dict[tuple[UUID, UUID, str], CorpusSpanRead] = {}
        self._members: dict[tuple[UUID, UUID], frozenset[UUID]] = {}

    def _require_available(self) -> None:
        if not self.available:
            raise CorpusStoreUnavailable("corpus research store unavailable")

    def add_search(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        query: str,
        response: CorpusSearchResponseRead,
    ) -> None:
        self._searches[(tenant_id, matter_id, query)] = response
        self._known_matters.add((tenant_id, matter_id))

    def add_span(self, tenant_id: UUID, matter_id: UUID, span: CorpusSpanRead) -> None:
        self._spans[(tenant_id, matter_id, span.source_id)] = span
        self._known_matters.add((tenant_id, matter_id))

    def set_matter_members(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        principal_ids: frozenset[UUID],
    ) -> None:
        self._members[(tenant_id, matter_id)] = principal_ids

    def search(
        self, tenant_id: UUID, matter_id: UUID, query: str
    ) -> CorpusSearchResponseRead | None:
        self._require_available()
        if (tenant_id, matter_id) not in self._known_matters:
            return None
        return self._searches.get((tenant_id, matter_id, query))

    def get_span(
        self, tenant_id: UUID, matter_id: UUID, source_id: str
    ) -> CorpusSpanRead | None:
        self._require_available()
        if (tenant_id, matter_id) not in self._known_matters:
            return None
        return self._spans.get((tenant_id, matter_id, source_id))

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        self._require_available()
        roster = self._members.get((tenant_id, matter_id))
        return roster is not None and principal_id in roster


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "corpus_unavailable"},
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


def _path_source_id(request: Request) -> str:
    source_id = str(request.path_params.get("source_id", ""))
    if not source_id:
        raise _not_found() from None
    return source_id


def _dump(model: CorpusReadModel) -> Any:
    return model.model_dump(mode="json", by_alias=True)


def build_corpus_research_router(
    *,
    store: CorpusResearchStore,
    authorizer: CapabilityAuthorizer,
    principal_resolver: PrincipalResolver,
    scope_resolver: ScopeResolver,
) -> APIRouter:
    """Build the matter-scoped corpus research router.

    Search requires a ``corpus.search`` capability for legal research
    scoped to the exact matter in the path; the span viewer requires
    ``corpus.artifact.read`` on the same matter. Both also require
    recorded matter membership, because material a principal cannot see
    must never enter retrieval in the first place. Request content never
    selects a tenant or a scope: the query text is the only request data
    and it cannot widen the governed matter scope.
    """

    router = APIRouter()

    def dependency(capability: Capability, purpose: Purpose, name: str) -> Any:
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=authorizer,
            route_name=f"corpus.{name}",
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

    async def search(
        request: Request,
        payload: CorpusSearchRequest,
        authorized: AuthorizedContext = Depends(
            dependency(
                Capability.CORPUS_SEARCH,
                Purpose.LEGAL_RESEARCH,
                "search",
            )
        ),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        require_membership(authorized, matter_id)
        try:
            response = store.search(
                authorized.principal.tenant_id, matter_id, payload.query
            )
        except Exception:
            raise _unavailable() from None
        if response is None:
            raise _not_found() from None
        return _dump(response)

    async def get_span(
        request: Request,
        authorized: AuthorizedContext = Depends(
            dependency(
                Capability.CORPUS_ARTIFACT_READ,
                Purpose.LEGAL_RESEARCH,
                "span",
            )
        ),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        source_id = _path_source_id(request)
        require_membership(authorized, matter_id)
        try:
            span = store.get_span(authorized.principal.tenant_id, matter_id, source_id)
        except Exception:
            raise _unavailable() from None
        if span is None:
            raise _not_found() from None
        return _dump(span)

    router.post(
        "/v1/matters/{matter_id}/corpus/search",
        operation_id="corpus_search",
    )(search)
    router.get(
        "/v1/matters/{matter_id}/corpus/sources/{source_id}/span",
        operation_id="corpus_span",
    )(get_span)

    return router
