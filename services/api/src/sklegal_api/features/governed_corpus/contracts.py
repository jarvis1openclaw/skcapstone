"""Frozen wire contracts for governed corpus retrieval and source control."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel
from sklegal_persistence.features.governed_corpus.models import (
    BoundedText,
    Classification,
    ContinuationCursor,
    CorpusSourceVersion,
    GovernedCorpusResult,
    OpaqueName,
    ProjectionState,
    RetrievalMode,
    Sha256,
    SpanLocator,
    VerificationState,
)


class CorpusContract(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class CorpusSearchCommand(CorpusContract):
    schema_version: Literal["sklegal.governed-corpus-query/v1"] = (
        "sklegal.governed-corpus-query/v1"
    )
    query: BoundedText
    query_embedding: tuple[float, ...] | None = Field(default=None, max_length=4096)
    mode: RetrievalMode = RetrievalMode.FULL_TEXT
    max_results: int = Field(default=10, ge=1, le=50)
    expected_release_id: OpaqueName
    expected_projection_generation: int = Field(ge=1)
    required_core_watermark: int = Field(ge=0)
    continuation_cursor: ContinuationCursor | None = None

    @field_validator("query_embedding")
    @classmethod
    def validate_embedding(
        cls, value: tuple[float, ...] | None
    ) -> tuple[float, ...] | None:
        if value is not None and (
            not all(math.isfinite(item) for item in value)
            or math.sqrt(sum(item * item for item in value)) == 0
        ):
            raise ValueError("query embedding must be finite and nonzero")
        return value

    @model_validator(mode="after")
    def validate_mode(self) -> Self:
        needs_vector = self.mode in {
            RetrievalMode.VECTOR_EXACT,
            RetrievalMode.HYBRID_RRF,
        }
        if needs_vector != (self.query_embedding is not None):
            raise ValueError("vector and hybrid modes require one query embedding")
        return self


class CorpusSpanCommand(CorpusContract):
    source_id: OpaqueName
    expected_release_id: OpaqueName
    expected_projection_generation: int = Field(ge=1)
    required_core_watermark: int = Field(ge=0)


class RecordCorpusSourceCommand(CorpusContract):
    schema_version: Literal["sklegal.governed-corpus-record-command/v1"] = (
        "sklegal.governed-corpus-record-command/v1"
    )
    source_id: OpaqueName
    source_version_id: UUID
    source_version: OpaqueName
    release_id: OpaqueName
    projection_generation: int = Field(ge=1)
    title: BoundedText
    citation: BoundedText
    source_role: Literal["matter_evidence", "course_instruction", "official_authority"]
    classification: Classification
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

    @model_validator(mode="after")
    def validate_rights(self) -> Self:
        if not self.permitted_principal_ids:
            raise ValueError("a controlled source requires at least one rights grant")
        return self


class CorpusSourceMutationReceipt(CorpusContract):
    source: CorpusSourceVersion
    idempotency_key_sha256: Sha256
    request_sha256: Sha256
    audit_id: UUID
    outbox_id: UUID
    correlation_id: UUID


class CorpusSourceRead(CorpusContract):
    """Authorized source projection without rights rosters or embeddings."""

    source_id: OpaqueName
    source_version_id: UUID
    source_version: OpaqueName
    release_id: OpaqueName
    projection_generation: int
    title: BoundedText
    citation: BoundedText
    source_role: Literal["matter_evidence", "course_instruction", "official_authority"]
    classification: Classification
    rights_revision: Sha256
    source_sha256: Sha256
    document_id: OpaqueName
    chunk_id: OpaqueName
    chunk_sha256: Sha256
    locator: SpanLocator
    exact_span: BoundedText
    verification_state: VerificationState
    jurisdiction: str | None
    recorded_at: datetime
    recorded_by_principal_id: UUID

    @classmethod
    def from_source(cls, source: CorpusSourceVersion) -> CorpusSourceRead:
        values = source.model_dump(
            mode="python",
            include=set(cls.model_fields),
        )
        return cls.model_validate(values)


class CorpusRankRead(CorpusContract):
    rank: int
    score: float
    full_text_rank: float | None
    vector_distance: float | None
    rank_path: tuple[Literal["full_text", "pgvector_exact", "hybrid_rrf"], ...]


class CorpusHitRead(CorpusContract):
    source: CorpusSourceRead
    rank: CorpusRankRead
    supersession_status: Literal["current"]


class GovernedCorpusResultRead(CorpusContract):
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
    hits: tuple[CorpusHitRead, ...]
    no_answer: bool
    continuation_cursor: ContinuationCursor | None

    @classmethod
    def from_result(cls, result: GovernedCorpusResult) -> GovernedCorpusResultRead:
        return cls(
            tenant_id=result.tenant_id,
            matter_id=result.matter_id,
            query_sha256=result.query_sha256,
            authorization_decision_id=result.authorization_decision_id,
            policy_decision_id=result.policy_decision_id,
            policy_revision=result.policy_revision,
            rights_revision=result.rights_revision,
            classification_ceiling=result.classification_ceiling,
            projection=result.projection,
            mode=result.mode,
            hits=tuple(
                CorpusHitRead(
                    source=CorpusSourceRead.from_source(hit.source),
                    rank=CorpusRankRead.model_validate(hit.rank.model_dump()),
                    supersession_status=hit.supersession_status,
                )
                for hit in result.hits
            ),
            no_answer=result.no_answer,
            continuation_cursor=result.continuation_cursor,
        )


class CorpusSpanRead(CorpusContract):
    schema_version: Literal["sklegal.governed-corpus-span/v1"] = (
        "sklegal.governed-corpus-span/v1"
    )
    source: CorpusSourceRead
    projection: ProjectionState


class CorpusSearchRead(CorpusContract):
    result: GovernedCorpusResultRead


__all__ = [
    "CorpusSearchCommand",
    "CorpusSearchRead",
    "CorpusSourceRead",
    "CorpusSourceMutationReceipt",
    "CorpusSpanCommand",
    "CorpusSpanRead",
    "RecordCorpusSourceCommand",
]
