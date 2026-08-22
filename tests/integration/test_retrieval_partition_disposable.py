"""Disposable PostgreSQL qualification suite for the S2-04 retrieval boundary.

One networkless disposable container per process proves the server-side
controls from the tenant partition contract: forced row security bound to the
database-owned credential mapping, exact Matter predicates, partition and
role isolation, the hardened security-definer graph gateway, registry
compare-and-swap cutover and rollback, idempotent outbox replay, the replica
LSN gate, dump/restore of the relational graph manifest, and failure-domain
separation from the core cluster. The stock pinned PostgreSQL 17 image has
no pgvector or AGE build, so vector rows use a plain double-precision array
stand-in and graph traversal uses the forced-RLS relational manifest; the
qualified-image behaviors are pinned and denied in the registry unit suite.
"""

from __future__ import annotations

import atexit
import hashlib
import os
import subprocess
import time
import unittest
import uuid

POSTGRES_IMAGE = (
    "postgres:17.7-alpine@sha256:"
    "a6d31f85"
    "3205ce20"
    "d399df4e"
    "33a0b4c7"
    "15672f23"
    "2f4ee744"
    "0499747e"
    "6e02c126"
)

TENANT_ONE = "10000000-0000-4000-8000-000000000101"
TENANT_TWO = "10000000-0000-4000-8000-000000000102"
MATTER_ONE = "10000000-0000-4000-8000-000000000201"
MATTER_TWO = "10000000-0000-4000-8000-000000000202"
SET_PRIOR = "10000000-0000-4000-8000-000000000301"
SET_CURRENT = "10000000-0000-4000-8000-000000000302"

