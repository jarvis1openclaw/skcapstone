-- sklegal:up
CREATE SCHEMA sklegal_governed_corpus;

CREATE TABLE sklegal_governed_corpus.projection_registry (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    projection_generation bigint NOT NULL CHECK (projection_generation >= 1),
    release_id text NOT NULL CHECK (length(btrim(release_id)) BETWEEN 1 AND 200),
    core_watermark bigint NOT NULL CHECK (core_watermark >= 0),
    policy_revision sklegal_legal.sha256_digest NOT NULL,
    rights_revision sklegal_legal.sha256_digest NOT NULL,
    record jsonb NOT NULL CHECK (jsonb_typeof(record) = 'object'),
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, projection_generation),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id)
);

CREATE TABLE sklegal_governed_corpus.source_versions (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    source_id text NOT NULL CHECK (length(btrim(source_id)) BETWEEN 1 AND 200),
    source_version_id uuid NOT NULL,
    source_version text NOT NULL
        CHECK (length(btrim(source_version)) BETWEEN 1 AND 200),
    release_id text NOT NULL CHECK (length(btrim(release_id)) BETWEEN 1 AND 200),
    projection_generation bigint NOT NULL CHECK (projection_generation >= 1),
    classification smallint NOT NULL CHECK (classification BETWEEN 0 AND 3),
    rights_revision sklegal_legal.sha256_digest NOT NULL,
    permitted_principal_ids uuid[] NOT NULL
        CHECK (cardinality(permitted_principal_ids) >= 1),
    source_sha256 sklegal_legal.sha256_digest NOT NULL,
    chunk_sha256 sklegal_legal.sha256_digest NOT NULL,
    exact_span text NOT NULL CHECK (length(exact_span) BETWEEN 1 AND 16384),
    supersedes_source_version_id uuid,
    record jsonb NOT NULL CHECK (jsonb_typeof(record) = 'object'),
    record_sha256 sklegal_legal.sha256_digest NOT NULL,
    authorization_decision_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, source_version_id),
    UNIQUE (tenant_id, matter_id, source_id, source_version),
    UNIQUE (tenant_id, matter_id, supersedes_source_version_id),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, matter_id, supersedes_source_version_id)
        REFERENCES sklegal_governed_corpus.source_versions(
            tenant_id, matter_id, source_version_id
        )
);

CREATE TABLE sklegal_governed_corpus.idempotency_receipts (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    idempotency_key_sha256 sklegal_legal.sha256_digest NOT NULL,
    request_sha256 sklegal_legal.sha256_digest NOT NULL,
    response_record jsonb NOT NULL CHECK (jsonb_typeof(response_record) = 'object'),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, idempotency_key_sha256),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id)
);

CREATE TABLE sklegal_governed_corpus.audit_events (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    audit_id uuid NOT NULL,
    source_version_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN (
        'corpus.source.recorded', 'corpus.source.superseded'
    )),
    actor_principal_id uuid NOT NULL,
    authorization_decision_id uuid NOT NULL,
    policy_decision_id uuid NOT NULL,
    policy_revision sklegal_legal.sha256_digest NOT NULL,
    request_sha256 sklegal_legal.sha256_digest NOT NULL,
    source_sha256 sklegal_legal.sha256_digest NOT NULL,
    correlation_id uuid NOT NULL,
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, audit_id),
    FOREIGN KEY (tenant_id, matter_id, source_version_id)
        REFERENCES sklegal_governed_corpus.source_versions(
            tenant_id, matter_id, source_version_id
        ),
    FOREIGN KEY (tenant_id, actor_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id)
);

CREATE TABLE sklegal_governed_corpus.outbox (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    outbox_id uuid NOT NULL,
    audit_id uuid NOT NULL,
    source_version_id uuid NOT NULL,
    topic text NOT NULL CHECK (topic = 'corpus.projection.requested'),
    payload_sha256 sklegal_legal.sha256_digest NOT NULL,
    qdrant_dispatch_allowed boolean NOT NULL CHECK (NOT qdrant_dispatch_allowed),
    falkordb_dispatch_allowed boolean NOT NULL CHECK (NOT falkordb_dispatch_allowed),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, outbox_id),
    FOREIGN KEY (tenant_id, matter_id, audit_id)
        REFERENCES sklegal_governed_corpus.audit_events(tenant_id, matter_id, audit_id),
    FOREIGN KEY (tenant_id, matter_id, source_version_id)
        REFERENCES sklegal_governed_corpus.source_versions(
            tenant_id, matter_id, source_version_id
        )
);

