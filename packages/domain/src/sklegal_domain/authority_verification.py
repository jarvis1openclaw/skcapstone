"""Authority applicability, status, quotation, contrary-search, and remedy checks.

Deterministic verification for the SKL-S3-05A claim ledger: no model calls,
no I/O, and no retrieval imports. Retrieval results enter as typed candidate
records that identify the qualified retrieval plane they came from, and every
decision is reduced from exact recorded factors: jurisdiction, scope keys,
authority status, supersession, effective time, source digests, and verified
exact quotations.

A similarity score is ranking metadata only. The code path that decides
whether a retrieval candidate becomes a qualified contrary-authority lead
never reads the similarity value, so no similarity score, however high, can
qualify an authority. Unverified candidates stay recorded as similarity-only
leads that require human review, and the authority-support verification fails
closed while any contrary lead is unresolved.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, model_validator

from .entities.claim_ledger import LedgerClaim
from .entities.claims import Remedy
from .entities.facts import Authority
from .states import (
    AuthorityStatus,
    ValidationOutcome,
)
from .value_objects import (
    DomainId,
    FrozenValue,
    NonEmptyText,
    PlaceholderKey,
    Sha256,
    ShortText,
    UtcDateTime,
    require_utc,
)

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class AuthorityCheckId(StrEnum):
    """Stable identifiers for the deterministic authority support checks."""

    CITATION_PRESENT = "citation.present"
    AUTHORITY_BINDING = "authority.binding"
    AUTHORITY_JURISDICTION = "authority.jurisdiction"
    AUTHORITY_SCOPE = "authority.scope"
    AUTHORITY_STATUS = "authority.status"
    AUTHORITY_EFFECTIVE_INTERVAL = "authority.effective_interval"
    AUTHORITY_QUOTATION = "authority.quotation"
    CONTRARY_SEARCH_EXECUTED = "contrary.search_executed"
    CONTRARY_LEADS_REVIEWED = "contrary.leads_reviewed"
    REMEDY_PRESENT = "remedy.present"
    REMEDY_AUTHORITY = "remedy.authority"


class AuthorityFailureReason(StrEnum):
    """The closed reason vocabulary for every failed authority check."""

    AUTHORITY_CITATION_MISSING = "authority_citation_missing"
    AUTHORITY_SCOPE_CROSSING = "authority_scope_crossing"
    QUOTATION_AUTHORITY_MISMATCH = "quotation_authority_mismatch"
    WRONG_JURISDICTION = "wrong_jurisdiction"
    SCOPE_KEY_MISSING = "scope_key_missing"
    SUPERSEDED_AUTHORITY = "superseded_authority"
    AUTHORITY_NOT_APPLICABLE = "authority_not_applicable"
    AUTHORITY_NOT_VERIFIED = "authority_not_verified"
    AUTHORITY_CHALLENGED = "authority_challenged"
    AUTHORITY_NOT_EFFECTIVE = "authority_not_effective"
    EFFECTIVE_INTERVAL_UNKNOWN = "effective_interval_unknown"
    QUOTATION_MISSING = "quotation_missing"
    QUOTATION_UNVERIFIED = "quotation_unverified"
    QUOTATION_MISMATCH = "quotation_mismatch"
    QUOTATION_SOURCE_UNPINNED = "quotation_source_unpinned"
    CONTRARY_SEARCH_MISSING = "contrary_search_missing"
    CONTRARY_LEADS_REQUIRE_REVIEW = "contrary_leads_require_review"
    REMEDY_MISSING = "remedy_missing"
    REMEDY_AUTHORITY_MISSING = "remedy_authority_missing"
    REMEDY_SCOPE_CROSSING = "remedy_scope_crossing"
    CITATION_MISSING = "citation_missing"
    JURISDICTION_MISSING = "jurisdiction_missing"
    SOURCE_DIGEST_MISMATCH = "source_digest_mismatch"
    SPAN_OUT_OF_BOUNDS = "span_out_of_bounds"
    EXCERPT_DIGEST_MISMATCH = "excerpt_digest_mismatch"
    QUOTATION_TEXT_MISMATCH = "quotation_text_mismatch"


def normalize_jurisdiction(value: str) -> str:
    """Collapse whitespace and casefold so jurisdiction equality is exact."""

    if not isinstance(value, str):
        raise TypeError("jurisdiction must be text")
    return " ".join(value.split()).casefold()


class MatterAuthorityScope(FrozenValue):
    """The jurisdiction and topical scope an authority must satisfy."""

    jurisdiction: ShortText
    scope_keys: tuple[PlaceholderKey, ...] = ()

    @model_validator(mode="after")
    def validate_scope_keys(self) -> MatterAuthorityScope:
        if len(self.scope_keys) != len(set(self.scope_keys)):
            raise ValueError("scope keys must be unique")
        return self


class AuthorityQuotation(FrozenValue):
    """One exact quotation pinned to an authority source version and span."""

    quotation_id: DomainId
    authority_id: DomainId
    quoted_text: NonEmptyText
    span_start: Annotated[int, Field(ge=0)]
    span_end: Annotated[int, Field(ge=1)]
    excerpt_sha256: Sha256
    source_content_sha256: Sha256

    @model_validator(mode="after")
    def validate_span(self) -> AuthorityQuotation:
        if self.span_end <= self.span_start:
            raise ValueError("quotation span end must be later than span start")
        return self


class QuotationVerification(FrozenValue):
    """The deterministic outcome of checking one quotation against source bytes."""

    quotation_id: DomainId
    outcome: ValidationOutcome
    failure_reasons: tuple[AuthorityFailureReason, ...] = ()
    observed_source_sha256: Sha256
    observed_excerpt_sha256: Sha256

    @model_validator(mode="after")
    def validate_outcome_reasons(self) -> QuotationVerification:
        if self.outcome is ValidationOutcome.INCOMPLETE:
            raise ValueError("quotation verification is never incomplete")
        if self.outcome is ValidationOutcome.PASSED and self.failure_reasons:
            raise ValueError("a passed quotation verification carries no reasons")
        if self.outcome is ValidationOutcome.FAILED and not self.failure_reasons:
            raise ValueError("a failed quotation verification cites its reasons")
        return self

    @property
    def passed(self) -> bool:
        return self.outcome is ValidationOutcome.PASSED


class AuthorityCitation(FrozenValue):
    """One ledger citation of an authority with asserted scope and quotations."""

    citation_id: DomainId
    authority: Authority
    asserted_scope_keys: tuple[PlaceholderKey, ...] = ()
    quotations: tuple[AuthorityQuotation, ...] = ()

    @model_validator(mode="after")
    def validate_citation(self) -> AuthorityCitation:
        if len(self.asserted_scope_keys) != len(set(self.asserted_scope_keys)):
            raise ValueError("asserted scope keys must be unique")
        identifiers = tuple(quotation.quotation_id for quotation in self.quotations)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("citation quotations must be unique")
        return self


class SupportCheck(FrozenValue):
    """One deterministic check outcome with its subject and closed reasons."""

    check_id: AuthorityCheckId
    subject_id: DomainId | None
    outcome: ValidationOutcome
    reasons: tuple[AuthorityFailureReason, ...] = ()

    @model_validator(mode="after")
    def validate_outcome_reasons(self) -> SupportCheck:
        if self.outcome is ValidationOutcome.INCOMPLETE:
            raise ValueError("an authority support check is never incomplete")
        if self.outcome is ValidationOutcome.PASSED and self.reasons:
            raise ValueError("a passed check carries no reasons")
        if self.outcome is ValidationOutcome.FAILED and not self.reasons:
            raise ValueError("a failed check cites its reasons")
        return self

    @property
    def passed(self) -> bool:
        return self.outcome is ValidationOutcome.PASSED


class RetrievalPlaneBinding(FrozenValue):
    """The qualified retrieval plane a contrary-authority search ran over.

    The qualification verdict digest is required, so a plane that cannot cite
    the verdict that qualified it cannot be expressed as an input to the
    authority-support verification at all.
    """

    plane_id: ShortText
    route_label: ShortText
    embedding_model_revision: ShortText
    qualification_verdict_sha256: Sha256
    alias_revision_sequence: Annotated[int, Field(ge=1)] | None = None


class ContrarySearchCandidate(FrozenValue):
    """One retrieval candidate for contrary authority, with ranking metadata.

    ``similarity`` is trace and ranking metadata only. It is never an input
    to any qualification decision in this module.
    """

    candidate_id: ShortText
    similarity: Annotated[float, Field(ge=0.0, le=1.0)]
    citation: ShortText | None = None
    jurisdiction: ShortText | None = None
    observed_authority_status: AuthorityStatus | None = None
    asserted_scope_keys: tuple[PlaceholderKey, ...] = ()
    quotation_verification: QuotationVerification | None = None

    @model_validator(mode="after")
    def validate_candidate(self) -> ContrarySearchCandidate:
        if len(self.asserted_scope_keys) != len(set(self.asserted_scope_keys)):
            raise ValueError("asserted scope keys must be unique")
        return self


class ContraryLeadDisposition(StrEnum):
    """What a retrieval candidate is allowed to become without human review."""

    QUALIFIED_LEAD = "qualified_lead"
    SIMILARITY_ONLY = "similarity_only"
    NOT_APPLICABLE = "not_applicable"


class ContraryCandidateEvaluation(FrozenValue):
    """One candidate's deterministic disposition with its ranking trace."""

    candidate_id: ShortText
    similarity: Annotated[float, Field(ge=0.0, le=1.0)]
    disposition: ContraryLeadDisposition
    citation: ShortText | None
    reasons: tuple[AuthorityFailureReason, ...] = ()

    @model_validator(mode="after")
    def validate_disposition_reasons(self) -> ContraryCandidateEvaluation:
        if self.disposition is ContraryLeadDisposition.QUALIFIED_LEAD:
            if self.reasons:
                raise ValueError("a qualified contrary lead cites no failure reasons")
            if self.citation is None:
                raise ValueError("a qualified contrary lead carries its citation")
        elif not self.reasons:
            raise ValueError("a non-qualified lead cites its reasons")
        return self


