"""Versioned wire contracts for the governed Agent Run API."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sklegal_persistence.features.agent_runs.models import (
    AgentSpecificationEvidence,
    HumanDispositionDecision,
    SnapshotPins,
)


class AgentRunCommand(BaseModel):
    """Strict camel-case request model with no caller-controlled scope."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class AnalysisRequestBody(AgentRunCommand):
    """One versioned, public-synthetic analysis request.

    Tenant, Matter, principal, authorization evidence, request identity, and
    idempotency identity come from the protected route rather than this body.
    """

    schema_version: Literal["sklegal.agent-analysis-command/v1"] = (
        "sklegal.agent-analysis-command/v1"
    )
    analysis_kind: Literal["matter_analysis", "recommendation_refresh"]
    purpose: str = Field(min_length=1, max_length=160)
    classification: Literal["public"]
    public_synthetic: Literal[True] = True
    prompt_template_id: str = Field(min_length=1, max_length=160)
    prompt_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_schema_id: str = Field(min_length=1, max_length=160)
    output_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scoring_policy_id: str = Field(min_length=1, max_length=160)
    scoring_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retry_of_run_id: UUID | None = None
    snapshots: SnapshotPins
    agent_specification: AgentSpecificationEvidence
    requested_logical_route_id: str = Field(min_length=1, max_length=160)


class BlindChallengeBody(AgentRunCommand):
    schema_version: Literal["sklegal.agent-blind-challenge-command/v1"] = (
        "sklegal.agent-blind-challenge-command/v1"
    )
    expected_run_version: int = Field(ge=1)
    recommendation_id: UUID
    recommendation_version: int = Field(ge=1)
    challenger_spec_id: str = Field(min_length=1, max_length=160)
    challenger_spec_version: int = Field(ge=1)
    challenger_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_logical_route_id: str = Field(min_length=1, max_length=160)


class HumanDispositionBody(AgentRunCommand):
    schema_version: Literal["sklegal.agent-human-disposition-command/v1"] = (
        "sklegal.agent-human-disposition-command/v1"
    )
    expected_run_version: int = Field(ge=1)
    recommendation_id: UUID
    recommendation_version: int = Field(ge=1)
    decision: HumanDispositionDecision
    rationale: str = Field(min_length=1, max_length=4096)
    policy_revision: str = Field(min_length=1, max_length=160)
