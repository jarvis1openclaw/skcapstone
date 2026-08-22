"""CapAuth-gated claim ledger read API (SKL-S4-03B).

Read side of the /corpus claim ledger surface: one matter-scoped response
carrying every ledger claim with its support and counter-support spans,
the deterministic applicability factors behind its authority support
verification, the typed blind-challenge records with their independence
labels and preserved defects, the claim-state transition history, and the
current claim gate evaluation.

The route runs inside the S1-03 CapAuth protected-route boundary with the
exact ``claim.review`` capability for claim review scoped to the matter in
the path. Tenant scope comes only from the authenticated principal, matter
membership is enforced before any ledger record is read, and all failures
are sanitized and fail closed: an unavailable store yields 503, an unknown
matter yields 404, and a membership or capability denial yields 403 with no
record detail.

Nothing here judges a claim: the ledger reports recorded evidence only.
Defects found by a blind challenge are preserved verbatim with their defect
kind, contrary support is reported beside support instead of being silently
reduced, and a failed gate carries its failed checks and closed reason
vocabulary so no failed gate can be presented as passing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
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

SUPPORT_KIND = "support"
COUNTER_SUPPORT_KIND = "counter_support"


class ClaimLedgerStoreUnavailable(RuntimeError):
    """The claim ledger read store has no current answer; fail closed."""


class ClaimLedgerReadModel(BaseModel):
    """Base read model: snake_case internally, camelCase on the wire."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ClaimSupportRecordRead(ClaimLedgerReadModel):
    """One append-only support or counter-support link to an exact span."""

    support_id: str
    kind: Literal["support", "counter_support"]
    recorded_at: datetime
    source_system: str
    source_version: str
    source_locator: str
    content_sha256: str
    span_start: int = Field(ge=0)
    span_end: int = Field(ge=1)
    excerpt_sha256: str
    note: str | None = None
    recorded_by_principal_id: str
    policy_revision: str


class ApplicabilityCheckRead(ClaimLedgerReadModel):
    """One deterministic applicability, status, or quotation factor.

    The closed reason vocabulary travels with every failed factor, so the
    UI can show why an authority did not qualify without inventing causes.
    """

    check_id: str
    subject_id: str | None = None
    outcome: Literal["passed", "failed"]
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_outcome_reasons(self) -> ApplicabilityCheckRead:
        if self.outcome == "passed" and self.reasons:
            raise ValueError("a passed applicability factor carries no reasons")
        if self.outcome == "failed" and not self.reasons:
            raise ValueError("a failed applicability factor cites its reasons")
        return self


class ChallengeDefectRead(ClaimLedgerReadModel):
    """One challenge finding preserved verbatim for human review."""

    defect_kind: str
    description: str


class ChallengeRead(ClaimLedgerReadModel):
    """One typed blind-challenge record with its independence label.

    The challenger model identity and whether the challenger saw the
    challenged conclusion are carried beside the label, so the same-model
    and non-blind labels stay reconstructible from the ledger itself.
    """

    challenge_id: str
    issued_at: datetime
    independence: Literal["independent", "same_model", "not_blind"]
    outcome: Literal["no_defect", "defect_found"]
    saw_challenged_conclusion: bool
    challenger_provider: str
    challenger_model_name: str
    challenger_model_revision: str
    defects: tuple[ChallengeDefectRead, ...] = ()

    @model_validator(mode="after")
    def validate_outcome_defects(self) -> ChallengeRead:
        if self.outcome == "defect_found" and not self.defects:
            raise ValueError("a defect-found challenge reports its defects")
        if self.outcome == "no_defect" and self.defects:
            raise ValueError("a no-defect challenge carries no defects")
        return self


class GateCheckRead(ClaimLedgerReadModel):
    """One failed gate check with the subject it names and its reasons."""

    check_id: str
    subject_id: str | None = None
    reasons: tuple[str, ...] = ()


class GateEvaluationRead(ClaimLedgerReadModel):
    """The recorded claim gate evaluation for one ledger claim.

    Only the failed checks travel; a passing gate is reported as passed
    with no failed checks, mirroring the domain reduction.
    """

    gate: Literal["claim_ready"]
    evaluated_at: datetime
    outcome: Literal["passed", "failed"]
    failed_checks: tuple[GateCheckRead, ...] = ()

    @model_validator(mode="after")
    def validate_outcome_checks(self) -> GateEvaluationRead:
        if self.outcome == "passed" and self.failed_checks:
            raise ValueError("a passed gate carries no failed checks")
        if self.outcome == "failed" and not self.failed_checks:
            raise ValueError("a failed gate reports its failed checks")
        return self


class ClaimStateTransitionRead(ClaimLedgerReadModel):
    """One recorded claim-state transition.

    ``from_status`` is null for the initial proposal entry, so the full
    state history stays explicit instead of being inferred from a snapshot.
    """

    from_status: (
        Literal["proposed", "under_review", "supported", "challenged", "withdrawn"]
        | None
    ) = None
    to_status: Literal[
        "proposed", "under_review", "supported", "challenged", "withdrawn"
    ]
    at: datetime
    version: int = Field(ge=1)