class ContraryAuthoritySearchResult(FrozenValue):
    """The recorded outcome of one contrary-authority search over one plane."""

    plane: RetrievalPlaneBinding
    searched_at: UtcDateTime
    evaluations: tuple[ContraryCandidateEvaluation, ...] = ()

    @model_validator(mode="after")
    def validate_evaluations(self) -> ContraryAuthoritySearchResult:
        identifiers = tuple(evaluation.candidate_id for evaluation in self.evaluations)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("contrary search evaluations must be unique")
        return self

    @property
    def qualified_leads(self) -> tuple[ContraryCandidateEvaluation, ...]:
        return tuple(
            evaluation
            for evaluation in self.evaluations
            if evaluation.disposition is ContraryLeadDisposition.QUALIFIED_LEAD
        )

    @property
    def similarity_only_leads(self) -> tuple[ContraryCandidateEvaluation, ...]:
        return tuple(
            evaluation
            for evaluation in self.evaluations
            if evaluation.disposition is ContraryLeadDisposition.SIMILARITY_ONLY
        )

    @property
    def unresolved_leads(self) -> tuple[ContraryCandidateEvaluation, ...]:
        """Leads that still require human review before any gate can pass."""

        return self.qualified_leads + self.similarity_only_leads


class AuthorityCitationEvaluation(FrozenValue):
    """The applicability and status checks for one authority citation."""

    citation_id: DomainId
    checks: tuple[SupportCheck, ...]


