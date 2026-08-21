-- sklegal:up
CREATE TABLE sklegal_identity.capability_revocations (
    tenant_id uuid NOT NULL REFERENCES sklegal_identity.tenants(id),
    credential_digest sklegal_legal.sha256_digest NOT NULL,
    revoked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    revoked_by uuid NOT NULL,
    rationale text NOT NULL CHECK (length(btrim(rationale)) BETWEEN 1 AND 512),
    PRIMARY KEY (tenant_id, credential_digest),
    FOREIGN KEY (tenant_id, revoked_by)
        REFERENCES sklegal_identity.principals(tenant_id, id)
);

CREATE TABLE sklegal_identity.capability_replay_reservations (
    tenant_id uuid NOT NULL REFERENCES sklegal_identity.tenants(id),
    credential_digest sklegal_legal.sha256_digest NOT NULL,
    decision_id uuid NOT NULL,
    reserved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, credential_digest),
    UNIQUE (tenant_id, decision_id),
    CHECK (expires_at > reserved_at)
);

ALTER TABLE sklegal_identity.capability_revocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_identity.capability_revocations FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_identity.capability_replay_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_identity.capability_replay_reservations FORCE ROW LEVEL SECURITY;

DO $capauth_domain_id_triggers$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_identity.capability_revocations'::regclass,
        'sklegal_identity.capability_replay_reservations'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER domain_id_non_nil BEFORE INSERT OR UPDATE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_nil_domain_ids()',
            target_table
        );
    END LOOP;
END;
$capauth_domain_id_triggers$;

CREATE POLICY capability_revocation_select
ON sklegal_identity.capability_revocations
FOR SELECT USING (
    tenant_id = sklegal_identity.current_tenant_id()
    AND sklegal_identity.has_tenant_membership(tenant_id)
);
CREATE POLICY capability_revocation_controlled_write
ON sklegal_identity.capability_revocations
FOR INSERT WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
);
CREATE POLICY capability_replay_select
ON sklegal_identity.capability_replay_reservations
FOR SELECT USING (
    tenant_id = sklegal_identity.current_tenant_id()
    AND sklegal_identity.has_tenant_membership(tenant_id)
);
CREATE POLICY capability_replay_controlled_write
ON sklegal_identity.capability_replay_reservations
FOR INSERT WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
);

CREATE FUNCTION sklegal_identity.revoke_capability(
    p_tenant_id uuid,
    p_credential_digest sklegal_legal.sha256_digest,
    p_revoked_by uuid,
    p_rationale text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR sklegal_identity.current_tenant_id() IS DISTINCT FROM p_tenant_id
       OR sklegal_identity.current_principal_id() IS DISTINCT FROM p_revoked_by
       OR NOT sklegal_identity.has_tenant_membership(p_tenant_id)
       OR p_credential_digest IS NULL
       OR p_rationale IS NULL
       OR length(btrim(p_rationale)) NOT BETWEEN 1 AND 512 THEN
        RAISE EXCEPTION 'capability revocation scope is unauthorized'
            USING ERRCODE = '42501';
    END IF;
    INSERT INTO sklegal_identity.capability_revocations (
        tenant_id, credential_digest, revoked_by, rationale
    ) VALUES (p_tenant_id, p_credential_digest, p_revoked_by, p_rationale);
END;
$function$;

CREATE FUNCTION sklegal_identity.reserve_capability(
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

CREATE FUNCTION sklegal_identity.capability_revocation_snapshot(
    p_tenant_id uuid,
    p_credential_digests sklegal_legal.sha256_digest[]
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    revoked jsonb;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR sklegal_identity.current_tenant_id() IS DISTINCT FROM p_tenant_id
       OR NOT sklegal_identity.has_tenant_membership(p_tenant_id)
       OR p_credential_digests IS NULL THEN
        RAISE EXCEPTION 'capability revocation scope is unauthorized'
            USING ERRCODE = '42501';
    END IF;
    SELECT COALESCE(jsonb_agg(credential_digest ORDER BY credential_digest), '[]'::jsonb)
    INTO revoked
    FROM sklegal_identity.capability_revocations
    WHERE tenant_id = p_tenant_id
      AND credential_digest = ANY (p_credential_digests);
    RETURN jsonb_build_object(
        'revision', sklegal_audit.payload_sha256(revoked),
        'revoked_credential_digests', revoked
    );
END;
$function$;

REVOKE ALL ON TABLE sklegal_identity.capability_revocations,
    sklegal_identity.capability_replay_reservations FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_identity.revoke_capability(uuid, sklegal_legal.sha256_digest, uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_identity.reserve_capability(uuid, sklegal_legal.sha256_digest, uuid, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_identity.capability_revocation_snapshot(uuid, sklegal_legal.sha256_digest[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sklegal_identity.revoke_capability(uuid, sklegal_legal.sha256_digest, uuid, text) TO sklegal_runtime;
GRANT EXECUTE ON FUNCTION sklegal_identity.reserve_capability(uuid, sklegal_legal.sha256_digest, uuid, timestamptz) TO sklegal_runtime;
GRANT EXECUTE ON FUNCTION sklegal_identity.capability_revocation_snapshot(uuid, sklegal_legal.sha256_digest[]) TO sklegal_runtime;

-- sklegal:down
DROP FUNCTION sklegal_identity.capability_revocation_snapshot(uuid, sklegal_legal.sha256_digest[]);
DROP FUNCTION sklegal_identity.reserve_capability(uuid, sklegal_legal.sha256_digest, uuid, timestamptz);
DROP FUNCTION sklegal_identity.revoke_capability(uuid, sklegal_legal.sha256_digest, uuid, text);
DROP POLICY capability_replay_controlled_write ON sklegal_identity.capability_replay_reservations;
DROP POLICY capability_replay_select ON sklegal_identity.capability_replay_reservations;
DROP POLICY capability_revocation_controlled_write ON sklegal_identity.capability_revocations;
DROP POLICY capability_revocation_select ON sklegal_identity.capability_revocations;
ALTER TABLE sklegal_identity.capability_replay_reservations NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_identity.capability_replay_reservations DISABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_identity.capability_revocations NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_identity.capability_revocations DISABLE ROW LEVEL SECURITY;
DROP TABLE sklegal_identity.capability_replay_reservations;
DROP TABLE sklegal_identity.capability_revocations;
