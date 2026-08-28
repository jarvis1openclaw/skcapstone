from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path("migrations/0024_artifact_intake.sql")

TABLES = (
    "artifacts",
    "artifact_projection_revisions",
    "artifact_scan_events",
    "artifact_custody_events",
    "artifact_derivations",
    "artifact_record_links",
    "artifact_reviews",
    "artifact_corrections",
    "artifact_supersessions",
    "artifact_idempotency_receipts",
    "artifact_audit_facts",
    "artifact_outbox",
)


def test_migration_uses_reserved_number_and_is_reversible() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    assert text.count("-- sklegal:up") == 1
    assert text.count("-- sklegal:down") == 1
    up, down = text.split("-- sklegal:down", 1)
    assert "CREATE SCHEMA sklegal_artifact" in up
    assert down.rstrip().endswith("DROP SCHEMA sklegal_artifact;")
    for table in TABLES:
        assert f"CREATE TABLE sklegal_artifact.{table}" in up
        assert f"DROP TABLE sklegal_artifact.{table}" in down


def test_every_artifact_table_is_forced_rls_and_append_only() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    for table in TABLES:
        qualified = f"sklegal_artifact.{table}"
        assert f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY" in text
        assert f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY" in text
        assert re.search(
            rf"CREATE POLICY [a-z0-9_]+_select\s+ON {re.escape(qualified)}",
            text,
        )
        assert re.search(
            rf"CREATE TRIGGER [a-z0-9_]+_append_only\s+BEFORE UPDATE OR DELETE ON {re.escape(qualified)}",
            text,
        )


def test_migration_binds_scope_hash_lineage_policy_and_governance() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    required = (
        "FOREIGN KEY (tenant_id, matter_id)",
        "sklegal_identity.record_is_authorized(tenant_id, matter_id)",
        "content_sha256 sklegal_legal.sha256_digest NOT NULL",
        "original_sha256 sklegal_legal.sha256_digest NOT NULL",
        "UNIQUE (tenant_id, matter_id, content_sha256)",
        "parent_artifact_id uuid",
        "derivation_kind",
        "tool_name",
        "tool_version",
        "classification sklegal_legal.data_classification NOT NULL",
        "retention_policy_id uuid NOT NULL",
        "legal_hold_ids uuid[] NOT NULL",
        "ethical_wall_ids uuid[] NOT NULL",
        "validate_artifact_governance",
        "stale or cross-scope retention policy",
        "stale, released, or cross-scope legal hold",
        "inactive or cross-scope ethical wall",
        "REVOKE ALL ON FUNCTION sklegal_artifact.validate_artifact_governance()",
        "SECURITY DEFINER",
        "runtime_role_is_safe()",
        "validate_derivation_hashes",
        "require_derived_lineage",
        "artifact derivation is not content-hash connected",
        "policy_decision_id uuid NOT NULL",
        "policy_revision sklegal_legal.sha256_digest NOT NULL",
        "idempotency_key_sha256 sklegal_legal.sha256_digest NOT NULL",
        "request_sha256 sklegal_legal.sha256_digest NOT NULL",
        "superseded_artifact_id",
        "successor_artifact_id",
    )
    for fragment in required:
        assert fragment in text
    assert "Inbox/" not in text
    assert "content_base64" not in text


def test_lifecycle_is_version_bound_and_runtime_grants_are_least_privilege() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    required = (
        "artifact projection revision is not sequential",
        "artifact review is stale or targets a superseded artifact",
        "artifact correction lifecycle is invalid",
        "artifact supersession lifecycle is invalid",
        "original artifact cannot be superseded",
        "GRANT USAGE ON SCHEMA sklegal_artifact TO sklegal_runtime",
        "GRANT SELECT ON ALL TABLES IN SCHEMA sklegal_artifact TO sklegal_runtime",
        "GRANT INSERT ON",
        "REVOKE ALL ON ALL TABLES IN SCHEMA sklegal_artifact FROM PUBLIC",
    )
    for fragment in required:
        assert fragment in text
    assert "GRANT UPDATE" not in text
    assert "GRANT DELETE" not in text
    assert "TO PUBLIC" not in text


def test_review_attribution_and_outbox_are_consistently_append_only() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    assert "review_state <> 'proposed'" in text
    assert "reviewed_at IS NOT NULL" in text
    assert "reviewed_by_principal_id IS NOT NULL" in text
    outbox = text.split("CREATE TABLE sklegal_artifact.artifact_outbox (", 1)[1].split(
        ");", 1
    )[0]
    assert "delivered_at" not in outbox
    assert "artifact.activity.local" in outbox


def test_no_unreserved_migration_or_manifest_edit_is_present() -> None:
    owned = sorted(Path("migrations").glob("004[0-9]*"))
    assert owned == []