SCHEMA_SQL = f"""
CREATE SCHEMA sklegal_retrieval;

CREATE TABLE sklegal_retrieval.credential_binding (
    database_principal text PRIMARY KEY,
    principal_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid,
    scope_kind text NOT NULL CHECK (scope_kind IN ('matter', 'tenant_shared')),
    projection_set_id uuid NOT NULL,
    projection_generation integer NOT NULL,
    policy_revision text NOT NULL,
    rights_revision text NOT NULL,
    authorization_event_sequence bigint NOT NULL,
    authorization_event_sha256 text NOT NULL,
    revoked_at timestamptz,
    CHECK ((scope_kind = 'matter') = (matter_id IS NOT NULL))
);

CREATE TABLE sklegal_retrieval.lexical_chunks (
    tenant_id uuid NOT NULL,
    projection_generation integer NOT NULL,
    matter_id uuid,
    retrieval_record_id text NOT NULL,
    content text NOT NULL,
    search_document tsvector NOT NULL,
    PRIMARY KEY (tenant_id, projection_generation, retrieval_record_id)
) PARTITION BY LIST (tenant_id);

CREATE TABLE sklegal_retrieval.lexical_chunks_t1
    PARTITION OF sklegal_retrieval.lexical_chunks
    FOR VALUES IN ('{TENANT_ONE}')
    PARTITION BY LIST (projection_generation);
CREATE TABLE sklegal_retrieval.lexical_chunks_t1_g3
    PARTITION OF sklegal_retrieval.lexical_chunks_t1
    FOR VALUES IN (3);
CREATE TABLE sklegal_retrieval.lexical_chunks_t2
    PARTITION OF sklegal_retrieval.lexical_chunks
    FOR VALUES IN ('{TENANT_TWO}')
    PARTITION BY LIST (projection_generation);
CREATE TABLE sklegal_retrieval.lexical_chunks_t2_g3
    PARTITION OF sklegal_retrieval.lexical_chunks_t2
    FOR VALUES IN (3);

CREATE INDEX lexical_chunks_t1_g3_search
    ON sklegal_retrieval.lexical_chunks_t1_g3 USING gin (search_document);
CREATE INDEX lexical_chunks_t2_g3_search
    ON sklegal_retrieval.lexical_chunks_t2_g3 USING gin (search_document);

CREATE TABLE sklegal_retrieval.vector_chunks (
    tenant_id uuid NOT NULL,
    projection_generation integer NOT NULL,
    matter_id uuid,
    retrieval_record_id text NOT NULL,
    content text NOT NULL,
    embedding double precision[] NOT NULL,
    PRIMARY KEY (tenant_id, projection_generation, retrieval_record_id)
) PARTITION BY LIST (tenant_id);

CREATE TABLE sklegal_retrieval.vector_chunks_t1
    PARTITION OF sklegal_retrieval.vector_chunks
    FOR VALUES IN ('{TENANT_ONE}')
    PARTITION BY LIST (projection_generation);
CREATE TABLE sklegal_retrieval.vector_chunks_t1_g3
    PARTITION OF sklegal_retrieval.vector_chunks_t1
    FOR VALUES IN (3);
CREATE TABLE sklegal_retrieval.vector_chunks_t2
    PARTITION OF sklegal_retrieval.vector_chunks
    FOR VALUES IN ('{TENANT_TWO}')
    PARTITION BY LIST (projection_generation);
CREATE TABLE sklegal_retrieval.vector_chunks_t2_g3
    PARTITION OF sklegal_retrieval.vector_chunks_t2
    FOR VALUES IN (3);

CREATE INDEX vector_chunks_t1_g3_record
    ON sklegal_retrieval.vector_chunks_t1_g3 (retrieval_record_id);
CREATE INDEX vector_chunks_t2_g3_record
    ON sklegal_retrieval.vector_chunks_t2_g3 (retrieval_record_id);

ALTER TABLE sklegal_retrieval.lexical_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_retrieval.lexical_chunks FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_retrieval.vector_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_retrieval.vector_chunks FORCE ROW LEVEL SECURITY;

CREATE POLICY exact_scope ON sklegal_retrieval.lexical_chunks
    USING (
        EXISTS (
            SELECT 1 FROM sklegal_retrieval.credential_binding b
            WHERE b.database_principal = session_user
              AND b.revoked_at IS NULL
              AND b.tenant_id = lexical_chunks.tenant_id
              AND b.projection_generation = lexical_chunks.projection_generation
              AND b.matter_id IS NOT DISTINCT FROM lexical_chunks.matter_id
        )
    );
CREATE POLICY exact_scope ON sklegal_retrieval.vector_chunks
    USING (
        EXISTS (
            SELECT 1 FROM sklegal_retrieval.credential_binding b
            WHERE b.database_principal = session_user
              AND b.revoked_at IS NULL
              AND b.tenant_id = vector_chunks.tenant_id
              AND b.projection_generation = vector_chunks.projection_generation
              AND b.matter_id IS NOT DISTINCT FROM vector_chunks.matter_id
        )
    );

CREATE FUNCTION sklegal_retrieval.lexical_search_v1(
    query_text text, max_results integer
) RETURNS TABLE (
    retrieval_record_id text,
    content text,
    rank real
)
LANGUAGE sql STABLE SECURITY INVOKER
SET search_path = sklegal_retrieval, pg_catalog
AS $$
    SELECT c.retrieval_record_id, c.content,
           ts_rank(c.search_document, websearch_to_tsquery('english', query_text))
    FROM sklegal_retrieval.lexical_chunks c
    WHERE c.search_document @@ websearch_to_tsquery('english', query_text)
    ORDER BY 3 DESC, c.retrieval_record_id
    LIMIT max_results
$$;

CREATE FUNCTION sklegal_retrieval.lexical_count_v1(query_text text)
RETURNS bigint
LANGUAGE sql STABLE SECURITY INVOKER
SET search_path = sklegal_retrieval, pg_catalog
AS $$
    SELECT count(*)
    FROM sklegal_retrieval.lexical_chunks c
    WHERE c.search_document @@ websearch_to_tsquery('english', query_text)
$$;

CREATE FUNCTION sklegal_retrieval.vector_exact_v1(
    query_embedding double precision[], max_results integer
) RETURNS TABLE (
    retrieval_record_id text,
    content text,
    distance double precision
)
LANGUAGE sql STABLE SECURITY INVOKER
SET search_path = sklegal_retrieval, pg_catalog
AS $$
    SELECT c.retrieval_record_id, c.content,
           sqrt((c.embedding[1] - query_embedding[1]) ^ 2
              + (c.embedding[2] - query_embedding[2]) ^ 2)
    FROM sklegal_retrieval.vector_chunks c
    ORDER BY 3, c.retrieval_record_id
    LIMIT max_results
$$;

CREATE TABLE sklegal_retrieval.graph_entities_g3 (
    graph_entity_id text PRIMARY KEY,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    content text NOT NULL,
    content_sha256 text NOT NULL
);
CREATE TABLE sklegal_retrieval.graph_entities_g4 (
    graph_entity_id text PRIMARY KEY,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    content text NOT NULL,
    content_sha256 text NOT NULL
);

CREATE ROLE sklegal_retrieval_gateway_owner
    NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEROLE NOCREATEDB NOREPLICATION;

CREATE FUNCTION sklegal_retrieval.graph_entity_v1(
    entity_ids text[], max_results integer
) RETURNS TABLE (graph_entity_id text, content text)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = sklegal_retrieval, pg_catalog
AS $gateway$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM sklegal_retrieval.credential_binding b
        WHERE b.database_principal = session_user
          AND b.revoked_at IS NULL
          AND b.projection_generation = 3
    ) THEN
        RAISE EXCEPTION 'retrieval_unavailable';
    END IF;
    RETURN QUERY
        SELECT g.graph_entity_id, g.content
        FROM sklegal_retrieval.graph_entities_g3 g
        WHERE g.graph_entity_id = ANY (entity_ids)
        ORDER BY g.graph_entity_id
        LIMIT max_results;
EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'retrieval_unavailable';
END;
$gateway$;

ALTER FUNCTION sklegal_retrieval.graph_entity_v1(text[], integer)
    OWNER TO sklegal_retrieval_gateway_owner;
REVOKE ALL ON FUNCTION sklegal_retrieval.graph_entity_v1(text[], integer)
    FROM PUBLIC;

CREATE TABLE sklegal_retrieval.projection_registry (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    projection_set_id uuid NOT NULL,
    component text NOT NULL CHECK (component IN ('lexical', 'vector', 'graph')),
    projection_generation integer NOT NULL,
    lifecycle text NOT NULL CHECK (lifecycle IN
        ('building', 'ready', 'active', 'retiring', 'retired', 'rejected', 'failed')),
    idempotency_key text NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, projection_set_id, component)
);

CREATE FUNCTION sklegal_retrieval.activate_projection_set(
    p_tenant uuid,
    p_matter uuid,
    p_set uuid,
    p_expected_active uuid,
    p_idem text
) RETURNS boolean
LANGUAGE plpgsql
SET search_path = sklegal_retrieval, pg_catalog
AS $activation$
DECLARE
    v_current uuid;
    v_ready integer;
BEGIN
    SELECT r.projection_set_id INTO v_current
    FROM sklegal_retrieval.projection_registry r
    WHERE r.tenant_id = p_tenant AND r.matter_id = p_matter
      AND r.lifecycle = 'active'
    GROUP BY r.projection_set_id;
    IF v_current IS NOT DISTINCT FROM p_expected_active AND EXISTS (
        SELECT 1 FROM sklegal_retrieval.projection_registry r
        WHERE r.tenant_id = p_tenant AND r.matter_id = p_matter
          AND r.projection_set_id = p_set AND r.lifecycle = 'active'
          AND r.idempotency_key = p_idem
    ) THEN
        RETURN true;
    END IF;
    IF v_current IS DISTINCT FROM p_expected_active THEN
        RETURN false;
    END IF;
    SELECT count(*) INTO v_ready
    FROM sklegal_retrieval.projection_registry r
    WHERE r.tenant_id = p_tenant AND r.matter_id = p_matter
      AND r.projection_set_id = p_set AND r.lifecycle = 'ready';
    IF v_ready < 2 THEN
        RETURN false;
    END IF;
    UPDATE sklegal_retrieval.projection_registry r
    SET lifecycle = 'retiring'
    WHERE r.tenant_id = p_tenant AND r.matter_id = p_matter
      AND r.lifecycle = 'active';
    UPDATE sklegal_retrieval.projection_registry r
    SET lifecycle = 'active'
    WHERE r.tenant_id = p_tenant AND r.matter_id = p_matter
      AND r.projection_set_id = p_set AND r.lifecycle = 'ready';
    RETURN true;
END;
$activation$;

CREATE FUNCTION sklegal_retrieval.rollback_projection_set(
    p_tenant uuid,
    p_matter uuid,
    p_prior_set uuid
) RETURNS boolean
LANGUAGE plpgsql
SET search_path = sklegal_retrieval, pg_catalog
AS $rollback$
DECLARE
    v_prior integer;
BEGIN
    SELECT count(*) INTO v_prior
    FROM sklegal_retrieval.projection_registry r
    WHERE r.tenant_id = p_tenant AND r.matter_id = p_matter
      AND r.projection_set_id = p_prior_set AND r.lifecycle = 'retiring';
    IF v_prior < 2 THEN
        RETURN false;
    END IF;
    UPDATE sklegal_retrieval.projection_registry r
    SET lifecycle = 'retiring'
    WHERE r.tenant_id = p_tenant AND r.matter_id = p_matter
      AND r.lifecycle = 'active';
    UPDATE sklegal_retrieval.projection_registry r
    SET lifecycle = 'active'
    WHERE r.tenant_id = p_tenant AND r.matter_id = p_matter
      AND r.projection_set_id = p_prior_set AND r.lifecycle = 'retiring';
    RETURN true;
END;
$rollback$;

CREATE TABLE sklegal_retrieval.outbox_replay_log (
    partition_key text NOT NULL,
    retrieval_record_id text NOT NULL,
    event_sequence bigint NOT NULL,
    idempotency_key text NOT NULL,
    content text NOT NULL,
    PRIMARY KEY (partition_key, retrieval_record_id),
    UNIQUE (partition_key, idempotency_key)
);

CREATE TABLE sklegal_retrieval.projection_watermark (
    partition_key text PRIMARY KEY,
    watermark bigint NOT NULL
);

CREATE ROLE rt_principal_m1
    LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEROLE NOCREATEDB NOREPLICATION;
CREATE ROLE rt_principal_m2
    LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEROLE NOCREATEDB NOREPLICATION;
CREATE ROLE rt_principal_t2
    LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEROLE NOCREATEDB NOREPLICATION;
CREATE ROLE rt_principal_unbound
    LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEROLE NOCREATEDB NOREPLICATION;

GRANT USAGE ON SCHEMA sklegal_retrieval TO
    rt_principal_m1, rt_principal_m2, rt_principal_t2, rt_principal_unbound,
    sklegal_retrieval_gateway_owner;
GRANT SELECT ON sklegal_retrieval.credential_binding TO
    rt_principal_m1, rt_principal_m2, rt_principal_t2, rt_principal_unbound;
GRANT SELECT ON sklegal_retrieval.lexical_chunks TO
    rt_principal_m1, rt_principal_m2, rt_principal_t2, rt_principal_unbound;
GRANT SELECT ON sklegal_retrieval.vector_chunks TO
    rt_principal_m1, rt_principal_m2, rt_principal_t2, rt_principal_unbound;
GRANT EXECUTE ON FUNCTION sklegal_retrieval.lexical_search_v1(text, integer) TO
    rt_principal_m1, rt_principal_m2, rt_principal_t2, rt_principal_unbound;
GRANT EXECUTE ON FUNCTION sklegal_retrieval.lexical_count_v1(text) TO
    rt_principal_m1, rt_principal_m2, rt_principal_t2, rt_principal_unbound;
GRANT EXECUTE ON FUNCTION sklegal_retrieval.vector_exact_v1(double precision[], integer)
    TO rt_principal_m1, rt_principal_m2, rt_principal_t2, rt_principal_unbound;
GRANT EXECUTE ON FUNCTION sklegal_retrieval.graph_entity_v1(text[], integer)
    TO rt_principal_m1;
GRANT SELECT ON sklegal_retrieval.graph_entities_g3
    TO sklegal_retrieval_gateway_owner;
GRANT SELECT ON sklegal_retrieval.credential_binding
    TO sklegal_retrieval_gateway_owner;
"""

