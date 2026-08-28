"""Closed V2 joined Matter analysis read contract.

This module is the sole wire-contract owner for the JOIN-01 slice. The
durable projection remains evidence, never a competing Claim lifecycle.
Canonical Claim identity, version, and status must agree with the embedded
ledger projection before any response can be emitted.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from pydantic.alias_generators import to_camel

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
RecordId = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Classification = Literal["public", "internal", "confidential", "privileged"]
ClaimStatus = Literal[
    "proposed", "under_review", "accepted", "challenged", "rejected", "withdrawn"
]
LedgerClaimStatus = Literal[
    "proposed", "under_review", "supported", "challenged", "withdrawn"
]
MappedClaimStatus = Literal[
    "proposed", "under_review", "accepted", "challenged", "withdrawn"
]

_LEGACY_LEDGER_STATUS_MAP: dict[LedgerClaimStatus, MappedClaimStatus] = {
    "proposed": "proposed",
    "under_review": "under_review",
    "supported": "accepted",
    "challenged": "challenged",
    "withdrawn": "withdrawn",
}


class JoinedAnalysisValue(BaseModel):
    """Immutable, extra-forbid value serialized as camelCase."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class SnapshotRead(JoinedAnalysisValue):
    snapshot_id: UUID
    version: int = Field(ge=1)
    observed_at: datetime
    matter_snapshot_sha256: Sha256
    claim_projection_revision: Sha256
    authority_snapshot: Sha256
    projection_revision: Sha256
    projection_sha256: Sha256

    @model_validator(mode="after")
    def require_utc_observation(self) -> Self:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(
            0
        ):
            raise ValueError("snapshot observation must carry a UTC offset")
        return self


class ClassificationRead(JoinedAnalysisValue):
    value: Classification
    purpose: Literal["claim_review"] = "claim_review"
    egress: Literal["not_evaluated", "local_only", "approved"]
    source_rights: Literal["public_synthetic", "recorded"]


class ForumRead(JoinedAnalysisValue):
    forum_id: UUID
    name: str = Field(min_length=1, max_length=512)
    jurisdiction: str = Field(min_length=1, max_length=512)
    forum_kind: Literal["court", "agency", "arbitration", "mediation", "other"]
    source_reference_id: UUID | None = None


class ProceedingRead(JoinedAnalysisValue):
    proceeding_id: UUID
    title: str = Field(min_length=1, max_length=512)
    forum_id: UUID | None = None
    docket_number: str | None = Field(default=None, min_length=1, max_length=512)
    status: Literal["proposed", "active", "stayed", "disposed", "closed"]
    version: int = Field(ge=1)


class SourceSpanRead(JoinedAnalysisValue):
    source_reference_id: UUID
    source_system: str = Field(min_length=1, max_length=512)
    source_version: str = Field(min_length=1, max_length=512)
    source_locator: str = Field(min_length=1, max_length=2048)
    content_sha256: Sha256
    span_start: int = Field(ge=0)
    span_end: int = Field(ge=1)
    excerpt_sha256: Sha256
    origin: Literal[
        "matter_record",
        "course_instruction",
        "current_official_authority",
        "model_inference",
    ]

    @model_validator(mode="after")
    def require_nonempty_span(self) -> Self:
        if self.span_end <= self.span_start:
            raise ValueError("source span end must exceed its start")
        return self


class EvidenceItemRead(JoinedAnalysisValue):
    evidence_item_id: UUID
    title: str = Field(min_length=1, max_length=512)
    media_type: str = Field(min_length=1, max_length=512)
    content_sha256: Sha256
    status: Literal[
        "proposed", "collected", "verified", "challenged", "excluded", "superseded"
    ]
    version: int = Field(ge=1)
    source: SourceSpanRead


class FactAssertionRead(JoinedAnalysisValue):
    fact_assertion_id: UUID
    subject_ref: UUID
    predicate: str = Field(min_length=1, max_length=512)
    asserted_value: bool | int | float | str | None
    status: Literal["source_asserted", "ambiguous", "verified", "superseded"]
    version: int = Field(ge=1)
    source: SourceSpanRead


