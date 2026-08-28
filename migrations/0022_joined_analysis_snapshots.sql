-- sklegal:up
CREATE TABLE sklegal_legal.joined_analysis_snapshots (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    version sklegal_legal.record_version NOT NULL,
    observed_at timestamptz NOT NULL,
    matter_snapshot_sha256 sklegal_legal.sha256_digest NOT NULL,
    claim_projection_revision sklegal_legal.sha256_digest NOT NULL,
    authority_snapshot sklegal_legal.sha256_digest NOT NULL,
    projection_revision sklegal_legal.sha256_digest NOT NULL,
    projection_sha256 sklegal_legal.sha256_digest NOT NULL,
    projection jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, snapshot_id, version),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    CHECK (observed_at <= created_at),
    CHECK (jsonb_typeof(projection) = 'object'),
    CHECK (projection->>'schema_revision' = 'sklegal-matter-analysis/v1'),
    CHECK ((projection->>'tenant_id')::uuid = tenant_id),
    CHECK ((projection->>'matter_id')::uuid = matter_id),
    CHECK ((projection#>>'{snapshot,snapshot_id}')::uuid = snapshot_id),
    CHECK ((projection#>>'{snapshot,version}')::bigint = version),
    CHECK ((projection#>>'{snapshot,matter_snapshot_sha256}') = matter_snapshot_sha256),
    CHECK ((projection#>>'{snapshot,claim_projection_revision}') = claim_projection_revision),
    CHECK ((projection#>>'{snapshot,authority_snapshot}') = authority_snapshot),
    CHECK ((projection#>>'{snapshot,projection_revision}') = projection_revision),
    CHECK ((projection#>>'{snapshot,projection_sha256}') = projection_sha256)
);

CREATE TRIGGER joined_analysis_snapshot_domain_id_non_nil
BEFORE INSERT OR UPDATE ON sklegal_legal.joined_analysis_snapshots
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_nil_domain_ids();

CREATE TRIGGER joined_analysis_snapshot_append_only
BEFORE UPDATE OR DELETE ON sklegal_legal.joined_analysis_snapshots
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

ALTER TABLE sklegal_legal.joined_analysis_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.joined_analysis_snapshots FORCE ROW LEVEL SECURITY;

CREATE POLICY joined_analysis_snapshot_select
ON sklegal_legal.joined_analysis_snapshots
FOR SELECT USING (
    sklegal_identity.record_is_authorized(tenant_id, matter_id)
);

CREATE POLICY joined_analysis_snapshot_insert
ON sklegal_legal.joined_analysis_snapshots
FOR INSERT WITH CHECK (
    sklegal_identity.record_is_authorized(tenant_id, matter_id)
);

CREATE VIEW sklegal_legal.joined_analysis_snapshot_current
WITH (security_invoker = true, security_barrier = true)
AS
SELECT DISTINCT ON (tenant_id, matter_id)
    tenant_id,
    matter_id,
    snapshot_id,
    version,
    observed_at,
    matter_snapshot_sha256,
    claim_projection_revision,
    authority_snapshot,
    projection_revision,
    projection_sha256,
    projection,
    created_at
FROM sklegal_legal.joined_analysis_snapshots
ORDER BY tenant_id, matter_id, observed_at DESC, snapshot_id DESC, version DESC;

-- sklegal:down
DROP VIEW sklegal_legal.joined_analysis_snapshot_current;
DROP TABLE sklegal_legal.joined_analysis_snapshots;
