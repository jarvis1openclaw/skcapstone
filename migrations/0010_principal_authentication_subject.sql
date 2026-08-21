-- sklegal:up
ALTER TABLE sklegal_identity.principals
    ADD COLUMN authentication_subject text;

ALTER TABLE sklegal_identity.principals
    ADD CONSTRAINT principal_authentication_subject_shape CHECK (
        authentication_subject IS NULL
        OR length(btrim(authentication_subject)) BETWEEN 1 AND 512
    );

CREATE INDEX principal_authentication_subject_idx
ON sklegal_identity.principals (tenant_id, authentication_subject)
WHERE authentication_subject IS NOT NULL;

DROP FUNCTION sklegal_identity.capability_principal_snapshot(uuid, uuid);

CREATE FUNCTION sklegal_identity.capability_principal_snapshot(
    p_tenant_id uuid,
    p_principal_id uuid
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    current_principal sklegal_identity.principals%ROWTYPE;
    payload jsonb;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR sklegal_identity.current_tenant_id() IS DISTINCT FROM p_tenant_id
       OR sklegal_identity.current_principal_id() IS DISTINCT FROM p_principal_id
       OR NOT sklegal_identity.has_tenant_membership(p_tenant_id) THEN
        RAISE EXCEPTION 'capability principal scope is unauthorized'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO STRICT current_principal
    FROM sklegal_identity.principals
    WHERE tenant_id = p_tenant_id AND id = p_principal_id;
    IF current_principal.authentication_subject IS NULL THEN
        RAISE EXCEPTION 'capability principal subject is unavailable'
            USING ERRCODE = '42501';
    END IF;
    payload := jsonb_build_object(
        'principal_id', current_principal.id,
        'principal_type', current_principal.principal_kind,
        'subject', current_principal.authentication_subject,
        'tenant_id', current_principal.tenant_id,
        'status', current_principal.status,
        'version', current_principal.version
    );
    RETURN jsonb_build_object(
        'revision', sklegal_audit.payload_sha256(payload),
        'principal', jsonb_build_object(
            'principal_id', current_principal.id,
            'principal_type', current_principal.principal_kind,
            'subject', current_principal.authentication_subject,
            'tenant_id', current_principal.tenant_id
        ),
        'active', current_principal.status = 'active'
    );
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_identity.capability_principal_snapshot(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sklegal_identity.capability_principal_snapshot(uuid, uuid) TO sklegal_runtime;

-- sklegal:down
DROP FUNCTION sklegal_identity.capability_principal_snapshot(uuid, uuid);

DROP INDEX sklegal_identity.principal_authentication_subject_idx;
ALTER TABLE sklegal_identity.principals
    DROP CONSTRAINT principal_authentication_subject_shape,
    DROP COLUMN authentication_subject;

CREATE FUNCTION sklegal_identity.capability_principal_snapshot(
    p_tenant_id uuid,
    p_principal_id uuid
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    current_principal sklegal_identity.principals%ROWTYPE;
    payload jsonb;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR sklegal_identity.current_tenant_id() IS DISTINCT FROM p_tenant_id
       OR sklegal_identity.current_principal_id() IS DISTINCT FROM p_principal_id
       OR NOT sklegal_identity.has_tenant_membership(p_tenant_id) THEN
        RAISE EXCEPTION 'capability principal scope is unauthorized'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO STRICT current_principal
    FROM sklegal_identity.principals
    WHERE tenant_id = p_tenant_id AND id = p_principal_id;
    payload := jsonb_build_object(
        'principal_id', current_principal.id,
        'principal_type', current_principal.principal_kind,
        'subject', current_principal.id::text,
        'tenant_id', current_principal.tenant_id,
        'status', current_principal.status,
        'version', current_principal.version
    );
    RETURN jsonb_build_object(
        'revision', sklegal_audit.payload_sha256(payload),
        'principal', jsonb_build_object(
            'principal_id', current_principal.id,
            'principal_type', current_principal.principal_kind,
            'subject', current_principal.id::text,
            'tenant_id', current_principal.tenant_id
        ),
        'active', current_principal.status = 'active'
    );
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_identity.capability_principal_snapshot(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sklegal_identity.capability_principal_snapshot(uuid, uuid) TO sklegal_runtime;
