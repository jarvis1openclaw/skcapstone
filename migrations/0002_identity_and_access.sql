-- sklegal:up
CREATE TABLE sklegal_identity.tenants (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL UNIQUE,
    slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{2,62}$'),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 512),
    status text NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'active', 'suspended', 'closed')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (id = tenant_id),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_identity.principals (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL REFERENCES sklegal_identity.tenants(id),
    principal_kind text NOT NULL
        CHECK (principal_kind IN ('human', 'agent', 'service', 'connector')),
    display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 512),
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'revoked')),
    encrypted_profile bytea,
    encryption_key_ref text,
    encryption_algorithm text,
    encrypted_at timestamptz,
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, id),
    CHECK (updated_at >= created_at),
    CHECK (sklegal_identity.encrypted_payload_is_complete(
        encrypted_profile,
        encryption_key_ref,
        encryption_algorithm,
        encrypted_at
    ))
);

CREATE TABLE sklegal_identity.database_role_bindings (
    database_role name PRIMARY KEY,
    tenant_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    active boolean NOT NULL DEFAULT true,
    tenant_active boolean NOT NULL DEFAULT false,
    principal_active boolean NOT NULL DEFAULT false,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_id, principal_id),
    FOREIGN KEY (tenant_id, principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (updated_at >= created_at)
);

CREATE FUNCTION sklegal_identity.synchronize_database_role_binding_status()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    tenant_status text;
    principal_status text;
BEGIN
    SELECT tenant.status, principal.status
    INTO tenant_status, principal_status
    FROM sklegal_identity.tenants AS tenant
    JOIN sklegal_identity.principals AS principal
      ON principal.tenant_id = tenant.id
    WHERE tenant.id = NEW.tenant_id AND principal.id = NEW.principal_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'database role binding identity does not exist'
            USING ERRCODE = '23503';
    END IF;
    NEW.tenant_active := tenant_status = 'active';
    NEW.principal_active := principal_status = 'active';
    RETURN NEW;
END;
$function$;

CREATE TRIGGER database_role_binding_identity_status
BEFORE INSERT OR UPDATE OF tenant_id, principal_id
ON sklegal_identity.database_role_bindings
FOR EACH ROW EXECUTE FUNCTION
    sklegal_identity.synchronize_database_role_binding_status();

CREATE FUNCTION sklegal_identity.propagate_authorization_status()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF TG_TABLE_NAME = 'tenants' THEN
        UPDATE sklegal_identity.database_role_bindings
        SET tenant_active = CASE WHEN NEW.status = 'active' THEN true ELSE false END,
            version = version + 1
        WHERE tenant_id = NEW.id;
    ELSE
        UPDATE sklegal_identity.database_role_bindings
        SET principal_active = CASE WHEN NEW.status = 'active' THEN true ELSE false END,
            version = version + 1
        WHERE tenant_id = NEW.tenant_id AND principal_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE FUNCTION sklegal_identity.enforce_identity_status_transition()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.status = OLD.status THEN
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'tenants' AND (
        (OLD.status = 'proposed' AND NEW.status IN ('active', 'closed'))
        OR (OLD.status = 'active' AND NEW.status IN ('suspended', 'closed'))
        OR (OLD.status = 'suspended' AND NEW.status IN ('active', 'closed'))
    ) THEN
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'principals' AND (
        (OLD.status = 'active' AND NEW.status IN ('suspended', 'revoked'))
        OR (OLD.status = 'suspended' AND NEW.status IN ('active', 'revoked'))
    ) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'invalid identity status transition: % -> %',
        OLD.status, NEW.status USING ERRCODE = '23514';
END;
$function$;

CREATE TRIGGER tenant_identity_status_transition
BEFORE UPDATE OF status ON sklegal_identity.tenants
FOR EACH ROW EXECUTE FUNCTION
    sklegal_identity.enforce_identity_status_transition();

CREATE TRIGGER principal_identity_status_transition
BEFORE UPDATE OF status ON sklegal_identity.principals
FOR EACH ROW EXECUTE FUNCTION
    sklegal_identity.enforce_identity_status_transition();

CREATE TRIGGER tenant_authorization_status_propagation
AFTER UPDATE OF status ON sklegal_identity.tenants
FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)
EXECUTE FUNCTION sklegal_identity.propagate_authorization_status();

CREATE TRIGGER principal_authorization_status_propagation
AFTER UPDATE OF status ON sklegal_identity.principals
FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)
EXECUTE FUNCTION sklegal_identity.propagate_authorization_status();

