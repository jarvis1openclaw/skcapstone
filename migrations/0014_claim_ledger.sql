-- sklegal:up
CREATE TABLE sklegal_legal.ledger_claim_identities (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id)
);

CREATE TABLE sklegal_legal.ledger_claims (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    statement text NOT NULL CHECK (length(btrim(statement)) > 0),
    policy_revision sklegal_legal.sha256_digest NOT NULL,
    status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'under_review', 'supported', 'challenged', 'withdrawn')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    system_from timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id, version),
    FOREIGN KEY (tenant_id, matter_id, id) REFERENCES sklegal_legal.ledger_claim_identities(tenant_id, matter_id, id),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.ledger_claim_support (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    claim_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('support', 'counter_support')),
    source_reference_id uuid NOT NULL,
    span_start integer NOT NULL CHECK (span_start >= 0),
    span_end integer NOT NULL CHECK (span_end > span_start),
    excerpt_sha256 sklegal_legal.sha256_digest NOT NULL,
    note text CHECK (note IS NULL OR length(btrim(note)) > 0),
    recorded_by_principal_id uuid NOT NULL,
    policy_revision sklegal_legal.sha256_digest NOT NULL,
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, claim_id) REFERENCES sklegal_legal.ledger_claim_identities(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, source_reference_id) REFERENCES sklegal_legal.source_references(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, recorded_by_principal_id) REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (updated_at >= created_at)
);

CREATE FUNCTION sklegal_legal.prepare_ledger_claim_version()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    prior sklegal_legal.ledger_claims%ROWTYPE;
    now_at timestamptz;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(NEW.tenant_id::text || ':' || NEW.matter_id::text || ':' || NEW.id::text, 0)
    );
    SELECT * INTO prior
    FROM sklegal_legal.ledger_claims
    WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id AND id = NEW.id
    ORDER BY version DESC LIMIT 1;
    now_at := clock_timestamp();
    IF NOT FOUND THEN
        IF NEW.version <> 1 OR NEW.status <> 'proposed' THEN
            RAISE EXCEPTION 'first ledger claim version must be proposed version 1'
                USING ERRCODE = '23514';
        END IF;
        NEW.created_at := now_at;
    ELSE
        IF NEW.version <> prior.version + 1 THEN
            RAISE EXCEPTION 'ledger claim version conflict: expected %', prior.version + 1 USING ERRCODE = '40001';
        END IF;
        IF NEW.policy_revision IS DISTINCT FROM prior.policy_revision THEN
            RAISE EXCEPTION 'ledger claim policy revision is immutable'
                USING ERRCODE = '55000';
        END IF;
        IF NEW.status <> prior.status AND NOT (
            (prior.status = 'proposed' AND NEW.status IN ('under_review', 'withdrawn'))
            OR (prior.status = 'under_review' AND NEW.status IN ('supported', 'challenged', 'withdrawn'))
            OR (prior.status = 'supported' AND NEW.status IN ('challenged', 'withdrawn'))
            OR (prior.status = 'challenged' AND NEW.status IN ('under_review', 'withdrawn'))
        ) THEN
            RAISE EXCEPTION 'ledger claim status is not an adjacent declared edge'
                USING ERRCODE = '23514';
        END IF;
        NEW.created_at := prior.created_at;
    END IF;
    NEW.updated_at := now_at;
    NEW.system_from := now_at;
    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.prepare_ledger_claim_version() FROM PUBLIC;

CREATE TRIGGER ledger_claim_version_prepare
BEFORE INSERT ON sklegal_legal.ledger_claims
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.prepare_ledger_claim_version();

CREATE TRIGGER ledger_claim_versions_append_only
BEFORE UPDATE OR DELETE ON sklegal_legal.ledger_claims
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

CREATE FUNCTION sklegal_legal.require_supported_ledger_claim()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM sklegal_legal.ledger_claim_support AS support
        WHERE support.tenant_id = NEW.tenant_id
          AND support.matter_id = NEW.matter_id
          AND support.claim_id = NEW.id
          AND support.kind = 'support'
    ) THEN
        RAISE EXCEPTION 'ledger claim requires at least one supporting source span'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.require_supported_ledger_claim() FROM PUBLIC;

CREATE CONSTRAINT TRIGGER ledger_claim_support_cardinality
AFTER INSERT ON sklegal_legal.ledger_claims
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_supported_ledger_claim();

CREATE VIEW sklegal_legal.ledger_claim_history
WITH (security_invoker = true, security_barrier = true)
AS
SELECT claim.*,
       lead(system_from) OVER (
           PARTITION BY tenant_id, matter_id, id ORDER BY version
       ) AS system_to
FROM sklegal_legal.ledger_claims AS claim;

CREATE VIEW sklegal_legal.ledger_claim_current
WITH (security_invoker = true, security_barrier = true)
AS
SELECT *
FROM sklegal_legal.ledger_claim_history
WHERE system_to IS NULL;

DO $claim_ledger_triggers$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.ledger_claim_identities'::regclass,
        'sklegal_legal.ledger_claims'::regclass,
        'sklegal_legal.ledger_claim_support'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER domain_id_non_nil BEFORE INSERT OR UPDATE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_nil_domain_ids()',
            target_table
        );
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', target_table);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', target_table);
        EXECUTE format(
            'CREATE POLICY matter_select ON %s FOR SELECT '
            'USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))',
            target_table
        );
        EXECUTE format(
            'CREATE POLICY matter_insert ON %s FOR INSERT '
            'WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id))',
            target_table
        );
    END LOOP;
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.ledger_claim_identities'::regclass,
        'sklegal_legal.ledger_claim_support'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change()',
            target_table
        );
    END LOOP;
END;
$claim_ledger_triggers$;

CREATE INDEX ledger_claim_support_claim_idx
ON sklegal_legal.ledger_claim_support (tenant_id, matter_id, claim_id, created_at);
CREATE INDEX ledger_claim_version_time_idx
ON sklegal_legal.ledger_claims (tenant_id, matter_id, id, system_from DESC);

-- sklegal:down
DROP INDEX sklegal_legal.ledger_claim_version_time_idx;
DROP INDEX sklegal_legal.ledger_claim_support_claim_idx;
DROP VIEW sklegal_legal.ledger_claim_current;
DROP VIEW sklegal_legal.ledger_claim_history;
DROP TABLE sklegal_legal.ledger_claim_support;
DROP TABLE sklegal_legal.ledger_claims;
DROP FUNCTION sklegal_legal.require_supported_ledger_claim();
DROP FUNCTION sklegal_legal.prepare_ledger_claim_version();
DROP TABLE sklegal_legal.ledger_claim_identities;
