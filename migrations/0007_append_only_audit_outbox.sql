-- sklegal:up
DO $audit_scaffold_empty$
BEGIN
    IF EXISTS (SELECT 1 FROM sklegal_audit.events) THEN
        RAISE EXCEPTION 'audit scaffold must be empty before chain installation'
            USING ERRCODE = '55000';
    END IF;
END;
$audit_scaffold_empty$;

DROP TRIGGER audit_writer_not_installed ON sklegal_audit.events;
DROP FUNCTION sklegal_audit.reject_insert_until_chain_writer();

ALTER TABLE sklegal_audit.events
    DROP CONSTRAINT events_correlation_id_check,
    DROP CONSTRAINT events_resource_kind_check;

ALTER TABLE sklegal_audit.events
    ALTER COLUMN correlation_id TYPE uuid USING correlation_id::uuid,
    ALTER COLUMN policy_decision_id TYPE uuid USING policy_decision_id::uuid,
    ADD COLUMN event_sequence bigint NOT NULL,
    ADD COLUMN run_id uuid NOT NULL,
    ADD COLUMN trace_id text NOT NULL,
    ADD COLUMN span_id text NOT NULL,
    ADD COLUMN trace_flags text NOT NULL,
    ADD COLUMN boundary text NOT NULL,
    ADD COLUMN outcome text NOT NULL,
    ADD COLUMN reason_code text NOT NULL,
    ADD COLUMN authorization_decision_id uuid,
    ADD COLUMN attributes jsonb NOT NULL,
    ADD COLUMN canonical_payload jsonb NOT NULL,
    ADD CONSTRAINT audit_event_sequence_positive CHECK (event_sequence >= 1),
    ADD CONSTRAINT events_resource_kind_check CHECK (
        resource_kind ~ '^[a-z0-9][a-z0-9._:/@-]{0,99}$'
    ),
    ADD CONSTRAINT audit_trace_id_shape CHECK (
        trace_id ~ '^[0-9a-f]{32}$' AND trace_id <> repeat('0', 32)
    ),
    ADD CONSTRAINT audit_span_id_shape CHECK (
        span_id ~ '^[0-9a-f]{16}$' AND span_id <> repeat('0', 16)
    ),
    ADD CONSTRAINT audit_trace_flags_shape CHECK (trace_flags ~ '^[0-9a-f]{2}$'),
    ADD CONSTRAINT audit_boundary_closed CHECK (
        boundary IN ('api', 'workflow', 'tool', 'model', 'human', 'connector')
    ),
    ADD CONSTRAINT audit_outcome_closed CHECK (
        outcome IN ('allow', 'deny', 'success', 'failure')
    ),
    ADD CONSTRAINT audit_reason_code_shape CHECK (
        reason_code ~ '^[a-z0-9][a-z0-9._:/@-]{0,254}$'
    ),
    ADD CONSTRAINT audit_attributes_object CHECK (jsonb_typeof(attributes) = 'object'),
    ADD CONSTRAINT audit_canonical_payload_object CHECK (
        jsonb_typeof(canonical_payload) = 'object'
    ),
    ADD CONSTRAINT audit_event_tenant_sequence_unique UNIQUE (
        tenant_id, event_sequence
    ),
    ADD CONSTRAINT audit_event_predecessor_fk FOREIGN KEY (
        tenant_id, previous_event_sha256
    ) REFERENCES sklegal_audit.events (tenant_id, event_sha256);

CREATE TABLE sklegal_audit.chain_heads (
    tenant_id uuid PRIMARY KEY REFERENCES sklegal_identity.tenants(id),
    last_event_id uuid,
    last_event_sequence bigint NOT NULL DEFAULT 0 CHECK (last_event_sequence >= 0),
    last_event_sha256 sklegal_legal.sha256_digest,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        (last_event_sequence = 0 AND last_event_id IS NULL AND last_event_sha256 IS NULL)
        OR
        (last_event_sequence > 0 AND last_event_id IS NOT NULL AND last_event_sha256 IS NOT NULL)
    )
);

CREATE TABLE sklegal_audit.outbox (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid,
    event_id uuid NOT NULL,
    run_id uuid NOT NULL,
    correlation_id uuid NOT NULL,
    event_sequence bigint NOT NULL CHECK (event_sequence >= 1),
    event_sha256 sklegal_legal.sha256_digest NOT NULL,
    destination text NOT NULL DEFAULT 'audit.local' CHECK (
        destination ~ '^[a-z0-9][a-z0-9._:/@-]{0,254}$'
    ),
    available_at timestamptz NOT NULL,
    delivered_at timestamptz,
    delivery_attempts integer NOT NULL DEFAULT 0 CHECK (delivery_attempts >= 0),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, event_id, destination),
    FOREIGN KEY (tenant_id, event_id)
        REFERENCES sklegal_audit.events (tenant_id, id),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters (tenant_id, matter_id),
    CHECK (delivered_at IS NULL OR delivered_at >= available_at)
);

