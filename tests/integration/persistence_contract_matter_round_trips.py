"""Scalar boundary and full matter round-trip persistence contract tests."""

from __future__ import annotations

import json
import subprocess
import time
import unittest
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from sklegal_domain import (
    EffectiveInterval,
    Forum,
)

from tests.integration.persistence_contract_support import PersistenceContractBase


class PersistenceContract04MatterRoundTripTests(PersistenceContractBase):
    def test_07_uuid_scalar_and_effective_interval_boundaries(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        missing_uuid_triggers = self._psql(
            "postgres",
            """
            SELECT count(*)
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname IN (
                'sklegal_identity', 'sklegal_legal', 'sklegal_integrations',
                'sklegal_workflow', 'sklegal_audit'
            ) AND relation.relkind = 'r'
              AND EXISTS (
                  SELECT 1 FROM pg_attribute AS attribute
                  WHERE attribute.attrelid = relation.oid
                    AND attribute.atttypid = 'uuid'::regtype
                    AND attribute.attnum > 0 AND NOT attribute.attisdropped
              )
              AND NOT EXISTS (
                  SELECT 1 FROM pg_trigger AS trigger_record
                  WHERE trigger_record.tgrelid = relation.oid
                    AND trigger_record.tgname = 'domain_id_non_nil'
                    AND NOT trigger_record.tgisinternal
              );
            """,
        )
        self.assertEqual("0", missing_uuid_triggers.stdout.strip())
        for statement in (
            f"""
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('00000000-0000-0000-0000-000000000000', '{tenant}',
                    '{matter}', 'Nil forum', 'Synthetic', 'other');
            """,
            f"""
            INSERT INTO sklegal_legal.tasks
                (id, tenant_id, matter_id, title, description,
                 assigned_principal_id)
            VALUES ('72000000-0000-4000-8000-000000000001', '{tenant}',
                    '{matter}', 'Nil assignee', 'Synthetic',
                    '00000000-0000-0000-0000-000000000000');
            """,
        ):
            denied_nil = self._psql("sklegal_test_alpha_one", statement, check=False)
            self.assertNotEqual(0, denied_nil.returncode)
            self.assertIn("nil UUID", denied_nil.stderr)
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('72000000-0000-4000-8000-000000000002', '{tenant}',
                    '{matter}', '{"x" * 512}', 'Synthetic', 'other');
            """,
        )
        too_long = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('72000000-0000-4000-8000-000000000003', '{tenant}',
                    '{matter}', '{"x" * 513}', 'Synthetic', 'other');
            """,
            check=False,
        )
        self.assertNotEqual(0, too_long.returncode)
        self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.engagements
                (id, tenant_id, client_id, title, scope, valid_to)
            VALUES ('72000000-0000-4000-8000-000000000004', '{tenant}',
                    '{value["client_alpha"]}', 'End-only interval', 'Synthetic',
                    '2026-12-01T00:00:00Z');
            """,
        )
        empty_interval = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.engagements
                (id, tenant_id, client_id, title, scope, valid_from, valid_to)
            VALUES ('72000000-0000-4000-8000-000000000005', '{tenant}',
                    '{value["client_alpha"]}', 'Empty interval', 'Synthetic',
                    '2026-12-01T00:00:00Z', '2026-12-01T00:00:00Z');
            """,
            check=False,
        )
        self.assertNotEqual(0, empty_interval.returncode)
        boundary_at = datetime.fromisoformat("2026-08-20T12:00:00+00:00")
        domain_forum = {
            "id": UUID("72000000-0000-4000-8000-000000000002"),
            "tenant_id": UUID(tenant),
            "matter_id": UUID(matter),
            "created_at": boundary_at,
            "updated_at": boundary_at,
            "name": "x" * 512,
            "jurisdiction": "Synthetic",
            "forum_kind": "other",
        }
        Forum.model_validate(domain_forum, strict=True)
        with self.assertRaises(ValidationError):
            Forum.model_validate({**domain_forum, "name": "x" * 513}, strict=True)
        EffectiveInterval(valid_to=boundary_at)
        with self.assertRaises(ValidationError):
            EffectiveInterval(valid_from=boundary_at, valid_to=boundary_at)

    def test_08_full_synthetic_legal_record_round_trip(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        principal = value["principal_alpha_one"]
        digest = "8" * 64
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES ('80000000-0000-4000-8000-000000000001', '{tenant}',
                    '{matter}', 'synthetic', 'v1', '{digest}',
                    'synthetic/round-trip', clock_timestamp());
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind,
                 source_reference_id)
            VALUES ('80000000-0000-4000-8000-000000000002', '{tenant}',
                    '{matter}', 'Round Trip Forum', 'Synthetic', 'court',
                    '80000000-0000-4000-8000-000000000001');
            INSERT INTO sklegal_legal.proceedings
                (id, tenant_id, matter_id, title, forum_id, status)
            VALUES ('80000000-0000-4000-8000-000000000003', '{tenant}',
                    '{matter}', 'Round Trip Proceeding',
                    '80000000-0000-4000-8000-000000000002', 'proposed');
            INSERT INTO sklegal_legal.parties
                (id, tenant_id, matter_id, display_name, party_kind,
                 source_reference_id)
            VALUES ('80000000-0000-4000-8000-000000000004', '{tenant}',
                    '{matter}', 'Round Trip Party', 'person',
                    '80000000-0000-4000-8000-000000000001');
            INSERT INTO sklegal_legal.party_roles
                (id, tenant_id, matter_id, party_id, proceeding_id, role,
                 source_reference_id)
            VALUES ('80000000-0000-4000-8000-000000000005', '{tenant}',
                    '{matter}', '80000000-0000-4000-8000-000000000004',
                    '80000000-0000-4000-8000-000000000003', 'client',
                    '80000000-0000-4000-8000-000000000001');
            INSERT INTO sklegal_legal.matter_events
                (id, tenant_id, matter_id, event_type, description, occurred_at,
                 observed_at, source_reference_id, status)
            VALUES ('80000000-0000-4000-8000-000000000006', '{tenant}',
                    '{matter}', 'round_trip', 'Synthetic event',
                    clock_timestamp(), clock_timestamp(),
                    '80000000-0000-4000-8000-000000000001', 'proposed');
            INSERT INTO sklegal_legal.legal_transactions
                (id, tenant_id, matter_id, title, description)
            VALUES ('80000000-0000-4000-8000-000000000007', '{tenant}',
                    '{matter}', 'Round Trip Transaction', 'Synthetic transaction');
            INSERT INTO sklegal_legal.transaction_party_roles
                (tenant_id, matter_id, transaction_id, party_role_id)
            VALUES ('{tenant}', '{matter}',
                    '80000000-0000-4000-8000-000000000007',
                    '80000000-0000-4000-8000-000000000005');
            INSERT INTO sklegal_legal.transaction_source_references
                (tenant_id, matter_id, transaction_id, source_reference_id)
            VALUES ('{tenant}', '{matter}',
                    '80000000-0000-4000-8000-000000000007',
                    '80000000-0000-4000-8000-000000000001');
            INSERT INTO sklegal_legal.fact_assertions
                (id, tenant_id, matter_id, subject_ref, predicate, value_type,
                 asserted_value, source_reference_id, source_locator, observed_at)
            VALUES ('80000000-0000-4000-8000-000000000008', '{tenant}',
                    '{matter}', '80000000-0000-4000-8000-000000000007',
                    'amount', 'integer', '7'::jsonb,
                    '80000000-0000-4000-8000-000000000001', 'line:7',
                    clock_timestamp());
            INSERT INTO sklegal_legal.evidence_items
                (id, tenant_id, matter_id, title, media_type, content_sha256,
                 source_reference_id, status)
            VALUES ('80000000-0000-4000-8000-000000000009', '{tenant}',
                    '{matter}', 'Round Trip Evidence', 'text/plain', '{digest}',
                    '80000000-0000-4000-8000-000000000001', 'proposed');
            INSERT INTO sklegal_legal.custody_events
                (id, tenant_id, matter_id, evidence_item_id, action, custodian_id,
                 occurred_at, source_reference_id)
            VALUES ('80000000-0000-4000-8000-000000000010', '{tenant}',
                    '{matter}', '80000000-0000-4000-8000-000000000009',
                    'acquired', '{principal}', clock_timestamp(),
                    '80000000-0000-4000-8000-000000000001');
            INSERT INTO sklegal_legal.authority_identities
                (tenant_id, matter_id, id)
            VALUES ('{tenant}', '{matter}',
                    '80000000-0000-4000-8000-000000000011');
            INSERT INTO sklegal_legal.authorities
                (id, tenant_id, matter_id, title, citation, jurisdiction,
                 authority_kind, source_reference_id, status, version)
            VALUES ('80000000-0000-4000-8000-000000000011', '{tenant}',
                    '{matter}', 'Round Trip Authority', 'Synthetic 8', 'Synthetic',
                    'administrative_material',
                    '80000000-0000-4000-8000-000000000001', 'proposed', 1);
            INSERT INTO sklegal_legal.issues
                (id, tenant_id, matter_id, question)
            VALUES ('80000000-0000-4000-8000-000000000012', '{tenant}',
                    '{matter}', 'What is the synthetic issue?');
            INSERT INTO sklegal_legal.claims
                (id, tenant_id, matter_id, issue_id, label, statement)
            VALUES ('80000000-0000-4000-8000-000000000013', '{tenant}',
                    '{matter}', '80000000-0000-4000-8000-000000000012',
                    'Synthetic claim', 'Synthetic claim statement.');
            INSERT INTO sklegal_legal.defenses
                (id, tenant_id, matter_id, issue_id, label, statement)
            VALUES ('80000000-0000-4000-8000-000000000014', '{tenant}',
                    '{matter}', '80000000-0000-4000-8000-000000000012',
                    'Synthetic defense', 'Synthetic defense statement.');
            INSERT INTO sklegal_legal.elements
                (id, tenant_id, matter_id, theory_kind, claim_id, description)
            VALUES
                ('80000000-0000-4000-8000-000000000015', '{tenant}', '{matter}',
                 'claim', '80000000-0000-4000-8000-000000000013',
                 'Synthetic claim element.'),
                ('80000000-0000-4000-8000-000000000016', '{tenant}', '{matter}',
                 'defense', '80000000-0000-4000-8000-000000000014',
                 'Synthetic defense element.');
            INSERT INTO sklegal_legal.element_evidence
                (tenant_id, matter_id, element_id, evidence_item_id)
            VALUES ('{tenant}', '{matter}',
                    '80000000-0000-4000-8000-000000000015',
                    '80000000-0000-4000-8000-000000000009');
            INSERT INTO sklegal_legal.theory_evidence
                (tenant_id, matter_id, theory_kind, theory_id, evidence_item_id)
            VALUES ('{tenant}', '{matter}', 'defense',
                    '80000000-0000-4000-8000-000000000014',
                    '80000000-0000-4000-8000-000000000009');
            INSERT INTO sklegal_legal.theory_authorities
                (tenant_id, matter_id, theory_kind, theory_id, authority_id)
            VALUES ('{tenant}', '{matter}', 'claim',
                    '80000000-0000-4000-8000-000000000013',
                    '80000000-0000-4000-8000-000000000011');
            INSERT INTO sklegal_legal.remedies
                (id, tenant_id, matter_id, claim_id, description)
            VALUES ('80000000-0000-4000-8000-000000000017', '{tenant}',
                    '{matter}', '80000000-0000-4000-8000-000000000013',
                    'Synthetic remedy.');
            INSERT INTO sklegal_legal.remedy_authorities
                (tenant_id, matter_id, remedy_id, authority_id)
            VALUES ('{tenant}', '{matter}',
                    '80000000-0000-4000-8000-000000000017',
                    '80000000-0000-4000-8000-000000000011');
            INSERT INTO sklegal_legal.deadline_calculations
                (id, tenant_id, matter_id, trigger_fact_id, calculation_rule,
                 candidate_due_at, calculated_at, calculation_version)
            VALUES ('80000000-0000-4000-8000-000000000018', '{tenant}',
                    '{matter}', '80000000-0000-4000-8000-000000000008',
                    'Synthetic plus 7 days', clock_timestamp() + interval '7 days',
                    clock_timestamp(), 'v1');
            INSERT INTO sklegal_legal.deadline_calculation_sources
                (tenant_id, matter_id, calculation_id, source_reference_id)
            VALUES ('{tenant}', '{matter}',
                    '80000000-0000-4000-8000-000000000018',
                    '80000000-0000-4000-8000-000000000001');
            INSERT INTO sklegal_legal.deadlines
                (id, tenant_id, matter_id, title, candidate_due_at)
            VALUES ('80000000-0000-4000-8000-000000000019', '{tenant}',
                    '{matter}', 'Synthetic candidate deadline', NULL);
            INSERT INTO sklegal_legal.tasks
                (id, tenant_id, matter_id, title, description)
            VALUES ('80000000-0000-4000-8000-000000000020', '{tenant}',
                    '{matter}', 'Synthetic task', 'Synthetic task description.');
            SELECT status FROM sklegal_legal.create_communication(
                '{tenant}', '{matter}',
                '80000000-0000-4000-8000-000000000021',
                'internal', 'calendar', 'Synthetic communication',
                ARRAY['80000000-0000-4000-8000-000000000004'::uuid]);
            COMMIT;
            """,
        )
        restored = self._psql(
            "sklegal_test_alpha_one",
            """
            SELECT jsonb_build_object(
                'defense', (SELECT label FROM sklegal_legal.defenses
                    WHERE id = '80000000-0000-4000-8000-000000000014'),
                'element_count', (SELECT count(*) FROM sklegal_legal.elements
                    WHERE id IN ('80000000-0000-4000-8000-000000000015',
                                 '80000000-0000-4000-8000-000000000016')),
                'remedy', (SELECT description FROM sklegal_legal.remedies
                    WHERE id = '80000000-0000-4000-8000-000000000017'),
                'deadline_candidate_unknown', (SELECT candidate_due_at IS NULL
                    FROM sklegal_legal.deadlines
                    WHERE id = '80000000-0000-4000-8000-000000000019'),
                'communication', (SELECT direction || ':' || channel
                    FROM sklegal_legal.communications
                    WHERE id = '80000000-0000-4000-8000-000000000021')
            );
            """,
        )
        payload = json.loads(restored.stdout)
        self.assertEqual("Synthetic defense", payload["defense"])
        self.assertEqual(2, payload["element_count"])
        self.assertTrue(payload["deadline_candidate_unknown"])
        self.assertEqual("internal:calendar", payload["communication"])

    def test_09_optimistic_conflict_and_monotonic_long_transaction_time(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        work_product_id = "89000000-0000-4000-8000-000000000001"
        version_id = "89000000-0000-4000-8000-000000000002"
        digest = "9" * 64
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.work_products
                (id, tenant_id, matter_id, title, work_product_kind,
                 current_version_id, current_version_number,
                 current_content_sha256)
            VALUES ('{work_product_id}', '{tenant}', '{matter}',
                    'Synthetic concurrency work product', 'memo', '{version_id}',
                    1, '{digest}');
            INSERT INTO sklegal_legal.work_product_versions
                (id, tenant_id, matter_id, work_product_id, version_number,
                 content_sha256, source_artifact_id)
            VALUES ('{version_id}', '{tenant}', '{matter}', '{work_product_id}',
                    1, '{digest}',
                    '89000000-0000-4000-8000-000000000003');
            COMMIT;
            """,
        )
        first_sql = f"""
            BEGIN;
            SET LOCAL application_name = 'sklegal-s102-first-writer';
            SELECT status FROM sklegal_legal.transition_work_product_version(
                '{tenant}', '{matter}', '{version_id}', 1, 'frozen');
            SELECT pg_sleep(1.0);
            COMMIT;
        """
        first = subprocess.Popen(
            [*self._psql_command("sklegal_test_alpha_one"), "--command", first_sql],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(50):
            waiting = self._psql(
                "postgres",
                """
                SELECT count(*) FROM pg_stat_activity
                WHERE application_name = 'sklegal-s102-first-writer'
                  AND wait_event = 'PgSleep';
                """,
            )
            if waiting.stdout.strip() == "1":
                break
            time.sleep(0.02)
        else:
            first.kill()
            self.fail("first writer did not reach deterministic sleep checkpoint")
        second = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.transition_work_product_version(
                '{tenant}', '{matter}', '{version_id}', 1, 'frozen');
            """,
            check=False,
        )
        stdout, stderr = first.communicate(timeout=10)
        self.assertEqual(0, first.returncode, stderr)
        self.assertIn("frozen", stdout)
        self.assertNotEqual(0, second.returncode)
        self.assertIn("version conflict", second.stderr)
        monotonic = self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            SELECT pg_sleep(0.05);
            SELECT (transitioned).updated_at > transaction_timestamp()
            FROM (
                SELECT sklegal_legal.transition_work_product_version(
                    '{tenant}', '{matter}', '{version_id}', 2, 'superseded'
                ) AS transitioned
            ) AS result;
            COMMIT;
            """,
        )
        self.assertIn("t", monotonic.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
