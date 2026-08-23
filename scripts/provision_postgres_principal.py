#!/usr/bin/env python3
"""Bind one safe PostgreSQL runtime role and install the S1-02 grant profile."""

from __future__ import annotations

import argparse
from uuid import UUID

from manage_migrations import PsqlClient, _sql_literal

SCHEMAS = {
    "sklegal_identity",
    "sklegal_legal",
    "sklegal_integrations",
    "sklegal_workflow",
    "sklegal_audit",
}
INSERT_TABLES = {
    "sklegal_legal.source_references",
    "sklegal_legal.forums",
    "sklegal_legal.proceedings",
    "sklegal_legal.parties",
    "sklegal_legal.party_roles",
    "sklegal_legal.matter_events",
    "sklegal_legal.legacy_aliases",
    "sklegal_legal.legal_transactions",
    "sklegal_legal.transaction_party_roles",
    "sklegal_legal.transaction_source_references",
    "sklegal_legal.fact_assertions",
    "sklegal_legal.tension_groups",
    "sklegal_legal.tension_assertions",
    "sklegal_legal.evidence_items",
    "sklegal_legal.custody_events",
    "sklegal_legal.authority_identities",
    "sklegal_legal.authorities",
    "sklegal_legal.issues",
    "sklegal_legal.claims",
    "sklegal_legal.defenses",
    "sklegal_legal.elements",
    "sklegal_legal.element_evidence",
    "sklegal_legal.theory_evidence",
    "sklegal_legal.theory_authorities",
    "sklegal_legal.remedies",
    "sklegal_legal.remedy_authorities",
    "sklegal_legal.ledger_claim_identities",
    "sklegal_legal.ledger_claims",
    "sklegal_legal.ledger_claim_support",
    "sklegal_legal.deadline_calculations",
    "sklegal_legal.deadline_calculation_sources",
    "sklegal_legal.deadlines",
    "sklegal_legal.tasks",
    "sklegal_legal.work_products",
    "sklegal_legal.work_product_versions",
    "sklegal_legal.work_product_templates",
    "sklegal_legal.work_product_template_versions",
    "sklegal_legal.work_product_unknowns",
    "sklegal_legal.sentence_groundings",
    "sklegal_legal.validations",
    "sklegal_legal.validation_checks",
    "sklegal_legal.approvals",
    "sklegal_legal.executions",
    "sklegal_integrations.external_references",
    "sklegal_integrations.corpus_release_references",
    "sklegal_workflow.workflow_references",
    "sklegal_workflow.policy_decision_references",
}
UPDATE_TABLES: set[str] = set()
SELECT_EXCLUDED_TABLES = {"sklegal_audit.rollback_guard"}
EXECUTE_FUNCTIONS = {
    "sklegal_identity.current_tenant_id()",
    "sklegal_identity.current_principal_id()",
    "sklegal_identity.has_tenant_membership(uuid)",
    "sklegal_identity.runtime_role_is_safe()",
    "sklegal_identity.record_is_authorized(uuid, uuid)",
    "sklegal_legal.has_matter_membership(uuid, uuid)",
    "sklegal_identity.encrypted_payload_is_complete(bytea, text, text, timestamp with time zone)",
    "sklegal_legal.typed_json_value_is_valid(text, jsonb)",
    "sklegal_legal.transition_work_product_version(uuid, uuid, uuid, bigint, text)",
    "sklegal_legal.transition_work_product(uuid, uuid, uuid, bigint, text, uuid, uuid)",
    "sklegal_legal.revise_work_product(uuid, uuid, uuid, bigint, text, text, uuid, bigint, text)",
    "sklegal_legal.transition_work_product_template_version(uuid, uuid, bigint, text)",
    "sklegal_legal.transition_work_product_template(uuid, uuid, bigint, text)",
    "sklegal_legal.advance_work_product_template(uuid, uuid, bigint, uuid, bigint, text)",
    "sklegal_legal.resolve_work_product_unknown(uuid, uuid, uuid, bigint, timestamp with time zone)",
    "sklegal_legal.transition_approval(uuid, uuid, uuid, bigint, text, text)",
    "sklegal_legal.transition_execution(uuid, uuid, uuid, bigint, text, text, uuid, uuid, uuid, text, text, timestamp with time zone, timestamp with time zone)",
    "sklegal_legal.create_communication(uuid, uuid, uuid, text, text, text, uuid[], uuid, bigint, text, sklegal_legal.data_classification, sklegal_legal.record_completeness, timestamp with time zone, timestamp with time zone)",
    "sklegal_legal.transition_communication(uuid, uuid, uuid, bigint, text, uuid, uuid, text, uuid)",
    "sklegal_legal.revise_communication(uuid, uuid, uuid, bigint, text, text, text, uuid, bigint, text, uuid[])",
    "sklegal_legal.material_policy_snapshot(uuid, uuid, uuid, bigint, uuid)",
    "sklegal_legal.skgateway_authorization_snapshot(text, text, jsonb, jsonb)",
    "sklegal_audit.append_event(uuid, uuid, uuid, uuid, uuid, uuid, text, text, text, text, text, text, uuid, uuid, uuid, text, text, timestamp with time zone, jsonb)",
    "sklegal_audit.verify_current_tenant_chain()",
    "sklegal_audit.record_outbox_delivery(uuid, text, uuid)",
    "sklegal_audit.advance_projection_watermark(text, bigint, sklegal_legal.sha256_digest, bigint, sklegal_legal.sha256_digest)",
}


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a UUID") from exc