CREATE TABLE sklegal_audit.outbox_deliveries (
    delivery_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid,
    outbox_id uuid NOT NULL,
    event_id uuid NOT NULL,
    destination text NOT NULL CHECK (
        destination ~ '^[a-z0-9][a-z0-9._:/@-]{0,254}$'
    ),
    idempotency_key sklegal_legal.sha256_digest NOT NULL,
    event_sha256 sklegal_legal.sha256_digest NOT NULL,
    delivered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, delivery_id),
    UNIQUE (tenant_id, outbox_id, destination),
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, outbox_id)
        REFERENCES sklegal_audit.outbox (tenant_id, id),
    FOREIGN KEY (tenant_id, event_id)
        REFERENCES sklegal_audit.events (tenant_id, id),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters (tenant_id, matter_id)
);

CREATE TABLE sklegal_audit.projection_watermarks (
    tenant_id uuid NOT NULL REFERENCES sklegal_identity.tenants(id),
    projection text NOT NULL CHECK (
        projection ~ '^[a-z0-9][a-z0-9._:/@-]{0,254}$'
    ),
    event_sequence bigint NOT NULL CHECK (event_sequence >= 1),
    event_sha256 sklegal_legal.sha256_digest NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, projection),
    FOREIGN KEY (tenant_id, event_sequence)
        REFERENCES sklegal_audit.events (tenant_id, event_sequence),
    FOREIGN KEY (tenant_id, event_sha256)
        REFERENCES sklegal_audit.events (tenant_id, event_sha256)
);

-- This owner-readable, content-free sentinel is deliberately outside RLS. It
-- is not granted to runtime roles and is written only by append_event through
-- the controlled-writer trigger. Its sole purpose is to make evidence-bearing
-- rollback detection reliable for the NOBYPASSRLS migration owner.
CREATE TABLE sklegal_audit.rollback_guard (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton)
);