class AuthorityRead(JoinedAnalysisValue):
    authority_id: UUID
    title: str = Field(min_length=1, max_length=512)
    citation: str = Field(min_length=1, max_length=512)
    jurisdiction: str = Field(min_length=1, max_length=512)
    authority_kind: Literal[
        "constitution",
        "statute",
        "regulation",
        "case",
        "rule",
        "administrative_material",
        "secondary_source",
        "other",
    ]
    status: Literal[
        "proposed", "verified", "challenged", "not_applicable", "superseded"
    ]
    version: int = Field(ge=1)
    source: SourceSpanRead


class BurdenRead(JoinedAnalysisValue):
    allocation: Literal[
        "claimant", "respondent", "moving_party", "opposing_party", "unknown"
    ]
    standard: str = Field(min_length=1, max_length=512)
    authority_id: UUID | None = None
    verification_state: Literal["recorded", "needs_authority", "disputed"]


class ElementRead(JoinedAnalysisValue):
    element_id: UUID
    theory_id: UUID
    theory_kind: Literal["claim", "defense"]
    description: str = Field(min_length=1)
    status: Literal["alleged", "supported", "disputed", "not_established"]
    version: int = Field(ge=1)
    burden: BurdenRead
    evidence_item_ids: tuple[UUID, ...] = ()
    fact_assertion_ids: tuple[UUID, ...] = ()


class EvidenceLinkRead(JoinedAnalysisValue):
    evidence_item_id: UUID
    role: Literal["support", "counter_support"]
    source: SourceSpanRead


class AuthorityLinkRead(JoinedAnalysisValue):
    authority_id: UUID
    role: Literal["support", "contrary_authority"]
    applicability: Literal["applicable", "disputed", "not_applicable", "unverified"]
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_reasons_for_nonapplicable(self) -> Self:
        if self.applicability != "applicable" and not self.reasons:
            raise ValueError("a non-applicable authority link requires reasons")
        return self


class GapRead(JoinedAnalysisValue):
    gap_id: UUID
    kind: Literal[
        "missing_fact", "missing_evidence", "missing_authority", "unresolved_burden"
    ]
    description: str = Field(min_length=1)
    blocking: bool


class ChallengeRead(JoinedAnalysisValue):
    challenge_id: UUID
    status: Literal["not_run", "pending_human_review", "defect_found", "no_defect"]
    description: str = Field(min_length=1)
    source: Literal["public_synthetic", "recorded_agent_run"]


class LegacyClaimMigrationRead(JoinedAnalysisValue):
    legacy_ledger_claim_id: UUID
    legacy_status: LedgerClaimStatus
    mapped_claim_status: MappedClaimStatus
    mapping_revision: Literal["ledger-claim-to-claim-v1"]


class LedgerProjectionRead(JoinedAnalysisValue):
    tenant_id: UUID
    matter_id: UUID
    claim_id: UUID
    projected_claim_version: int = Field(ge=1)
    projected_claim_status: ClaimStatus
    legacy_migration: LegacyClaimMigrationRead | None
    policy_revision: Sha256
    projection_state: Literal["current", "stale", "unavailable"]


class TheoryRead(JoinedAnalysisValue):
    theory_id: UUID
    theory_kind: Literal["claim", "defense"]
    issue_id: UUID
    label: str = Field(min_length=1, max_length=512)
    statement: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: ClaimStatus
    element_ids: tuple[UUID, ...]
    fact_assertion_ids: tuple[UUID, ...] = ()
    evidence: tuple[EvidenceLinkRead, ...] = ()
    authorities: tuple[AuthorityLinkRead, ...] = ()
    gaps: tuple[GapRead, ...] = ()
    challenges: tuple[ChallengeRead, ...] = ()
    ledger_projection: LedgerProjectionRead | None = None


class IssueRead(JoinedAnalysisValue):
    issue_id: UUID
    question: str = Field(min_length=1)
    status: Literal["identified", "under_review", "resolved", "deferred"]
    version: int = Field(ge=1)
    theory_ids: tuple[UUID, ...]