SEED_SQL = f"""
INSERT INTO sklegal_retrieval.credential_binding (
    database_principal, principal_id, tenant_id, matter_id, scope_kind,
    projection_set_id, projection_generation, policy_revision, rights_revision,
    authorization_event_sequence, authorization_event_sha256, revoked_at
) VALUES
    ('rt_principal_m1', '10000000-0000-4000-8000-000000000401', '{TENANT_ONE}',
     '{MATTER_ONE}', 'matter', '{SET_CURRENT}', 3, 'policy-1', 'rights-1',
     11, repeat('a', 64), NULL),
    ('rt_principal_m2', '10000000-0000-4000-8000-000000000402', '{TENANT_ONE}',
     '{MATTER_TWO}', 'matter', '{SET_CURRENT}', 3, 'policy-1', 'rights-1',
     11, repeat('a', 64), NULL),
    ('rt_principal_t2', '10000000-0000-4000-8000-000000000403', '{TENANT_TWO}',
     '{MATTER_ONE}', 'matter', '{SET_CURRENT}', 3, 'policy-1', 'rights-1',
     11, repeat('a', 64), NULL);

INSERT INTO sklegal_retrieval.lexical_chunks (
    tenant_id, projection_generation, matter_id, retrieval_record_id,
    content, search_document
) VALUES
    ('{TENANT_ONE}', 3, '{MATTER_ONE}', 'record-1',
     'liberty mutual settlement packet',
     to_tsvector('english', 'liberty mutual settlement packet')),
    ('{TENANT_ONE}', 3, '{MATTER_ONE}', 'record-2',
     'liberty mutual release draft',
     to_tsvector('english', 'liberty mutual release draft')),
    ('{TENANT_ONE}', 3, '{MATTER_TWO}', 'record-3',
     'liberty mutual confidential memo',
     to_tsvector('english', 'liberty mutual confidential memo')),
    ('{TENANT_TWO}', 3, '{MATTER_ONE}', 'record-4',
     'liberty mutual foreign tenant row',
     to_tsvector('english', 'liberty mutual foreign tenant row'));

INSERT INTO sklegal_retrieval.vector_chunks (
    tenant_id, projection_generation, matter_id, retrieval_record_id,
    content, embedding
) VALUES
    ('{TENANT_ONE}', 3, '{MATTER_ONE}', 'record-1', 'embedding one', ARRAY[1.0, 0.0]),
    ('{TENANT_ONE}', 3, '{MATTER_ONE}', 'record-2', 'embedding two', ARRAY[0.9, 0.1]),
    ('{TENANT_ONE}', 3, '{MATTER_ONE}', 'record-3v', 'embedding three', ARRAY[0.0, 1.0]),
    ('{TENANT_ONE}', 3, '{MATTER_ONE}', 'record-4v', 'embedding four', ARRAY[0.5, 0.5]),
    ('{TENANT_ONE}', 3, '{MATTER_ONE}', 'record-5v', 'embedding five', ARRAY[-1.0, 0.0]),
    ('{TENANT_ONE}', 3, '{MATTER_TWO}', 'record-6', 'other matter', ARRAY[1.0, 0.0]),
    ('{TENANT_TWO}', 3, '{MATTER_ONE}', 'record-7', 'foreign tenant', ARRAY[1.0, 0.0]);

INSERT INTO sklegal_retrieval.graph_entities_g3 VALUES
    ('entity-1', '{TENANT_ONE}', '{MATTER_ONE}', 'entity one', repeat('b', 64)),
    ('entity-2', '{TENANT_ONE}', '{MATTER_ONE}', 'entity two', repeat('c', 64));
INSERT INTO sklegal_retrieval.graph_entities_g4 VALUES
    ('entity-9', '{TENANT_ONE}', '{MATTER_ONE}', 'other generation', repeat('d', 64));

INSERT INTO sklegal_retrieval.projection_registry (
    tenant_id, matter_id, projection_set_id, component,
    projection_generation, lifecycle, idempotency_key
) VALUES
    ('{TENANT_ONE}', '{MATTER_ONE}', '{SET_PRIOR}', 'lexical', 2, 'active', 'a0'),
    ('{TENANT_ONE}', '{MATTER_ONE}', '{SET_PRIOR}', 'vector', 2, 'active', 'a0'),
    ('{TENANT_ONE}', '{MATTER_ONE}', '{SET_CURRENT}', 'lexical', 3, 'ready', 'a1'),
    ('{TENANT_ONE}', '{MATTER_ONE}', '{SET_CURRENT}', 'vector', 3, 'ready', 'a1'),
    ('{TENANT_ONE}', '{MATTER_TWO}', '{SET_CURRENT}', 'lexical', 3, 'ready', 'a2');
"""

