"""Disposable-Postgres contract tests for the SKL-S5-01B pilot import.

Exercises the approved import path against the isolated, networkless
disposable container shared with the persistence contract: real migration
0016 tables, idempotent rerun, changed-source revision, negative execution
state enforcement, and disposable rollback. All matter content is
synthetic fixture data.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sklegal_hammertime import HammerTimeReleaseAdapter, MatterAccessRequest
from sklegal_migration import (
    ImportGateError,
    MappingApproval,
    PilotImportPlan,
    PostgresPilotImportStore,
    reset_disposable_batch,
    run_approved_import,
    run_pilot_dry_run,
    withdraw_batch,
)

from tests.integration.persistence_contract_support import PersistenceContractBase
from tests.support import hammertime_fixture as fixture

FIXED_NOW = datetime(2099, 1, 2, 3, 4, 5, tzinfo=UTC)
REVIEWER = "Synthetic Human Reviewer"
REVIEW_ARTIFACT = "SKL-S5-01A-PILOT-MAPPING-REVIEW-2099-01-02.md#approved"


def _allow_all(request: MatterAccessRequest) -> bool:
    return True


def _approve(plan: PilotImportPlan) -> MappingApproval:
    return MappingApproval(
        import_batch_id=plan.import_batch_id,
        reviewer=REVIEWER,
        decided_at="2099-01-03T00:00:00Z",
        decision="approved",
        approved_idempotency_keys=tuple(
            record.idempotency_key for record in plan.records
        ),
        review_artifact=REVIEW_ARTIFACT,
    )


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "ARRAY[" + ", ".join(_literal(item) for item in value) + "]"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise TypeError(f"unsupported pilot import parameter: {type(value)!r}")


class PilotImportIntegrationTests(PersistenceContractBase):
    """Real import and rollback against the disposable Postgres container."""

    def _execute(self, sql: str, params: tuple[object, ...]) -> object:
        rendered = sql
        for param in params:
            rendered = rendered.replace("%s", _literal(param), 1)
        if "%s" in rendered:
            raise ValueError("pilot import SQL parameter count is incomplete")
        return self._psql("postgres", rendered).stdout.strip()

    def _store(self) -> PostgresPilotImportStore:
        return PostgresPilotImportStore(self._execute, disposable=True)

    def _report(self, root: Path, snapshot: str):
        adapter = HammerTimeReleaseAdapter(
            root=root,
            matter_authorizer=_allow_all,
            clock=lambda: FIXED_NOW,
        )
        return run_pilot_dry_run(
            adapter,
            source_snapshot=snapshot,
            problem_id=fixture.PROBLEM_ID,
            incident_id=fixture.INCIDENT_ID,
        )

    def _import_once(self, snapshot: str, tenant_id: str):
        tempdir = tempfile.TemporaryDirectory(prefix="sklegal-s501b-")
        self.addCleanup(tempdir.cleanup)
        root = fixture.build_hammertime_fixture(Path(tempdir.name))
        # Mark fixture bodies per test so idempotency keys stay unique across
        # tests sharing one disposable container.
        for relative in (fixture.PROBLEM_RELATIVE, fixture.INCIDENT_RELATIVE):
            path = root / relative
            path.write_text(
                path.read_text(encoding="utf-8")
                + f"Synthetic test marker: {snapshot}\n",
                encoding="utf-8",
            )
        report = self._report(root, snapshot)
        store = self._store()
        result = run_approved_import(
            report.plan,
            approval=_approve(report.plan),
            inventory=report.pre_inventory,
            store=store,
            tenant_id=tenant_id,
        )
        return root, report, store, result

    def test_01_import_tables_exist_in_disposable_schema(self) -> None:
        tables = self._psql(
            "postgres",
            """
            SELECT string_agg(tablename, ',' ORDER BY tablename)
            FROM pg_tables
            WHERE schemaname = 'sklegal_migrations'
              AND tablename LIKE 'pilot_import_%';
            """,
        ).stdout.strip()
        self.assertEqual(
            "pilot_import_batches,pilot_import_facts,pilot_import_records,"
            "pilot_import_source_files,pilot_import_states,"
            "pilot_import_tension_groups,pilot_import_withdrawn_targets",
            tables,
        )

    def test_02_approved_import_and_idempotent_rerun(self) -> None:
        tenant = "a5b70000-0000-4000-8000-000000000201"
        _root, report, store, first = self._import_once(
            "fixture-snapshot-integration-02", tenant
        )
        plan = report.plan

        self.assertTrue(first.batch_created)
        self.assertFalse(first.rerun)
        self.assertEqual(len(plan.records), first.records_created)
        self.assertEqual(len(plan.facts), first.facts_created)
        self.assertEqual(len(plan.tensions), first.tensions_created)
        self.assertGreater(first.tensions_created, 0)
        self.assertEqual(2 * len(plan.records), first.states_created)
        self.assertEqual(len(plan.source_files), first.source_files_recorded)
        self.assertEqual(
            len(report.pre_inventory),
            first.reconciliation["inventoried_files"],
        )

        batch_row = self._psql(
            "postgres",
            f"""
            SELECT status || ':' || approved_by || ':' || source_file_count
            FROM sklegal_migrations.pilot_import_batches
            WHERE batch_id = '{plan.import_batch_id}';
            """,
        ).stdout.strip()
        self.assertEqual(f"imported:{REVIEWER}:{len(plan.source_files)}", batch_row)
        negative_states = self._psql(
            "postgres",
            f"""
            SELECT count(*) || ':' || count(*) FILTER (
                WHERE (state_kind = 'approval' AND state_value = 'pending_review')
                   OR (state_kind = 'execution' AND state_value = 'not_started')
            )
            FROM sklegal_migrations.pilot_import_states
            WHERE batch_id = '{plan.import_batch_id}';
            """,
        ).stdout.strip()
        self.assertEqual(
            f"{2 * len(plan.records)}:{2 * len(plan.records)}", negative_states
        )

        before = dict(store.counts())
        second = run_approved_import(
            plan,
            approval=_approve(plan),
            inventory=report.pre_inventory,
            store=store,
            tenant_id=tenant,
        )
        self.assertTrue(second.rerun)
        self.assertFalse(second.batch_created)
        self.assertEqual(0, second.records_created)
        self.assertEqual(len(plan.records), second.records_suppressed)
        self.assertEqual(0, second.facts_created)
        self.assertEqual(len(plan.facts), second.facts_suppressed)
        self.assertEqual(0, second.tensions_created)
        self.assertEqual(0, second.states_created)
        self.assertEqual(0, second.source_files_recorded)
        self.assertEqual(before, dict(store.counts()))

    def test_03_database_rejects_non_negative_execution_state(self) -> None:
        tenant = "a5b70000-0000-4000-8000-000000000202"
        _root, report, _store, _result = self._import_once(
            "fixture-snapshot-integration-03", tenant
        )
        record = report.plan.records[0]
        refused = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_migrations.pilot_import_states
                (idempotency_key, batch_id, target_type, target_id,
                 state_kind, state_value)
            VALUES ('{"e" * 64}', '{report.plan.import_batch_id}',
                    '{record.target_type}', '{record.target_id}',
                    'execution', 'dispatched');
            """,
            check=False,
        )
        self.assertNotEqual(0, refused.returncode)
        states = self._psql(
            "postgres",
            f"""
            SELECT count(*) FROM sklegal_migrations.pilot_import_states
            WHERE batch_id = '{report.plan.import_batch_id}';
            """,
        ).stdout.strip()
        self.assertEqual(str(2 * len(report.plan.records)), states)

    def test_04_changed_source_revision_and_duplicate_suppression(self) -> None:
        tenant = "a5b70000-0000-4000-8000-000000000203"
        root, first_report, store, _first = self._import_once(
            "fixture-snapshot-integration-04", tenant
        )
        incident_path = root / fixture.INCIDENT_RELATIVE
        incident_path.write_text(
            incident_path.read_text(encoding="utf-8") + "Additional synthetic note.\n",
            encoding="utf-8",
        )
        second_report = self._report(root, "fixture-snapshot-integration-04")
        second_plan = second_report.plan
        self.assertNotEqual(
            first_report.plan.import_batch_id, second_plan.import_batch_id
        )

        result = run_approved_import(
            second_plan,
            approval=_approve(second_plan),
            inventory=second_report.pre_inventory,
            store=store,
            tenant_id=tenant,
        )
        self.assertEqual(1, result.records_created)
        self.assertEqual(1, result.records_suppressed)
        self.assertEqual(0, result.states_created)

        revisions = (
            self._psql(
                "postgres",
                """
            SELECT target_id::text || ':' || string_agg(revision::text, ','
                   ORDER BY revision)
            FROM sklegal_migrations.pilot_import_records
            GROUP BY target_id
            ORDER BY target_id;
            """,
            )
            .stdout.strip()
            .splitlines()
        )
        by_target = dict(line.split(":") for line in revisions)
        matter_target = first_report.plan.records[0].target_id
        event_target = first_report.plan.records[1].target_id
        self.assertEqual("1", by_target[matter_target])
        self.assertEqual("1,2", by_target[event_target])

        tensions = self._psql(
            "postgres",
            """
            SELECT count(*) || ':' || count(*) FILTER (WHERE status = 'unresolved'
                   AND review_required)
            FROM sklegal_migrations.pilot_import_tension_groups;
            """,
        ).stdout.strip()
        total, unresolved = tensions.split(":")
        self.assertEqual(total, unresolved)
        self.assertGreaterEqual(int(total), len(first_report.plan.tensions))

    def test_05_withdraw_and_disposable_rollback(self) -> None:
        tenant = "a5b70000-0000-4000-8000-000000000204"
        _root, report, store, _result = self._import_once(
            "fixture-snapshot-integration-05", tenant
        )
        plan = report.plan

        with self.assertRaises(ImportGateError):
            reset_disposable_batch(store, plan.import_batch_id)

        self.assertTrue(
            withdraw_batch(
                store, plan.import_batch_id, withdrawn_at="2099-01-04T00:00:00Z"
            )
        )
        self.assertFalse(withdraw_batch(store, plan.import_batch_id))
        self.assertEqual("withdrawn", store.batch_status(plan.import_batch_id))
        self.assertEqual(len(plan.records), len(store.withdrawn_targets()))

        with self.assertRaises(ImportGateError):
            run_approved_import(
                plan,
                approval=_approve(plan),
                inventory=report.pre_inventory,
                store=store,
                tenant_id=tenant,
            )

        deleted = reset_disposable_batch(store, plan.import_batch_id)
        self.assertEqual(len(plan.records), deleted["records"])
        self.assertEqual(len(plan.facts), deleted["facts"])
        self.assertEqual(len(plan.tensions), deleted["tension_groups"])
        self.assertEqual(2 * len(plan.records), deleted["states"])
        self.assertEqual(len(plan.source_files), deleted["source_files"])

        aftermath = self._psql(
            "postgres",
            f"""
            SELECT
                (SELECT count(*) FROM sklegal_migrations.pilot_import_records
                 WHERE batch_id = '{plan.import_batch_id}') || ':' ||
                (SELECT count(*) FROM sklegal_migrations.pilot_import_facts
                 WHERE batch_id = '{plan.import_batch_id}') || ':' ||
                (SELECT status FROM sklegal_migrations.pilot_import_batches
                 WHERE batch_id = '{plan.import_batch_id}') || ':' ||
                (SELECT count(*) FROM sklegal_migrations.pilot_import_withdrawn_targets
                 WHERE batch_id = '{plan.import_batch_id}');
            """,
        ).stdout.strip()
        self.assertEqual(f"0:0:withdrawn:{len(plan.records)}", aftermath)

        with self.assertRaises(ImportGateError):
            run_approved_import(
                plan,
                approval=_approve(plan),
                inventory=report.pre_inventory,
                store=store,
                tenant_id=tenant,
            )

    def test_06_import_fails_closed_without_approval_in_real_store(self) -> None:
        tenant = "a5b70000-0000-4000-8000-000000000205"
        with tempfile.TemporaryDirectory(prefix="sklegal-s501b-") as tempdir:
            root = fixture.build_hammertime_fixture(Path(tempdir))
            report = self._report(root, "fixture-snapshot-integration-06")
            store = self._store()
            with self.assertRaises(ImportGateError):
                run_approved_import(
                    report.plan,
                    approval=None,
                    inventory=report.pre_inventory,
                    store=store,
                    tenant_id=tenant,
                )
            batches = self._psql(
                "postgres",
                """
                SELECT count(*)
                FROM sklegal_migrations.pilot_import_batches
                WHERE source_snapshot = 'fixture-snapshot-integration-06';
                """,
            ).stdout.strip()
            self.assertEqual("0", batches)


if __name__ == "__main__":
    unittest.main()