class JoinedAnalysisSnapshotProjection(JoinedAnalysisValue):
    """Durable projection bytes without request-local authorization evidence."""

    schema_revision: Literal["sklegal-matter-analysis/v1"] = (
        "sklegal-matter-analysis/v1"
    )
    tenant_id: UUID
    matter_id: UUID
    snapshot: SnapshotRead
    classification: ClassificationRead
    forum: ForumRead | None = None
    proceedings: tuple[ProceedingRead, ...] = ()
    issues: tuple[IssueRead, ...] = ()
    theories: tuple[TheoryRead, ...] = ()
    elements: tuple[ElementRead, ...] = ()
    fact_assertions: tuple[FactAssertionRead, ...] = ()
    evidence_items: tuple[EvidenceItemRead, ...] = ()
    authorities: tuple[AuthorityRead, ...] = ()

    @model_validator(mode="after")
    def validate_closed_graph(self) -> Self:
        def unique(name: str, values: tuple[UUID, ...]) -> set[UUID]:
            if len(values) != len(set(values)):
                raise ValueError(f"{name} identifiers must be unique")
            return set(values)

        issue_ids = unique("issue", tuple(item.issue_id for item in self.issues))
        theory_ids = unique("theory", tuple(item.theory_id for item in self.theories))
        element_ids = unique(
            "element", tuple(item.element_id for item in self.elements)
        )
        fact_ids = unique(
            "fact assertion",
            tuple(item.fact_assertion_id for item in self.fact_assertions),
        )
        evidence_ids = unique(
            "evidence item",
            tuple(item.evidence_item_id for item in self.evidence_items),
        )
        authority_ids = unique(
            "authority", tuple(item.authority_id for item in self.authorities)
        )
        proceeding_ids = tuple(item.proceeding_id for item in self.proceedings)
        unique("proceeding", proceeding_ids)
        theory_by_id = {item.theory_id: item for item in self.theories}
        element_by_id = {item.element_id: item for item in self.elements}

        if self.forum is None and any(
            item.forum_id is not None for item in self.proceedings
        ):
            raise ValueError("a proceeding cannot reference an absent Forum")
        if self.forum is not None and any(
            item.forum_id not in {None, self.forum.forum_id}
            for item in self.proceedings
        ):
            raise ValueError("a proceeding references a different Forum")
        if any(
            item.status in {"active", "stayed", "disposed"} and item.forum_id is None
            for item in self.proceedings
        ):
            raise ValueError("an active Proceeding requires a Forum")

        for issue in self.issues:
            if set(issue.theory_ids) - theory_ids:
                raise ValueError("an Issue references an unknown Claim or Defense")
            if len(issue.theory_ids) != len(set(issue.theory_ids)):
                raise ValueError("an Issue cannot repeat a Claim or Defense")
            if any(
                theory_by_id[theory_id].issue_id != issue.issue_id
                for theory_id in issue.theory_ids
            ):
                raise ValueError("Issue and Claim or Defense mapping is not reciprocal")
        for theory in self.theories:
            if theory.issue_id not in issue_ids:
                raise ValueError("a Claim or Defense references an unknown Issue")
            if set(theory.element_ids) - element_ids:
                raise ValueError("a Claim or Defense references an unknown Element")
            if set(theory.fact_assertion_ids) - fact_ids:
                raise ValueError(
                    "a Claim or Defense references an unknown Fact Assertion"
                )
            if {item.evidence_item_id for item in theory.evidence} - evidence_ids:
                raise ValueError(
                    "a Claim or Defense references an unknown Evidence Item"
                )
            if {item.authority_id for item in theory.authorities} - authority_ids:
                raise ValueError("a Claim or Defense references an unknown Authority")
            if len(theory.evidence) != len(
                {item.evidence_item_id for item in theory.evidence}
            ):
                raise ValueError(
                    "an Evidence Item cannot occupy conflicting support roles"
                )
            if len(theory.authorities) != len(
                {item.authority_id for item in theory.authorities}
            ):
                raise ValueError("an Authority cannot be both supporting and contrary")
            if len(theory.gaps) != len({item.gap_id for item in theory.gaps}):
                raise ValueError("a Claim or Defense cannot repeat a gap")
            if len(theory.challenges) != len(
                {item.challenge_id for item in theory.challenges}
            ):
                raise ValueError("a Claim or Defense cannot repeat a challenge")
            issue = next(
                item for item in self.issues if item.issue_id == theory.issue_id
            )
            if theory.theory_id not in issue.theory_ids:
                raise ValueError("Issue and Claim or Defense mapping is not reciprocal")
            if len(theory.element_ids) != len(set(theory.element_ids)):
                raise ValueError("a Claim or Defense cannot repeat an Element")
            if any(
                element_by_id[element_id].theory_id != theory.theory_id
                or element_by_id[element_id].theory_kind != theory.theory_kind
                for element_id in theory.element_ids
            ):
                raise ValueError(
                    "Element and Claim or Defense mapping is not reciprocal"
                )
            if len(theory.fact_assertion_ids) != len(set(theory.fact_assertion_ids)):
                raise ValueError("a Claim or Defense cannot repeat a Fact Assertion")
            evidence_by_id = {
                item.evidence_item_id: item for item in self.evidence_items
            }
            for evidence_link in theory.evidence:
                evidence = evidence_by_id[evidence_link.evidence_item_id]
                if evidence_link.source != evidence.source:
                    raise ValueError(
                        "Evidence Item and support span provenance disagree"
                    )
            authority_by_id = {item.authority_id: item for item in self.authorities}
            for authority_link in theory.authorities:
                authority = authority_by_id[authority_link.authority_id]
                if authority.source.origin != "current_official_authority":
                    raise ValueError(
                        "an Authority link requires official-source provenance"
                    )
            if theory.theory_kind == "claim":
                ledger = theory.ledger_projection
                if ledger is None:
                    raise ValueError(
                        "a Claim requires its one-to-one ledger projection"
                    )
                if (
                    ledger.tenant_id != self.tenant_id
                    or ledger.matter_id != self.matter_id
                    or ledger.claim_id != theory.theory_id
                    or ledger.projected_claim_version != theory.version
                    or ledger.projected_claim_status != theory.status
                ):
                    raise ValueError("canonical Claim and ledger projection disagree")
                migration = ledger.legacy_migration
                if migration is None and theory.status in {"accepted", "rejected"}:
                    raise ValueError("legacy ledger status mapping is invalid")
                if migration is not None:
                    if (
                        _LEGACY_LEDGER_STATUS_MAP[migration.legacy_status]
                        != migration.mapped_claim_status
                        or migration.mapped_claim_status != theory.status
                    ):
                        raise ValueError("legacy ledger status mapping is invalid")
            elif theory.ledger_projection is not None:
                raise ValueError("a Defense cannot reuse the Claim ledger projection")
        for element in self.elements:
            target = next(
                (item for item in self.theories if item.theory_id == element.theory_id),
                None,
            )
            if target is None or target.theory_kind != element.theory_kind:
                raise ValueError("an Element references the wrong Claim or Defense")
            if set(element.evidence_item_ids) - evidence_ids:
                raise ValueError("an Element references an unknown Evidence Item")
            if set(element.fact_assertion_ids) - fact_ids:
                raise ValueError("an Element references an unknown Fact Assertion")
            if element.burden.authority_id not in {None, *authority_ids}:
                raise ValueError("an Element burden references an unknown Authority")
            if element.element_id not in target.element_ids:
                raise ValueError(
                    "Element and Claim or Defense mapping is not reciprocal"
                )
        allowed_fact_subjects = (
            {self.matter_id}
            | issue_ids
            | theory_ids
            | element_ids
            | fact_ids
            | evidence_ids
            | authority_ids
            | set(proceeding_ids)
        )
        if self.forum is not None:
            allowed_fact_subjects.add(self.forum.forum_id)
        if any(
            fact.subject_ref not in allowed_fact_subjects
            for fact in self.fact_assertions
        ):
            raise ValueError("a Fact Assertion references an unknown graph subject")
        for evidence in self.evidence_items:
            if evidence.content_sha256 != evidence.source.content_sha256:
                raise ValueError("Evidence Item content and provenance digest disagree")
        return self


class PolicyIdentityRead(JoinedAnalysisValue):
    authorization_decision_id: UUID
    principal_id: UUID
    capability: Literal["claim.review"] = "claim.review"
    purpose: Literal["claim_review"] = "claim_review"
    verifier_policy_version: str = Field(min_length=1, max_length=160)
    principal_policy_revisions: tuple[Sha256, ...]
    trusted_issuer_policy_revision: Sha256
    revocation_revision: Sha256


class PageRead(JoinedAnalysisValue):
    limit: int = Field(ge=1, le=100)
    returned: int = Field(ge=0)
    has_more: bool
    next_cursor: str | None = Field(default=None, min_length=1, max_length=512)


class MatterAnalysisRead(JoinedAnalysisSnapshotProjection):
    """Request-local authorized response for the joined analysis endpoint."""

    policy_identity: PolicyIdentityRead
    page: PageRead
