"""SKL-S5-04A security and tenant-isolation qualification over real PostgreSQL.

This module executes the isolation leak matrix at scale against the shared
disposable PostgreSQL container from the persistence contract: every forced
row-level-security table is probed for cross-tenant and cross-matter reads by
every bound runtime role, every runtime role is probed for write and truncate
denial on every table, policy-gateway bypass attempts (role switching,
caller-set GUC spoofing, pg_temp shadowing, function privilege abuse) are
executed, and the information-barrier function surface is probed from every
runtime role.

Every probe is read-only or an expected denial, so the shared seeded scopes
stay intact for the other contract modules. All identifiers are synthetic.
"""

from __future__ import annotations

import unittest

from tests.integration.persistence_contract_support import PersistenceContractBase

BOUND_ROLES = {
    "sklegal_test_alpha_one": ("tenant_alpha", "matter_alpha_one"),
    "sklegal_test_alpha_two": ("tenant_alpha", "matter_alpha_two"),
    "sklegal_test_beta_one": ("tenant_beta", "matter_beta_one"),
}
UNGRANTED_ROLES = (
    "sklegal_test_unbound",
    "sklegal_test_bypass",
    "sklegal_runtime",
)
OTHER_SCOPE = {
    "sklegal_test_alpha_one": ("tenant_beta", "matter_beta_one"),
    "sklegal_test_alpha_two": ("tenant_beta", "matter_beta_one"),
    "sklegal_test_beta_one": ("tenant_alpha", "matter_alpha_one"),
}
SAME_TENANT_FOREIGN_MATTER = {
    "sklegal_test_alpha_one": "matter_alpha_two",
    "sklegal_test_alpha_two": "matter_alpha_one",
}

# Pinned against the S1-02 grant profile readback in
# scripts/provision_postgres_principal.py (EXECUTE_FUNCTIONS).
BOUND_ROLE_EXECUTE_ALLOWLIST = {
    "sklegal_audit.advance_projection_watermark(text, bigint, sklegal_legal.sha256_digest, bigint, sklegal_legal.sha256_digest)",
    "sklegal_audit.append_event(uuid, uuid, uuid, uuid, uuid, uuid, text, text, text, text, text, text, uuid, uuid, uuid, text, text, timestamp with time zone, jsonb)",
    "sklegal_audit.record_outbox_delivery(uuid, text, uuid)",
    "sklegal_audit.verify_current_tenant_chain()",
    "sklegal_identity.current_principal_id()",
    "sklegal_identity.current_tenant_id()",
    "sklegal_identity.encrypted_payload_is_complete(bytea, text, text, timestamp with time zone)",
    "sklegal_identity.has_tenant_membership(uuid)",
    "sklegal_identity.record_is_authorized(uuid, uuid)",
    "sklegal_identity.runtime_role_is_safe()",
    "sklegal_legal.advance_work_product_template(uuid, uuid, bigint, uuid, bigint, text)",
    "sklegal_legal.create_communication(uuid, uuid, uuid, text, text, text, uuid[], uuid, bigint, text, sklegal_legal.data_classification, sklegal_legal.record_completeness, timestamp with time zone, timestamp with time zone)",
    "sklegal_legal.has_matter_membership(uuid, uuid)",
    "sklegal_legal.material_policy_snapshot(uuid, uuid, uuid, bigint, uuid)",
    "sklegal_legal.skgateway_authorization_snapshot(text, text, jsonb, jsonb)",
    "sklegal_legal.resolve_work_product_unknown(uuid, uuid, uuid, bigint, timestamp with time zone)",
    "sklegal_legal.revise_communication(uuid, uuid, uuid, bigint, text, text, text, uuid, bigint, text, uuid[])",
    "sklegal_legal.revise_work_product(uuid, uuid, uuid, bigint, text, text, uuid, bigint, text)",
    "sklegal_legal.transition_approval(uuid, uuid, uuid, bigint, text, text)",
    "sklegal_legal.transition_communication(uuid, uuid, uuid, bigint, text, uuid, uuid, text, uuid)",
    "sklegal_legal.transition_execution(uuid, uuid, uuid, bigint, text, text, uuid, uuid, uuid, text, text, timestamp with time zone, timestamp with time zone)",
    "sklegal_legal.transition_work_product(uuid, uuid, uuid, bigint, text, uuid, uuid)",
    "sklegal_legal.transition_work_product_template(uuid, uuid, bigint, text)",
    "sklegal_legal.transition_work_product_template_version(uuid, uuid, bigint, text)",
    "sklegal_legal.transition_work_product_version(uuid, uuid, uuid, bigint, text)",
    "sklegal_legal.typed_json_value_is_valid(text, jsonb)",
}