class AuthoritySupportVerification(FrozenValue):
    """The deterministic authority-support verification for one ledger claim."""

    claim_id: DomainId
    tenant_id: DomainId
    matter_id: DomainId
    evaluated_at: UtcDateTime
    checks: tuple[SupportCheck, ...]
    outcome: ValidationOutcome

    @model_validator(mode="after")
    def validate_outcome(self) -> AuthoritySupportVerification:
        if self.outcome is ValidationOutcome.INCOMPLETE:
            raise ValueError("an authority support verification is never incomplete")
        failed = any(check.outcome is ValidationOutcome.FAILED for check in self.checks)
        if self.outcome is ValidationOutcome.PASSED and failed:
            raise ValueError("a passed verification has no failed checks")
        if self.outcome is ValidationOutcome.FAILED and not failed:
            raise ValueError("a failed verification has at least one failed check")
        return self

    @property
    def failed_checks(self) -> tuple[SupportCheck, ...]:
        return tuple(
            check for check in self.checks if check.outcome is ValidationOutcome.FAILED
        )


def verify_authority_quotation(
    quotation: AuthorityQuotation,
    *,
    source_text: str,
    expected_source_sha256: Sha256,
) -> QuotationVerification:
    """Verify one quotation against the exact pinned source bytes.

    The document digest proves the supplied text is the pinned source version,
    the span proves the excerpt location, the excerpt digest proves the exact
    bytes, and the text comparison proves the quoted wording. Every failing
    condition is reported together so a mismatch is never silently reduced.
    """

    if not isinstance(source_text, str):
        raise TypeError("source_text must be text")
    reasons: list[AuthorityFailureReason] = []
    observed_source = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    if observed_source != expected_source_sha256:
        reasons.append(AuthorityFailureReason.SOURCE_DIGEST_MISMATCH)
    observed_excerpt = _EMPTY_SHA256
    if quotation.span_end > len(source_text):
        reasons.append(AuthorityFailureReason.SPAN_OUT_OF_BOUNDS)
    else:
        excerpt = source_text[quotation.span_start : quotation.span_end]
        observed_excerpt = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        if observed_excerpt != quotation.excerpt_sha256:
            reasons.append(AuthorityFailureReason.EXCERPT_DIGEST_MISMATCH)
        if excerpt != quotation.quoted_text:
            reasons.append(AuthorityFailureReason.QUOTATION_TEXT_MISMATCH)
    return QuotationVerification(
        quotation_id=quotation.quotation_id,
        outcome=(ValidationOutcome.PASSED if not reasons else ValidationOutcome.FAILED),
        failure_reasons=tuple(reasons),
        observed_source_sha256=observed_source,
        observed_excerpt_sha256=observed_excerpt,
    )


