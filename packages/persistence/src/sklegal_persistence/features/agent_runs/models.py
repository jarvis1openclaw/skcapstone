"""Immutable persistence models for governed Agent Runs.

The records in this module contain typed proposals and evidence, never raw
credentials or prompt text. Every mutation creates a new ``AgentRunRecord``
version and preserves the prior version in the repository history.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AgentRunValue(BaseModel):
    """Frozen, strict, camel-case wire-compatible value."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class AgentRunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class RecommendationReviewState(StrEnum):
    PENDING = "pending"
    CHALLENGED = "challenged"
    DISPOSED = "disposed"


class ChallengeOutcome(StrEnum):
    NO_DEFECT = "no_defect"
    DEFECT_FOUND = "defect_found"


class HumanDispositionDecision(StrEnum):
    ACCEPT_AS_PROPOSED_TASK = "accept_as_proposed_task"
    REQUEST_WORK_PRODUCT_PROPOSAL = "request_work_product_proposal"
    REJECT = "reject"
    CHANGES_REQUESTED = "changes_requested"


class SnapshotPins(AgentRunValue):
    matter_snapshot_sha256: Sha256
    corpus_release_id: str = Field(min_length=1, max_length=255)
    corpus_snapshot_sha256: Sha256
    authority_snapshot_sha256: Sha256
    policy_snapshot_sha256: Sha256


class AgentSpecificationEvidence(AgentRunValue):
    spec_id: str = Field(min_length=1, max_length=160)
    spec_version: int = Field(ge=1)
    spec_sha256: Sha256
    deployment_revision: str = Field(min_length=1, max_length=160)
    deployment_sha256: Sha256


class ModelRouteEvidence(AgentRunValue):
    logical_route_id: str = Field(min_length=1, max_length=160)
    transport_profile_revision: str = Field(min_length=1, max_length=160)
    transport_profile_sha256: Sha256
    gateway_revision: str = Field(min_length=1, max_length=160)
    gateway_config_sha256: Sha256
    catalog_revision: str = Field(min_length=1, max_length=160)
    route_policy_revision: str = Field(min_length=1, max_length=160)
    gateway_request_id: str = Field(min_length=1, max_length=160)
    backend: str = Field(min_length=1, max_length=64)
    capacity_domain: str = Field(min_length=1, max_length=160)
    requested_model_or_bucket: str = Field(min_length=1, max_length=160)
    bucket_member: str = Field(min_length=1, max_length=160)
    served_model_name: str = Field(min_length=1, max_length=160)
    served_model_revision: str = Field(min_length=1, max_length=160)
    retry_count: int = Field(ge=0, le=8)
    failover_count: int = Field(ge=0, le=8)
    saturated: bool
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)


class AuthorizationEvidence(AgentRunValue):
    decision_id: UUID
    correlation_id: UUID
    principal_id: UUID
    capability: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=160)
    verifier_policy_version: str = Field(min_length=1, max_length=160)
    revocation_revision: str = Field(min_length=1, max_length=160)
    credential_digest: Sha256
    allowed: Literal[True] = True


class AnalysisRequestRecord(AgentRunValue):
    schema_version: Literal["sklegal.agent-analysis-request/v1"] = (
        "sklegal.agent-analysis-request/v1"
    )
    tenant_id: UUID
    matter_id: UUID
    principal_id: UUID
    request_id: UUID
    idempotency_key: UUID
    analysis_kind: Literal[
        "matter_analysis", "recommendation_refresh", "blind_challenge"
    ]
    purpose: str = Field(min_length=1, max_length=160)
    classification: Literal["public"]
    public_synthetic: Literal[True] = True
    prompt_template_id: str = Field(min_length=1, max_length=160)
    prompt_template_sha256: Sha256
    output_schema_id: str = Field(min_length=1, max_length=160)
    output_schema_sha256: Sha256
    scoring_policy_id: str = Field(min_length=1, max_length=160)
    scoring_policy_sha256: Sha256
    retry_of_run_id: UUID | None = None
    snapshots: SnapshotPins
    agent_specification: AgentSpecificationEvidence
    requested_logical_route_id: str = Field(min_length=1, max_length=160)


