"""Migration preflight and migrator profile persistence contract tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID

from sklegal_capauth import (
    PostgresPrincipalPolicyBackend,
    PostgresReplayBackend,
    PostgresRevocationBackend,
    PrincipalContext,
    PrincipalType,
)
from sklegal_policies import PostgresAuthorizationUseBackend

from tests.integration.persistence_contract_support import (
    AUDIT_MIGRATION_STEPS,
    CAPAUTH_MIGRATION_FILES,
    CAPAUTH_RUNTIME_PRINCIPAL,
    CAPAUTH_RUNTIME_ROLE,
    CAPAUTH_RUNTIME_SUBJECT,
    MIGRATION_ROOT,
    MIGRATION_TOTAL,
    PersistenceContractBase,
)


class PersistenceContract00MigrationPreflightTests(PersistenceContractBase):
    def test_00_migrations_preflight_up_down_up_and_owners(self) -> None:
        self.assertNotEqual(0, self.unsafe_preflight.returncode)
        self.assertIn("exact sklegal_migrator", self.unsafe_preflight.stderr)
        self.assertEqual("t", self.unsafe_preflight_left_no_schema)
        self.assertEqual(
            (
                f"applied {MIGRATION_TOTAL} migration(s)",
                f"reverted {MIGRATION_TOTAL} migration(s)",
                f"applied {MIGRATION_TOTAL} migration(s)",
            ),
            self.migration_evidence,
        )
        self.assertEqual(
            f"migration status: {MIGRATION_TOTAL}/{MIGRATION_TOTAL} applied",
            self._migrate("status").stdout.strip(),
        )
        self.assertEqual(
            tuple(
                (
                    steps,
                    f"reverted {steps} migration(s)",
                    f"applied {steps} migration(s)",
                )
                for steps in range(1, AUDIT_MIGRATION_STEPS + 1)
            ),
            self.migration_step_evidence,
        )
        with tempfile.TemporaryDirectory(prefix="sklegal-s102-migration-") as directory:
            synthetic_root = Path(directory) / "migrations"
            shutil.copytree(MIGRATION_ROOT, synthetic_root)
            migration_name = (
                f"{MIGRATION_TOTAL + 1:04d}_intentionally_failing_synthetic_probe.sql"
            )
            migration_path = synthetic_root / migration_name
            migration_path.write_text(
                "-- sklegal:up\n"
                "CREATE TABLE sklegal_legal.synthetic_rollback_probe (id integer);\n"
                "SELECT 1 / 0;\n"
                "-- sklegal:down\n"
                "DROP TABLE sklegal_legal.synthetic_rollback_probe;\n",
                encoding="utf-8",
            )
            manifest_path = synthetic_root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["migrations"].append(
                {
                    "file": migration_name,
                    "sha256": hashlib.sha256(migration_path.read_bytes()).hexdigest(),
                }
            )
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            failure = self._migrate("up", root=synthetic_root, check=False)
            self.assertNotEqual(0, failure.returncode)
            self.assertIn("division by zero", failure.stderr)
        rollback_state = self._psql(
            "postgres",
            f"""
            SELECT to_regclass('sklegal_legal.synthetic_rollback_probe') IS NULL,
                   count(*) = {MIGRATION_TOTAL}
            FROM sklegal_migrations.schema_migrations;
            """,
        )
        self.assertEqual("t|t", rollback_state.stdout.strip())
        self.assertEqual(
            f"migration status: {MIGRATION_TOTAL}/{MIGRATION_TOTAL} applied",
            self._migrate("status").stdout.strip(),
        )
        owners = self._psql(
            "postgres",
            """
            SELECT count(*) FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname LIKE 'sklegal_%'
              AND relation.relowner <> 'sklegal_migrator'::regrole;
            """,
        )
        self.assertEqual("0", owners.stdout.strip())

    def test_00_capauth_migrations_and_security_definer_grants_are_live(self) -> None:
        applied = self._psql(
            "postgres",
            """
            SELECT string_agg(file, ',' ORDER BY file)
            FROM sklegal_migrations.schema_migrations
            WHERE file >= '0008_' AND file <= '0012_zzzz';
            """,
        ).stdout.strip()
        self.assertEqual(",".join(CAPAUTH_MIGRATION_FILES), applied)
        functions = self._psql(
            "postgres",
            f"""
            SELECT string_agg(
                procedure.proname || ':' || procedure.prosecdef || ':' ||
                has_function_privilege(
                    '{CAPAUTH_RUNTIME_ROLE}', procedure.oid, 'EXECUTE'
                ), ',' ORDER BY procedure.proname
            )
            FROM pg_proc AS procedure
            JOIN pg_namespace AS namespace
              ON namespace.oid = procedure.pronamespace
            WHERE namespace.nspname = 'sklegal_identity'
              AND procedure.proname IN (
                  'capability_principal_snapshot',
                  'capability_revocation_snapshot',
                  'reserve_capability',
                  'revoke_capability'
              );
            """,
        ).stdout.strip()
        self.assertEqual(
            "capability_principal_snapshot:true:true,"
            "capability_revocation_snapshot:true:true,"
            "reserve_capability:true:true,revoke_capability:true:true",
            functions,
        )
        schema_usage = self._psql(
            "postgres",
            f"""
            SELECT has_schema_privilege(
                       '{CAPAUTH_RUNTIME_ROLE}', 'sklegal_identity', 'USAGE'
                   ),
                   has_schema_privilege(
                       '{CAPAUTH_RUNTIME_ROLE}', 'sklegal_legal', 'USAGE'
                   );
            """,
        ).stdout.strip()
        self.assertEqual("t|t", schema_usage)

    def test_00_capauth_principal_snapshot_tracks_subject_rebinding(self) -> None:
        tenant_id = UUID(self.fixture["tenant_alpha"])
        principal_id = UUID(CAPAUTH_RUNTIME_PRINCIPAL)
        initial = PrincipalContext(
            principal_id=principal_id,
            principal_type=PrincipalType.SERVICE,
            subject=CAPAUTH_RUNTIME_SUBJECT,
            tenant_id=tenant_id,
        )
        backend = PostgresPrincipalPolicyBackend(self._capauth_execute)
        before = backend.snapshot(initial)
        self.assertTrue(before.active)
        self.assertEqual(initial, before.principal)

        rebound_subject = "synthetic:service:capauth-runtime-rebound"
        self._psql(
            "postgres",
            f"""
            UPDATE sklegal_identity.principals
            SET authentication_subject = '{rebound_subject}',
                version = version + 1
            WHERE tenant_id = '{tenant_id}' AND id = '{principal_id}';
            """,
        )
        after = backend.snapshot(initial)
        self.assertNotEqual(before.revision, after.revision)
        self.assertNotEqual(initial, after.principal)
        self.assertEqual(rebound_subject, after.principal.subject)

    def test_00_capauth_revocation_persists_across_runtime_sessions(self) -> None:
        tenant_id = UUID(self.fixture["tenant_alpha"])
        digest = "a8" * 32
        empty = PostgresRevocationBackend(
            self._capauth_execute, tenant_id=tenant_id
        ).snapshot((digest,))
        self.assertEqual(frozenset(), empty.revoked_credential_digests)
        self._psql(
            CAPAUTH_RUNTIME_ROLE,
            f"""
            SELECT sklegal_identity.revoke_capability(
                '{tenant_id}', '{digest}', '{CAPAUTH_RUNTIME_PRINCIPAL}',
                'Synthetic S1-08 integration revocation'
            );
            """,
        )
        persisted = PostgresRevocationBackend(
            self._capauth_execute, tenant_id=tenant_id
        ).snapshot((digest,))
        self.assertEqual(frozenset({digest}), persisted.revoked_credential_digests)
        self.assertNotEqual(empty.revision, persisted.revision)
        self.assertEqual(
            "1",
            self._psql(
                "postgres",
                f"""
                SELECT count(*)
                FROM sklegal_identity.capability_revocations
                WHERE tenant_id = '{tenant_id}'
                  AND credential_digest = '{digest}';
                """,
            ).stdout.strip(),
        )

    def test_00_policy_authorization_use_is_durable_and_one_use(self) -> None:
        tenant_id = UUID(self.fixture["tenant_alpha"])
        decision_id = UUID("a5400000-0000-4000-8000-000000000018")
        evaluated_at = datetime.now(UTC)
        backend = PostgresAuthorizationUseBackend(
            self._capauth_execute,
            tenant_id=tenant_id,
        )
        arguments = {
            "capauth_decision_id": decision_id,
            "invocation_digest": "c8" * 32,
            "expires_at": evaluated_at + timedelta(minutes=5),
            "evaluated_at": evaluated_at,
        }
        self.assertTrue(backend.reserve(**arguments))
        self.assertFalse(backend.reserve(**arguments))
        self.assertEqual(
            "1",
            self._psql(
                "postgres",
                f"""
                SELECT count(*)
                FROM sklegal_identity.policy_authorization_uses
                WHERE tenant_id = '{tenant_id}'
                  AND capauth_decision_id = '{decision_id}';
                """,
            ).stdout.strip(),
        )

    def test_00_capauth_concurrent_replay_reservation_is_atomic(self) -> None:
        tenant_id = UUID(self.fixture["tenant_alpha"])
        digest = "b9" * 32
        decision_ids = tuple(uuid.uuid4() for _ in range(8))
        expires_at = datetime.now(UTC) + timedelta(minutes=5)
        barrier = Barrier(len(decision_ids))

        def reserve(decision_id: UUID) -> bool:
            barrier.wait(timeout=10)
            return PostgresReplayBackend(
                self._capauth_execute, tenant_id=tenant_id
            ).reserve(
                credential_digest=digest,
                decision_id=str(decision_id),
                expires_at=expires_at,
            )

        with ThreadPoolExecutor(max_workers=len(decision_ids)) as executor:
            outcomes = tuple(executor.map(reserve, decision_ids))
        self.assertEqual(1, outcomes.count(True))
        self.assertEqual(len(decision_ids) - 1, outcomes.count(False))
        stored = self._psql(
            "postgres",
            f"""
            SELECT count(*) || ':' || min(decision_id::text)
            FROM sklegal_identity.capability_replay_reservations
            WHERE tenant_id = '{tenant_id}'
              AND credential_digest = '{digest}';
            """,
        ).stdout.strip()
        count, stored_decision_id = stored.split(":", maxsplit=1)
        self.assertEqual("1", count)
        self.assertIn(UUID(stored_decision_id), decision_ids)

    def test_00_migrator_exact_profile_rejects_every_drift_before_bootstrap(
        self,
    ) -> None:
        database = "sklegal_migrator_profile_probe"
        graph_role = "sklegal_migrator_graph_probe"
        self._psql(
            "postgres",
            f"""
            DROP DATABASE IF EXISTS {database} WITH (FORCE);
            CREATE DATABASE {database};
            CREATE ROLE {graph_role} NOLOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;
            """,
        )
        self.addCleanup(
            self._psql,
            "postgres",
            f"DROP DATABASE IF EXISTS {database} WITH (FORCE);",
        )
        self.addCleanup(
            self._psql,
            "postgres",
            f"""
            ALTER ROLE sklegal_migrator LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;
            REVOKE {graph_role} FROM sklegal_migrator;
            REVOKE sklegal_migrator FROM {graph_role};
            DROP ROLE IF EXISTS {graph_role};
            """,
        )
        exact_profile = (
            "ALTER ROLE sklegal_migrator LOGIN NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;"
        )
        attribute_drifts = (
            ("NOLOGIN", "ALTER ROLE sklegal_migrator NOLOGIN;"),
            ("INHERIT", "ALTER ROLE sklegal_migrator INHERIT;"),
            ("SUPERUSER", "ALTER ROLE sklegal_migrator SUPERUSER;"),
            ("BYPASSRLS", "ALTER ROLE sklegal_migrator BYPASSRLS;"),
            ("CREATEROLE", "ALTER ROLE sklegal_migrator CREATEROLE;"),
            ("CREATEDB", "ALTER ROLE sklegal_migrator CREATEDB;"),
            ("REPLICATION", "ALTER ROLE sklegal_migrator REPLICATION;"),
        )
        for label, mutation in attribute_drifts:
            with self.subTest(profile_drift=label):
                try:
                    self._psql("postgres", mutation)
                    result = self._migrate_database(database, check=False)
                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual(
                        "t",
                        self._psql_in_database(
                            database,
                            "postgres",
                            "SELECT to_regnamespace('sklegal_migrations') IS NULL;",
                        ).stdout.strip(),
                    )
                finally:
                    self._psql("postgres", exact_profile)

        membership_drifts = (
            (
                "migrator_is_member",
                f"GRANT {graph_role} TO sklegal_migrator;",
                f"REVOKE {graph_role} FROM sklegal_migrator;",
            ),
            (
                "migrator_is_granted",
                f"GRANT sklegal_migrator TO {graph_role};",
                f"REVOKE sklegal_migrator FROM {graph_role};",
            ),
        )
        for label, mutation, restoration in membership_drifts:
            with self.subTest(profile_drift=label):
                try:
                    self._psql("postgres", mutation)
                    result = self._migrate_database(database, check=False)
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("no role membership edge", result.stderr)
                    self.assertEqual(
                        "t",
                        self._psql_in_database(
                            database,
                            "postgres",
                            "SELECT to_regnamespace('sklegal_migrations') IS NULL;",
                        ).stdout.strip(),
                    )
                finally:
                    self._psql("postgres", restoration)
        self.assertEqual(
            "t|t|f|f|f|f|f|f|f",
            self._psql(
                "postgres",
                """
                SELECT rolname = 'sklegal_migrator', rolcanlogin, rolinherit,
                       rolsuper, rolbypassrls, rolcreaterole, rolcreatedb,
                       rolreplication,
                       EXISTS (
                           SELECT 1 FROM pg_auth_members
                           WHERE member = role_record.oid
                              OR roleid = role_record.oid
                       )
                FROM pg_roles AS role_record
                WHERE rolname = 'sklegal_migrator';
                """,
            ).stdout.strip(),
        )
        self.assertEqual(
            f"migration status: {MIGRATION_TOTAL}/{MIGRATION_TOTAL} applied",
            self._migrate("status").stdout.strip(),
        )


if __name__ == "__main__":
    unittest.main()