def _verify_grants(client: PsqlClient, runtime_role: str) -> None:
    role_literal = _sql_literal(runtime_role)
    relation_output = client.run(
        """
        SELECT namespace.nspname || '.' || relation.relname
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname IN (
            'sklegal_identity', 'sklegal_legal', 'sklegal_integrations',
            'sklegal_workflow', 'sklegal_audit'
        ) AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
        ORDER BY 1;
        """
    )
    relations = set(relation_output.splitlines()) if relation_output else set()
    grant_output = client.run(
        f"""
        SELECT table_schema || '.' || table_name || ':' || privilege_type
        FROM information_schema.role_table_grants
        WHERE grantee = {role_literal}
          AND table_schema LIKE 'sklegal_%'
        ORDER BY 1;
        """
    )
    actual = set(grant_output.splitlines()) if grant_output else set()
    expected = {
        f"{table}:SELECT" for table in relations if table not in SELECT_EXCLUDED_TABLES
    }
    expected.update(f"{table}:INSERT" for table in INSERT_TABLES)
    expected.update(f"{table}:UPDATE" for table in UPDATE_TABLES)
    if actual != expected:
        raise RuntimeError("runtime table grant readback differs from the allowlist")

    schema_output = client.run(
        f"""
        SELECT nspname, has_schema_privilege({role_literal}, nspname, 'USAGE'),
               has_schema_privilege({role_literal}, nspname, 'CREATE')
        FROM pg_namespace
        WHERE nspname IN (
            'sklegal_identity', 'sklegal_legal', 'sklegal_integrations',
            'sklegal_workflow', 'sklegal_audit'
        ) ORDER BY nspname;
        """
    )
    schema_rows = {tuple(row.split("|")) for row in schema_output.splitlines()}
    if schema_rows != {(schema, "t", "f") for schema in SCHEMAS}:
        raise RuntimeError("runtime schema grant readback is not USAGE-only")

    function_output = client.run(
        f"""
        SELECT namespace.nspname || '.' || procedure.proname || '('
               || pg_catalog.oidvectortypes(procedure.proargtypes) || ')'
        FROM pg_catalog.pg_proc AS procedure
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = procedure.pronamespace
        WHERE namespace.nspname LIKE 'sklegal_%'
          AND has_function_privilege({role_literal}, procedure.oid, 'EXECUTE')
        ORDER BY 1;
        """
    )
    functions = set(function_output.splitlines()) if function_output else set()
    if functions != EXECUTE_FUNCTIONS:
        raise RuntimeError("runtime function grant readback differs from the allowlist")

    safety_output = client.run(
        f"""
        SELECT role_record.rolcanlogin AND NOT role_record.rolsuper
               AND NOT role_record.rolbypassrls
               AND NOT role_record.rolcreaterole
               AND NOT role_record.rolcreatedb
               AND NOT role_record.rolreplication
               AND NOT role_record.rolinherit
               AND NOT EXISTS (
                   SELECT 1 FROM pg_auth_members
                   WHERE member = role_record.oid OR roleid = role_record.oid
               )
        FROM pg_roles AS role_record WHERE role_record.rolname = {role_literal};
        """
    )
    if safety_output != "t":
        raise RuntimeError("runtime role drifted outside the exact safe role profile")