class ToolCallEvidence(AgentRunValue):
    tool_call_id: UUID
    sequence: int = Field(ge=1)
    tool_id: str = Field(min_length=1, max_length=160)
    capability_decision_id: UUID
    arguments_sha256: Sha256
    result_sha256: Sha256
    started_at: datetime
    completed_at: datetime
    outcome: Literal["completed", "denied", "failed"]
    error_code: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def validate_error(self) -> ToolCallEvidence:
        if self.outcome == "completed" and self.error_code is not None:
            raise ValueError("completed tool call cannot carry an error")
        if self.outcome != "completed" and not self.error_code:
            raise ValueError("failed or denied tool call requires an error code")
        if self.completed_at < self.started_at:
            raise ValueError("tool call completion cannot precede start")
        return self


class SourceRoleEvidence(AgentRunValue):
    source_role: Literal[
        "course_instruction",
        "current_authority",
        "matter_record",
        "model_inference",
    ]
    source_id: str = Field(min_length=1, max_length=255)
    source_version: str = Field(min_length=1, max_length=160)
    source_sha256: Sha256
    exact_locator: str = Field(min_length=1, max_length=512)
    retrieval_trace_sha256: Sha256
    verification_state: Literal[
        "source_derived", "official_verified", "matter_recorded", "model_proposed"
    ]


class ScoreDimension(AgentRunValue):
    dimension: Literal["evidentiary_support", "procedural_fit", "urgency", "readiness"]
    value: int = Field(ge=0, le=100)
    rationale_sha256: Sha256


class RecommendationRecord(AgentRunValue):
    recommendation_id: UUID
    version: int = Field(ge=1)
    target_kind: Literal["issue", "claim", "defense", "element", "proceeding"]
    target_id: UUID
    proceeding_phase: str = Field(min_length=1, max_length=160)
    proposed_output: Literal["task", "work_product"]
    reason: str = Field(min_length=1, max_length=4096)
    urgency: Literal["low", "medium", "high", "critical"]
    prerequisites: tuple[str, ...] = ()
    prohibited_sequencing: tuple[str, ...] = ()
    score_dimensions: tuple[ScoreDimension, ...] = Field(min_length=4, max_length=4)
    score_total: int = Field(ge=0, le=100)
    confidence_basis_points: int = Field(ge=0, le=10_000)
    evidence: tuple[SourceRoleEvidence, ...] = Field(min_length=1)
    required_capability: str = Field(min_length=1, max_length=160)
    downstream_human_gate: str = Field(min_length=1, max_length=160)
    review_state: RecommendationReviewState = RecommendationReviewState.PENDING

    @model_validator(mode="after")
    def validate_scoring_and_sources(self) -> RecommendationRecord:
        names = tuple(item.dimension for item in self.score_dimensions)
        if len(names) != len(set(names)) or set(names) != {
            "evidentiary_support",
            "procedural_fit",
            "urgency",
            "readiness",
        }:
            raise ValueError("recommendation requires four unique score dimensions")
        expected = sum(item.value for item in self.score_dimensions) // 4
        if self.score_total != expected:
            raise ValueError("recommendation score does not match scoring policy")
        if "model_inference" not in {item.source_role for item in self.evidence}:
            raise ValueError("recommendation must identify model inference evidence")
        return self


class ChallengeDefectRecord(AgentRunValue):
    defect_kind: Literal[
        "source_mismatch",
        "contrary_authority",
        "missing_fact",
        "procedural_sequence",
        "scoring_error",
        "unsupported_inference",
    ]
    description: str = Field(min_length=1, max_length=2048)
    evidence_sha256: Sha256