CREATE FUNCTION sklegal_identity.reject_unsafe_database_role_binding()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    selected_role record;
BEGIN
    SELECT oid, rolsuper, rolbypassrls, rolcanlogin, rolcreaterole,
           rolcreatedb, rolreplication, rolinherit
    INTO selected_role
    FROM pg_roles
    WHERE rolname = NEW.database_role;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'database role does not exist'
            USING ERRCODE = '23503';
    END IF;
    IF selected_role.rolsuper OR selected_role.rolbypassrls
       OR NOT selected_role.rolcanlogin OR selected_role.rolcreaterole
       OR selected_role.rolcreatedb OR selected_role.rolreplication
       OR selected_role.rolinherit THEN
        RAISE EXCEPTION 'bound runtime role does not satisfy the exact safe role profile'
            USING ERRCODE = '42501';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_auth_members
        WHERE member = selected_role.oid OR roleid = selected_role.oid
    ) THEN
        RAISE EXCEPTION 'bound runtime role cannot participate in any role membership edge'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER database_role_binding_safety
BEFORE INSERT OR UPDATE ON sklegal_identity.database_role_bindings
FOR EACH ROW EXECUTE FUNCTION sklegal_identity.reject_unsafe_database_role_binding();

CREATE TABLE sklegal_identity.tenant_memberships (
    tenant_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    membership_role text NOT NULL
        CHECK (membership_role IN ('member', 'reviewer', 'administrator', 'trustee')),
    active boolean NOT NULL DEFAULT true,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, principal_id),
    FOREIGN KEY (tenant_id, principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (updated_at >= created_at)
);

DO $triggers$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_identity.tenants'::regclass,
        'sklegal_identity.principals'::regclass,
        'sklegal_identity.database_role_bindings'::regclass,
        'sklegal_identity.tenant_memberships'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER initialize_record '
            'BEFORE INSERT ON %s FOR EACH ROW EXECUTE FUNCTION '
            'sklegal_legal.initialize_record_audit()', target_table
        );
        EXECUTE format(
            'CREATE TRIGGER domain_id_non_nil '
            'BEFORE INSERT OR UPDATE ON %s FOR EACH ROW EXECUTE FUNCTION '
            'sklegal_legal.reject_nil_domain_ids()', target_table
        );
        EXECUTE format(
            'CREATE TRIGGER optimistic_record_update '
            'BEFORE UPDATE ON %s FOR EACH ROW EXECUTE FUNCTION '
            'sklegal_legal.enforce_optimistic_record_update()', target_table
        );
    END LOOP;
END;
$triggers$;

CREATE FUNCTION sklegal_identity.current_tenant_id()
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
RETURN (
    SELECT binding.tenant_id
    FROM sklegal_identity.database_role_bindings AS binding
    WHERE binding.database_role = session_user
      AND binding.active
      AND binding.tenant_active
      AND binding.principal_active
);

CREATE FUNCTION sklegal_identity.current_principal_id()
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
RETURN (
    SELECT binding.principal_id
    FROM sklegal_identity.database_role_bindings AS binding
    WHERE binding.database_role = session_user
      AND binding.active
      AND binding.tenant_active
      AND binding.principal_active
);

CREATE FUNCTION sklegal_identity.has_tenant_membership(target_tenant_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
RETURN COALESCE(
    target_tenant_id = sklegal_identity.current_tenant_id()
    AND EXISTS (
        SELECT 1
        FROM sklegal_identity.tenant_memberships AS membership
        WHERE membership.tenant_id = target_tenant_id
          AND membership.principal_id = sklegal_identity.current_principal_id()
          AND membership.active
    ),
    false
);

-- sklegal:down
DROP FUNCTION sklegal_identity.has_tenant_membership(uuid);
DROP FUNCTION sklegal_identity.current_principal_id();
DROP FUNCTION sklegal_identity.current_tenant_id();
DROP TABLE sklegal_identity.tenant_memberships;
DROP TRIGGER principal_identity_status_transition ON sklegal_identity.principals;
DROP TRIGGER tenant_identity_status_transition ON sklegal_identity.tenants;
DROP FUNCTION sklegal_identity.enforce_identity_status_transition();
DROP TRIGGER principal_authorization_status_propagation ON sklegal_identity.principals;
DROP TRIGGER tenant_authorization_status_propagation ON sklegal_identity.tenants;
DROP FUNCTION sklegal_identity.propagate_authorization_status();
DROP TRIGGER database_role_binding_safety ON sklegal_identity.database_role_bindings;
DROP TRIGGER database_role_binding_identity_status ON sklegal_identity.database_role_bindings;
DROP FUNCTION sklegal_identity.synchronize_database_role_binding_status();
DROP FUNCTION sklegal_identity.reject_unsafe_database_role_binding();
DROP TABLE sklegal_identity.database_role_bindings;
DROP TABLE sklegal_identity.principals;
DROP TABLE sklegal_identity.tenants;
