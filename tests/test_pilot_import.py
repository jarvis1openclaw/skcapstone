"""Unit tests for the SKL-S5-01B human-approved pilot import.

All fixtures are synthetic and built in a temporary directory by the
shared HammerTime fixture builder. Imports run against the in-memory
pilot store; no real matter content, real legacy identifiers, or
HammerTime paths are used.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sklegal_hammertime import HammerTimeReleaseAdapter, MatterAccessRequest
from sklegal_migration import (
    APPROVAL_STATE,
    EXECUTION_STATE,
    ImportGateError,
    InMemoryPilotImportStore,
    MappingApproval,
    PilotImportPlan,
    SourceInventoryEntry,
    reset_disposable_batch,
    run_approved_import,
    run_pilot_dry_run,
    state_idempotency_key,
    withdraw_batch,
)

from tests.support import hammertime_fixture as fixture

FIXED_NOW = datetime(2099, 1, 2, 3, 4, 5, tzinfo=UTC)
SNAPSHOT = "fixture-snapshot-2099-01-02"
TENANT_ID = "a5b70000-0000-4000-8000-000000000101"
REVIEWER = "Synthetic Human Reviewer"
REVIEW_ARTIFACT = "SKL-S5-01A-PILOT-MAPPING-REVIEW-2099-01-02.md#approved"


def allow_all(request: MatterAccessRequest) -> bool:
    return True


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return fixture.build_hammertime_fixture(tmp_path)


@pytest.fixture
def adapter(tree: Path) -> HammerTimeReleaseAdapter:
    return HammerTimeReleaseAdapter(
        root=tree,
        matter_authorizer=allow_all,
        clock=lambda: FIXED_NOW,
    )


def _report(adapter: HammerTimeReleaseAdapter):
    return run_pilot_dry_run(
        adapter,
        source_snapshot=SNAPSHOT,
        problem_id=fixture.PROBLEM_ID,
        incident_id=fixture.INCIDENT_ID,
    )


def approve(plan: PilotImportPlan, **overrides: object) -> MappingApproval:
    """Synthetic approved-review fixture covering the exact plan keys."""
    values: dict[str, object] = {
        "import_batch_id": plan.import_batch_id,
        "reviewer": REVIEWER,
        "decided_at": "2099-01-03T00:00:00Z",
        "decision": "approved",
        "approved_idempotency_keys": tuple(
            record.idempotency_key for record in plan.records
        ),
        "review_artifact": REVIEW_ARTIFACT,
    }
    values.update(overrides)
    return MappingApproval(**values)  # type: ignore[arg-type]


def test_import_fails_closed_without_any_approval(adapter) -> None:
    report = _report(adapter)
    store = InMemoryPilotImportStore()

    with pytest.raises(ImportGateError, match="requires an explicit human"):
        run_approved_import(
            report.plan,
            approval=None,
            inventory=report.pre_inventory,
            store=store,
            tenant_id=TENANT_ID,
        )

    assert store.counts()["batches"] == 0


def test_import_fails_closed_on_unapproved_or_mismatched_review(adapter) -> None:
    report = _report(adapter)
    plan = report.plan

    rejected = approve(plan, decision="rejected")
    with pytest.raises(ImportGateError, match="not 'approved'"):
        run_approved_import(
            plan,
            approval=rejected,
            inventory=report.pre_inventory,
            store=InMemoryPilotImportStore(),
            tenant_id=TENANT_ID,
        )

    wrong_batch = approve(plan, import_batch_id="9" * 8 + plan.import_batch_id[8:])
    with pytest.raises(ImportGateError, match="does not match"):
        run_approved_import(
            plan,
            approval=wrong_batch,
            inventory=report.pre_inventory,
            store=InMemoryPilotImportStore(),
            tenant_id=TENANT_ID,
        )

    partial = approve(
        plan,
        approved_idempotency_keys=(plan.records[0].idempotency_key,),
    )
    with pytest.raises(ImportGateError, match="does not exactly cover"):
        run_approved_import(
            plan,
            approval=partial,
            inventory=report.pre_inventory,
            store=InMemoryPilotImportStore(),
            tenant_id=TENANT_ID,
        )

    blank_reviewer = approve(plan, reviewer="  ")
    with pytest.raises(ImportGateError, match="reviewer"):
        run_approved_import(
            plan,
            approval=blank_reviewer,
            inventory=report.pre_inventory,
            store=InMemoryPilotImportStore(),
            tenant_id=TENANT_ID,
        )


def test_import_fails_closed_on_inventory_mismatch(adapter) -> None:
    report = _report(adapter)
    plan = report.plan

    extra = SourceInventoryEntry(
        relative_path=f"{fixture.INCIDENT_DIR_RELATIVE}/UNPINNED-EXTRA.md",
        content_sha256="f" * 64,
        byte_count=1,
        modified_at="2099-01-02T00:00:00+00:00",
        observed_at="2099-01-02T00:00:00+00:00",
    )
    with pytest.raises(ImportGateError, match="missing_from_plan"):
        run_approved_import(
            plan,
            approval=approve(plan),
            inventory=(*report.pre_inventory, extra),
            store=InMemoryPilotImportStore(),
            tenant_id=TENANT_ID,
        )

    corrupted = tuple(
        entry.__class__(
            relative_path=entry.relative_path,
            content_sha256="0" * 64,
            byte_count=entry.byte_count,
            modified_at=entry.modified_at,
            observed_at=entry.observed_at,
        )
        if index == 0
        else entry
        for index, entry in enumerate(report.pre_inventory)
    )
    with pytest.raises(ImportGateError, match="hash_mismatches"):
        run_approved_import(
            plan,
            approval=approve(plan),
            inventory=corrupted,
            store=InMemoryPilotImportStore(),
            tenant_id=TENANT_ID,
        )


def test_import_fails_closed_on_invalid_tenant(adapter) -> None:
    report = _report(adapter)
    with pytest.raises(ImportGateError, match="tenant_id"):
        run_approved_import(
            report.plan,
            approval=approve(report.plan),
            inventory=report.pre_inventory,
            store=InMemoryPilotImportStore(),
            tenant_id="not-a-uuid",
        )
    with pytest.raises(ImportGateError, match="nil UUID"):
        run_approved_import(
            report.plan,
            approval=approve(report.plan),
            inventory=report.pre_inventory,
            store=InMemoryPilotImportStore(),
            tenant_id="00000000-0000-0000-0000-000000000000",
        )


def test_approved_import_writes_proposals_and_negative_states(adapter) -> None:
    report = _report(adapter)
    plan = report.plan
    store = InMemoryPilotImportStore()

    result = run_approved_import(
        plan,
        approval=approve(plan),
        inventory=report.pre_inventory,
        store=store,
        tenant_id=TENANT_ID,
    )

    assert result.batch_created is True
    assert result.rerun is False
    assert result.records_created == len(plan.records) == 2
    assert result.records_suppressed == 0
    assert result.facts_created == len(plan.facts)
    assert result.tensions_created == len(plan.tensions)
    assert result.tensions_created > 0
    assert result.states_created == 2 * len(plan.records)
    assert result.source_files_recorded == len(plan.source_files)
    assert result.reconciliation["inventoried_files"] == len(report.pre_inventory)
    assert result.reconciliation["missing_from_plan"] == []
    assert result.reconciliation["hash_mismatches"] == []
    assert result.reconciliation["approval_states_advanced"] == 0
    assert result.reconciliation["execution_states_advanced"] == 0

    for row in store.records.values():
        assert row["mapping_status"] == "proposed"
        assert row["reviewed_by"] == REVIEWER
        assert row["revision"] == 1
        assert row["tenant_id"] == TENANT_ID
    batch = store.batches[plan.import_batch_id]
    assert batch["status"] == "imported"
    assert batch["approved_by"] == REVIEWER
    assert batch["review_artifact"] == REVIEW_ARTIFACT

    for state in store.states.values():
        assert (state.state_kind, state.state_value) in (
            ("approval", APPROVAL_STATE),
            ("execution", EXECUTION_STATE),
        )
        assert state.idempotency_key == state_idempotency_key(
            state.target_type, state.target_id, state.state_kind
        )

    for tension in store.tensions.values():
        assert tension.status == "unresolved"
        assert tension.review_required is True
        for assertion_id in tension.assertion_ids:
            assert assertion_id in store.facts


def test_rerun_creates_no_duplicates(adapter) -> None:
    report = _report(adapter)
    plan = report.plan
    store = InMemoryPilotImportStore()
    first = run_approved_import(
        plan,
        approval=approve(plan),
        inventory=report.pre_inventory,
        store=store,
        tenant_id=TENANT_ID,
    )
    baseline = dict(store.counts())

    second = run_approved_import(
        plan,
        approval=approve(plan),
        inventory=report.pre_inventory,
        store=store,
        tenant_id=TENANT_ID,
    )

    assert second.rerun is True
    assert second.batch_created is False
    assert second.records_created == 0
    assert second.records_suppressed == len(plan.records)
    assert second.facts_created == 0
    assert second.facts_suppressed == len(plan.facts)
    assert second.tensions_created == 0
    assert second.tensions_suppressed == len(plan.tensions)
    assert second.states_created == 0
    assert second.states_suppressed == 2 * len(plan.records)
    assert second.source_files_recorded == 0
    assert second.source_files_suppressed == len(plan.source_files)
    assert dict(store.counts()) == baseline
    assert first.import_batch_id == second.import_batch_id


def test_changed_source_creates_revision_without_duplicates(adapter, tree) -> None:
    first_report = _report(adapter)
    first_plan = first_report.plan
    store = InMemoryPilotImportStore()
    run_approved_import(
        first_plan,
        approval=approve(first_plan),
        inventory=first_report.pre_inventory,
        store=store,
        tenant_id=TENANT_ID,
    )
    baseline_facts = dict(store.facts)

    incident_path = tree / fixture.INCIDENT_RELATIVE
    incident_path.write_text(
        incident_path.read_text(encoding="utf-8") + "Additional synthetic note.\n",
        encoding="utf-8",
    )
    second_report = _report(adapter)
    second_plan = second_report.plan
    assert second_plan.import_batch_id != first_plan.import_batch_id

    result = run_approved_import(
        second_plan,
        approval=approve(second_plan),
        inventory=second_report.pre_inventory,
        store=store,
        tenant_id=TENANT_ID,
    )

    assert result.batch_created is True
    assert result.rerun is False
    by_rule = {row["mapping_rule"]: row for row in store.records.values()}
    matter = by_rule["problem.matter@1"]
    incident_rows = [
        row
        for row in store.records.values()
        if row["mapping_rule"] == "incident.transaction_review@1"
    ]
    assert matter["revision"] == 1
    assert len(incident_rows) == 2
    assert sorted(row["revision"] for row in incident_rows) == [1, 2]
    assert {row["target_id"] for row in incident_rows} == {
        second_plan.records[1].target_id
    }
    assert result.records_created == 1
    assert result.records_suppressed == 1
    # Unchanged matter facts are suppressed; changed incident facts are new.
    assert result.facts_created > 0
    assert result.facts_suppressed > 0
    assert len(store.facts) > len(baseline_facts)
    for fact_id, row in baseline_facts.items():
        assert store.facts[fact_id] == row
    # Negative states are keyed by target, so the revision does not duplicate them.
    assert result.states_created == 0
    assert result.states_suppressed == 2 * len(second_plan.records)
    # Tension groups stay visible for both batches, never harmonized.
    tension_keys = {tension.key for tension in store.tensions.values()}
    assert tension_keys == {tension.key for tension in first_plan.tensions}
    assert all(tension.status == "unresolved" for tension in store.tensions.values())


def test_tensions_remain_visible_after_import_and_rerun(adapter) -> None:
    report = _report(adapter)
    plan = report.plan
    assert len(plan.tensions) > 0
    store = InMemoryPilotImportStore()
    for _ in range(2):
        run_approved_import(
            plan,
            approval=approve(plan),
            inventory=report.pre_inventory,
            store=store,
            tenant_id=TENANT_ID,
        )

    assert len(store.tensions) == len(plan.tensions)
    for (key, batch_id), tension in store.tensions.items():
        assert batch_id == plan.import_batch_id
        assert tension.status == "unresolved"
        assert tension.review_required is True
        assert len(tension.assertion_ids) >= 2


def test_withdraw_blocks_reimport_and_target_reuse(adapter) -> None:
    report = _report(adapter)
    plan = report.plan
    store = InMemoryPilotImportStore()
    run_approved_import(
        plan,
        approval=approve(plan),
        inventory=report.pre_inventory,
        store=store,
        tenant_id=TENANT_ID,
    )

    assert (
        withdraw_batch(store, plan.import_batch_id, withdrawn_at="2099-01-04T00:00:00Z")
        is True
    )
    assert withdraw_batch(store, plan.import_batch_id) is False
    assert store.batch_status(plan.import_batch_id) == "withdrawn"
    assert store.counts()["withdrawn_targets"] == len(plan.records)

    with pytest.raises(ImportGateError, match="withdrawn"):
        run_approved_import(
            plan,
            approval=approve(plan),
            inventory=report.pre_inventory,
            store=store,
            tenant_id=TENANT_ID,
        )

    # Rows are preserved after withdrawal: mappings and human decisions survive.
    assert store.counts()["records"] == len(plan.records)
    assert store.counts()["facts"] == len(plan.facts)


def test_disposable_reset_requires_disposable_withdrawn_batch(adapter) -> None:
    report = _report(adapter)
    plan = report.plan

    guarded = InMemoryPilotImportStore(disposable=False)
    run_approved_import(
        plan,
        approval=approve(plan),
        inventory=report.pre_inventory,
        store=guarded,
        tenant_id=TENANT_ID,
    )
    with pytest.raises(ImportGateError, match="disposable"):
        reset_disposable_batch(guarded, plan.import_batch_id)

    store = InMemoryPilotImportStore()
    run_approved_import(
        plan,
        approval=approve(plan),
        inventory=report.pre_inventory,
        store=store,
        tenant_id=TENANT_ID,
    )
    with pytest.raises(ImportGateError, match="withdrawn"):
        reset_disposable_batch(store, plan.import_batch_id)

    assert withdraw_batch(store, plan.import_batch_id) is True
    deleted = reset_disposable_batch(store, plan.import_batch_id)

    assert deleted["records"] == len(plan.records)
    assert deleted["facts"] == len(plan.facts)
    assert deleted["tension_groups"] == len(plan.tensions)
    assert deleted["states"] == 2 * len(plan.records)
    assert deleted["source_files"] == len(plan.source_files)
    counts = store.counts()
    assert counts["records"] == 0
    assert counts["facts"] == 0
    assert counts["tension_groups"] == 0
    assert counts["states"] == 0
    assert counts["source_files"] == 0
    # Tombstone and withdrawn-target pins survive the disposable reset.
    assert counts["batches"] == 1
    assert counts["withdrawn_targets"] == len(plan.records)
    assert store.batch_status(plan.import_batch_id) == "withdrawn"

    with pytest.raises(ImportGateError, match="withdrawn"):
        run_approved_import(
            plan,
            approval=approve(plan),
            inventory=report.pre_inventory,
            store=store,
            tenant_id=TENANT_ID,
        )


def test_import_result_json_is_dash_clean_and_complete(adapter) -> None:
    report = _report(adapter)
    plan = report.plan
    store = InMemoryPilotImportStore()
    result = run_approved_import(
        plan,
        approval=approve(plan),
        inventory=report.pre_inventory,
        store=store,
        tenant_id=TENANT_ID,
    )
    payload = result.json()

    assert "\u2013" not in payload
    assert "\u2014" not in payload
    assert result.to_dict()["reconciliation"]["inventoried_files"] == len(
        report.pre_inventory
    )


def test_replay_script_produces_consistent_dash_clean_evidence(tmp_path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "pilot_import_replay.py"
    spec = importlib.util.spec_from_file_location("pilot_import_replay", script)
    assert spec and spec.loader
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)

    evidence = replay.run_replay()

    assert all(evidence["invariants"].values())
    assert evidence["invariants"]["rerun_created_no_duplicates"] is True
    assert evidence["invariants"]["changed_source_created_new_revision"] is True
    assert evidence["invariants"]["negative_states_only"] is True
    assert evidence["invariants"]["rollback_removed_withdrawn_batch_rows"] is True
    assert evidence["zero_source_changes"] is True
    payload = json.dumps(evidence, indent=2, sort_keys=True)
    assert "\u2013" not in payload
    assert "\u2014" not in payload
