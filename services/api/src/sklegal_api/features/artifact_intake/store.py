"""Atomic public-synthetic store used by the isolated artifact feature.

Durable PostgreSQL structure lives under the matching persistence feature and
migration 0040. This store deliberately models the same append-only revisions,
idempotency receipt, audit fact, and outbox transaction for bounded tests and
the future central composition lane.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Protocol
from uuid import UUID

from .contracts import (
    ArtifactCorrectionCommand,
    ArtifactIntakeCommand,
    ArtifactIntakeReceipt,
    ArtifactRead,
    ArtifactReviewCommand,
    ArtifactReviewReceipt,
    ArtifactSupersessionCommand,
)

ArtifactReceipt = ArtifactIntakeReceipt | ArtifactReviewReceipt
ArtifactCommand = (
    ArtifactIntakeCommand
    | ArtifactReviewCommand
    | ArtifactCorrectionCommand
    | ArtifactSupersessionCommand
)


class ArtifactStoreUnavailable(RuntimeError):
    """The artifact store or its atomic audit boundary is unavailable."""


@dataclass(frozen=True, slots=True)
class ArtifactAuditFact:
    event_id: UUID
    tenant_id: UUID
    matter_id: UUID
    principal_id: UUID
    action: str
    resource_id: UUID
    outcome: str
    policy_decision_id: UUID
    policy_revision: str
    request_sha256: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ArtifactOutboxMessage:
    outbox_id: UUID
    tenant_id: UUID
    matter_id: UUID
    event_id: UUID
    destination: str
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class ArtifactIdempotencyRecord:
    request_sha256: str
    response: ArtifactReceipt


class ArtifactStore(Protocol):
    """Minimal atomic store contract consumed by the service."""

    def ensure_ready(self) -> None: ...

    def idempotency_record(
        self, tenant_id: UUID, matter_id: UUID, idempotency_key: str
    ) -> ArtifactIdempotencyRecord | None: ...

    def by_content_sha256(
        self, tenant_id: UUID, matter_id: UUID, content_sha256: str
    ) -> ArtifactRead | None: ...

    def current(
        self, tenant_id: UUID, matter_id: UUID, artifact_id: UUID
    ) -> ArtifactRead | None: ...

    def all_current(
        self, tenant_id: UUID, matter_id: UUID
    ) -> tuple[ArtifactRead, ...]: ...

    def commit(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        idempotency_key: str,
        request_sha256: str,
        revisions: tuple[ArtifactRead, ...],
        command: ArtifactCommand,
        response: ArtifactReceipt,
        audit: ArtifactAuditFact,
        outbox: ArtifactOutboxMessage,
    ) -> None: ...

    def append_read_audit(
        self, audit: ArtifactAuditFact, outbox: ArtifactOutboxMessage
    ) -> None: ...


class InMemoryArtifactStore:
    """Append-only, lock-atomic public-synthetic artifact store."""

    def __init__(self) -> None:
        self.available = True
        self.audit_available = True
        self.lookup_count = 0
        self._lock = RLock()
        self._revisions: dict[tuple[UUID, UUID, UUID], list[ArtifactRead]] = {}
        self._idempotency: dict[tuple[UUID, UUID, str], ArtifactIdempotencyRecord] = {}
        self._audit_events: list[ArtifactAuditFact] = []
        self._outbox: list[ArtifactOutboxMessage] = []

    @property
    def audit_events(self) -> tuple[ArtifactAuditFact, ...]:
        return tuple(self._audit_events)

    @property
    def outbox(self) -> tuple[ArtifactOutboxMessage, ...]:
        return tuple(self._outbox)

    def ensure_ready(self) -> None:
        if not self.available or not self.audit_available:
            raise ArtifactStoreUnavailable("artifact atomic boundary unavailable")

    def idempotency_record(
        self, tenant_id: UUID, matter_id: UUID, idempotency_key: str
    ) -> ArtifactIdempotencyRecord | None:
        self.ensure_ready()
        self.lookup_count += 1
        return self._idempotency.get((tenant_id, matter_id, idempotency_key))

    def by_content_sha256(
        self, tenant_id: UUID, matter_id: UUID, content_sha256: str
    ) -> ArtifactRead | None:
        self.ensure_ready()
        self.lookup_count += 1
        for (row_tenant, row_matter, _), history in self._revisions.items():
            current = history[-1]
            if (
                row_tenant == tenant_id
                and row_matter == matter_id
                and current.content_sha256 == content_sha256
                and current.artifact_kind == "original"
            ):
                return current
        return None

    def current(
        self, tenant_id: UUID, matter_id: UUID, artifact_id: UUID
    ) -> ArtifactRead | None:
        self.ensure_ready()
        self.lookup_count += 1
        history = self._revisions.get((tenant_id, matter_id, artifact_id))
        return None if history is None else history[-1]

    def all_current(self, tenant_id: UUID, matter_id: UUID) -> tuple[ArtifactRead, ...]:
        self.ensure_ready()
        self.lookup_count += 1
        return tuple(
            history[-1]
            for (row_tenant, row_matter, _), history in self._revisions.items()
            if row_tenant == tenant_id and row_matter == matter_id
        )

    def commit(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        idempotency_key: str,
        request_sha256: str,
        revisions: tuple[ArtifactRead, ...],
        command: ArtifactCommand,
        response: ArtifactReceipt,
        audit: ArtifactAuditFact,
        outbox: ArtifactOutboxMessage,
    ) -> None:
        with self._lock:
            self.ensure_ready()
            key = (tenant_id, matter_id, idempotency_key)
            if key in self._idempotency:
                raise ValueError("idempotency record already exists")
            staged = {
                identity: list(history) for identity, history in self._revisions.items()
            }
            for revision in revisions:
                if revision.tenant_id != tenant_id or revision.matter_id != matter_id:
                    raise ValueError("artifact revision escaped transaction scope")
                identity = (tenant_id, matter_id, revision.artifact_id)
                history = staged.setdefault(identity, [])
                if history:
                    prior = history[-1]
                    if revision.projection_revision != prior.projection_revision + 1:
                        raise ValueError("artifact revision is not sequential")
                    immutable = (
                        "artifact_id",
                        "tenant_id",
                        "matter_id",
                        "artifact_kind",
                        "filename",
                        "media_type",
                        "byte_count",
                        "content_sha256",
                        "original_sha256",
                        "source",
                        "storage_locator",
                        "parent_artifact_id",
                        "derivation",
                        "created_at",
                    )
                    if any(
                        getattr(prior, field) != getattr(revision, field)
                        for field in immutable
                    ):
                        raise ValueError("artifact immutable field changed")
                elif revision.projection_revision != 1:
                    raise ValueError("new artifact revision must start at one")
                history.append(revision)
            self._revisions = staged
            self._idempotency[key] = ArtifactIdempotencyRecord(
                request_sha256=request_sha256,
                response=response,
            )
            self._audit_events.append(audit)
            self._outbox.append(outbox)

    def append_read_audit(
        self, audit: ArtifactAuditFact, outbox: ArtifactOutboxMessage
    ) -> None:
        with self._lock:
            self.ensure_ready()
            self._audit_events.append(audit)
            self._outbox.append(outbox)

    def artifact_ids(self, tenant_id: UUID, matter_id: UUID) -> tuple[UUID, ...]:
        return tuple(
            artifact_id
            for row_tenant, row_matter, artifact_id in self._revisions
            if row_tenant == tenant_id and row_matter == matter_id
        )

    def history(
        self, tenant_id: UUID, matter_id: UUID, artifact_id: UUID
    ) -> tuple[ArtifactRead, ...]:
        return tuple(self._revisions.get((tenant_id, matter_id, artifact_id), ()))
