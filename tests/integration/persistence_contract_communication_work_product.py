"""Communication and work product persistence contract tests."""

from __future__ import annotations

import subprocess
import unittest

from tests.integration.persistence_contract_support import PersistenceContractBase


class PersistenceContract06CommunicationWorkProductTests(PersistenceContractBase):
    def test_10_controlled_communication_participants_are_atomic_and_sealed(
        self,
    ) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        role = "sklegal_test_alpha_one"
        first_party = "92000000-0000-4000-8000-000000000001"
        second_party = "92000000-0000-4000-8000-000000000002"
        first_source = "92000000-0000-4000-8001-000000000001"
        second_source = "92000000-0000-4000-8001-000000000002"
        communication = "92000000-0000-4000-8002-000000000001"
        rolled_back_communication = "92000000-0000-4000-8002-000000000002"
        self._psql(
            role,
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES
                ('{first_source}', '{tenant}', '{matter}', 'synthetic', 'v1',
                 '{"a" * 64}', 'synthetic:sealed-first', clock_timestamp()),
                ('{second_source}', '{tenant}', '{matter}', 'synthetic', 'v1',
                 '{"b" * 64}', 'synthetic:sealed-second', clock_timestamp());
            INSERT INTO sklegal_legal.parties
                (id, tenant_id, matter_id, display_name, party_kind,
                 source_reference_id)
            VALUES
                ('{first_party}', '{tenant}', '{matter}', 'Synthetic sealed first',
                 'person', '{first_source}'),
                ('{second_party}', '{tenant}', '{matter}', 'Synthetic sealed second',
                 'person', '{second_source}');
            COMMIT;
            SELECT status FROM sklegal_legal.create_communication(
                '{tenant}', '{matter}', '{communication}', 'internal',
                'calendar', 'Synthetic sealed communication',
                ARRAY['{first_party}'::uuid]);
            """,
        )
        self.assertEqual(
            "f|f|t|f|t|t",
            self._psql(
                "postgres",
                f"""
                SELECT has_table_privilege('{role}', 'sklegal_legal.communications', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.communication_participants', 'INSERT'),
                       has_function_privilege('{role}',
                           'sklegal_legal.create_communication(uuid,uuid,uuid,text,text,text,uuid[],uuid,bigint,text,sklegal_legal.data_classification,sklegal_legal.record_completeness,timestamptz,timestamptz)',
                           'EXECUTE'),
                       has_function_privilege('{role}',
                           'sklegal_legal.lock_exact_work_product_version(uuid,uuid,uuid,bigint,text)',
                           'EXECUTE'),
                       to_regprocedure('sklegal_legal.advance_new_communication_relation_owner()') IS NULL,
                       to_regprocedure('sklegal_legal.seal_communication_participant_creation()') IS NULL;
                """,
            ).stdout.strip(),
        )

        direct_parent = self._psql(
            role,
            f"""
            INSERT INTO sklegal_legal.communications
                (id, tenant_id, matter_id, direction, channel, subject)
            VALUES ('92000000-0000-4000-8002-000000000099', '{tenant}',
                    '{matter}', 'internal', 'calendar', 'Direct denied');
            """,
            check=False,
        )
        direct_participant = self._psql(
            role,
            f"""
            INSERT INTO sklegal_legal.communication_participants
                (tenant_id, matter_id, communication_id, party_id)
            VALUES ('{tenant}', '{matter}', '{communication}', '{second_party}');
            """,
            check=False,
        )
        self.assertNotEqual(0, direct_parent.returncode)
        self.assertNotEqual(0, direct_participant.returncode)

        revised = self._psql(
            role,
            f"""
            SELECT status || ':' || version FROM sklegal_legal.revise_communication(
                '{tenant}', '{matter}', '{communication}', 1, 'outbound',
                'email', 'Synthetic sealed revision', NULL, NULL, NULL,
                ARRAY['{second_party}'::uuid]);
            """,
        )
        self.assertEqual("draft:2", revised.stdout.strip())

        same_transaction_bypass = self._psql(
            role,
            f"""
            BEGIN;
            SELECT version FROM sklegal_legal.revise_communication(
                '{tenant}', '{matter}', '{communication}', 2, 'internal',
                'calendar', 'Synthetic bypass attempt', NULL, NULL, NULL,
                ARRAY['{first_party}'::uuid]);
            INSERT INTO sklegal_legal.communication_participants
                (tenant_id, matter_id, communication_id, party_id)
            VALUES ('{tenant}', '{matter}', '{communication}', '{second_party}');
            COMMIT;
            """,
            check=False,
        )
        self.assertNotEqual(0, same_transaction_bypass.returncode)

        invalid_replacement = self._psql(
            role,
            f"""
            SELECT version FROM sklegal_legal.revise_communication(
                '{tenant}', '{matter}', '{communication}', 2, 'internal',
                'calendar', 'Synthetic rollback attempt', NULL, NULL, NULL,
                ARRAY['92000000-0000-4000-8009-000000000099'::uuid]);
            """,
            check=False,
        )
        self.assertNotEqual(0, invalid_replacement.returncode)
        unchanged = self._psql(
            role,
            f"""
            SELECT communication.version || ':' || communication.subject || ':' ||
                   string_agg(participant.party_id::text, ',' ORDER BY participant.party_id)
            FROM sklegal_legal.communications AS communication
            JOIN sklegal_legal.communication_participants AS participant
              ON participant.tenant_id = communication.tenant_id
             AND participant.matter_id = communication.matter_id
             AND participant.communication_id = communication.id
            WHERE communication.id = '{communication}'
            GROUP BY communication.version, communication.subject;
            """,
        )
        self.assertEqual(
            f"2:Synthetic sealed revision:{second_party}", unchanged.stdout.strip()
        )

        duplicate_create = self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.create_communication(
                '{tenant}', '{matter}', '{rolled_back_communication}', 'internal',
                'calendar', 'Synthetic duplicate rollback',
                ARRAY['{first_party}'::uuid, '{first_party}'::uuid]);
            """,
            check=False,
        )
        self.assertNotEqual(0, duplicate_create.returncode)
        self.assertEqual(
            "0",
            self._psql(
                role,
                f"SELECT count(*) FROM sklegal_legal.communications "
                f"WHERE id = '{rolled_back_communication}';",
            ).stdout.strip(),
        )

        first_revision_sql = f"""
            BEGIN;
            SET LOCAL application_name = 'sklegal-s102-communication-revision-first';
            SELECT version FROM sklegal_legal.revise_communication(
                '{tenant}', '{matter}', '{communication}', 2, 'internal',
                'calendar', 'Synthetic concurrent winner', NULL, NULL, NULL,
                ARRAY['{first_party}'::uuid]);
            SELECT pg_sleep(1.0);
            COMMIT;
        """
        first_revision = subprocess.Popen(
            [*self._psql_command(role), "--command", first_revision_sql],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._wait_for_sleep(
            "sklegal-s102-communication-revision-first", first_revision
        )
        stale_revision = self._psql(
            role,
            f"""
            SELECT version FROM sklegal_legal.revise_communication(
                '{tenant}', '{matter}', '{communication}', 2, 'outbound',
                'email', 'Synthetic concurrent loser', NULL, NULL, NULL,
                ARRAY['{second_party}'::uuid]);
            """,
            check=False,
        )
        winner_stdout, winner_stderr = first_revision.communicate(timeout=10)
        self.assertEqual(0, first_revision.returncode, winner_stderr)
        self.assertIn("3", winner_stdout)
        self.assertNotEqual(0, stale_revision.returncode)
        self.assertIn("version conflict", stale_revision.stderr)
        final = self._psql(
            role,
            f"""
            SELECT communication.version || ':' || communication.subject || ':' ||
                   participant.party_id
            FROM sklegal_legal.communications AS communication
            JOIN sklegal_legal.communication_participants AS participant
              ON participant.tenant_id = communication.tenant_id
             AND participant.matter_id = communication.matter_id
             AND participant.communication_id = communication.id
            WHERE communication.id = '{communication}';
            """,
        )
        self.assertEqual(
            f"3:Synthetic concurrent winner:{first_party}", final.stdout.strip()
        )

    def test_10_controlled_work_product_approval_and_execution_paths(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        principal = value["principal_alpha_one"]
        work_product = "90000000-0000-4000-8000-000000000001"
        artifact = "90000000-0000-4000-8000-000000000002"
        validation = "90000000-0000-4000-8000-000000000003"
        approval = "90000000-0000-4000-8000-000000000004"
        participant = "90000000-0000-4000-8000-000000000050"
        participant_source = "90000000-0000-4000-8000-000000000051"
        artifact_sha = "a" * 64
        destination_sha = "b" * 64
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES ('{participant_source}', '{tenant}', '{matter}',
                    'synthetic', 'v1', '{"e" * 64}', 'synthetic:participant',
                    clock_timestamp());
            INSERT INTO sklegal_legal.parties
                (id, tenant_id, matter_id, display_name, party_kind,
                 source_reference_id)
            VALUES ('{participant}', '{tenant}', '{matter}',
                    'Synthetic communication participant', 'person',
                    '{participant_source}');
            INSERT INTO sklegal_legal.work_products
                (id, tenant_id, matter_id, title, work_product_kind,
                 current_version_id, current_version_number,
                 current_content_sha256)
            VALUES ('{work_product}', '{tenant}', '{matter}',
                    'Synthetic governed work product', 'memo', '{artifact}', 1,
                    '{artifact_sha}');
            INSERT INTO sklegal_legal.work_product_versions
                (id, tenant_id, matter_id, work_product_id, version_number,
                 content_sha256, source_artifact_id)
            VALUES ('{artifact}', '{tenant}', '{matter}', '{work_product}', 1,
                    '{artifact_sha}',
                    '90000000-0000-4000-8000-000000000099');
            COMMIT;
            SELECT status FROM sklegal_legal.transition_work_product_version(
                '{tenant}', '{matter}', '{artifact}', 1, 'frozen');
            BEGIN;
            INSERT INTO sklegal_legal.validations
                (id, tenant_id, matter_id, subject_kind, subject_artifact_id,
                 subject_artifact_version, subject_content_sha256, outcome,
                 validator_principal_id, validated_at, rationale)
            VALUES ('{validation}', '{tenant}', '{matter}',
                    'work_product_version', '{artifact}', 1,
                    '{artifact_sha}', 'passed', '{principal}', clock_timestamp(),
                    'Synthetic validation passed.');
            INSERT INTO sklegal_legal.validation_checks
                (tenant_id, matter_id, validation_id, check_id)
            VALUES ('{tenant}', '{matter}', '{validation}',
                    'synthetic-artifact-check');
            COMMIT;
            INSERT INTO sklegal_legal.approvals
                (id, tenant_id, matter_id, subject_artifact_id,
                 subject_artifact_version, subject_content_sha256)
            VALUES ('{approval}', '{tenant}', '{matter}', '{artifact}', 1,
                    '{artifact_sha}');
            SELECT status FROM sklegal_legal.transition_approval(
                '{tenant}', '{matter}', '{approval}', 1, 'approved',
                'Synthetic exact-version approval.');
            """,
        )
        frozen_payload_change = self._psql(
            "sklegal_test_alpha_one",
            f"""
            UPDATE sklegal_legal.work_product_versions
            SET content_sha256 = '{"c" * 64}', version = version + 1
            WHERE id = '{artifact}';
            """,
            check=False,
        )
        terminal_approval = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.approvals
                (id, tenant_id, matter_id, subject_artifact_id,
                 subject_artifact_version, subject_content_sha256, status,
                 reviewer_principal_id, decided_at, rationale)
            VALUES ('90000000-0000-4000-8000-000000000005', '{tenant}',
                    '{matter}', '{artifact}', 1, '{artifact_sha}', 'approved',
                    '{principal}', clock_timestamp(), 'Bypass attempt');
            """,
            check=False,
        )
        self.assertNotEqual(0, frozen_payload_change.returncode)
        self.assertNotEqual(0, terminal_approval.returncode)

        rejected_approval = "90000000-0000-4000-8000-000000000006"
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.approvals
                (id, tenant_id, matter_id, subject_artifact_id,
                 subject_artifact_version, subject_content_sha256)
            VALUES ('{rejected_approval}', '{tenant}', '{matter}', '{artifact}',
                    1, '{artifact_sha}');
            SELECT status FROM sklegal_legal.transition_approval(
                '{tenant}', '{matter}', '{rejected_approval}', 1, 'rejected',
                'Synthetic rejection evidence.');
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{work_product}', 1, 'in_review');
            """,
        )
        failed_work_product_gate = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{work_product}', 2, 'validated',
                '90000000-0000-4000-8000-000000000098', NULL);
            """,
            check=False,
        )
        self.assertNotEqual(0, failed_work_product_gate.returncode)
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{work_product}', 2, 'validated',
                '{validation}', NULL);
            """,
        )
        rejected_work_product_gate = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{work_product}', 3, 'approved', NULL,
                '{rejected_approval}');
            """,
            check=False,
        )
        self.assertNotEqual(0, rejected_work_product_gate.returncode)
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{work_product}', 3, 'approved', NULL,
                '{approval}');
            SELECT status FROM sklegal_legal.revise_work_product(
                '{tenant}', '{matter}', '{work_product}', 4,
                'Synthetic governed work product revision', 'memo',
                '{artifact}', 1, '{artifact_sha}');
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{work_product}', 5, 'validated',
                '{validation}', NULL);
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{work_product}', 6, 'approved', NULL,
                '{approval}');
            """,
        )
        terminal_work_product = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.work_products
                (id, tenant_id, matter_id, title, work_product_kind,
                 current_version_id, current_version_number,
                 current_content_sha256, validation_result_id, approval_id,
                 status)
            VALUES ('90000000-0000-4000-8000-000000000007', '{tenant}',
                    '{matter}', 'Terminal bypass', 'memo', '{artifact}', 1,
                    '{artifact_sha}', '{validation}', '{approval}', 'approved');
            """,
            check=False,
        )
        self.assertNotEqual(0, terminal_work_product.returncode)

        def insert_execution(suffix: int) -> str:
            execution_id = f"90000000-0000-4000-8000-{suffix:012d}"
            self._psql(
                "sklegal_test_alpha_one",
                f"""
                INSERT INTO sklegal_legal.executions
                    (id, tenant_id, matter_id, subject_artifact_id,
                     subject_artifact_version, subject_content_sha256,
                     destination_sha256, idempotency_key)
                VALUES ('{execution_id}', '{tenant}', '{matter}', '{artifact}', 1,
                        '{artifact_sha}', '{destination_sha}',
                        'synthetic-execution-{suffix}');
                """,
            )
            return execution_id

        def transition(
            execution_id: str,
            version: int,
            status: str,
            *,
            validation_id: str | None = None,
            approval_id: str | None = None,
            receipt: bool = False,
            check: bool = True,
        ) -> subprocess.CompletedProcess[str]:
            receipt_arguments = (
                ", '90000000-0000-4000-8000-000000000090', "
                "'synthetic-connector', 'synthetic-receipt-90', "
                "clock_timestamp(), clock_timestamp()"
                if receipt
                else ""
            )
            return self._psql(
                "sklegal_test_alpha_one",
                f"""
                SELECT status FROM sklegal_legal.transition_execution(
                    '{tenant}', '{matter}', '{execution_id}', {version},
                    '{status}', 'corr-{execution_id[-8:]}-{version}',
                    {f"'{validation_id}'" if validation_id else "NULL"},
                    {f"'{approval_id}'" if approval_id else "NULL"}
                    {receipt_arguments});
                """,
                check=check,
            )

        happy = insert_execution(10)
        communication = "90000000-0000-4000-8000-000000000040"
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.create_communication(
                '{tenant}', '{matter}', '{communication}', 'outbound',
                'email', 'Synthetic governed communication',
                ARRAY['{participant}'::uuid], '{artifact}', 1,
                '{artifact_sha}');
            """,
        )
        terminal_communication = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.communications
                (id, tenant_id, matter_id, direction, channel, subject, status)
            VALUES ('90000000-0000-4000-8000-000000000041', '{tenant}',
                    '{matter}', 'internal', 'calendar', 'Terminal bypass',
                    'cancelled');
            """,
            check=False,
        )
        no_participants = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.create_communication(
                '{tenant}', '{matter}',
                '90000000-0000-4000-8000-000000000042',
                'internal', 'calendar', 'Missing participant', ARRAY[]::uuid[]);
            """,
            check=False,
        )
        self.assertNotEqual(0, terminal_communication.returncode)
        self.assertNotEqual(0, no_participants.returncode)

        def transition_communication(
            version: int,
            status: str,
            *,
            validation_id: str | None = None,
            approval_id: str | None = None,
            execution_id: str | None = None,
            check: bool = True,
        ) -> subprocess.CompletedProcess[str]:
            return self._psql(
                "sklegal_test_alpha_one",
                f"""
                SELECT status FROM sklegal_legal.transition_communication(
                    '{tenant}', '{matter}', '{communication}', {version},
                    '{status}',
                    {f"'{validation_id}'" if validation_id else "NULL"},
                    {f"'{approval_id}'" if approval_id else "NULL"},
                    {f"'{destination_sha}'" if execution_id else "NULL"},
                    {f"'{execution_id}'" if execution_id else "NULL"});
                """,
                check=check,
            )

        wrong_communication_gate = transition_communication(
            1,
            "validated",
            validation_id="90000000-0000-4000-8000-000000000098",
            check=False,
        )
        self.assertNotEqual(0, wrong_communication_gate.returncode)
        transition_communication(1, "validated", validation_id=validation)
        transition_communication(2, "approved", approval_id=approval)
        communication_reset = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status || ':' || version || ':' || subject
            FROM sklegal_legal.revise_communication(
                '{tenant}', '{matter}', '{communication}', 3, 'outbound',
                'email', 'Synthetic revised governed communication',
                '{artifact}', 1, '{artifact_sha}',
                ARRAY['{participant}'::uuid]);
            """,
        )
        self.assertEqual(
            "draft:4:Synthetic revised governed communication",
            communication_reset.stdout.strip(),
        )
        transition_communication(4, "validated", validation_id=validation)
        transition_communication(5, "approved", approval_id=approval)
        skipped = transition(happy, 1, "approved", approval_id=approval, check=False)
        self.assertNotEqual(0, skipped.returncode)
        transition(happy, 1, "validated", validation_id=validation)
        changed_gate = transition(
            happy,
            2,
            "approved",
            validation_id="90000000-0000-4000-8000-000000000098",
            approval_id=approval,
            check=False,
        )
        self.assertNotEqual(0, changed_gate.returncode)
        transition(happy, 2, "approved", approval_id=approval)
        transition(happy, 3, "queued")
        transition_communication(6, "queued", execution_id=happy)
        transition(happy, 4, "dispatched")
        transition_communication(7, "dispatched")
        missing_receipt = transition(happy, 5, "receipt_verified", check=False)
        self.assertNotEqual(0, missing_receipt.returncode)
        transition(happy, 5, "receipt_verified", receipt=True)
        transition_communication(8, "receipt_verified")

        cancelled_draft = insert_execution(20)
        transition(cancelled_draft, 1, "cancelled")
        cancelled_validated = insert_execution(21)
        transition(cancelled_validated, 1, "validated", validation_id=validation)
        transition(cancelled_validated, 2, "cancelled")
        cancelled_approved = insert_execution(22)
        transition(cancelled_approved, 1, "validated", validation_id=validation)
        transition(cancelled_approved, 2, "approved", approval_id=approval)
        transition(cancelled_approved, 3, "cancelled")

        retry = insert_execution(30)
        transition(retry, 1, "validated", validation_id=validation)
        transition(retry, 2, "approved", approval_id=approval)
        transition(retry, 3, "queued")
        transition(retry, 4, "failed")
        transition(retry, 5, "queued")
        transition(retry, 6, "dispatched")
        wrong_receipt = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.execution_receipts
                (id, tenant_id, matter_id, execution_id, connector,
                 external_receipt_id, artifact_content_sha256,
                 destination_sha256, received_at, verified_at)
            VALUES ('90000000-0000-4000-8000-000000000091', '{tenant}',
                    '{matter}', '{retry}', 'synthetic', 'wrong-hash',
                    '{"c" * 64}', '{destination_sha}', clock_timestamp(),
                    clock_timestamp());
            """,
            check=False,
        )
        self.assertNotEqual(0, wrong_receipt.returncode)
        transition(retry, 7, "failed")

        direct_update = self._psql(
            "sklegal_test_alpha_one",
            f"""
            UPDATE sklegal_legal.executions
            SET status = 'failed', version = version + 1 WHERE id = '{happy}';
            """,
            check=False,
        )
        direct_event = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.execution_events
                (id, tenant_id, matter_id, execution_id, sequence_no, step,
                 occurred_at, correlation_id, actor_principal_id)
            VALUES ('90000000-0000-4000-8000-000000000092', '{tenant}',
                    '{matter}', '{happy}', 6, 'failed', clock_timestamp(),
                    'corr-direct-write', '{principal}');
            """,
            check=False,
        )
        self.assertNotEqual(0, direct_update.returncode)
        self.assertNotEqual(0, direct_event.returncode)
        direct_communication_update = self._psql(
            "sklegal_test_alpha_one",
            f"""
            UPDATE sklegal_legal.communications
            SET status = 'failed', version = version + 1
            WHERE id = '{communication}';
            """,
            check=False,
        )
        self.assertNotEqual(0, direct_communication_update.returncode)
        evidence = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT execution.status || ':' || execution.version || ':' ||
                   count(event.id) || ':' || count(DISTINCT receipt.id) || ':' ||
                   bool_and(event.updated_at <= execution.updated_at) || ':' ||
                   bool_and(receipt.updated_at <= execution.updated_at)
            FROM sklegal_legal.executions AS execution
            JOIN sklegal_legal.execution_events AS event
              ON event.tenant_id = execution.tenant_id
             AND event.matter_id = execution.matter_id
             AND event.execution_id = execution.id
            LEFT JOIN sklegal_legal.execution_receipts AS receipt
              ON receipt.tenant_id = execution.tenant_id
             AND receipt.matter_id = execution.matter_id
             AND receipt.execution_id = execution.id
            WHERE execution.id = '{happy}'
            GROUP BY execution.status, execution.version;
            """,
        )
        self.assertEqual("receipt_verified:6:5:1:true:true", evidence.stdout.strip())


if __name__ == "__main__":
    unittest.main()
