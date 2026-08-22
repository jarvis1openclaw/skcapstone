"""Reviewed party identity resolution beyond exact normalization.

SKL-S2-08 builds on the conservative SKL-S1-04 exact normalization without
changing it. Fuzzy or model-derived observations can only propose a possible
match. A proposal never clears a conflict, merges parties, grants access, or
creates a waiver. Human review decisions are versioned, tenant-scoped,
reversible by superseding records, and carry immutable provenance. Missing
sources and source outages remain unknown and fail closed.

SKL-S2-02 coordination: pilot mapping review consumes the current reviewed
decision head for a proposal through
``IdentityResolutionService.current_decision`` and must preserve legacy names
exactly as recorded. Nothing here silently harmonizes legacy party names.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from .models import PolicyValue, Sha256, ShortCode, require_utc
from .normalization import normalize_party_name

IDENTITY_REVIEW_VERSION: Final = "sklegal-party-identity-review/v1"

IdentityEntityKind = Literal["person", "family", "trust", "estate", "company", "other"]

NameText = Annotated[str, StringConstraints(min_length=1, max_length=512)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=1024)]

_NAME_FAMILY_WEIGHT: Final = 0.40
_ALIAS_FAMILY_WEIGHT: Final = 0.20
_EXTERNAL_ID_FAMILY_WEIGHT: Final = 0.25
_JURISDICTION_FAMILY_WEIGHT: Final = 0.075
_ENTITY_KIND_FAMILY_WEIGHT: Final = 0.075


class IdentityProvenance(PolicyValue):
    """Immutable pointer to one source record backing an identity fact."""

    source_system: ShortCode
    source_record_id: ShortCode
    content_sha256: Sha256
    retrieved_at: datetime

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        require_utc(self.retrieved_at)
        return self


class ExternalIdentifier(PolicyValue):
    """Stable external identifier attached only with a recorded license basis."""

    scheme: ShortCode
    value: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    license_basis: ShortCode
    provenance: IdentityProvenance


class SourceBackedAlias(PolicyValue):
    """Alias preserved exactly as its source recorded it."""

    display_name: NameText
    provenance: IdentityProvenance


class IdentityContext(PolicyValue):
    """Jurisdiction and entity-type context for one observed party."""

    entity_kind: IdentityEntityKind
    jurisdiction: ShortCode | None = None


class PartyObservation(PolicyValue):
    """Observed party facts from sources; never an identity decision."""

    party_id: UUID
    tenant_id: UUID
    display_name: NameText
    context: IdentityContext
    aliases: tuple[SourceBackedAlias, ...] = ()
    external_identifiers: tuple[ExternalIdentifier, ...] = ()
    provenance: tuple[IdentityProvenance, ...] = Field(min_length=1)
    sources_available: bool = True


class CandidateFeature(PolicyValue):
    """One explainable signal behind a possible-match proposal."""

    feature: ShortCode
    weight: float = Field(ge=0.0, le=1.0)
    detail: LongText


class MatchUncertainty(PolicyValue):
    """Explainable uncertainty for a proposal; unknown state stays scoreless."""

    basis: Literal["features", "unknown"]
    score: float = Field(ge=0.0, le=1.0)
    rationale: LongText

    @model_validator(mode="after")
    def validate_basis(self) -> Self:
        if self.basis == "unknown" and self.score != 0.0:
            raise ValueError("unknown identity state cannot carry a match score")
        return self


class IdentityCandidateProposal(PolicyValue):
    """Reviewer-facing possible-match proposal with no side effects."""

    proposal_id: UUID
    tenant_id: UUID
    subject: PartyObservation
    target: PartyObservation
    features: tuple[CandidateFeature, ...]
    uncertainty: MatchUncertainty
    contradictions: tuple[LongText, ...] = ()
    proposed_at: datetime
    effect: Literal["propose_possible_match"] = "propose_possible_match"
    resolution_version: Literal["sklegal-party-identity-review/v1"] = (
        "sklegal-party-identity-review/v1"
    )

    @model_validator(mode="after")
    def validate_proposal(self) -> Self:
        require_utc(self.proposed_at)
        if self.subject.tenant_id != self.tenant_id or (
            self.target.tenant_id != self.tenant_id
        ):
            raise ValueError("identity proposal crosses tenant scope")
        if self.subject.party_id == self.target.party_id:
            raise ValueError("identity proposal requires two distinct parties")
        if self.uncertainty.basis == "unknown" and self.features:
            raise ValueError("unknown identity state cannot carry match features")
        return self


class IdentityReviewOutcome(StrEnum):
    CONFIRMED_SAME_PARTY = "confirmed_same_party"
    REJECTED_DISTINCT = "rejected_distinct"
    INCONCLUSIVE = "inconclusive"


class IdentityReviewDecision(PolicyValue):
    """Versioned human review record; reversible only by superseding it."""

    decision_id: UUID
    proposal_id: UUID
    tenant_id: UUID
    outcome: IdentityReviewOutcome
    rationale: LongText
    decided_by_principal_id: UUID
    decided_at: datetime
    decision_version: int = Field(ge=1)
    supersedes_decision_id: UUID | None = None
    effect: Literal["review_record_only"] = "review_record_only"

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        require_utc(self.decided_at)
        if (self.decision_version == 1) != (self.supersedes_decision_id is None):
            raise ValueError("only the first review decision lacks a predecessor")
        return self


class LabeledIdentityPair(PolicyValue):
    """Synthetic or approved evaluation label bound to one exact proposal."""

    pair_id: UUID
    proposal_id: UUID
    same_party: bool
    synthetic: Literal[True] = True


class IdentityEvaluationReport(PolicyValue):
    """False-positive and false-negative counts over synthetic labels."""

    threshold: float = Field(ge=0.0, le=1.0)
    evaluated_pair_count: int = Field(ge=0)
    true_positive_count: int = Field(ge=0)
    false_positive_count: int = Field(ge=0)
    true_negative_count: int = Field(ge=0)
    false_negative_count: int = Field(ge=0)
    evaluation_version: Literal["sklegal-party-identity-review/v1"] = (
        "sklegal-party-identity-review/v1"
    )

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        total = (
            self.true_positive_count
            + self.false_positive_count
            + self.true_negative_count
            + self.false_negative_count
        )
        if total != self.evaluated_pair_count:
            raise ValueError("evaluation counts must cover every labeled pair")
        return self


def _normalized_tokens(observation: PartyObservation) -> tuple[str, ...]:
    normalized = normalize_party_name(
        observation.display_name, observation.context.entity_kind
    )
    return tuple(normalized.split())


def _token_overlap(left: Sequence[str], right: Sequence[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    if not union:
        return 0.0
    return len(left_set & right_set) / len(union)


def _name_features(
    subject: PartyObservation, target: PartyObservation
) -> list[CandidateFeature]:
    subject_tokens = _normalized_tokens(subject)
    target_tokens = _normalized_tokens(target)
    exact = subject_tokens == target_tokens
    overlap = _token_overlap(subject_tokens, target_tokens)
    return [
        CandidateFeature(
            feature="normalized-name",
            weight=1.0 if exact else 0.0,
            detail=(
                "normalized names are exactly equal under "
                "sklegal-party-normalization/v1"
                if exact
                else "normalized names differ under sklegal-party-normalization/v1"
            ),
        ),
        CandidateFeature(
            feature="name-token-overlap",
            weight=overlap,
            detail=(
                f"{len(set(subject_tokens) & set(target_tokens))} of "
                f"{len(set(subject_tokens) | set(target_tokens))} "
                "normalized name tokens are shared"
            ),
        ),
    ]


def _alias_feature(
    subject: PartyObservation, target: PartyObservation
) -> CandidateFeature | None:
    comparisons: list[tuple[tuple[str, ...], tuple[str, ...], str]] = []
    subject_tokens = _normalized_tokens(subject)
    target_tokens = _normalized_tokens(target)
    for alias in target.aliases:
        alias_tokens = tuple(
            normalize_party_name(alias.display_name, target.context.entity_kind).split()
        )
        comparisons.append((subject_tokens, alias_tokens, "subject-to-target-alias"))
    for alias in subject.aliases:
        alias_tokens = tuple(
            normalize_party_name(
                alias.display_name, subject.context.entity_kind
            ).split()
        )
        comparisons.append((target_tokens, alias_tokens, "target-to-subject-alias"))
    if not comparisons:
        return None
    best = 0.0
    best_exact = False
    for left, right, _direction in comparisons:
        if left == right:
            best_exact = True
            best = 1.0
            break
        best = max(best, _token_overlap(left, right))
    return CandidateFeature(
        feature="source-backed-alias",
        weight=best,
        detail=(
            "a source-backed alias normalizes exactly to the other party name"
            if best_exact
            else "source-backed aliases share only partial normalized tokens"
        ),
    )


def _external_identifier_feature(
    subject: PartyObservation, target: PartyObservation
) -> tuple[CandidateFeature | None, list[str]]:
    subject_ids: dict[str, list[str]] = {}
    for identifier in subject.external_identifiers:
        subject_ids.setdefault(identifier.scheme, []).append(identifier.value)
    target_ids: dict[str, list[str]] = {}
    for identifier in target.external_identifiers:
        target_ids.setdefault(identifier.scheme, []).append(identifier.value)
    shared_schemes = sorted(set(subject_ids) & set(target_ids))
    if not shared_schemes:
        return None, []
    matched = 0
    contradictions: list[str] = []
    for scheme in shared_schemes:
        if set(subject_ids[scheme]) & set(target_ids[scheme]):
            matched += 1
        else:
            contradictions.append(
                f"external identifier scheme '{scheme}' has conflicting values "
                "across sources; both values are preserved"
            )
    return (
        CandidateFeature(
            feature="external-identifier-agreement",
            weight=matched / len(shared_schemes),
            detail=(
                f"{matched} of {len(shared_schemes)} shared external identifier "
                "schemes agree on a stable value"
            ),
        ),
        contradictions,
    )


def _context_features(
    subject: PartyObservation, target: PartyObservation
) -> tuple[list[CandidateFeature], list[str]]:
    features: list[CandidateFeature] = []
    contradictions: list[str] = []
    subject_jurisdiction = subject.context.jurisdiction
    target_jurisdiction = target.context.jurisdiction
    if subject_jurisdiction is not None and target_jurisdiction is not None:
        same = subject_jurisdiction == target_jurisdiction
        features.append(
            CandidateFeature(
                feature="jurisdiction-context",
                weight=1.0 if same else 0.0,
                detail=(
                    "both observations record the same jurisdiction"
                    if same
                    else "observations record different jurisdictions"
                ),
            )
        )
    same_kind = subject.context.entity_kind == target.context.entity_kind
    features.append(
        CandidateFeature(
            feature="entity-kind-context",
            weight=1.0 if same_kind else 0.0,
            detail=(
                "both observations record the same entity kind"
                if same_kind
                else "observations record different entity kinds"
            ),
        )
    )
    if not same_kind:
        contradictions.append(
            "entity-type context differs between sources; both entity kinds "
            "are preserved and no harmonization was applied"
        )
    return features, contradictions


def _score_pair(
    subject: PartyObservation, target: PartyObservation
) -> tuple[tuple[CandidateFeature, ...], tuple[str, ...], float]:
    contradictions: list[str] = []
    weighted: list[tuple[float, CandidateFeature]] = []

    name_features = _name_features(subject, target)
    name_share = _NAME_FAMILY_WEIGHT / len(name_features)
    for feature in name_features:
        weighted.append((name_share, feature))

    alias_feature = _alias_feature(subject, target)
    if alias_feature is not None:
        weighted.append((_ALIAS_FAMILY_WEIGHT, alias_feature))

    external_feature, external_contradictions = _external_identifier_feature(
        subject, target
    )
    if external_feature is not None:
        weighted.append((_EXTERNAL_ID_FAMILY_WEIGHT, external_feature))
    contradictions.extend(external_contradictions)

    context_features, context_contradictions = _context_features(subject, target)
    for feature in context_features:
        family = (
            _JURISDICTION_FAMILY_WEIGHT
            if feature.feature == "jurisdiction-context"
            else _ENTITY_KIND_FAMILY_WEIGHT
        )
        weighted.append((family, feature))
    contradictions.extend(context_contradictions)

    total_weight = sum(family for family, _feature in weighted)
    score = sum(family * feature.weight for family, feature in weighted) / total_weight
    features = tuple(feature for _family, feature in weighted)
    return features, tuple(contradictions), score


class IdentityResolutionService:
    """Propose possible matches and record reviewer decisions only."""

    def propose(
        self,
        *,
        proposal_id: UUID,
        subject: PartyObservation,
        target: PartyObservation,
        proposed_at: datetime,
    ) -> IdentityCandidateProposal:
        """Build a reviewer-facing proposal; unknown state fails closed."""
        require_utc(proposed_at)
        if subject.tenant_id != target.tenant_id:
            raise ValueError("identity proposal cannot cross tenant scope")
        if subject.party_id == target.party_id:
            raise ValueError("identity proposal requires two distinct parties")
        if not (subject.sources_available and target.sources_available):
            return IdentityCandidateProposal(
                proposal_id=proposal_id,
                tenant_id=subject.tenant_id,
                subject=subject,
                target=target,
                features=(),
                uncertainty=MatchUncertainty(
                    basis="unknown",
                    score=0.0,
                    rationale=(
                        "missing sources or source outage: identity remains "
                        "unknown and fails closed"
                    ),
                ),
                contradictions=(),
                proposed_at=proposed_at,
            )
        features, contradictions, score = _score_pair(subject, target)
        feature_names = ", ".join(feature.feature for feature in features)
        return IdentityCandidateProposal(
            proposal_id=proposal_id,
            tenant_id=subject.tenant_id,
            subject=subject,
            target=target,
            features=features,
            uncertainty=MatchUncertainty(
                basis="features",
                score=score,
                rationale=f"deterministic feature aggregation over: {feature_names}",
            ),
            contradictions=contradictions,
            proposed_at=proposed_at,
        )

    def record_decision(
        self,
        *,
        proposal: IdentityCandidateProposal,
        decision: IdentityReviewDecision,
        prior: IdentityReviewDecision | None = None,
    ) -> IdentityReviewDecision:
        """Record one reviewer decision on the exact versioned chain."""
        if decision.proposal_id != proposal.proposal_id:
            raise ValueError("review decision must bind its exact proposal")
        if decision.tenant_id != proposal.tenant_id:
            raise ValueError("review decision crosses tenant scope")
        if proposal.uncertainty.basis == "unknown":
            raise ValueError("unknown identity state cannot be decided")
        _validate_chain_link(decision, prior)
        return decision

    def current_decision(
        self, decisions: Sequence[IdentityReviewDecision]
    ) -> IdentityReviewDecision:
        """Return the head of one linear, tenant-consistent decision chain."""
        if not decisions:
            raise ValueError("reviewed identity requires at least one decision")
        ordered = sorted(decisions, key=lambda item: item.decision_version)
        prior: IdentityReviewDecision | None = None
        for decision in ordered:
            if prior is not None and (
                decision.proposal_id != prior.proposal_id
                or decision.tenant_id != prior.tenant_id
            ):
                raise ValueError("review chain mixes proposals or tenants")
            _validate_chain_link(decision, prior)
            prior = decision
        return ordered[-1]


def _validate_chain_link(
    decision: IdentityReviewDecision, prior: IdentityReviewDecision | None
) -> None:
    if prior is None:
        if decision.decision_version != 1:
            raise ValueError("first review decision must open version 1")
        return
    if decision.decision_version != prior.decision_version + 1:
        raise ValueError("review decisions must extend the exact version chain")
    if decision.supersedes_decision_id != prior.decision_id:
        raise ValueError("review decision must supersede the exact prior head")
    if decision.decided_at < prior.decided_at:
        raise ValueError("review decision cannot precede its predecessor")


def evaluate_candidate_quality(
    *,
    labeled_pairs: Sequence[LabeledIdentityPair],
    proposals: Sequence[IdentityCandidateProposal],
    threshold: float,
) -> IdentityEvaluationReport:
    """Count false positives and negatives against synthetic labels only.

    A proposal suggests a possible match when its uncertainty basis is
    feature-backed and its score meets the threshold. Unknown state never
    suggests a match, so outages fail closed in evaluation as well.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("evaluation threshold must lie within [0, 1]")
    proposals_by_id: dict[UUID, IdentityCandidateProposal] = {}
    for proposal in proposals:
        if proposal.proposal_id in proposals_by_id:
            raise ValueError("evaluation proposals must have unique identifiers")
        proposals_by_id[proposal.proposal_id] = proposal
    pair_ids = tuple(pair.pair_id for pair in labeled_pairs)
    if len(pair_ids) != len(set(pair_ids)):
        raise ValueError("evaluation labels must have unique identifiers")

    true_positive = 0
    false_positive = 0
    true_negative = 0
    false_negative = 0
    for pair in labeled_pairs:
        matched = proposals_by_id.get(pair.proposal_id)
        if matched is None:
            raise ValueError("evaluation label lacks its exact proposal")
        suggested = (
            matched.uncertainty.basis == "features"
            and matched.uncertainty.score >= threshold
        )
        if suggested and pair.same_party:
            true_positive += 1
        elif suggested:
            false_positive += 1
        elif pair.same_party:
            false_negative += 1
        else:
            true_negative += 1
    return IdentityEvaluationReport(
        threshold=threshold,
        evaluated_pair_count=len(labeled_pairs),
        true_positive_count=true_positive,
        false_positive_count=false_positive,
        true_negative_count=true_negative,
        false_negative_count=false_negative,
    )
