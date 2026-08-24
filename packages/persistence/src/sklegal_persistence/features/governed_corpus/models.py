"""Immutable records for governed Matter corpus retrieval."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
OpaqueName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*$",
    ),
]
BoundedText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16384)
]
ContinuationCursor = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=2048,
        pattern=r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$",
    ),
]


class CorpusValue(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class Classification(IntEnum):
    PUBLIC = 0
    INTERNAL = 1
    CONFIDENTIAL = 2
    PRIVILEGED = 3


class RetrievalMode(StrEnum):
    FULL_TEXT = "full_text"
    VECTOR_EXACT = "vector_exact"
    HYBRID_RRF = "hybrid_rrf"


class ProjectionGuardStatus(StrEnum):
    CURRENT = "current"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    UNKNOWN = "unknown"
    UNAUTHORIZED = "unauthorized"


class VerificationState(StrEnum):
    CORPUS_PROPOSAL = "corpus_proposal"
    SOURCE_VERIFIED = "source_verified"
    OFFICIAL_AUTHORITY_VERIFIED = "official_authority_verified"


class SpanLocator(CorpusValue):
    kind: Literal["character", "page_character"]
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    page: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.end <= self.start:
            raise ValueError("span end must follow span start")
        if (self.kind == "page_character") != (self.page is not None):
            raise ValueError("page_character locators require a page")
        return self


class ProjectionState(CorpusValue):
    release_id: OpaqueName
    projection_generation: int = Field(ge=1)
    backend_watermark: int = Field(ge=0)
    core_watermark: int = Field(ge=0)
    lag_events: int = Field(ge=0)
    lag_seconds: float = Field(ge=0)
    max_lag_events: int = Field(ge=0)
    max_lag_seconds: float = Field(ge=0)
    full_text_backend: Literal["postgresql_full_text"] = "postgresql_full_text"
    vector_backend: Literal["postgresql_pgvector_exact"] = "postgresql_pgvector_exact"
    qdrant_compatibility: Literal["metadata_only"] = "metadata_only"
    falkordb_compatibility: Literal["metadata_only"] = "metadata_only"

    @model_validator(mode="after")
    def validate_watermark(self) -> Self:
        if self.backend_watermark > self.core_watermark:
            raise ValueError("backend watermark cannot exceed the core watermark")
        return self

    @property
    def current(self) -> bool:
        return (
            self.backend_watermark >= self.core_watermark
            and self.lag_events <= self.max_lag_events
            and self.lag_seconds <= self.max_lag_seconds
        )


class ProjectionRegistryState(CorpusValue):
    """Canonical core guard for one retrieval projection generation."""

    tenant_id: UUID
    matter_id: UUID
    release_id: OpaqueName
    projection_generation: int = Field(ge=1)
    core_watermark: int = Field(ge=0)
    policy_revision: Sha256
    rights_revision: Sha256
    recorded_at: datetime


class CorpusSourceVersion(CorpusValue):
    schema_version: Literal["sklegal.governed-corpus-source/v1"] = (
        "sklegal.governed-corpus-source/v1"
    )
    tenant_id: UUID
    matter_id: UUID
    source_id: OpaqueName
    source_version_id: UUID
    source_version: OpaqueName
    release_id: OpaqueName
    projection_generation: int = Field(ge=1)
    title: BoundedText
    citation: BoundedText
    source_role: Literal["matter_evidence", "course_instruction", "official_authority"]
    classification: Classification
    rights_revision: Sha256
    permitted_principal_ids: frozenset[UUID]
    source_sha256: Sha256
    document_id: OpaqueName
    chunk_id: OpaqueName
    chunk_sha256: Sha256
    locator: SpanLocator
    exact_span: BoundedText
    embedding: tuple[float, ...] = Field(min_length=1, max_length=4096)
    verification_state: VerificationState
    jurisdiction: str | None = Field(default=None, max_length=200)
    supersedes_source_version_id: UUID | None = None
    recorded_at: datetime
    recorded_by_principal_id: UUID
    authorization_decision_id: UUID
    policy_decision_id: UUID
    policy_revision: Sha256

    @field_validator("embedding")
    @classmethod
    def validate_embedding(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if not all(math.isfinite(item) for item in value):
            raise ValueError("embedding must contain only finite values")
        if math.sqrt(sum(item * item for item in value)) == 0:
            raise ValueError("embedding must have a nonzero norm")
        return value

    @model_validator(mode="after")
    def validate_exact_span(self) -> Self:
        if self.locator.end - self.locator.start != len(self.exact_span):
            raise ValueError("exact span length must match its locator")
        if hashlib.sha256(self.exact_span.encode("utf-8")).hexdigest() != (
            self.chunk_sha256
        ):
            raise ValueError("exact span bytes must match the chunk hash")
        if self.supersedes_source_version_id == self.source_version_id:
            raise ValueError("a source version cannot supersede itself")
        if (
            self.source_role == "official_authority"
            and self.verification_state
            is not VerificationState.OFFICIAL_AUTHORITY_VERIFIED
        ):
            raise ValueError("official Authority requires official verification")
        return self


class RetrievalQuery(CorpusValue):
    query: BoundedText
    query_embedding: tuple[float, ...] | None = Field(default=None, max_length=4096)
    mode: RetrievalMode
    max_results: int = Field(default=10, ge=1, le=50)
    expected_release_id: OpaqueName
    expected_projection_generation: int = Field(ge=1)
    required_core_watermark: int = Field(ge=0)
    classification_ceiling: Classification
    rights_revision: Sha256
    snapshot_at: datetime
    after_score: float | None = None
    after_source_version_id: UUID | None = None

    @model_validator(mode="after")
    def validate_mode(self) -> Self:
        needs_vector = self.mode in {
            RetrievalMode.VECTOR_EXACT,
            RetrievalMode.HYBRID_RRF,
        }
        if needs_vector != (self.query_embedding is not None):
            raise ValueError("vector and hybrid modes require one query embedding")
        if self.query_embedding is not None:
            if not all(math.isfinite(item) for item in self.query_embedding):
                raise ValueError("query embedding must contain only finite values")
            if math.sqrt(sum(item * item for item in self.query_embedding)) == 0:
                raise ValueError("query embedding must have a nonzero norm")
        if self.snapshot_at.tzinfo is None or self.snapshot_at.utcoffset() is None:
            raise ValueError("snapshot time must be timezone aware")
        if (self.after_score is None) != (self.after_source_version_id is None):
            raise ValueError("cursor sort key must be complete")
        if self.after_score is not None and not math.isfinite(self.after_score):
            raise ValueError("cursor score must be finite")
        return self


class RankEvidence(CorpusValue):
    rank: int = Field(ge=1)
    score: float
    full_text_rank: float | None = None
    vector_distance: float | None = None
    rank_path: tuple[Literal["full_text", "pgvector_exact", "hybrid_rrf"], ...]

    @field_validator("score", "full_text_rank", "vector_distance")
    @classmethod
    def finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("rank evidence must be finite")
        return value


class GovernedCorpusHit(CorpusValue):
    source: CorpusSourceVersion
    rank: RankEvidence
    supersession_status: Literal["current"] = "current"


class GovernedCorpusResult(CorpusValue):
    schema_version: Literal["sklegal.governed-corpus-result/v1"] = (
        "sklegal.governed-corpus-result/v1"
    )
    tenant_id: UUID
    matter_id: UUID
    query_sha256: Sha256
    authorization_decision_id: UUID
    policy_decision_id: UUID
    policy_revision: Sha256
    rights_revision: Sha256
    classification_ceiling: Classification
    projection: ProjectionState
    mode: RetrievalMode
    hits: tuple[GovernedCorpusHit, ...]
    no_answer: bool
    continuation_cursor: ContinuationCursor | None = None

    @model_validator(mode="after")
    def validate_answer_state(self) -> Self:
        if self.no_answer == bool(self.hits):
            raise ValueError("no-answer state must match empty result state")
        if self.no_answer and self.continuation_cursor is not None:
            raise ValueError("an empty result cannot continue")
        return self


class CorpusAuditEvent(CorpusValue):
    audit_id: UUID
    tenant_id: UUID
    matter_id: UUID
    source_version_id: UUID
    action: Literal["corpus.source.recorded", "corpus.source.superseded"]
    actor_principal_id: UUID
    authorization_decision_id: UUID
    policy_decision_id: UUID
    policy_revision: Sha256
    request_sha256: Sha256
    source_sha256: Sha256
    correlation_id: UUID
    occurred_at: datetime


class CorpusOutboxRecord(CorpusValue):
    outbox_id: UUID
    audit_id: UUID
    tenant_id: UUID
    matter_id: UUID
    source_version_id: UUID
    topic: Literal["corpus.projection.requested"] = "corpus.projection.requested"
    payload_sha256: Sha256
    qdrant_dispatch_allowed: Literal[False] = False
    falkordb_dispatch_allowed: Literal[False] = False
    created_at: datetime


class CorpusProjectionCommand(CorpusValue):
    command_id: UUID
    operation: Literal["create", "correct", "supersede", "revoke"]
    source: CorpusSourceVersion
    payload_sha256: Sha256
    core_watermark: int = Field(ge=0)
    projected_at: datetime

    @model_validator(mode="after")
    def validate_command(self) -> Self:
        if self.payload_sha256 != canonical_sha256(self.source):
            raise ValueError("projection command payload hash is invalid")
        if (self.operation == "supersede") != (
            self.source.supersedes_source_version_id is not None
        ):
            raise ValueError("projection supersession lineage is invalid")
        return self


def canonical_sha256(value: BaseModel | dict[str, Any]) -> str:
    payload = (
        value.model_dump(mode="json", by_alias=True)
        if isinstance(value, BaseModel)
        else value
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "BoundedText",
    "Classification",
    "ContinuationCursor",
    "CorpusAuditEvent",
    "CorpusOutboxRecord",
    "CorpusProjectionCommand",
    "CorpusSourceVersion",
    "GovernedCorpusHit",
    "GovernedCorpusResult",
    "OpaqueName",
    "ProjectionRegistryState",
    "ProjectionGuardStatus",
    "ProjectionState",
    "RankEvidence",
    "RetrievalMode",
    "RetrievalQuery",
    "Sha256",
    "SpanLocator",
    "VerificationState",
    "canonical_sha256",
]
