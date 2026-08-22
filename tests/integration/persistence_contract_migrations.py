"""Migration preflight and migrator profile persistence contract tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.integration.persistence_contract_support import (
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
                for steps in range(1, 8)
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