# Pinned against migrations 0008 through 0018 GRANT EXECUTE statements.
SHARED_RUNTIME_EXECUTE_ALLOWLIST = {
    "sklegal_identity.capability_principal_snapshot(uuid, uuid)",
    "sklegal_identity.capability_revocation_snapshot(uuid, sklegal_legal.sha256_digest[])",
    "sklegal_identity.prune_expired_capability_replay_reservations(uuid)",
    "sklegal_identity.reserve_policy_authorization_use(uuid, uuid, sklegal_legal.sha256_digest, timestamp with time zone, timestamp with time zone)",
    "sklegal_identity.reserve_capability(uuid, sklegal_legal.sha256_digest, uuid, timestamp with time zone)",
    "sklegal_identity.revoke_capability(uuid, sklegal_legal.sha256_digest, uuid, text)",
    "sklegal_legal.skgateway_authorization_snapshot(text, text, jsonb, jsonb)",
}

_RELATIONS = """
    SELECT n.nspname, c.relname,
           (SELECT a.attname FROM pg_attribute AS a
            WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum LIMIT 1) AS first_column
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname LIKE 'sklegal\\_%' AND n.nspname <> 'sklegal_migrations'
      AND c.relkind = 'r'
"""

_FORCED_RLS_RELATIONS = _RELATIONS + "  AND c.relrowsecurity AND c.relforcerowsecurity\n"

_MATTER_SCOPED = """
    AND EXISTS (
        SELECT 1 FROM information_schema.columns AS ic
        WHERE ic.table_schema = n.nspname AND ic.table_name = c.relname
          AND ic.column_name = 'matter_id'
    )
"""

_PROBE_ID = "5c040000-0000-4000-8000-00000000"


