"""Typed records for versioned, bounded SKLegal agent specifications.

An AgentSpec is a bounded role contract: it pins the task purpose, input
and output schemas, allowed context, an explicit tool allowlist, finite
budgets, model routes from the pinned gateway registry, a retry class, and
a human escalation target. A spec is immutable data. It grants no authority
by itself; the tool gateway and CapAuth mediate every call at run time.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sklegal_domain import DataClassification

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ShortCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9][a-z0-9._:/-]*$",
    ),
]

SPEC_SCHEMA = "sklegal-agent-spec/v1"

MAX_TOOL_CALLS_PER_RUN = 64
MAX_WALL_CLOCK_SECONDS = 3600
MAX_RETRIES = 8

HUMAN_ESCALATION_PREFIX = "human."

RETRY_CLASSES = ("interactive", "batch", "long_context", "model")

_CLASSIFICATION_RANK = {
    classification: rank for rank, classification in enumerate(DataClassification)
}


def classification_rank(classification: DataClassification) -> int:
    """Order classifications so ceilings can be compared deterministically."""

    return _CLASSIFICATION_RANK[classification]


class AgentSpecValue(BaseModel):
    """Immutable strict base for agent specification boundary values."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class SchemaPin(AgentSpecValue):
    """Exact schema identity pinned by id and SHA-256 of the schema artifact."""

    schema_id: ShortCode
    sha256: Sha256


class SpecBudgets(AgentSpecValue):
    """Finite per-run budgets. Every budget is explicit and capped."""

    max_tool_calls: int = Field(ge=1, le=MAX_TOOL_CALLS_PER_RUN)
    max_wall_clock_seconds: int = Field(gt=0, le=MAX_WALL_CLOCK_SECONDS)
    max_retries: int = Field(ge=0, le=MAX_RETRIES)


class AllowedContext(AgentSpecValue):
    """Context the agent may see. Context is always scoped to one matter."""

    classification_ceiling: DataClassification
    matter_scoped: bool


class AgentSpec(AgentSpecValue):
    """One immutable version of a bounded agent role contract."""

    spec_id: ShortCode
    version: int = Field(ge=1)
    task_purpose: str = Field(min_length=1, max_length=2000)
    input_schema: SchemaPin
    allowed_context: AllowedContext
    tool_allowlist: tuple[ShortCode, ...] = ()
    budgets: SpecBudgets
    model_routes: tuple[ShortCode, ...] = Field(min_length=1)
    output_schema: SchemaPin
    retry_class: Literal["interactive", "batch", "long_context", "model"]
    escalation_target: ShortCode


class AgentSpecRecord(AgentSpecValue):
    """A validated spec together with the hash that pins its source file."""

    spec: AgentSpec
    spec_sha256: Sha256
    source_name: str = Field(min_length=1, max_length=260)
