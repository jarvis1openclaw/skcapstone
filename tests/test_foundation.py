from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import secrets
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_fixture_safety  # noqa: E402
import check_migrations  # noqa: E402
import check_secrets  # noqa: E402
import manage_migrations  # noqa: E402
import provision_postgres_principal  # noqa: E402


class FoundationTests(unittest.TestCase):
    def test_required_commands_are_exposed(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        for target in (
            "bootstrap:",
            "check:",
            "clean-room-check:",
            "dev-deps:",
            "format-check lint type-check unit-test integration-test",
            "migration-check sbom secret-scan vulnerability-scan fixture-check license-audit:",
            "license-audit-live:",
        ):
            with self.subTest(target=target):
                self.assertIn(target, makefile)

    def test_bootstrap_validates_fresh_and_existing_uv_versions(self) -> None:
        bootstrap = (REPO_ROOT / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn('verify_uv_version "$extracted/uv"', bootstrap)
        self.assertEqual(2, bootstrap.count('verify_uv_version "$tools_bin/uv"'))

    def test_migration_manifest_accepts_reversible_persistence_set(self) -> None:
        migrations = check_migrations.validate()
        manifest = json.loads(
            (REPO_ROOT / "migrations" / "manifest.json").read_text(encoding="utf-8")
        )
        expected = [entry["file"] for entry in manifest["migrations"]]
        self.assertEqual(expected, migrations)
        self.assertEqual("0001_persistence_foundation.sql", migrations[0])
        self.assertIn("0007_append_only_audit_outbox.sql", migrations)

    def test_migration_digest_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "0001_fixture.sql").write_text("select 1;\n", encoding="utf-8")
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "migrations": [
                            {"file": "0001_fixture.sql", "sha256": "0" * 64}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                check_migrations.MigrationError, "digest mismatch"
            ):
                check_migrations.validate(root)

    def test_migration_manifest_accepts_ordered_hashed_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = b"-- sklegal:up\nselect 1;\n-- sklegal:down\nselect 2;\n"
            (root / "0001_fixture.sql").write_bytes(body)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "migrations": [
                            {
                                "file": "0001_fixture.sql",
                                "sha256": hashlib.sha256(body).hexdigest(),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(["0001_fixture.sql"], check_migrations.validate(root))

    def _write_migration_set(self, temporary: str, bodies: list[str]) -> Path:
        root = Path(temporary)
        entries = []
        for index, body in enumerate(bodies, start=1):
            name = f"{index:04d}_synthetic.sql"
            payload = f"-- sklegal:up\n{body}\n-- sklegal:down\nselect 2;\n".encode()
            (root / name).write_bytes(payload)
            entries.append(
                {"file": name, "sha256": hashlib.sha256(payload).hexdigest()}
            )
        (root / "manifest.json").write_text(
            json.dumps({"schema_version": 1, "migrations": entries}),
            encoding="utf-8",
        )
        return root

    def test_migration_lint_accepts_blanket_covered_function(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._write_migration_set(
                temporary,
                [
                    "CREATE FUNCTION sklegal_legal.probe(id uuid) RETURNS boolean"
                    " LANGUAGE sql AS $function$ SELECT true $function$;",
                    "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA sklegal_legal FROM PUBLIC;",
                ],
            )
            self.assertEqual(2, len(check_migrations.validate(root)))

    def test_migration_lint_rejects_function_created_after_last_blanket(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._write_migration_set(
                temporary,
                [
                    "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA sklegal_legal FROM PUBLIC;",
                    "CREATE FUNCTION sklegal_legal.probe(id uuid) RETURNS boolean"
                    " LANGUAGE sql AS $function$ SELECT true $function$;",
                ],
            )
            with self.assertRaisesRegex(
                check_migrations.MigrationError, "missing REVOKE FROM PUBLIC"
            ):
                check_migrations.validate(root)

    def test_migration_lint_rejects_recreated_function_missing_revoke(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._write_migration_set(
                temporary,
                [
                    "CREATE FUNCTION sklegal_legal.probe(id uuid) RETURNS boolean"
                    " LANGUAGE sql AS $function$ SELECT true $function$;\n"
                    "REVOKE ALL ON FUNCTION sklegal_legal.probe(uuid) FROM PUBLIC;\n"
                    "GRANT EXECUTE ON FUNCTION sklegal_legal.probe(uuid)"
                    " TO sklegal_runtime;",
                    "DROP FUNCTION sklegal_legal.probe(uuid);\n"
                    "CREATE FUNCTION sklegal_legal.probe(id uuid) RETURNS boolean"
                    " LANGUAGE sql AS $function$ SELECT false $function$;",
                ],
            )
            with self.assertRaisesRegex(
                check_migrations.MigrationError, "missing REVOKE FROM PUBLIC"
            ):
                check_migrations.validate(root)

    def test_migration_lint_rejects_recreated_function_missing_grants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._write_migration_set(
                temporary,
                [
                    "CREATE FUNCTION sklegal_legal.probe(id uuid) RETURNS boolean"
                    " LANGUAGE sql AS $function$ SELECT true $function$;\n"
                    "REVOKE ALL ON FUNCTION sklegal_legal.probe(uuid) FROM PUBLIC;\n"
                    "GRANT EXECUTE ON FUNCTION sklegal_legal.probe(uuid)"
                    " TO sklegal_runtime;",
                    "DROP FUNCTION sklegal_legal.probe(uuid);\n"
                    "CREATE FUNCTION sklegal_legal.probe(id uuid) RETURNS boolean"
                    " LANGUAGE sql AS $function$ SELECT false $function$;\n"
                    "REVOKE ALL ON FUNCTION sklegal_legal.probe(uuid) FROM PUBLIC;",
                ],
            )
            with self.assertRaisesRegex(check_migrations.MigrationError, "lost grants"):
                check_migrations.validate(root)

    def test_migration_lint_accepts_create_or_replace_recreation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._write_migration_set(
                temporary,
                [
                    "CREATE FUNCTION sklegal_legal.probe(id uuid) RETURNS boolean"
                    " LANGUAGE sql AS $function$ SELECT true $function$;\n"
                    "REVOKE ALL ON FUNCTION sklegal_legal.probe(uuid) FROM PUBLIC;\n"
                    "GRANT EXECUTE ON FUNCTION sklegal_legal.probe(uuid)"
                    " TO sklegal_runtime;",
                    "CREATE OR REPLACE FUNCTION sklegal_legal.probe(id uuid)"
                    " RETURNS boolean LANGUAGE sql AS $function$ SELECT false $function$;",
                ],
            )
            self.assertEqual(2, len(check_migrations.validate(root)))

    def test_migration_without_reversible_markers_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = b"select 1;\n"
            (root / "0001_fixture.sql").write_bytes(body)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "migrations": [
                            {
                                "file": "0001_fixture.sql",
                                "sha256": hashlib.sha256(body).hexdigest(),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                check_migrations.MigrationError, "one up and down marker"
            ):
                check_migrations.validate(root)

    def test_migration_steps_fail_closed(self) -> None:
        self.assertIsNone(manage_migrations.parse_steps("all"))
        self.assertEqual(2, manage_migrations.parse_steps("2"))
        for invalid in ("0", "-1", "many"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(argparse.ArgumentTypeError):
                    manage_migrations.parse_steps(invalid)

    def test_migration_transport_keeps_credentials_out_of_argv(self) -> None:
        direct = manage_migrations.PsqlClient(
            direct=True,
            docker_container=None,
            database="synthetic_database",
            user="sklegal_migrator",
        )
        docker = manage_migrations.PsqlClient(
            direct=False,
            docker_container="synthetic-container",
            database="synthetic_database",
            user="sklegal_migrator",
        )
        for command in (direct.command, docker.command):
            rendered = " ".join(command)
            self.assertNotIn("postgresql://", rendered)
            self.assertNotIn("postgres://", rendered)
            self.assertNotIn("password", rendered.casefold())
            self.assertIn("--username sklegal_migrator", rendered)
            self.assertIn("--dbname synthetic_database", rendered)

    def test_runtime_grants_exclude_direct_controlled_evidence_writes(self) -> None:
        self.assertNotIn(
            "sklegal_legal.execution_events",
            provision_postgres_principal.INSERT_TABLES,
        )
        self.assertNotIn(
            "sklegal_legal.execution_receipts",
            provision_postgres_principal.INSERT_TABLES,
        )
        self.assertNotIn(
            "sklegal_audit.events", provision_postgres_principal.INSERT_TABLES
        )
        self.assertNotIn(
            "sklegal_legal.communications",
            provision_postgres_principal.INSERT_TABLES,
        )
        self.assertNotIn(
            "sklegal_legal.communication_participants",
            provision_postgres_principal.INSERT_TABLES,
        )
        self.assertNotIn(
            "sklegal_legal.executions", provision_postgres_principal.UPDATE_TABLES
        )
        self.assertEqual(
            {
                "sklegal_legal.transition_approval(uuid, uuid, uuid, bigint, text, text)",
                "sklegal_legal.transition_execution(uuid, uuid, uuid, bigint, text, text, uuid, uuid, uuid, text, text, timestamp with time zone, timestamp with time zone)",
                "sklegal_legal.transition_communication(uuid, uuid, uuid, bigint, text, uuid, uuid, text, uuid)",
                "sklegal_legal.transition_work_product(uuid, uuid, uuid, bigint, text, uuid, uuid)",
                "sklegal_legal.transition_work_product_version(uuid, uuid, uuid, bigint, text)",
            },
            {
                function
                for function in provision_postgres_principal.EXECUTE_FUNCTIONS
                if ".transition_" in function
            },
        )
        self.assertIn(
            "sklegal_legal.create_communication(uuid, uuid, uuid, text, text, "
            "text, uuid[], uuid, bigint, text, sklegal_legal.data_classification, "
            "sklegal_legal.record_completeness, timestamp with time zone, "
            "timestamp with time zone)",
            provision_postgres_principal.EXECUTE_FUNCTIONS,
        )

    def test_repository_fixtures_are_synthetic(self) -> None:
        checked = check_fixture_safety.validate()
        self.assertGreaterEqual(len(checked), 1)

    def test_secret_scan_excludes_only_generated_license_artifacts(self) -> None:
        pattern = check_secrets.EXCLUDE_FILES
        self.assertIn("DOC-HAUS-RIGHTS-INVENTORY", pattern)
        self.assertIn("DOC-HAUS-SBOM", pattern)
        self.assertNotIn("doc-haus-audit", pattern)
        self.assertNotIn("DOC-HAUS-LICENSE-PROVENANCE-AUDIT", pattern)
        self.assertNotIn("migrations/manifest", pattern)

    def test_secret_scan_detects_new_manifest_high_entropy_field(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            migrations = root / "migrations"
            migrations.mkdir()
            (migrations / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "migrations": [],
                        "runtime_secret": secrets.token_hex(32),
                    }
                ),
                encoding="utf-8",
            )
            baseline = root / "baseline.json"
            baseline.write_text('{"results": {}}\n', encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                result = check_secrets.scan(baseline, repo_root=root)
            self.assertEqual(1, result)

    def test_protected_identifier_in_fixture_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "unsafe.json").write_text(
                '{"source": "INC-999"}\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "legacy matter identifier"):
                check_fixture_safety.validate(root)

    def test_untracked_migration_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "manifest.json").write_text(
                '{"schema_version": 1, "migrations": []}\n', encoding="utf-8"
            )
            (root / "0001_untracked.sql").write_text("select 1;\n", encoding="utf-8")
            with self.assertRaisesRegex(
                check_migrations.MigrationError,
                "migration files and manifest entries differ",
            ):
                check_migrations.validate(root)


if __name__ == "__main__":
    unittest.main()
