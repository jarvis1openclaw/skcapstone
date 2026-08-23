-- sklegal:up
CREATE TABLE sklegal_legal.sentence_groundings (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    work_product_version_id uuid NOT NULL,
    work_product_version_number sklegal_legal.record_version NOT NULL,
    work_product_content_sha256 sklegal_legal.sha256_digest NOT NULL,
    sentence_key sklegal_legal.sha256_digest NOT NULL,
    claim_id uuid NOT NULL,
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (
        tenant_id,
        matter_id,
        work_product_version_id,
        work_product_version_number,
        work_product_content_sha256,
        sentence_key
    ),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (
        tenant_id,
        matter_id,
        work_product_version_id,
        work_product_version_number,
        work_product_content_sha256
    ) REFERENCES sklegal_legal.work_product_versions(
        tenant_id,
        matter_id,
        id,
        version_number,
        content_sha256
    ),
    FOREIGN KEY (tenant_id, matter_id, claim_id)
        REFERENCES sklegal_legal.ledger_claim_identities(tenant_id, matter_id, id),
    CHECK (updated_at >= created_at)
);

CREATE TRIGGER domain_id_non_nil
BEFORE INSERT OR UPDATE ON sklegal_legal.sentence_groundings
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_nil_domain_ids();

CREATE TRIGGER sentence_grounding_append_only
BEFORE UPDATE OR DELETE ON sklegal_legal.sentence_groundings
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

ALTER TABLE sklegal_legal.sentence_groundings ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.sentence_groundings FORCE ROW LEVEL SECURITY;

CREATE POLICY sentence_grounding_select
ON sklegal_legal.sentence_groundings
FOR SELECT
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));

CREATE POLICY sentence_grounding_insert
ON sklegal_legal.sentence_groundings
FOR INSERT
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));

CREATE INDEX sentence_grounding_claim_idx
ON sklegal_legal.sentence_groundings (tenant_id, matter_id, claim_id);

-- sklegal:down
DROP INDEX sklegal_legal.sentence_grounding_claim_idx;
DROP TABLE sklegal_legal.sentence_groundings;
