"""Atomic repository contracts for governed corpus records."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from .models import (
    Classification,
    CorpusAuditEvent,
    CorpusOutboxRecord,
    CorpusProjectionCommand,
    CorpusSourceVersion,
    ProjectionRegistryState,
    ProjectionState,
    RetrievalMode,
    RetrievalQuery,
    canonical_sha256,
)


class GovernedCorpusRepositoryError(RuntimeError):
    """Base sanitized repository error."""


class GovernedCorpusRepositoryUnavailable(GovernedCorpusRepositoryError):
    """The durable corpus, audit, or outbox has no current answer."""


class GovernedCorpusIdempotencyConflict(GovernedCorpusRepositoryError):
    """An idempotency key is already bound to other request bytes."""


class GovernedCorpusVersionConflict(GovernedCorpusRepositoryError):
    """A controlled source write conflicts with durable lineage."""


@dataclass(frozen=True, slots=True)
class RankedSource:
    source: CorpusSourceVersion
    score: float
    full_text_rank: float | None
    vector_distance: float | None


@dataclass(frozen=True, slots=True)
class RankedPage:
    rows: tuple[RankedSource, ...]
    has_more: bool


class GovernedCorpusCoreRepository(Protocol):
    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool: ...

    def projection_registry(
        self, tenant_id: UUID, matter_id: UUID
    ) -> ProjectionRegistryState | None: ...

    def get_current_source(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        principal_id: UUID,
        source_id: str,
        classification_ceiling: Classification,
        rights_revision: str,
        release_id: str,
        projection_generation: int,
    ) -> CorpusSourceVersion | None: ...

    def commit_source(
        self,
        *,
        idempotency_key_sha256: str,
        request_sha256: str,
        source: CorpusSourceVersion,
        audit: CorpusAuditEvent,
        outbox: CorpusOutboxRecord,
    ) -> CorpusSourceVersion: ...

    def projection_commands(
        self, tenant_id: UUID, matter_id: UUID
    ) -> tuple[CorpusProjectionCommand, ...]: ...


class GovernedCorpusRetrievalRepository(Protocol):
    def projection_state(
        self, tenant_id: UUID, matter_id: UUID
    ) -> ProjectionState | None: ...

    def retrieve(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        principal_id: UUID,
        query: RetrievalQuery,
    ) -> RankedPage: ...

    def apply_projection(self, command: CorpusProjectionCommand) -> None: ...

    def rebuild_projection(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        commands: Iterable[CorpusProjectionCommand],
    ) -> None: ...


def validate_write_evidence(
    source: CorpusSourceVersion,
    audit: CorpusAuditEvent,
    outbox: CorpusOutboxRecord,
) -> None:
    payload_sha256 = canonical_sha256(source)
    action = (
        "corpus.source.superseded"
        if source.supersedes_source_version_id is not None
        else "corpus.source.recorded"
    )
    if (
        audit.audit_id != outbox.audit_id
        or audit.tenant_id != source.tenant_id
        or audit.matter_id != source.matter_id
        or audit.source_version_id != source.source_version_id
        or audit.source_sha256 != payload_sha256
        or audit.actor_principal_id != source.recorded_by_principal_id
        or audit.authorization_decision_id != source.authorization_decision_id
        or audit.policy_decision_id != source.policy_decision_id
        or audit.policy_revision != source.policy_revision
        or audit.action != action
        or outbox.tenant_id != source.tenant_id
        or outbox.matter_id != source.matter_id
        or outbox.source_version_id != source.source_version_id
        or outbox.payload_sha256 != payload_sha256
        or outbox.qdrant_dispatch_allowed
        or outbox.falkordb_dispatch_allowed
    ):
        raise GovernedCorpusVersionConflict("audit or outbox does not bind source")


def _terms(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", text.casefold()))


def _cosine_distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise GovernedCorpusRepositoryUnavailable("vector shape is unavailable")
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if left_norm == 0 or right_norm == 0:
        raise GovernedCorpusRepositoryUnavailable("vector shape is unavailable")
    similarity = sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )
    return 1.0 - similarity


def _after_cursor(item: RankedSource, query: RetrievalQuery) -> bool:
    if query.after_score is None or query.after_source_version_id is None:
        return True
    return item.score < query.after_score or (
        item.score == query.after_score
        and item.source.source_version_id > query.after_source_version_id
    )


class InMemoryGovernedCorpusRepository:
    """Deterministic public-synthetic repository with atomic failure probes."""

    def __init__(
        self,
        *,
        available: bool = True,
        core_available: bool | None = None,
        retrieval_available: bool | None = None,
        audit_available: bool = True,
        outbox_available: bool = True,
        policy_revision: str = "0" * 64,
        rights_revision: str = "0" * 64,
    ) -> None:
        self.core_available = available if core_available is None else core_available
        self.retrieval_available = (
            available if retrieval_available is None else retrieval_available
        )
        self.audit_available = audit_available
        self.outbox_available = outbox_available
        self.policy_revision = policy_revision
        self.rights_revision = rights_revision
        self._members: dict[tuple[UUID, UUID], frozenset[UUID]] = {}
        self._projections: dict[tuple[UUID, UUID], ProjectionState] = {}
        self._registries: dict[tuple[UUID, UUID], ProjectionRegistryState] = {}
        self._versions: dict[tuple[UUID, UUID, UUID], CorpusSourceVersion] = {}
        self._successors: dict[tuple[UUID, UUID, UUID], UUID] = {}
        self._projection_versions: dict[
            tuple[UUID, UUID, UUID], CorpusSourceVersion
        ] = {}
        self._projection_successors: dict[tuple[UUID, UUID, UUID], UUID] = {}
        self._projection_commands: dict[
            tuple[UUID, UUID, UUID], CorpusProjectionCommand
        ] = {}
        self._idempotency: dict[
            tuple[UUID, UUID, str], tuple[str, CorpusSourceVersion]
        ] = {}
        self._audit: list[CorpusAuditEvent] = []
        self._outbox: list[CorpusOutboxRecord] = []

    @property
    def available(self) -> bool:
        return self.core_available and self.retrieval_available

    @available.setter
    def available(self, value: bool) -> None:
        self.core_available = value
        self.retrieval_available = value

    @property
    def audit_events(self) -> tuple[CorpusAuditEvent, ...]:
        return tuple(self._audit)

    @property
    def outbox_records(self) -> tuple[CorpusOutboxRecord, ...]:
        return tuple(self._outbox)

    def set_matter_members(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        principal_ids: Iterable[UUID],
    ) -> None:
        self._members[(tenant_id, matter_id)] = frozenset(principal_ids)

    def set_projection(
        self, tenant_id: UUID, matter_id: UUID, projection: ProjectionState
    ) -> None:
        self._projections[(tenant_id, matter_id)] = projection
        self._registries[(tenant_id, matter_id)] = ProjectionRegistryState(
            tenant_id=tenant_id,
            matter_id=matter_id,
            release_id=projection.release_id,
            projection_generation=projection.projection_generation,
            core_watermark=projection.core_watermark,
            policy_revision=self.policy_revision,
            rights_revision=self.rights_revision,
            recorded_at=datetime.fromtimestamp(0, tz=UTC),
        )

    def _require_core_available(self) -> None:
        if not self.core_available:
            raise GovernedCorpusRepositoryUnavailable("core corpus store unavailable")

    def _require_retrieval_available(self) -> None:
        if not self.retrieval_available:
            raise GovernedCorpusRepositoryUnavailable("retrieval store unavailable")

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        self._require_core_available()
        return principal_id in self._members.get((tenant_id, matter_id), frozenset())

    def projection_registry(
        self, tenant_id: UUID, matter_id: UUID
    ) -> ProjectionRegistryState | None:
        self._require_core_available()
        return self._registries.get((tenant_id, matter_id))

    def projection_state(
        self, tenant_id: UUID, matter_id: UUID
    ) -> ProjectionState | None:
        self._require_retrieval_available()
        return self._projections.get((tenant_id, matter_id))

    def _current_sources(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        snapshot_at: datetime | None = None,
        *,
        projected: bool = False,
    ) -> tuple[CorpusSourceVersion, ...]:
        source_store = self._projection_versions if projected else self._versions
        successor_store = self._projection_successors if projected else self._successors
        versions = {
            key: source
            for key, source in source_store.items()
            if key[:2] == (tenant_id, matter_id)
            and (snapshot_at is None or source.recorded_at <= snapshot_at)
        }
        superseded = {
            key
            for key, successor_id in successor_store.items()
            if (tenant_id, matter_id, successor_id) in versions
        }
        return tuple(
            source for key, source in versions.items() if key not in superseded
        )

    @staticmethod
    def _authorized(
        source: CorpusSourceVersion,
        *,
        principal_id: UUID,
        query: RetrievalQuery,
    ) -> bool:
        return (
            principal_id in source.permitted_principal_ids
            and source.classification <= query.classification_ceiling
            and source.rights_revision == query.rights_revision
            and source.release_id == query.expected_release_id
            and source.projection_generation == query.expected_projection_generation
        )

    def retrieve(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        principal_id: UUID,
        query: RetrievalQuery,
    ) -> RankedPage:
        self._require_retrieval_available()
        sources = tuple(
            source
            for source in self._current_sources(
                tenant_id, matter_id, query.snapshot_at, projected=True
            )
            if self._authorized(source, principal_id=principal_id, query=query)
        )
        query_terms = _terms(query.query)
        lexical = {
            source.source_version_id: float(
                len(query_terms & _terms(f"{source.title} {source.exact_span}"))
            )
            for source in sources
        }
        lexical_order = sorted(
            sources,
            key=lambda source: (
                -lexical[source.source_version_id],
                source.source_version_id,
            ),
        )
        if query.mode is RetrievalMode.FULL_TEXT:
            selected = [
                source
                for source in lexical_order
                if lexical[source.source_version_id] > 0
            ]
            ranked = [
                RankedSource(
                    source,
                    lexical[source.source_version_id],
                    lexical[source.source_version_id],
                    None,
                )
                for source in selected
            ]
        else:
            embedding = query.query_embedding
            if embedding is None:
                raise GovernedCorpusRepositoryUnavailable("vector query is unavailable")
            distances = {
                source.source_version_id: _cosine_distance(embedding, source.embedding)
                for source in sources
            }
            vector_order = sorted(
                sources,
                key=lambda source: (
                    distances[source.source_version_id],
                    source.source_version_id,
                ),
            )
            if query.mode is RetrievalMode.VECTOR_EXACT:
                ranked = [
                    RankedSource(
                        source,
                        1.0 / (1.0 + distances[source.source_version_id]),
                        None,
                        distances[source.source_version_id],
                    )
                    for source in vector_order
                ]
            else:
                lexical_positions = {
                    source.source_version_id: rank
                    for rank, source in enumerate(lexical_order, start=1)
                }
                vector_positions = {
                    source.source_version_id: rank
                    for rank, source in enumerate(vector_order, start=1)
                }
                ranked = [
                    RankedSource(
                        source,
                        1.0 / (60 + lexical_positions[source.source_version_id])
                        + 1.0 / (60 + vector_positions[source.source_version_id]),
                        lexical[source.source_version_id],
                        distances[source.source_version_id],
                    )
                    for source in sources
                ]
                ranked.sort(
                    key=lambda item: (-item.score, item.source.source_version_id)
                )
        remaining = tuple(item for item in ranked if _after_cursor(item, query))
        return RankedPage(
            rows=remaining[: query.max_results],
            has_more=len(remaining) > query.max_results,
        )

    def apply_projection(self, command: CorpusProjectionCommand) -> None:
        """Apply one idempotent outbox command to rebuildable state."""

        self._require_retrieval_available()
        source = command.source
        command_key = (source.tenant_id, source.matter_id, command.command_id)
        prior = self._projection_commands.get(command_key)
        if prior is not None:
            if prior != command:
                raise GovernedCorpusVersionConflict(
                    "projection command is bound to other bytes"
                )
            return
        source_key = (source.tenant_id, source.matter_id, source.source_version_id)
        predecessor = source.supersedes_source_version_id
        if command.operation == "revoke":
            self._projection_versions.pop(source_key, None)
            self._projection_successors.pop(source_key, None)
        else:
            if (
                command.operation == "create"
                and source_key in self._projection_versions
            ):
                raise GovernedCorpusVersionConflict("projection source already exists")
            self._projection_versions[source_key] = source
            if predecessor is not None:
                predecessor_key = (source.tenant_id, source.matter_id, predecessor)
                if predecessor_key not in self._projection_versions:
                    raise GovernedCorpusVersionConflict(
                        "projection supersession lineage is stale"
                    )
                self._projection_successors[predecessor_key] = source.source_version_id
        self._projection_commands[command_key] = command
        prior_state = self._projections.get((source.tenant_id, source.matter_id))
        if prior_state is None:
            raise GovernedCorpusRepositoryUnavailable("projection state is unknown")
        self._projections[(source.tenant_id, source.matter_id)] = (
            prior_state.model_copy(
                update={
                    "release_id": source.release_id,
                    "projection_generation": source.projection_generation,
                    "backend_watermark": command.core_watermark,
                    "core_watermark": command.core_watermark,
                    "lag_events": 0,
                    "lag_seconds": 0.0,
                }
            )
        )

    def rebuild_projection(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        commands: Iterable[CorpusProjectionCommand],
    ) -> None:
        """Replace one Matter projection from a deterministic command sequence."""

        self._require_retrieval_available()
        for store in (
            self._projection_versions,
            self._projection_successors,
            self._projection_commands,
        ):
            for key in tuple(store):
                if key[:2] == (tenant_id, matter_id):
                    del store[key]
        ordered = sorted(
            commands,
            key=lambda item: (item.projected_at, item.command_id),
        )
        for command in ordered:
            if (command.source.tenant_id, command.source.matter_id) != (
                tenant_id,
                matter_id,
            ):
                raise GovernedCorpusVersionConflict("projection rebuild scope drift")
            self.apply_projection(command)

    def projection_commands(
        self, tenant_id: UUID, matter_id: UUID
    ) -> tuple[CorpusProjectionCommand, ...]:
        self._require_core_available()
        registry = self._registries.get((tenant_id, matter_id))
        if registry is None:
            raise GovernedCorpusRepositoryUnavailable("projection registry is unknown")
        commands: list[CorpusProjectionCommand] = []
        for outbox in sorted(
            self._outbox, key=lambda item: (item.created_at, item.outbox_id)
        ):
            if (outbox.tenant_id, outbox.matter_id) != (tenant_id, matter_id):
                continue
            source = self._versions.get(
                (tenant_id, matter_id, outbox.source_version_id)
            )
            if source is None:
                raise GovernedCorpusRepositoryUnavailable(
                    "outbox projection source is unavailable"
                )
            commands.append(
                CorpusProjectionCommand(
                    command_id=outbox.outbox_id,
                    operation=(
                        "supersede"
                        if source.supersedes_source_version_id is not None
                        else "create"
                    ),
                    source=source,
                    payload_sha256=outbox.payload_sha256,
                    core_watermark=registry.core_watermark,
                    projected_at=outbox.created_at,
                )
            )
        return tuple(commands)

    def get_current_source(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        principal_id: UUID,
        source_id: str,
        classification_ceiling: Classification,
        rights_revision: str,
        release_id: str,
        projection_generation: int,
    ) -> CorpusSourceVersion | None:
        self._require_core_available()
        for source in self._current_sources(tenant_id, matter_id):
            if (
                source.source_id == source_id
                and principal_id in source.permitted_principal_ids
                and source.classification <= classification_ceiling
                and source.rights_revision == rights_revision
                and source.release_id == release_id
                and source.projection_generation == projection_generation
            ):
                return source
        return None

    def commit_source(
        self,
        *,
        idempotency_key_sha256: str,
        request_sha256: str,
        source: CorpusSourceVersion,
        audit: CorpusAuditEvent,
        outbox: CorpusOutboxRecord,
    ) -> CorpusSourceVersion:
        self._require_core_available()
        if not self.audit_available or not self.outbox_available:
            raise GovernedCorpusRepositoryUnavailable("atomic evidence unavailable")
        validate_write_evidence(source, audit, outbox)
        idempotency_key = (source.tenant_id, source.matter_id, idempotency_key_sha256)
        existing = self._idempotency.get(idempotency_key)
        if existing is not None:
            if existing[0] != request_sha256:
                raise GovernedCorpusIdempotencyConflict(
                    "idempotency key is bound to other bytes"
                )
            return existing[1]
        source_key = (source.tenant_id, source.matter_id, source.source_version_id)
        if source_key in self._versions:
            raise GovernedCorpusVersionConflict("source version already exists")
        predecessor_id = source.supersedes_source_version_id
        if predecessor_id is not None:
            predecessor_key = (source.tenant_id, source.matter_id, predecessor_id)
            predecessor = self._versions.get(predecessor_key)
            if (
                predecessor is None
                or predecessor.source_id != source.source_id
                or predecessor_key in self._successors
            ):
                raise GovernedCorpusVersionConflict("supersession lineage is stale")
            self._successors[predecessor_key] = source.source_version_id
        elif any(
            item.source_id == source.source_id
            for item in self._current_sources(source.tenant_id, source.matter_id)
        ):
            raise GovernedCorpusVersionConflict("current source already exists")
        self._versions[source_key] = source
        self._idempotency[idempotency_key] = (request_sha256, source)
        self._audit.append(audit)
        self._outbox.append(outbox)
        return source


__all__ = [
    "GovernedCorpusIdempotencyConflict",
    "GovernedCorpusCoreRepository",
    "GovernedCorpusRepositoryError",
    "GovernedCorpusRepositoryUnavailable",
    "GovernedCorpusRetrievalRepository",
    "GovernedCorpusVersionConflict",
    "InMemoryGovernedCorpusRepository",
    "RankedPage",
    "RankedSource",
    "validate_write_evidence",
]
