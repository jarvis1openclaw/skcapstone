"""Versioned public-synthetic Matter activity chronology."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from .contracts import (
    ActivitySourceKind,
    ActivitySourceRead,
    ActivityTraceRead,
    MatterActivityItemRead,
    canonical_sha256,
)
from .store import InMemoryMatterActivityStore, TenantAuditLink

PUBLIC_SYNTHETIC_ACTIVITY_VERSION = "sklegal-matter-activity-fixture/v1"

TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("20000000-0000-4000-8000-000000000001")
OTHER_MATTER_ID = UUID("20000000-0000-4000-8000-000000000002")
PRINCIPAL_ID = UUID("30000000-0000-4000-8000-000000000001")

_KINDS: tuple[ActivitySourceKind, ...] = (
    "audit_event",
    "matter_event",
    "workflow_reference",
    "agent_run",
    "tool_call",
    "source_reference",
    "work_product_version",
    "approval",
    "execution_event",
    "receipt",
    "correction",
)


def _uuid(prefix: int, sequence: int) -> UUID:
    return UUID(f"{prefix:08x}-0000-4000-8000-{sequence:012d}")


def build_public_synthetic_activity_store() -> InMemoryMatterActivityStore:
    """Create a deterministic chronology with one unrelated Matter chain link."""

    start = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    previous: str | None = None
    links: list[TenantAuditLink] = []
    items: list[MatterActivityItemRead] = []
    source_ids: list[UUID] = []
    for sequence in range(1, 14):
        source_id = _uuid(0x51000000, sequence)
        source_ids.append(source_id)
        kind = (
            "execution_event"
            if sequence == 12
            else _KINDS[(sequence - 1) % len(_KINDS)]
        )
        matter_id = OTHER_MATTER_ID if sequence == 13 else MATTER_ID
        status = "superseded" if sequence == 9 else "recorded"
        source_version = 3 if sequence == 12 else 2 if sequence == 9 else 1
        event_sha256 = canonical_sha256(
            {
                "fixtureVersion": PUBLIC_SYNTHETIC_ACTIVITY_VERSION,
                "kind": kind,
                "matterId": str(matter_id),
                "previousEventSha256": previous,
                "sequence": sequence,
                "sourceId": str(source_id),
            }
        )
        link = TenantAuditLink(
            tenant_id=TENANT_ID,
            event_sequence=sequence,
            event_sha256=event_sha256,
            previous_event_sha256=previous,
        )
        links.append(link)
        occurred_at = start + timedelta(minutes=sequence)
        items.append(
            MatterActivityItemRead(
                activity_id=_uuid(0x52000000, sequence),
                tenant_id=TENANT_ID,
                matter_id=matter_id,
                event_sequence=sequence,
                event_sha256=event_sha256,
                previous_event_sha256=previous,
                action=f"matter_activity.synthetic.{kind}",
                boundary=(
                    "workflow"
                    if kind in {"workflow_reference", "agent_run"}
                    else "tool"
                    if kind == "tool_call"
                    else "human"
                    if kind in {"approval", "correction"}
                    else "connector"
                    if kind == "receipt"
                    else "api"
                ),
                outcome="success",
                reason_code="synthetic_fixture",
                occurred_at=occurred_at,
                recorded_at=occurred_at + timedelta(seconds=1),
                actor_principal_id=PRINCIPAL_ID,
                authorization_decision_id=_uuid(0x53000000, sequence),
                policy_decision_id=_uuid(0x54000000, sequence),
                trace=ActivityTraceRead(
                    correlation_id=_uuid(0x55000000, 1),
                    causation_id=(
                        _uuid(0x52000000, sequence - 1) if sequence > 1 else None
                    ),
                    run_id=_uuid(0x56000000, 1),
                    workflow_reference_id=(
                        source_id if kind == "workflow_reference" else None
                    ),
                    agent_run_id=source_id if kind == "agent_run" else None,
                    tool_call_id=source_id if kind == "tool_call" else None,
                ),
                source=ActivitySourceRead(
                    kind=kind,
                    source_id=source_id,
                    source_version=source_version,
                    source_sha256=canonical_sha256(
                        {
                            "kind": kind,
                            "sourceId": str(source_id),
                            "sourceVersion": source_version,
                        }
                    ),
                    status=status,
                    recorded_at=occurred_at,
                    corrects_source_id=(
                        source_ids[6] if kind == "correction" else None
                    ),
                    superseded_by_source_id=(
                        _uuid(0x51000000, 12) if status == "superseded" else None
                    ),
                    superseded_by_source_version=(
                        3 if status == "superseded" else None
                    ),
                ),
            )
        )
        previous = event_sha256
    return InMemoryMatterActivityStore(
        links=tuple(links),
        items=tuple(items),
        projected_sequence=13,
        projected_sha256=links[-1].event_sha256,
    )
