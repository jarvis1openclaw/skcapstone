from __future__ import annotations

from pathlib import Path

MIGRATION = Path(__file__).parents[3] / "migrations" / "0026_task_deadlines.sql"


def test_task_deadline_migration_is_additive_scoped_and_reversible() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    up, down = text.split("-- sklegal:down", 1)
    assert up.count("CREATE SCHEMA sklegal_task_deadline") == 1
    tables = (
        "task_versions",
        "deadline_versions",
        "simulation_receipts",
        "idempotency_receipts",
        "audit_events",
        "outbox",
    )
    for table in tables:
        assert f"CREATE TABLE sklegal_task_deadline.{table}" in up
        assert (
            f"ALTER TABLE sklegal_task_deadline.{table} FORCE ROW LEVEL SECURITY" in up
        )
        assert f"DROP TABLE sklegal_task_deadline.{table}" in down
    assert up.count("sklegal_identity.record_is_authorized(tenant_id, matter_id)") == 12
    assert up.count("EXECUTE FUNCTION sklegal_legal.reject_record_change()") == 6
    assert "GRANT USAGE ON SCHEMA sklegal_task_deadline TO sklegal_runtime" in up
    assert "GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA sklegal_task_deadline" in up
    assert "REVOKE USAGE ON SCHEMA sklegal_task_deadline FROM sklegal_runtime" in down
    assert "REVOKE SELECT, INSERT ON ALL TABLES" in down
    assert "DROP SCHEMA sklegal_task_deadline" in down


def test_simulation_and_outbox_are_database_enforced_non_dispatching() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    assert "state text NOT NULL CHECK (state = 'simulated')" in text
    assert "external_effect boolean NOT NULL CHECK (NOT external_effect)" in text
    assert "connector_invoked boolean NOT NULL CHECK (NOT connector_invoked)" in text
    assert "dispatch_attempted boolean NOT NULL CHECK (NOT dispatch_attempted)" in text
    assert "dispatch_allowed boolean NOT NULL CHECK (NOT dispatch_allowed)" in text


def test_deadline_operation_requires_accepted_review_and_exact_due_time() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    assert "state = 'operative' AND review_state = 'accepted'" in text
    assert "operative_due_at = candidate_due_at" in text
    assert "trigger_evidence_sha256" in text
    assert "rule_authority_sha256" in text
    assert "holiday_calendar_sha256" in text