class IsolationQualification01ReadMatrixTests(PersistenceContractBase):
    """Cross-tenant and cross-matter read denial for every runtime role."""

    def test_01_cross_tenant_read_matrix_every_forced_table(self) -> None:
        value = self.fixture
        for role, (tenant_key, _matter_key) in BOUND_ROLES.items():
            foreign_tenant = value[OTHER_SCOPE[role][0]]
            result = self._psql(
                role,
                f"""
                DO $probe$
                DECLARE
                    rel record;
                    leaked bigint;
                    violations text := '';
                    probes integer := 0;
                BEGIN
                    FOR rel IN {_FORCED_RLS_RELATIONS}
                    LOOP
                        probes := probes + 1;
                        BEGIN
                            EXECUTE format(
                                'SELECT count(*) FROM %I.%I
                                 WHERE tenant_id = %L::uuid',
                                rel.nspname, rel.relname, '{foreign_tenant}'
                            ) INTO leaked;
                        EXCEPTION WHEN insufficient_privilege THEN
                            leaked := 0;
                        END;
                        IF leaked > 0 THEN
                            violations := violations || rel.nspname || '.'
                                || rel.relname || '=' || leaked || ';';
                        END IF;
                    END LOOP;
                    RAISE NOTICE 'probes=%', probes;
                    IF violations <> '' THEN
                        RAISE EXCEPTION 'cross-tenant leak: %', violations;
                    END IF;
                END
                $probe$;
                """,
            )
            with self.subTest(role=role):
                self.assertIn("probes=", result.stderr)
                probe_count = int(
                    result.stderr.split("probes=")[1].splitlines()[0].strip()
                )
                self.assertGreaterEqual(probe_count, 80)

    def test_02_cross_matter_read_matrix_same_tenant(self) -> None:
        value = self.fixture
        for role, matter_key in SAME_TENANT_FOREIGN_MATTER.items():
            foreign_matter = value[matter_key]
            result = self._psql(
                role,
                f"""
                DO $probe$
                DECLARE
                    rel record;
                    leaked bigint;
                    violations text := '';
                    probes integer := 0;
                BEGIN
                    FOR rel IN {_FORCED_RLS_RELATIONS}{_MATTER_SCOPED}
                    LOOP
                        probes := probes + 1;
                        BEGIN
                            EXECUTE format(
                                'SELECT count(*) FROM %I.%I
                                 WHERE matter_id = %L::uuid',
                                rel.nspname, rel.relname, '{foreign_matter}'
                            ) INTO leaked;
                        EXCEPTION WHEN insufficient_privilege THEN
                            leaked := 0;
                        END;
                        IF leaked > 0 THEN
                            violations := violations || rel.nspname || '.'
                                || rel.relname || '=' || leaked || ';';
                        END IF;
                    END LOOP;
                    RAISE NOTICE 'probes=%', probes;
                    IF violations <> '' THEN
                        RAISE EXCEPTION 'cross-matter leak: %', violations;
                    END IF;
                END
                $probe$;
                """,
            )
            with self.subTest(role=role):
                self.assertIn("probes=", result.stderr)
                probe_count = int(
                    result.stderr.split("probes=")[1].splitlines()[0].strip()
                )
                self.assertGreaterEqual(probe_count, 60)

    def test_03_unbound_bypass_and_shared_runtime_roles_read_nothing(self) -> None:
        for role in UNGRANTED_ROLES:
            result = self._psql(
                role,
                f"""
                DO $probe$
                DECLARE
                    rel record;
                    visible bigint;
                    violations text := '';
                    probes integer := 0;
                BEGIN
                    FOR rel IN {_RELATIONS}
                    LOOP
                        probes := probes + 1;
                        BEGIN
                            EXECUTE format(
                                'SELECT count(*) FROM %I.%I',
                                rel.nspname, rel.relname
                            ) INTO visible;
                            violations := violations || rel.nspname || '.'
                                || rel.relname || '=' || visible || ';';
                        EXCEPTION WHEN insufficient_privilege THEN
                            NULL;
                        END;
                    END LOOP;
                    RAISE NOTICE 'probes=%', probes;
                    IF violations <> '' THEN
                        RAISE EXCEPTION 'ungranted role read access: %', violations;
                    END IF;
                END
                $probe$;
                """,
            )
            with self.subTest(role=role):
                self.assertIn("probes=", result.stderr)
                probe_count = int(
                    result.stderr.split("probes=")[1].splitlines()[0].strip()
                )
                self.assertGreaterEqual(probe_count, 80)


