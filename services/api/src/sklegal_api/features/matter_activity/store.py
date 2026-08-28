"""Feature-local stores for Matter activity and export proposals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from .contracts import (
    ActivityExportProposalRead,
    ActivityWatermarkRead,
    MatterActivityItemRead,
    validate_activity_supersession_graph,
)


class ActivityStoreUnavailable(RuntimeError):
    """The activity projection has no trustworthy current answer."""


class ActivityIdempotencyConflict(RuntimeError):
    """An idempotency key was reused for another export proposal."""


@dataclass(frozen=True, slots=True)
class TenantAuditLink:
    tenant_id: UUID
    event_sequence: int
    event_sha256: str
    previous_event_sha256: str | None


@dataclass(frozen=True, slots=True)
class ActivityAuditFact:
    fact_id: UUID
    tenant_id: UUID
    matter_id: UUID
    principal_id: UUID
    action: str
    proposal_id: UUID
    selection_sha256: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ActivityOutboxMessage:
    message_id: UUID
    tenant_id: UUID
    matter_id: UUID
    audit_fact_id: UUID
    proposal_id: UUID
    selection_sha256: str
    destination: str = "matter_activity.local"


class MatterActivityStore(Protocol):
    def ensure_ready(self) -> None: ...

    def read_page(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        after_sequence: int,
        snapshot_sequence: int | None,
        snapshot_sha256: str | None,
        limit: int,
        verified_at: datetime,
    ) -> tuple[tuple[MatterActivityItemRead, ...], bool, ActivityWatermarkRead]: ...

    def read_selection(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        first_sequence: int,
        last_sequence: int,
        projected_sequence: int,
        projected_sha256: str,
        verified_at: datetime,
    ) -> tuple[tuple[MatterActivityItemRead, ...], ActivityWatermarkRead]: ...

    def append_export(
        self,
        *,
        proposal: ActivityExportProposalRead,
        policy_revision: str,
        request_sha256: str,
        audit_fact: ActivityAuditFact,
        outbox: ActivityOutboxMessage,
    ) -> tuple[ActivityExportProposalRead, bool]: ...


class InMemoryMatterActivityStore:
    """Versioned public-synthetic activity store with fail-closed verification."""

    def __init__(
        self,
        *,
        links: tuple[TenantAuditLink, ...],
        items: tuple[MatterActivityItemRead, ...],
        projected_sequence: int,
        projected_sha256: str,
    ) -> None:
        self.available = True
        self.fail_next_export = False
        self._initial_links = links
        self._initial_items = items
        self._initial_projected = (projected_sequence, projected_sha256)
        self._proposals: dict[
            tuple[UUID, UUID, str], tuple[str, ActivityExportProposalRead]
        ] = {}
        self._audit: list[ActivityAuditFact] = []
        self._outbox: list[ActivityOutboxMessage] = []
        self._links = links
        self._items = items
        self._projected_sequence = projected_sequence
        self._projected_sha256 = projected_sha256
        self._validate_seed()

    @property
    def audit_facts(self) -> tuple[ActivityAuditFact, ...]:
        return tuple(self._audit)

    @property
    def outbox_messages(self) -> tuple[ActivityOutboxMessage, ...]:
        return tuple(self._outbox)

    def reset(self) -> None:
        self._links = self._initial_links
        self._items = self._initial_items
        self._projected_sequence, self._projected_sha256 = self._initial_projected
        self._proposals.clear()
        self._audit.clear()
        self._outbox.clear()
        self.available = True
        self.fail_next_export = False

    def replace_links_for_test(self, links: tuple[TenantAuditLink, ...]) -> None:
        self._links = links

    def set_projected_for_test(self, sequence: int, sha256: str) -> None:
        self._projected_sequence = sequence
        self._projected_sha256 = sha256

    def ensure_ready(self) -> None:
        if not self.available:
            raise ActivityStoreUnavailable("activity projection unavailable")
        self._verify_chain()

    def _validate_seed(self) -> None:
        self._verify_chain()
        by_sequence = {link.event_sequence: link for link in self._links}
        projected = by_sequence.get(self._projected_sequence)
        if projected is None or projected.event_sha256 != self._projected_sha256:
            raise ValueError("projection watermark is not in the Tenant audit chain")
        for item in self._items:
            link = by_sequence.get(item.event_sequence)
            if (
                link is None
                or link.tenant_id != item.tenant_id
                or link.event_sha256 != item.event_sha256
                or link.previous_event_sha256 != item.previous_event_sha256
            ):
                raise ValueError("activity item is not bound to the Tenant audit chain")
        validate_activity_supersession_graph(self._items)

    def _verify_chain(self) -> None:
        if not self._links:
            raise ActivityStoreUnavailable("Tenant audit chain unavailable")
        ordered = sorted(self._links, key=lambda item: item.event_sequence)
        tenant_id = ordered[0].tenant_id
        previous: str | None = None
        expected = 1
        for link in ordered:
            if (
                link.tenant_id != tenant_id
                or link.event_sequence != expected
                or link.previous_event_sha256 != previous
            ):
                raise ActivityStoreUnavailable("Tenant audit chain verification failed")
            previous = link.event_sha256
            expected += 1

    def _watermark(
        self,
        *,
        snapshot_sequence: int,
        snapshot_sha256: str,
        verified_at: datetime,
    ) -> ActivityWatermarkRead:
        head = max(self._links, key=lambda item: item.event_sequence)
        selected = next(
            (
                link
                for link in self._links
                if link.event_sequence == snapshot_sequence
                and link.event_sha256 == snapshot_sha256
            ),
            None,
        )
        if selected is None or snapshot_sequence > self._projected_sequence:
            raise ActivityStoreUnavailable("activity snapshot is unavailable")
        return ActivityWatermarkRead(
            tenant_head_sequence=head.event_sequence,
            tenant_head_sha256=head.event_sha256,
            projected_sequence=snapshot_sequence,
            projected_event_sha256=snapshot_sha256,
            lag_events=head.event_sequence - snapshot_sequence,
            verified_at=verified_at,
        )

    def read_page(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        after_sequence: int,
        snapshot_sequence: int | None,
        snapshot_sha256: str | None,
        limit: int,
        verified_at: datetime,
    ) -> tuple[tuple[MatterActivityItemRead, ...], bool, ActivityWatermarkRead]:
        self.ensure_ready()
        if snapshot_sequence is None:
            snapshot_sequence = self._projected_sequence
            snapshot_sha256 = self._projected_sha256
        if snapshot_sha256 is None:
            raise ActivityStoreUnavailable("activity snapshot is unavailable")
        watermark = self._watermark(
            snapshot_sequence=snapshot_sequence,
            snapshot_sha256=snapshot_sha256,
            verified_at=verified_at,
        )
        visible = tuple(
            item
            for item in sorted(self._items, key=lambda value: value.event_sequence)
            if item.tenant_id == tenant_id
            and item.matter_id == matter_id
            and after_sequence < item.event_sequence <= snapshot_sequence
        )
        return visible[:limit], len(visible) > limit, watermark

    def read_selection(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        first_sequence: int,
        last_sequence: int,
        projected_sequence: int,
        projected_sha256: str,
        verified_at: datetime,
    ) -> tuple[tuple[MatterActivityItemRead, ...], ActivityWatermarkRead]:
        self.ensure_ready()
        watermark = self._watermark(
            snapshot_sequence=projected_sequence,
            snapshot_sha256=projected_sha256,
            verified_at=verified_at,
        )
        items = tuple(
            item
            for item in sorted(self._items, key=lambda value: value.event_sequence)
            if item.tenant_id == tenant_id
            and item.matter_id == matter_id
            and first_sequence <= item.event_sequence <= last_sequence
        )
        if (
            not items
            or items[0].event_sequence != first_sequence
            or items[-1].event_sequence != last_sequence
        ):
            raise ActivityStoreUnavailable("activity export selection is unavailable")
        return items, watermark

    def append_export(
        self,
        *,
        proposal: ActivityExportProposalRead,
        policy_revision: str,
        request_sha256: str,
        audit_fact: ActivityAuditFact,
        outbox: ActivityOutboxMessage,
    ) -> tuple[ActivityExportProposalRead, bool]:
        self.ensure_ready()
        key = (
            proposal.tenant_id,
            proposal.matter_id,
            proposal.idempotency_key_sha256,
        )
        existing = self._proposals.get(key)
        if existing is not None:
            if existing[0] != request_sha256:
                raise ActivityIdempotencyConflict("activity export key conflict")
            return existing[1], True
        if (
            audit_fact.tenant_id != proposal.tenant_id
            or audit_fact.matter_id != proposal.matter_id
            or audit_fact.principal_id != proposal.proposed_by_principal_id
            or audit_fact.action != "matter.activity_export.created"
            or audit_fact.proposal_id != proposal.proposal_id
            or audit_fact.selection_sha256 != proposal.selection_sha256
            or outbox.tenant_id != proposal.tenant_id
            or outbox.matter_id != proposal.matter_id
            or outbox.audit_fact_id != audit_fact.fact_id
            or outbox.proposal_id != proposal.proposal_id
            or outbox.selection_sha256 != proposal.selection_sha256
        ):
            raise ActivityStoreUnavailable("activity export evidence is inconsistent")
        if self.fail_next_export:
            self.fail_next_export = False
            raise ActivityStoreUnavailable("activity export transaction unavailable")
        self._proposals[key] = (request_sha256, proposal)
        self._audit.append(audit_fact)
        self._outbox.append(outbox)
        return proposal, False