class BlindChallengeRecord(AgentRunValue):
    challenge_id: UUID
    version: int = Field(ge=1)
    recommendation_id: UUID
    recommendation_version: int = Field(ge=1)
    challenger_spec_id: str = Field(min_length=1, max_length=160)
    challenger_spec_version: int = Field(ge=1)
    challenger_spec_sha256: Sha256
    challenger_route: ModelRouteEvidence
    blind_input_sha256: Sha256
    independent_output_sha256: Sha256
    authorization: AuthorizationEvidence
    saw_challenged_conclusion: Literal[False] = False
    outcome: ChallengeOutcome
    defects: tuple[ChallengeDefectRecord, ...] = ()
    created_at: datetime

    @model_validator(mode="after")
    def validate_defects(self) -> BlindChallengeRecord:
        if (
            self.authorization.capability != "claim.review"
            or self.authorization.purpose != "claim_review"
        ):
            raise ValueError("blind challenge requires claim review authorization")
        if self.outcome is ChallengeOutcome.DEFECT_FOUND and not self.defects:
            raise ValueError("defect-found challenge requires defects")
        if self.outcome is ChallengeOutcome.NO_DEFECT and self.defects:
            raise ValueError("no-defect challenge cannot carry defects")
        return self


class HumanDispositionRecord(AgentRunValue):
    disposition_id: UUID
    version: int = Field(ge=1)
    recommendation_id: UUID
    recommendation_version: int = Field(ge=1)
    decision: HumanDispositionDecision
    reviewer_principal_id: UUID
    rationale: str = Field(min_length=1, max_length=4096)
    policy_revision: str = Field(min_length=1, max_length=160)
    capability_decision_id: UUID
    authorization: AuthorizationEvidence
    decided_at: datetime
    creates_domain_record: Literal[False] = False
    external_effect: Literal[False] = False

    @model_validator(mode="after")
    def validate_reviewer_authorization(self) -> HumanDispositionRecord:
        if (
            self.authorization.principal_id != self.reviewer_principal_id
            or self.authorization.decision_id != self.capability_decision_id
            or self.authorization.capability != "claim.review"
            or self.authorization.purpose != "claim_review"
        ):
            raise ValueError("human disposition requires exact reviewer authorization")
        return self


class AgentRunAttemptRecord(AgentRunValue):
    attempt_id: UUID
    attempt_number: int = Field(ge=1, le=9)
    started_at: datetime
    completed_at: datetime
    outcome: AgentRunStatus
    error_code: str | None = Field(default=None, max_length=160)
    retryable: bool

    @model_validator(mode="after")
    def validate_attempt(self) -> AgentRunAttemptRecord:
        if self.completed_at < self.started_at:
            raise ValueError("attempt completion cannot precede start")
        if self.outcome is AgentRunStatus.COMPLETED and self.error_code is not None:
            raise ValueError("completed attempt cannot carry an error")
        if self.outcome is not AgentRunStatus.COMPLETED and not self.error_code:
            raise ValueError("failed attempt requires a sanitized error code")
        return self


