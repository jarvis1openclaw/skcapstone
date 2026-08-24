from __future__ import annotations

from pathlib import Path

MIGRATION = Path(__file__).parents[3] / "migrations/0030_governed_agent_runs.sql"
TABLES = (
    "agent_run_identities",
    "agent_runs",
    "agent_run_attempts",
    "agent_tool_calls",
    "agent_recommendations",
    "agent_blind_challenges",
    "agent_human_dispositions",
    "agent_run_idempotency",
    "agent_run_audit_events",
)


def test_migration_has_one_ordered_up_and_down_and_exact_rollback() -> None:
    sql = MIGRATION.read_text()
    assert sql.count("-- sklegal:up") == 1
    assert sql.count("-- sklegal:down") == 1
    up, down = sql.split("-- sklegal:down")
    for table in TABLES:
        qualified = f"sklegal_workflow.{table}"
        assert f"CREATE TABLE {qualified}" in up
        assert f"DROP TABLE {qualified}" in down
    assert down.index(
        "DROP TABLE sklegal_workflow.agent_run_audit_events"
    ) < down.index("DROP TABLE sklegal_workflow.agent_runs")
    assert down.rstrip().endswith("DROP TABLE sklegal_workflow.agent_run_identities;")


def test_every_agent_run_table_is_forced_rls_append_only_and_matter_scoped() -> None:
    sql = MIGRATION.read_text()
    boundary_array = sql.split("DO $agent_run_boundaries$", 1)[1].split("]", 1)[0]
    for table in TABLES:
        assert f"'sklegal_workflow.{table}'::regclass" in boundary_array
    assert "ALTER TABLE %s ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE %s FORCE ROW LEVEL SECURITY" in sql
    assert "sklegal_identity.record_is_authorized(tenant_id, matter_id)" in sql
    assert "sklegal_legal.reject_record_change()" in sql
    assert "sklegal_legal.reject_nil_domain_ids()" in sql
    assert "SECURITY DEFINER" not in sql


def test_schema_preserves_public_synthetic_inert_and_evidence_contracts() -> None:
    sql = MIGRATION.read_text()
    assert "CHECK (classification = 'public')" in sql
    assert "CHECK (public_synthetic)" in sql
    assert "CHECK (NOT saw_challenged_conclusion)" in sql
    assert "CHECK (NOT creates_domain_record)" in sql
    assert "CHECK (NOT external_effect)" in sql
    for field in (
        "credential_digest",
        "agent_spec_sha256",
        "deployment_sha256",
        "prompt_template_sha256",
        "output_schema_sha256",
        "scoring_policy_sha256",
        "matter_snapshot_sha256",
        "corpus_snapshot_sha256",
        "authority_snapshot_sha256",
        "policy_snapshot_sha256",
        "proposal_payload_sha256",
        "blind_input_sha256",
        "independent_output_sha256",
    ):
        assert field in sql
