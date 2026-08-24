from pathlib import Path

MIGRATION = Path("migrations/0070_matter_activity_projection.sql")


def test_migration_is_reversible_forced_rls_and_least_privilege() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert sql.count("-- sklegal:up") == 1
    assert sql.count("-- sklegal:down") == 1
    up, down = sql.split("-- sklegal:down", 1)
    for table in ("entries", "projection_receipts", "export_proposals"):
        assert f"sklegal_activity.{table} ENABLE ROW LEVEL SECURITY" in up
        assert f"sklegal_activity.{table} FORCE ROW LEVEL SECURITY" in up
        assert f"DROP TABLE sklegal_activity.{table}" in down
    assert "GRANT SELECT ON sklegal_activity.entries" in up
    assert "GRANT EXECUTE ON FUNCTION sklegal_activity.project_event" in up
    assert "GRANT EXECUTE ON FUNCTION sklegal_activity.propose_export" in up
    assert "GRANT INSERT" not in up
    assert "GRANT UPDATE" not in up
    assert "GRANT DELETE" not in up
    assert "GRANT ALL" not in up
    assert "BYPASSRLS" not in up
    assert "GRANT USAGE ON SCHEMA sklegal_audit" not in up
    assert "GRANT SELECT ON sklegal_audit" not in up
    assert "REVOKE USAGE ON SCHEMA sklegal_audit" not in down


def test_migration_binds_chain_sources_watermark_and_inert_exports() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    required = {
        "sklegal_audit.verify_current_tenant_chain()",
        "sklegal_audit.advance_projection_watermark(",
        "source_event.resource_id IS DISTINCT FROM p_source_id",
        "source_event.resource_kind IS DISTINCT FROM p_source_kind",
        "NOT source_event.attributes ? 'resource_sha256'",
        "NOT source_event.attributes ? 'resource_version'",
        "cause.id = p_causation_id",
        "p_workflow_reference_id IS DISTINCT FROM (CASE",
        "p_agent_run_id IS DISTINCT FROM (CASE",
        "p_tool_call_id IS DISTINCT FROM (CASE",
        "p_corrects_source_id",
        "p_superseded_by_source_id",
        "p_superseded_by_source_version",
        "successor.resource_kind = p_source_kind",
        "successor.matter_id = p_matter_id",
        "successor.source_version > source.source_version",
        "sklegal_activity.supersession_graph_is_valid(",
        "operation IN ('project', 'create_activity_export')",
        "'matter.activity_export.created'",
        "status text NOT NULL DEFAULT 'proposed' CHECK (status = 'proposed')",
        "dispatch_state text NOT NULL DEFAULT 'not_requested'",
        "CHECK (approval_id IS NULL)",
        "Matter activity history is append only",
    }
    assert required.issubset(set(filter(lambda item: item in sql, required)))
    assert "sklegal_activity.snapshot_is_valid(" in sql
    assert "has_schema_privilege(session_user, 'sklegal_audit', 'USAGE')" in sql


def test_export_replay_compares_recorded_authorization_context() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    receipt = sql.split("CREATE TABLE sklegal_activity.projection_receipts (", 1)[
        1
    ].split("\n);", 1)[0]
    export_function = sql.split("CREATE FUNCTION sklegal_activity.propose_export(", 1)[
        1
    ]
    replay = export_function.split("SELECT * INTO prior", 1)[1].split("END IF;", 1)[0]
    for field in {
        "principal_id uuid",
        "authorization_decision_id uuid",
        "policy_decision_id uuid",
        "policy_revision sklegal_legal.sha256_digest",
    }:
        assert field in receipt
    for comparison in {
        "prior.tenant_id IS DISTINCT FROM p_tenant_id",
        "prior.matter_id IS DISTINCT FROM p_matter_id",
        "prior.operation IS DISTINCT FROM 'create_activity_export'",
        "prior.principal_id IS DISTINCT FROM p_proposed_by_principal_id",
        "prior.authorization_decision_id IS DISTINCT FROM",
        "prior.policy_decision_id IS DISTINCT FROM p_policy_decision_id",
        "prior.policy_revision IS DISTINCT FROM p_policy_revision",
        "prior.request_sha256 IS DISTINCT FROM p_request_sha256",
    }:
        assert comparison in replay
