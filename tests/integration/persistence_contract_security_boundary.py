"""Audit timestamp, information barrier, and parity persistence contract tests."""

from __future__ import annotations

import inspect
import unittest

import sklegal_domain
from sklegal_domain.base import DomainEntity
from sklegal_persistence import validate_mapping_contract

from tests.integration.persistence_contract_support import PersistenceContractBase


class PersistenceContract01SecurityBoundaryTests(PersistenceContractBase):
    def test_01_audit_timestamp_domain_rejects_without_side_effects(self) -> None:
        value = self.fixture
        before = self._psql(
            "postgres",
            """
            SELECT (SELECT count(*) FROM sklegal_audit.events)::text || ':' ||
                   (SELECT count(*) FROM sklegal_audit.outbox)::text || ':' ||
                   (SELECT count(*) FROM sklegal_audit.rollback_guard)::text;
            """,
        )
        self.assertEqual("0:0:0", before.stdout.strip())

        for label, occurred_at in (
            ("bc", "0001-01-02 03:04:05.123456 BC"),
            ("year-10000", "10000-01-02 03:04:05.123456 AD"),
        ):
            denied = self._append_audit_event(
                event_id=(
                    "a6700000-0000-4000-8000-000000000001"
                    if label == "bc"
                    else "a6700000-0000-4000-8000-000000000002"
                ),
                run_id="a6700000-0000-4000-8000-000000000010",
                correlation_id="a6700000-0000-4000-8000-000000000020",
                span_id=f"{1 if label == 'bc' else 2:016x}",
                boundary="api",
                action=f"audit.synthetic.timestamp-{label}",
                occurred_at=occurred_at,
                check=False,
            )
            with self.subTest(append=label):
                self.assertNotEqual(0, denied.returncode)
                self.assertIn("canonical audit timestamp", denied.stderr)

        for label, expression in (
            ("bc", "'0001-01-02 03:04:05.123456 BC'::timestamptz"),
            ("year-10000", "'10000-01-02 03:04:05.123456 AD'::timestamptz"),
            ("infinity", "'infinity'::timestamptz"),
            ("negative-infinity", "'-infinity'::timestamptz"),
        ):
            denied = self._psql(
                "postgres",
                f"SELECT sklegal_audit.canonical_timestamp({expression});",
                check=False,
            )
            with self.subTest(helper=label):
                self.assertNotEqual(0, denied.returncode)
                self.assertIn("canonical audit timestamp", denied.stderr)

        after = self._psql(
            "postgres",
            f"""
            SELECT (SELECT count(*) FROM sklegal_audit.events)::text || ':' ||
                   (SELECT count(*) FROM sklegal_audit.outbox)::text || ':' ||
                   (SELECT count(*) FROM sklegal_audit.rollback_guard)::text || ':' ||
                   (SELECT count(*) FROM sklegal_audit.events
                    WHERE tenant_id = '{value["tenant_alpha"]}'
                      AND id IN (
                          'a6700000-0000-4000-8000-000000000001',
                          'a6700000-0000-4000-8000-000000000002'
                      ))::text;
            """,
        )
        self.assertEqual("0:0:0:0", after.stdout.strip())

    def test_01_domain_parity_matrix_and_forced_rls(self) -> None:
        validate_mapping_contract()
        entities = self.parity["entities"]
        exported_entities = {
            name
            for name in sklegal_domain.__all__
            if inspect.isclass(entity_type := getattr(sklegal_domain, name, None))
            and issubclass(entity_type, DomainEntity)
            and entity_type is not DomainEntity
        }
        self.assertEqual(36, len(exported_entities))
        self.assertEqual(exported_entities, set(entities))
        for contract in entities.values():
            schema, table = contract["table"].split(".")
            output = self._psql(
                "postgres",
                f"""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = '{schema}' AND table_name = '{table}'
                ORDER BY ordinal_position;
                """,
            )
            columns = set(output.stdout.splitlines())
            self.assertTrue(set(contract["columns"]).issubset(columns), contract)
        rls = self._psql(
            "postgres",
            """
            SELECT count(*) FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname LIKE 'sklegal_%'
              AND namespace.nspname <> 'sklegal_migrations'
              AND relation.relkind = 'r'
              AND NOT (
                  namespace.nspname = 'sklegal_audit'
                  AND relation.relname = 'rollback_guard'
              )
              AND (NOT relation.relrowsecurity OR NOT relation.relforcerowsecurity);
            """,
        )
        self.assertEqual("0", rls.stdout.strip())
        rollback_guard = self._psql(
            "postgres",
            """
            SELECT relation.relrowsecurity || ':' ||
                   relation.relforcerowsecurity || ':' ||
                   has_table_privilege(
                       'sklegal_test_alpha_one', relation.oid, 'SELECT'
                   ) || ':' ||
                   has_table_privilege(
                       'sklegal_test_alpha_one', relation.oid, 'INSERT'
                   ) || ':' ||
                   has_table_privilege(
                       'sklegal_test_alpha_one', relation.oid, 'UPDATE'
                   ) || ':' ||
                   has_table_privilege(
                       'sklegal_test_alpha_one', relation.oid, 'DELETE'
                   )
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'sklegal_audit'
              AND relation.relname = 'rollback_guard';
            """,
        )
        self.assertEqual(
            "false:false:false:false:false:false", rollback_guard.stdout.strip()
        )
        hidden_guard = self._psql(
            "sklegal_test_alpha_one",
            "SELECT * FROM sklegal_audit.rollback_guard;",
            check=False,
        )
        self.assertNotEqual(0, hidden_guard.returncode)
        self.assertIn("permission denied", hidden_guard.stderr)

    def test_01_information_barrier_snapshot_is_scoped_sanitized_and_sealed(
        self,
    ) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        other_matter = value["matter_alpha_two"]
        principal = value["principal_alpha_one"]
        other_principal = value["principal_alpha_two"]
        source_one = "93000000-0000-4000-8000-000000000001"
        source_two = "93000000-0000-4000-8000-000000000002"
        party_one = "93000000-0000-4000-8001-000000000001"
        party_two = "93000000-0000-4000-8001-000000000002"
        normalization_one = "93000000-0000-4000-8002-000000000001"
        normalization_two = "93000000-0000-4000-8002-000000000002"
        association = "93000000-0000-4000-8003-000000000001"
        conflict_check = "93000000-0000-4000-8004-000000000001"
        conflict_match = "93000000-0000-4000-8005-000000000001"
        conflict_decision = "93000000-0000-4000-8006-000000000001"
        conflict_hold = "93000000-0000-4000-8007-000000000001"
        classification = "93000000-0000-4000-8008-000000000001"
        retention = "93000000-0000-4000-8009-000000000001"
        retention_successor = "93000000-0000-4000-8009-000000000002"
        legal_hold = "93000000-0000-4000-8010-000000000001"
        policy_state = "93000000-0000-4000-8011-000000000001"
        policy_state_successor = "93000000-0000-4000-8011-000000000002"
        policy_state_after_release = "93000000-0000-4000-8011-000000000003"
        material = "93000000-0000-4000-8012-000000000001"
        normalized_digest = "d" * 64
        self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES
                ('{source_one}', '{tenant}', '{matter}', 'synthetic', 'v1',
                 '{"a" * 64}', 'synthetic:policy-one',
                 '2026-08-20T06:00:00Z'),
                ('{source_two}', '{tenant}', '{other_matter}', 'synthetic', 'v1',
                 '{"b" * 64}', 'synthetic:policy-two',
                 '2026-08-20T06:00:00Z');
            INSERT INTO sklegal_legal.parties
                (id, tenant_id, matter_id, display_name, party_kind,
                 source_reference_id)
            VALUES
                ('{party_one}', '{tenant}', '{matter}', 'Synthetic Policy One',
                 'company', '{source_one}'),
                ('{party_two}', '{tenant}', '{other_matter}',
                 'Synthetic Policy Two', 'company', '{source_two}');
            INSERT INTO sklegal_legal.party_normalizations
                (id, policy_change_id, tenant_id, matter_id, party_id, party_kind,
                 normalized_identity_digest, normalization_version,
                 reviewed_by_principal_id, reviewed_at)
            VALUES
                ('{normalization_one}', 999999, '{tenant}', '{matter}', '{party_one}',
                 'company', '{normalized_digest}',
                 'sklegal-party-normalization/v1', '{principal}',
                 '2026-08-20T06:01:00Z'),
                ('{normalization_two}', 999999, '{tenant}', '{other_matter}',
                 '{party_two}',
                 'company', '{normalized_digest}',
                 'sklegal-party-normalization/v1', '{other_principal}',
                 '2026-08-20T06:01:00Z');
            INSERT INTO sklegal_legal.party_associations
                (id, tenant_id, matter_id, party_id, relationship, active,
                 valid_from, source_reference_id)
            VALUES
                ('{association}', '{tenant}', '{other_matter}', '{party_two}',
                 'adverse_party', true, '2026-08-20T06:00:00Z', '{source_two}');
            INSERT INTO sklegal_legal.conflict_checks
                (id, tenant_id, matter_id, checked_by_principal_id, checked_at,
                 normalization_version, result, complete)
            VALUES
                ('{conflict_check}', '{tenant}', '{matter}', '{principal}',
                 '2026-08-20T06:02:00Z', 'sklegal-party-normalization/v1',
                 'hold', true);
            INSERT INTO sklegal_legal.conflict_matches
                (id, tenant_id, matter_id, conflict_check_id, candidate_party_id,
                 existing_matter_id, association_id,
                 normalized_identity_digest, collision_kind)
            VALUES
                ('{conflict_match}', '{tenant}', '{matter}', '{conflict_check}',
                 '{party_one}', '{other_matter}', '{association}',
                 '{normalized_digest}', 'exact_normalized_name');
            INSERT INTO sklegal_legal.conflict_decisions
                (id, tenant_id, matter_id, conflict_check_id, disposition,
                 decided_by_principal_id, decided_at)
            VALUES
                ('{conflict_decision}', '{tenant}', '{matter}', '{conflict_check}',
                 'hold', '{principal}', '2026-08-20T06:03:00Z');
            INSERT INTO sklegal_legal.conflict_holds
                (id, tenant_id, matter_id, conflict_decision_id, reason_code,
                 effective_from)
            VALUES
                ('{conflict_hold}', '{tenant}', '{matter}', '{conflict_decision}',
                 'adverse_party_collision', '2026-08-20T06:03:00Z');
            INSERT INTO sklegal_legal.material_classifications
                (id, tenant_id, matter_id, material_id, material_version,
                 source_kind, source_id, classification,
                 classified_by_principal_id, classified_at)
            VALUES
                ('{classification}', '{tenant}', '{matter}', '{material}', 1,
                 'material', '{material}', 'confidential', '{principal}',
                 '2026-08-20T06:04:00Z');
            INSERT INTO sklegal_legal.retention_policies
                (id, tenant_id, matter_id, retain_for_days, effective_from,
                 effective_to, decided_by_principal_id)
            VALUES
                ('{retention}', '{tenant}', '{matter}', 30,
                 '2026-08-20T06:00:00Z', '2026-08-20T07:00:00Z',
                 '{principal}');
            INSERT INTO sklegal_legal.legal_holds
                (id, tenant_id, matter_id, status, hold_scope,
                 issued_by_principal_id, effective_from)
            VALUES
                ('{legal_hold}', '{tenant}', '{matter}', 'active', 'matter',
                 '{principal}', '2026-08-20T06:05:00Z');
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, conflict_decision_id,
                 retention_policy_id,
                 conflict_state_complete, wall_state_complete,
                 classification_state_complete, legal_hold_state_complete,
                 ownership_resolved, pending_export, preservation_required,
                 recorded_by_principal_id, recorded_at)
            VALUES
                ('{policy_state}', '{tenant}', '{matter}', '{conflict_decision}',
                 '{retention}',
                 true, true, true, true, true, false, false, '{principal}',
                 '2026-08-20T06:06:00Z');
            """,
        )
        direct = self._psql(
            "sklegal_test_alpha_one",
            "SELECT count(*) FROM sklegal_legal.conflict_matches;",
        )
        self.assertEqual("0", direct.stdout.strip())
        self.assertEqual(
            "t",
            self._psql(
                "postgres",
                f"""
                SELECT pg_catalog.bool_and(policy_change_id <> 999999)
                FROM sklegal_legal.party_normalizations
                WHERE tenant_id = '{tenant}';
                """,
            ).stdout.strip(),
        )
        snapshot = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT snapshot->>'policy_revision' ~ '^[0-9a-f]{{64}}$',
                   snapshot#>>'{{conflict_decision,disposition}}',
                   snapshot#>>'{{classification_sources,0,classification}}',
                   snapshot#>>'{{legal_holds,0,status}}',
                   snapshot->>'profile_owner_principal_id',
                   snapshot ? 'content', snapshot ? 'display_name'
            FROM (
                SELECT sklegal_legal.material_policy_snapshot(
                    '{tenant}', '{matter}', '{material}', 1, '{principal}'
                ) AS snapshot
            ) AS policy;
            """,
        )
        self.assertEqual(
            "t|hold|confidential|active||f|f",
            snapshot.stdout.strip(),
        )
        initial_revision = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT sklegal_legal.material_policy_snapshot(
                '{tenant}', '{matter}', '{material}', 1, '{principal}'
            )->>'policy_revision';
            """,
        ).stdout.strip()
        self.assertEqual(64, len(initial_revision))

        duplicate_decision_head = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.conflict_decisions
                (id, tenant_id, matter_id, conflict_check_id, disposition,
                 decided_by_principal_id, decided_at)
            VALUES
                ('93000000-0000-4000-8095-000000000001', '{tenant}', '{matter}',
                 '{conflict_check}', 'hold', '{principal}',
                 '2026-08-20T06:03:01Z');
            """,
            check=False,
        )
        self.assertNotEqual(0, duplicate_decision_head.returncode)
        self.assertIn("linear current head", duplicate_decision_head.stderr)

        tied_decision = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.conflict_decisions
                (id, tenant_id, matter_id, conflict_check_id, disposition,
                 decided_by_principal_id, decided_at, supersedes_decision_id)
            VALUES
                ('93000000-0000-4000-8095-000000000002', '{tenant}', '{matter}',
                 '{conflict_check}', 'hold', '{principal}',
                 '2026-08-20T06:03:00Z', '{conflict_decision}');
            """,
            check=False,
        )
        self.assertNotEqual(0, tied_decision.returncode)
        self.assertIn("strictly advance", tied_decision.stderr)

        future_decision = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.conflict_decisions
                (id, tenant_id, matter_id, conflict_check_id, disposition,
                 decided_by_principal_id, decided_at, supersedes_decision_id)
            VALUES
                ('93000000-0000-4000-8095-000000000003', '{tenant}', '{matter}',
                 '{conflict_check}', 'hold', '{principal}',
                 '2099-08-20T06:03:00Z', '{conflict_decision}');
            """,
            check=False,
        )
        self.assertNotEqual(0, future_decision.returncode)
        self.assertIn("future", future_decision.stderr)

        successor_check = "93000000-0000-4000-8095-000000000004"
        successor_decision = "93000000-0000-4000-8095-000000000005"
        self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.conflict_checks
                (id, tenant_id, matter_id, checked_by_principal_id, checked_at,
                 normalization_version, result, complete)
            VALUES
                ('{successor_check}', '{tenant}', '{matter}', '{principal}',
                 '2026-08-20T06:06:30Z', 'sklegal-party-normalization/v1',
                 'clear', true);
            INSERT INTO sklegal_legal.conflict_decisions
                (id, tenant_id, matter_id, conflict_check_id, disposition,
                 decided_by_principal_id, decided_at, supersedes_decision_id)
            VALUES
                ('{successor_decision}', '{tenant}', '{matter}',
                 '{successor_check}', 'clear', '{principal}',
                 '2026-08-20T06:06:31Z', '{conflict_decision}');
            """,
        )
        stale_snapshot = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT sklegal_legal.material_policy_snapshot(
                '{tenant}', '{matter}', '{material}', 1, '{principal}'
            );
            """,
            check=False,
        )
        self.assertNotEqual(0, stale_snapshot.returncode)
        self.assertIn("policy state is stale", stale_snapshot.stderr)

        overlapping_retention = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.retention_policies
                (id, tenant_id, matter_id, retain_for_days, effective_from,
                 decided_by_principal_id, supersedes_retention_policy_id)
            VALUES
                ('93000000-0000-4000-8095-000000000006', '{tenant}', '{matter}',
                 60, '2026-08-20T06:59:59Z', '{principal}', '{retention}');
            """,
            check=False,
        )
        self.assertNotEqual(0, overlapping_retention.returncode)
        self.assertIn("overlap", overlapping_retention.stderr)

        self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.retention_policies
                (id, tenant_id, matter_id, retain_for_days, effective_from,
                 decided_by_principal_id, supersedes_retention_policy_id)
            VALUES
                ('{retention_successor}', '{tenant}', '{matter}', 60,
                 '2026-08-20T07:00:00Z', '{principal}', '{retention}');
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, conflict_decision_id,
                 retention_policy_id, conflict_state_complete,
                 wall_state_complete, classification_state_complete,
                 legal_hold_state_complete, ownership_resolved, pending_export,
                 preservation_required, recorded_by_principal_id, recorded_at,
                 supersedes_state_id)
            VALUES
                ('{policy_state_successor}', '{tenant}', '{matter}',
                 '{successor_decision}', '{retention_successor}', true, true,
                 true, true, true, false, false, '{principal}',
                 '2026-08-20T07:00:01Z', '{policy_state}');
            """,
        )
        current_snapshot = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT snapshot#>>'{{conflict_decision,disposition}}',
                   snapshot#>>'{{retention_policy,retention_policy_id}}',
                   snapshot->>'policy_revision' <> '{initial_revision}'
            FROM (
                SELECT sklegal_legal.material_policy_snapshot(
                    '{tenant}', '{matter}', '{material}', 1, '{principal}'
                ) AS snapshot
            ) AS policy;
            """,
        )
        self.assertEqual(
            f"clear|{retention_successor}|t",
            current_snapshot.stdout.strip(),
        )

        branched_state = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, conflict_decision_id,
                 retention_policy_id, conflict_state_complete,
                 wall_state_complete, classification_state_complete,
                 legal_hold_state_complete, ownership_resolved, pending_export,
                 preservation_required, recorded_by_principal_id, recorded_at,
                 supersedes_state_id)
            VALUES
                ('93000000-0000-4000-8095-000000000007', '{tenant}', '{matter}',
                 '{successor_decision}', '{retention_successor}', true, true,
                 true, true, true, false, false, '{principal}',
                 '2026-08-20T07:00:02Z', '{policy_state}');
            """,
            check=False,
        )
        self.assertNotEqual(0, branched_state.returncode)
        self.assertIn("linear current head", branched_state.stderr)

        tied_state = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, conflict_decision_id,
                 retention_policy_id, conflict_state_complete,
                 wall_state_complete, classification_state_complete,
                 legal_hold_state_complete, ownership_resolved, pending_export,
                 preservation_required, recorded_by_principal_id, recorded_at,
                 supersedes_state_id)
            VALUES
                ('93000000-0000-4000-8095-000000000008', '{tenant}', '{matter}',
                 '{successor_decision}', '{retention_successor}', true, true,
                 true, true, true, false, false, '{principal}',
                 '2026-08-20T07:00:01Z', '{policy_state_successor}');
            """,
            check=False,
        )
        self.assertNotEqual(0, tied_state.returncode)
        self.assertIn("strictly advance", tied_state.stderr)
        missing_conflict_head = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, retention_policy_id,
                 conflict_state_complete, wall_state_complete,
                 classification_state_complete, legal_hold_state_complete,
                 ownership_resolved, pending_export, preservation_required,
                 recorded_by_principal_id, recorded_at, supersedes_state_id)
            VALUES
                ('93000000-0000-4000-8095-000000000010', '{tenant}', '{matter}',
                 '{retention_successor}', true, true, true, true, true, false,
                 false, '{principal}', '2026-08-20T07:00:02Z',
                 '{policy_state_successor}');
            """,
            check=False,
        )
        self.assertNotEqual(0, missing_conflict_head.returncode)
        self.assertIn("exact decision head", missing_conflict_head.stderr)

        future_state = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, conflict_decision_id,
                 retention_policy_id, conflict_state_complete,
                 wall_state_complete, classification_state_complete,
                 legal_hold_state_complete, ownership_resolved, pending_export,
                 preservation_required, recorded_by_principal_id, recorded_at,
                 supersedes_state_id)
            VALUES
                ('93000000-0000-4000-8095-000000000011', '{tenant}', '{matter}',
                 '{successor_decision}', '{retention_successor}', true, true,
                 true, true, true, false, false, '{principal}',
                 '2099-08-20T07:00:02Z', '{policy_state_successor}');
            """,
            check=False,
        )
        self.assertNotEqual(0, future_state.returncode)
        self.assertIn("future head", future_state.stderr)

        future_retention = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.retention_policies
                (id, tenant_id, matter_id, retain_for_days, effective_from,
                 decided_by_principal_id, supersedes_retention_policy_id)
            VALUES
                ('93000000-0000-4000-8095-000000000012', '{tenant}', '{matter}',
                 90, '2099-08-20T07:00:02Z', '{principal}',
                 '{retention_successor}');
            """,
            check=False,
        )
        self.assertNotEqual(0, future_retention.returncode)
        self.assertIn("future head", future_retention.stderr)
        mismatched_decision = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.conflict_decisions
                (id, tenant_id, matter_id, conflict_check_id, disposition,
                 decided_by_principal_id, decided_at)
            VALUES
                ('93000000-0000-4000-8098-000000000001', '{tenant}', '{matter}',
                 '{conflict_check}', 'clear', '{principal}',
                 '2026-08-20T06:03:01Z');
            """,
            check=False,
        )
        self.assertNotEqual(0, mismatched_decision.returncode)
        self.assertIn("matching complete check evidence", mismatched_decision.stderr)
        mismatched_release = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.legal_holds
                (id, tenant_id, matter_id, status, hold_scope, material_id,
                 issued_by_principal_id, effective_from, supersedes_hold_id,
                 released_by_principal_id, released_at)
            VALUES
                ('93000000-0000-4000-8097-000000000001', '{tenant}', '{matter}',
                 'released', 'material', '{material}', '{principal}',
                 '2026-08-20T06:05:00Z', '{legal_hold}', '{principal}',
                 '2026-08-20T06:06:00Z');
            """,
            check=False,
        )
        self.assertNotEqual(0, mismatched_release.returncode)
        self.assertIn("exact active hold", mismatched_release.stderr)
        valid_release = "93000000-0000-4000-8096-000000000001"
        self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.legal_holds
                (id, tenant_id, matter_id, status, hold_scope,
                 issued_by_principal_id, effective_from, supersedes_hold_id,
                 released_by_principal_id, released_at)
            VALUES
                ('{valid_release}', '{tenant}', '{matter}', 'released', 'matter',
                 '{principal}', '2026-08-20T06:05:00Z', '{legal_hold}',
                 '{principal}', '2026-08-20T06:06:00Z');
            """,
        )
        self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, conflict_decision_id,
                 retention_policy_id, conflict_state_complete,
                 wall_state_complete, classification_state_complete,
                 legal_hold_state_complete, ownership_resolved, pending_export,
                 preservation_required, recorded_by_principal_id, recorded_at,
                 supersedes_state_id)
            VALUES
                ('{policy_state_after_release}', '{tenant}', '{matter}',
                 '{successor_decision}', '{retention_successor}', true, true,
                 true, true, true, false, false, '{principal}',
                 '2026-08-20T07:00:03Z', '{policy_state_successor}');
            """,
        )
        duplicate_release = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.legal_holds
                (id, tenant_id, matter_id, status, hold_scope,
                 issued_by_principal_id, effective_from, supersedes_hold_id,
                 released_by_principal_id, released_at)
            VALUES
                ('93000000-0000-4000-8095-000000000009', '{tenant}', '{matter}',
                 'released', 'matter', '{principal}',
                 '2026-08-20T06:05:00Z', '{legal_hold}', '{principal}',
                 '2026-08-20T07:00:04Z');
            """,
            check=False,
        )
        self.assertNotEqual(0, duplicate_release.returncode)
        released_snapshot = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT snapshot->'legal_holds' @> pg_catalog.jsonb_build_array(
                pg_catalog.jsonb_build_object(
                    'legal_hold_id', '{valid_release}'::uuid,
                    'status', 'released',
                    'supersedes_hold_id', '{legal_hold}'::uuid
                )
            )
            FROM (
                SELECT sklegal_legal.material_policy_snapshot(
                    '{tenant}', '{matter}', '{material}', 1, '{principal}'
                ) AS snapshot
            ) AS policy;
            """,
        )
        self.assertEqual("t", released_snapshot.stdout.strip())
        for target_matter, target_principal in (
            (other_matter, principal),
            (matter, other_principal),
        ):
            with self.subTest(
                target_matter=target_matter,
                target_principal=target_principal,
            ):
                denied = self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    SELECT sklegal_legal.material_policy_snapshot(
                        '{tenant}', '{target_matter}', '{material}', 1,
                        '{target_principal}'
                    );
                    """,
                    check=False,
                )
                self.assertNotEqual(0, denied.returncode)
                self.assertIn("policy snapshot scope is not authorized", denied.stderr)
        direct_insert = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, policy_revision,
                 conflict_state_complete, wall_state_complete,
                 classification_state_complete, legal_hold_state_complete,
                 ownership_resolved, pending_export, preservation_required,
                 recorded_by_principal_id, recorded_at)
            VALUES
                ('93000000-0000-4000-8099-000000000001', '{tenant}', '{matter}',
                 '{"f" * 64}', true, true, true, true, true, false, false,
                 '{principal}', '2026-08-20T06:07:00Z');
            """,
            check=False,
        )
        sealed_update = self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.conflict_decisions
            SET version = 2 WHERE tenant_id = '{tenant}'
              AND matter_id = '{matter}' AND id = '{conflict_decision}';
            """,
            check=False,
        )
        self.assertNotEqual(0, direct_insert.returncode)
        self.assertNotEqual(0, sealed_update.returncode)
        self.assertIn("append-only", sealed_update.stderr)
        self.assertEqual(
            "t|f|f",
            self._psql(
                "postgres",
                """
                SELECT has_function_privilege(
                           'sklegal_test_alpha_one',
                           'sklegal_legal.material_policy_snapshot(uuid,uuid,uuid,bigint,uuid)',
                           'EXECUTE'),
                       EXISTS (
                           SELECT 1 FROM information_schema.columns
                           WHERE table_schema = 'sklegal_legal'
                             AND table_name = 'matter_policy_states'
                             AND column_name = 'profile_owner_principal_id'
                       ),
                       EXISTS (
                           SELECT 1 FROM information_schema.columns
                           WHERE table_schema = 'sklegal_legal'
                             AND table_name = 'matter_memberships'
                             AND column_name = 'profile_owner_principal_id'
                       );
                """,
            ).stdout.strip(),
        )


if __name__ == "__main__":
    unittest.main()