def provision(
    client: PsqlClient, *, runtime_role: str, tenant_id: str, principal_id: str
) -> None:
    role_literal = _sql_literal(runtime_role)
    tenant_literal = _sql_literal(tenant_id)
    principal_literal = _sql_literal(principal_id)
    client.run(
        f"""
        DO $provision$
        DECLARE
            selected_role record;
        BEGIN
            SELECT * INTO selected_role FROM pg_roles WHERE rolname = {role_literal};
            IF NOT FOUND OR NOT selected_role.rolcanlogin OR selected_role.rolsuper
               OR selected_role.rolbypassrls OR selected_role.rolcreaterole
               OR selected_role.rolcreatedb OR selected_role.rolreplication
               OR selected_role.rolinherit THEN
                RAISE EXCEPTION 'runtime role does not satisfy the least-privilege profile';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_auth_members AS membership
                WHERE membership.member = selected_role.oid
                   OR membership.roleid = selected_role.oid
            ) THEN
                RAISE EXCEPTION 'runtime role must not participate in a PostgreSQL role graph';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_namespace WHERE nspname LIKE 'sklegal_%'
                  AND nspowner = selected_role.oid
                UNION ALL
                SELECT 1 FROM pg_class AS relation
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname LIKE 'sklegal_%'
                  AND relation.relowner = selected_role.oid
                UNION ALL
                SELECT 1 FROM pg_proc AS procedure
                JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname LIKE 'sklegal_%'
                  AND procedure.proowner = selected_role.oid
                UNION ALL
                SELECT 1 FROM pg_type AS type_record
                JOIN pg_namespace AS namespace ON namespace.oid = type_record.typnamespace
                WHERE namespace.nspname LIKE 'sklegal_%'
                  AND type_record.typowner = selected_role.oid
            ) THEN
                RAISE EXCEPTION 'runtime role cannot own an SKLegal database object';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM sklegal_identity.tenants
                WHERE id = {tenant_literal}::uuid AND status = 'active'
            ) OR NOT EXISTS (
                SELECT 1 FROM sklegal_identity.principals
                WHERE tenant_id = {tenant_literal}::uuid
                  AND id = {principal_literal}::uuid
                  AND status = 'active'
            ) OR NOT EXISTS (
                SELECT 1 FROM sklegal_identity.tenant_memberships
                WHERE tenant_id = {tenant_literal}::uuid
                  AND principal_id = {principal_literal}::uuid
                  AND active
            ) THEN
                RAISE EXCEPTION 'active principal and tenant membership are required';
            END IF;

            INSERT INTO sklegal_identity.database_role_bindings (
                database_role, tenant_id, principal_id
            ) VALUES (
                {role_literal}, {tenant_literal}::uuid, {principal_literal}::uuid
            );

            EXECUTE format(
                'REVOKE ALL ON SCHEMA sklegal_identity, sklegal_legal, '
                'sklegal_integrations, sklegal_workflow, sklegal_audit FROM %I',
                {role_literal}
            );
            EXECUTE format(
                'REVOKE ALL ON ALL TABLES IN SCHEMA sklegal_identity, sklegal_legal, '
                'sklegal_integrations, sklegal_workflow, sklegal_audit FROM %I',
                {role_literal}
            );
            EXECUTE format(
                'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA sklegal_identity, sklegal_legal, '
                'sklegal_integrations, sklegal_workflow, sklegal_audit FROM %I',
                {role_literal}
            );
            EXECUTE format(
                'GRANT USAGE ON SCHEMA sklegal_identity, sklegal_legal, '
                'sklegal_integrations, sklegal_workflow, sklegal_audit TO %I',
                {role_literal}
            );
            EXECUTE format(
                'GRANT SELECT ON ALL TABLES IN SCHEMA sklegal_identity, sklegal_legal, '
                'sklegal_integrations, sklegal_workflow, sklegal_audit TO %I',
                {role_literal}
            );
            EXECUTE format(
                'REVOKE ALL ON sklegal_audit.rollback_guard FROM %I',
                {role_literal}
            );
            EXECUTE format(
                'GRANT USAGE ON DOMAIN sklegal_legal.record_version, '
                'sklegal_legal.sha256_digest, sklegal_legal.data_classification, '
                'sklegal_legal.record_completeness TO %I',
                {role_literal}
            );
            EXECUTE format(
                'GRANT EXECUTE ON FUNCTION '
                'sklegal_identity.current_tenant_id(), '
                'sklegal_identity.current_principal_id(), '
                'sklegal_identity.has_tenant_membership(uuid), '
                'sklegal_identity.runtime_role_is_safe(), '
                'sklegal_identity.record_is_authorized(uuid, uuid), '
                'sklegal_legal.has_matter_membership(uuid, uuid), '
                'sklegal_identity.encrypted_payload_is_complete(bytea, text, text, timestamptz), '
                'sklegal_legal.typed_json_value_is_valid(text, jsonb), '
                'sklegal_legal.transition_work_product_version(uuid, uuid, uuid, bigint, text), '
                'sklegal_legal.transition_work_product(uuid, uuid, uuid, bigint, text, uuid, uuid), '
                'sklegal_legal.revise_work_product(uuid, uuid, uuid, bigint, text, text, uuid, bigint, text), '
                'sklegal_legal.transition_work_product_template_version(uuid, uuid, bigint, text), '
                'sklegal_legal.transition_work_product_template(uuid, uuid, bigint, text), '
                'sklegal_legal.advance_work_product_template(uuid, uuid, bigint, uuid, bigint, text), '
                'sklegal_legal.resolve_work_product_unknown(uuid, uuid, uuid, bigint, timestamptz), '
                'sklegal_legal.transition_approval(uuid, uuid, uuid, bigint, text, text), '
                'sklegal_legal.transition_execution(uuid, uuid, uuid, bigint, text, text, uuid, uuid, uuid, text, text, timestamptz, timestamptz), '
                'sklegal_legal.create_communication(uuid, uuid, uuid, text, text, text, uuid[], uuid, bigint, text, sklegal_legal.data_classification, sklegal_legal.record_completeness, timestamptz, timestamptz), '
                'sklegal_legal.transition_communication(uuid, uuid, uuid, bigint, text, uuid, uuid, text, uuid), '
                'sklegal_legal.revise_communication(uuid, uuid, uuid, bigint, text, text, text, uuid, bigint, text, uuid[]) '
                ', sklegal_legal.material_policy_snapshot(uuid, uuid, uuid, bigint, uuid), '
                'sklegal_legal.skgateway_authorization_snapshot(text, text, jsonb, jsonb), '
                'sklegal_audit.append_event(uuid, uuid, uuid, uuid, uuid, uuid, text, text, text, text, text, text, uuid, uuid, uuid, text, text, timestamptz, jsonb), '
                'sklegal_audit.verify_current_tenant_chain(), '
                'sklegal_audit.record_outbox_delivery(uuid, text, uuid), '
                'sklegal_audit.advance_projection_watermark(text, bigint, sklegal_legal.sha256_digest, bigint, sklegal_legal.sha256_digest) '
                'TO %I',
                {role_literal}
            );
            EXECUTE format(
                'GRANT INSERT ON '
                'sklegal_legal.source_references, sklegal_legal.forums, '
                'sklegal_legal.proceedings, sklegal_legal.parties, '
                'sklegal_legal.party_roles, sklegal_legal.matter_events, '
                'sklegal_legal.legacy_aliases, sklegal_legal.legal_transactions, '
                'sklegal_legal.transaction_party_roles, '
                'sklegal_legal.transaction_source_references, '
                'sklegal_legal.fact_assertions, sklegal_legal.tension_groups, '
                'sklegal_legal.tension_assertions, sklegal_legal.evidence_items, '
                'sklegal_legal.custody_events, sklegal_legal.authority_identities, '
                'sklegal_legal.authorities, sklegal_legal.issues, '
                'sklegal_legal.claims, sklegal_legal.defenses, '
                'sklegal_legal.elements, sklegal_legal.element_evidence, '
                'sklegal_legal.theory_evidence, sklegal_legal.theory_authorities, '
                'sklegal_legal.remedies, sklegal_legal.remedy_authorities, '
                'sklegal_legal.ledger_claim_identities, '
                'sklegal_legal.ledger_claims, sklegal_legal.ledger_claim_support, '
                'sklegal_legal.deadline_calculations, '
                'sklegal_legal.deadline_calculation_sources, '
                'sklegal_legal.deadlines, sklegal_legal.tasks, '
                'sklegal_legal.work_products, sklegal_legal.work_product_versions, '
                'sklegal_legal.work_product_templates, '
                'sklegal_legal.work_product_template_versions, '
                'sklegal_legal.work_product_unknowns, '
                'sklegal_legal.sentence_groundings, '
                'sklegal_legal.validations, sklegal_legal.validation_checks, '
                'sklegal_legal.approvals, sklegal_legal.executions, '
                'sklegal_integrations.external_references, '
                'sklegal_integrations.corpus_release_references, '
                'sklegal_workflow.workflow_references, '
                'sklegal_workflow.policy_decision_references TO %I',
                {role_literal}
            );
        END;
        $provision$;
        """
    )
    _verify_grants(client, runtime_role)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    connection = parser.add_mutually_exclusive_group(required=True)
    connection.add_argument("--direct", action="store_true")
    connection.add_argument("--docker-container")
    parser.add_argument("--database", default="sklegal")
    parser.add_argument("--admin-user", default="postgres")
    parser.add_argument("--runtime-role", required=True)
    parser.add_argument("--tenant-id", type=_uuid, required=True)
    parser.add_argument("--principal-id", type=_uuid, required=True)
    args = parser.parse_args()
    client = PsqlClient(
        direct=args.direct,
        docker_container=args.docker_container,
        database=args.database,
        user=args.admin_user,
    )
    provision(
        client,
        runtime_role=args.runtime_role,
        tenant_id=args.tenant_id,
        principal_id=args.principal_id,
    )
    print(f"provisioned runtime role: {args.runtime_role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
