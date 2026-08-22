"""Project the imported Liberty Auto pilot into workspace read models.

Reads the S5-01B approved pilot import (plan plus in-memory import store)
and builds the SKL-S4-02 matter workspace aggregate in legal-domain
terminology. The projection never harmonizes: unresolved tension groups
stay unresolved, approval and execution states stay at their imported
negative values (``pending_review`` / ``not_started``), missing sources
become explicit gaps, and legacy identifiers surface only as provenance
aliases, never as canonical record types.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sklegal_migration import InMemoryPilotImportStore, PilotImportPlan

from .workspace import (
    AuditEntryRead,
    ClientSummaryRead,
    CommunicationRead,
    ExecutionStateRead,
    FactAssertionRead,
    InMemoryWorkspaceReadStore,
    MatterDetailRead,
    MatterWorkspaceRead,
    SourceFileRead,
    TensionGroupRead,
    TimelineEventRead,
    VersionLineageRead,
    WorkspaceGapRead,
    WorkspaceMatterRead,
    WorkspaceProvenanceRead,
)


def _source_id(relative_path: str, content_sha256: str) -> str:
    """Recompute the importer's deterministic source reference id."""
    return str(uuid5(NAMESPACE_URL, f"source:{relative_path}:{content_sha256}"))


def _parse_instant(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _parse_date_value(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _event_type_from_rule(mapping_rule: str) -> str:
    """Extract the legal event type from an approved mapping rule id.

    Rule ids carry legacy storage labels in their first segment; only the
    legal middle segment (for example ``transaction_review``) is surfaced.
    """
    parts = mapping_rule.split(".")
    if len(parts) != 2 or "@" not in parts[1]:
        raise ValueError(f"unexpected mapping rule id: {mapping_rule!r}")
    return parts[1].split("@", 1)[0]


def _fact_text(facts: Iterable[Any], predicate: str) -> str | None:
    for fact in facts:
        if fact.predicate == predicate and isinstance(fact.value, str):
            return fact.value
    return None


def project_pilot_matter_workspace(
    *,
    plan: PilotImportPlan,
    store: InMemoryPilotImportStore,
    workspace_store: InMemoryWorkspaceReadStore,
    tenant_id: UUID,
    client_id: UUID,
    client_display_name: str,
    matter_status: str,
    matter_summary: str,
    member_principal_ids: Iterable[UUID],
    matter_title: str | None = None,
    engagement_id: UUID | None = None,
    engagement_display_name: str | None = None,
    current_source_snapshot: str | None = None,
) -> MatterWorkspaceRead:
    """Build and register the pilot matter workspace view.

    Fails closed with ValueError when the batch is not recorded in the
    import store, the batch tenant does not match, or the plan does not
    carry exactly one matter target.
    """
    batch = store.batches.get(plan.import_batch_id)
    if batch is None:
        raise ValueError("pilot import batch is not recorded; refusing projection")
    if str(batch["tenant_id"]) != str(tenant_id):
        raise ValueError("pilot import batch tenant mismatch; refusing projection")

    path_by_source_id = {
        _source_id(item.relative_path, item.content_sha256): item.relative_path
        for item in plan.source_files
    }
    observed_by_path = {
        item.relative_path: _parse_instant(item.observed_at)
        for item in plan.source_files
    }

    matter_records = [r for r in plan.records if r.target_type == "matter"]
    event_records = [r for r in plan.records if r.target_type == "matter_event"]
    if len(matter_records) != 1:
        raise ValueError("pilot plan must carry exactly one matter target")
    matter_record = matter_records[0]
    matter_id = UUID(matter_record.target_id)

    facts_by_source: dict[str, list[Any]] = {}
    for fact in plan.facts:
        facts_by_source.setdefault(fact.source_reference_id, []).append(fact)
    matter_facts = facts_by_source.get(matter_record.source_reference_id, [])

    title = matter_title or _fact_text(matter_facts, "title")
    if title is None or not title.strip():
        raise ValueError("pilot matter carries no title fact; refusing projection")
    legacy_alias = _fact_text(matter_facts, "legacy_id")
    matter_aliases = (legacy_alias,) if legacy_alias is not None else ()
    opened_at = _parse_date_value(_fact_text(matter_facts, "created_date"))

    timeline: list[TimelineEventRead] = []
    for record in event_records:
        event_facts = facts_by_source.get(record.source_reference_id, [])
        description = _fact_text(event_facts, "title")
        if description is None or not description.strip():
            raise ValueError(
                "pilot matter event carries no title fact; refusing projection"
            )
        event_alias = _fact_text(event_facts, "legacy_id")
        source_path = path_by_source_id.get(record.source_reference_id)
        observed = (
            observed_by_path[source_path]
            if source_path is not None
            else _parse_instant(str(batch["approved_at"]))
        )
        timeline.append(
            TimelineEventRead(
                event_id=UUID(record.target_id),
                event_type=_event_type_from_rule(record.mapping_rule),
                description=description,
                occurred_at=_parse_date_value(_fact_text(event_facts, "created_date")),
                observed_at=observed,
                status=record.mapping_status,
                source_path=source_path,
                legacy_aliases=(event_alias,) if event_alias is not None else (),
            )
        )
    timeline.sort(key=lambda event: event.occurred_at or event.observed_at)

    gaps: list[WorkspaceGapRead] = []
    missing_sources: set[str] = set()
    fact_reads: list[FactAssertionRead] = []
    for fact in sorted(plan.facts, key=lambda item: item.fact_assertion_id):
        source_path = path_by_source_id.get(fact.source_reference_id)
        missing = source_path is None
        if missing:
            missing_sources.add(fact.source_reference_id)
        fact_reads.append(
            FactAssertionRead(
                fact_assertion_id=UUID(fact.fact_assertion_id),
                predicate=fact.predicate,
                asserted_value=fact.value,
                value_type=fact.value_type,
                review_status=fact.review_status,
                source_path=source_path,
                source_locator=fact.source_locator,
                source_missing=missing,
                tension_group_key=fact.tension_group_key,
            )
        )
    for source_id in sorted(missing_sources):
        gaps.append(
            WorkspaceGapRead(
                gap_id=f"missing-source-{source_id}",
                kind="missing_source",
                description=(
                    "A fact assertion references a source that has no "
                    "recorded source file; the assertion stays visible "
                    "with a missing-source marker."
                ),
            )
        )
    gaps.append(
        WorkspaceGapRead(
            gap_id="unrecorded-parties",
            kind="unrecorded_parties",
            description=(
                "No verified parties are recorded for this matter; "
                "party-related assertions remain visible under Facts "
                "and tensions."
            ),
        )
    )
    gaps.append(
        WorkspaceGapRead(
            gap_id="unrecorded-evidence",
            kind="unrecorded_evidence",
            description="No evidence items are recorded for this matter yet.",
        )
    )
    if engagement_id is None:
        gaps.append(
            WorkspaceGapRead(
                gap_id="unrecorded-engagement",
                kind="unrecorded_engagement",
                description="No engagement is recorded for this matter yet.",
            )
        )

    communications: list[CommunicationRead] = []
    for item in plan.source_files:
        if "/correspondence/" not in item.relative_path:
            continue
        source_id = _source_id(item.relative_path, item.content_sha256)
        communications.append(
            CommunicationRead(
                communication_id=UUID(
                    str(uuid5(NAMESPACE_URL, f"communication:{source_id}"))
                ),
                channel="correspondence",
                summary=item.relative_path.rsplit("/", 1)[-1],
                occurred_at=_parse_instant(item.observed_at),
                status="recorded",
                source_path=item.relative_path,
            )
        )
    communications.sort(key=lambda entry: entry.communication_id)

    states = tuple(
        sorted(
            (
                ExecutionStateRead(
                    target_type=row.target_type,
                    target_id=UUID(row.target_id),
                    state_kind=row.state_kind,
                    state_value=row.state_value,
                )
                for row in store.states.values()
                if row.batch_id == plan.import_batch_id
            ),
            key=lambda row: (str(row.target_id), row.state_kind),
        )
    )

    approved_at = _parse_instant(str(batch["approved_at"]))
    audit = [
        AuditEntryRead(
            audit_id=f"pilot-mapping-approval-{plan.import_batch_id}",
            action="pilot_mapping_review.approved",
            actor=str(batch["approved_by"]),
            occurred_at=approved_at,
            outcome="approved",
            detail=str(batch["review_artifact"]),
        ),
        AuditEntryRead(
            audit_id=f"pilot-import-batch-{plan.import_batch_id}",
            action="pilot_import.recorded",
            actor=str(batch["approved_by"]),
            occurred_at=approved_at,
            outcome=str(batch["status"]),
            detail=f"import batch {plan.import_batch_id}",
        ),
    ]
    if batch["status"] == "withdrawn" and batch["withdrawn_at"] is not None:
        audit.append(
            AuditEntryRead(
                audit_id=f"pilot-import-withdrawn-{plan.import_batch_id}",
                action="pilot_import.withdrawn",
                actor=str(batch["approved_by"]),
                occurred_at=_parse_instant(str(batch["withdrawn_at"])),
                outcome="withdrawn",
                detail=f"import batch {plan.import_batch_id}",
            )
        )

    observed_at = max(observed_by_path.values(), default=approved_at)
    current_snapshot = current_source_snapshot or plan.source_snapshot
    provenance = WorkspaceProvenanceRead(
        source_snapshot=plan.source_snapshot,
        current_source_snapshot=current_snapshot,
        adapter_version=plan.adapter_version,
        observed_at=observed_at,
        stale=current_snapshot != plan.source_snapshot,
        source_files=tuple(
            SourceFileRead(
                relative_path=item.relative_path,
                content_sha256=item.content_sha256,
                observed_at=_parse_instant(item.observed_at),
                byte_count=item.byte_count,
            )
            for item in plan.source_files
        ),
    )

    view = MatterWorkspaceRead(
        matter=WorkspaceMatterRead(
            matter_id=matter_id,
            client_id=client_id,
            client_display_name=client_display_name,
            engagement_id=engagement_id,
            title=title,
            summary=matter_summary,
            status=matter_status,
            opened_at=opened_at,
            legacy_aliases=matter_aliases,
        ),
        engagement_display_name=engagement_display_name,
        parties=(),
        timeline=tuple(timeline),
        facts=tuple(fact_reads),
        tensions=tuple(
            TensionGroupRead(
                tension_key=tension.key,
                status=tension.status,
                assertion_ids=tuple(UUID(item) for item in tension.assertion_ids),
                review_required=tension.review_required,
            )
            for tension in plan.tensions
        ),
        evidence=(),
        communications=tuple(communications),
        version_lineage=tuple(
            VersionLineageRead(
                packet_version=item.packet_version,
                source_path=item.source_path,
                source_sha256=item.source_sha256,
                historical=item.historical,
                current_review_baseline=item.current_review_baseline,
            )
            for item in plan.version_lineage
        ),
        execution_states=states,
        gaps=tuple(gaps),
        audit=tuple(audit),
        provenance=provenance,
    )

    workspace_store.add_client(
        tenant_id,
        ClientSummaryRead(
            id=client_id,
            tenant_id=tenant_id,
            display_name=client_display_name,
            matter_count=1,
        ),
    )
    workspace_store.add_matter(
        tenant_id,
        MatterDetailRead(
            id=matter_id,
            tenant_id=tenant_id,
            client_id=client_id,
            client_display_name=client_display_name,
            title=title,
            status=matter_status,
            engagement_id=engagement_id,
            summary=matter_summary,
            opened_on=opened_at.date().isoformat() if opened_at is not None else None,
            legacy_aliases=matter_aliases,
        ),
    )
    workspace_store.add_workspace(tenant_id, view)
    workspace_store.set_matter_members(
        tenant_id, matter_id, frozenset(member_principal_ids)
    )
    return view