class IsolationQualification02WriteMatrixTests(PersistenceContractBase):
    """Write denial at scale and row-level-security WITH CHECK probes."""

    def test_01_update_delete_truncate_denied_on_every_table(self) -> None:
        for role in (*BOUND_ROLES, *UNGRANTED_ROLES):
            result = self._psql(
                role,
                f"""
                DO $probe$
                DECLARE
                    rel record;
                    blocked integer := 0;
                    leaks text := '';
                BEGIN
                    FOR rel IN {_RELATIONS}
                    LOOP
                        BEGIN
                            EXECUTE format(
                                'UPDATE %I.%I SET %I = %I WHERE FALSE',
                                rel.nspname, rel.relname,
                                rel.first_column, rel.first_column
                            );
                            leaks := leaks || rel.nspname || '.'
                                || rel.relname || ':update;';
                        EXCEPTION WHEN insufficient_privilege THEN
                            blocked := blocked + 1;
                        END;
                        BEGIN
                            EXECUTE format(
                                'DELETE FROM %I.%I WHERE FALSE',
                                rel.nspname, rel.relname
                            );
                            leaks := leaks || rel.nspname || '.'
                                || rel.relname || ':delete;';
                        EXCEPTION WHEN insufficient_privilege THEN
                            blocked := blocked + 1;
                        END;
                        BEGIN
                            EXECUTE format(
                                'TRUNCATE %I.%I', rel.nspname, rel.relname
                            );
                            leaks := leaks || rel.nspname || '.'
                                || rel.relname || ':truncate;';
                        EXCEPTION WHEN insufficient_privilege THEN
                            blocked := blocked + 1;
                        END;
                    END LOOP;
                    RAISE NOTICE 'blocked=%', blocked;
                    IF leaks <> '' THEN
                        RAISE EXCEPTION 'write leak: %', leaks;
                    END IF;
                END
                $probe$;
                """,
            )
            with self.subTest(role=role):
                self.assertIn("blocked=", result.stderr)
                blocked_count = int(
                    result.stderr.split("blocked=")[1].splitlines()[0].strip()
                )
                self.assertGreaterEqual(blocked_count, 240)

    def test_02_cross_scope_insert_with_check_denied(self) -> None:
        value = self.fixture
        inserts = {
            "sklegal_legal.forums": (
                "(id, tenant_id, matter_id, name, jurisdiction, forum_kind) "
                "VALUES ('{probe}', '{tenant}', '{matter}', "
                "'Isolation probe forum', 'Synthetic', 'other')"
            ),
            "sklegal_legal.source_references": (
                "(id, tenant_id, matter_id, source_system, source_version, "
                "content_sha256, locator, observed_at) "
                "VALUES ('{probe}', '{tenant}', '{matter}', 'synthetic', 'v1', "
                "'" + "c" * 64 + "', 'synthetic:isolation-probe', "
                "'2026-08-22T00:00:00Z')"
            ),
            "sklegal_workflow.workflow_references": (
                "(id, tenant_id, matter_id, workflow_id, run_id, "
                "workflow_type, task_queue, input_sha256, status) "
                "VALUES ('{probe}', '{tenant}', '{matter}', 'wf-probe', "
                "'run-probe', 'synthetic', 'synthetic', "
                "'" + "d" * 64 + "', 'pending')"
            ),
        }
        probe_targets = (
            ("sklegal_test_alpha_one", "tenant_beta", "matter_beta_one"),
            ("sklegal_test_alpha_two", "tenant_beta", "matter_beta_one"),
            ("sklegal_test_beta_one", "tenant_alpha", "matter_alpha_one"),
            ("sklegal_test_alpha_one", "tenant_alpha", "matter_alpha_two"),
            ("sklegal_test_alpha_two", "tenant_alpha", "matter_alpha_one"),
        )
        sequence = 0
        for table, template in inserts.items():
            for role, tenant_key, matter_key in probe_targets:
                sequence += 1
                probe_id = f"{_PROBE_ID}{sequence:04x}"
                statement = template.format(
                    probe=probe_id,
                    tenant=value[tenant_key],
                    matter=value[matter_key],
                )
                denied = self._psql(
                    role,
                    f"INSERT INTO {table} {statement};",
                    check=False,
                )
                with self.subTest(table=table, role=role):
                    self.assertNotEqual(0, denied.returncode)
                    self.assertIn("row-level security", denied.stderr)
        landed = self._psql(
            "postgres",
            f"""
            SELECT (
                SELECT count(*) FROM sklegal_legal.forums
                WHERE id::text LIKE '{_PROBE_ID}%'
            ) + (
                SELECT count(*) FROM sklegal_legal.source_references
                WHERE id::text LIKE '{_PROBE_ID}%'
            ) + (
                SELECT count(*) FROM sklegal_workflow.workflow_references
                WHERE id::text LIKE '{_PROBE_ID}%'
            );
            """,
        )
        self.assertEqual("0", landed.stdout.strip())

    def test_03_audit_append_cross_scope_denied_for_bound_roles(self) -> None:
        value = self.fixture
        for role, tenant_key, matter_key, principal_key in (
            (
                "sklegal_test_alpha_one",
                "tenant_beta",
                "matter_beta_one",
                "principal_beta_one",
            ),
            (
                "sklegal_test_beta_one",
                "tenant_alpha",
                "matter_alpha_one",
                "principal_alpha_one",
            ),
        ):
            denied = self._append_audit_event(
                event_id=f"{_PROBE_ID}00a1",
                run_id=f"{_PROBE_ID}00a2",
                correlation_id=f"{_PROBE_ID}00a3",
                span_id=f"{0xA1:016x}",
                boundary="api",
                action="audit.synthetic.isolation-probe",
                tenant_id=value[tenant_key],
                matter_id=value[matter_key],
                principal_id=value[principal_key],
                role=role,
                check=False,
            )
            with self.subTest(role=role):
                self.assertNotEqual(0, denied.returncode)
                self.assertIn("audit scope is unauthorized", denied.stderr)


