"""Pilot verification suite: render, coverage, and replay evidence.

Assembles the SKL-S5-01C verification evidence for one imported pilot
Matter: the rendered SKL-S4-02 workspace aggregate, SKL-S2-05
materialized corpus registry coverage for the pinned snapshot, and a
deterministic replay comparison. The suite never mutates import,
corpus, or workspace state; it reads the results of the SKL-S5-01A dry
run, the SKL-S5-01B approved import, and the S4-02 projection, then
records typed booleans for every pilot acceptance check. Unresolved
Tension Groups, negative approval and execution states, explicit
record gaps, and legacy provenance aliases are verified exactly as
rendered; nothing is harmonized, advanced, or hidden.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, model_validator
from sklegal_migration import (
    APPROVAL_STATE,
    EXECUTION_STATE,
    ApprovedImportResult,
    InMemoryPilotImportStore,
    MappingApproval,
    PilotDryRunReport,
    PilotImportPlan,
)
from sklegal_retrieval import (
    CorpusHealthStatus,
    CorpusHealthView,
    CorpusReconciliationReport,
    CorpusRegistrySnapshot,
)

from .workspace import InMemoryWorkspaceReadStore, MatterWorkspaceRead

ARTIFACT_NAME = "SKL-S5-01C pilot verification suite"

_NEGATIVE_STATE_VALUES = frozenset({APPROVAL_STATE, EXECUTION_STATE})
_CANONICAL_TARGET_TYPES = frozenset({"matter", "matter_event"})


class _EvidenceSection(BaseModel):
    """Frozen evidence base; every boolean field is one acceptance check."""

    model_config = ConfigDict(frozen=True)


class SourcePreservationEvidence(_EvidenceSection):
    """Pilot TDD source preservation checks over the dry-run pins."""

    inventoried_files: int
    plan_source_files: int
    changed: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    mtime_changed: tuple[str, ...] = ()
    zero_source_changes: bool
    content_hashes_unchanged: bool
    plan_pins_cover_inventory: bool
    no_write_operations: bool


class TerminologyEvidence(_EvidenceSection):
    """Legal terminology and legacy provenance alias checks."""

    matter_legacy_alias: str | None
    matter_event_legacy_aliases: tuple[str, ...]
    target_types: tuple[str, ...]
    legacy_storage_labels: tuple[str, ...]
    problem_rendered_as_matter: bool
    incident_rendered_as_transaction_review: bool
    legacy_storage_labels_absent_from_ui: bool
    canonical_types_present_in_ui: bool
    legacy_aliases_unique_in_scope: bool


class TensionEvidence(_EvidenceSection):
    """Tension preservation checks; unresolved groups stay visible."""

    tension_groups: int
    assertions_in_tensions: int
    all_tensions_unresolved: bool
    all_tensions_review_required: bool
    tension_groups_rendered: bool
    assertions_not_harmonized: bool
    assertion_values_preserved: bool


class StateEvidence(_EvidenceSection):
    """Approval and execution state checks; no state may advance."""

    approval_value: str
    execution_value: str
    state_rows: int
    imported_targets: int
    approval_states_pending_review: bool
    execution_states_not_started: bool
    no_state_advanced: bool
    every_imported_target_negative: bool
    rendered_states_match_import_store: bool
    human_approval_recorded: bool
    import_gated_on_approval: bool


class ProvenanceEvidence(_EvidenceSection):
    """Version lineage, snapshot pins, sources, gaps, and isolation."""

    source_snapshot: str
    current_source_snapshot: str
    adapter_version: str
    pinned_sources: int
    baseline_packet_version: int | None
    historical_packet_versions: tuple[int, ...] = ()
    gap_kinds: tuple[str, ...] = ()
    version_lineage_rendered: bool
    lineage_sources_pinned: bool
    current_baseline_without_history_loss: bool
    source_snapshot_pinned: bool
    not_stale: bool
    pinned_sources_rendered: bool
    facts_carry_source_references: bool
    missing_source_gaps_rendered: bool
    explicit_gaps_rendered: bool
    member_access_granted: bool
    nonmember_denied: bool
    workspace_read_back_identical: bool


class CorpusCoverageEvidence(_EvidenceSection):
    """SKL-S2-05 registry coverage checks for the pinned snapshot."""

    release_id: str
    registry_source_count: int | None
    pinned_source_count: int
    health_status: str
    health_budget_ms: int
    registry_entry_present: bool
    registry_counts_match_pinned_corpus: bool
    registry_reconciled_at_recorded: bool
    deep_reconciliation_complete: bool
    deep_reconciliation_covered_releases: bool
    deep_reconciliation_no_discrepancies: bool
    bounded_health_available: bool
    bounded_health_counts_consistent: bool


class ReplayEvidence(_EvidenceSection):
    """Deterministic replay comparison against one earlier suite run.

    Fields stay ``None`` until a replay is performed; ``None`` checks
    are not collected so an unreplayed suite does not count as failed.
    """

    original_run_id: str | None = None
    original_fingerprint: str | None = None
    replayed_fingerprint: str | None = None
    replay_run_id_matches: bool | None = None
    replay_check_set_matches: bool | None = None
    replay_fingerprints_match: bool | None = None
    replay_import_batch_matches: bool | None = None
    replays_deterministic: bool | None = None


class VerificationCounts(BaseModel):
    """Content-free size evidence for the rendered pilot workspace."""

    model_config = ConfigDict(frozen=True)

    import_records: int
    fact_assertions: int
    tension_groups: int
    timeline_events: int
    source_files: int
    communications: int
    version_lineage: int
    execution_states: int
    record_gaps: int
    audit_entries: int


class PilotVerificationSuite(BaseModel):
    """Complete machine-readable pilot verification evidence."""

    model_config = ConfigDict(frozen=True)

    artifact: str
    run_id: str
    generated_at: str
    replay_performed: bool
    source_snapshot: str
    tenant_id: str
    import_batch_id: str
    adapter_version: str
    mapping_review: dict[str, str]
    counts: VerificationCounts
    source_preservation: SourcePreservationEvidence
    terminology: TerminologyEvidence
    tensions: TensionEvidence
    states: StateEvidence
    provenance: ProvenanceEvidence
    corpus_coverage: CorpusCoverageEvidence
    replay: ReplayEvidence
    checks: dict[str, bool]
    passed: bool

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        collected = _collect_checks(
            (
                self.source_preservation,
                self.terminology,
                self.tensions,
                self.states,
                self.provenance,
                self.corpus_coverage,
                self.replay,
            )
        )
        if collected != self.checks:
            raise ValueError("suite checks disagree with the evidence sections")
        if self.passed != all(self.checks.values()):
            raise ValueError("suite pass flag disagrees with its checks")
        if not self.checks:
            raise ValueError("suite must record at least one check")
        return self

    def evidence_json(self) -> str:
        """Render the suite as sorted, pretty-printed evidence JSON."""

        return json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def _collect_checks(sections: Iterable[Any]) -> dict[str, bool]:
    """Collect one typed boolean per evidence field, keyed by section."""

    checks: dict[str, bool] = {}
    for section in sections:
        for name, value in section:
            if isinstance(value, bool):
                key = f"{type(section).__name__.removesuffix('Evidence')}.{name}"
                if not key[0].islower():
                    key = key[0].lower() + key[1:]
                checks[key] = value
    return checks


def verification_fingerprint(suite: PilotVerificationSuite) -> str:
    """Fingerprint of one suite's replayable evidence content.

    Excludes the replay comparison, the collected checks, the pass flag,
    and the wall-clock generation time so two independently built suites
    over identical pinned inputs must produce identical fingerprints.
    """

    payload = suite.model_dump(
        mode="json",
        exclude={"replay", "checks", "passed", "replay_performed", "generated_at"},
    )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _view_fingerprint(view: MatterWorkspaceRead) -> str:
    payload = view.model_dump(mode="json", by_alias=True)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _pinned_pairs(view: MatterWorkspaceRead) -> set[tuple[str, str]]:
    return {
        (item.relative_path, item.content_sha256)
        for item in view.provenance.source_files
    }


def _plan_pairs(report: PilotDryRunReport) -> set[tuple[str, str]]:
    return {
        (item.relative_path, item.content_sha256) for item in report.plan.source_files
    }


def _inventory_pairs(report: PilotDryRunReport) -> set[tuple[str, str]]:
    return {
        (entry.relative_path, entry.content_sha256) for entry in report.pre_inventory
    }


def run_pilot_verification(
    *,
    report: PilotDryRunReport,
    approval: MappingApproval,
    import_result: ApprovedImportResult,
    import_store: InMemoryPilotImportStore,
    workspace_view: MatterWorkspaceRead,
    workspace_store: InMemoryWorkspaceReadStore,
    tenant_id: UUID,
    member_principal_ids: Iterable[UUID],
    release_id: str,
    registry: CorpusRegistrySnapshot,
    reconciliation: CorpusReconciliationReport,
    health: CorpusHealthView,
    replay: PilotVerificationSuite | None = None,
    observed_at: datetime | None = None,
) -> PilotVerificationSuite:
    """Verify one rendered pilot matter and its corpus registry coverage.

    Reads only: the dry-run report, the approved import store, the
    rendered workspace view, and the SKL-S2-05 registry state. When
    ``replay`` carries an earlier suite built over the same pinned
    inputs, the replay section compares both runs for determinism.
    """

    plan = report.plan
    view = workspace_view
    generated_at = (observed_at or datetime.now(UTC)).isoformat()
    members = tuple(member_principal_ids)
    nonmember = uuid5(NAMESPACE_URL, "pilot-verification-nonmember")

    proof = report.change_proof
    pre_pairs = _inventory_pairs(report)
    post_pairs = {
        (entry.relative_path, entry.content_sha256) for entry in report.post_inventory
    }
    source_preservation = SourcePreservationEvidence(
        inventoried_files=len(report.pre_inventory),
        plan_source_files=len(plan.source_files),
        changed=proof.changed,
        added=proof.added,
        removed=proof.removed,
        mtime_changed=proof.mtime_changed,
        zero_source_changes=proof.zero_source_changes,
        content_hashes_unchanged=pre_pairs == post_pairs,
        plan_pins_cover_inventory=(
            pre_pairs <= _plan_pairs(report)
            and not report.reconciliation.get("excluded_from_plan")
        ),
        no_write_operations=not plan.hammer_time_mutation,
    )

    ui_dump = json.dumps(view.model_dump(mode="json", by_alias=True))
    matter_records = [item for item in plan.records if item.target_type == "matter"]
    event_records = [
        item for item in plan.records if item.target_type == "matter_event"
    ]
    legacy_labels = tuple(
        sorted({item.mapping_rule.split(".", 1)[0] for item in plan.records})
    )
    event_aliases = tuple(
        alias for event in view.timeline for alias in event.legacy_aliases
    )
    all_aliases = tuple(view.matter.legacy_aliases) + event_aliases
    terminology = TerminologyEvidence(
        matter_legacy_alias=report.problem_legacy_id,
        matter_event_legacy_aliases=event_aliases,
        target_types=tuple(sorted({item.target_type for item in plan.records})),
        legacy_storage_labels=legacy_labels,
        problem_rendered_as_matter=(
            len(matter_records) == 1
            and view.matter.legacy_aliases == (report.problem_legacy_id,)
        ),
        incident_rendered_as_transaction_review=(
            len(event_records) == 1
            and all(event.event_type == "transaction_review" for event in view.timeline)
            and event_aliases == (report.incident_legacy_id,)
        ),
        legacy_storage_labels_absent_from_ui=all(
            f"{label}." not in ui_dump for label in legacy_labels
        ),
        canonical_types_present_in_ui=(
            '"matter"' in ui_dump and '"matter_event"' in ui_dump
        ),
        legacy_aliases_unique_in_scope=(
            len(all_aliases) == len(set(all_aliases)) and len(all_aliases) > 0
        ),
    )

    view_facts_by_id = {str(fact.fact_assertion_id): fact for fact in view.facts}
    plan_facts_by_id = {item.fact_assertion_id: item for item in plan.facts}
    tension_keys = {tension.tension_key for tension in view.tensions}
    grouped_keys = {
        fact.tension_group_key
        for fact in view.facts
        if fact.tension_group_key is not None
    }
    plan_groups = {
        tension.key: frozenset(tension.assertion_ids) for tension in plan.tensions
    }
    view_groups = {
        tension.tension_key: frozenset(
            str(identifier) for identifier in tension.assertion_ids
        )
        for tension in view.tensions
    }
    tensions = TensionEvidence(
        tension_groups=len(view.tensions),
        assertions_in_tensions=sum(
            len(tension.assertion_ids) for tension in view.tensions
        ),
        all_tensions_unresolved=all(
            tension.status == "unresolved" for tension in view.tensions
        )
        and len(view.tensions) > 0,
        all_tensions_review_required=all(
            tension.review_required for tension in view.tensions
        ),
        tension_groups_rendered=(
            tension_keys == {tension.key for tension in plan.tensions}
            and grouped_keys == tension_keys
        ),
        assertions_not_harmonized=(
            plan_groups == view_groups
            and all(len(tension.assertion_ids) >= 2 for tension in view.tensions)
            and all(
                identifier in view_facts_by_id
                for group in view_groups.values()
                for identifier in group
            )
            and len(grouped_keys) > 0
        ),
        assertion_values_preserved=all(
            identifier in view_facts_by_id
            and json.dumps(
                view_facts_by_id[identifier].asserted_value,
                sort_keys=True,
                default=str,
            )
            == json.dumps(fact.value, sort_keys=True, default=str)
            and view_facts_by_id[identifier].predicate == fact.predicate
            for identifier, fact in plan_facts_by_id.items()
        )
        and len(view_facts_by_id) == len(plan_facts_by_id),
    )

    rendered_states = {
        (str(state.target_id), state.state_kind, state.state_value)
        for state in view.execution_states
    }
    store_states = {
        (str(row.target_id), row.state_kind, row.state_value)
        for row in import_store.states.values()
        if row.batch_id == plan.import_batch_id
    }
    states_by_target: dict[str, dict[str, str]] = {}
    for state in view.execution_states:
        states_by_target.setdefault(str(state.target_id), {})[state.state_kind] = (
            state.state_value
        )
    batch = import_store.batches.get(plan.import_batch_id)
    state_evidence = StateEvidence(
        approval_value=APPROVAL_STATE,
        execution_value=EXECUTION_STATE,
        state_rows=len(view.execution_states),
        imported_targets=len(plan.records),
        approval_states_pending_review=all(
            state.state_kind != "approval" or state.state_value == APPROVAL_STATE
            for state in view.execution_states
        ),
        execution_states_not_started=all(
            state.state_kind != "execution" or state.state_value == EXECUTION_STATE
            for state in view.execution_states
        ),
        no_state_advanced=all(
            state.state_value in _NEGATIVE_STATE_VALUES
            for state in view.execution_states
        ),
        every_imported_target_negative=all(
            states_by_target.get(item.target_id, {}).get("approval") == APPROVAL_STATE
            and states_by_target.get(item.target_id, {}).get("execution")
            == EXECUTION_STATE
            for item in plan.records
        ),
        rendered_states_match_import_store=rendered_states == store_states,
        human_approval_recorded=(
            batch is not None
            and str(batch["approved_by"]) == approval.reviewer
            and str(batch["review_artifact"]) == approval.review_artifact
            and str(batch["status"]) == "imported"
        ),
        import_gated_on_approval=(
            approval.decision == "approved"
            and import_result.import_batch_id == plan.import_batch_id
            and (import_result.batch_created or import_result.rerun)
            and str(import_result.tenant_id) == str(tenant_id)
        ),
    )

    plan_lineage = {
        (item.source_path, item.source_sha256, item.packet_version): item
        for item in plan.version_lineage
    }
    view_lineage = {
        (item.source_path, item.source_sha256, item.packet_version): item
        for item in view.version_lineage
    }
    baseline_versions = tuple(
        item.packet_version
        for item in view.version_lineage
        if item.current_review_baseline
    )
    historical_versions = tuple(
        item.packet_version for item in view.version_lineage if item.historical
    )
    missing_source_gaps = sum(1 for gap in view.gaps if gap.kind == "missing_source")
    missing_source_facts = sum(1 for fact in view.facts if fact.source_missing)
    stored = workspace_store.get_matter_workspace(tenant_id, view.matter.matter_id)
    provenance = ProvenanceEvidence(
        source_snapshot=plan.source_snapshot,
        current_source_snapshot=view.provenance.current_source_snapshot or "",
        adapter_version=plan.adapter_version,
        pinned_sources=len(plan.source_files),
        baseline_packet_version=baseline_versions[0] if baseline_versions else None,
        historical_packet_versions=historical_versions,
        gap_kinds=tuple(sorted({gap.kind for gap in view.gaps})),
        version_lineage_rendered=tuple(
            sorted(item.packet_version for item in view.version_lineage)
        )
        == tuple(sorted(item.packet_version for item in plan.version_lineage)),
        lineage_sources_pinned=set(plan_lineage) == set(view_lineage)
        and all(
            view_lineage[key].historical == (not item.current_review_baseline)
            for key, item in plan_lineage.items()
        ),
        current_baseline_without_history_loss=(
            len(baseline_versions) == 1
            and len(view.version_lineage) == len(plan.version_lineage)
            and baseline_versions[0]
            == max(item.packet_version for item in view.version_lineage)
            and len(historical_versions) == len(view.version_lineage) - 1
        )
        if view.version_lineage
        else not plan.version_lineage,
        source_snapshot_pinned=(
            view.provenance.source_snapshot == plan.source_snapshot
            and view.provenance.adapter_version == plan.adapter_version
        ),
        not_stale=not view.provenance.stale,
        pinned_sources_rendered=_pinned_pairs(view) == _plan_pairs(report),
        facts_carry_source_references=all(
            fact.source_path is not None or fact.source_missing for fact in view.facts
        ),
        missing_source_gaps_rendered=missing_source_gaps == missing_source_facts,
        explicit_gaps_rendered=len(view.gaps) > 0,
        member_access_granted=all(
            workspace_store.is_matter_member(tenant_id, view.matter.matter_id, member)
            for member in members
        )
        and len(members) > 0,
        nonmember_denied=not workspace_store.is_matter_member(
            tenant_id, view.matter.matter_id, nonmember
        ),
        workspace_read_back_identical=(
            stored is not None and _view_fingerprint(stored) == _view_fingerprint(view)
        ),
    )

    pinned_count = len(
        {(item.relative_path, item.content_sha256) for item in plan.source_files}
    )
    entry = registry.entry_for(tenant_id=tenant_id, release_id=release_id)
    corpus_coverage = CorpusCoverageEvidence(
        release_id=release_id,
        registry_source_count=entry.counts.source if entry is not None else None,
        pinned_source_count=pinned_count,
        health_status=health.status.value,
        health_budget_ms=health.budget_ms,
        registry_entry_present=entry is not None,
        registry_counts_match_pinned_corpus=(
            entry is not None and entry.counts.source == pinned_count
        ),
        registry_reconciled_at_recorded=(
            entry is not None and entry.last_reconciled_at is not None
        ),
        deep_reconciliation_complete=reconciliation.complete,
        deep_reconciliation_covered_releases=reconciliation.releases_reconciled >= 1,
        deep_reconciliation_no_discrepancies=not reconciliation.discrepancies,
        bounded_health_available=(
            health.status in (CorpusHealthStatus.HEALTHY, CorpusHealthStatus.DEGRADED)
        ),
        bounded_health_counts_consistent=(
            health.counts is not None and health.counts.source == pinned_count
        ),
    )

    base = _build_suite(
        report=report,
        approval=approval,
        import_result=import_result,
        view=view,
        tenant_id=tenant_id,
        generated_at=generated_at,
        counts=_counts(report, view),
        source_preservation=source_preservation,
        terminology=terminology,
        tensions=tensions,
        states=state_evidence,
        provenance=provenance,
        corpus_coverage=corpus_coverage,
    )

    if replay is None:
        return base

    original_free = _replay_free_checks(replay)
    own_free = _replay_free_checks(base)
    original = verification_fingerprint(replay)
    own = verification_fingerprint(base)
    run_id_matches = replay.run_id == base.run_id
    # The recorded suite must have run the identical check battery and
    # passed every replay-free check of its own; a divergent or failing
    # original cannot anchor a deterministic replay.
    check_set_matches = set(original_free) == set(own_free) and all(
        original_free.values()
    )
    batch_matches = replay.import_batch_id == plan.import_batch_id
    fingerprints_match = own == original
    replay_evidence = ReplayEvidence(
        original_run_id=replay.run_id,
        original_fingerprint=original,
        replayed_fingerprint=own,
        replay_run_id_matches=run_id_matches,
        replay_check_set_matches=check_set_matches,
        replay_fingerprints_match=fingerprints_match,
        replay_import_batch_matches=batch_matches,
        replays_deterministic=all(
            (
                run_id_matches,
                check_set_matches,
                fingerprints_match,
                batch_matches,
            )
        ),
    )

    return _build_suite(
        report=report,
        approval=approval,
        import_result=import_result,
        view=view,
        tenant_id=tenant_id,
        generated_at=generated_at,
        counts=_counts(report, view),
        source_preservation=source_preservation,
        terminology=terminology,
        tensions=tensions,
        states=state_evidence,
        provenance=provenance,
        corpus_coverage=corpus_coverage,
        replay_evidence=replay_evidence,
    )


def _run_id(plan: PilotImportPlan) -> str:
    return str(uuid5(NAMESPACE_URL, f"pilot-verification:{plan.import_batch_id}"))


def _counts(report: PilotDryRunReport, view: MatterWorkspaceRead) -> VerificationCounts:
    return VerificationCounts(
        import_records=len(report.plan.records),
        fact_assertions=len(view.facts),
        tension_groups=len(view.tensions),
        timeline_events=len(view.timeline),
        source_files=len(report.plan.source_files),
        communications=len(view.communications),
        version_lineage=len(view.version_lineage),
        execution_states=len(view.execution_states),
        record_gaps=len(view.gaps),
        audit_entries=len(view.audit),
    )


def _replay_free_checks(suite: PilotVerificationSuite) -> dict[str, bool]:
    return {
        key: value
        for key, value in suite.checks.items()
        if not key.startswith("replay.")
    }


def _build_suite(
    *,
    report: PilotDryRunReport,
    approval: MappingApproval,
    import_result: ApprovedImportResult,
    view: MatterWorkspaceRead,
    tenant_id: UUID,
    generated_at: str,
    counts: VerificationCounts,
    source_preservation: SourcePreservationEvidence,
    terminology: TerminologyEvidence,
    tensions: TensionEvidence,
    states: StateEvidence,
    provenance: ProvenanceEvidence,
    corpus_coverage: CorpusCoverageEvidence,
    replay_evidence: ReplayEvidence | None = None,
) -> PilotVerificationSuite:
    plan = report.plan
    replay_section = replay_evidence or ReplayEvidence()
    sections = (
        source_preservation,
        terminology,
        tensions,
        states,
        provenance,
        corpus_coverage,
        replay_section,
    )
    checks = _collect_checks(sections)
    return PilotVerificationSuite(
        artifact=ARTIFACT_NAME,
        run_id=_run_id(plan),
        generated_at=generated_at,
        replay_performed=replay_evidence is not None,
        source_snapshot=plan.source_snapshot,
        tenant_id=str(tenant_id),
        import_batch_id=plan.import_batch_id,
        adapter_version=plan.adapter_version,
        mapping_review={
            "reviewer": approval.reviewer,
            "decided_at": approval.decided_at,
            "decision": approval.decision,
            "review_artifact": approval.review_artifact,
            "import_batch_id": import_result.import_batch_id,
        },
        counts=counts,
        source_preservation=source_preservation,
        terminology=terminology,
        tensions=tensions,
        states=states,
        provenance=provenance,
        corpus_coverage=corpus_coverage,
        replay=replay_section,
        checks=checks,
        passed=all(checks.values()),
    )


__all__ = [
    "ARTIFACT_NAME",
    "PilotVerificationSuite",
    "ReplayEvidence",
    "run_pilot_verification",
    "verification_fingerprint",
]
