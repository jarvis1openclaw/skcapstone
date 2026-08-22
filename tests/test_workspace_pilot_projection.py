"""Projection tests: approved pilot import to matter workspace read model.

Proves the pilot matter renders entirely in legal terminology with
unresolved tensions, negative execution states, version lineage,
provenance, and explicit gaps intact. All fixtures are synthetic and
built in a temporary directory; no real matter content, real legacy
identifiers, or HammerTime paths are used.
"""

from __future__ import annotations

import dataclasses
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, UUID, uuid5

from sklegal_api.workspace import InMemoryWorkspaceReadStore
from sklegal_api.workspace_pilot import project_pilot_matter_workspace
from sklegal_hammertime import HammerTimeReleaseAdapter, MatterAccessRequest
from sklegal_migration import (
    AtomicFactProposal,
    InMemoryPilotImportStore,
    MappingApproval,
    run_approved_import,
    run_pilot_dry_run,
)

from tests.support import hammertime_fixture as fixture

TENANT_ID = UUID("a5b70000-0000-4000-8000-000000000101")
CLIENT_ID = UUID("a5b70000-0000-4000-8000-000000000202")
MEMBER_ID = UUID("a5b70000-0000-4000-8000-000000000303")
SNAPSHOT = "fixture-snapshot-2099-01-02"
FIXED_NOW = datetime(2099, 1, 2, 3, 4, 5, tzinfo=UTC)


def allow_all(request: MatterAccessRequest) -> bool:
    return True


