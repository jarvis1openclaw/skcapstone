-- sklegal:up
CREATE SCHEMA sklegal_identity;
CREATE SCHEMA sklegal_legal;
CREATE SCHEMA sklegal_integrations;
CREATE SCHEMA sklegal_workflow;
CREATE SCHEMA sklegal_audit;

CREATE DOMAIN sklegal_legal.record_version AS bigint
CHECK (VALUE >= 1);

CREATE DOMAIN sklegal_legal.sha256_digest AS text
CHECK (VALUE ~ '^[0-9a-f]{64}$');

CREATE DOMAIN sklegal_legal.data_classification AS text
CHECK (VALUE IN (
    'public',
    'internal',
    'confidential',
    'privileged_work_product',
    'highly_restricted'
));

CREATE DOMAIN sklegal_legal.record_completeness AS text
CHECK (VALUE IN ('incomplete', 'complete', 'unresolved'));

CREATE FUNCTION sklegal_identity.encrypted_payload_is_complete(
    encrypted_payload bytea,
    key_reference text,
    algorithm text,
    encrypted_timestamp timestamptz
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
RETURN COALESCE(
    (
        encrypted_payload IS NULL
        AND key_reference IS NULL
        AND algorithm IS NULL
        AND encrypted_timestamp IS NULL
    )
    OR
    (
        encrypted_payload IS NOT NULL
        AND octet_length(encrypted_payload) >= 16
        AND key_reference IS NOT NULL
        AND key_reference ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{2,255}$'
        AND algorithm IN ('aes-256-gcm', 'xchacha20-poly1305')
        AND encrypted_timestamp IS NOT NULL
    ),
    false
);

CREATE FUNCTION sklegal_legal.reject_nil_domain_ids()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_each_text(pg_catalog.to_jsonb(NEW)) AS field(name, value)
        JOIN pg_catalog.pg_attribute AS attribute
          ON attribute.attrelid = TG_RELID
         AND attribute.attname = field.name
         AND attribute.atttypid = 'uuid'::pg_catalog.regtype
        WHERE field.value = '00000000-0000-0000-0000-000000000000'
    ) THEN
        RAISE EXCEPTION 'domain identifiers cannot be nil UUIDs'
            USING ERRCODE = '22023';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE FUNCTION sklegal_legal.typed_json_value_is_valid(
    value_type text,
    asserted_value jsonb
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
RETURN COALESCE(
    CASE value_type
        WHEN 'string' THEN jsonb_typeof(asserted_value) = 'string'
        WHEN 'integer' THEN
            jsonb_typeof(asserted_value) = 'number'
            AND asserted_value::text ~ '^-?(0|[1-9][0-9]*)$'
        WHEN 'number' THEN jsonb_typeof(asserted_value) = 'number'
        WHEN 'boolean' THEN jsonb_typeof(asserted_value) = 'boolean'
        WHEN 'null' THEN jsonb_typeof(asserted_value) = 'null'
        ELSE false
    END,
    false
);

CREATE FUNCTION sklegal_legal.initialize_record_audit()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    now_at timestamptz := clock_timestamp();
BEGIN
    NEW.version := 1;
    NEW.created_at := now_at;
    NEW.updated_at := now_at;
    RETURN NEW;
END;
$function$;

CREATE FUNCTION sklegal_legal.enforce_optimistic_record_update()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF to_jsonb(NEW)->'id' IS DISTINCT FROM to_jsonb(OLD)->'id'
       OR to_jsonb(NEW)->'database_role' IS DISTINCT FROM to_jsonb(OLD)->'database_role'
       OR to_jsonb(NEW)->'tenant_id' IS DISTINCT FROM to_jsonb(OLD)->'tenant_id'
       OR to_jsonb(NEW)->'matter_id' IS DISTINCT FROM to_jsonb(OLD)->'matter_id'
       OR to_jsonb(NEW)->'principal_id' IS DISTINCT FROM to_jsonb(OLD)->'principal_id'
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'record identity and creation time are immutable'
            USING ERRCODE = '22000';
    END IF;
    IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
        RAISE EXCEPTION 'optimistic version conflict: expected %', OLD.version + 1
            USING ERRCODE = '40001';
    END IF;
    NEW.updated_at := GREATEST(OLD.updated_at, clock_timestamp());
    RETURN NEW;
END;
$function$;

CREATE FUNCTION sklegal_legal.initialize_created_at()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$function$;

CREATE FUNCTION sklegal_legal.initialize_validation_result()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    now_at timestamptz := clock_timestamp();
    actor_id uuid;
BEGIN
    actor_id := sklegal_identity.current_principal_id();
    IF actor_id IS NULL
       OR NEW.validator_principal_id IS DISTINCT FROM actor_id THEN
        RAISE EXCEPTION 'validation actor must be the current bound principal'
            USING ERRCODE = '42501';
    END IF;
    NEW.validator_principal_id := actor_id;
    IF NEW.validated_at > now_at THEN
        RAISE EXCEPTION 'validation time cannot be in the future'
            USING ERRCODE = '22007';
    END IF;
    NEW.version := 1;
    NEW.created_at := NEW.validated_at;
    NEW.updated_at := now_at;
    RETURN NEW;
END;
$function$;

CREATE FUNCTION sklegal_legal.initialize_execution_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    NEW.version := 1;
    NEW.created_at := NEW.occurred_at;
    NEW.updated_at := GREATEST(NEW.occurred_at, clock_timestamp());
    RETURN NEW;
END;
$function$;

CREATE FUNCTION sklegal_legal.initialize_execution_receipt()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    NEW.version := 1;
    NEW.created_at := NEW.received_at;
    NEW.updated_at := GREATEST(NEW.verified_at, clock_timestamp());
    RETURN NEW;
END;
$function$;

CREATE FUNCTION sklegal_legal.reject_record_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF TG_OP = 'UPDATE'
       AND current_user = 'sklegal_migrator'
       AND session_user <> current_user
       AND to_jsonb(NEW) ? 'version'
       AND (to_jsonb(NEW) - ARRAY['version', 'updated_at'])
           = (to_jsonb(OLD) - ARRAY['version', 'updated_at'])
       AND (to_jsonb(NEW)->>'version')::bigint
           = (to_jsonb(OLD)->>'version')::bigint + 1 THEN
        NEW.updated_at := GREATEST(OLD.updated_at, clock_timestamp());
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'record is append-only'
        USING ERRCODE = '55000';
END;
$function$;

-- sklegal:down
DROP FUNCTION sklegal_legal.reject_nil_domain_ids();
DROP SCHEMA sklegal_audit CASCADE;
DROP SCHEMA sklegal_workflow CASCADE;
DROP SCHEMA sklegal_integrations CASCADE;
DROP SCHEMA sklegal_legal CASCADE;
DROP SCHEMA sklegal_identity CASCADE;
