-- sklegal:up
CREATE SCHEMA sklegal_governed_corpus;
CREATE EXTENSION vector WITH SCHEMA public VERSION '0.8.0';

CREATE TABLE sklegal_governed_corpus.projection_state (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    projection_generation bigint NOT NULL CHECK (projection_generation >= 1),
    release_id text NOT NULL CHECK (length(btrim(release_id)) BETWEEN 1 AND 200),
    backend_watermark bigint NOT NULL CHECK (backend_watermark >= 0),
    core_watermark bigint NOT NULL CHECK (core_watermark >= backend_watermark),
    record jsonb NOT NULL CHECK (jsonb_typeof(record) = 'object'),
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, projection_generation)
);

CREATE TABLE sklegal_governed_corpus.source_projections (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    source_id text NOT NULL CHECK (length(btrim(source_id)) BETWEEN 1 AND 200),
    source_version_id uuid NOT NULL,
    source_version text NOT NULL
        CHECK (length(btrim(source_version)) BETWEEN 1 AND 200),
    release_id text NOT NULL CHECK (length(btrim(release_id)) BETWEEN 1 AND 200),
    projection_generation bigint NOT NULL CHECK (projection_generation >= 1),
    classification smallint NOT NULL CHECK (classification BETWEEN 0 AND 3),
    rights_revision text NOT NULL CHECK (rights_revision ~ '^[0-9a-f]{64}$'),
    permitted_principal_ids uuid[] NOT NULL
        CHECK (cardinality(permitted_principal_ids) >= 1),
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    chunk_sha256 text NOT NULL CHECK (chunk_sha256 ~ '^[0-9a-f]{64}$'),
    exact_span text NOT NULL CHECK (length(exact_span) BETWEEN 1 AND 16384),
    search_document tsvector NOT NULL,
    embedding public.vector NOT NULL
        CHECK (public.vector_dims(embedding) BETWEEN 1 AND 4096),
    supersedes_source_version_id uuid,
    record jsonb NOT NULL CHECK (jsonb_typeof(record) = 'object'),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, source_version_id),
    UNIQUE (tenant_id, matter_id, source_id, source_version),
    UNIQUE (tenant_id, matter_id, supersedes_source_version_id)
);

CREATE TABLE sklegal_governed_corpus.projection_commands (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    command_id uuid NOT NULL,
    source_version_id uuid NOT NULL,
    operation text NOT NULL CHECK (
        operation IN ('create', 'correct', 'supersede', 'revoke')
    ),
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    projected_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, command_id)
);

CREATE INDEX governed_corpus_full_text_idx
ON sklegal_governed_corpus.source_projections USING gin (search_document);
CREATE INDEX governed_corpus_scope_idx
ON sklegal_governed_corpus.source_projections (
    tenant_id, matter_id, release_id, projection_generation, source_id
);
CREATE INDEX governed_corpus_vector_hnsw_idx
ON sklegal_governed_corpus.source_projections
USING hnsw ((embedding::public.vector(3)) public.vector_cosine_ops)
WHERE public.vector_dims(embedding) = 3;

CREATE FUNCTION sklegal_governed_corpus.vector_exact_distance_v1(
    left_embedding public.vector, right_embedding public.vector
) RETURNS double precision
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog
AS $$
    SELECT left_embedding OPERATOR(public.<=>) right_embedding
$$;

REVOKE ALL ON FUNCTION
    sklegal_governed_corpus.vector_exact_distance_v1(
        public.vector, public.vector
    )
FROM PUBLIC;