class PilotWorkspaceProjectionTest(unittest.TestCase):
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
        approval = MappingApproval(
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
        run_approved_import(
            self.plan,
            approval=approval,
            inventory=self.report.pre_inventory,
            store=self.import_store,
            tenant_id=str(TENANT_ID),
        )
        self.workspace_store = InMemoryWorkspaceReadStore()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def project(self, **overrides: object):
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

    def test_matter_presented_in_legal_terminology(self) -> None:
        view = self.project()
        self.assertEqual("Fixture matter for adapter tests", view.matter.title)
        self.assertEqual((fixture.PROBLEM_ID,), view.matter.legacy_aliases)
        self.assertEqual(1, len(view.timeline))
        event = view.timeline[0]
        self.assertEqual("transaction_review", event.event_type)
        self.assertEqual((fixture.INCIDENT_ID,), event.legacy_aliases)
        dumped = json.dumps(view.model_dump(mode="json", by_alias=True))
        # Mapping rule ids carry legacy storage labels and must not surface.
        self.assertNotIn("problem.matter", dumped)
        self.assertNotIn("incident.transaction_review", dumped)
        # Legacy storage labels never appear as canonical record types.
        self.assertNotIn('"problem"', dumped)
        self.assertNotIn('"incident"', dumped)
        self.assertIn('"matter"', dumped)
        self.assertIn('"matter_event"', dumped)

    def test_unresolved_tensions_remain_visible(self) -> None:
        view = self.project()
        self.assertGreaterEqual(len(view.tensions), 1)
        for tension in view.tensions:
            self.assertEqual("unresolved", tension.status)
            self.assertTrue(tension.review_required)
            self.assertGreaterEqual(len(tension.assertion_ids), 2)
        tension_keys = {tension.tension_key for tension in view.tensions}
        grouped = {
            fact.tension_group_key
            for fact in view.facts
            if fact.tension_group_key is not None
        }
        self.assertEqual(tension_keys, grouped)

    def test_negative_execution_states_remain_visible(self) -> None:
        view = self.project()
        self.assertEqual(2 * len(self.plan.records), len(view.execution_states))
        by_kind: dict[str, set[str]] = {}
        for state in view.execution_states:
            by_kind.setdefault(state.state_kind, set()).add(state.state_value)
        self.assertEqual({"pending_review"}, by_kind["approval"])
        self.assertEqual({"not_started"}, by_kind["execution"])

    def test_version_lineage_marks_historical_and_baseline(self) -> None:
        view = self.project()
        self.assertEqual((1, 2), tuple(v.packet_version for v in view.version_lineage))
        v1, v2 = view.version_lineage
        self.assertTrue(v1.historical)
        self.assertFalse(v1.current_review_baseline)
        self.assertFalse(v2.historical)
        self.assertTrue(v2.current_review_baseline)

    def test_facts_keep_source_asserted_review_and_provenance(self) -> None:
        view = self.project()
        self.assertEqual(len(self.plan.facts), len(view.facts))
        for fact in view.facts:
            self.assertEqual("source_asserted", fact.review_status)
            self.assertFalse(fact.source_missing)
            self.assertIsNotNone(fact.source_path)
        self.assertFalse(view.provenance.stale)
        self.assertEqual(SNAPSHOT, view.provenance.source_snapshot)

    def test_stale_snapshot_is_flagged(self) -> None:
        view = self.project(current_source_snapshot="fixture-snapshot-later")
        self.assertTrue(view.provenance.stale)
        self.assertEqual(SNAPSHOT, view.provenance.source_snapshot)
        self.assertEqual(
            "fixture-snapshot-later", view.provenance.current_source_snapshot
        )

    def test_missing_source_becomes_explicit_gap(self) -> None:
        orphan = AtomicFactProposal(
            fact_assertion_id=str(uuid5(NAMESPACE_URL, "fact:orphan:synthetic")),
            predicate="synthetic_predicate",
            value="synthetic value",
            value_type="string",
            source_reference_id=str(
                uuid5(NAMESPACE_URL, "source:no-such-file:deadbeef")
            ),
            source_locator="frontmatter/synthetic_predicate",
        )
        plan = dataclasses.replace(self.plan, facts=self.plan.facts + (orphan,))
        view = self.project(plan=plan)
        flagged = [fact for fact in view.facts if fact.source_missing]
        self.assertEqual(1, len(flagged))
        self.assertEqual("synthetic_predicate", flagged[0].predicate)
        self.assertIsNone(flagged[0].source_path)
        self.assertIn("missing_source", {gap.kind for gap in view.gaps})

    def test_explicit_gaps_for_unrecorded_sections(self) -> None:
        view = self.project()
        kinds = {gap.kind for gap in view.gaps}
        self.assertIn("unrecorded_parties", kinds)
        self.assertIn("unrecorded_evidence", kinds)
        self.assertIn("unrecorded_engagement", kinds)
        self.assertEqual((), view.parties)
        self.assertEqual((), view.evidence)

    def test_correspondence_surfaces_as_communication(self) -> None:
        view = self.project()
        self.assertEqual(1, len(view.communications))
        entry = view.communications[0]
        self.assertEqual("correspondence", entry.channel)
        self.assertEqual("recorded", entry.status)
        self.assertIn("OWNER-DIRECTIONS", entry.summary)

    def test_audit_records_approval_and_import(self) -> None:
        view = self.project()
        actions = {entry.action for entry in view.audit}
        self.assertIn("pilot_mapping_review.approved", actions)
        self.assertIn("pilot_import.recorded", actions)
        approval = next(
            entry
            for entry in view.audit
            if entry.action == "pilot_mapping_review.approved"
        )
        self.assertEqual("Synthetic Human Reviewer", approval.actor)
        self.assertEqual("approved", approval.outcome)

    def test_projection_registers_store_and_membership(self) -> None:
        view = self.project()
        found = self.workspace_store.get_matter_workspace(
            TENANT_ID, view.matter.matter_id
        )
        self.assertIsNotNone(found)
        self.assertTrue(
            self.workspace_store.is_matter_member(
                TENANT_ID, view.matter.matter_id, MEMBER_ID
            )
        )
        self.assertFalse(
            self.workspace_store.is_matter_member(
                TENANT_ID, view.matter.matter_id, uuid5(NAMESPACE_URL, "nonmember")
            )
        )

    def test_projection_fails_closed_on_tenant_mismatch(self) -> None:
        with self.assertRaises(ValueError):
            self.project(tenant_id=uuid5(NAMESPACE_URL, "other-tenant"))

    def test_projection_fails_closed_without_recorded_batch(self) -> None:
        with self.assertRaises(ValueError):
            self.project(store=InMemoryPilotImportStore())


if __name__ == "__main__":
    unittest.main()