CREATE FUNCTION sklegal_audit.attributes_are_safe(value jsonb)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SECURITY INVOKER
SET search_path = pg_catalog
RETURN COALESCE(
    jsonb_typeof(value) = 'object'
    AND NOT EXISTS (
        SELECT 1
        FROM jsonb_object_keys(value) AS attribute(key)
        WHERE key NOT IN (
            'capability', 'audience', 'target', 'operation', 'purpose',
            'model_route', 'workflow_run_id', 'connector_kind', 'error_code',
            'event_schema', 'effective_classification', 'resource_version',
            'resource_sha256', 'policy_revision', 'resource_identity',
            'policy_boundary', 'status_code', 'retry_count'
        )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM jsonb_each(value) AS attribute(key, item)
        WHERE item <> 'null'::jsonb
          AND NOT (
              (
                  key IN (
                      'capability', 'audience', 'target', 'operation', 'purpose',
                      'model_route', 'workflow_run_id', 'connector_kind',
                      'error_code', 'event_schema', 'effective_classification',
                      'policy_boundary'
                  )
                  AND jsonb_typeof(item) = 'string'
                  AND length(item #>> '{}') BETWEEN 1 AND 255
                  AND item #>> '{}' ~ '^[a-z0-9][a-z0-9._:/@-]*$'
              )
              OR (
                  key = 'resource_identity'
                  AND jsonb_typeof(item) = 'string'
                  AND length(item #>> '{}') BETWEEN 1 AND 160
                  AND item #>> '{}' ~ '^[a-z0-9][a-z0-9._:/@-]*$'
              )
              OR (
                  key IN ('resource_sha256', 'policy_revision')
                  AND jsonb_typeof(item) = 'string'
                  AND item #>> '{}' ~ '^[0-9a-f]{64}$'
              )
              OR (
                  key = 'resource_version'
                  AND jsonb_typeof(item) = 'number'
                  AND item #>> '{}' ~ '^[0-9]+$'
                  AND (item #>> '{}')::numeric >= 1
              )
              OR (
                  key = 'status_code'
                  AND jsonb_typeof(item) = 'number'
                  AND item #>> '{}' ~ '^[0-9]+$'
                  AND (item #>> '{}')::numeric BETWEEN 100 AND 599
              )
              OR (
                  key = 'retry_count'
                  AND jsonb_typeof(item) = 'number'
                  AND item #>> '{}' ~ '^[0-9]+$'
                  AND (item #>> '{}')::numeric BETWEEN 0 AND 1000
              )
          )
    ),
    false
);

CREATE FUNCTION sklegal_audit.canonical_json_text(value jsonb)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
PARALLEL SAFE
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    rendered text;
BEGIN
    CASE jsonb_typeof(value)
    WHEN 'object' THEN
        SELECT '{' || COALESCE(string_agg(
            to_json(attribute.key)::text || ':' ||
            sklegal_audit.canonical_json_text(attribute.item),
            ',' ORDER BY attribute.key COLLATE "C"
        ), '') || '}'
        INTO rendered
        FROM jsonb_each(value) AS attribute(key, item);
    WHEN 'array' THEN
        SELECT '[' || COALESCE(string_agg(
            sklegal_audit.canonical_json_text(element.item),
            ',' ORDER BY element.position
        ), '') || ']'
        INTO rendered
        FROM jsonb_array_elements(value) WITH ORDINALITY
            AS element(item, position);
    WHEN 'string' THEN
        rendered := to_json(value #>> '{}')::text;
    ELSE
        rendered := value::text;
    END CASE;
    RETURN rendered;
END;
$function$;

CREATE FUNCTION sklegal_audit.payload_sha256(value jsonb)
RETURNS sklegal_legal.sha256_digest
LANGUAGE sql
IMMUTABLE
SECURITY INVOKER
SET search_path = pg_catalog
RETURN encode(
    sha256(convert_to(sklegal_audit.canonical_json_text(value), 'UTF8')),
    'hex'
);

CREATE FUNCTION sklegal_audit.canonical_timestamp(value timestamptz)
RETURNS text
LANGUAGE plpgsql
STABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    utc_value timestamp without time zone;
    year_number integer;
BEGIN
    IF NOT isfinite(value) THEN
        RAISE EXCEPTION 'canonical audit timestamp is outside AD 1 through 9999'
            USING ERRCODE = '22008';
    END IF;
    utc_value := value AT TIME ZONE 'UTC';
    IF utc_value < TIMESTAMP '0001-01-01 00:00:00 AD'
       OR utc_value >= TIMESTAMP '10000-01-01 00:00:00 AD' THEN
        RAISE EXCEPTION 'canonical audit timestamp is outside AD 1 through 9999'
            USING ERRCODE = '22008';
    END IF;
    year_number := EXTRACT(YEAR FROM utc_value)::integer;
    IF year_number < 1
       OR year_number > 9999 THEN
        RAISE EXCEPTION 'canonical audit timestamp is outside AD 1 through 9999'
            USING ERRCODE = '22008';
    END IF;
    RETURN lpad(year_number::text, 4, '0') || '-' ||
        to_char(utc_value, 'MM-DD"T"HH24:MI:SS.US"Z"');
END;
$function$;

CREATE FUNCTION sklegal_audit.canonical_event_payload(
    event_id uuid,
    tenant_id uuid,
    matter_id uuid,
    principal_id uuid,
    run_id uuid,
    correlation_id uuid,
    trace_id text,
    span_id text,
    trace_flags text,
    boundary text,
    action text,
    resource_kind text,
    resource_id uuid,
    authorization_decision_id uuid,
    policy_decision_id uuid,
    outcome text,
    reason_code text,
    occurred_at timestamptz,
    attributes jsonb,
    event_sequence bigint,
    previous_event_sha256 sklegal_legal.sha256_digest,
    recorded_at timestamptz
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
RETURN jsonb_build_object(
    'event_id', event_id,
    'tenant_id', tenant_id,
    'matter_id', matter_id,
    'principal_id', principal_id,
    'correlation', jsonb_build_object(
        'run_id', run_id,
        'correlation_id', correlation_id,
        'trace_id', trace_id,
        'span_id', span_id,
        'trace_flags', trace_flags
    ),
    'boundary', boundary,
    'action', action,
    'resource_kind', resource_kind,
    'resource_id', resource_id,
    'authorization_decision_id', authorization_decision_id,
    'policy_decision_id', policy_decision_id,
    'outcome', outcome,
    'reason_code', reason_code,
    'occurred_at', sklegal_audit.canonical_timestamp(occurred_at),
    'attributes', jsonb_strip_nulls(attributes),
    'event_sequence', event_sequence,
    'previous_event_sha256', previous_event_sha256,
    'recorded_at', sklegal_audit.canonical_timestamp(recorded_at)
);

CREATE FUNCTION sklegal_audit.event_row_matches_payload(value sklegal_audit.events)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
RETURN value.canonical_payload = sklegal_audit.canonical_event_payload(
    value.id,
    value.tenant_id,
    value.matter_id,
    value.principal_id,
    value.run_id,
    value.correlation_id,
    value.trace_id,
    value.span_id,
    value.trace_flags,
    value.boundary,
    value.action,
    value.resource_kind,
    value.resource_id,
    value.authorization_decision_id,
    value.policy_decision_id,
    value.outcome,
    value.reason_code,
    value.occurred_at,
    value.attributes,
    value.event_sequence,
    value.previous_event_sha256,
    value.recorded_at
);

CREATE FUNCTION sklegal_audit.require_controlled_writer()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF current_user <> 'sklegal_migrator' OR session_user = current_user THEN
        RAISE EXCEPTION '%', TG_ARGV[0] USING ERRCODE = '42501';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE FUNCTION sklegal_audit.validate_event_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NOT sklegal_audit.attributes_are_safe(NEW.attributes)
       OR NOT sklegal_audit.event_row_matches_payload(NEW)
       OR NEW.event_sha256 <> sklegal_audit.payload_sha256(NEW.canonical_payload) THEN
        RAISE EXCEPTION 'audit event metadata is invalid' USING ERRCODE = '22023';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER audit_events_controlled_insert
BEFORE INSERT ON sklegal_audit.events
FOR EACH ROW EXECUTE FUNCTION sklegal_audit.require_controlled_writer(
    'controlled audit chain writer required'
);

CREATE TRIGGER audit_events_validate_insert
BEFORE INSERT ON sklegal_audit.events
FOR EACH ROW EXECUTE FUNCTION sklegal_audit.validate_event_insert();

CREATE TRIGGER audit_chain_heads_controlled
BEFORE INSERT OR UPDATE OR DELETE ON sklegal_audit.chain_heads
FOR EACH ROW EXECUTE FUNCTION sklegal_audit.require_controlled_writer(
    'controlled audit chain writer required'
);

CREATE TRIGGER audit_outbox_controlled
BEFORE INSERT OR UPDATE OR DELETE ON sklegal_audit.outbox
FOR EACH ROW EXECUTE FUNCTION sklegal_audit.require_controlled_writer(
    'controlled outbox writer required'
);

CREATE TRIGGER audit_outbox_delivery_controlled_insert
BEFORE INSERT ON sklegal_audit.outbox_deliveries
FOR EACH ROW EXECUTE FUNCTION sklegal_audit.require_controlled_writer(
    'controlled outbox writer required'
);

CREATE TRIGGER audit_outbox_delivery_append_only
BEFORE UPDATE OR DELETE ON sklegal_audit.outbox_deliveries
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

CREATE TRIGGER audit_projection_watermark_controlled
BEFORE INSERT OR UPDATE OR DELETE ON sklegal_audit.projection_watermarks
FOR EACH ROW EXECUTE FUNCTION sklegal_audit.require_controlled_writer(
    'controlled projection writer required'
);

CREATE TRIGGER audit_rollback_guard_controlled
BEFORE INSERT OR UPDATE OR DELETE ON sklegal_audit.rollback_guard
FOR EACH ROW EXECUTE FUNCTION sklegal_audit.require_controlled_writer(
    'controlled rollback guard writer required'
);

DO $audit_domain_id_triggers$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_audit.chain_heads'::regclass,
        'sklegal_audit.outbox'::regclass,
        'sklegal_audit.outbox_deliveries'::regclass,
        'sklegal_audit.projection_watermarks'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER domain_id_non_nil BEFORE INSERT OR UPDATE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_nil_domain_ids()',
            target_table
        );
    END LOOP;
END;
$audit_domain_id_triggers$;

ALTER TABLE sklegal_audit.chain_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_audit.chain_heads FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_audit.outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_audit.outbox FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_audit.outbox_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_audit.outbox_deliveries FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_audit.projection_watermarks ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_audit.projection_watermarks FORCE ROW LEVEL SECURITY;

CREATE POLICY audit_controlled_insert ON sklegal_audit.events
FOR INSERT WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
    AND principal_id = sklegal_identity.current_principal_id()
);

CREATE POLICY audit_integrity_select ON sklegal_audit.events
FOR SELECT USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND current_setting('sklegal.audit_integrity_verification', true) = 'on'
    AND tenant_id = sklegal_identity.current_tenant_id()
);

CREATE POLICY audit_chain_head_select ON sklegal_audit.chain_heads
FOR SELECT USING (sklegal_identity.record_is_authorized(tenant_id, NULL));
CREATE POLICY audit_chain_head_controlled_insert ON sklegal_audit.chain_heads
FOR INSERT WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, NULL)
);
CREATE POLICY audit_chain_head_controlled_update ON sklegal_audit.chain_heads
FOR UPDATE USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, NULL)
) WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, NULL)
);

