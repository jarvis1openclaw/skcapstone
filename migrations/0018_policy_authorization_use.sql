-- sklegal:up
CREATE TABLE sklegal_identity.policy_authorization_uses (
    tenant_id uuid NOT NULL REFERENCES sklegal_identity.tenants(id),
    capauth_decision_id uuid NOT NULL,
    invocation_digest sklegal_legal.sha256_digest NOT NULL,
    reserved_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, capauth_decision_id),
    CHECK (expires_at > reserved_at)
);

ALTER TABLE sklegal_identity.policy_authorization_uses ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_identity.policy_authorization_uses FORCE ROW LEVEL SECURITY;

CREATE TRIGGER domain_id_non_nil
BEFORE INSERT OR UPDATE ON sklegal_identity.policy_authorization_uses
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_nil_domain_ids();

CREATE POLICY policy_authorization_use_select
ON sklegal_identity.policy_authorization_uses
FOR SELECT USING (
    tenant_id = sklegal_identity.current_tenant_id()
    AND sklegal_identity.has_tenant_membership(tenant_id)
);

CREATE POLICY policy_authorization_use_controlled_insert
ON sklegal_identity.policy_authorization_uses
FOR INSERT WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
);

CREATE POLICY policy_authorization_use_controlled_delete
ON sklegal_identity.policy_authorization_uses
FOR DELETE USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
);

CREATE FUNCTION sklegal_identity.reserve_policy_authorization_use(
    p_tenant_id uuid,
    p_capauth_decision_id uuid,
    p_invocation_digest sklegal_legal.sha256_digest,
    p_expires_at timestamptz,
    p_evaluated_at timestamptz
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
       OR p_capauth_decision_id IS NULL
       OR p_invocation_digest IS NULL
       OR p_expires_at IS NULL
       OR p_evaluated_at IS NULL
       OR p_expires_at <= p_evaluated_at
       OR p_evaluated_at > clock_timestamp() + interval '5 seconds' THEN
        RAISE EXCEPTION 'policy authorization-use scope is unauthorized'
            USING ERRCODE = '42501';
    END IF;
    DELETE FROM sklegal_identity.policy_authorization_uses
    WHERE tenant_id = p_tenant_id
      AND expires_at <= p_evaluated_at;
    INSERT INTO sklegal_identity.policy_authorization_uses (
        tenant_id,
        capauth_decision_id,
        invocation_digest,
        reserved_at,
        expires_at
    ) VALUES (
        p_tenant_id,
        p_capauth_decision_id,
        p_invocation_digest,
        p_evaluated_at,
        p_expires_at
    )
    ON CONFLICT (tenant_id, capauth_decision_id) DO NOTHING;
    RETURN FOUND;
END;
$function$;

REVOKE ALL ON TABLE sklegal_identity.policy_authorization_uses FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_identity.reserve_policy_authorization_use(
    uuid, uuid, sklegal_legal.sha256_digest, timestamptz, timestamptz
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sklegal_identity.reserve_policy_authorization_use(
    uuid, uuid, sklegal_legal.sha256_digest, timestamptz, timestamptz
) TO sklegal_runtime;

-- sklegal:down
DROP FUNCTION sklegal_identity.reserve_policy_authorization_use(
    uuid, uuid, sklegal_legal.sha256_digest, timestamptz, timestamptz
);
DROP POLICY policy_authorization_use_controlled_delete
ON sklegal_identity.policy_authorization_uses;
DROP POLICY policy_authorization_use_controlled_insert
ON sklegal_identity.policy_authorization_uses;
DROP POLICY policy_authorization_use_select
ON sklegal_identity.policy_authorization_uses;
ALTER TABLE sklegal_identity.policy_authorization_uses NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_identity.policy_authorization_uses DISABLE ROW LEVEL SECURITY;
DROP TABLE sklegal_identity.policy_authorization_uses;
