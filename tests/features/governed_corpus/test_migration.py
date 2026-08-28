from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parents[3]
CORE_MIGRATION = ROOT / "migrations/0028_governed_corpus.sql"
RETRIEVAL_ROOT = ROOT / "migrations/retrieval"
RETRIEVAL_MIGRATION = RETRIEVAL_ROOT / "0001_governed_corpus_projection.sql"


def test_core_migration_owns_only_canonical_append_only_state() -> None:
    text = CORE_MIGRATION.read_text(encoding="utf-8")
    up, down = text.split("-- sklegal:down", 1)
    assert up.count("CREATE SCHEMA sklegal_governed_corpus") == 1
    tables = (
        "projection_registry",
        "source_versions",
        "idempotency_receipts",
        "audit_events",
        "outbox",
    )
    for table in tables:
        assert f"CREATE TABLE sklegal_governed_corpus.{table}" in up
        assert (
            f"ALTER TABLE sklegal_governed_corpus.{table} FORCE ROW LEVEL SECURITY"
            in up
        )
        assert f"DROP TABLE sklegal_governed_corpus.{table}" in down
    assert up.count("EXECUTE FUNCTION sklegal_legal.reject_record_change()") == 5
    assert up.count("sklegal_identity.record_is_authorized(tenant_id, matter_id)") == 10
    assert "CREATE EXTENSION vector" not in text
    assert "public.vector" not in text
    assert "tsvector" not in text
    assert "search_v1" not in text
    assert "DROP SCHEMA sklegal_governed_corpus" in down


def test_retrieval_migration_owns_only_rebuildable_search_projection() -> None:
    text = RETRIEVAL_MIGRATION.read_text(encoding="utf-8")
    assert "CREATE EXTENSION vector WITH SCHEMA public VERSION '0.8.0'" in text
    assert "embedding public.vector NOT NULL" in text
    assert "public.vector_dims(embedding) BETWEEN 1 AND 4096" in text
    assert "search_document tsvector" in text
    assert "USING gin (search_document)" in text
    assert "USING hnsw ((embedding::public.vector(3)) public.vector_cosine_ops)" in text
    assert "WHERE public.vector_dims(embedding) = 3" in text
    assert "vector_exact_distance_v1" in text
    assert "OPERATOR(public.<=>)" in text
    assert "double precision[]" not in text
    assert "unnest(left_embedding)" not in text
    assert "'vector_exact'" in text
    assert "'hybrid_rrf'" in text
    assert "requested_principal_id = ANY(candidate.permitted_principal_ids)" in text
    assert "successor.supersedes_source_version_id" in text
    assert "Qdrant and FalkorDB remain metadata-only" in text
    assert "DROP EXTENSION vector" in text
    assert "CREATE TABLE sklegal_governed_corpus.source_projections" in text
    assert "CREATE TABLE sklegal_governed_corpus.projection_commands" in text
    assert "CREATE TABLE sklegal_governed_corpus.projection_state" in text
    assert "CREATE TABLE sklegal_governed_corpus.source_versions" not in text
    assert "CREATE TABLE sklegal_governed_corpus.audit_events" not in text
    assert "CREATE TABLE sklegal_governed_corpus.outbox" not in text
    assert "REFERENCES sklegal_legal" not in text
    assert "sklegal_identity" not in text


def test_retrieval_migration_binds_snapshot_cursor_and_final_sort_key() -> None:
    text = RETRIEVAL_MIGRATION.read_text(encoding="utf-8")
    up, down = text.split("-- sklegal:down", 1)
    assert "snapshot_at timestamptz" in up
    assert "after_score double precision" in up
    assert "after_source_version_id uuid" in up
    assert "candidate.recorded_at <= snapshot_at" in up
    assert "successor.recorded_at <= snapshot_at" in up
    assert "after_score IS NULL AND after_source_version_id IS NULL" in up
    assert "scored.final_score < after_score" in up
    assert "scored.source_version_id > after_source_version_id" in up
    assert "ORDER BY scored.final_score DESC, scored.source_version_id" in up
    assert "LIMIT LEAST(fetch_limit, 51)" in up
    assert "timestamptz, double precision, uuid, integer" in down


def test_retrieval_manifest_is_independent_and_exact() -> None:
    manifest = json.loads(
        (RETRIEVAL_ROOT / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == 1
    assert [item["file"] for item in manifest["migrations"]] == [
        "0001_governed_corpus_projection.sql"
    ]
    assert (
        manifest["migrations"][0]["sha256"]
        == hashlib.sha256(RETRIEVAL_MIGRATION.read_bytes()).hexdigest()
    )