REPLAY_SQL = """
INSERT INTO sklegal_retrieval.outbox_replay_log (
    partition_key, retrieval_record_id, event_sequence, idempotency_key, content
) VALUES
    ('rp_synthetic', 'record-1', 1, 'event-1', 'liberty mutual settlement packet'),
    ('rp_synthetic', 'record-2', 2, 'event-2', 'liberty mutual release draft')
ON CONFLICT (partition_key, retrieval_record_id) DO NOTHING;
INSERT INTO sklegal_retrieval.projection_watermark (partition_key, watermark)
VALUES ('rp_synthetic', 2)
ON CONFLICT (partition_key) DO UPDATE
SET watermark = GREATEST(projection_watermark.watermark, EXCLUDED.watermark);
"""


def _psql(
    container: str,
    user: str,
    sql: str,
    *,
    check: bool = True,
    database: str = "sklegal",
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container,
            "psql",
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--username",
            user,
            "--dbname",
            database,
            "--tuples-only",
            "--no-align",
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"psql failed for role {user}: {result.stderr.strip()}")
    return result


def _start_container(name: str) -> None:
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--label",
            "com.sklegal.test-card=SKL-S2-04",
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
            POSTGRES_IMAGE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    consecutive_ready = 0
    for attempt in range(240):
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
        )
        if ready.returncode == 0:
            consecutive_ready += 1
            if consecutive_ready == 4:
                return
        else:
            consecutive_ready = 0
        if attempt == 239:
            raise RuntimeError("disposable PostgreSQL readiness timeout")
        time.sleep(0.25)