CREATE INDEX governed_corpus_source_scope_idx
ON sklegal_governed_corpus.source_versions (
    tenant_id, matter_id, release_id, projection_generation, source_id
);

CREATE FUNCTION sklegal_governed_corpus.current_source_v1(
    requested_tenant_id uuid,
    requested_matter_id uuid,
    requested_principal_id uuid,
    requested_source_id text,
    classification_ceiling integer,
    requested_rights_revision text,
    requested_release_id text,
    requested_projection_generation integer
) RETURNS TABLE (record jsonb)
LANGUAGE sql STABLE SECURITY INVOKER
SET search_path = sklegal_governed_corpus, pg_catalog
AS $$
    SELECT candidate.record
    FROM sklegal_governed_corpus.source_versions candidate
    WHERE candidate.tenant_id = requested_tenant_id
      AND candidate.matter_id = requested_matter_id
      AND candidate.source_id = requested_source_id
      AND requested_principal_id = ANY(candidate.permitted_principal_ids)
      AND candidate.classification <= classification_ceiling
      AND candidate.rights_revision = requested_rights_revision
      AND candidate.release_id = requested_release_id
      AND candidate.projection_generation = requested_projection_generation
      AND NOT EXISTS (
          SELECT 1 FROM sklegal_governed_corpus.source_versions successor
          WHERE successor.tenant_id = candidate.tenant_id
            AND successor.matter_id = candidate.matter_id
            AND successor.supersedes_source_version_id = candidate.source_version_id
      )
    LIMIT 1
$$;

REVOKE ALL ON FUNCTION
    sklegal_governed_corpus.current_source_v1(
        uuid, uuid, uuid, text, integer, text, text, integer
    )
FROM PUBLIC;

CREATE TRIGGER governed_corpus_registry_append_only
BEFORE UPDATE OR DELETE ON sklegal_governed_corpus.projection_registry
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER governed_corpus_source_append_only
BEFORE UPDATE OR DELETE ON sklegal_governed_corpus.source_versions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER governed_corpus_idempotency_append_only
BEFORE UPDATE OR DELETE ON sklegal_governed_corpus.idempotency_receipts
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER governed_corpus_audit_append_only
BEFORE UPDATE OR DELETE ON sklegal_governed_corpus.audit_events
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER governed_corpus_outbox_append_only
BEFORE UPDATE OR DELETE ON sklegal_governed_corpus.outbox
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

ALTER TABLE sklegal_governed_corpus.projection_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_governed_corpus.projection_registry FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_governed_corpus.source_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_governed_corpus.source_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_governed_corpus.idempotency_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_governed_corpus.idempotency_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_governed_corpus.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_governed_corpus.audit_events FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_governed_corpus.outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_governed_corpus.outbox FORCE ROW LEVEL SECURITY;

CREATE POLICY governed_corpus_registry_scope
ON sklegal_governed_corpus.projection_registry
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY governed_corpus_source_scope
ON sklegal_governed_corpus.source_versions
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY governed_corpus_idempotency_scope
ON sklegal_governed_corpus.idempotency_receipts
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY governed_corpus_audit_scope
ON sklegal_governed_corpus.audit_events
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY governed_corpus_outbox_scope
ON sklegal_governed_corpus.outbox
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));

REVOKE ALL ON SCHEMA sklegal_governed_corpus FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA sklegal_governed_corpus FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA sklegal_governed_corpus FROM PUBLIC;

-- sklegal:down
DROP FUNCTION sklegal_governed_corpus.current_source_v1(
    uuid, uuid, uuid, text, integer, text, text, integer
);
DROP INDEX sklegal_governed_corpus.governed_corpus_source_scope_idx;
DROP TABLE sklegal_governed_corpus.outbox;
DROP TABLE sklegal_governed_corpus.audit_events;
DROP TABLE sklegal_governed_corpus.idempotency_receipts;
DROP TABLE sklegal_governed_corpus.source_versions;
DROP TABLE sklegal_governed_corpus.projection_registry;
DROP SCHEMA sklegal_governed_corpus;
