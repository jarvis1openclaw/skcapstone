-- sklegal:up
CREATE OR REPLACE FUNCTION sklegal_identity.reserve_capability(
    p_tenant_id uuid,
    p_credential_digest sklegal_legal.sha256_digest,
    p_decision_id uuid,
    p_expires_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR sklegal_identity.current_tenant_id() IS DISTINCT FROM p_tenant_id
       OR NOT sklegal_identity.has_tenant_membership(p_tenant_id)
       OR p_credential_digest IS NULL
       OR p_decision_id IS NULL
       OR p_expires_at IS NULL
       OR p_expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'capability replay scope is unauthorized'
            USING ERRCODE = '42501';
    END IF;
    DELETE FROM sklegal_identity.capability_replay_reservations
    WHERE tenant_id = p_tenant_id
      AND credential_digest = p_credential_digest
      AND expires_at <= clock_timestamp();
    INSERT INTO sklegal_identity.capability_replay_reservations (
        tenant_id, credential_digest, decision_id, expires_at
    ) VALUES (p_tenant_id, p_credential_digest, p_decision_id, p_expires_at)
    ON CONFLICT (tenant_id, credential_digest) DO NOTHING;
    RETURN FOUND;
END;
$function$;

CREATE FUNCTION sklegal_identity.prune_expired_capability_replay_reservations(
    p_tenant_id uuid
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    pruned bigint;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR sklegal_identity.current_tenant_id() IS DISTINCT FROM p_tenant_id
       OR NOT sklegal_identity.has_tenant_membership(p_tenant_id) THEN
        RAISE EXCEPTION 'capability replay prune scope is unauthorized'
            USING ERRCODE = '42501';
    END IF;
    DELETE FROM sklegal_identity.capability_replay_reservations
    WHERE tenant_id = p_tenant_id
      AND expires_at <= clock_timestamp();
    GET DIAGNOSTICS pruned = ROW_COUNT;
    RETURN pruned;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_identity.prune_expired_capability_replay_reservations(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sklegal_identity.prune_expired_capability_replay_reservations(uuid) TO sklegal_runtime;

CREATE POLICY capability_replay_controlled_delete
ON sklegal_identity.capability_replay_reservations
FOR DELETE USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
);

-- sklegal:down
DROP POLICY capability_replay_controlled_delete ON sklegal_identity.capability_replay_reservations;
DROP FUNCTION sklegal_identity.prune_expired_capability_replay_reservations(uuid);

CREATE OR REPLACE FUNCTION sklegal_identity.reserve_capability(
    p_tenant_id uuid,
    p_credential_digest sklegal_legal.sha256_digest,
    p_decision_id uuid,
    p_expires_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR sklegal_identity.current_tenant_id() IS DISTINCT FROM p_tenant_id
       OR NOT sklegal_identity.has_tenant_membership(p_tenant_id)
       OR p_credential_digest IS NULL
       OR p_decision_id IS NULL
       OR p_expires_at IS NULL
       OR p_expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'capability replay scope is unauthorized'
            USING ERRCODE = '42501';
    END IF;
    INSERT INTO sklegal_identity.capability_replay_reservations (
        tenant_id, credential_digest, decision_id, expires_at
    ) VALUES (p_tenant_id, p_credential_digest, p_decision_id, p_expires_at)
    ON CONFLICT (tenant_id, credential_digest) DO NOTHING;
    RETURN FOUND;
END;
$function$;
