"""Pilot verification suite tests: render, coverage, and replay evidence.

Assembles the full SKL-S5-01C stack over the synthetic HammerTime fixture:
the S5-01A dry run, the S5-01B approved import, the S4-02 workspace
projection, and the S2-05 corpus registry built through the real
projector, reconciler, and bounded health reader. The suite must pass
every acceptance check on the healthy stack and fail closed on every
tampered input: an advanced execution state, a harmonized tension, a
registry mismatch, an unreconciled corpus, a stale render, and a replay
against changed pinned inputs. No real matter content, Tenant, release,
or HammerTime path is used.
"""

from __future__ import annotations

import dataclasses
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from pydantic import ValidationError
from sklegal_api.pilot_verification import (
    PilotVerificationSuite,
    run_pilot_verification,
    verification_fingerprint,
)
from sklegal_api.workspace import InMemoryWorkspaceReadStore, MatterWorkspaceRead
from sklegal_api.workspace_pilot import project_pilot_matter_workspace
from sklegal_hammertime import HammerTimeReleaseAdapter, MatterAccessRequest
from sklegal_migration import (
    APPROVAL_STATE,
    EXECUTION_STATE,
    ImportStateRow,
    InMemoryPilotImportStore,
    MappingApproval,
    run_approved_import,
    run_pilot_dry_run,
)
from sklegal_retrieval.corpus_health import BoundedCorpusHealthReader
from sklegal_retrieval.corpus_reconciliation import CorpusReconciliationReport
from sklegal_retrieval.corpus_registry import (
    CorpusCountKind,
    CorpusRegistryEntry,
    CorpusRegistrySnapshot,
    zero_corpus_counts,
)
from sklegal_retrieval.models import ProjectionLag

from tests.support import hammertime_fixture as fixture
from tests.support import pilot_corpus

TENANT_ID = UUID("a5b70000-0000-4000-8000-000000000101")
CLIENT_ID = UUID("a5b70000-0000-4000-8000-000000000202")
MEMBER_ID = UUID("a5b70000-0000-4000-8000-000000000303")
SNAPSHOT = "fixture-snapshot-2099-01-02"
FIXED_NOW = datetime(2099, 1, 2, 3, 4, 5, tzinfo=UTC)
FIRST_RUN_AT = datetime(2099, 1, 5, 7, 0, 0, tzinfo=UTC)
SECOND_RUN_AT = datetime(2099, 1, 6, 8, 30, 0, tzinfo=UTC)
RELEASE_ID = fixture.RELEASE_ID

EXPECTED_CHECK_KEYS = frozenset(
    {
        "sourcePreservation.zero_source_changes",
        "sourcePreservation.content_hashes_unchanged",
        "sourcePreservation.plan_pins_cover_inventory",
        "sourcePreservation.no_write_operations",
        "terminology.problem_rendered_as_matter",
        "terminology.incident_rendered_as_transaction_review",
        "terminology.legacy_storage_labels_absent_from_ui",
        "terminology.canonical_types_present_in_ui",
        "terminology.legacy_aliases_unique_in_scope",
        "tension.all_tensions_unresolved",
        "tension.all_tensions_review_required",
        "tension.tension_groups_rendered",
        "tension.assertions_not_harmonized",
        "tension.assertion_values_preserved",
        "state.approval_states_pending_review",
        "state.execution_states_not_started",
        "state.no_state_advanced",
        "state.every_imported_target_negative",
        "state.rendered_states_match_import_store",
        "state.human_approval_recorded",
        "state.import_gated_on_approval",
        "provenance.version_lineage_rendered",
        "provenance.lineage_sources_pinned",
        "provenance.current_baseline_without_history_loss",
        "provenance.source_snapshot_pinned",
        "provenance.not_stale",
        "provenance.pinned_sources_rendered",
        "provenance.facts_carry_source_references",
        "provenance.missing_source_gaps_rendered",
        "provenance.explicit_gaps_rendered",
        "provenance.member_access_granted",
        "provenance.nonmember_denied",
        "provenance.workspace_read_back_identical",
        "corpusCoverage.registry_entry_present",
        "corpusCoverage.registry_counts_match_pinned_corpus",
        "corpusCoverage.registry_reconciled_at_recorded",
        "corpusCoverage.deep_reconciliation_complete",
        "corpusCoverage.deep_reconciliation_covered_releases",
        "corpusCoverage.deep_reconciliation_no_discrepancies",
        "corpusCoverage.bounded_health_available",
        "corpusCoverage.bounded_health_counts_consistent",
    }
)


