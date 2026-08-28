-- sklegal:up
CREATE TABLE sklegal_legal.work_product_feature_identities (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    work_product_id uuid NOT NULL,
    current_aggregate_version sklegal_legal.record_version NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, work_product_id),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.work_product_feature_versions (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    work_product_id uuid NOT NULL,
    aggregate_version sklegal_legal.record_version NOT NULL,
    current_version_id uuid NOT NULL,
    current_version_number sklegal_legal.record_version NOT NULL,
    current_content_sha256 sklegal_legal.sha256_digest NOT NULL,
    status text NOT NULL CHECK (
        status IN ('draft', 'in_review', 'validated', 'approved', 'withdrawn')
    ),
    aggregate_sha256 sklegal_legal.sha256_digest NOT NULL,
    aggregate_payload jsonb NOT NULL CHECK (
        aggregate_payload->>'schemaVersion' = 'sklegal.work-product-aggregate/v1'
        AND aggregate_payload->>'tenantId' = tenant_id::text
        AND aggregate_payload->>'matterId' = matter_id::text
        AND aggregate_payload->>'workProductId' = work_product_id::text
        AND (aggregate_payload->>'aggregateVersion')::bigint = aggregate_version
        AND aggregate_payload->>'currentVersionId' = current_version_id::text
        AND aggregate_payload->>'status' = status
    ),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, work_product_id, aggregate_version),
    UNIQUE (tenant_id, matter_id, aggregate_sha256),
    FOREIGN KEY (tenant_id, matter_id, work_product_id)
        REFERENCES sklegal_legal.work_product_feature_identities(
            tenant_id, matter_id, work_product_id
        ) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE sklegal_legal.work_product_feature_idempotency (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    request_sha256 sklegal_legal.sha256_digest NOT NULL,
    work_product_id uuid NOT NULL,
    aggregate_version sklegal_legal.record_version NOT NULL,
    aggregate_payload jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, idempotency_key),
    FOREIGN KEY (tenant_id, matter_id, work_product_id, aggregate_version)
        REFERENCES sklegal_legal.work_product_feature_versions(
            tenant_id, matter_id, work_product_id, aggregate_version
        )
);

CREATE TABLE sklegal_audit.work_product_feature_events (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    event_id uuid NOT NULL,
    work_product_id uuid NOT NULL,
    aggregate_version sklegal_legal.record_version NOT NULL,
    correlation_id uuid NOT NULL,
    actor_principal_id uuid NOT NULL,
    action text NOT NULL CHECK (length(btrim(action)) BETWEEN 1 AND 512),
    outcome text NOT NULL CHECK (outcome IN ('completed', 'denied')),
    subject_sha256 sklegal_legal.sha256_digest NOT NULL,
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, event_id),
    UNIQUE (tenant_id, matter_id, work_product_id, aggregate_version),
    FOREIGN KEY (tenant_id, matter_id, work_product_id, aggregate_version)
        REFERENCES sklegal_legal.work_product_feature_versions(
            tenant_id, matter_id, work_product_id, aggregate_version
        ),
    FOREIGN KEY (tenant_id, actor_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id)
);

CREATE TABLE sklegal_audit.work_product_feature_outbox (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    outbox_id uuid NOT NULL,
    event_id uuid NOT NULL,
    work_product_id uuid NOT NULL,
    aggregate_version sklegal_legal.record_version NOT NULL,
    topic text NOT NULL CHECK (topic = 'sklegal.work_product.changed'),
    payload_sha256 sklegal_legal.sha256_digest NOT NULL,
    available_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, outbox_id),
    UNIQUE (tenant_id, matter_id, event_id),
    FOREIGN KEY (tenant_id, matter_id, event_id)
        REFERENCES sklegal_audit.work_product_feature_events(
            tenant_id, matter_id, event_id
        ),
    FOREIGN KEY (tenant_id, matter_id, work_product_id, aggregate_version)
        REFERENCES sklegal_legal.work_product_feature_versions(
            tenant_id, matter_id, work_product_id, aggregate_version
        )
);

