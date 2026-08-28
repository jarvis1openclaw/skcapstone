"""Two-cluster PostgreSQL 17.7 and pgvector 0.8.0 qualification."""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from .helpers import FIXTURE, MATTER, OTHER_MATTER, PRINCIPAL, RIGHTS, TENANT

CORE_IMAGE = "sha256:bb377b7239d2774ac8cc76f481596ce96c5a6b5e9d141f6d0a0ee371a6e7c0f2"
RETRIEVAL_IMAGE = (
    "sha256:7afbb9c1cd01dfa0aff9a69881bf401283736a79d8744098d04fc576c72a8b16"
)
OTHER_TENANT = uuid.UUID("10000000-0000-4000-8000-000000000002")
ROOT = Path(__file__).parents[3]
CORE_MIGRATIONS = tuple(
    sorted((ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql"))
)
CORE_MIGRATION = ROOT / "migrations/0028_governed_corpus.sql"
RETRIEVAL_MIGRATION = ROOT / "migrations/retrieval/0001_governed_corpus_projection.sql"


def _docker_available() -> bool:
    commands = (
        ["docker", "info"],
        ["docker", "image", "inspect", CORE_IMAGE],
        ["docker", "image", "inspect", RETRIEVAL_IMAGE],
    )
    return all(
        subprocess.run(command, capture_output=True, check=False).returncode == 0
        for command in commands
    )


def _remove(name: str) -> None:
    subprocess.run(
        ["docker", "rm", "--force", name],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_cluster(name: str, image: str, card: str) -> None:
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--label",
            f"com.sklegal.test-card={card}",
            "--network",
            "none",
            "--tmpfs",
            "/var/lib/postgresql/data:rw,noexec,nosuid,size=512m",
            "--env",
            "POSTGRES_DB=sklegal",
            "--env",
            "POSTGRES_USER=postgres",
            "--env",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            image,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    atexit.register(_remove, name)


def _ready(name: str) -> None:
    consecutive = 0
    for attempt in range(180):
        ready = subprocess.run(
            [
                "docker",
                "exec",
                name,
                "pg_isready",
                "--username",
                "postgres",
                "--dbname",
                "sklegal",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if ready.returncode == 0:
            consecutive += 1
            if consecutive == 3:
                return
        else:
            consecutive = 0
        if attempt == 179:
            raise AssertionError(f"PostgreSQL readiness timeout: {name}")
        time.sleep(0.1)


def _psql(name: str, role: str, sql: str, *, check: bool = True):
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            name,
            "psql",
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--username",
            role,
            "--dbname",
            "sklegal",
            "--tuples-only",
            "--no-align",
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr.strip())
    return result


def _split(path: Path) -> tuple[str, str]:
    return tuple(path.read_text(encoding="utf-8").split("-- sklegal:down", 1))


def _fixture_rows() -> tuple[dict[str, object], list[dict[str, object]]]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for source in data["sources"]:
        source["chunkSha256"] = hashlib.sha256(
            source["exactSpan"].encode("utf-8")
        ).hexdigest()
    return data["projection"], data["sources"]


def _record_values(source: dict[str, object], *, retrieval: bool) -> str:
    record = json.dumps(source, separators=(",", ":"))
    permitted = "','".join(source["permittedPrincipalIds"])
    base = (
        f"'{source['tenantId']}', '{source['matterId']}', "
        f"'{source['sourceId']}', '{source['sourceVersionId']}', "
        f"'{source['sourceVersion']}', '{source['releaseId']}', "
        f"{source['projectionGeneration']}, {source['classification']}, "
        f"'{source['rightsRevision']}', ARRAY['{permitted}']::uuid[], "
        f"'{source['sourceSha256']}', '{source['chunkSha256']}', "
        f"$span${source['exactSpan']}$span$, "
    )
    if retrieval:
        embedding = ",".join(str(value) for value in source["embedding"])
        base += (
            f"to_tsvector('english', $span${source['title']} "
            f"{source['exactSpan']}$span$), '[{embedding}]'::public.vector, "
        )
    return (
        "("
        + base
        + "NULL, "
        + f"$json${record}$json$::jsonb, '{'c' * 64}', "
        + (f"'{source['authorizationDecisionId']}', " if not retrieval else "")
        + f"'{source['recordedAt']}')"
    )


@pytest.mark.skipif(
    not _docker_available(),
    reason="exact approved PostgreSQL and pgvector images are unavailable",
)
def test_two_cluster_lifecycle_outage_rebuild_and_contamination() -> None:
    suffix = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    core = f"sklegal-corpus-core-{suffix}"
    retrieval = f"sklegal-corpus-retrieval-{suffix}"
    _run_cluster(core, CORE_IMAGE, "SKL-MVP-CORPUS-01F4-core")
    _run_cluster(retrieval, RETRIEVAL_IMAGE, "SKL-MVP-CORPUS-01F4-retrieval")
    try:
        _ready(core)
        _ready(retrieval)
        _psql(
            core,
            "postgres",
            """
            CREATE ROLE sklegal_runtime LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            CREATE ROLE sklegal_migrator NOLOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            CREATE SCHEMA sklegal_migrations;
            """,
        )
        for migration in CORE_MIGRATIONS:
            _psql(core, "postgres", _split(migration)[0])
        retrieval_up, retrieval_down = _split(RETRIEVAL_MIGRATION)
        core_up, core_down = _split(CORE_MIGRATION)
        _psql(retrieval, "postgres", retrieval_up)

        core_identity = _psql(
            core,
            "postgres",
            """
            SELECT current_setting('server_version'),
                   NOT EXISTS (
                       SELECT 1 FROM pg_extension WHERE extname = 'vector'
                   ),
                   to_regtype('public.vector') IS NULL;
            """,
        ).stdout.strip()
        assert core_identity == "17.7|t|t"
        retrieval_identity = _psql(
            retrieval,
            "postgres",
            """
            SELECT current_setting('server_version'), extversion,
                   pg_typeof('[1,0,0]'::public.vector)::text
            FROM pg_extension WHERE extname = 'vector';
            """,
        ).stdout.strip()
        assert retrieval_identity == "17.7|0.8.0|vector"

        core_boundaries = _psql(
            core,
            "postgres",
            """
            SELECT to_regclass('sklegal_governed_corpus.source_versions') IS NOT NULL,
                   to_regclass('sklegal_governed_corpus.audit_events') IS NOT NULL,
                   to_regclass('sklegal_governed_corpus.outbox') IS NOT NULL,
                   to_regclass('sklegal_governed_corpus.source_projections') IS NULL;
            """,
        ).stdout.strip()
        assert core_boundaries == "t|t|t|t"
        retrieval_boundaries = _psql(
            retrieval,
            "postgres",
            """
            SELECT to_regclass('sklegal_governed_corpus.source_projections') IS NOT NULL,
                   to_regclass('sklegal_governed_corpus.audit_events') IS NULL,
                   to_regclass('sklegal_governed_corpus.outbox') IS NULL,
                   to_regnamespace('sklegal_identity') IS NULL,
                   to_regnamespace('sklegal_legal') IS NULL;
            """,
        ).stdout.strip()
        assert retrieval_boundaries == "t|t|t|t|t"
        assert (
            _psql(
                retrieval,
                "postgres",
                """
            SELECT count(*) FROM pg_constraint c
            JOIN pg_namespace n ON n.oid = c.connamespace
            WHERE n.nspname = 'sklegal_governed_corpus'
              AND c.contype = 'f';
            """,
            ).stdout.strip()
            == "0"
        )
        core_rls = _psql(
            core,
            "postgres",
            """
            SELECT bool_and(relrowsecurity AND relforcerowsecurity)
            FROM pg_class
            WHERE oid = ANY(ARRAY[
                'sklegal_governed_corpus.projection_registry'::regclass,
                'sklegal_governed_corpus.source_versions'::regclass,
                'sklegal_governed_corpus.idempotency_receipts'::regclass,
                'sklegal_governed_corpus.audit_events'::regclass,
                'sklegal_governed_corpus.outbox'::regclass
            ]);
            """,
        ).stdout.strip()
        assert core_rls == "t"
        retrieval_rls = _psql(
            retrieval,
            "postgres",
            """
            SELECT bool_and(relrowsecurity AND relforcerowsecurity)
            FROM pg_class
            WHERE oid = ANY(ARRAY[
                'sklegal_governed_corpus.projection_state'::regclass,
                'sklegal_governed_corpus.source_projections'::regclass,
                'sklegal_governed_corpus.projection_commands'::regclass
            ]);
            """,
        ).stdout.strip()
        assert retrieval_rls == "t"

        projection, sources = _fixture_rows()
        projection_json = json.dumps(projection, separators=(",", ":"))
        _psql(
            core,
            "postgres",
            f"""
            CREATE ROLE sklegal_corpus_core LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            INSERT INTO sklegal_identity.tenants
                (id, tenant_id, slug, name, status)
            VALUES ('{TENANT}', '{TENANT}', 'synthetic-corpus',
                    'Synthetic Corpus Tenant', 'active');
            INSERT INTO sklegal_identity.principals
                (id, tenant_id, principal_kind, display_name)
            VALUES ('{PRINCIPAL}', '{TENANT}', 'human', 'Synthetic Reviewer');
            INSERT INTO sklegal_identity.tenant_memberships
                (tenant_id, principal_id, membership_role)
            VALUES ('{TENANT}', '{PRINCIPAL}', 'member');
            INSERT INTO sklegal_identity.database_role_bindings
                (database_role, tenant_id, principal_id)
            VALUES ('sklegal_corpus_core', '{TENANT}', '{PRINCIPAL}');
            INSERT INTO sklegal_legal.clients
                (id, tenant_id, display_name, client_kind, status)
            VALUES ('60000000-0000-4000-8000-000000000001', '{TENANT}',
                    'Synthetic Client', 'company', 'active');
            INSERT INTO sklegal_legal.engagements
                (id, tenant_id, client_id, title, scope, status, valid_from)
            VALUES ('70000000-0000-4000-8000-000000000001', '{TENANT}',
                    '60000000-0000-4000-8000-000000000001',
                    'Synthetic Engagement', 'Synthetic scope', 'active',
                    '2026-08-01T00:00:00Z');
            INSERT INTO sklegal_legal.matters
                (id, tenant_id, matter_id, client_id, engagement_id, title,
                 summary, status, opened_at)
            VALUES ('{MATTER}', '{TENANT}', '{MATTER}',
                    '60000000-0000-4000-8000-000000000001',
                    '70000000-0000-4000-8000-000000000001',
                    'Synthetic Matter', 'Synthetic only.', 'open',
                    '2026-08-02T00:00:00Z'),
                   ('{OTHER_MATTER}', '{TENANT}', '{OTHER_MATTER}',
                    '60000000-0000-4000-8000-000000000001',
                    '70000000-0000-4000-8000-000000000001',
                    'Other Matter', 'No membership.', 'open',
                    '2026-08-03T00:00:00Z');
            INSERT INTO sklegal_legal.matter_memberships
                (tenant_id, matter_id, principal_id, membership_role)
            VALUES ('{TENANT}', '{MATTER}', '{PRINCIPAL}', 'member');
            INSERT INTO sklegal_governed_corpus.projection_registry
                (tenant_id, matter_id, projection_generation, release_id,
                 core_watermark, policy_revision, rights_revision, record,
                 recorded_at)
            VALUES ('{TENANT}', '{MATTER}', 3, 'synthetic-release-1', 42,
                    '{"2" * 64}', '{RIGHTS}', $json${projection_json}$json$,
                    '2026-08-23T12:00:00Z');
            INSERT INTO sklegal_governed_corpus.source_versions
                (tenant_id, matter_id, source_id, source_version_id,
                 source_version, release_id, projection_generation,
                 classification, rights_revision, permitted_principal_ids,
                 source_sha256, chunk_sha256, exact_span,
                 supersedes_source_version_id, record, record_sha256,
                 authorization_decision_id, recorded_at)
            VALUES {",".join(_record_values(source, retrieval=False) for source in sources)};
            INSERT INTO sklegal_governed_corpus.audit_events
                (tenant_id, matter_id, audit_id, source_version_id, action,
                 actor_principal_id, authorization_decision_id,
                 policy_decision_id, policy_revision, request_sha256,
                 source_sha256, correlation_id, occurred_at)
            VALUES ('{TENANT}', '{MATTER}',
                    '80000000-0000-4000-8000-000000000001',
                    '{sources[0]["sourceVersionId"]}', 'corpus.source.recorded',
                    '{PRINCIPAL}', '{sources[0]["authorizationDecisionId"]}',
                    '{sources[0]["policyDecisionId"]}', '{"2" * 64}',
                    '{"d" * 64}', '{"c" * 64}',
                    '81000000-0000-4000-8000-000000000001',
                    '2026-08-23T12:00:00Z');
            INSERT INTO sklegal_governed_corpus.outbox
                (tenant_id, matter_id, outbox_id, audit_id, source_version_id,
                 topic, payload_sha256, qdrant_dispatch_allowed,
                 falkordb_dispatch_allowed, created_at)
            VALUES ('{TENANT}', '{MATTER}',
                    '82000000-0000-4000-8000-000000000001',
                    '80000000-0000-4000-8000-000000000001',
                    '{sources[0]["sourceVersionId"]}',
                    'corpus.projection.requested', '{"c" * 64}', false, false,
                    '2026-08-23T12:00:00Z');
            GRANT USAGE ON SCHEMA sklegal_identity, sklegal_legal,
                sklegal_governed_corpus TO sklegal_corpus_core;
            GRANT SELECT ON sklegal_identity.database_role_bindings,
                sklegal_identity.tenant_memberships,
                sklegal_legal.matter_memberships,
                sklegal_governed_corpus.projection_registry,
                sklegal_governed_corpus.source_versions,
                sklegal_governed_corpus.audit_events,
                sklegal_governed_corpus.outbox TO sklegal_corpus_core;
            GRANT EXECUTE ON FUNCTION sklegal_governed_corpus.current_source_v1(
                uuid, uuid, uuid, text, integer, text, text, integer
            ) TO sklegal_corpus_core;
            GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA sklegal_identity
                TO sklegal_corpus_core;
            GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA sklegal_legal
                TO sklegal_corpus_core;
            """,
        )

        _psql(
            retrieval,
            "postgres",
            f"""
            CREATE ROLE sklegal_retrieval_runtime LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            CREATE ROLE sklegal_retrieval_projector LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            INSERT INTO sklegal_governed_corpus.projection_state
                (tenant_id, matter_id, projection_generation, release_id,
                 backend_watermark, core_watermark, record, recorded_at)
            VALUES ('{TENANT}', '{MATTER}', 3, 'synthetic-release-1', 42, 42,
                    $json${projection_json}$json$, '2026-08-23T12:00:00Z');
            INSERT INTO sklegal_governed_corpus.source_projections
                (tenant_id, matter_id, source_id, source_version_id,
                 source_version, release_id, projection_generation,
                 classification, rights_revision, permitted_principal_ids,
                 source_sha256, chunk_sha256, exact_span, search_document,
                 embedding, supersedes_source_version_id, record,
                 record_sha256, recorded_at)
            VALUES {",".join(_record_values(source, retrieval=True) for source in sources)};
            GRANT USAGE ON SCHEMA sklegal_governed_corpus
                TO sklegal_retrieval_runtime, sklegal_retrieval_projector;
            GRANT SELECT ON sklegal_governed_corpus.projection_state,
                sklegal_governed_corpus.source_projections
                TO sklegal_retrieval_runtime;
            GRANT SELECT, INSERT, UPDATE, DELETE ON
                sklegal_governed_corpus.projection_state,
                sklegal_governed_corpus.source_projections,
                sklegal_governed_corpus.projection_commands
                TO sklegal_retrieval_projector;
            GRANT EXECUTE ON FUNCTION sklegal_governed_corpus.search_v1(
                uuid, uuid, uuid, text, public.vector, text, text, integer,
                integer, text, timestamptz, double precision, uuid, integer
            ) TO sklegal_retrieval_runtime;
            GRANT EXECUTE ON FUNCTION
                sklegal_governed_corpus.vector_exact_distance_v1(
                    public.vector, public.vector
                ) TO sklegal_retrieval_runtime;
            """,
        )

        core_visible = _psql(
            core,
            "sklegal_corpus_core",
            "SELECT count(*) FROM sklegal_governed_corpus.source_versions;",
        ).stdout.strip()
        assert core_visible == "2"
        retrieval_scope = (
            f"SET sklegal.tenant_id = '{TENANT}';"
            f"SET sklegal.matter_id = '{MATTER}';"
            f"SET sklegal.principal_id = '{PRINCIPAL}';"
        )
        lexical = (
            _psql(
                retrieval,
                "sklegal_retrieval_runtime",
                retrieval_scope
                + f"""
            SELECT record->>'sourceId'
            FROM sklegal_governed_corpus.search_v1(
                '{TENANT}', '{MATTER}', '{PRINCIPAL}', 'filing date', NULL,
                'full_text', 'synthetic-release-1', 3, 0, '{RIGHTS}',
                '2026-08-23T12:00:00Z', NULL, NULL, 10
            );
            """,
            )
            .stdout.strip()
            .splitlines()[-1]
        )
        assert lexical == "synthetic-filing-record"
        vector = (
            _psql(
                retrieval,
                "sklegal_retrieval_runtime",
                retrieval_scope
                + f"""
            SELECT record->>'sourceId'
            FROM sklegal_governed_corpus.search_v1(
                '{TENANT}', '{MATTER}', '{PRINCIPAL}', 'synthetic',
                '[1,0,0]'::public.vector, 'hybrid_rrf',
                'synthetic-release-1', 3, 0, '{RIGHTS}',
                '2026-08-23T12:00:00Z', NULL, NULL, 10
            );
            """,
            )
            .stdout.strip()
            .splitlines()[-2:]
        )
        assert vector == ["synthetic-filing-record", "synthetic-response-rule"]
        hnsw_plan = _psql(
            retrieval,
            "postgres",
            """
            ANALYZE sklegal_governed_corpus.source_projections;
            SET enable_seqscan = off;
            EXPLAIN (COSTS OFF)
            SELECT source_id
            FROM sklegal_governed_corpus.source_projections
            WHERE public.vector_dims(embedding) = 3
            ORDER BY (embedding::public.vector(3))
                <=> '[1,0,0]'::public.vector
            LIMIT 2;
            """,
        ).stdout
        assert "governed_corpus_vector_hnsw_idx" in hnsw_plan

        denied = (
            _psql(
                retrieval,
                "sklegal_retrieval_runtime",
                f"SET sklegal.tenant_id = '{OTHER_TENANT}';"
                f"SET sklegal.matter_id = '{MATTER}';"
                f"SET sklegal.principal_id = '{PRINCIPAL}';"
                "SELECT count(*) FROM sklegal_governed_corpus.source_projections;",
            )
            .stdout.strip()
            .splitlines()[-1]
        )
        assert denied == "0"
        mutation = _psql(
            core,
            "postgres",
            "UPDATE sklegal_governed_corpus.source_versions SET source_version = 'x';",
            check=False,
        )
        assert mutation.returncode != 0
        assert "append-only" in mutation.stderr

        _psql(
            retrieval,
            "postgres",
            "DELETE FROM sklegal_governed_corpus.source_projections;",
        )
        absent = _psql(
            retrieval,
            "postgres",
            "SELECT count(*) FROM sklegal_governed_corpus.source_projections;",
        ).stdout.strip()
        assert absent == "0"
        _psql(
            retrieval,
            "postgres",
            f"""
            INSERT INTO sklegal_governed_corpus.source_projections
                (tenant_id, matter_id, source_id, source_version_id,
                 source_version, release_id, projection_generation,
                 classification, rights_revision, permitted_principal_ids,
                 source_sha256, chunk_sha256, exact_span, search_document,
                 embedding, supersedes_source_version_id, record,
                 record_sha256, recorded_at)
            VALUES {",".join(_record_values(source, retrieval=True) for source in reversed(sources))};
            """,
        )
        role_safety = (
            _psql(
                retrieval,
                "postgres",
                """
            SELECT rolname, rolsuper, rolcreatedb, rolcreaterole,
                   rolinherit, rolbypassrls
            FROM pg_roles
            WHERE rolname IN (
                'sklegal_retrieval_runtime', 'sklegal_retrieval_projector'
            )
            ORDER BY rolname;
            """,
            )
            .stdout.strip()
            .splitlines()
        )
        assert role_safety == [
            "sklegal_retrieval_projector|f|f|f|f|f",
            "sklegal_retrieval_runtime|f|f|f|f|f",
        ]
        rebuilt = (
            _psql(
                retrieval,
                "sklegal_retrieval_runtime",
                retrieval_scope
                + f"""
            SELECT record->>'sourceId'
            FROM sklegal_governed_corpus.search_v1(
                '{TENANT}', '{MATTER}', '{PRINCIPAL}', 'synthetic',
                '[1,0,0]'::public.vector, 'hybrid_rrf',
                'synthetic-release-1', 3, 0, '{RIGHTS}',
                '2026-08-23T12:00:00Z', NULL, NULL, 10
            );
            """,
            )
            .stdout.strip()
            .splitlines()[-2:]
        )
        assert rebuilt == vector

        subprocess.run(["docker", "pause", retrieval], check=True, capture_output=True)
        core_during_outage = _psql(
            core,
            "sklegal_corpus_core",
            "SELECT count(*) FROM sklegal_governed_corpus.outbox;",
        ).stdout.strip()
        assert core_during_outage == "1"
        subprocess.run(
            ["docker", "unpause", retrieval], check=True, capture_output=True
        )

        contamination_core = _psql(core, "postgres", retrieval_up, check=False)
        assert contamination_core.returncode != 0
        contamination_retrieval = _psql(retrieval, "postgres", core_up, check=False)
        assert contamination_retrieval.returncode != 0

        _psql(retrieval, "postgres", retrieval_down)
        retrieval_absence = _psql(
            retrieval,
            "postgres",
            """
            SELECT to_regnamespace('sklegal_governed_corpus') IS NULL,
                   NOT EXISTS (
                       SELECT 1 FROM pg_extension WHERE extname = 'vector'
                   );
            """,
        ).stdout.strip()
        assert retrieval_absence == "t|t"
        _psql(retrieval, "postgres", retrieval_up)
        assert (
            _psql(
                retrieval,
                "postgres",
                "SELECT extversion FROM pg_extension WHERE extname = 'vector';",
            ).stdout.strip()
            == "0.8.0"
        )

        _psql(core, "postgres", core_down)
        core_absence = _psql(
            core,
            "postgres",
            "SELECT to_regnamespace('sklegal_governed_corpus') IS NULL;",
        ).stdout.strip()
        assert core_absence == "t"
        _psql(core, "postgres", core_up)
        assert (
            _psql(
                core,
                "postgres",
                "SELECT to_regclass('sklegal_governed_corpus.outbox') IS NOT NULL;",
            ).stdout.strip()
            == "t"
        )
    finally:
        _remove(retrieval)
        _remove(core)