class AgentRunRecord(AgentRunValue):
    schema_version: Literal["sklegal.agent-run/v1"] = "sklegal.agent-run/v1"
    run_id: UUID
    version: int = Field(ge=1)
    tenant_id: UUID
    matter_id: UUID
    request: AnalysisRequestRecord
    status: AgentRunStatus
    workflow_id: str = Field(min_length=1, max_length=255)
    workflow_revision: str = Field(min_length=1, max_length=160)
    authorization: AuthorizationEvidence
    route: ModelRouteEvidence | None = None
    attempts: tuple[AgentRunAttemptRecord, ...] = Field(min_length=1)
    tool_calls: tuple[ToolCallEvidence, ...] = ()
    recommendations: tuple[RecommendationRecord, ...] = ()
    challenges: tuple[BlindChallengeRecord, ...] = ()
    dispositions: tuple[HumanDispositionRecord, ...] = ()
    proposal_payload_sha256: Sha256 | None = None
    audit_event_ids: tuple[UUID, ...] = Field(min_length=1)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_scope_and_completion(self) -> AgentRunRecord:
        if (
            self.request.tenant_id != self.tenant_id
            or self.request.matter_id != self.matter_id
        ):
            raise ValueError("Agent Run scope differs from request scope")
        if (
            self.authorization.principal_id != self.request.principal_id
            or self.authorization.purpose != self.request.purpose
            or self.authorization.capability != "claim.propose"
            or self.authorization.purpose != "claim_development"
        ):
            raise ValueError("Agent Run authorization differs from request evidence")
        if self.updated_at < self.created_at:
            raise ValueError("Agent Run update cannot precede creation")
        attempt_numbers = tuple(item.attempt_number for item in self.attempts)
        if (
            len(attempt_numbers) != len(set(attempt_numbers))
            or tuple(sorted(attempt_numbers)) != attempt_numbers
        ):
            raise ValueError("Agent Run attempts must be unique and ordered")
        if self.attempts[-1].outcome is not self.status:
            raise ValueError("Agent Run status differs from its latest attempt")
        tool_sequences = tuple(item.sequence for item in self.tool_calls)
        if tool_sequences != tuple(range(1, len(tool_sequences) + 1)):
            raise ValueError("Agent Run tool-call evidence is not contiguous")
        if len(self.audit_event_ids) != len(set(self.audit_event_ids)):
            raise ValueError("Agent Run audit references must be unique")
        if self.status is AgentRunStatus.COMPLETED:
            if self.route is None or self.proposal_payload_sha256 is None:
                raise ValueError(
                    "completed Agent Run requires route and proposal evidence"
                )
            if not self.recommendations:
                raise ValueError("completed Agent Run requires a typed recommendation")
        else:
            if self.recommendations or self.proposal_payload_sha256 is not None:
                raise ValueError("failed Agent Run cannot carry recommendations")
        if self.route is not None and (
            self.route.logical_route_id != self.request.requested_logical_route_id
        ):
            raise ValueError("Agent Run route differs from requested logical route")
        recommendation_keys = tuple(
            (item.recommendation_id, item.version) for item in self.recommendations
        )
        if len(recommendation_keys) != len(set(recommendation_keys)):
            raise ValueError("Agent Run recommendation versions must be unique")
        challenge_keys = tuple(
            (item.challenge_id, item.version) for item in self.challenges
        )
        if len(challenge_keys) != len(set(challenge_keys)):
            raise ValueError("Agent Run challenge versions must be unique")
        disposition_keys = tuple(
            (item.disposition_id, item.version) for item in self.dispositions
        )
        if len(disposition_keys) != len(set(disposition_keys)):
            raise ValueError("Agent Run disposition versions must be unique")
        known_recommendations = set(recommendation_keys)
        for challenge in self.challenges:
            if (
                challenge.recommendation_id,
                challenge.recommendation_version,
            ) not in known_recommendations:
                raise ValueError(
                    "challenge references an unknown recommendation version"
                )
        for disposition in self.dispositions:
            if (
                disposition.recommendation_id,
                disposition.recommendation_version,
            ) not in known_recommendations:
                raise ValueError(
                    "disposition references an unknown recommendation version"
                )
        challenge_recommendations = {
            (item.recommendation_id, item.recommendation_version)
            for item in self.challenges
        }
        disposition_recommendations = {
            (item.recommendation_id, item.recommendation_version)
            for item in self.dispositions
        }
        for recommendation in self.recommendations:
            key = (recommendation.recommendation_id, recommendation.version)
            if (
                recommendation.review_state is RecommendationReviewState.PENDING
                and key in challenge_recommendations | disposition_recommendations
            ):
                raise ValueError("pending recommendation has recorded review evidence")
            if (
                recommendation.review_state is RecommendationReviewState.CHALLENGED
                and key not in challenge_recommendations
            ):
                raise ValueError("challenged recommendation lacks challenge evidence")
            if (
                recommendation.review_state is RecommendationReviewState.DISPOSED
                and key not in disposition_recommendations
            ):
                raise ValueError("disposed recommendation lacks disposition evidence")
        return self


class AgentRunAuditEvent(AgentRunValue):
    event_id: UUID
    tenant_id: UUID
    matter_id: UUID
    run_id: UUID
    run_version: int = Field(ge=1)
    correlation_id: UUID
    actor_principal_id: UUID
    action: Literal[
        "agent_run.recorded",
        "agent_run.challenge_recorded",
        "agent_run.disposition_recorded",
    ]
    outcome: Literal["completed", "failed", "denied"]
    subject_sha256: Sha256
    occurred_at: datetime


def canonical_sha256(value: Any) -> str:
    """Hash one JSON-compatible value with stable ordering."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
