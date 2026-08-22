"""Work product gate race persistence contract tests."""

from __future__ import annotations

import subprocess
import unittest

from tests.integration.persistence_contract_support import PersistenceContractBase


class PersistenceContract07WorkProductGateTests(PersistenceContractBase):
    def test_10_work_product_gate_race_and_draft_revision_boundaries(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        principal = value["principal_alpha_one"]
        digest = "7" * 64

        def setup_gated_candidate(suffix: int) -> tuple[str, str, str]:
            work_product = f"91000000-0000-4000-8000-{suffix:012d}"
            artifact = f"91000000-0000-4000-8001-{suffix:012d}"
            validation = f"91000000-0000-4000-8002-{suffix:012d}"
            self._psql(
                "sklegal_test_alpha_one",
                f"""
                BEGIN;
                INSERT INTO sklegal_legal.work_products
                    (id, tenant_id, matter_id, title, work_product_kind,
                     current_version_id, current_version_number,
                     current_content_sha256)
                VALUES ('{work_product}', '{tenant}', '{matter}',
                        'Synthetic race work product {suffix}', 'memo',
                        '{artifact}', 1, '{digest}');
                INSERT INTO sklegal_legal.work_product_versions
                    (id, tenant_id, matter_id, work_product_id, version_number,
                     content_sha256, source_artifact_id)
                VALUES ('{artifact}', '{tenant}', '{matter}', '{work_product}',
                        1, '{digest}',
                        '91000000-0000-4000-8003-{suffix:012d}');
                COMMIT;
                SELECT status FROM sklegal_legal.transition_work_product_version(
                    '{tenant}', '{matter}', '{artifact}', 1, 'frozen');
                BEGIN;
                INSERT INTO sklegal_legal.validations
                    (id, tenant_id, matter_id, subject_kind,
                     subject_artifact_id, subject_artifact_version,
                     subject_content_sha256, outcome, validator_principal_id,
                     validated_at, rationale)
                VALUES ('{validation}', '{tenant}', '{matter}',
                        'work_product_version', '{artifact}', 1, '{digest}',
                        'passed', '{principal}', clock_timestamp(),
                        'Synthetic race validation.');
                INSERT INTO sklegal_legal.validation_checks
                    (tenant_id, matter_id, validation_id, check_id)
                VALUES ('{tenant}', '{matter}', '{validation}',
                        'synthetic-race-check-{suffix}');
                COMMIT;
                SELECT status FROM sklegal_legal.transition_work_product(
                    '{tenant}', '{matter}', '{work_product}', 1, 'in_review');
                """,
            )
            return work_product, artifact, validation

        draft_work_product = "91000000-0000-4000-8000-000000000021"
        draft_artifact = "91000000-0000-4000-8001-000000000021"
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.work_products
                (id, tenant_id, matter_id, title, work_product_kind,
                 current_version_id, current_version_number,
                 current_content_sha256)
            VALUES ('{draft_work_product}', '{tenant}', '{matter}',
                    'Synthetic draft before edit', 'memo', '{draft_artifact}',
                    1, '{digest}');
            INSERT INTO sklegal_legal.work_product_versions
                (id, tenant_id, matter_id, work_product_id, version_number,
                 content_sha256, source_artifact_id)
            VALUES ('{draft_artifact}', '{tenant}', '{matter}',
                    '{draft_work_product}', 1, '{digest}',
                    '91000000-0000-4000-8003-000000000021');
            COMMIT;
            SELECT status || ':' || version || ':' || title
            FROM sklegal_legal.revise_work_product(
                '{tenant}', '{matter}', '{draft_work_product}', 1,
                'Synthetic draft after edit', 'memo', '{draft_artifact}', 1,
                '{digest}');
            """,
        )
        edited = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status || ':' || version || ':' || title
            FROM sklegal_legal.work_products
            WHERE id = '{draft_work_product}';
            """,
        )
        self.assertEqual("draft:2:Synthetic draft after edit", edited.stdout.strip())

        first_party = "91000000-0000-4000-8004-000000000001"
        second_party = "91000000-0000-4000-8004-000000000002"
        first_source = "91000000-0000-4000-8005-000000000001"
        second_source = "91000000-0000-4000-8005-000000000002"
        communication = "91000000-0000-4000-8006-000000000001"
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES
                ('{first_source}', '{tenant}', '{matter}', 'synthetic', 'v1',
                 '{"8" * 64}', 'synthetic:first-party', clock_timestamp()),
                ('{second_source}', '{tenant}', '{matter}', 'synthetic', 'v1',
                 '{"9" * 64}', 'synthetic:second-party', clock_timestamp());
            INSERT INTO sklegal_legal.parties
                (id, tenant_id, matter_id, display_name, party_kind,
                 source_reference_id)
            VALUES
                ('{first_party}', '{tenant}', '{matter}', 'Synthetic first',
                 'person', '{first_source}'),
                ('{second_party}', '{tenant}', '{matter}', 'Synthetic second',
                 'person', '{second_source}');
            SELECT status FROM sklegal_legal.create_communication(
                '{tenant}', '{matter}', '{communication}', 'internal',
                'calendar', 'Synthetic draft communication',
                ARRAY['{first_party}'::uuid], '{draft_artifact}', 1,
                '{digest}');
            COMMIT;
            SELECT status || ':' || version || ':' || subject
            FROM sklegal_legal.revise_communication(
                '{tenant}', '{matter}', '{communication}', 1, 'outbound',
                'email', 'Synthetic edited draft communication',
                '{draft_artifact}', 1, '{digest}',
                ARRAY['{second_party}'::uuid]);
            """,
        )
        communication_evidence = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT communication.status || ':' || communication.version || ':' ||
                   communication.subject || ':' || participant.party_id
            FROM sklegal_legal.communications AS communication
            JOIN sklegal_legal.communication_participants AS participant
              ON participant.tenant_id = communication.tenant_id
             AND participant.matter_id = communication.matter_id
             AND participant.communication_id = communication.id
            WHERE communication.id = '{communication}';
            """,
        )
        self.assertEqual(
            f"draft:2:Synthetic edited draft communication:{second_party}",
            communication_evidence.stdout.strip(),
        )

        gate_first_work_product, gate_first_artifact, gate_first_validation = (
            setup_gated_candidate(101)
        )
        gate_first_sql = f"""
            BEGIN;
            SET LOCAL application_name = 'sklegal-s102-gate-first';
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{gate_first_work_product}', 2,
                'validated', '{gate_first_validation}', NULL);
            SELECT pg_sleep(1.0);
            COMMIT;
        """
        gate_first = subprocess.Popen(
            [
                *self._psql_command("sklegal_test_alpha_one"),
                "--command",
                gate_first_sql,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._wait_for_sleep("sklegal-s102-gate-first", gate_first)
        stale_supersede = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.transition_work_product_version(
                '{tenant}', '{matter}', '{gate_first_artifact}', 2,
                'superseded');
            """,
            check=False,
        )
        gate_stdout, gate_stderr = gate_first.communicate(timeout=10)
        self.assertEqual(0, gate_first.returncode, gate_stderr)
        self.assertIn("validated", gate_stdout)
        self.assertNotEqual(0, stale_supersede.returncode)
        self.assertIn("gated work product", stale_supersede.stderr)

        supersede_first_work_product, supersede_first_artifact, supersede_validation = (
            setup_gated_candidate(102)
        )
        supersede_first_sql = f"""
            BEGIN;
            SET LOCAL application_name = 'sklegal-s102-supersede-first';
            SELECT status FROM sklegal_legal.transition_work_product_version(
                '{tenant}', '{matter}', '{supersede_first_artifact}', 2,
                'superseded');
            SELECT pg_sleep(1.0);
            COMMIT;
        """
        supersede_first = subprocess.Popen(
            [
                *self._psql_command("sklegal_test_alpha_one"),
                "--command",
                supersede_first_sql,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._wait_for_sleep("sklegal-s102-supersede-first", supersede_first)
        stale_gate = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{supersede_first_work_product}', 2,
                'validated', '{supersede_validation}', NULL);
            """,
            check=False,
        )
        supersede_stdout, supersede_stderr = supersede_first.communicate(timeout=10)
        self.assertEqual(0, supersede_first.returncode, supersede_stderr)
        self.assertIn("superseded", supersede_stdout)
        self.assertNotEqual(0, stale_gate.returncode)
        self.assertIn("remain frozen", stale_gate.stderr)


if __name__ == "__main__":
    unittest.main()