def evaluate_authority_citation(
    citation: AuthorityCitation,
    scope: MatterAuthorityScope,
    *,
    at: datetime,
    expected_tenant_id: DomainId,
    expected_matter_id: DomainId,
    quotation_verifications: Mapping[DomainId, QuotationVerification] | None = None,
) -> AuthorityCitationEvaluation:
    """Reduce one citation to deterministic applicability and status checks."""

    instant = require_utc(at)
    verifications = quotation_verifications or {}
    authority = citation.authority
    checks: list[SupportCheck] = []

    binding_reasons: list[AuthorityFailureReason] = []
    if (
        authority.tenant_id != expected_tenant_id
        or authority.matter_id != expected_matter_id
    ):
        binding_reasons.append(AuthorityFailureReason.AUTHORITY_SCOPE_CROSSING)
    for quotation in citation.quotations:
        if quotation.authority_id != authority.id:
            binding_reasons.append(AuthorityFailureReason.QUOTATION_AUTHORITY_MISMATCH)
    checks.append(
        SupportCheck(
            check_id=AuthorityCheckId.AUTHORITY_BINDING,
            subject_id=citation.citation_id,
            outcome=(
                ValidationOutcome.FAILED
                if binding_reasons
                else ValidationOutcome.PASSED
            ),
            reasons=tuple(binding_reasons),
        )
    )

    jurisdiction_reasons: list[AuthorityFailureReason] = []
    if normalize_jurisdiction(authority.jurisdiction) != normalize_jurisdiction(
        scope.jurisdiction
    ):
        jurisdiction_reasons.append(AuthorityFailureReason.WRONG_JURISDICTION)
    checks.append(
        SupportCheck(
            check_id=AuthorityCheckId.AUTHORITY_JURISDICTION,
            subject_id=citation.citation_id,
            outcome=(
                ValidationOutcome.FAILED
                if jurisdiction_reasons
                else ValidationOutcome.PASSED
            ),
            reasons=tuple(jurisdiction_reasons),
        )
    )

    asserted = set(citation.asserted_scope_keys)
    scope_reasons = [
        AuthorityFailureReason.SCOPE_KEY_MISSING
        for key in scope.scope_keys
        if key not in asserted
    ]
    checks.append(
        SupportCheck(
            check_id=AuthorityCheckId.AUTHORITY_SCOPE,
            subject_id=citation.citation_id,
            outcome=(
                ValidationOutcome.FAILED if scope_reasons else ValidationOutcome.PASSED
            ),
            reasons=tuple(scope_reasons),
        )
    )

    status_reasons = _authority_status_reasons(authority.status)
    checks.append(
        SupportCheck(
            check_id=AuthorityCheckId.AUTHORITY_STATUS,
            subject_id=citation.citation_id,
            outcome=(
                ValidationOutcome.FAILED if status_reasons else ValidationOutcome.PASSED
            ),
            reasons=tuple(status_reasons),
        )
    )

    interval_reasons: list[AuthorityFailureReason] = []
    if authority.effective_interval.is_unknown:
        interval_reasons.append(AuthorityFailureReason.EFFECTIVE_INTERVAL_UNKNOWN)
    elif not authority.effective_interval.contains(instant):
        interval_reasons.append(AuthorityFailureReason.AUTHORITY_NOT_EFFECTIVE)
    checks.append(
        SupportCheck(
            check_id=AuthorityCheckId.AUTHORITY_EFFECTIVE_INTERVAL,
            subject_id=citation.citation_id,
            outcome=(
                ValidationOutcome.FAILED
                if interval_reasons
                else ValidationOutcome.PASSED
            ),
            reasons=tuple(interval_reasons),
        )
    )

    quotation_reasons: list[AuthorityFailureReason] = []
    if not citation.quotations:
        quotation_reasons.append(AuthorityFailureReason.QUOTATION_MISSING)
    for quotation in citation.quotations:
        verification = verifications.get(quotation.quotation_id)
        if verification is None:
            quotation_reasons.append(AuthorityFailureReason.QUOTATION_UNVERIFIED)
        elif not verification.passed:
            quotation_reasons.append(AuthorityFailureReason.QUOTATION_MISMATCH)
        if quotation.source_content_sha256 != authority.source_reference.content_sha256:
            quotation_reasons.append(AuthorityFailureReason.QUOTATION_SOURCE_UNPINNED)
    checks.append(
        SupportCheck(
            check_id=AuthorityCheckId.AUTHORITY_QUOTATION,
            subject_id=citation.citation_id,
            outcome=(
                ValidationOutcome.FAILED
                if quotation_reasons
                else ValidationOutcome.PASSED
            ),
            reasons=tuple(quotation_reasons),
        )
    )

    return AuthorityCitationEvaluation(
        citation_id=citation.citation_id,
        checks=tuple(checks),
    )