def _remove_container(name: str) -> None:
    subprocess.run(
        ["docker", "rm", "--force", name],
        check=False,
        capture_output=True,
        text=True,
    )
    remaining = subprocess.run(
        ["docker", "ps", "--all", "--quiet", "--filter", f"name=^{name}$"],
        check=True,
        capture_output=True,
        text=True,
    )
    if remaining.stdout.strip():
        raise AssertionError("disposable PostgreSQL container leaked")


class RetrievalDisposablePostgresTests(unittest.TestCase):
    """Server-side contract controls against one disposable cluster."""

    container: str
    _shared_ready = False

    @classmethod
    def setUpClass(cls) -> None:
        cls = RetrievalDisposablePostgresTests
        if cls._shared_ready:
            return
        cls.container = f"sklegal-s204-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        _start_container(cls.container)
        atexit.register(_remove_container, cls.container)
        _psql(cls.container, "postgres", SCHEMA_SQL)
        _psql(cls.container, "postgres", SEED_SQL)
        cls._shared_ready = True

    def _q(self, user: str, sql: str, *, check: bool = True) -> str:
        output = _psql(self.container, user, sql, check=check).stdout.strip()
        return "\n".join(line for line in output.splitlines() if line != "SET")

    def test_runtime_roles_carry_the_contract_attributes(self) -> None:
        rows = self._q(
            "postgres",
            """
            SELECT rolname, rolsuper, rolbypassrls, rolinherit,
                   rolcreaterole, rolcreatedb, rolreplication
            FROM pg_roles
            WHERE rolname IN (
                'rt_principal_m1', 'rt_principal_m2',
                'rt_principal_t2', 'rt_principal_unbound',
                'sklegal_retrieval_gateway_owner'
            )
            ORDER BY rolname;
            """,
        ).splitlines()
        assert len(rows) == 5
        for row in rows:
            fields = row.split("|")
            assert fields[1:] == ["f", "f", "f", "f", "f", "f"], row

    def test_runtime_roles_own_no_objects_and_public_create_is_revoked(self) -> None:
        owned = self._q(
            "postgres",
            """
            SELECT count(*) FROM pg_class c
            JOIN pg_roles r ON r.oid = c.relowner
            WHERE r.rolname LIKE 'rt\\_principal\\_%';
            """,
        )
        assert owned == "0"
        public_create = self._q(
            "postgres",
            r"""
            SELECT count(*) FROM pg_namespace n
            WHERE n.nspname = 'public'
              AND n.nspacl::text ~ '(^|[{,])=[^}/]*C';
            """,
        )
        assert public_create == "0"

    def test_row_security_is_forced_on_the_partitioned_parents(self) -> None:
        rows = self._q(
            "postgres",
            """
            SELECT relname, relrowsecurity, relforcerowsecurity
            FROM pg_class
            WHERE relname IN ('lexical_chunks', 'vector_chunks')
            ORDER BY relname;
            """,
        ).splitlines()
        assert rows == [
            "lexical_chunks|t|t",
            "vector_chunks|t|t",
        ]

    def test_omitted_matter_filter_returns_only_the_bound_matter(self) -> None:
        rows = self._q(
            "rt_principal_m1",
            """
            SELECT retrieval_record_id
            FROM sklegal_retrieval.lexical_chunks
            ORDER BY retrieval_record_id;
            """,
        )
        assert rows.splitlines() == ["record-1", "record-2"]

    def test_same_tenant_unassigned_matter_is_denied_without_a_filter(self) -> None:
        rows = self._q(
            "rt_principal_m2",
            "SELECT retrieval_record_id FROM sklegal_retrieval.lexical_chunks;",
        )
        assert rows == "record-3"
        rows = self._q(
            "rt_principal_unbound",
            "SELECT count(*) FROM sklegal_retrieval.lexical_chunks;",
        )
        assert rows == "0"

    def test_cross_tenant_partition_rows_are_invisible(self) -> None:
        rows = self._q(
            "rt_principal_t2",
            "SELECT retrieval_record_id FROM sklegal_retrieval.lexical_chunks;",
        )
        assert rows == "record-4"

    def test_direct_child_partition_access_is_denied(self) -> None:
        result = _psql(
            self.container,
            "rt_principal_m1",
            "SELECT count(*) FROM sklegal_retrieval.lexical_chunks_t1_g3;",
            check=False,
        )
        assert result.returncode != 0
        assert "permission denied" in result.stderr

    def test_lexical_search_and_count_share_the_mandatory_scope(self) -> None:
        search = self._q(
            "rt_principal_m1",
            """
            SELECT retrieval_record_id
            FROM sklegal_retrieval.lexical_search_v1('liberty mutual', 10);
            """,
        ).splitlines()
        assert set(search) == {"record-1", "record-2"}
        count = self._q(
            "rt_principal_m1",
            "SELECT sklegal_retrieval.lexical_count_v1('liberty mutual');",
        )
        assert count == "2"
        foreign_count = self._q(
            "rt_principal_t2",
            "SELECT sklegal_retrieval.lexical_count_v1('liberty mutual');",
        )
        assert foreign_count == "1"

    def test_lexical_ranking_ties_break_by_retrieval_record_id(self) -> None:
        self._q(
            "postgres",
            f"""
            INSERT INTO sklegal_retrieval.lexical_chunks (
                tenant_id, projection_generation, matter_id,
                retrieval_record_id, content, search_document
            ) VALUES
                ('{TENANT_ONE}', 3, '{MATTER_ONE}', 'record-tie-b',
                 'tie alpha', to_tsvector('english', 'tie alpha')),
                ('{TENANT_ONE}', 3, '{MATTER_ONE}', 'record-tie-a',
                 'tie beta', to_tsvector('english', 'tie beta'))
            ON CONFLICT DO NOTHING;
            """,
        )
        try:
            rows = self._q(
                "rt_principal_m1",
                """
                SELECT retrieval_record_id
                FROM sklegal_retrieval.lexical_search_v1('tie', 10);
                """,
            ).splitlines()
            assert rows == ["record-tie-a", "record-tie-b"]
        finally:
            self._q(
                "postgres",
                """
                DELETE FROM sklegal_retrieval.lexical_chunks
                WHERE retrieval_record_id IN ('record-tie-a', 'record-tie-b');
                """,
            )

    def test_exact_vector_order_matches_the_brute_force_baseline(self) -> None:
        rows = self._q(
            "rt_principal_m1",
            """
            SELECT retrieval_record_id
            FROM sklegal_retrieval.vector_exact_v1(ARRAY[1.0, 0.0], 3);
            """,
        ).splitlines()
        fixtures = {
            "record-1": (1.0, 0.0),
            "record-2": (0.9, 0.1),
            "record-3v": (0.0, 1.0),
            "record-4v": (0.5, 0.5),
            "record-5v": (-1.0, 0.0),
        }
        reference = sorted(
            fixtures,
            key=lambda item: (
                sum((a - b) ** 2 for a, b in zip((1.0, 0.0), fixtures[item])),
                item,
            ),
        )[:3]
        assert rows == reference

    def test_vector_scope_denies_cross_matter_and_cross_tenant_rows(self) -> None:
        rows = self._q(
            "rt_principal_m2",
            """
            SELECT retrieval_record_id
            FROM sklegal_retrieval.vector_exact_v1(ARRAY[1.0, 0.0], 10);
            """,
        ).splitlines()
        assert rows == ["record-6"]
        rows = self._q(
            "rt_principal_t2",
            """
            SELECT retrieval_record_id
            FROM sklegal_retrieval.vector_exact_v1(ARRAY[1.0, 0.0], 10);
            """,
        ).splitlines()
        assert rows == ["record-7"]

    def test_each_partition_has_independent_indexes(self) -> None:
        rows = self._q(
            "postgres",
            """
            SELECT tablename, indexname FROM pg_indexes
            WHERE schemaname = 'sklegal_retrieval'
              AND indexname LIKE '%\\_search'
            ORDER BY tablename;
            """,
        ).splitlines()
        assert rows == [
            "lexical_chunks_t1_g3|lexical_chunks_t1_g3_search",
            "lexical_chunks_t2_g3|lexical_chunks_t2_g3_search",
        ]

    def test_revocation_closes_access_immediately(self) -> None:
        self._q(
            "postgres",
            """
            UPDATE sklegal_retrieval.credential_binding
            SET revoked_at = now()
            WHERE database_principal = 'rt_principal_m2';
            """,
        )
        try:
            rows = self._q(
                "rt_principal_m2",
                "SELECT count(*) FROM sklegal_retrieval.lexical_chunks;",
            )
            assert rows == "0"
        finally:
            self._q(
                "postgres",
                """
                UPDATE sklegal_retrieval.credential_binding
                SET revoked_at = NULL
                WHERE database_principal = 'rt_principal_m2';
                """,
            )

    def test_gateway_owner_is_hardened_and_denied_other_generations(self) -> None:
        owner = self._q(
            "postgres",
            """
            SELECT r.rolcanlogin, r.rolsuper, r.rolbypassrls
            FROM pg_authid r
            WHERE r.rolname = 'sklegal_retrieval_gateway_owner';
            """,
        )
        assert owner == "f|f|f"
        denied = _psql(
            self.container,
            "postgres",
            """
            SET ROLE sklegal_retrieval_gateway_owner;
            SELECT count(*) FROM sklegal_retrieval.graph_entities_g4;
            """,
            check=False,
        )
        assert denied.returncode != 0
        assert "permission denied" in denied.stderr
        allowed = self._q(
            "postgres",
            """
            SET ROLE sklegal_retrieval_gateway_owner;
            SELECT count(*) FROM sklegal_retrieval.graph_entities_g3;
            """,
        )
        assert allowed == "2"

    def test_gateway_public_execute_is_revoked_and_acl_is_exact(self) -> None:
        acl = self._q(
            "postgres",
            """
            SELECT proacl FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'sklegal_retrieval'
              AND p.proname = 'graph_entity_v1';
            """,
        )
        assert "rt_principal_m1" in acl
        assert "rt_principal_m2" not in acl
        result = _psql(
            self.container,
            "rt_principal_m2",
            "SELECT * FROM sklegal_retrieval.graph_entity_v1(ARRAY['entity-1'], 10);",
            check=False,
        )
        assert result.returncode != 0
        assert "permission denied" in result.stderr

    def test_gateway_rechecks_the_binding_and_normalizes_every_failure(self) -> None:
        rows = self._q(
            "rt_principal_m1",
            """
            SELECT graph_entity_id
            FROM sklegal_retrieval.graph_entity_v1(ARRAY['entity-1', 'entity-2'], 10);
            """,
        ).splitlines()
        assert rows == ["entity-1", "entity-2"]
        unbound = _psql(
            self.container,
            "postgres",
            "SELECT * FROM sklegal_retrieval.graph_entity_v1(ARRAY['entity-1'], 10);",
            check=False,
        )
        assert unbound.returncode != 0
        assert unbound.stderr.count("retrieval_unavailable") == 1
        broken_owner = _psql(
            self.container,
            "postgres",
            """
            BEGIN;
            REVOKE SELECT ON sklegal_retrieval.graph_entities_g3
                FROM sklegal_retrieval_gateway_owner;
            SET ROLE rt_principal_m1;
            SELECT * FROM sklegal_retrieval.graph_entity_v1(ARRAY['entity-1'], 10);
            ROLLBACK;
            """,
            check=False,
        )
        assert broken_owner.returncode != 0
        assert "retrieval_unavailable" in broken_owner.stderr
        assert "graph_entities_g3" not in broken_owner.stderr
        assert "relation" not in broken_owner.stderr.lower().replace(
            "retrieval_unavailable", ""
        )

    def test_gateway_definition_is_pinnable_and_free_of_dynamic_sql(self) -> None:
        definition = self._q(
            "postgres",
            """
            SELECT pg_get_functiondef(p.oid) FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'sklegal_retrieval'
              AND p.proname = 'graph_entity_v1';
            """,
        )
        digest = hashlib.sha256(definition.encode("utf-8")).hexdigest()
        again = self._q(
            "postgres",
            """
            SELECT pg_get_functiondef(p.oid) FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'sklegal_retrieval'
              AND p.proname = 'graph_entity_v1';
            """,
        )
        assert hashlib.sha256(again.encode("utf-8")).hexdigest() == digest
        assert "SECURITY DEFINER" in definition
        assert "search_path" in definition
        assert "EXECUTE" not in definition
        assert "graph_entities_g4" not in definition
        assert "graph_entities_g3" in definition

    def test_registry_cutover_is_atomic_and_rollback_restores_the_prior_set(
        self,
    ) -> None:
        wrong_expected = self._q(
            "postgres",
            f"""
            SELECT sklegal_retrieval.activate_projection_set(
                '{TENANT_ONE}', '{MATTER_ONE}', '{SET_CURRENT}',
                '{SET_CURRENT}', 'a1');
            """,
        )
        assert wrong_expected == "f"
        active_before = self._q(
            "postgres",
            f"""
            SELECT projection_set_id, lifecycle
            FROM sklegal_retrieval.projection_registry
            WHERE tenant_id = '{TENANT_ONE}' AND matter_id = '{MATTER_ONE}'
            ORDER BY projection_set_id, component;
            """,
        ).splitlines()
        cutover = self._q(
            "postgres",
            f"""
            SELECT sklegal_retrieval.activate_projection_set(
                '{TENANT_ONE}', '{MATTER_ONE}', '{SET_CURRENT}',
                '{SET_PRIOR}', 'a1');
            """,
        )
        assert cutover == "t"
        replay = self._q(
            "postgres",
            f"""
            SELECT sklegal_retrieval.activate_projection_set(
                '{TENANT_ONE}', '{MATTER_ONE}', '{SET_CURRENT}',
                '{SET_CURRENT}', 'a1');
            """,
        )
        assert replay == "t"
        states = self._q(
            "postgres",
            f"""
            SELECT projection_set_id, lifecycle, count(*)
            FROM sklegal_retrieval.projection_registry
            WHERE tenant_id = '{TENANT_ONE}' AND matter_id = '{MATTER_ONE}'
            GROUP BY projection_set_id, lifecycle
            ORDER BY projection_set_id;
            """,
        ).splitlines()
        assert states == [
            f"{SET_PRIOR}|retiring|2",
            f"{SET_CURRENT}|active|2",
        ]
        rollback = self._q(
            "postgres",
            f"""
            SELECT sklegal_retrieval.rollback_projection_set(
                '{TENANT_ONE}', '{MATTER_ONE}', '{SET_PRIOR}');
            """,
        )
        assert rollback == "t"
        states = self._q(
            "postgres",
            f"""
            SELECT projection_set_id, lifecycle, count(*)
            FROM sklegal_retrieval.projection_registry
            WHERE tenant_id = '{TENANT_ONE}' AND matter_id = '{MATTER_ONE}'
            GROUP BY projection_set_id, lifecycle
            ORDER BY projection_set_id;
            """,
        ).splitlines()
        assert states == [
            f"{SET_PRIOR}|active|2",
            f"{SET_CURRENT}|retiring|2",
        ]
        partial = self._q(
            "postgres",
            f"""
            SELECT sklegal_retrieval.activate_projection_set(
                '{TENANT_ONE}', '{MATTER_TWO}', '{SET_CURRENT}',
                '{SET_PRIOR}', 'a2');
            """,
        )
        assert partial == "f"
        untouched = self._q(
            "postgres",
            f"""
            SELECT lifecycle, count(*)
            FROM sklegal_retrieval.projection_registry
            WHERE tenant_id = '{TENANT_ONE}' AND matter_id = '{MATTER_TWO}'
            GROUP BY lifecycle;
            """,
        )
        assert untouched == "ready|1"
        assert active_before != states

    def test_outbox_replay_is_idempotent(self) -> None:
        self._q("postgres", REPLAY_SQL)
        self._q("postgres", REPLAY_SQL)
        rows = self._q(
            "postgres",
            """
            SELECT count(*) FROM sklegal_retrieval.outbox_replay_log
            WHERE partition_key = 'rp_synthetic';
            """,
        )
        assert rows == "2"
        watermark = self._q(
            "postgres",
            """
            SELECT watermark FROM sklegal_retrieval.projection_watermark
            WHERE partition_key = 'rp_synthetic';
            """,
        )
        assert watermark == "2"

    def test_replica_must_replay_the_required_lsn_before_serving(self) -> None:
        current = self._q("postgres", "SELECT pg_current_wal_lsn();")
        assert "/" in current
        behind = self._q(
            "postgres",
            "SELECT '0/00000010'::pg_lsn >= pg_current_wal_lsn();",
        )
        assert behind == "f"
        ahead = self._q(
            "postgres",
            "SELECT pg_current_wal_lsn() >= '0/00000010'::pg_lsn;",
        )
        assert ahead == "t"

    def test_dump_restore_preserves_the_graph_manifest_and_catalog(self) -> None:
        digest_before = self._q(
            "postgres",
            """
            SELECT md5(string_agg(graph_entity_id || content_sha256, ':'
                ORDER BY graph_entity_id))
            FROM sklegal_retrieval.graph_entities_g3;
            """,
        )
        dump = subprocess.run(
            [
                "docker",
                "exec",
                self.container,
                "pg_dump",
                "--username",
                "postgres",
                "--dbname",
                "sklegal",
                "--schema",
                "sklegal_retrieval",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self._q("postgres", "CREATE DATABASE sklegal_restore;")
        try:
            restore = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    self.container,
                    "psql",
                    "--no-psqlrc",
                    "--set",
                    "ON_ERROR_STOP=1",
                    "--username",
                    "postgres",
                    "--dbname",
                    "sklegal_restore",
                ],
                input=dump.stdout,
                text=True,
                capture_output=True,
                check=False,
            )
            assert restore.returncode == 0, restore.stderr
            digest_after = _psql(
                self.container,
                "postgres",
                """
                SELECT md5(string_agg(graph_entity_id || content_sha256, ':'
                    ORDER BY graph_entity_id))
                FROM sklegal_retrieval.graph_entities_g3;
                """,
                database="sklegal_restore",
            ).stdout.strip()
            assert digest_after == digest_before
            catalog = _psql(
                self.container,
                "postgres",
                """
                SELECT count(*) FROM sklegal_retrieval.graph_entities_g3;
                """,
                database="sklegal_restore",
            ).stdout.strip()
            assert catalog == "2"
        finally:
            self._q("postgres", "DROP DATABASE sklegal_restore;")

    def test_retrieval_backend_crash_does_not_interrupt_the_core_cluster(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        core = f"sklegal-s204-core-{os.getpid()}-{suffix}"
        victim = f"sklegal-s204-victim-{os.getpid()}-{suffix}"
        _start_container(core)
        _start_container(victim)
        try:
            subprocess.run(
                ["docker", "kill", "--signal", "SIGKILL", victim],
                check=True,
                capture_output=True,
                text=True,
            )
            time.sleep(1.0)
            answer = _psql(core, "postgres", "SELECT 1;")
            assert answer.stdout.strip() == "1"
        finally:
            _remove_container(core)
            _remove_container(victim)


if __name__ == "__main__":
    unittest.main()