CREATE FUNCTION sklegal_governed_corpus.search_v1(
    requested_tenant_id uuid,
    requested_matter_id uuid,
    requested_principal_id uuid,
    query_text text,
    query_embedding public.vector,
    rank_mode text,
    requested_release_id text,
    requested_projection_generation integer,
    classification_ceiling integer,
    requested_rights_revision text,
    snapshot_at timestamptz,
    after_score double precision,
    after_source_version_id uuid,
    fetch_limit integer
) RETURNS TABLE (
    record jsonb,
    score double precision,
    full_text_rank double precision,
    vector_distance double precision
)
LANGUAGE sql STABLE SECURITY INVOKER
SET search_path = sklegal_governed_corpus, pg_catalog
AS $$
    WITH visible AS (
        SELECT candidate.*,
               ts_rank_cd(
                   candidate.search_document,
                   websearch_to_tsquery('english', query_text)
               )::double precision AS lexical_score,
               sklegal_governed_corpus.vector_exact_distance_v1(
                   candidate.embedding, query_embedding
               ) AS exact_distance
        FROM sklegal_governed_corpus.source_projections candidate
        WHERE candidate.tenant_id = requested_tenant_id
          AND candidate.matter_id = requested_matter_id
          AND candidate.recorded_at <= snapshot_at
          AND requested_principal_id = ANY(candidate.permitted_principal_ids)
          AND candidate.classification <= classification_ceiling
          AND candidate.rights_revision = requested_rights_revision
          AND candidate.release_id = requested_release_id
          AND candidate.projection_generation = requested_projection_generation
          AND NOT EXISTS (
              SELECT 1 FROM sklegal_governed_corpus.source_projections successor
              WHERE successor.tenant_id = candidate.tenant_id
                AND successor.matter_id = candidate.matter_id
                AND successor.recorded_at <= snapshot_at
                AND successor.supersedes_source_version_id
                    = candidate.source_version_id
          )
    ), ranked AS (
        SELECT visible.*,
               row_number() OVER (
                   ORDER BY lexical_score DESC, source_version_id
               ) AS lexical_position,
               row_number() OVER (
                   ORDER BY exact_distance ASC NULLS LAST, source_version_id
               ) AS vector_position
        FROM visible
    ), scored AS (
        SELECT ranked.*,
               CASE rank_mode
                   WHEN 'full_text' THEN lexical_score
                   WHEN 'vector_exact' THEN 1.0 / (1.0 + exact_distance)
                   WHEN 'hybrid_rrf' THEN
                       1.0 / (60.0 + lexical_position)
                       + 1.0 / (60.0 + vector_position)
                   ELSE NULL
               END AS final_score
        FROM ranked
        WHERE rank_mode IN ('full_text', 'vector_exact', 'hybrid_rrf')
          AND (rank_mode <> 'full_text' OR lexical_score > 0)
          AND (rank_mode = 'full_text' OR exact_distance IS NOT NULL)
    )
    SELECT scored.record,
           scored.final_score,
           scored.lexical_score,
           scored.exact_distance
    FROM scored
    WHERE fetch_limit BETWEEN 1 AND 51
      AND length(btrim(query_text)) BETWEEN 1 AND 16384
      AND (
          (after_score IS NULL AND after_source_version_id IS NULL)
          OR (
              after_score IS NOT NULL
              AND after_source_version_id IS NOT NULL
              AND (
                  scored.final_score < after_score
                  OR (
                      scored.final_score = after_score
                      AND scored.source_version_id > after_source_version_id
                  )
              )
          )
      )
    ORDER BY scored.final_score DESC, scored.source_version_id
    LIMIT LEAST(fetch_limit, 51)
$$;

REVOKE ALL ON FUNCTION
    sklegal_governed_corpus.search_v1(
        uuid, uuid, uuid, text, public.vector, text, text, integer,
        integer, text, timestamptz, double precision, uuid, integer
    )
FROM PUBLIC;

ALTER TABLE sklegal_governed_corpus.projection_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_governed_corpus.projection_state FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_governed_corpus.source_projections ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_governed_corpus.source_projections FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_governed_corpus.projection_commands ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_governed_corpus.projection_commands FORCE ROW LEVEL SECURITY;

CREATE POLICY governed_corpus_projection_state_scope
ON sklegal_governed_corpus.projection_state
USING (
    tenant_id = nullif(current_setting('sklegal.tenant_id', true), '')::uuid
    AND matter_id = nullif(current_setting('sklegal.matter_id', true), '')::uuid
)
WITH CHECK (
    tenant_id = nullif(current_setting('sklegal.tenant_id', true), '')::uuid
    AND matter_id = nullif(current_setting('sklegal.matter_id', true), '')::uuid
);
CREATE POLICY governed_corpus_source_projection_scope
ON sklegal_governed_corpus.source_projections
USING (
    tenant_id = nullif(current_setting('sklegal.tenant_id', true), '')::uuid
    AND matter_id = nullif(current_setting('sklegal.matter_id', true), '')::uuid
    AND nullif(current_setting('sklegal.principal_id', true), '')::uuid
        = ANY(permitted_principal_ids)
)
WITH CHECK (
    tenant_id = nullif(current_setting('sklegal.tenant_id', true), '')::uuid
    AND matter_id = nullif(current_setting('sklegal.matter_id', true), '')::uuid
);
CREATE POLICY governed_corpus_projection_command_scope
ON sklegal_governed_corpus.projection_commands
USING (
    tenant_id = nullif(current_setting('sklegal.tenant_id', true), '')::uuid
    AND matter_id = nullif(current_setting('sklegal.matter_id', true), '')::uuid
)
WITH CHECK (
    tenant_id = nullif(current_setting('sklegal.tenant_id', true), '')::uuid
    AND matter_id = nullif(current_setting('sklegal.matter_id', true), '')::uuid
);

REVOKE ALL ON SCHEMA sklegal_governed_corpus FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA sklegal_governed_corpus FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA sklegal_governed_corpus FROM PUBLIC;

-- Qdrant and FalkorDB remain metadata-only compatibility targets. This
-- migration grants them no connector, credential, function or dispatch path.

-- sklegal:down
DROP FUNCTION sklegal_governed_corpus.search_v1(
    uuid, uuid, uuid, text, public.vector, text, text, integer,
    integer, text, timestamptz, double precision, uuid, integer
);
DROP FUNCTION sklegal_governed_corpus.vector_exact_distance_v1(
    public.vector, public.vector
);
DROP INDEX sklegal_governed_corpus.governed_corpus_vector_hnsw_idx;
DROP INDEX sklegal_governed_corpus.governed_corpus_scope_idx;
DROP INDEX sklegal_governed_corpus.governed_corpus_full_text_idx;
DROP TABLE sklegal_governed_corpus.projection_commands;
DROP TABLE sklegal_governed_corpus.source_projections;
DROP TABLE sklegal_governed_corpus.projection_state;
DROP SCHEMA sklegal_governed_corpus;
DROP EXTENSION vector;