CREATE POLICY audit_outbox_select ON sklegal_audit.outbox
FOR SELECT USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY audit_outbox_controlled_insert ON sklegal_audit.outbox
FOR INSERT WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
);
CREATE POLICY audit_outbox_controlled_update ON sklegal_audit.outbox
FOR UPDATE USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
) WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
);

CREATE POLICY audit_delivery_select ON sklegal_audit.outbox_deliveries
FOR SELECT USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY audit_delivery_controlled_insert ON sklegal_audit.outbox_deliveries
FOR INSERT WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
);

CREATE POLICY audit_watermark_select ON sklegal_audit.projection_watermarks
FOR SELECT USING (sklegal_identity.record_is_authorized(tenant_id, NULL));
CREATE POLICY audit_watermark_controlled_insert ON sklegal_audit.projection_watermarks
FOR INSERT WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, NULL)
);
CREATE POLICY audit_watermark_controlled_update ON sklegal_audit.projection_watermarks
FOR UPDATE USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, NULL)
) WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, NULL)
);

CREATE FUNCTION sklegal_audit.append_event(
    p_event_id uuid,
    p_tenant_id uuid,
    p_matter_id uuid,
    p_principal_id uuid,
    p_run_id uuid,
    p_correlation_id uuid,
    p_trace_id text,
    p_span_id text,
    p_trace_flags text,
    p_boundary text,
    p_action text,
    p_resource_kind text,
    p_resource_id uuid,
    p_authorization_decision_id uuid,
    p_policy_decision_id uuid,
    p_outcome text,
    p_reason_code text,
    p_occurred_at timestamptz,
    p_attributes jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    head sklegal_audit.chain_heads%ROWTYPE;
    inserted sklegal_audit.events%ROWTYPE;
    next_sequence bigint;
    recorded timestamptz := clock_timestamp();
    payload jsonb;
    digest sklegal_legal.sha256_digest;
BEGIN
    PERFORM set_config('sklegal.audit_integrity_verification', '', true);
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR sklegal_identity.current_tenant_id() IS DISTINCT FROM p_tenant_id
       OR sklegal_identity.current_principal_id() IS DISTINCT FROM p_principal_id
       OR NOT sklegal_identity.record_is_authorized(p_tenant_id, p_matter_id) THEN
        RAISE EXCEPTION 'audit scope is unauthorized' USING ERRCODE = '42501';
    END IF;
    IF NOT sklegal_audit.attributes_are_safe(p_attributes) THEN
        RAISE EXCEPTION 'audit attributes are not allowlisted'
            USING ERRCODE = '22023';
    END IF;
    IF p_event_id IS NULL OR p_event_id = '00000000-0000-0000-0000-000000000000'
       OR p_run_id IS NULL OR p_run_id = '00000000-0000-0000-0000-000000000000'
       OR p_correlation_id IS NULL
       OR p_correlation_id = '00000000-0000-0000-0000-000000000000'
       OR p_trace_id !~ '^[0-9a-f]{32}$' OR p_trace_id = repeat('0', 32)
       OR p_span_id !~ '^[0-9a-f]{16}$' OR p_span_id = repeat('0', 16)
       OR p_trace_flags !~ '^[0-9a-f]{2}$'
       OR p_boundary NOT IN ('api', 'workflow', 'tool', 'model', 'human', 'connector')
       OR p_action !~ '^[a-z0-9][a-z0-9._:/@-]{0,254}$'
       OR p_resource_kind !~ '^[a-z0-9][a-z0-9._:/@-]{0,99}$'
       OR p_outcome NOT IN ('allow', 'deny', 'success', 'failure')
       OR p_reason_code !~ '^[a-z0-9][a-z0-9._:/@-]{0,254}$'
       OR p_occurred_at IS NULL THEN
        RAISE EXCEPTION 'audit event metadata is invalid' USING ERRCODE = '22023';
    END IF;
    PERFORM sklegal_audit.canonical_timestamp(p_occurred_at);
    IF p_occurred_at > recorded THEN
        RAISE EXCEPTION 'audit event metadata is invalid' USING ERRCODE = '22023';
    END IF;

    INSERT INTO sklegal_audit.chain_heads (tenant_id)
    VALUES (p_tenant_id)
    ON CONFLICT (tenant_id) DO NOTHING;

    SELECT * INTO STRICT head
    FROM sklegal_audit.chain_heads
    WHERE tenant_id = p_tenant_id
    FOR UPDATE;

    next_sequence := head.last_event_sequence + 1;
    payload := sklegal_audit.canonical_event_payload(
        p_event_id,
        p_tenant_id,
        p_matter_id,
        p_principal_id,
        p_run_id,
        p_correlation_id,
        p_trace_id,
        p_span_id,
        p_trace_flags,
        p_boundary,
        p_action,
        p_resource_kind,
        p_resource_id,
        p_authorization_decision_id,
        p_policy_decision_id,
        p_outcome,
        p_reason_code,
        p_occurred_at,
        p_attributes,
        next_sequence,
        head.last_event_sha256,
        recorded
    );
    digest := sklegal_audit.payload_sha256(payload);

    INSERT INTO sklegal_audit.events (
        id,
        tenant_id,
        matter_id,
        principal_id,
        action,
        resource_kind,
        resource_id,
        policy_decision_id,
        correlation_id,
        event_sha256,
        previous_event_sha256,
        occurred_at,
        recorded_at,
        event_sequence,
        run_id,
        trace_id,
        span_id,
        trace_flags,
        boundary,
        outcome,
        reason_code,
        authorization_decision_id,
        attributes,
        canonical_payload
    ) VALUES (
        p_event_id,
        p_tenant_id,
        p_matter_id,
        p_principal_id,
        p_action,
        p_resource_kind,
        p_resource_id,
        p_policy_decision_id,
        p_correlation_id,
        digest,
        head.last_event_sha256,
        p_occurred_at,
        recorded,
        next_sequence,
        p_run_id,
        p_trace_id,
        p_span_id,
        p_trace_flags,
        p_boundary,
        p_outcome,
        p_reason_code,
        p_authorization_decision_id,
        p_attributes,
        payload
    ) RETURNING * INTO inserted;

    UPDATE sklegal_audit.chain_heads
    SET last_event_id = inserted.id,
        last_event_sequence = inserted.event_sequence,
        last_event_sha256 = inserted.event_sha256,
        updated_at = recorded
    WHERE tenant_id = p_tenant_id;

    INSERT INTO sklegal_audit.outbox (
        id,
        tenant_id,
        matter_id,
        event_id,
        run_id,
        correlation_id,
        event_sequence,
        event_sha256,
        available_at
    ) VALUES (
        inserted.id,
        inserted.tenant_id,
        inserted.matter_id,
        inserted.id,
        inserted.run_id,
        inserted.correlation_id,
        inserted.event_sequence,
        inserted.event_sha256,
        recorded
    );

    INSERT INTO sklegal_audit.rollback_guard (singleton)
    VALUES (true)
    ON CONFLICT (singleton) DO NOTHING;

    RETURN inserted.canonical_payload || jsonb_build_object(
        'event_sha256', inserted.event_sha256,
        'outbox_id', inserted.id
    );
END;
$function$;

CREATE FUNCTION sklegal_audit.verify_current_tenant_chain()
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    selected_tenant uuid;
    previous_integrity_mode text;
    valid boolean;
BEGIN
    previous_integrity_mode := current_setting(
        'sklegal.audit_integrity_verification', true
    );
    PERFORM set_config('sklegal.audit_integrity_verification', '', true);
    BEGIN
        IF NOT sklegal_identity.runtime_role_is_safe() THEN
            RAISE EXCEPTION 'audit scope is unauthorized' USING ERRCODE = '42501';
        END IF;
        selected_tenant := sklegal_identity.current_tenant_id();
        PERFORM set_config('sklegal.audit_integrity_verification', 'on', true);

        WITH ordered AS (
            SELECT event_row AS event_record,
                   event_row.*,
                   row_number() OVER (ORDER BY event_sequence) AS expected_sequence,
                   lag(event_sha256) OVER (
                       ORDER BY event_sequence
                   ) AS expected_predecessor
            FROM sklegal_audit.events AS event_row
            WHERE tenant_id = selected_tenant
        ), summary AS (
            SELECT count(*) AS event_count,
                   COALESCE(bool_and(
                       event_sequence = expected_sequence
                       AND previous_event_sha256 IS NOT DISTINCT
                           FROM expected_predecessor
                       AND event_sha256 =
                           sklegal_audit.payload_sha256(canonical_payload)
                       AND sklegal_audit.event_row_matches_payload(event_record)
                   ), true) AS rows_valid
            FROM ordered
        ), tip AS (
            SELECT id, event_sequence, event_sha256
            FROM ordered
            ORDER BY event_sequence DESC
            LIMIT 1
        )
        SELECT CASE
            WHEN summary.event_count = 0 THEN NOT EXISTS (
                SELECT 1 FROM sklegal_audit.chain_heads
                WHERE tenant_id = selected_tenant AND last_event_sequence <> 0
            )
            ELSE summary.rows_valid AND EXISTS (
                SELECT 1
                FROM sklegal_audit.chain_heads AS head
                CROSS JOIN tip
                WHERE head.tenant_id = selected_tenant
                  AND head.last_event_sequence = summary.event_count
                  AND head.last_event_sequence = tip.event_sequence
                  AND head.last_event_id = tip.id
                  AND head.last_event_sha256 = tip.event_sha256
            )
        END
        INTO valid
        FROM summary;

        PERFORM set_config(
            'sklegal.audit_integrity_verification',
            COALESCE(previous_integrity_mode, ''),
            true
        );
        RETURN valid;
    EXCEPTION WHEN OTHERS THEN
        PERFORM set_config(
            'sklegal.audit_integrity_verification',
            COALESCE(previous_integrity_mode, ''),
            true
        );
        RAISE;
    END;
END;
$function$;

CREATE FUNCTION sklegal_audit.record_outbox_delivery(
    p_outbox_id uuid,
    p_destination text,
    p_delivery_id uuid
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    message sklegal_audit.outbox%ROWTYPE;
    delivered timestamptz := clock_timestamp();
    idempotency sklegal_legal.sha256_digest;
BEGIN
    PERFORM set_config('sklegal.audit_integrity_verification', '', true);
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR p_destination !~ '^[a-z0-9][a-z0-9._:/@-]{0,254}$'
       OR p_delivery_id IS NULL
       OR p_delivery_id = '00000000-0000-0000-0000-000000000000' THEN
        RAISE EXCEPTION 'outbox delivery is unauthorized' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO message
    FROM sklegal_audit.outbox
    WHERE tenant_id = sklegal_identity.current_tenant_id()
      AND id = p_outbox_id
      AND destination = p_destination
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'outbox message is unavailable' USING ERRCODE = '55000';
    END IF;
    IF EXISTS (
        SELECT 1 FROM sklegal_audit.outbox_deliveries
        WHERE tenant_id = message.tenant_id
          AND outbox_id = message.id
          AND destination = message.destination
    ) THEN
        RETURN false;
    END IF;
    idempotency := sklegal_audit.payload_sha256(jsonb_build_object(
        'tenant_id', message.tenant_id,
        'event_id', message.event_id,
        'destination', message.destination,
        'event_sha256', message.event_sha256
    ));
    INSERT INTO sklegal_audit.outbox_deliveries (
        delivery_id,
        tenant_id,
        matter_id,
        outbox_id,
        event_id,
        destination,
        idempotency_key,
        event_sha256,
        delivered_at
    ) VALUES (
        p_delivery_id,
        message.tenant_id,
        message.matter_id,
        message.id,
        message.event_id,
        message.destination,
        idempotency,
        message.event_sha256,
        delivered
    );
    UPDATE sklegal_audit.outbox
    SET delivered_at = delivered,
        delivery_attempts = delivery_attempts + 1
    WHERE tenant_id = message.tenant_id AND id = message.id;
    RETURN true;
END;
$function$;

CREATE FUNCTION sklegal_audit.advance_projection_watermark(
    p_projection text,
    p_expected_sequence bigint,
    p_expected_event_sha256 sklegal_legal.sha256_digest,
    p_new_sequence bigint,
    p_new_event_sha256 sklegal_legal.sha256_digest
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    selected_tenant uuid;
    current_watermark sklegal_audit.projection_watermarks%ROWTYPE;
    had_current boolean;
BEGIN
    PERFORM set_config('sklegal.audit_integrity_verification', '', true);
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR p_projection !~ '^[a-z0-9][a-z0-9._:/@-]{0,254}$'
       OR p_expected_sequence < 0 OR p_new_sequence < 1 THEN
        RAISE EXCEPTION 'projection update is unauthorized' USING ERRCODE = '42501';
    END IF;
    selected_tenant := sklegal_identity.current_tenant_id();
    PERFORM 1
    FROM sklegal_audit.chain_heads
    WHERE tenant_id = selected_tenant
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'target audit event is unavailable' USING ERRCODE = '55000';
    END IF;
    SELECT * INTO current_watermark
    FROM sklegal_audit.projection_watermarks
    WHERE tenant_id = selected_tenant AND projection = p_projection
    FOR UPDATE;
    had_current := FOUND;
    IF had_current THEN
        IF current_watermark.event_sequence <> p_expected_sequence
           OR current_watermark.event_sha256 IS DISTINCT FROM p_expected_event_sha256 THEN
            RAISE EXCEPTION 'projection watermark is stale' USING ERRCODE = '40001';
        END IF;
        IF current_watermark.event_sequence = p_new_sequence
           AND current_watermark.event_sha256 = p_new_event_sha256 THEN
            RETURN false;
        END IF;
    ELSIF p_expected_sequence <> 0 OR p_expected_event_sha256 IS NOT NULL THEN
        RAISE EXCEPTION 'projection watermark is stale' USING ERRCODE = '40001';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM sklegal_audit.events
        WHERE tenant_id = selected_tenant
          AND event_sequence = p_new_sequence
          AND event_sha256 = p_new_event_sha256
    ) OR (had_current AND p_new_sequence <= current_watermark.event_sequence) THEN
        RAISE EXCEPTION 'target audit event is unavailable' USING ERRCODE = '55000';
    END IF;
    IF had_current THEN
        UPDATE sklegal_audit.projection_watermarks
        SET event_sequence = p_new_sequence,
            event_sha256 = p_new_event_sha256,
            updated_at = clock_timestamp()
        WHERE tenant_id = selected_tenant AND projection = p_projection;
    ELSE
        INSERT INTO sklegal_audit.projection_watermarks (
            tenant_id, projection, event_sequence, event_sha256
        ) VALUES (
            selected_tenant, p_projection, p_new_sequence, p_new_event_sha256
        );
    END IF;
    RETURN true;
END;
$function$;

CREATE INDEX audit_event_run_sequence_idx
ON sklegal_audit.events (tenant_id, run_id, event_sequence);
CREATE INDEX audit_outbox_pending_idx
ON sklegal_audit.outbox (tenant_id, available_at, event_sequence)
WHERE delivered_at IS NULL;

REVOKE ALL ON sklegal_audit.chain_heads,
    sklegal_audit.outbox,
    sklegal_audit.outbox_deliveries,
    sklegal_audit.projection_watermarks,
    sklegal_audit.rollback_guard FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_audit.attributes_are_safe(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_audit.canonical_json_text(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_audit.payload_sha256(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_audit.canonical_timestamp(timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_audit.canonical_event_payload(
    uuid, uuid, uuid, uuid, uuid, uuid, text, text, text, text, text, text,
    uuid, uuid, uuid, text, text, timestamptz, jsonb, bigint,
    sklegal_legal.sha256_digest, timestamptz
) FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_audit.event_row_matches_payload(
    sklegal_audit.events
) FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_audit.require_controlled_writer() FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_audit.validate_event_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_audit.append_event(
    uuid, uuid, uuid, uuid, uuid, uuid, text, text, text, text, text, text,
    uuid, uuid, uuid, text, text, timestamptz, jsonb
) FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_audit.verify_current_tenant_chain() FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_audit.record_outbox_delivery(
    uuid, text, uuid
) FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_audit.advance_projection_watermark(
    text, bigint, sklegal_legal.sha256_digest, bigint,
    sklegal_legal.sha256_digest
) FROM PUBLIC;

-- sklegal:down
DO $audit_rollback_guard$
BEGIN
    IF EXISTS (SELECT 1 FROM sklegal_audit.rollback_guard) THEN
        RAISE EXCEPTION 'audit chain cannot be rolled back while evidence exists'
            USING ERRCODE = '55000';
    END IF;
END;
$audit_rollback_guard$;

DROP INDEX sklegal_audit.audit_outbox_pending_idx;
DROP INDEX sklegal_audit.audit_event_run_sequence_idx;

DROP FUNCTION sklegal_audit.advance_projection_watermark(
    text, bigint, sklegal_legal.sha256_digest, bigint,
    sklegal_legal.sha256_digest
);
DROP FUNCTION sklegal_audit.record_outbox_delivery(uuid, text, uuid);
DROP FUNCTION sklegal_audit.verify_current_tenant_chain();
DROP FUNCTION sklegal_audit.append_event(
    uuid, uuid, uuid, uuid, uuid, uuid, text, text, text, text, text, text,
    uuid, uuid, uuid, text, text, timestamptz, jsonb
);

DROP POLICY audit_watermark_controlled_update
ON sklegal_audit.projection_watermarks;
DROP POLICY audit_watermark_controlled_insert
ON sklegal_audit.projection_watermarks;
DROP POLICY audit_watermark_select ON sklegal_audit.projection_watermarks;
DROP POLICY audit_delivery_controlled_insert
ON sklegal_audit.outbox_deliveries;
DROP POLICY audit_delivery_select ON sklegal_audit.outbox_deliveries;
DROP POLICY audit_outbox_controlled_update ON sklegal_audit.outbox;
DROP POLICY audit_outbox_controlled_insert ON sklegal_audit.outbox;
DROP POLICY audit_outbox_select ON sklegal_audit.outbox;
DROP POLICY audit_chain_head_controlled_update ON sklegal_audit.chain_heads;
DROP POLICY audit_chain_head_controlled_insert ON sklegal_audit.chain_heads;
DROP POLICY audit_chain_head_select ON sklegal_audit.chain_heads;
DROP POLICY audit_integrity_select ON sklegal_audit.events;
DROP POLICY audit_controlled_insert ON sklegal_audit.events;

DROP TABLE sklegal_audit.projection_watermarks;
DROP TABLE sklegal_audit.outbox_deliveries;
DROP TABLE sklegal_audit.outbox;
DROP TABLE sklegal_audit.chain_heads;
DROP TABLE sklegal_audit.rollback_guard;

DROP TRIGGER audit_events_validate_insert ON sklegal_audit.events;
DROP TRIGGER audit_events_controlled_insert ON sklegal_audit.events;
DROP FUNCTION sklegal_audit.validate_event_insert();
DROP FUNCTION sklegal_audit.require_controlled_writer();
DROP FUNCTION sklegal_audit.event_row_matches_payload(sklegal_audit.events);
DROP FUNCTION sklegal_audit.canonical_event_payload(
    uuid, uuid, uuid, uuid, uuid, uuid, text, text, text, text, text, text,
    uuid, uuid, uuid, text, text, timestamptz, jsonb, bigint,
    sklegal_legal.sha256_digest, timestamptz
);
DROP FUNCTION sklegal_audit.canonical_timestamp(timestamptz);
DROP FUNCTION sklegal_audit.payload_sha256(jsonb);
DROP FUNCTION sklegal_audit.canonical_json_text(jsonb);
DROP FUNCTION sklegal_audit.attributes_are_safe(jsonb);

ALTER TABLE sklegal_audit.events
    DROP CONSTRAINT audit_event_predecessor_fk,
    DROP CONSTRAINT audit_event_tenant_sequence_unique,
    DROP CONSTRAINT audit_canonical_payload_object,
    DROP CONSTRAINT audit_attributes_object,
    DROP CONSTRAINT audit_reason_code_shape,
    DROP CONSTRAINT audit_outcome_closed,
    DROP CONSTRAINT audit_boundary_closed,
    DROP CONSTRAINT audit_trace_flags_shape,
    DROP CONSTRAINT audit_span_id_shape,
    DROP CONSTRAINT audit_trace_id_shape,
    DROP CONSTRAINT audit_event_sequence_positive,
    DROP CONSTRAINT events_resource_kind_check,
    DROP COLUMN canonical_payload,
    DROP COLUMN attributes,
    DROP COLUMN authorization_decision_id,
    DROP COLUMN reason_code,
    DROP COLUMN outcome,
    DROP COLUMN boundary,
    DROP COLUMN trace_flags,
    DROP COLUMN span_id,
    DROP COLUMN trace_id,
    DROP COLUMN run_id,
    DROP COLUMN event_sequence,
    ALTER COLUMN policy_decision_id TYPE text USING policy_decision_id::text,
    ALTER COLUMN correlation_id TYPE text USING correlation_id::text,
    ADD CONSTRAINT events_resource_kind_check CHECK (
        length(btrim(resource_kind)) BETWEEN 1 AND 100
    ),
    ADD CONSTRAINT events_correlation_id_check CHECK (
        length(correlation_id) BETWEEN 8 AND 255
    );

CREATE FUNCTION sklegal_audit.reject_insert_until_chain_writer()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    RAISE EXCEPTION 'audit insertion is unavailable until the SKL-S1-05 chain writer is installed'
        USING ERRCODE = '55000';
END;
$function$;

CREATE TRIGGER audit_writer_not_installed
BEFORE INSERT ON sklegal_audit.events
FOR EACH ROW EXECUTE FUNCTION sklegal_audit.reject_insert_until_chain_writer();
