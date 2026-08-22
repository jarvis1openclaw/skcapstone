"""Artifact consumer lifecycle persistence contract tests."""

from __future__ import annotations

import subprocess
import unittest

from sklegal_persistence import reconstruct_with_metadata

from tests.integration.persistence_contract_support import PersistenceContractBase


class PersistenceContract05ArtifactConsumerTests(PersistenceContractBase):
    def test_10_artifact_consumer_lifecycle_serializes_and_completes(
        self,
    ) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        principal = value["principal_alpha_one"]
        role = "sklegal_test_alpha_one"
        digest = "c" * 64
        destination = "d" * 64

        def setup_artifact(
            suffix: int,
        ) -> tuple[str, str, str, str, str]:
            work_product = f"93000000-0000-4000-8000-{suffix:012d}"
            artifact = f"93000000-0000-4000-8001-{suffix:012d}"
            validation = f"93000000-0000-4000-8002-{suffix:012d}"
            approval = f"93000000-0000-4000-8003-{suffix:012d}"
            participant = f"93000000-0000-4000-8004-{suffix:012d}"
            source = f"93000000-0000-4000-8005-{suffix:012d}"
            self._psql(
                role,
                f"""
                BEGIN;
                INSERT INTO sklegal_legal.source_references
                    (id, tenant_id, matter_id, source_system, source_version,
                     content_sha256, locator, observed_at)
                VALUES ('{source}', '{tenant}', '{matter}', 'synthetic', 'v1',
                        '{"e" * 64}', 'synthetic:consumer-{suffix}',
                        clock_timestamp());
                INSERT INTO sklegal_legal.parties
                    (id, tenant_id, matter_id, display_name, party_kind,
                     source_reference_id)
                VALUES ('{participant}', '{tenant}', '{matter}',
                        'Synthetic consumer {suffix}', 'person', '{source}');
                INSERT INTO sklegal_legal.work_products
                    (id, tenant_id, matter_id, title, work_product_kind,
                     current_version_id, current_version_number,
                     current_content_sha256)
                VALUES ('{work_product}', '{tenant}', '{matter}',
                        'Synthetic consumer work product {suffix}', 'memo',
                        '{artifact}', 1, '{digest}');
                INSERT INTO sklegal_legal.work_product_versions
                    (id, tenant_id, matter_id, work_product_id, version_number,
                     content_sha256, source_artifact_id)
                VALUES ('{artifact}', '{tenant}', '{matter}', '{work_product}',
                        1, '{digest}',
                        '93000000-0000-4000-8006-{suffix:012d}');
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
                        'Synthetic exact consumer validation.');
                INSERT INTO sklegal_legal.validation_checks
                    (tenant_id, matter_id, validation_id, check_id)
                VALUES ('{tenant}', '{matter}', '{validation}',
                        'synthetic.consumer.{suffix}');
                COMMIT;
                INSERT INTO sklegal_legal.approvals
                    (id, tenant_id, matter_id, subject_artifact_id,
                     subject_artifact_version, subject_content_sha256)
                VALUES ('{approval}', '{tenant}', '{matter}', '{artifact}', 1,
                        '{digest}');
                SELECT status FROM sklegal_legal.transition_approval(
                    '{tenant}', '{matter}', '{approval}', 1, 'approved',
                    'Synthetic exact consumer approval.');
                """,
            )
            return work_product, artifact, validation, approval, participant

        def insert_execution(suffix: int, artifact: str) -> str:
            execution = f"93000000-0000-4000-8007-{suffix:012d}"
            self._psql(
                role,
                f"""
                INSERT INTO sklegal_legal.executions
                    (id, tenant_id, matter_id, subject_artifact_id,
                     subject_artifact_version, subject_content_sha256,
                     destination_sha256, idempotency_key)
                VALUES ('{execution}', '{tenant}', '{matter}', '{artifact}', 1,
                        '{digest}', '{destination}',
                        'synthetic-consumer-{suffix}');
                """,
            )
            return execution

        def create_communication(suffix: int, artifact: str, participant: str) -> str:
            communication = f"93000000-0000-4000-8008-{suffix:012d}"
            self._psql(
                role,
                f"""
                SELECT status FROM sklegal_legal.create_communication(
                    '{tenant}', '{matter}', '{communication}', 'outbound',
                    'email', 'Synthetic consumer communication {suffix}',
                    ARRAY['{participant}'::uuid], '{artifact}', 1, '{digest}');
                """,
            )
            return communication

        def transition_execution(
            execution: str,
            version: int,
            status: str,
            *,
            validation: str | None = None,
            approval: str | None = None,
            receipt_suffix: int | None = None,
        ) -> subprocess.CompletedProcess[str]:
            receipt_arguments = ""
            if receipt_suffix is not None:
                receipt_id = f"93000000-0000-4000-8009-{receipt_suffix:012d}"
                receipt_arguments = (
                    f", '{receipt_id}', 'synthetic-connector', "
                    f"'synthetic-consumer-receipt-{receipt_suffix}', "
                    "clock_timestamp(), clock_timestamp()"
                )
            return self._psql(
                role,
                f"""
                SELECT status FROM sklegal_legal.transition_execution(
                    '{tenant}', '{matter}', '{execution}', {version},
                    '{status}', 'consumer-{execution[-8:]}-{version}',
                    {f"'{validation}'" if validation else "NULL"},
                    {f"'{approval}'" if approval else "NULL"}
                    {receipt_arguments});
                """,
            )

        def transition_communication(
            communication: str,
            version: int,
            status: str,
            *,
            validation: str | None = None,
            approval: str | None = None,
            execution: str | None = None,
        ) -> subprocess.CompletedProcess[str]:
            return self._psql(
                role,
                f"""
                SELECT status FROM sklegal_legal.transition_communication(
                    '{tenant}', '{matter}', '{communication}', {version},
                    '{status}',
                    {f"'{validation}'" if validation else "NULL"},
                    {f"'{approval}'" if approval else "NULL"},
                    {f"'{destination}'" if execution else "NULL"},
                    {f"'{execution}'" if execution else "NULL"});
                """,
            )

        def supersede(artifact: str) -> subprocess.CompletedProcess[str]:
            return self._psql(
                role,
                f"""
                SELECT status FROM sklegal_legal.transition_work_product_version(
                    '{tenant}', '{matter}', '{artifact}', 2, 'superseded');
                """,
                check=False,
            )

        def revoke_approval(
            approval: str, *, check: bool = False
        ) -> subprocess.CompletedProcess[str]:
            return self._psql(
                role,
                f"""
                SELECT status FROM sklegal_legal.transition_approval(
                    '{tenant}', '{matter}', '{approval}', 2, 'revoked',
                    'Synthetic prospective revocation.');
                """,
                check=check,
            )

        _, artifact, validation, approval, participant = setup_artifact(1)
        execution = insert_execution(1, artifact)
        communication = create_communication(1, artifact, participant)
        transition_execution(execution, 1, "validated", validation=validation)
        transition_communication(communication, 1, "validated", validation=validation)
        transition_execution(execution, 2, "approved", approval=approval)
        transition_communication(communication, 2, "approved", approval=approval)
        approved_revoke_denial = revoke_approval(approval)
        self.assertNotEqual(0, approved_revoke_denial.returncode)
        approved_denial = supersede(artifact)
        self.assertNotEqual(0, approved_denial.returncode)
        transition_execution(execution, 3, "queued")
        transition_communication(communication, 3, "queued", execution=execution)
        queued_revoke_denial = revoke_approval(approval)
        self.assertNotEqual(0, queued_revoke_denial.returncode)
        queued_denial = supersede(artifact)
        self.assertNotEqual(0, queued_denial.returncode)
        transition_execution(execution, 4, "dispatched")
        transition_communication(communication, 4, "dispatched")
        dispatched_revoke_denial = revoke_approval(approval)
        self.assertNotEqual(0, dispatched_revoke_denial.returncode)
        dispatched_denial = supersede(artifact)
        self.assertNotEqual(0, dispatched_denial.returncode)
        transition_execution(execution, 5, "receipt_verified", receipt_suffix=1)
        transition_communication(communication, 5, "receipt_verified")
        terminal_revoke = revoke_approval(approval, check=True)
        self.assertEqual("revoked", terminal_revoke.stdout.strip())
        historical_execution_row = self._json_rows(
            role, "sklegal_legal.executions", f"id = '{execution}'"
        )[0]
        historical_execution = reconstruct_with_metadata(
            "Execution",
            historical_execution_row,
            self._normalized_relations(role, "Execution", historical_execution_row),
        )
        self.assertEqual("approved", historical_execution.entity.approval.status)
        self.assertEqual(2, historical_execution.entity.approval.version)
        self.assertEqual(
            "revoked:approved:1",
            self._psql(
                role,
                f"""
                SELECT current.status || ':' || history.status || ':' || count(*)
                FROM sklegal_legal.approvals AS current
                JOIN sklegal_legal.approval_history AS history
                  ON history.tenant_id = current.tenant_id
                 AND history.matter_id = current.matter_id
                 AND history.approval_id = current.id
                WHERE current.id = '{approval}'
                GROUP BY current.status, history.status;
                """,
            ).stdout.strip(),
        )
        self.assertEqual(
            "f|f|f",
            self._psql(
                "postgres",
                f"""
                SELECT has_table_privilege(
                           '{role}', 'sklegal_legal.approval_history', 'INSERT'),
                       has_table_privilege(
                           '{role}', 'sklegal_legal.approval_history', 'UPDATE'),
                       has_table_privilege(
                           '{role}', 'sklegal_legal.approval_history', 'DELETE');
                """,
            ).stdout.strip(),
        )
        direct_history_change = self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.approval_history
            SET rationale = 'Synthetic forbidden rewrite'
            WHERE tenant_id = '{tenant}' AND matter_id = '{matter}'
              AND approval_id = '{approval}' AND approval_version = 2;
            """,
            check=False,
        )
        self.assertNotEqual(0, direct_history_change.returncode)
        self.assertIn("append-only", direct_history_change.stderr)
        revoked_execution = insert_execution(90, artifact)
        revoked_communication = create_communication(90, artifact, participant)
        transition_execution(revoked_execution, 1, "validated", validation=validation)
        transition_communication(
            revoked_communication, 1, "validated", validation=validation
        )
        revoked_execution_progress = self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_execution(
                '{tenant}', '{matter}', '{revoked_execution}', 2, 'approved',
                'revoked-approval-execution', NULL, '{approval}');
            """,
            check=False,
        )
        revoked_communication_progress = self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_communication(
                '{tenant}', '{matter}', '{revoked_communication}', 2,
                'approved', NULL, '{approval}', NULL, NULL);
            """,
            check=False,
        )
        self.assertNotEqual(0, revoked_execution_progress.returncode)
        self.assertNotEqual(0, revoked_communication_progress.returncode)
        transition_execution(revoked_execution, 2, "cancelled")
        transition_communication(revoked_communication, 2, "cancelled")
        terminal_supersede = supersede(artifact)
        self.assertEqual(0, terminal_supersede.returncode, terminal_supersede.stderr)

        _, retry_artifact, retry_validation, retry_approval, retry_party = (
            setup_artifact(2)
        )
        retry_execution = insert_execution(2, retry_artifact)
        retry_communication = create_communication(2, retry_artifact, retry_party)
        transition_execution(
            retry_execution, 1, "validated", validation=retry_validation
        )
        transition_communication(
            retry_communication, 1, "validated", validation=retry_validation
        )
        transition_execution(retry_execution, 2, "approved", approval=retry_approval)
        transition_communication(
            retry_communication, 2, "approved", approval=retry_approval
        )
        transition_execution(retry_execution, 3, "queued")
        transition_communication(
            retry_communication, 3, "queued", execution=retry_execution
        )
        transition_execution(retry_execution, 4, "dispatched")
        transition_communication(retry_communication, 4, "dispatched")
        transition_execution(retry_execution, 5, "failed")
        transition_communication(retry_communication, 5, "failed")
        failed_revoke_denial = revoke_approval(retry_approval)
        self.assertNotEqual(0, failed_revoke_denial.returncode)
        retryable_denial = supersede(retry_artifact)
        self.assertNotEqual(0, retryable_denial.returncode)
        transition_execution(retry_execution, 6, "queued")
        transition_communication(retry_communication, 6, "queued")
        transition_execution(retry_execution, 7, "dispatched")
        transition_communication(retry_communication, 7, "dispatched")
        transition_execution(retry_execution, 8, "receipt_verified", receipt_suffix=2)
        transition_communication(retry_communication, 8, "receipt_verified")
        retry_terminal_revoke = revoke_approval(retry_approval, check=True)
        self.assertEqual("revoked", retry_terminal_revoke.stdout.strip())
        retry_terminal = supersede(retry_artifact)
        self.assertEqual(0, retry_terminal.returncode, retry_terminal.stderr)

        (
            reset_work_product,
            reset_artifact,
            reset_validation,
            reset_approval,
            _,
        ) = setup_artifact(7)
        self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{reset_work_product}', 1, 'in_review');
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{reset_work_product}', 2, 'validated',
                '{reset_validation}', NULL);
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{reset_work_product}', 3, 'approved',
                NULL, '{reset_approval}');
            """,
        )
        work_product_revoke_denial = revoke_approval(reset_approval)
        self.assertNotEqual(0, work_product_revoke_denial.returncode)
        self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.revise_work_product(
                '{tenant}', '{matter}', '{reset_work_product}', 4,
                'Synthetic consumer work product 7 reset', 'memo',
                '{reset_artifact}', 1, '{digest}');
            """,
        )
        reset_revoke = revoke_approval(reset_approval, check=True)
        self.assertEqual("revoked", reset_revoke.stdout.strip())

        for consumer_kind, suffix in (("execution", 3), ("communication", 4)):
            _, race_artifact, race_validation, _, race_party = setup_artifact(suffix)
            consumer = (
                insert_execution(suffix, race_artifact)
                if consumer_kind == "execution"
                else create_communication(suffix, race_artifact, race_party)
            )
            gate_statement = (
                f"SELECT status FROM sklegal_legal.transition_execution("
                f"'{tenant}', '{matter}', '{consumer}', 1, 'validated', "
                f"'race-{consumer_kind}-{suffix}', '{race_validation}', NULL);"
                if consumer_kind == "execution"
                else f"SELECT status FROM sklegal_legal.transition_communication("
                f"'{tenant}', '{matter}', '{consumer}', 1, 'validated', "
                f"'{race_validation}', NULL, NULL, NULL);"
            )
            application_name = f"sklegal-s102-{consumer_kind}-gate-first"
            gate_first_sql = f"""
                BEGIN;
                SET LOCAL application_name = '{application_name}';
                {gate_statement}
                SELECT pg_sleep(1.0);
                COMMIT;
            """
            gate_first = subprocess.Popen(
                [*self._psql_command(role), "--command", gate_first_sql],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._wait_for_sleep(application_name, gate_first)
            blocked_supersede = supersede(race_artifact)
            gate_stdout, gate_stderr = gate_first.communicate(timeout=10)
            self.assertEqual(0, gate_first.returncode, gate_stderr)
            self.assertIn("validated", gate_stdout)
            self.assertNotEqual(0, blocked_supersede.returncode)

        for consumer_kind, suffix in (("execution", 5), ("communication", 6)):
            _, race_artifact, race_validation, _, race_party = setup_artifact(suffix)
            application_name = f"sklegal-s102-{consumer_kind}-supersede-first"
            supersede_first_sql = f"""
                BEGIN;
                SET LOCAL application_name = '{application_name}';
                SELECT status FROM sklegal_legal.transition_work_product_version(
                    '{tenant}', '{matter}', '{race_artifact}', 2, 'superseded');
                SELECT pg_sleep(1.0);
                COMMIT;
            """
            supersede_first = subprocess.Popen(
                [*self._psql_command(role), "--command", supersede_first_sql],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._wait_for_sleep(application_name, supersede_first)
            if consumer_kind == "execution":
                blocked_consumer = self._psql(
                    role,
                    f"""
                    BEGIN;
                    INSERT INTO sklegal_legal.executions
                        (id, tenant_id, matter_id, subject_artifact_id,
                         subject_artifact_version, subject_content_sha256,
                         destination_sha256, idempotency_key)
                    VALUES ('93000000-0000-4000-8007-{suffix:012d}',
                            '{tenant}', '{matter}', '{race_artifact}', 1,
                            '{digest}', '{destination}',
                            'synthetic-supersede-first-{suffix}');
                    SELECT status FROM sklegal_legal.transition_execution(
                        '{tenant}', '{matter}',
                        '93000000-0000-4000-8007-{suffix:012d}', 1,
                        'validated', 'supersede-first-execution',
                        '{race_validation}', NULL);
                    COMMIT;
                    """,
                    check=False,
                )
            else:
                blocked_consumer = self._psql(
                    role,
                    f"""
                    BEGIN;
                    SELECT status FROM sklegal_legal.create_communication(
                        '{tenant}', '{matter}',
                        '93000000-0000-4000-8008-{suffix:012d}', 'outbound',
                        'email', 'Synthetic supersede-first communication',
                        ARRAY['{race_party}'::uuid], '{race_artifact}', 1,
                        '{digest}');
                    SELECT status FROM sklegal_legal.transition_communication(
                        '{tenant}', '{matter}',
                        '93000000-0000-4000-8008-{suffix:012d}', 1,
                        'validated', '{race_validation}', NULL, NULL, NULL);
                    COMMIT;
                    """,
                    check=False,
                )
            supersede_stdout, supersede_stderr = supersede_first.communicate(timeout=10)
            self.assertEqual(0, supersede_first.returncode, supersede_stderr)
            self.assertIn("superseded", supersede_stdout)
            self.assertNotEqual(0, blocked_consumer.returncode)
            self.assertIn("superseded", blocked_consumer.stderr)

        for consumer_kind, suffix in (
            ("work_product", 8),
            ("execution", 9),
            ("communication", 10),
        ):
            (
                race_work_product,
                race_artifact,
                race_validation,
                race_approval,
                race_party,
            ) = setup_artifact(suffix)
            if consumer_kind == "work_product":
                self._psql(
                    role,
                    f"""
                    SELECT status FROM sklegal_legal.transition_work_product(
                        '{tenant}', '{matter}', '{race_work_product}', 1,
                        'in_review');
                    SELECT status FROM sklegal_legal.transition_work_product(
                        '{tenant}', '{matter}', '{race_work_product}', 2,
                        'validated', '{race_validation}', NULL);
                    """,
                )
                gate_statement = f"""
                    SELECT status FROM sklegal_legal.transition_work_product(
                        '{tenant}', '{matter}', '{race_work_product}', 3,
                        'approved', NULL, '{race_approval}');
                """
            elif consumer_kind == "execution":
                race_consumer = insert_execution(suffix, race_artifact)
                transition_execution(
                    race_consumer, 1, "validated", validation=race_validation
                )
                gate_statement = f"""
                    SELECT status FROM sklegal_legal.transition_execution(
                        '{tenant}', '{matter}', '{race_consumer}', 2,
                        'approved', 'approval-race-{suffix}', NULL,
                        '{race_approval}');
                """
            else:
                race_consumer = create_communication(suffix, race_artifact, race_party)
                transition_communication(
                    race_consumer, 1, "validated", validation=race_validation
                )
                gate_statement = f"""
                    SELECT status FROM sklegal_legal.transition_communication(
                        '{tenant}', '{matter}', '{race_consumer}', 2,
                        'approved', NULL, '{race_approval}', NULL, NULL);
                """
            application_name = f"sklegal-s102-{consumer_kind}-approval-first"
            gate_first = subprocess.Popen(
                [
                    *self._psql_command(role),
                    "--command",
                    f"""
                    BEGIN;
                    SET LOCAL application_name = '{application_name}';
                    {gate_statement}
                    SELECT pg_sleep(1.0);
                    COMMIT;
                    """,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._wait_for_sleep(application_name, gate_first)
            blocked_revoke = revoke_approval(race_approval)
            gate_stdout, gate_stderr = gate_first.communicate(timeout=10)
            self.assertEqual(0, gate_first.returncode, gate_stderr)
            self.assertIn("approved", gate_stdout)
            self.assertNotEqual(0, blocked_revoke.returncode)

        for consumer_kind, suffix in (
            ("work_product", 11),
            ("execution", 12),
            ("communication", 13),
        ):
            (
                race_work_product,
                race_artifact,
                race_validation,
                race_approval,
                race_party,
            ) = setup_artifact(suffix)
            if consumer_kind == "work_product":
                self._psql(
                    role,
                    f"""
                    SELECT status FROM sklegal_legal.transition_work_product(
                        '{tenant}', '{matter}', '{race_work_product}', 1,
                        'in_review');
                    SELECT status FROM sklegal_legal.transition_work_product(
                        '{tenant}', '{matter}', '{race_work_product}', 2,
                        'validated', '{race_validation}', NULL);
                    """,
                )
                gate_statement = f"""
                    SELECT status FROM sklegal_legal.transition_work_product(
                        '{tenant}', '{matter}', '{race_work_product}', 3,
                        'approved', NULL, '{race_approval}');
                """
            elif consumer_kind == "execution":
                race_consumer = insert_execution(suffix, race_artifact)
                transition_execution(
                    race_consumer, 1, "validated", validation=race_validation
                )
                gate_statement = f"""
                    SELECT status FROM sklegal_legal.transition_execution(
                        '{tenant}', '{matter}', '{race_consumer}', 2,
                        'approved', 'revocation-race-{suffix}', NULL,
                        '{race_approval}');
                """
            else:
                race_consumer = create_communication(suffix, race_artifact, race_party)
                transition_communication(
                    race_consumer, 1, "validated", validation=race_validation
                )
                gate_statement = f"""
                    SELECT status FROM sklegal_legal.transition_communication(
                        '{tenant}', '{matter}', '{race_consumer}', 2,
                        'approved', NULL, '{race_approval}', NULL, NULL);
                """
            application_name = f"sklegal-s102-{consumer_kind}-revocation-first"
            revoke_first = subprocess.Popen(
                [
                    *self._psql_command(role),
                    "--command",
                    f"""
                    BEGIN;
                    SET LOCAL application_name = '{application_name}';
                    SELECT status FROM sklegal_legal.transition_approval(
                        '{tenant}', '{matter}', '{race_approval}', 2,
                        'revoked', 'Synthetic revocation-first race.');
                    SELECT pg_sleep(1.0);
                    COMMIT;
                    """,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._wait_for_sleep(application_name, revoke_first)
            blocked_gate = self._psql(role, gate_statement, check=False)
            revoke_stdout, revoke_stderr = revoke_first.communicate(timeout=10)
            self.assertEqual(0, revoke_first.returncode, revoke_stderr)
            self.assertIn("revoked", revoke_stdout)
            self.assertNotEqual(0, blocked_gate.returncode)

        (
            _,
            terminal_artifact,
            terminal_validation,
            terminal_approval,
            terminal_party,
        ) = setup_artifact(14)
        terminal_execution = insert_execution(14, terminal_artifact)
        terminal_communication = create_communication(
            14, terminal_artifact, terminal_party
        )
        transition_execution(
            terminal_execution, 1, "validated", validation=terminal_validation
        )
        transition_communication(
            terminal_communication, 1, "validated", validation=terminal_validation
        )
        transition_execution(
            terminal_execution, 2, "approved", approval=terminal_approval
        )
        transition_communication(
            terminal_communication, 2, "approved", approval=terminal_approval
        )
        transition_execution(terminal_execution, 3, "queued")
        transition_communication(
            terminal_communication, 3, "queued", execution=terminal_execution
        )
        transition_execution(terminal_execution, 4, "dispatched")
        transition_communication(terminal_communication, 4, "dispatched")
        application_name = "sklegal-s102-terminal-first-revocation"
        terminal_first = subprocess.Popen(
            [
                *self._psql_command(role),
                "--command",
                f"""
                BEGIN;
                SET LOCAL application_name = '{application_name}';
                SELECT status FROM sklegal_legal.transition_execution(
                    '{tenant}', '{matter}', '{terminal_execution}', 5,
                    'receipt_verified', 'terminal-first-receipt', NULL, NULL,
                    '93000000-0000-4000-8009-000000000014',
                    'synthetic-connector', 'terminal-first-receipt',
                    clock_timestamp(), clock_timestamp());
                SELECT status FROM sklegal_legal.transition_communication(
                    '{tenant}', '{matter}', '{terminal_communication}', 5,
                    'receipt_verified', NULL, NULL, NULL, NULL);
                SELECT pg_sleep(1.0);
                COMMIT;
                """,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._wait_for_sleep(application_name, terminal_first)
        terminal_revoke = revoke_approval(terminal_approval, check=True)
        terminal_stdout, terminal_stderr = terminal_first.communicate(timeout=10)
        self.assertEqual(0, terminal_first.returncode, terminal_stderr)
        self.assertIn("receipt_verified", terminal_stdout)
        self.assertEqual("revoked", terminal_revoke.stdout.strip())

        (
            _,
            reverse_artifact,
            reverse_validation,
            reverse_approval,
            reverse_party,
        ) = setup_artifact(15)
        reverse_execution = insert_execution(15, reverse_artifact)
        reverse_communication = create_communication(
            15, reverse_artifact, reverse_party
        )
        transition_execution(
            reverse_execution, 1, "validated", validation=reverse_validation
        )
        transition_communication(
            reverse_communication, 1, "validated", validation=reverse_validation
        )
        transition_execution(
            reverse_execution, 2, "approved", approval=reverse_approval
        )
        transition_communication(
            reverse_communication, 2, "approved", approval=reverse_approval
        )
        transition_execution(reverse_execution, 3, "queued")
        transition_communication(
            reverse_communication, 3, "queued", execution=reverse_execution
        )
        transition_execution(reverse_execution, 4, "dispatched")
        transition_communication(reverse_communication, 4, "dispatched")
        reverse_revoke_denial = revoke_approval(reverse_approval)
        self.assertNotEqual(0, reverse_revoke_denial.returncode)
        transition_execution(
            reverse_execution, 5, "receipt_verified", receipt_suffix=15
        )
        transition_communication(reverse_communication, 5, "receipt_verified")
        reverse_terminal_revoke = revoke_approval(reverse_approval, check=True)
        self.assertEqual("revoked", reverse_terminal_revoke.stdout.strip())

        (
            reset_race_work_product,
            reset_race_artifact,
            reset_race_validation,
            reset_race_approval,
            _,
        ) = setup_artifact(16)
        self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{reset_race_work_product}', 1,
                'in_review');
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{reset_race_work_product}', 2,
                'validated', '{reset_race_validation}', NULL);
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{reset_race_work_product}', 3,
                'approved', NULL, '{reset_race_approval}');
            """,
        )
        application_name = "sklegal-s102-work-product-reset-first"
        reset_first = subprocess.Popen(
            [
                *self._psql_command(role),
                "--command",
                f"""
                BEGIN;
                SET LOCAL application_name = '{application_name}';
                SELECT status FROM sklegal_legal.revise_work_product(
                    '{tenant}', '{matter}', '{reset_race_work_product}', 4,
                    'Synthetic reset-first work product', 'memo',
                    '{reset_race_artifact}', 1, '{digest}');
                SELECT pg_sleep(1.0);
                COMMIT;
                """,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._wait_for_sleep(application_name, reset_first)
        reset_race_revoke = revoke_approval(reset_race_approval, check=True)
        reset_stdout, reset_stderr = reset_first.communicate(timeout=10)
        self.assertEqual(0, reset_first.returncode, reset_stderr)
        self.assertIn("in_review", reset_stdout)
        self.assertEqual("revoked", reset_race_revoke.stdout.strip())

        (
            _,
            progression_artifact,
            progression_validation,
            progression_approval,
            progression_party,
        ) = setup_artifact(17)
        progression_execution = insert_execution(17, progression_artifact)
        progression_communication = create_communication(
            17, progression_artifact, progression_party
        )
        transition_execution(
            progression_execution,
            1,
            "validated",
            validation=progression_validation,
        )
        transition_communication(
            progression_communication,
            1,
            "validated",
            validation=progression_validation,
        )
        transition_execution(
            progression_execution, 2, "approved", approval=progression_approval
        )
        transition_communication(
            progression_communication,
            2,
            "approved",
            approval=progression_approval,
        )
        progression_stages = (
            (
                "queue",
                f"""
                SELECT status FROM sklegal_legal.transition_execution(
                    '{tenant}', '{matter}', '{progression_execution}', 3,
                    'queued', 'progression-queue');
                SELECT status FROM sklegal_legal.transition_communication(
                    '{tenant}', '{matter}', '{progression_communication}', 3,
                    'queued', NULL, NULL, '{destination}',
                    '{progression_execution}');
                """,
            ),
            (
                "dispatch",
                f"""
                SELECT status FROM sklegal_legal.transition_execution(
                    '{tenant}', '{matter}', '{progression_execution}', 4,
                    'dispatched', 'progression-dispatch');
                SELECT status FROM sklegal_legal.transition_communication(
                    '{tenant}', '{matter}', '{progression_communication}', 4,
                    'dispatched', NULL, NULL, NULL, NULL);
                """,
            ),
            (
                "fail",
                f"""
                SELECT status FROM sklegal_legal.transition_execution(
                    '{tenant}', '{matter}', '{progression_execution}', 5,
                    'failed', 'progression-fail');
                SELECT status FROM sklegal_legal.transition_communication(
                    '{tenant}', '{matter}', '{progression_communication}', 5,
                    'failed', NULL, NULL, NULL, NULL);
                """,
            ),
            (
                "retry",
                f"""
                SELECT status FROM sklegal_legal.transition_execution(
                    '{tenant}', '{matter}', '{progression_execution}', 6,
                    'queued', 'progression-retry');
                SELECT status FROM sklegal_legal.transition_communication(
                    '{tenant}', '{matter}', '{progression_communication}', 6,
                    'queued', NULL, NULL, NULL, NULL);
                """,
            ),
        )
        for stage, statements in progression_stages:
            application_name = f"sklegal-s102-{stage}-first-revocation"
            stage_first = subprocess.Popen(
                [
                    *self._psql_command(role),
                    "--command",
                    f"""
                    BEGIN;
                    SET LOCAL application_name = '{application_name}';
                    {statements}
                    SELECT pg_sleep(1.0);
                    COMMIT;
                    """,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._wait_for_sleep(application_name, stage_first)
            progression_revoke = revoke_approval(progression_approval)
            stage_stdout, stage_stderr = stage_first.communicate(timeout=10)
            self.assertEqual(0, stage_first.returncode, stage_stderr)
            self.assertIn(
                "queued" if stage in {"queue", "retry"} else stage + "ed",
                stage_stdout,
            )
            self.assertNotEqual(0, progression_revoke.returncode)
        transition_execution(progression_execution, 7, "dispatched")
        transition_communication(progression_communication, 7, "dispatched")
        transition_execution(
            progression_execution, 8, "receipt_verified", receipt_suffix=17
        )
        transition_communication(progression_communication, 8, "receipt_verified")
        progression_terminal_revoke = revoke_approval(progression_approval, check=True)
        self.assertEqual("revoked", progression_terminal_revoke.stdout.strip())


if __name__ == "__main__":
    unittest.main()
