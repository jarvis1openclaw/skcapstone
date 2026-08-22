"""Scope, privilege, and tenant status persistence contract tests."""

from __future__ import annotations

import subprocess
import unittest

from tests.integration.persistence_contract_support import (
    PROVISIONER,
    REPO_ROOT,
    PersistenceContractBase,
)


class PersistenceContract02ScopePrivilegeTests(PersistenceContractBase):
    def test_02_unfiltered_scope_and_scoped_duplicate_ids(self) -> None:
        value = self.fixture
        self.assertEqual(
            value["tenant_alpha"],
            self._psql(
                "sklegal_test_alpha_one", "SELECT id FROM sklegal_identity.tenants;"
            ).stdout.strip(),
        )
        self.assertEqual(
            value["matter_alpha_one"],
            self._psql(
                "sklegal_test_alpha_one", "SELECT id FROM sklegal_legal.matters;"
            ).stdout.strip(),
        )
        shared_id = "30000000-0000-4000-8000-000000000001"
        for role, tenant, matter in (
            (
                "sklegal_test_alpha_one",
                value["tenant_alpha"],
                value["matter_alpha_one"],
            ),
            (
                "sklegal_test_alpha_two",
                value["tenant_alpha"],
                value["matter_alpha_two"],
            ),
            ("sklegal_test_beta_one", value["tenant_beta"], value["matter_beta_one"]),
        ):
            self._psql(
                role,
                f"""
                INSERT INTO sklegal_legal.forums
                    (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
                VALUES ('{shared_id}', '{tenant}', '{matter}',
                        'Synthetic Forum', 'Synthetic', 'other');
                """,
            )
        visible = self._psql(
            "sklegal_test_alpha_one",
            f"SELECT count(*) FROM sklegal_legal.forums WHERE id = '{shared_id}';",
        )
        self.assertEqual("1", visible.stdout.strip())
        duplicate = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('{shared_id}', '{value["tenant_alpha"]}',
                    '{value["matter_alpha_one"]}', 'Duplicate', 'Synthetic', 'other');
            """,
            check=False,
        )
        self.assertNotEqual(0, duplicate.returncode)
        cross = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.proceedings
                (id, tenant_id, matter_id, title, forum_id)
            VALUES ('30000000-0000-4000-8000-000000000002',
                    '{value["tenant_alpha"]}', '{value["matter_alpha_one"]}',
                    'Synthetic cross-scope probe',
                    '30000000-0000-4000-8000-000000000099');
            """,
            check=False,
        )
        self.assertNotEqual(0, cross.returncode)
        self.assertIn("foreign key", cross.stderr)

        denied_updates = []
        denied_inserts = []
        for inaccessible_tenant, inaccessible_matter, suffix in (
            (value["tenant_alpha"], value["matter_alpha_two"], "same-tenant"),
            (value["tenant_beta"], value["matter_beta_one"], "cross-tenant"),
        ):
            denied_updates.append(
                self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    UPDATE sklegal_legal.forums
                    SET name = 'Unauthorized {suffix}', version = version + 1
                    WHERE tenant_id = '{inaccessible_tenant}'
                      AND matter_id = '{inaccessible_matter}'
                      AND id = '{shared_id}';
                    """,
                    check=False,
                )
            )
            denied_inserts.append(
                self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    INSERT INTO sklegal_legal.forums
                        (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
                    VALUES ('30000000-0000-4000-8000-0000000000{suffix == "cross-tenant" and "04" or "03"}',
                            '{inaccessible_tenant}', '{inaccessible_matter}',
                            'Unauthorized {suffix}', 'Synthetic', 'other');
                    """,
                    check=False,
                )
            )
        self.assertTrue(all(result.returncode != 0 for result in denied_updates))
        self.assertTrue(
            all("permission denied" in result.stderr for result in denied_updates)
        )
        self.assertTrue(all(result.returncode != 0 for result in denied_inserts))
        self.assertTrue(
            all("row-level security" in result.stderr for result in denied_inserts)
        )

        rollback_id = "30000000-0000-4000-8000-000000000005"
        ordinary_rollback = self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('{rollback_id}', '{value["tenant_alpha"]}',
                    '{value["matter_alpha_one"]}', 'Rollback probe',
                    'Synthetic', 'other');
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('{rollback_id}', '{value["tenant_alpha"]}',
                    '{value["matter_alpha_one"]}', 'Duplicate rollback probe',
                    'Synthetic', 'other');
            COMMIT;
            """,
            check=False,
        )
        self.assertNotEqual(0, ordinary_rollback.returncode)
        rolled_back = self._psql(
            "postgres",
            f"SELECT count(*) FROM sklegal_legal.forums WHERE id = '{rollback_id}';",
        )
        self.assertEqual("0", rolled_back.stdout.strip())

    def test_03_least_privilege_grants_and_role_bypass_resistance(self) -> None:
        value = self.fixture
        dangerous = self._psql(
            "postgres",
            """
            SELECT count(*) FROM information_schema.role_table_grants
            WHERE grantee LIKE 'sklegal_test_%'
              AND table_schema LIKE 'sklegal_%'
              AND privilege_type IN ('DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER');
            """,
        )
        self.assertEqual("0", dangerous.stdout.strip())
        evidence_writes = self._psql(
            "postgres",
            """
            SELECT count(*) FROM information_schema.role_table_grants
            WHERE grantee IN (
                'sklegal_test_alpha_one', 'sklegal_test_alpha_two',
                'sklegal_test_beta_one'
            ) AND table_schema = 'sklegal_legal'
              AND table_name IN ('execution_events', 'execution_receipts')
              AND privilege_type <> 'SELECT';
            """,
        )
        self.assertEqual("0", evidence_writes.stdout.strip())
        audit_writes = self._psql(
            "postgres",
            """
            SELECT count(*) FROM information_schema.role_table_grants
            WHERE grantee LIKE 'sklegal_test_%'
              AND table_schema = 'sklegal_audit' AND table_name = 'events'
              AND privilege_type <> 'SELECT';
            """,
        )
        self.assertEqual("0", audit_writes.stdout.strip())
        self.assertEqual(
            "0",
            self._psql(
                "sklegal_migrator", "SELECT count(*) FROM sklegal_legal.matters;"
            ).stdout.strip(),
        )
        unsafe = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_identity.database_role_bindings
                (database_role, tenant_id, principal_id)
            VALUES ('sklegal_test_bypass', '{value["tenant_alpha"]}',
                    '{value["principal_alpha_one"]}');
            """,
            check=False,
        )
        self.assertNotEqual(0, unsafe.returncode)
        self.assertIn("exact safe role profile", unsafe.stderr)
        self_enroll = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.matter_memberships
                (tenant_id, matter_id, principal_id, membership_role)
            VALUES ('{value["tenant_alpha"]}', '{value["matter_alpha_two"]}',
                    '{value["principal_alpha_one"]}', 'member');
            """,
            check=False,
        )
        self.assertNotEqual(0, self_enroll.returncode)

        self._psql(
            "postgres",
            """
            CREATE ROLE sklegal_test_object_owner LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            CREATE FUNCTION sklegal_legal.synthetic_owned_function()
            RETURNS integer LANGUAGE sql IMMUTABLE RETURN 1;
            ALTER FUNCTION sklegal_legal.synthetic_owned_function()
                OWNER TO sklegal_test_object_owner;
            """,
        )
        owner_provision = subprocess.run(
            [
                str(REPO_ROOT / ".tools" / "bin" / "uv"),
                "run",
                "--locked",
                "python",
                str(PROVISIONER),
                "--docker-container",
                self.container,
                "--runtime-role",
                "sklegal_test_object_owner",
                "--tenant-id",
                value["tenant_alpha"],
                "--principal-id",
                value["principal_alpha_one"],
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, owner_provision.returncode)
        self.assertIn("cannot own an SKLegal database object", owner_provision.stderr)
        self._psql(
            "postgres",
            """
            DROP FUNCTION sklegal_legal.synthetic_owned_function();
            DROP ROLE sklegal_test_object_owner;
            """,
        )

        self._psql(
            "postgres",
            """
            CREATE ROLE sklegal_test_role_graph NOLOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            GRANT sklegal_test_role_graph TO sklegal_test_alpha_one;
            """,
        )
        self.assertEqual(
            "f",
            self._psql(
                "sklegal_test_alpha_one",
                "SELECT sklegal_identity.runtime_role_is_safe();",
            ).stdout.strip(),
        )
        drifted = subprocess.run(
            [
                str(REPO_ROOT / ".tools" / "bin" / "uv"),
                "run",
                "--locked",
                "python",
                str(PROVISIONER),
                "--docker-container",
                self.container,
                "--runtime-role",
                "sklegal_test_alpha_one",
                "--tenant-id",
                value["tenant_alpha"],
                "--principal-id",
                value["principal_alpha_one"],
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, drifted.returncode)
        self.assertIn("role graph", drifted.stderr)
        self._psql(
            "postgres",
            """
            REVOKE sklegal_test_role_graph FROM sklegal_test_alpha_one;
            DROP ROLE sklegal_test_role_graph;
            ALTER ROLE sklegal_test_alpha_one BYPASSRLS;
            """,
        )
        self.assertEqual(
            "f",
            self._psql(
                "sklegal_test_alpha_one",
                "SELECT sklegal_identity.runtime_role_is_safe();",
            ).stdout.strip(),
        )
        bypass_drift = subprocess.run(
            [
                str(REPO_ROOT / ".tools" / "bin" / "uv"),
                "run",
                "--locked",
                "python",
                str(PROVISIONER),
                "--docker-container",
                self.container,
                "--runtime-role",
                "sklegal_test_alpha_one",
                "--tenant-id",
                value["tenant_alpha"],
                "--principal-id",
                value["principal_alpha_one"],
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, bypass_drift.returncode)
        self.assertIn("least-privilege profile", bypass_drift.stderr)
        self._psql("postgres", "ALTER ROLE sklegal_test_alpha_one NOBYPASSRLS;")
        self.assertEqual(
            "t",
            self._psql(
                "sklegal_test_alpha_one",
                "SELECT sklegal_identity.runtime_role_is_safe();",
            ).stdout.strip(),
        )

    def test_03_live_tenant_and_principal_status_fail_closed_and_reactivate(
        self,
    ) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        principal = value["principal_alpha_one"]
        role = "sklegal_test_alpha_one"
        baseline_forum = "33000000-0000-4000-8000-000000000001"
        first_work_product = "33000000-0000-4000-8000-000000000010"
        first_artifact = "33000000-0000-4000-8000-000000000011"
        second_work_product = "33000000-0000-4000-8000-000000000012"
        second_artifact = "33000000-0000-4000-8000-000000000013"
        digest = "e" * 64
        self._psql(
            role,
            f"""
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('{baseline_forum}', '{tenant}', '{matter}',
                    'Synthetic status baseline', 'Synthetic', 'other');
            BEGIN;
            INSERT INTO sklegal_legal.work_products
                (id, tenant_id, matter_id, title, work_product_kind,
                 current_version_id, current_version_number,
                 current_content_sha256)
            VALUES ('{first_work_product}', '{tenant}', '{matter}',
                    'Synthetic tenant status work product', 'memo',
                    '{first_artifact}', 1, '{digest}');
            INSERT INTO sklegal_legal.work_product_versions
                (id, tenant_id, matter_id, work_product_id, version_number,
                 content_sha256, source_artifact_id)
            VALUES ('{first_artifact}', '{tenant}', '{matter}',
                    '{first_work_product}', 1, '{digest}',
                    '33000000-0000-4000-8000-000000000014');
            COMMIT;
            """,
        )

        def provision_result(
            target_role: str = role,
            target_tenant: str = tenant,
            target_principal: str = principal,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    str(REPO_ROOT / ".tools" / "bin" / "uv"),
                    "run",
                    "--locked",
                    "python",
                    str(PROVISIONER),
                    "--docker-container",
                    self.container,
                    "--runtime-role",
                    target_role,
                    "--tenant-id",
                    target_tenant,
                    "--principal-id",
                    target_principal,
                ],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        def set_tenant_status(status: str) -> str:
            self._psql(
                "postgres",
                f"""
                UPDATE sklegal_identity.tenants
                SET status = '{status}', version = version + 1
                WHERE id = '{tenant}';
                """,
            )
            return self._psql(
                "postgres",
                f"""
                SELECT tenant.status || '|' || binding.tenant_active::int ||
                       '|' || binding.principal_active::int
                FROM sklegal_identity.database_role_bindings AS binding
                JOIN sklegal_identity.tenants AS tenant
                  ON tenant.id = binding.tenant_id
                WHERE binding.database_role = '{role}';
                """,
            ).stdout.strip()

        def set_principal_status(status: str) -> str:
            self._psql(
                "postgres",
                f"""
                UPDATE sklegal_identity.principals
                SET status = '{status}', version = version + 1
                WHERE tenant_id = '{tenant}' AND id = '{principal}';
                """,
            )
            return self._psql(
                "postgres",
                f"""
                SELECT principal.status || '|' || binding.tenant_active::int ||
                       '|' || binding.principal_active::int
                FROM sklegal_identity.database_role_bindings AS binding
                JOIN sklegal_identity.principals AS principal
                  ON principal.tenant_id = binding.tenant_id
                 AND principal.id = binding.principal_id
                WHERE binding.database_role = '{role}';
                """,
            ).stdout.strip()

        def assert_runtime_inactive(insert_id: int) -> None:
            self.assertEqual(
                "f",
                self._psql(
                    role, "SELECT sklegal_identity.runtime_role_is_safe();"
                ).stdout.strip(),
            )
            self.assertEqual(
                "0",
                self._psql(
                    role,
                    f"SELECT count(*) FROM sklegal_legal.forums "
                    f"WHERE id = '{baseline_forum}';",
                ).stdout.strip(),
            )
            denied = self._psql(
                role,
                f"""
                INSERT INTO sklegal_legal.forums
                    (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
                VALUES ('33000000-0000-4000-8000-{insert_id:012d}',
                        '{tenant}', '{matter}', 'Synthetic inactive insert',
                        'Synthetic', 'other');
                """,
                check=False,
            )
            self.assertNotEqual(0, denied.returncode)
            self.assertIn("row-level security", denied.stderr)
            reprovision = provision_result()
            self.assertNotEqual(0, reprovision.returncode)
            self.assertIn(
                "active principal and tenant membership are required",
                reprovision.stderr,
            )

        for index, inactive_status in enumerate(("suspended",), start=2):
            with self.subTest(tenant_status=inactive_status):
                self.assertEqual(
                    f"{inactive_status}|0|1",
                    set_tenant_status(inactive_status),
                )
                assert_runtime_inactive(index)
                denied_transition = self._psql(
                    role,
                    f"""
                    SELECT status
                    FROM sklegal_legal.transition_work_product_version(
                        '{tenant}', '{matter}', '{first_artifact}', 1,
                        'frozen');
                    """,
                    check=False,
                )
                self.assertNotEqual(0, denied_transition.returncode)
                self.assertIn("not authorized", denied_transition.stderr)
                self.assertEqual("active|1|1", set_tenant_status("active"))
                self.assertEqual(
                    "t",
                    self._psql(
                        role, "SELECT sklegal_identity.runtime_role_is_safe();"
                    ).stdout.strip(),
                )
                self.assertEqual(
                    "1",
                    self._psql(
                        role,
                        f"SELECT count(*) FROM sklegal_legal.forums "
                        f"WHERE id = '{baseline_forum}';",
                    ).stdout.strip(),
                )
        self.assertEqual(
            "frozen",
            self._psql(
                role,
                f"""
                SELECT status
                FROM sklegal_legal.transition_work_product_version(
                    '{tenant}', '{matter}', '{first_artifact}', 1, 'frozen');
                """,
            ).stdout.strip(),
        )

        self._psql(
            role,
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.work_products
                (id, tenant_id, matter_id, title, work_product_kind,
                 current_version_id, current_version_number,
                 current_content_sha256)
            VALUES ('{second_work_product}', '{tenant}', '{matter}',
                    'Synthetic principal status work product', 'memo',
                    '{second_artifact}', 1, '{digest}');
            INSERT INTO sklegal_legal.work_product_versions
                (id, tenant_id, matter_id, work_product_id, version_number,
                 content_sha256, source_artifact_id)
            VALUES ('{second_artifact}', '{tenant}', '{matter}',
                    '{second_work_product}', 1, '{digest}',
                    '33000000-0000-4000-8000-000000000015');
            COMMIT;
            """,
        )

        proposed_tenant = "33100000-0000-4000-8000-000000000101"
        proposed_principal = "33100000-0000-4000-8000-000000000102"
        proposed_role = "sklegal_test_proposed_identity"
        closed_tenant = "33100000-0000-4000-8000-000000000111"
        closed_principal = "33100000-0000-4000-8000-000000000112"
        closed_role = "sklegal_test_closed_identity"
        revoked_tenant = "33100000-0000-4000-8000-000000000121"
        revoked_principal = "33100000-0000-4000-8000-000000000122"
        revoked_role = "sklegal_test_revoked_identity"
        self._psql(
            "postgres",
            f"""
            CREATE ROLE {proposed_role} LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;
            CREATE ROLE {closed_role} LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;
            CREATE ROLE {revoked_role} LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;
            INSERT INTO sklegal_identity.tenants
                (id, tenant_id, slug, name, status)
            VALUES
                ('{proposed_tenant}', '{proposed_tenant}',
                 'synthetic-proposed-identity', 'Synthetic Proposed Identity',
                 'proposed'),
                ('{closed_tenant}', '{closed_tenant}',
                 'synthetic-closed-identity', 'Synthetic Closed Identity',
                 'active'),
                ('{revoked_tenant}', '{revoked_tenant}',
                 'synthetic-revoked-identity', 'Synthetic Revoked Identity',
                 'active');
            INSERT INTO sklegal_identity.principals
                (id, tenant_id, principal_kind, display_name, status)
            VALUES
                ('{proposed_principal}', '{proposed_tenant}', 'human',
                 'Synthetic Proposed Principal', 'active'),
                ('{closed_principal}', '{closed_tenant}', 'human',
                 'Synthetic Closed Principal', 'active'),
                ('{revoked_principal}', '{revoked_tenant}', 'human',
                 'Synthetic Revoked Principal', 'active');
            INSERT INTO sklegal_identity.tenant_memberships
                (tenant_id, principal_id, membership_role)
            VALUES
                ('{proposed_tenant}', '{proposed_principal}', 'administrator'),
                ('{closed_tenant}', '{closed_principal}', 'administrator'),
                ('{revoked_tenant}', '{revoked_principal}', 'administrator');
            """,
        )
        proposed_denied = provision_result(
            proposed_role, proposed_tenant, proposed_principal
        )
        self.assertNotEqual(0, proposed_denied.returncode)
        self.assertIn(
            "active principal and tenant membership are required",
            proposed_denied.stderr,
        )
        self._psql(
            "postgres",
            f"""
            UPDATE sklegal_identity.tenants
            SET status = 'active', version = version + 1
            WHERE id = '{proposed_tenant}';
            """,
        )
        self._provision(proposed_role, proposed_tenant, proposed_principal)
        self.assertEqual(
            "t",
            self._psql(
                proposed_role, "SELECT sklegal_identity.runtime_role_is_safe();"
            ).stdout.strip(),
        )

        self._provision(closed_role, closed_tenant, closed_principal)
        self._psql(
            "postgres",
            f"""
            UPDATE sklegal_identity.tenants
            SET status = 'closed', version = version + 1
            WHERE id = '{closed_tenant}';
            """,
        )
        self.assertEqual(
            "0|1",
            self._psql(
                "postgres",
                f"""
                SELECT binding.tenant_active::int || '|' ||
                       binding.principal_active::int
                FROM sklegal_identity.database_role_bindings AS binding
                WHERE binding.database_role = '{closed_role}';
                """,
            ).stdout.strip(),
        )
        self.assertEqual(
            "f",
            self._psql(
                closed_role, "SELECT sklegal_identity.runtime_role_is_safe();"
            ).stdout.strip(),
        )
        closed_reprovision = provision_result(
            closed_role, closed_tenant, closed_principal
        )
        self.assertNotEqual(0, closed_reprovision.returncode)
        closed_reactivation = self._psql(
            "postgres",
            f"""
            UPDATE sklegal_identity.tenants
            SET status = 'active', version = version + 1
            WHERE id = '{closed_tenant}';
            """,
            check=False,
        )
        self.assertNotEqual(0, closed_reactivation.returncode)
        self.assertIn("invalid identity status transition", closed_reactivation.stderr)

        self._provision(revoked_role, revoked_tenant, revoked_principal)
        self._psql(
            "postgres",
            f"""
            UPDATE sklegal_identity.principals
            SET status = 'revoked', version = version + 1
            WHERE tenant_id = '{revoked_tenant}' AND id = '{revoked_principal}';
            """,
        )
        self.assertEqual(
            "1|0",
            self._psql(
                "postgres",
                f"""
                SELECT binding.tenant_active::int || '|' ||
                       binding.principal_active::int
                FROM sklegal_identity.database_role_bindings AS binding
                WHERE binding.database_role = '{revoked_role}';
                """,
            ).stdout.strip(),
        )
        self.assertEqual(
            "f",
            self._psql(
                revoked_role, "SELECT sklegal_identity.runtime_role_is_safe();"
            ).stdout.strip(),
        )
        revoked_reprovision = provision_result(
            revoked_role, revoked_tenant, revoked_principal
        )
        self.assertNotEqual(0, revoked_reprovision.returncode)
        revoked_reactivation = self._psql(
            "postgres",
            f"""
            UPDATE sklegal_identity.principals
            SET status = 'active', version = version + 1
            WHERE tenant_id = '{revoked_tenant}' AND id = '{revoked_principal}';
            """,
            check=False,
        )
        self.assertNotEqual(0, revoked_reactivation.returncode)
        self.assertIn("invalid identity status transition", revoked_reactivation.stderr)
        for index, inactive_status in enumerate(("suspended",), start=5):
            with self.subTest(principal_status=inactive_status):
                self.assertEqual(
                    f"{inactive_status}|1|0",
                    set_principal_status(inactive_status),
                )
                assert_runtime_inactive(index)
                denied_transition = self._psql(
                    role,
                    f"""
                    SELECT status
                    FROM sklegal_legal.transition_work_product_version(
                        '{tenant}', '{matter}', '{second_artifact}', 1,
                        'frozen');
                    """,
                    check=False,
                )
                self.assertNotEqual(0, denied_transition.returncode)
                self.assertIn("not authorized", denied_transition.stderr)
                self.assertEqual("active|1|1", set_principal_status("active"))
                self.assertEqual(
                    "t",
                    self._psql(
                        role, "SELECT sklegal_identity.runtime_role_is_safe();"
                    ).stdout.strip(),
                )
        self.assertEqual(
            "frozen",
            self._psql(
                role,
                f"""
                SELECT status
                FROM sklegal_legal.transition_work_product_version(
                    '{tenant}', '{matter}', '{second_artifact}', 1, 'frozen');
                """,
            ).stdout.strip(),
        )
        self._psql(
            role,
            f"""
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('33000000-0000-4000-8000-000000000007',
                    '{tenant}', '{matter}', 'Synthetic reactivated insert',
                    'Synthetic', 'other');
            """,
        )


if __name__ == "__main__":
    unittest.main()
