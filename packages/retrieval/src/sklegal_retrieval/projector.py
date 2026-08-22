"""Idempotent outbox projector, graph manifest validation, shadow parity.

The core transactional outbox drives projection builds. Replays and rebuilds
are idempotent: event idempotency keys make duplicate delivery a no-op and a
from-scratch rebuild over the same pinned log yields the identical state.
Graph relationship manifests are validated against entity scope digests at
build time; one mismatch rejects the whole build and nothing becomes visible.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping
from typing import Protocol, Self

from pydantic import Field, field_validator, model_validator

from .errors import RetrievalIntegrityError, RetrievalUnavailableError
from .models import (
    OpaqueId,
    ProjectionPins,
    RetrievalComponent,
    RetrievalScope,
    RetrievalValue,
    Sha256,
    SourceProvenance,
)


def scope_sha256(scope: RetrievalScope) -> str:
    """Return the canonical scope digest used by graph endpoint manifests."""

    return hashlib.sha256(scope.canonical_json().encode("utf-8")).hexdigest()


class ProjectedRow(RetrievalValue):
    """One derived row staged by the projector for one partition."""

    projection: ProjectionPins
    source: SourceProvenance
    content: str = Field(min_length=1, max_length=1048576)
    searchable_text: str | None = Field(default=None, max_length=1048576)
    embedding: tuple[float, ...] | None = None

    @field_validator("embedding")
    @classmethod
    def validate_finite_embedding(
        cls, value: tuple[float, ...] | None
    ) -> tuple[float, ...] | None:
        if value is not None and not all(math.isfinite(item) for item in value):
            raise ValueError("projected embeddings must contain only finite values")
        return value

    @model_validator(mode="after")
    def validate_component_shape(self) -> Self:
        vector = self.projection.component is RetrievalComponent.VECTOR
        if vector != (self.embedding is not None):
            raise ValueError("vector rows require an exact-dimension embedding")
        if vector and self.embedding is not None:
            pins = self.projection.vector
            if pins is None or len(self.embedding) != pins.embedding_dimension:
                raise ValueError("projected embedding does not match the pinned shape")
        return self


class OutboxEvent(RetrievalValue):
    """One ordered, idempotent projection event from the core outbox."""

    event_sequence: int = Field(ge=1)
    idempotency_key: OpaqueId
    row: ProjectedRow


class ProjectionSink(Protocol):
    """Staged projection storage for one rebuildable partition."""

    def apply(self, event: OutboxEvent) -> bool:
        """Apply one event; return False when its key was already applied."""

    def rows(self) -> tuple[ProjectedRow, ...]:
        """Return the staged rows in deterministic order."""

    def watermark(self) -> int:
        """Return the highest applied outbox sequence."""


class OutboxProjector:
    """Replay pinned outbox events into a sink with idempotent delivery."""

    def replay(self, events: tuple[OutboxEvent, ...], sink: ProjectionSink) -> int:
        """Apply ordered events exactly once; return the visible watermark."""

        previous = 0
        for event in events:
            if not isinstance(event, OutboxEvent):
                raise RetrievalIntegrityError("outbox event failed validation")
            if event.event_sequence <= previous:
                raise RetrievalIntegrityError(
                    "outbox events must arrive in strict sequence"
                )
            previous = event.event_sequence
            try:
                sink.apply(event)
            except RetrievalUnavailableError:
                raise
            except Exception:
                raise RetrievalUnavailableError(
                    "projection sink is unavailable"
                ) from None
        return sink.watermark()

    def rebuild(
        self,
        events: tuple[OutboxEvent, ...],
        sink_factory: Callable[[], ProjectionSink],
    ) -> ProjectionSink:
        """Rebuild one partition from scratch over the same pinned log."""

        sink = sink_factory()
        self.replay(events, sink)
        return sink


class GraphEntityManifestEntry(RetrievalValue):
    """One graph entity with its immutable scope digest."""

    graph_entity_id: OpaqueId
    scope: RetrievalScope
    scope_digest: Sha256
    content_sha256: Sha256

    @model_validator(mode="after")
    def validate_scope_digest(self) -> Self:
        if self.scope_digest != scope_sha256(self.scope):
            raise ValueError("entity scope digest does not match its scope")
        return self


class GraphRelationshipManifestEntry(RetrievalValue):
    """One graph relationship with both endpoint scope digests."""

    relationship_id: OpaqueId
    relationship_type: OpaqueId
    scope: RetrievalScope
    start_entity_id: OpaqueId
    end_entity_id: OpaqueId
    start_endpoint_scope_sha256: Sha256
    end_endpoint_scope_sha256: Sha256
    content_sha256: Sha256


def validate_graph_relationship(
    relationship: GraphRelationshipManifestEntry,
    entities: Mapping[str, GraphEntityManifestEntry],
) -> None:
    """Validate endpoint scope digests and prohibit cross-graph edges.

    One endpoint mismatch or cross-scope edge rejects the entire build; the
    relationship is never silently dropped while the rest proceeds.
    """

    if not isinstance(relationship, GraphRelationshipManifestEntry):
        raise RetrievalIntegrityError("graph relationship failed validation")
    relationship_digest = scope_sha256(relationship.scope)
    for entity_id, endpoint_digest in (
        (relationship.start_entity_id, relationship.start_endpoint_scope_sha256),
        (relationship.end_entity_id, relationship.end_endpoint_scope_sha256),
    ):
        entity = entities.get(entity_id)
        if not isinstance(entity, GraphEntityManifestEntry):
            raise RetrievalIntegrityError(
                "graph relationship endpoint is absent from the manifest"
            )
        if entity.scope_digest != endpoint_digest:
            raise RetrievalIntegrityError(
                "graph relationship endpoint scope digest mismatch"
            )
        if entity.scope_digest != relationship_digest:
            raise RetrievalIntegrityError("cross-graph edges are prohibited")


class LegacyShadowSnapshot(RetrievalValue):
    """HammerTime-owned legacy projection summary used only for parity."""

    alias: OpaqueId
    record_count: int = Field(ge=0)
    content_digest: Sha256
    watermark: int = Field(ge=0)
    scope_digest: Sha256


def project_rows_digest(rows: tuple[ProjectedRow, ...]) -> str:
    """Return one deterministic digest over row source and content pins."""

    digests = sorted(
        hashlib.sha256(
            f"{row.source.canonical_sha256()}:{row.content}".encode()
        ).hexdigest()
        for row in rows
    )
    return hashlib.sha256(":".join(digests).encode("utf-8")).hexdigest()


def shadow_parity(
    snapshot: LegacyShadowSnapshot, rows: tuple[ProjectedRow, ...]
) -> tuple[str, ...]:
    """Compare one rebuilt projection with its legacy shadow snapshot.

    Returns one entry per mismatch; an empty tuple is parity. The legacy
    alias is never routing eligible regardless of the outcome.
    """

    mismatches: list[str] = []
    if len(rows) != snapshot.record_count:
        mismatches.append("record_count")
    if rows and project_rows_digest(rows) != snapshot.content_digest:
        mismatches.append("content_digest")
    watermark = max((row.projection.backend_watermark for row in rows), default=0)
    if watermark != snapshot.watermark:
        mismatches.append("watermark")
    if rows:
        scopes = {scope_sha256(row.projection.scope) for row in rows}
        if scopes != {snapshot.scope_digest}:
            mismatches.append("scope_digest")
    return tuple(mismatches)


__all__ = [
    "GraphEntityManifestEntry",
    "GraphRelationshipManifestEntry",
    "LegacyShadowSnapshot",
    "OutboxEvent",
    "OutboxProjector",
    "ProjectedRow",
    "ProjectionSink",
    "project_rows_digest",
    "scope_sha256",
    "shadow_parity",
    "validate_graph_relationship",
]
