"""Repository contract and atomic in-memory adapter for Work Products."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from copy import deepcopy
from datetime import datetime
from typing import Protocol
from uuid import UUID

from .models import (
    AuthorizationEvidence,
    WorkProductAggregate,
    WorkProductAuditEvent,
    WorkProductOutboxEvent,
    canonical_sha256,
)


class WorkProductRepositoryError(RuntimeError):
    """Base sanitized repository failure."""


class WorkProductNotFound(WorkProductRepositoryError):
    pass


class WorkProductVersionConflict(WorkProductRepositoryError):
    pass


class WorkProductIdempotencyConflict(WorkProductRepositoryError):
    pass


class WorkProductRepositoryUnavailable(WorkProductRepositoryError):
    pass


class WorkProductRepository(Protocol):
    """Storage boundary used by the application service."""

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool: ...

    def current_policy_revision(self, tenant_id: UUID, matter_id: UUID) -> str: ...

    def claim_exists(
        self, tenant_id: UUID, matter_id: UUID, claim_id: UUID
    ) -> bool: ...

    def authorization_is_active(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        evidence: AuthorizationEvidence,
        at: datetime,
    ) -> bool: ...

    def get(
        self, tenant_id: UUID, matter_id: UUID, work_product_id: UUID
    ) -> WorkProductAggregate | None: ...

    def list_for_matter(
        self, tenant_id: UUID, matter_id: UUID
    ) -> tuple[WorkProductAggregate, ...]: ...

    def find_idempotent(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        idempotency_key: UUID,
    ) -> tuple[str, WorkProductAggregate] | None: ...

    def commit(
        self,
        *,
        expected_aggregate_version: int | None,
        idempotency_key: UUID,
        request_sha256: str,
        aggregate: WorkProductAggregate,
        audit: WorkProductAuditEvent,
        outbox: WorkProductOutboxEvent,
    ) -> WorkProductAggregate: ...

    def audit_events(self) -> tuple[WorkProductAuditEvent, ...]: ...

    def outbox_events(self) -> tuple[WorkProductOutboxEvent, ...]: ...


def validate_write_evidence(
    aggregate: WorkProductAggregate,
    audit: WorkProductAuditEvent,
    outbox: WorkProductOutboxEvent,
) -> None:
    """Verify exact aggregate, audit, and outbox linkage before a write."""

    identity = (
        aggregate.tenant_id,
        aggregate.matter_id,
        aggregate.work_product_id,
        aggregate.aggregate_version,
    )
    if identity != (
        audit.tenant_id,
        audit.matter_id,
        audit.work_product_id,
        audit.aggregate_version,
    ):
        raise WorkProductRepositoryUnavailable(
            "audit identity does not match aggregate"
        )
    if identity != (
        outbox.tenant_id,
        outbox.matter_id,
        outbox.work_product_id,
        outbox.aggregate_version,
    ):
        raise WorkProductRepositoryUnavailable(
            "outbox identity does not match aggregate"
        )
    if outbox.event_id != audit.event_id or outbox.outbox_id != audit.event_id:
        raise WorkProductRepositoryUnavailable(
            "outbox does not reference exact audit event"
        )
    if audit.subject_sha256 != canonical_sha256(aggregate):
        raise WorkProductRepositoryUnavailable(
            "audit subject digest does not match aggregate"
        )
    if outbox.payload_sha256 != audit.subject_sha256:
        raise WorkProductRepositoryUnavailable(
            "outbox digest does not match audit subject"
        )


class InMemoryWorkProductRepository:
    """Deterministic transactional adapter for service and outage tests."""

    def __init__(
        self,
        *,
        memberships: Iterable[tuple[UUID, UUID, UUID]] = (),
        policy_revisions: dict[tuple[UUID, UUID], str] | None = None,
    ) -> None:
        self._memberships = set(memberships)
        self._policy_revisions = dict(policy_revisions or {})
        self._revoked_decisions: set[UUID] = set()
        self._revoked_credentials: set[str] = set()
        self._claims: set[tuple[UUID, UUID, UUID]] = set()
        self._aggregates: dict[tuple[UUID, UUID, UUID], WorkProductAggregate] = {}
        self._idempotency: dict[
            tuple[UUID, UUID, UUID], tuple[str, WorkProductAggregate]
        ] = {}
        self._audits: list[WorkProductAuditEvent] = []
        self._outbox: list[WorkProductOutboxEvent] = []
        self.fail_on: str | None = None

    def add_membership(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> None:
        self._memberships.add((tenant_id, matter_id, principal_id))

    def set_policy_revision(
        self, tenant_id: UUID, matter_id: UUID, revision: str
    ) -> None:
        self._policy_revisions[(tenant_id, matter_id)] = revision

    def revoke_decision(self, decision_id: UUID) -> None:
        self._revoked_decisions.add(decision_id)

    def restore_decision(self, decision_id: UUID) -> None:
        self._revoked_decisions.discard(decision_id)

    def revoke_credential(self, credential_digest: str) -> None:
        self._revoked_credentials.add(credential_digest)

    def restore_credential(self, credential_digest: str) -> None:
        self._revoked_credentials.discard(credential_digest)

    def add_claim(self, tenant_id: UUID, matter_id: UUID, claim_id: UUID) -> None:
        self._claims.add((tenant_id, matter_id, claim_id))

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        self._raise_if("membership")
        return (tenant_id, matter_id, principal_id) in self._memberships

    def current_policy_revision(self, tenant_id: UUID, matter_id: UUID) -> str:
        self._raise_if("policy")
        try:
            return self._policy_revisions[(tenant_id, matter_id)]
        except KeyError:
            raise WorkProductRepositoryUnavailable(
                "current policy revision is unavailable"
            ) from None

    def claim_exists(self, tenant_id: UUID, matter_id: UUID, claim_id: UUID) -> bool:
        self._raise_if("store")
        return (tenant_id, matter_id, claim_id) in self._claims

    def authorization_is_active(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        evidence: AuthorizationEvidence,
        at: datetime,
    ) -> bool:
        self._raise_if("authorization")
        requested = (
            evidence.credential_digest,
            *evidence.ancestor_credential_digests,
        )
        revoked = sorted(self._revoked_credentials.intersection(requested))
        revision = hashlib.sha256(
            json.dumps(revoked, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return (
            evidence.decision_id not in self._revoked_decisions
            and evidence.tenant_id == tenant_id
            and evidence.matter_id == matter_id
            and evidence.authorized_at <= at < evidence.expires_at
            and self._policy_revisions.get((tenant_id, matter_id))
            == evidence.policy_revision
            and evidence.revocation_revision == revision
            and not revoked
        )

    def get(
        self, tenant_id: UUID, matter_id: UUID, work_product_id: UUID
    ) -> WorkProductAggregate | None:
        self._raise_if("store")
        value = self._aggregates.get((tenant_id, matter_id, work_product_id))
        return deepcopy(value)

    def list_for_matter(
        self, tenant_id: UUID, matter_id: UUID
    ) -> tuple[WorkProductAggregate, ...]:
        self._raise_if("store")
        return tuple(
            deepcopy(value)
            for (row_tenant, row_matter, _), value in sorted(
                self._aggregates.items(), key=lambda item: str(item[0][2])
            )
            if row_tenant == tenant_id and row_matter == matter_id
        )

    def find_idempotent(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        idempotency_key: UUID,
    ) -> tuple[str, WorkProductAggregate] | None:
        self._raise_if("store")
        value = self._idempotency.get((tenant_id, matter_id, idempotency_key))
        return deepcopy(value)

    def commit(
        self,
        *,
        expected_aggregate_version: int | None,
        idempotency_key: UUID,
        request_sha256: str,
        aggregate: WorkProductAggregate,
        audit: WorkProductAuditEvent,
        outbox: WorkProductOutboxEvent,
    ) -> WorkProductAggregate:
        validate_write_evidence(aggregate, audit, outbox)
        self._raise_if("store")
        receipt_key = (aggregate.tenant_id, aggregate.matter_id, idempotency_key)
        prior = self._idempotency.get(receipt_key)
        if prior is not None:
            if prior[0] != request_sha256:
                raise WorkProductIdempotencyConflict("idempotency bytes changed")
            return deepcopy(prior[1])

        aggregate_key = (
            aggregate.tenant_id,
            aggregate.matter_id,
            aggregate.work_product_id,
        )
        current = self._aggregates.get(aggregate_key)
        if expected_aggregate_version is None:
            if current is not None or aggregate.aggregate_version != 1:
                raise WorkProductVersionConflict("Work Product already exists")
        elif (
            current is None
            or current.aggregate_version != expected_aggregate_version
            or aggregate.aggregate_version != expected_aggregate_version + 1
        ):
            raise WorkProductVersionConflict("Work Product aggregate version is stale")

        # Failure injection occurs before publishing any copied state. This mirrors
        # one database transaction that contains the aggregate, audit, and outbox.
        self._raise_if("audit")
        self._raise_if("outbox")
        aggregates = dict(self._aggregates)
        receipts = dict(self._idempotency)
        audits = list(self._audits)
        outbox_events = list(self._outbox)
        aggregates[aggregate_key] = deepcopy(aggregate)
        receipts[receipt_key] = (request_sha256, deepcopy(aggregate))
        audits.append(deepcopy(audit))
        outbox_events.append(deepcopy(outbox))
        self._aggregates = aggregates
        self._idempotency = receipts
        self._audits = audits
        self._outbox = outbox_events
        return deepcopy(aggregate)

    def audit_events(self) -> tuple[WorkProductAuditEvent, ...]:
        self._raise_if("audit")
        return tuple(deepcopy(self._audits))

    def outbox_events(self) -> tuple[WorkProductOutboxEvent, ...]:
        self._raise_if("outbox")
        return tuple(deepcopy(self._outbox))

    def _raise_if(self, boundary: str) -> None:
        if self.fail_on == boundary:
            raise WorkProductRepositoryUnavailable(f"{boundary} is unavailable")