class ClaimReviewRecordRead(ClaimLedgerReadModel):
    """One append-only human review decision for an exact claim version."""

    review_id: str
    reviewed_at: datetime
    reviewer_principal_id: str
    claim_version: int = Field(ge=1)
    decision: Literal[
        "accepted", "changes_requested", "challenge_recorded", "withdrawal_confirmed"
    ]
    note: str
    policy_revision: str


class ClaimLedgerEntryRead(ClaimLedgerReadModel):
    """One material claim with its support, qualification, and challenges."""

    claim_id: str
    statement: str
    status: Literal["proposed", "under_review", "supported", "challenged", "withdrawn"]
    version: int = Field(ge=1)
    policy_revision: str
    updated_at: datetime
    support: tuple[ClaimSupportRecordRead, ...] = ()
    counter_support: tuple[ClaimSupportRecordRead, ...] = ()
    support_verification_state: Literal["passed", "failed", "missing"]
    applicability: tuple[ApplicabilityCheckRead, ...] = ()
    challenges: tuple[ChallengeRead, ...] = ()
    review_history: tuple[ClaimReviewRecordRead, ...] = ()
    gate: GateEvaluationRead | None = None
    state_transitions: tuple[ClaimStateTransitionRead, ...] = ()

    @model_validator(mode="after")
    def validate_support_panels(self) -> ClaimLedgerEntryRead:
        if self.support and any(record.kind != SUPPORT_KIND for record in self.support):
            raise ValueError("support panel holds only support records")
        if self.counter_support and any(
            record.kind != COUNTER_SUPPORT_KIND for record in self.counter_support
        ):
            raise ValueError("counter-support panel holds only counter-support records")
        return self


class ClaimLedgerRead(ClaimLedgerReadModel):
    """The claim ledger for one matter.

    An empty claims tuple is a recorded no-answer state, not an error: the
    UI renders it explicitly instead of showing a blank surface.
    """

    matter_id: UUID
    claims: tuple[ClaimLedgerEntryRead, ...] = ()


class ClaimLedgerStore(Protocol):
    """Tenant-scoped read contract behind the claim ledger route.

    Every method is keyed by the authenticated principal's tenant, never
    by request content. Implementations raise ClaimLedgerStoreUnavailable
    when they have no current answer and return None for an unknown
    matter.
    """

    def ledger(self, tenant_id: UUID, matter_id: UUID) -> ClaimLedgerRead | None: ...

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool: ...


class InMemoryClaimLedgerStore:
    """Deterministic in-memory claim ledger store for tests.

    Membership fails closed: a matter with no recorded roster denies every
    principal. Set ``available = False`` to simulate a backend outage; all
    reads then raise ClaimLedgerStoreUnavailable.
    """

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self._ledgers: dict[tuple[UUID, UUID], ClaimLedgerRead] = {}
        self._known_matters: set[tuple[UUID, UUID]] = set()
        self._members: dict[tuple[UUID, UUID], frozenset[UUID]] = {}

    def _require_available(self) -> None:
        if not self.available:
            raise ClaimLedgerStoreUnavailable("claim ledger store unavailable")

    def add_ledger(
        self, tenant_id: UUID, matter_id: UUID, ledger: ClaimLedgerRead
    ) -> None:
        self._ledgers[(tenant_id, matter_id)] = ledger
        self._known_matters.add((tenant_id, matter_id))

    def set_matter_members(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        principal_ids: frozenset[UUID],
    ) -> None:
        self._members[(tenant_id, matter_id)] = principal_ids

    def ledger(self, tenant_id: UUID, matter_id: UUID) -> ClaimLedgerRead | None:
        self._require_available()
        if (tenant_id, matter_id) not in self._known_matters:
            return None
        return self._ledgers.get(
            (tenant_id, matter_id),
            ClaimLedgerRead(matter_id=matter_id),
        )

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        self._require_available()
        roster = self._members.get((tenant_id, matter_id))
        return roster is not None and principal_id in roster


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "claim_ledger_unavailable"},
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


def _dump(model: ClaimLedgerReadModel) -> Any:
    return model.model_dump(mode="json", by_alias=True)


def build_claim_ledger_router(
    *,
    store: ClaimLedgerStore,
    authorizer: CapabilityAuthorizer,
    principal_resolver: PrincipalResolver,
    scope_resolver: ScopeResolver,
) -> APIRouter:
    """Build the matter-scoped claim ledger read router.

    Reading the ledger requires a ``claim.review`` capability for claim
    review scoped to the exact matter in the path, plus recorded matter
    membership, because reviewer history and challenge records must never
    reach a principal outside the matter. The route is read only: no
    request body exists and no claim state can change through it.
    """

    router = APIRouter()

    def dependency(capability: Capability, purpose: Purpose, name: str) -> Any:
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=authorizer,
            route_name=f"claims.{name}",
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

    async def get_ledger(
        request: Request,
        authorized: AuthorizedContext = Depends(
            dependency(
                Capability.CLAIM_REVIEW,
                Purpose.CLAIM_REVIEW,
                "ledger",
            )
        ),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        require_membership(authorized, matter_id)
        try:
            ledger = store.ledger(authorized.principal.tenant_id, matter_id)
        except Exception:
            raise _unavailable() from None
        if ledger is None:
            raise _not_found() from None
        return _dump(ledger)

    router.get(
        "/v1/matters/{matter_id}/claims",
        operation_id="claims_ledger",
    )(get_ledger)

    return router