def allow_all(request: MatterAccessRequest) -> bool:
    return True


class _StaticRegistryStore:
    """Registry store double pinned to one fixed snapshot."""

    def __init__(self, snapshot: CorpusRegistrySnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> CorpusRegistrySnapshot:
        return self._snapshot


class PilotVerificationSuiteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        tree = fixture.build_hammertime_fixture(Path(self._tmp.name))
        adapter = HammerTimeReleaseAdapter(
            root=tree,
            matter_authorizer=allow_all,
            clock=lambda: FIXED_NOW,
        )
        self.report = run_pilot_dry_run(
            adapter,
            source_snapshot=SNAPSHOT,
            problem_id=fixture.PROBLEM_ID,
            incident_id=fixture.INCIDENT_ID,
        )
        self.plan = self.report.plan
        self.approval = MappingApproval(
            import_batch_id=self.plan.import_batch_id,
            reviewer="Synthetic Human Reviewer",
            decided_at="2099-01-03T00:00:00Z",
            decision="approved",
            approved_idempotency_keys=tuple(
                record.idempotency_key for record in self.plan.records
            ),
            review_artifact="synthetic-review-artifact#approved",
        )
        self.import_store = InMemoryPilotImportStore()
        self.import_result = run_approved_import(
            self.plan,
            approval=self.approval,
            inventory=self.report.pre_inventory,
            store=self.import_store,
            tenant_id=str(TENANT_ID),
        )
        self.workspace_store = InMemoryWorkspaceReadStore()
        self.view = self._project()
        self.corpus = pilot_corpus.build_pilot_corpus_state(
            tenant_id=TENANT_ID,
            release_id=RELEASE_ID,
            source_pins=pilot_corpus.pilot_source_pins(self.plan),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _project(self, **overrides: object) -> MatterWorkspaceRead:
        values: dict[str, object] = {
            "plan": self.plan,
            "store": self.import_store,
            "workspace_store": self.workspace_store,
            "tenant_id": TENANT_ID,
            "client_id": CLIENT_ID,
            "client_display_name": "Synthetic Pilot Client",
            "matter_status": "open",
            "matter_summary": "Synthetic pilot matter summary.",
            "member_principal_ids": (MEMBER_ID,),
        }
        values.update(overrides)
        return project_pilot_matter_workspace(**values)  # type: ignore[arg-type]

    def _suite(self, **overrides: object) -> PilotVerificationSuite:
        values: dict[str, object] = {
            "report": self.report,
            "approval": self.approval,
            "import_result": self.import_result,
            "import_store": self.import_store,
            "workspace_view": self.view,
            "workspace_store": self.workspace_store,
            "tenant_id": TENANT_ID,
            "member_principal_ids": (MEMBER_ID,),
            "release_id": RELEASE_ID,
            "registry": self.corpus.registry,
            "reconciliation": self.corpus.reconciliation,
            "health": self.corpus.health,
            "observed_at": FIRST_RUN_AT,
        }
        values.update(overrides)
        return run_pilot_verification(**values)  # type: ignore[arg-type]

    def test_healthy_stack_passes_every_check(self) -> None:
        suite = self._suite()
        self.assertTrue(suite.passed, msg=json.dumps(suite.checks, indent=2))
        self.assertEqual(EXPECTED_CHECK_KEYS, frozenset(suite.checks))
        self.assertFalse(suite.replay_performed)
        self.assertNotIn("replay.replays_deterministic", suite.checks)
        self.assertEqual(SNAPSHOT, suite.source_snapshot)
        self.assertEqual(str(TENANT_ID), suite.tenant_id)
        self.assertEqual(self.plan.import_batch_id, suite.import_batch_id)
        self.assertEqual(FIRST_RUN_AT.isoformat(), suite.generated_at)
        self.assertEqual(len(self.plan.records), suite.counts.import_records)
        self.assertEqual(len(self.view.tensions), suite.counts.tension_groups)

    def test_json_artifact_round_trips(self) -> None:
        suite = self._suite()
        restored = PilotVerificationSuite.model_validate(
            json.loads(suite.evidence_json())
        )
        self.assertEqual(suite, restored)
        self.assertIn("SKL-S5-01C", suite.artifact)

    def test_source_and_registry_coverage_evidence(self) -> None:
        suite = self._suite()
        self.assertEqual(
            len(self.report.pre_inventory), suite.source_preservation.inventoried_files
        )
        self.assertTrue(suite.source_preservation.zero_source_changes)
        self.assertTrue(suite.corpus_coverage.registry_entry_present)
        self.assertEqual(
            len(self.plan.source_files), suite.corpus_coverage.pinned_source_count
        )
        self.assertEqual(
            len(self.plan.source_files),
            suite.corpus_coverage.registry_source_count,
        )
        self.assertEqual("healthy", suite.corpus_coverage.health_status)
        self.assertEqual(100, suite.corpus_coverage.health_budget_ms)
        self.assertEqual(1, suite.corpus_coverage.deep_reconciliation_covered_releases)

    def test_replay_over_identical_pinned_inputs_is_deterministic(self) -> None:
        first = self._suite()
        second = self._suite(
            replay=first,
            observed_at=SECOND_RUN_AT,
        )
        self.assertTrue(second.replay_performed)
        self.assertTrue(second.replay.replays_deterministic)
        self.assertTrue(second.replay.replay_run_id_matches)
        self.assertTrue(second.replay.replay_check_set_matches)
        self.assertTrue(second.replay.replay_fingerprints_match)
        self.assertTrue(second.replay.replay_import_batch_matches)
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(
            second.replay.original_fingerprint,
            second.replay.replayed_fingerprint,
        )
        self.assertTrue(second.passed)

    def test_fingerprint_excludes_generation_time(self) -> None:
        first = self._suite(observed_at=FIRST_RUN_AT)
        second = self._suite(observed_at=SECOND_RUN_AT)
        self.assertNotEqual(first.generated_at, second.generated_at)
        self.assertEqual(
            verification_fingerprint(first), verification_fingerprint(second)
        )

    def test_replay_detects_changed_pinned_inputs(self) -> None:
        first = self._suite()
        replayed = self._suite(
            replay=first,
            release_id="dev-changed-release",
        )
        self.assertFalse(replayed.replay.replay_fingerprints_match)
        self.assertFalse(replayed.replay.replays_deterministic)
        self.assertFalse(replayed.passed)

    def test_replay_rejects_failing_original(self) -> None:
        failing = self._suite(
            registry=CorpusRegistrySnapshot(generated_at=FIRST_RUN_AT, entries=()),
        )
        self.assertFalse(failing.passed)
        replayed = self._suite(replay=failing)
        self.assertFalse(replayed.replay.replay_check_set_matches)
        self.assertFalse(replayed.replay.replays_deterministic)

    def test_advanced_execution_state_fails_closed(self) -> None:
        victim = self.plan.records[0]
        self.import_store.states[f"tampered:{victim.target_id}:execution"] = (
            ImportStateRow(
                idempotency_key=f"tampered:{victim.target_id}:execution",
                batch_id=self.plan.import_batch_id,
                target_type=victim.target_type,
                target_id=victim.target_id,
                state_kind="execution",
                state_value="dispatched",
            )
        )
        self.view = self._project()
        suite = self._suite()
        self.assertFalse(suite.states.no_state_advanced)
        self.assertFalse(suite.states.execution_states_not_started)
        self.assertFalse(suite.states.every_imported_target_negative)
        self.assertFalse(suite.passed)

    def test_harmonized_tension_fails_closed(self) -> None:
        harmonized = tuple(
            dataclasses.replace(tension, status="resolved", review_required=False)
            for tension in self.plan.tensions
        )
        self.plan = dataclasses.replace(self.plan, tensions=harmonized)
        self.view = self._project()
        suite = self._suite()
        self.assertFalse(suite.tensions.all_tensions_unresolved)
        self.assertFalse(suite.tensions.all_tensions_review_required)
        self.assertFalse(suite.passed)

    def test_registry_count_mismatch_fails_coverage(self) -> None:
        pinned = len(self.plan.source_files)
        drifted = zero_corpus_counts().with_count(CorpusCountKind.SOURCE, pinned + 1)
        entry = CorpusRegistryEntry(
            tenant_id=TENANT_ID,
            release_id=RELEASE_ID,
            counts=drifted,
            core_watermark=1,
            core_event_sha256="ab" * 32,
            required_replay_lsn="0/10",
            lag=ProjectionLag(lag_events=0, lag_seconds=0.0),
            last_reconciled_at=FIRST_RUN_AT,
        )
        drifted_snapshot = CorpusRegistrySnapshot(
            generated_at=FIRST_RUN_AT, entries=(entry,)
        )
        # The bounded health read must observe the same drifted store the
        # registry snapshot came from, as it does against a live backend.
        drifted_health = BoundedCorpusHealthReader(
            _StaticRegistryStore(drifted_snapshot),
            budget_ms=100,
            degraded_lag_events=1000,
        ).read()
        suite = self._suite(
            registry=drifted_snapshot,
            health=drifted_health,
        )
        self.assertFalse(suite.corpus_coverage.registry_counts_match_pinned_corpus)
        self.assertFalse(suite.corpus_coverage.bounded_health_counts_consistent)
        self.assertFalse(suite.passed)

    def test_missing_registry_entry_fails_coverage(self) -> None:
        suite = self._suite(
            registry=CorpusRegistrySnapshot(generated_at=FIRST_RUN_AT, entries=()),
        )
        self.assertFalse(suite.corpus_coverage.registry_entry_present)
        self.assertIsNone(suite.corpus_coverage.registry_source_count)
        self.assertFalse(suite.corpus_coverage.registry_counts_match_pinned_corpus)
        self.assertFalse(suite.passed)

    def test_unreconciled_corpus_fails_coverage(self) -> None:
        incomplete = CorpusReconciliationReport(
            tenant_id=TENANT_ID,
            started_at=FIRST_RUN_AT,
            completed_at=None,
            complete=False,
            releases_reconciled=0,
            discrepancies=(),
        )
        suite = self._suite(reconciliation=incomplete)
        self.assertFalse(suite.corpus_coverage.deep_reconciliation_complete)
        self.assertFalse(suite.corpus_coverage.deep_reconciliation_covered_releases)
        self.assertFalse(suite.passed)

    def test_stale_render_fails_provenance(self) -> None:
        self.view = self._project(current_source_snapshot="fixture-snapshot-later")
        suite = self._suite()
        self.assertFalse(suite.provenance.not_stale)
        self.assertTrue(suite.provenance.source_snapshot_pinned)
        self.assertFalse(suite.passed)

    def test_negative_states_render_exactly_as_imported(self) -> None:
        suite = self._suite()
        self.assertEqual(APPROVAL_STATE, suite.states.approval_value)
        self.assertEqual(EXECUTION_STATE, suite.states.execution_value)
        self.assertEqual(2 * len(self.plan.records), suite.states.state_rows)
        self.assertTrue(suite.states.rendered_states_match_import_store)
        self.assertTrue(suite.states.human_approval_recorded)
        self.assertTrue(suite.states.import_gated_on_approval)

    def test_suite_rejects_inconsistent_checks(self) -> None:
        suite = self._suite()
        tampered = json.loads(suite.evidence_json())
        tampered["passed"] = False
        with self.assertRaises(ValidationError):
            PilotVerificationSuite.model_validate(tampered)


if __name__ == "__main__":
    unittest.main()