CREATE FUNCTION sklegal_legal.control_work_product_feature_identity_update()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.matter_id IS DISTINCT FROM OLD.matter_id
       OR NEW.work_product_id IS DISTINCT FROM OLD.work_product_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.current_aggregate_version <> OLD.current_aggregate_version + 1
       OR NEW.updated_at < OLD.updated_at THEN
        RAISE EXCEPTION 'invalid Work Product feature identity transition'
            USING ERRCODE = '55000';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM sklegal_legal.work_product_feature_versions AS version
        WHERE version.tenant_id = NEW.tenant_id
          AND version.matter_id = NEW.matter_id
          AND version.work_product_id = NEW.work_product_id
          AND version.aggregate_version = NEW.current_aggregate_version
    ) THEN
        RAISE EXCEPTION 'current Work Product aggregate version is missing'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION
    sklegal_legal.control_work_product_feature_identity_update()
FROM PUBLIC;

CREATE TRIGGER work_product_feature_identity_controlled_update
BEFORE UPDATE ON sklegal_legal.work_product_feature_identities
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.control_work_product_feature_identity_update();

CREATE TRIGGER work_product_feature_identity_delete_denied
BEFORE DELETE ON sklegal_legal.work_product_feature_identities
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

DO $append_only$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.work_product_feature_versions'::regclass,
        'sklegal_legal.work_product_feature_idempotency'::regclass,
        'sklegal_audit.work_product_feature_events'::regclass,
        'sklegal_audit.work_product_feature_outbox'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change()',
            target_table
        );
    END LOOP;
END;
$append_only$;

DO $rls$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.work_product_feature_identities'::regclass,
        'sklegal_legal.work_product_feature_versions'::regclass,
        'sklegal_legal.work_product_feature_idempotency'::regclass,
        'sklegal_audit.work_product_feature_events'::regclass,
        'sklegal_audit.work_product_feature_outbox'::regclass
    ]
    LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', target_table);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', target_table);
        EXECUTE format(
            'CREATE POLICY work_product_feature_boundary ON %s '
            'USING (sklegal_identity.record_is_authorized(tenant_id, matter_id)) '
            'WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id))',
            target_table
        );
    END LOOP;
END;
$rls$;

CREATE INDEX work_product_feature_current_idx
ON sklegal_legal.work_product_feature_versions (
    tenant_id, matter_id, work_product_id, aggregate_version DESC
);

-- sklegal:down
DROP INDEX sklegal_legal.work_product_feature_current_idx;

DROP POLICY work_product_feature_boundary
ON sklegal_audit.work_product_feature_outbox;
ALTER TABLE sklegal_audit.work_product_feature_outbox NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_audit.work_product_feature_outbox DISABLE ROW LEVEL SECURITY;

DROP POLICY work_product_feature_boundary
ON sklegal_audit.work_product_feature_events;
ALTER TABLE sklegal_audit.work_product_feature_events NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_audit.work_product_feature_events DISABLE ROW LEVEL SECURITY;

DROP POLICY work_product_feature_boundary
ON sklegal_legal.work_product_feature_idempotency;
ALTER TABLE sklegal_legal.work_product_feature_idempotency NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.work_product_feature_idempotency DISABLE ROW LEVEL SECURITY;

DROP POLICY work_product_feature_boundary
ON sklegal_legal.work_product_feature_versions;
ALTER TABLE sklegal_legal.work_product_feature_versions NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.work_product_feature_versions DISABLE ROW LEVEL SECURITY;

DROP POLICY work_product_feature_boundary
ON sklegal_legal.work_product_feature_identities;
ALTER TABLE sklegal_legal.work_product_feature_identities NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.work_product_feature_identities DISABLE ROW LEVEL SECURITY;

DROP TABLE sklegal_audit.work_product_feature_outbox;
DROP TABLE sklegal_audit.work_product_feature_events;
DROP TABLE sklegal_legal.work_product_feature_idempotency;
DROP TRIGGER work_product_feature_identity_delete_denied
ON sklegal_legal.work_product_feature_identities;
DROP TRIGGER work_product_feature_identity_controlled_update
ON sklegal_legal.work_product_feature_identities;
DROP TABLE sklegal_legal.work_product_feature_versions;
DROP TABLE sklegal_legal.work_product_feature_identities;
DROP FUNCTION sklegal_legal.control_work_product_feature_identity_update();
