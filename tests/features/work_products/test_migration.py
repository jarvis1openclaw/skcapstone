from __future__ import annotations

from pathlib import Path

MIGRATION = Path("migrations/0050_work_product_feature_lane.sql")


def sql_parts() -> tuple[str, str]:
    text = MIGRATION.read_text(encoding="utf-8")
    up, down = text.split("-- sklegal:down", 1)
    return up, down


def test_migration_has_reversible_up_and_down_sections() -> None:
    up, down = sql_parts()
    assert up.startswith("-- sklegal:up")
    for table in (
        "work_product_feature_identities",
        "work_product_feature_versions",
        "work_product_feature_idempotency",
        "work_product_feature_events",
        "work_product_feature_outbox",
    ):
        assert "CREATE TABLE sklegal_" in up and table in up
        assert "DROP TABLE sklegal_" in down and table in down


def test_every_feature_table_has_forced_rls() -> None:
    up, _ = sql_parts()
    assert up.count("ENABLE ROW LEVEL SECURITY") == 1
    assert "FOREACH target_table" in up
    assert "FORCE ROW LEVEL SECURITY" in up
    assert "record_is_authorized(tenant_id, matter_id)" in up


def test_versions_receipts_audit_and_outbox_are_append_only() -> None:
    up, _ = sql_parts()
    for table in (
        "work_product_feature_versions",
        "work_product_feature_idempotency",
        "work_product_feature_events",
        "work_product_feature_outbox",
    ):
        assert (
            f"'{('sklegal_audit' if table.endswith(('events', 'outbox')) else 'sklegal_legal')}.{table}'::regclass"
            in up
        )
    assert "BEFORE UPDATE OR DELETE" in up
    assert "reject_record_change()" in up


def test_identity_update_requires_one_version_increment() -> None:
    up, _ = sql_parts()
    assert "NEW.current_aggregate_version <> OLD.current_aggregate_version + 1" in up
    assert "current Work Product aggregate version is missing" in up
    assert "work_product_feature_identity_delete_denied" in up


def test_atomic_evidence_has_exact_foreign_keys() -> None:
    up, _ = sql_parts()
    assert "UNIQUE (tenant_id, matter_id, work_product_id, aggregate_version)" in up
    assert "REFERENCES sklegal_audit.work_product_feature_events" in up
    assert "REFERENCES sklegal_legal.work_product_feature_versions" in up
    assert "topic = 'sklegal.work_product.changed'" in up


def test_migration_does_not_modify_shared_manifest() -> None:
    assert MIGRATION.name.startswith("0050_")
    manifest = Path("migrations/manifest.json").read_text(encoding="utf-8")
    assert MIGRATION.name not in manifest