class IsolationQualification03GatewayBypassTests(PersistenceContractBase):
    """Policy-gateway bypass attempts against the database boundary."""

    def test_01_set_role_and_session_authorization_denied(self) -> None:
        for role in (*BOUND_ROLES, *UNGRANTED_ROLES):
            for statement in (
                "SET ROLE sklegal_migrator;",
                "SET ROLE postgres;",
                "SET SESSION AUTHORIZATION sklegal_migrator;",
                "SET SESSION AUTHORIZATION postgres;",
            ):
                denied = self._psql(role, statement, check=False)
                with self.subTest(role=role, statement=statement):
                    self.assertNotEqual(0, denied.returncode)
                    self.assertIn("permission denied", denied.stderr)

    def test_02_caller_set_guc_is_not_an_authorization_fact(self) -> None:
        value = self.fixture
        result = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SET sklegal.tenant_id = '{value["tenant_beta"]}';
            SET request.jwt.claim.tenant_id = '{value["tenant_beta"]}';
            SELECT count(*) FROM sklegal_legal.matters
            WHERE tenant_id = '{value["tenant_beta"]}';
            SELECT count(*) FROM sklegal_legal.matters;
            """,
        )
        lines = [line for line in result.stdout.splitlines() if line != "SET"]
        self.assertEqual(["0", "1"], lines)

    def test_03_pg_temp_shadowing_cannot_defeat_rls_predicates(self) -> None:
        value = self.fixture
        result = self._psql(
            "sklegal_test_alpha_one",
            f"""
            CREATE OR REPLACE FUNCTION pg_temp.record_is_authorized(uuid, uuid)
            RETURNS boolean LANGUAGE sql AS 'SELECT true';
            CREATE OR REPLACE FUNCTION pg_temp.current_tenant_id()
            RETURNS uuid LANGUAGE sql AS
                'SELECT ''{value["tenant_beta"]}''::uuid';
            SELECT count(*) FROM sklegal_legal.matters
            WHERE tenant_id = '{value["tenant_beta"]}';
            SELECT count(*) FROM sklegal_legal.matters;
            SELECT sklegal_identity.current_tenant_id();
            """,
        )
        lines = [
            line
            for line in result.stdout.splitlines()
            if line != "CREATE FUNCTION"
        ]
        self.assertEqual("0", lines[0])
        self.assertEqual("1", lines[1])
        self.assertEqual(value["tenant_alpha"], lines[2])

    def test_04_function_execute_inventory_matches_allowlist(self) -> None:
        inventory_sql = """
            SELECT n.nspname || '.' || p.proname || '('
                   || pg_catalog.oidvectortypes(p.proargtypes) || ')'
            FROM pg_proc AS p
            JOIN pg_namespace AS n ON n.oid = p.pronamespace
            WHERE n.nspname LIKE 'sklegal\\_%'
              AND has_function_privilege($1, p.oid, 'EXECUTE')
            ORDER BY 1;
        """
        for role in (*BOUND_ROLES, *UNGRANTED_ROLES):
            sql = inventory_sql.replace("$1", f"'{role}'")
            output = self._psql("postgres", sql).stdout.splitlines()
            with self.subTest(role=role):
                if role in BOUND_ROLES:
                    self.assertEqual(BOUND_ROLE_EXECUTE_ALLOWLIST, set(output))
                elif role == "sklegal_runtime":
                    self.assertEqual(SHARED_RUNTIME_EXECUTE_ALLOWLIST, set(output))
                else:
                    self.assertEqual([], output)

    def test_05_policy_snapshot_denied_outside_exact_scope(self) -> None:
        value = self.fixture
        material = f"{_PROBE_ID}00b1"
        probes = (
            (
                "sklegal_test_alpha_one",
                value["tenant_beta"],
                value["matter_beta_one"],
                value["principal_alpha_one"],
                "policy snapshot scope is not authorized",
            ),
            (
                "sklegal_test_alpha_one",
                value["tenant_alpha"],
                value["matter_alpha_two"],
                value["principal_alpha_one"],
                "policy snapshot scope is not authorized",
            ),
            (
                "sklegal_test_alpha_two",
                value["tenant_alpha"],
                value["matter_alpha_one"],
                value["principal_alpha_two"],
                "policy snapshot scope is not authorized",
            ),
            (
                "sklegal_test_beta_one",
                value["tenant_alpha"],
                value["matter_alpha_one"],
                value["principal_beta_one"],
                "policy snapshot scope is not authorized",
            ),
        )
        for role, tenant, matter, principal, message in probes:
            denied = self._psql(
                role,
                f"""
                SELECT sklegal_legal.material_policy_snapshot(
                    '{tenant}', '{matter}', '{material}', 1, '{principal}');
                """,
                check=False,
            )
            with self.subTest(role=role):
                self.assertNotEqual(0, denied.returncode)
                self.assertIn(message, denied.stderr)
        for role in UNGRANTED_ROLES:
            denied = self._psql(
                role,
                f"""
                SELECT sklegal_legal.material_policy_snapshot(
                    '{value["tenant_alpha"]}', '{value["matter_alpha_one"]}',
                    '{material}', 1, '{value["principal_alpha_one"]}');
                """,
                check=False,
            )
            with self.subTest(role=role):
                self.assertNotEqual(0, denied.returncode)
                self.assertIn("permission denied", denied.stderr)


class IsolationQualification04BarrierProbeTests(PersistenceContractBase):
    """Ethical-wall and information-barrier probes at the database boundary."""

    def test_01_barrier_tables_sealed_for_every_runtime_role(self) -> None:
        value = self.fixture
        barrier_tables = (
            "sklegal_legal.conflict_checks",
            "sklegal_legal.conflict_matches",
            "sklegal_legal.conflict_decisions",
            "sklegal_legal.conflict_holds",
            "sklegal_legal.conflict_waiver_references",
            "sklegal_legal.ethical_walls",
            "sklegal_legal.ethical_wall_memberships",
            "sklegal_legal.legal_holds",
            "sklegal_legal.retention_policies",
            "sklegal_legal.matter_policy_states",
            "sklegal_legal.material_classifications",
            "sklegal_legal.material_protection_labels",
            "sklegal_legal.party_normalizations",
            "sklegal_legal.protected_access_grants",
        )
        for role in (*BOUND_ROLES, *UNGRANTED_ROLES):
            tenant_key, _matter_key = BOUND_ROLES.get(
                role, ("tenant_alpha", "matter_alpha_one")
            )
            own_tenant = value[tenant_key]
            for table in barrier_tables:
                cross = self._psql(
                    role,
                    f"""
                    SELECT count(*) FROM {table}
                    WHERE tenant_id <> '{own_tenant}';
                    """,
                    check=False,
                )
                with self.subTest(role=role, table=table, probe="cross-tenant"):
                    if cross.returncode == 0:
                        self.assertEqual("0", cross.stdout.strip())
                    else:
                        self.assertIn("permission denied", cross.stderr)
                direct = self._psql(
                    role,
                    f"SELECT count(*) FROM {table};",
                    check=False,
                )
                with self.subTest(role=role, table=table, probe="direct"):
                    if direct.returncode == 0:
                        self.assertEqual("0", direct.stdout.strip())
                    else:
                        self.assertIn("permission denied", direct.stderr)

    def test_02_role_bindings_are_self_read_only(self) -> None:
        for role in BOUND_ROLES:
            visible = self._psql(
                role,
                """
                SELECT count(*) FROM sklegal_identity.database_role_bindings
                WHERE database_role <> session_user;
                """,
            )
            with self.subTest(role=role, probe="cross-read"):
                self.assertEqual("0", visible.stdout.strip())
            own = self._psql(
                role,
                """
                SELECT count(*) FROM sklegal_identity.database_role_bindings
                WHERE database_role = session_user;
                """,
            )
            with self.subTest(role=role, probe="self-read"):
                self.assertEqual("1", own.stdout.strip())
        for role in (*BOUND_ROLES, "sklegal_runtime"):
            write = self._psql(
                role,
                """
                UPDATE sklegal_identity.database_role_bindings
                SET active = FALSE WHERE database_role = session_user;
                """,
                check=False,
            )
            with self.subTest(role=role, probe="write"):
                self.assertNotEqual(0, write.returncode)
        for role in UNGRANTED_ROLES:
            read = self._psql(
                role,
                "SELECT count(*) FROM sklegal_identity.database_role_bindings;",
                check=False,
            )
            with self.subTest(role=role, probe="ungranted-read"):
                self.assertNotEqual(0, read.returncode)
                self.assertIn("permission denied", read.stderr)


if __name__ == "__main__":
    unittest.main()