def _authority_status_reasons(
    status: AuthorityStatus,
) -> list[AuthorityFailureReason]:
    if status is AuthorityStatus.VERIFIED:
        return []
    return [
        {
            AuthorityStatus.PROPOSED: AuthorityFailureReason.AUTHORITY_NOT_VERIFIED,
            AuthorityStatus.CHALLENGED: AuthorityFailureReason.AUTHORITY_CHALLENGED,
            AuthorityStatus.NOT_APPLICABLE: (
                AuthorityFailureReason.AUTHORITY_NOT_APPLICABLE
            ),
            AuthorityStatus.SUPERSEDED: AuthorityFailureReason.SUPERSEDED_AUTHORITY,
        }[status]
    ]


def _contrary_candidate_disposition(
    candidate: ContrarySearchCandidate,
    scope: MatterAuthorityScope,
) -> tuple[ContraryLeadDisposition, tuple[AuthorityFailureReason, ...]]:
    """Decide a candidate's disposition from recorded factors only.

    This function deliberately receives only applicability factors. The
    similarity score is not an input, so no similarity value can qualify a
    candidate as contrary authority.
    """

    reasons: list[AuthorityFailureReason] = []
    if candidate.citation is None:
        reasons.append(AuthorityFailureReason.CITATION_MISSING)
    if candidate.jurisdiction is None:
        reasons.append(AuthorityFailureReason.JURISDICTION_MISSING)
    else:
        if normalize_jurisdiction(candidate.jurisdiction) != normalize_jurisdiction(
            scope.jurisdiction
        ):
            return ContraryLeadDisposition.NOT_APPLICABLE, (
                AuthorityFailureReason.WRONG_JURISDICTION,
            )
    observed_status = candidate.observed_authority_status
    if observed_status is AuthorityStatus.SUPERSEDED:
        return ContraryLeadDisposition.NOT_APPLICABLE, (
            AuthorityFailureReason.SUPERSEDED_AUTHORITY,
        )
    if observed_status is AuthorityStatus.NOT_APPLICABLE:
        return ContraryLeadDisposition.NOT_APPLICABLE, (
            AuthorityFailureReason.AUTHORITY_NOT_APPLICABLE,
        )
    if observed_status is None or observed_status is AuthorityStatus.PROPOSED:
        reasons.append(AuthorityFailureReason.AUTHORITY_NOT_VERIFIED)
    elif observed_status is AuthorityStatus.CHALLENGED:
        reasons.append(AuthorityFailureReason.AUTHORITY_CHALLENGED)
    asserted = set(candidate.asserted_scope_keys)
    reasons.extend(
        AuthorityFailureReason.SCOPE_KEY_MISSING
        for key in scope.scope_keys
        if key not in asserted
    )
    verification = candidate.quotation_verification
    if verification is None:
        reasons.append(AuthorityFailureReason.QUOTATION_UNVERIFIED)
    elif not verification.passed:
        reasons.append(AuthorityFailureReason.QUOTATION_MISMATCH)
    if reasons:
        return ContraryLeadDisposition.SIMILARITY_ONLY, tuple(reasons)
    return ContraryLeadDisposition.QUALIFIED_LEAD, ()


