"""Append-only Agent Run repository contracts and hermetic implementation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from .models import AgentRunAuditEvent, AgentRunRecord, canonical_sha256


class AgentRunRepositoryError(RuntimeError):
    """Base fail-closed repository error."""


class AgentRunRepositoryUnavailable(AgentRunRepositoryError):
    """Durable state or atomic audit persistence has no current answer."""


class AgentRunIdempotencyConflict(AgentRunRepositoryError):
    """An idempotency key is already bound to different request bytes."""


class AgentRunVersionConflict(AgentRunRepositoryError):
    """An append named a stale or non-adjacent record version."""


class AgentRunNotFound(AgentRunRepositoryError):
    """The scoped Agent Run does not exist."""


class AgentRunIdempotencyReceipt(Protocol):
    request_sha256: str
    run_id: UUID


class AgentRunRepository(Protocol):
    """Atomic persistence boundary for run versions and audit evidence."""

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool: ...

    def find_idempotent(
        self, tenant_id: UUID, matter_id: UUID, idempotency_key: UUID
    ) -> tuple[str, AgentRunRecord] | None: ...

    def commit_new(
        self,
        *,
        request_sha256: str,
        record: AgentRunRecord,
        audit: AgentRunAuditEvent,
    ) -> AgentRunRecord: ...

    def append_version(
        self,
        *,
        expected_version: int,
        idempotency_key: UUID,
        request_sha256: str,
        record: AgentRunRecord,
        audit: AgentRunAuditEvent,
    ) -> AgentRunRecord: ...

    def get(
        self, tenant_id: UUID, matter_id: UUID, run_id: UUID
    ) -> AgentRunRecord | None: ...

    def list_for_matter(
        self, tenant_id: UUID, matter_id: UUID
    ) -> tuple[AgentRunRecord, ...]: ...

    def history(
        self, tenant_id: UUID, matter_id: UUID, run_id: UUID
    ) -> tuple[AgentRunRecord, ...]: ...

    def audit_events(self) -> tuple[AgentRunAuditEvent, ...]: ...


def validate_write_evidence(
    record: AgentRunRecord, audit: AgentRunAuditEvent
) -> AgentRunRecord:
    """Revalidate copied models and bind the atomic audit to exact bytes."""

    validated = AgentRunRecord.model_validate(
        record.model_dump(mode="json", by_alias=True)
    )
    if (
        audit.tenant_id != validated.tenant_id
        or audit.matter_id != validated.matter_id
        or audit.run_id != validated.run_id
        or audit.run_version != validated.version
        or audit.event_id not in validated.audit_event_ids
        or audit.subject_sha256 != canonical_sha256(validated)
    ):
        raise AgentRunVersionConflict(
            "Agent Run audit does not bind exact record bytes"
        )
    return validated


class InMemoryAgentRunRepository:
    """Hermetic repository with the same atomic and idempotent contract.

    This implementation is only for unit tests and explicit public-synthetic
    composition. ``audit_available`` is checked before any byte changes.
    """

    def __init__(self, *, available: bool = True, audit_available: bool = True) -> None:
        self.available = available
        self.audit_available = audit_available
        self._members: dict[tuple[UUID, UUID], frozenset[UUID]] = {}
        self._history: dict[tuple[UUID, UUID, UUID], list[AgentRunRecord]] = (
            defaultdict(list)
        )
        self._idempotency: dict[tuple[UUID, UUID, UUID], tuple[str, UUID]] = {}
        self._audit: list[AgentRunAuditEvent] = []

    def _require_available(self) -> None:
        if not self.available:
            raise AgentRunRepositoryUnavailable("Agent Run repository unavailable")

    def _require_atomic_audit(self) -> None:
        self._require_available()
        if not self.audit_available:
            raise AgentRunRepositoryUnavailable("Agent Run audit unavailable")

    def set_matter_members(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        principal_ids: Iterable[UUID],
    ) -> None:
        self._members[(tenant_id, matter_id)] = frozenset(principal_ids)

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        self._require_available()
        return principal_id in self._members.get((tenant_id, matter_id), frozenset())

    def find_idempotent(
        self, tenant_id: UUID, matter_id: UUID, idempotency_key: UUID
    ) -> tuple[str, AgentRunRecord] | None:
        self._require_available()
        value = self._idempotency.get((tenant_id, matter_id, idempotency_key))
        if value is None:
            return None
        request_sha256, run_id = value
        record = self.get(tenant_id, matter_id, run_id)
        if record is None:
            raise AgentRunRepositoryUnavailable("idempotency receipt lost its run")
        return request_sha256, record

    def commit_new(
        self,
        *,
        request_sha256: str,
        record: AgentRunRecord,
        audit: AgentRunAuditEvent,
    ) -> AgentRunRecord:
        self._require_atomic_audit()
        record = validate_write_evidence(record, audit)
        key = (record.tenant_id, record.matter_id, record.request.idempotency_key)
        existing = self._idempotency.get(key)
        if existing is not None:
            if existing[0] != request_sha256:
                raise AgentRunIdempotencyConflict(
                    "idempotency key is bound to another analysis request"
                )
            current = self.get(record.tenant_id, record.matter_id, existing[1])
            if current is None:
                raise AgentRunRepositoryUnavailable("idempotent run is unavailable")
            return current
        history_key = (record.tenant_id, record.matter_id, record.run_id)
        if record.version != 1 or self._history.get(history_key):
            raise AgentRunVersionConflict("new Agent Run must begin at version 1")
        self._history[history_key].append(record)
        self._idempotency[key] = (request_sha256, record.run_id)
        self._audit.append(audit)
        return record

    def append_version(
        self,
        *,
        expected_version: int,
        idempotency_key: UUID,
        request_sha256: str,
        record: AgentRunRecord,
        audit: AgentRunAuditEvent,
    ) -> AgentRunRecord:
        self._require_atomic_audit()
        record = validate_write_evidence(record, audit)
        receipt_key = (record.tenant_id, record.matter_id, idempotency_key)
        existing = self._idempotency.get(receipt_key)
        if existing is not None:
            if existing[0] != request_sha256 or existing[1] != record.run_id:
                raise AgentRunIdempotencyConflict(
                    "idempotency key is bound to another Agent Run mutation"
                )
            current = self.get(record.tenant_id, record.matter_id, record.run_id)
            if current is None:
                raise AgentRunRepositoryUnavailable("idempotent run is unavailable")
            return current
        history_key = (record.tenant_id, record.matter_id, record.run_id)
        history = self._history.get(history_key)
        if not history:
            raise AgentRunNotFound("Agent Run not found")
        current = history[-1]
        if (
            current.version != expected_version
            or record.version != expected_version + 1
        ):
            raise AgentRunVersionConflict("Agent Run version is stale")
        if (
            current.tenant_id != record.tenant_id
            or current.matter_id != record.matter_id
            or current.run_id != record.run_id
            or current.request != record.request
        ):
            raise AgentRunVersionConflict("Agent Run immutable identity changed")
        history.append(record)
        self._idempotency[receipt_key] = (request_sha256, record.run_id)
        self._audit.append(audit)
        return record

    def get(
        self, tenant_id: UUID, matter_id: UUID, run_id: UUID
    ) -> AgentRunRecord | None:
        self._require_available()
        history = self._history.get((tenant_id, matter_id, run_id))
        return None if not history else history[-1]

    def list_for_matter(
        self, tenant_id: UUID, matter_id: UUID
    ) -> tuple[AgentRunRecord, ...]:
        self._require_available()
        rows = [
            history[-1]
            for (row_tenant, row_matter, _), history in self._history.items()
            if row_tenant == tenant_id and row_matter == matter_id and history
        ]
        return tuple(sorted(rows, key=lambda row: (row.created_at, str(row.run_id))))

    def history(
        self, tenant_id: UUID, matter_id: UUID, run_id: UUID
    ) -> tuple[AgentRunRecord, ...]:
        self._require_available()
        return tuple(self._history.get((tenant_id, matter_id, run_id), ()))

    def audit_events(self) -> tuple[AgentRunAuditEvent, ...]:
        self._require_available()
        return tuple(self._audit)