def run_contrary_authority_search(
    scope: MatterAuthorityScope,
    *,
    plane: RetrievalPlaneBinding,
    candidates: tuple[ContrarySearchCandidate, ...],
    at: datetime,
) -> ContraryAuthoritySearchResult:
    """Record one contrary-authority search over a qualified retrieval plane."""

    instant = require_utc(at)
    identifiers = [candidate.candidate_id for candidate in candidates]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("contrary search candidates must have unique identifiers")
    evaluations = []
    for candidate in candidates:
        disposition, reasons = _contrary_candidate_disposition(candidate, scope)
        evaluations.append(
            ContraryCandidateEvaluation(
                candidate_id=candidate.candidate_id,
                similarity=candidate.similarity,
                disposition=disposition,
                citation=candidate.citation,
                reasons=reasons,
            )
        )
    return ContraryAuthoritySearchResult(
        plane=plane,
        searched_at=instant,
        evaluations=tuple(evaluations),
    )


def verify_authority_support(
    *,
    claim: LedgerClaim,
    scope: MatterAuthorityScope,
    citations: tuple[AuthorityCitation, ...],
    quotation_verifications: Mapping[DomainId, QuotationVerification] | None = None,
    remedies: tuple[Remedy, ...] = (),
    contrary_search: ContraryAuthoritySearchResult | None = None,
    at: datetime,
) -> AuthoritySupportVerification:
    """Verify the authority support behind one ledger claim, fail closed.

    Runs every deterministic applicability, status, quotation, contrary-search,
    and remedy check and reduces them to one outcome. No model output is an
    input to this function, so no failed check can be waived by a model.
    """

    instant = require_utc(at)
    citation_ids = [citation.citation_id for citation in citations]
    if len(citation_ids) != len(set(citation_ids)):
        raise ValueError("authority citations must have unique identifiers")
    if contrary_search is not None and contrary_search.searched_at > instant:
        raise ValueError("contrary search cannot postdate the evaluation instant")

    checks: list[SupportCheck] = []
    checks.append(
        SupportCheck(
            check_id=AuthorityCheckId.CITATION_PRESENT,
            subject_id=None,
            outcome=(
                ValidationOutcome.FAILED if not citations else ValidationOutcome.PASSED
            ),
            reasons=(
                (AuthorityFailureReason.AUTHORITY_CITATION_MISSING,)
                if not citations
                else ()
            ),
        )
    )
    for citation in citations:
        evaluation = evaluate_authority_citation(
            citation,
            scope,
            at=instant,
            expected_tenant_id=claim.tenant_id,
            expected_matter_id=claim.matter_id,
            quotation_verifications=quotation_verifications,
        )
        checks.extend(evaluation.checks)

    search_reasons: list[AuthorityFailureReason] = []
    if contrary_search is None:
        search_reasons.append(AuthorityFailureReason.CONTRARY_SEARCH_MISSING)
    checks.append(
        SupportCheck(
            check_id=AuthorityCheckId.CONTRARY_SEARCH_EXECUTED,
            subject_id=None,
            outcome=(
                ValidationOutcome.FAILED if search_reasons else ValidationOutcome.PASSED
            ),
            reasons=tuple(search_reasons),
        )
    )

    lead_reasons: list[AuthorityFailureReason] = []
    if contrary_search is not None and contrary_search.unresolved_leads:
        lead_reasons.append(AuthorityFailureReason.CONTRARY_LEADS_REQUIRE_REVIEW)
    checks.append(
        SupportCheck(
            check_id=AuthorityCheckId.CONTRARY_LEADS_REVIEWED,
            subject_id=None,
            outcome=(
                ValidationOutcome.FAILED if lead_reasons else ValidationOutcome.PASSED
            ),
            reasons=tuple(lead_reasons),
        )
    )

    checks.append(
        SupportCheck(
            check_id=AuthorityCheckId.REMEDY_PRESENT,
            subject_id=None,
            outcome=(
                ValidationOutcome.FAILED if not remedies else ValidationOutcome.PASSED
            ),
            reasons=((AuthorityFailureReason.REMEDY_MISSING,) if not remedies else ()),
        )
    )

    remedy_reasons: list[AuthorityFailureReason] = []
    for remedy in remedies:
        if (
            remedy.claim_id != claim.id
            or remedy.tenant_id != claim.tenant_id
            or remedy.matter_id != claim.matter_id
        ):
            remedy_reasons.append(AuthorityFailureReason.REMEDY_SCOPE_CROSSING)
        if not remedy.authority_ids:
            remedy_reasons.append(AuthorityFailureReason.REMEDY_AUTHORITY_MISSING)
    checks.append(
        SupportCheck(
            check_id=AuthorityCheckId.REMEDY_AUTHORITY,
            subject_id=None,
            outcome=(
                ValidationOutcome.FAILED if remedy_reasons else ValidationOutcome.PASSED
            ),
            reasons=tuple(remedy_reasons),
        )
    )

    outcome = (
        ValidationOutcome.PASSED
        if all(check.passed for check in checks)
        else ValidationOutcome.FAILED
    )
    return AuthoritySupportVerification(
        claim_id=claim.id,
        tenant_id=claim.tenant_id,
        matter_id=claim.matter_id,
        evaluated_at=instant,
        checks=tuple(checks),
        outcome=outcome,
    )


__all__ = [
    "AuthorityCheckId",
    "AuthorityCitation",
    "AuthorityCitationEvaluation",
    "AuthorityFailureReason",
    "AuthorityQuotation",
    "AuthoritySupportVerification",
    "ContraryAuthoritySearchResult",
    "ContraryCandidateEvaluation",
    "ContraryLeadDisposition",
    "ContrarySearchCandidate",
    "MatterAuthorityScope",
    "QuotationVerification",
    "RetrievalPlaneBinding",
    "SupportCheck",
    "normalize_jurisdiction",
    "run_contrary_authority_search",
    "verify_authority_quotation",
    "verify_authority_support",
]
