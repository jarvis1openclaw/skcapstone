-- sklegal:up
CREATE TABLE sklegal_legal.clients (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL REFERENCES sklegal_identity.tenants(id),
    display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 512),
    client_kind text NOT NULL CHECK (client_kind IN ('person', 'family', 'trust', 'estate', 'company', 'other')),
    status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'active', 'inactive', 'closed')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, id),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.engagements (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    client_id uuid NOT NULL,
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 512),
    scope text NOT NULL CHECK (length(btrim(scope)) > 0),
    valid_from timestamptz,
    valid_to timestamptz,
    status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'active', 'suspended', 'closed')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, id, client_id),
    FOREIGN KEY (tenant_id, client_id) REFERENCES sklegal_legal.clients(tenant_id, id),
    CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_to > valid_from),
    CHECK (status <> 'active' OR valid_from IS NOT NULL),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.matters (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    client_id uuid NOT NULL,
    engagement_id uuid NOT NULL,
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 512),
    summary text NOT NULL CHECK (length(btrim(summary)) > 0),
    status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'open', 'on_hold', 'closed', 'archived')),
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    opened_at timestamptz,
    closed_at timestamptz,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, matter_id),
    FOREIGN KEY (tenant_id, engagement_id, client_id)
        REFERENCES sklegal_legal.engagements(tenant_id, id, client_id),
    CHECK (id = matter_id),
    CHECK (closed_at IS NULL OR opened_at IS NULL OR closed_at >= opened_at),
    CHECK (status NOT IN ('open', 'on_hold') OR opened_at IS NOT NULL),
    CHECK (status <> 'proposed' OR opened_at IS NULL),
    CHECK (status NOT IN ('closed', 'archived') OR closed_at IS NOT NULL),
    CHECK (status IN ('closed', 'archived') OR closed_at IS NULL),
    CHECK (opened_at IS NULL OR opened_at <= updated_at),
    CHECK (closed_at IS NULL OR closed_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.matter_memberships (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    membership_role text NOT NULL CHECK (membership_role IN ('member', 'reviewer', 'lead', 'administrator', 'trustee')),
    active boolean NOT NULL DEFAULT true,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, principal_id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, principal_id) REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (updated_at >= created_at)
);

CREATE FUNCTION sklegal_legal.enforce_matter_status_transition()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.status = OLD.status OR (
        (OLD.status = 'proposed' AND NEW.status IN ('open', 'closed'))
        OR (OLD.status = 'open' AND NEW.status IN ('on_hold', 'closed'))
        OR (OLD.status = 'on_hold' AND NEW.status IN ('open', 'closed'))
        OR (OLD.status = 'closed' AND NEW.status IN ('open', 'archived'))
    ) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'invalid matter status transition: % -> %',
        OLD.status, NEW.status USING ERRCODE = '23514';
END;
$function$;

CREATE TRIGGER matter_status_transition
BEFORE UPDATE OF status ON sklegal_legal.matters
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.enforce_matter_status_transition();

CREATE FUNCTION sklegal_legal.has_matter_membership(target_tenant_id uuid, target_matter_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
RETURN COALESCE(
    sklegal_identity.has_tenant_membership(target_tenant_id)
    AND EXISTS (
        SELECT 1
        FROM sklegal_legal.matter_memberships AS membership
        WHERE membership.tenant_id = target_tenant_id
          AND membership.matter_id = target_matter_id
          AND membership.principal_id = sklegal_identity.current_principal_id()
          AND membership.active
    ),
    false
);

CREATE TABLE sklegal_legal.source_references (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    source_system text NOT NULL CHECK (length(btrim(source_system)) BETWEEN 1 AND 512),
    source_version text NOT NULL CHECK (length(btrim(source_version)) BETWEEN 1 AND 512),
    content_sha256 sklegal_legal.sha256_digest NOT NULL,
    locator text NOT NULL CHECK (length(btrim(locator)) > 0),
    observed_at timestamptz NOT NULL,
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    CHECK (observed_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.forums (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 512),
    jurisdiction text NOT NULL CHECK (length(btrim(jurisdiction)) BETWEEN 1 AND 512),
    forum_kind text NOT NULL CHECK (forum_kind IN ('court', 'agency', 'arbitration', 'mediation', 'other')),
    source_reference_id uuid,
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, source_reference_id) REFERENCES sklegal_legal.source_references(tenant_id, matter_id, id),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.proceedings (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 512),
    forum_id uuid,
    docket_number text CHECK (
        docket_number IS NULL
        OR length(btrim(docket_number)) BETWEEN 1 AND 512
    ),
    valid_from timestamptz,
    valid_to timestamptz,
    status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'active', 'stayed', 'disposed', 'closed')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, matter_id, forum_id) REFERENCES sklegal_legal.forums(tenant_id, matter_id, id),
    CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_to > valid_from),
    CHECK (status NOT IN ('active', 'stayed', 'disposed') OR forum_id IS NOT NULL),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.parties (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 512),
    party_kind text NOT NULL CHECK (party_kind IN ('person', 'family', 'trust', 'estate', 'company', 'other')),
    source_reference_id uuid NOT NULL,
    verification_reference_id uuid,
    status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'verified', 'disputed', 'superseded')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, source_reference_id) REFERENCES sklegal_legal.source_references(tenant_id, matter_id, id),
    CHECK ((status = 'verified') = (verification_reference_id IS NOT NULL) OR status NOT IN ('proposed', 'verified')),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.party_roles (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    party_id uuid NOT NULL,
    proceeding_id uuid,
    role text NOT NULL CHECK (role IN ('client', 'counterparty', 'trustee', 'beneficiary', 'witness', 'counsel', 'court', 'agency', 'other')),
    valid_from timestamptz,
    valid_to timestamptz,
    source_reference_id uuid NOT NULL,
    verification_reference_id uuid,
    status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'verified', 'inactive')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, party_id) REFERENCES sklegal_legal.parties(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, proceeding_id) REFERENCES sklegal_legal.proceedings(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, source_reference_id) REFERENCES sklegal_legal.source_references(tenant_id, matter_id, id),
    CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_to > valid_from),
    CHECK ((status = 'verified') = (verification_reference_id IS NOT NULL) OR status = 'inactive'),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.matter_events (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    event_type text NOT NULL CHECK (length(btrim(event_type)) BETWEEN 1 AND 512),
    description text NOT NULL CHECK (length(btrim(description)) > 0),
    occurred_at timestamptz,
    observed_at timestamptz NOT NULL,
    source_reference_id uuid NOT NULL,
    verification_reference_id uuid,
    status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'recorded', 'verified', 'superseded')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, source_reference_id) REFERENCES sklegal_legal.source_references(tenant_id, matter_id, id),
    CHECK (occurred_at IS NULL OR occurred_at <= observed_at),
    CHECK (observed_at <= updated_at),
    CHECK (status <> 'verified' OR occurred_at IS NOT NULL),
    CHECK ((status = 'verified') = (verification_reference_id IS NOT NULL) OR status NOT IN ('proposed', 'verified')),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.legacy_aliases (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    canonical_record_kind text NOT NULL CHECK (canonical_record_kind IN ('matter', 'matter_event')),
    canonical_record_id uuid NOT NULL,
    canonical_matter_event_id uuid GENERATED ALWAYS AS (
        CASE WHEN canonical_record_kind = 'matter_event' THEN canonical_record_id END
    ) STORED,
    legacy_record_kind text NOT NULL CHECK (legacy_record_kind IN ('problem', 'incident')),
    legacy_id text NOT NULL,
    legacy_slug text NOT NULL CHECK (legacy_slug ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$'),
    legacy_path text NOT NULL CHECK (
        length(legacy_path) > 0
        AND legacy_path = btrim(legacy_path)
        AND legacy_path ~ '^[^/]+(/[^/]+)*$'
        AND legacy_path !~ '\\'
        AND legacy_path !~ '(^|/)\.\.?(/|$)'
    ),
    source_system text NOT NULL DEFAULT 'hammertime' CHECK (source_system = 'hammertime'),
    source_version text NOT NULL CHECK (length(btrim(source_version)) BETWEEN 1 AND 512),
    content_sha256 sklegal_legal.sha256_digest NOT NULL,
    observed_at timestamptz NOT NULL,
    import_batch_id uuid NOT NULL,
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, source_system, legacy_record_kind, legacy_id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, matter_id, canonical_matter_event_id) REFERENCES sklegal_legal.matter_events(tenant_id, matter_id, id),
    CHECK (
        (canonical_record_kind = 'matter' AND canonical_record_id = matter_id
            AND legacy_record_kind = 'problem' AND legacy_id ~ '^PRB-[0-9]{4}-[0-9]{3,}$')
        OR
        (canonical_record_kind = 'matter_event' AND legacy_record_kind = 'incident'
            AND legacy_id ~ '^INC-[0-9]{3,}$')
    ),
    CHECK (observed_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.legal_transactions (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 512),
    description text NOT NULL CHECK (length(btrim(description)) > 0),
    effective_at timestamptz,
    status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'under_review', 'confirmed', 'disputed', 'superseded')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    CHECK (status <> 'confirmed' OR effective_at IS NOT NULL),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.transaction_party_roles (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    transaction_id uuid NOT NULL,
    party_role_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, transaction_id, party_role_id),
    FOREIGN KEY (tenant_id, matter_id, transaction_id) REFERENCES sklegal_legal.legal_transactions(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, party_role_id) REFERENCES sklegal_legal.party_roles(tenant_id, matter_id, id)
);

CREATE TABLE sklegal_legal.transaction_source_references (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    transaction_id uuid NOT NULL,
    source_reference_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, transaction_id, source_reference_id),
    FOREIGN KEY (tenant_id, matter_id, transaction_id) REFERENCES sklegal_legal.legal_transactions(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, source_reference_id) REFERENCES sklegal_legal.source_references(tenant_id, matter_id, id)
);

CREATE FUNCTION sklegal_legal.require_confirmed_transaction_source()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.status = 'confirmed' AND NOT EXISTS (
        SELECT 1 FROM sklegal_legal.transaction_source_references
        WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
          AND transaction_id = NEW.id
    ) THEN
        RAISE EXCEPTION 'confirmed transaction requires source provenance'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER confirmed_transaction_source
BEFORE INSERT OR UPDATE ON sklegal_legal.legal_transactions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_confirmed_transaction_source();

CREATE FUNCTION sklegal_legal.validate_transaction_sources()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    new_record jsonb := to_jsonb(NEW);
    old_record jsonb := to_jsonb(OLD);
    selected_tenant uuid := COALESCE(
        (new_record->>'tenant_id')::uuid, (old_record->>'tenant_id')::uuid
    );
    selected_matter uuid := COALESCE(
        (new_record->>'matter_id')::uuid, (old_record->>'matter_id')::uuid
    );
    selected_id uuid := COALESCE(
        (new_record->>'transaction_id')::uuid, (new_record->>'id')::uuid,
        (old_record->>'transaction_id')::uuid, (old_record->>'id')::uuid
    );
BEGIN
    IF EXISTS (
        SELECT 1 FROM sklegal_legal.legal_transactions
        WHERE tenant_id = selected_tenant AND matter_id = selected_matter
          AND id = selected_id
    ) AND NOT EXISTS (
        SELECT 1 FROM sklegal_legal.transaction_source_references
        WHERE tenant_id = selected_tenant AND matter_id = selected_matter
          AND transaction_id = selected_id
    ) THEN
        RAISE EXCEPTION 'transaction requires source provenance'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$function$;

CREATE CONSTRAINT TRIGGER transaction_sources_complete
AFTER INSERT OR UPDATE ON sklegal_legal.legal_transactions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_transaction_sources();
CREATE CONSTRAINT TRIGGER transaction_source_links_complete
AFTER INSERT OR UPDATE OR DELETE ON sklegal_legal.transaction_source_references
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_transaction_sources();

CREATE TABLE sklegal_legal.fact_assertions (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    subject_ref uuid NOT NULL,
    predicate text NOT NULL CHECK (length(btrim(predicate)) BETWEEN 1 AND 512),
    value_type text NOT NULL CHECK (value_type IN ('string', 'integer', 'number', 'boolean', 'null')),
    asserted_value jsonb NOT NULL,
    source_reference_id uuid NOT NULL,
    source_locator text NOT NULL CHECK (length(btrim(source_locator)) > 0),
    valid_from timestamptz,
    valid_to timestamptz,
    observed_at timestamptz NOT NULL,
    tension_group_id uuid,
    verification_reference_id uuid,
    status text NOT NULL DEFAULT 'source_asserted' CHECK (status IN ('source_asserted', 'ambiguous', 'verified', 'superseded')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, source_reference_id) REFERENCES sklegal_legal.source_references(tenant_id, matter_id, id),
    CHECK (sklegal_legal.typed_json_value_is_valid(value_type, asserted_value)),
    CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_to > valid_from),
    CHECK (observed_at <= updated_at),
    CHECK (status <> 'ambiguous' OR tension_group_id IS NOT NULL),
    CHECK ((status = 'verified') = (verification_reference_id IS NOT NULL) OR status NOT IN ('source_asserted', 'verified')),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.tension_groups (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 512),
    selected_assertion_id uuid,
    resolution_rationale text,
    resolved_by uuid,
    resolved_at timestamptz,
    status text NOT NULL DEFAULT 'unresolved' CHECK (status IN ('unresolved', 'under_review', 'resolved', 'dismissed')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, matter_id, selected_assertion_id) REFERENCES sklegal_legal.fact_assertions(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, resolved_by) REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (
        (status = 'resolved' AND selected_assertion_id IS NOT NULL
            AND length(btrim(resolution_rationale)) > 0 AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)
        OR
        (status <> 'resolved' AND selected_assertion_id IS NULL
            AND resolution_rationale IS NULL AND resolved_by IS NULL AND resolved_at IS NULL)
    ),
    CHECK (resolved_at IS NULL OR resolved_at <= updated_at),
    CHECK (updated_at >= created_at)
);

ALTER TABLE sklegal_legal.fact_assertions
ADD CONSTRAINT fact_tension_scope_fk
FOREIGN KEY (tenant_id, matter_id, tension_group_id)
REFERENCES sklegal_legal.tension_groups(tenant_id, matter_id, id);

CREATE TABLE sklegal_legal.tension_assertions (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    tension_group_id uuid NOT NULL,
    assertion_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, tension_group_id, assertion_id),
    FOREIGN KEY (tenant_id, matter_id, tension_group_id) REFERENCES sklegal_legal.tension_groups(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, assertion_id) REFERENCES sklegal_legal.fact_assertions(tenant_id, matter_id, id)
);

CREATE FUNCTION sklegal_legal.validate_tension_membership()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    selected_group sklegal_legal.tension_groups%ROWTYPE;
    new_record jsonb := to_jsonb(NEW);
    old_record jsonb := to_jsonb(OLD);
    selected_tenant uuid := COALESCE(
        (new_record->>'tenant_id')::uuid, (old_record->>'tenant_id')::uuid
    );
    selected_matter uuid := COALESCE(
        (new_record->>'matter_id')::uuid, (old_record->>'matter_id')::uuid
    );
    selected_id uuid := COALESCE(
        (new_record->>'tension_group_id')::uuid, (new_record->>'id')::uuid,
        (old_record->>'tension_group_id')::uuid, (old_record->>'id')::uuid
    );
BEGIN
    SELECT * INTO selected_group
    FROM sklegal_legal.tension_groups
    WHERE tenant_id = selected_tenant AND matter_id = selected_matter AND id = selected_id;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    IF (SELECT count(*) FROM sklegal_legal.tension_assertions
        WHERE tenant_id = selected_tenant AND matter_id = selected_matter
          AND tension_group_id = selected_id) < 2 THEN
        RAISE EXCEPTION 'tension group requires at least two assertions'
            USING ERRCODE = '23514';
    END IF;
    IF selected_group.status = 'resolved' AND NOT EXISTS (
        SELECT 1 FROM sklegal_legal.tension_assertions
        WHERE tenant_id = selected_tenant AND matter_id = selected_matter
          AND tension_group_id = selected_id
          AND assertion_id = selected_group.selected_assertion_id
    ) THEN
        RAISE EXCEPTION 'selected assertion must belong to the tension group'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$function$;

CREATE CONSTRAINT TRIGGER tension_group_membership_complete
AFTER INSERT OR UPDATE ON sklegal_legal.tension_groups
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_tension_membership();
CREATE CONSTRAINT TRIGGER tension_assertion_membership_complete
AFTER INSERT OR UPDATE OR DELETE ON sklegal_legal.tension_assertions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_tension_membership();

CREATE TABLE sklegal_legal.evidence_items (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 512),
    media_type text NOT NULL CHECK (length(btrim(media_type)) BETWEEN 1 AND 512),
    content_sha256 sklegal_legal.sha256_digest NOT NULL,
    source_reference_id uuid NOT NULL,
    verification_reference_id uuid,
    status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'collected', 'verified', 'challenged', 'excluded', 'superseded')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, source_reference_id) REFERENCES sklegal_legal.source_references(tenant_id, matter_id, id),
    CHECK ((status = 'verified') = (verification_reference_id IS NOT NULL) OR status NOT IN ('proposed', 'verified')),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.custody_events (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    evidence_item_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('acquired', 'transferred', 'copied', 'sealed', 'unsealed', 'disposed')),
    custodian_id uuid NOT NULL,
    occurred_at timestamptz NOT NULL,
    source_reference_id uuid NOT NULL,
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, evidence_item_id) REFERENCES sklegal_legal.evidence_items(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, custodian_id) REFERENCES sklegal_identity.principals(tenant_id, id),
    FOREIGN KEY (tenant_id, matter_id, source_reference_id) REFERENCES sklegal_legal.source_references(tenant_id, matter_id, id),
    CHECK (occurred_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.authority_identities (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id)
);

CREATE TABLE sklegal_legal.authorities (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 512),
    citation text NOT NULL CHECK (length(btrim(citation)) BETWEEN 1 AND 512),
    jurisdiction text NOT NULL CHECK (length(btrim(jurisdiction)) BETWEEN 1 AND 512),
    authority_kind text NOT NULL CHECK (authority_kind IN ('constitution', 'statute', 'regulation', 'case', 'rule', 'administrative_material', 'secondary_source', 'other')),
    source_reference_id uuid NOT NULL,
    valid_from timestamptz,
    valid_to timestamptz,
    applicability_validation_id uuid,
    status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'verified', 'challenged', 'not_applicable', 'superseded')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    system_from timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id, version),
    FOREIGN KEY (tenant_id, matter_id, id) REFERENCES sklegal_legal.authority_identities(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, source_reference_id) REFERENCES sklegal_legal.source_references(tenant_id, matter_id, id),
    CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_to > valid_from),
    CHECK (
        status NOT IN ('verified', 'not_applicable')
        OR applicability_validation_id IS NOT NULL
    ),
    CHECK (status <> 'proposed' OR applicability_validation_id IS NULL),
    CHECK (updated_at >= created_at)
);

CREATE FUNCTION sklegal_legal.prepare_authority_version()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    prior sklegal_legal.authorities%ROWTYPE;
    now_at timestamptz;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(NEW.tenant_id::text || ':' || NEW.matter_id::text || ':' || NEW.id::text, 0)
    );
    SELECT * INTO prior
    FROM sklegal_legal.authorities
    WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id AND id = NEW.id
    ORDER BY version DESC LIMIT 1;
    now_at := clock_timestamp();
    IF NOT FOUND THEN
        IF NEW.version <> 1 OR NEW.status <> 'proposed'
           OR NEW.applicability_validation_id IS NOT NULL THEN
            RAISE EXCEPTION 'first authority version must be unreviewed proposed version 1'
                USING ERRCODE = '23514';
        END IF;
        NEW.created_at := now_at;
    ELSE
        IF NEW.version <> prior.version + 1 THEN
            RAISE EXCEPTION 'authority version conflict: expected %', prior.version + 1 USING ERRCODE = '40001';
        END IF;
        IF NEW.source_reference_id IS DISTINCT FROM prior.source_reference_id THEN
            RAISE EXCEPTION 'authority source provenance is immutable'
                USING ERRCODE = '55000';
        END IF;
        IF NEW.status = prior.status THEN
            IF NEW.applicability_validation_id IS DISTINCT FROM prior.applicability_validation_id THEN
                RAISE EXCEPTION 'authority applicability evidence changes only with status'
                    USING ERRCODE = '55000';
            END IF;
        ELSIF NOT (
            (prior.status = 'proposed' AND NEW.status IN ('verified', 'challenged', 'not_applicable', 'superseded'))
            OR (prior.status = 'verified' AND NEW.status IN ('challenged', 'not_applicable', 'superseded'))
            OR (prior.status = 'challenged' AND NEW.status IN ('verified', 'not_applicable', 'superseded'))
            OR (prior.status = 'not_applicable' AND NEW.status = 'superseded')
        ) THEN
            RAISE EXCEPTION 'authority status is not an adjacent declared edge'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.status IN ('verified', 'not_applicable') AND NOT EXISTS (
            SELECT 1 FROM sklegal_legal.validations AS validation
            WHERE validation.tenant_id = NEW.tenant_id
              AND validation.matter_id = NEW.matter_id
              AND validation.id = NEW.applicability_validation_id
              AND validation.subject_kind = 'authority'
              AND validation.subject_artifact_id = NEW.id
              AND validation.subject_artifact_version = prior.version
              AND validation.subject_content_sha256 IS NULL
              AND validation.outcome = 'passed'
              AND EXISTS (
                  SELECT 1 FROM sklegal_legal.validation_checks AS check_record
                  WHERE check_record.tenant_id = validation.tenant_id
                    AND check_record.matter_id = validation.matter_id
                    AND check_record.validation_id = validation.id
              )
        ) THEN
            RAISE EXCEPTION 'authority decision requires passed exact prior-version validation'
                USING ERRCODE = '23514';
        END IF;
        NEW.created_at := prior.created_at;
    END IF;
    NEW.updated_at := now_at;
    NEW.system_from := now_at;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER authority_version_prepare
BEFORE INSERT ON sklegal_legal.authorities
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.prepare_authority_version();

CREATE TRIGGER authority_versions_append_only
BEFORE UPDATE OR DELETE ON sklegal_legal.authorities
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

CREATE VIEW sklegal_legal.authority_history
WITH (security_invoker = true, security_barrier = true)
AS
SELECT authority.*,
       lead(system_from) OVER (
           PARTITION BY tenant_id, matter_id, id ORDER BY version
       ) AS system_to
FROM sklegal_legal.authorities AS authority;

CREATE VIEW sklegal_legal.authority_current
WITH (security_invoker = true, security_barrier = true)
AS
SELECT *
FROM sklegal_legal.authority_history
WHERE system_to IS NULL;

CREATE TABLE sklegal_legal.issues (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    question text NOT NULL CHECK (length(btrim(question)) > 0),
    status text NOT NULL DEFAULT 'identified' CHECK (status IN ('identified', 'under_review', 'resolved', 'deferred')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.claims (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    issue_id uuid NOT NULL,
    label text NOT NULL CHECK (length(btrim(label)) BETWEEN 1 AND 512),
    statement text NOT NULL CHECK (length(btrim(statement)) > 0),
    acceptance_validation_id uuid,
    status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'under_review', 'accepted', 'challenged', 'rejected', 'withdrawn')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, issue_id) REFERENCES sklegal_legal.issues(tenant_id, matter_id, id),
    CHECK ((status = 'accepted') = (acceptance_validation_id IS NOT NULL) OR status NOT IN ('proposed', 'accepted')),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.defenses (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    issue_id uuid NOT NULL,
    label text NOT NULL CHECK (length(btrim(label)) BETWEEN 1 AND 512),
    statement text NOT NULL CHECK (length(btrim(statement)) > 0),
    acceptance_validation_id uuid,
    status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'under_review', 'accepted', 'challenged', 'rejected', 'withdrawn')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, issue_id) REFERENCES sklegal_legal.issues(tenant_id, matter_id, id),
    CHECK ((status = 'accepted') = (acceptance_validation_id IS NOT NULL) OR status NOT IN ('proposed', 'accepted')),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.elements (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    theory_kind text NOT NULL CHECK (theory_kind IN ('claim', 'defense')),
    claim_id uuid NOT NULL,
    description text NOT NULL CHECK (length(btrim(description)) > 0),
    status text NOT NULL DEFAULT 'alleged' CHECK (status IN ('alleged', 'supported', 'disputed', 'not_established')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    CHECK (updated_at >= created_at)
);

CREATE FUNCTION sklegal_legal.require_scoped_theory()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.theory_kind = 'claim' AND EXISTS (
        SELECT 1 FROM sklegal_legal.claims
        WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id AND id = NEW.claim_id
    ) THEN
        RETURN NEW;
    END IF;
    IF NEW.theory_kind = 'defense' AND EXISTS (
        SELECT 1 FROM sklegal_legal.defenses
        WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id AND id = NEW.claim_id
    ) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'element theory does not exist in its tenant and matter scope'
        USING ERRCODE = '23503';
END;
$function$;

CREATE TRIGGER element_scoped_theory
BEFORE INSERT OR UPDATE ON sklegal_legal.elements
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_scoped_theory();

CREATE TABLE sklegal_legal.element_evidence (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    element_id uuid NOT NULL,
    evidence_item_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, element_id, evidence_item_id),
    FOREIGN KEY (tenant_id, matter_id, element_id) REFERENCES sklegal_legal.elements(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, evidence_item_id) REFERENCES sklegal_legal.evidence_items(tenant_id, matter_id, id)
);

CREATE TABLE sklegal_legal.theory_evidence (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    theory_kind text NOT NULL CHECK (theory_kind IN ('claim', 'defense')),
    theory_id uuid NOT NULL,
    evidence_item_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, theory_kind, theory_id, evidence_item_id),
    FOREIGN KEY (tenant_id, matter_id, evidence_item_id) REFERENCES sklegal_legal.evidence_items(tenant_id, matter_id, id)
);

CREATE TABLE sklegal_legal.theory_authorities (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    theory_kind text NOT NULL CHECK (theory_kind IN ('claim', 'defense')),
    theory_id uuid NOT NULL,
    authority_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, theory_kind, theory_id, authority_id),
    FOREIGN KEY (tenant_id, matter_id, authority_id) REFERENCES sklegal_legal.authority_identities(tenant_id, matter_id, id)
);

CREATE FUNCTION sklegal_legal.require_scoped_theory_link()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.theory_kind = 'claim' AND EXISTS (
        SELECT 1 FROM sklegal_legal.claims
        WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id AND id = NEW.theory_id
    ) THEN
        RETURN NEW;
    END IF;
    IF NEW.theory_kind = 'defense' AND EXISTS (
        SELECT 1 FROM sklegal_legal.defenses
        WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id AND id = NEW.theory_id
    ) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'theory link does not exist in its tenant and matter scope'
        USING ERRCODE = '23503';
END;
$function$;

CREATE TRIGGER theory_evidence_scope
BEFORE INSERT OR UPDATE ON sklegal_legal.theory_evidence
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_scoped_theory_link();
CREATE TRIGGER theory_authority_scope
BEFORE INSERT OR UPDATE ON sklegal_legal.theory_authorities
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_scoped_theory_link();

CREATE TABLE sklegal_legal.remedies (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    claim_id uuid NOT NULL,
    description text NOT NULL CHECK (length(btrim(description)) > 0),
    status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'available', 'unavailable', 'awarded', 'denied')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, claim_id) REFERENCES sklegal_legal.claims(tenant_id, matter_id, id),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.remedy_authorities (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    remedy_id uuid NOT NULL,
    authority_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, remedy_id, authority_id),
    FOREIGN KEY (tenant_id, matter_id, remedy_id) REFERENCES sklegal_legal.remedies(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, authority_id) REFERENCES sklegal_legal.authority_identities(tenant_id, matter_id, id)
);

CREATE TABLE sklegal_legal.deadline_calculations (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    trigger_fact_id uuid NOT NULL,
    calculation_rule text NOT NULL CHECK (length(btrim(calculation_rule)) > 0),
    candidate_due_at timestamptz NOT NULL,
    calculated_at timestamptz NOT NULL,
    calculation_version text NOT NULL CHECK (length(btrim(calculation_version)) BETWEEN 1 AND 512),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, trigger_fact_id) REFERENCES sklegal_legal.fact_assertions(tenant_id, matter_id, id),
    CHECK (calculated_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.deadline_calculation_sources (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    calculation_id uuid NOT NULL,
    source_reference_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, calculation_id, source_reference_id),
    FOREIGN KEY (tenant_id, matter_id, calculation_id) REFERENCES sklegal_legal.deadline_calculations(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, source_reference_id) REFERENCES sklegal_legal.source_references(tenant_id, matter_id, id)
);

CREATE TABLE sklegal_legal.deadlines (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 512),
    candidate_due_at timestamptz,
    operative_due_at timestamptz,
    trigger_fact_id uuid,
    calculation_id uuid,
    review_validation_id uuid,
    completed_at timestamptz,
    status text NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'reviewed', 'operative', 'satisfied', 'missed', 'withdrawn')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, trigger_fact_id) REFERENCES sklegal_legal.fact_assertions(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, calculation_id) REFERENCES sklegal_legal.deadline_calculations(tenant_id, matter_id, id),
    CHECK (
        status = 'candidate'
        OR (trigger_fact_id IS NOT NULL AND calculation_id IS NOT NULL AND review_validation_id IS NOT NULL)
    ),
    CHECK (status NOT IN ('operative', 'satisfied', 'missed') OR operative_due_at IS NOT NULL),
    CHECK (status IN ('operative', 'satisfied', 'missed') OR operative_due_at IS NULL),
    CHECK ((status = 'satisfied') = (completed_at IS NOT NULL)),
    CHECK (status <> 'candidate' OR review_validation_id IS NULL),
    CHECK (completed_at IS NULL OR completed_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.tasks (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 512),
    description text NOT NULL CHECK (length(btrim(description)) > 0),
    assigned_principal_id uuid,
    due_at timestamptz,
    blocked_reason text,
    completed_at timestamptz,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'ready', 'in_progress', 'blocked', 'completed', 'cancelled')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, assigned_principal_id) REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (status <> 'ready' OR assigned_principal_id IS NOT NULL),
    CHECK ((status = 'blocked') = (blocked_reason IS NOT NULL)),
    CHECK ((status = 'completed') = (completed_at IS NOT NULL)),
    CHECK (completed_at IS NULL OR completed_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.work_products (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 512),
    work_product_kind text NOT NULL CHECK (work_product_kind IN ('memo', 'letter', 'pleading', 'contract', 'packet', 'report', 'other')),
    current_version_id uuid NOT NULL,
    current_version_number sklegal_legal.record_version NOT NULL,
    current_content_sha256 sklegal_legal.sha256_digest NOT NULL,
    validation_result_id uuid,
    approval_id uuid,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'in_review', 'validated', 'approved', 'superseded', 'withdrawn')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    CHECK (
        (status IN ('draft', 'in_review') AND validation_result_id IS NULL AND approval_id IS NULL)
        OR (status = 'validated' AND validation_result_id IS NOT NULL AND approval_id IS NULL)
        OR (status IN ('approved', 'superseded') AND validation_result_id IS NOT NULL AND approval_id IS NOT NULL)
        OR status = 'withdrawn'
    ),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.work_product_versions (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    work_product_id uuid NOT NULL,
    version_number sklegal_legal.record_version NOT NULL,
    content_sha256 sklegal_legal.sha256_digest NOT NULL,
    source_artifact_id uuid NOT NULL,
    encrypted_content bytea,
    encryption_key_ref text,
    encryption_algorithm text,
    encrypted_at timestamptz,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'frozen', 'superseded')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, work_product_id, version_number),
    UNIQUE (tenant_id, matter_id, id, version_number, content_sha256),
    UNIQUE (tenant_id, matter_id, id, work_product_id, version_number, content_sha256),
    FOREIGN KEY (tenant_id, matter_id, work_product_id)
        REFERENCES sklegal_legal.work_products(tenant_id, matter_id, id)
        DEFERRABLE INITIALLY DEFERRED,
    CHECK (sklegal_identity.encrypted_payload_is_complete(
        encrypted_content, encryption_key_ref, encryption_algorithm, encrypted_at
    )),
    CHECK (updated_at >= created_at)
);

CREATE FUNCTION sklegal_legal.require_draft_work_product_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.status <> 'draft' OR NEW.validation_result_id IS NOT NULL
       OR NEW.approval_id IS NOT NULL THEN
        RAISE EXCEPTION 'work product must be inserted as an ungated draft'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER work_product_draft_insert_only
BEFORE INSERT ON sklegal_legal.work_products
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_draft_work_product_insert();

CREATE FUNCTION sklegal_legal.control_work_product_version_update()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.matter_id IS DISTINCT FROM OLD.matter_id
       OR NEW.work_product_id IS DISTINCT FROM OLD.work_product_id
       OR NEW.version_number IS DISTINCT FROM OLD.version_number
       OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
       OR NEW.source_artifact_id IS DISTINCT FROM OLD.source_artifact_id
       OR NEW.encrypted_content IS DISTINCT FROM OLD.encrypted_content
       OR NEW.encryption_key_ref IS DISTINCT FROM OLD.encryption_key_ref
       OR NEW.encryption_algorithm IS DISTINCT FROM OLD.encryption_algorithm
       OR NEW.encrypted_at IS DISTINCT FROM OLD.encrypted_at
       OR NEW.classification IS DISTINCT FROM OLD.classification
       OR NEW.completeness IS DISTINCT FROM OLD.completeness
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'work product version payload and source binding are immutable'
            USING ERRCODE = '55000';
    END IF;
    IF NEW.version <> OLD.version + 1
       OR NOT (
           (OLD.status = 'draft' AND NEW.status = 'frozen')
           OR (OLD.status = 'frozen' AND NEW.status = 'superseded')
       ) THEN
        RAISE EXCEPTION 'invalid work product version transition'
            USING ERRCODE = '23514';
    END IF;
    NEW.updated_at := GREATEST(OLD.updated_at, clock_timestamp());
    RETURN NEW;
END;
$function$;

CREATE FUNCTION sklegal_legal.require_draft_work_product_version_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.status <> 'draft' THEN
        RAISE EXCEPTION 'work product version must be inserted as draft'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER work_product_version_draft_insert
BEFORE INSERT ON sklegal_legal.work_product_versions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_draft_work_product_version_insert();
CREATE TRIGGER work_product_version_controlled_update
BEFORE UPDATE ON sklegal_legal.work_product_versions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.control_work_product_version_update();

CREATE FUNCTION sklegal_legal.lock_exact_work_product_version(
    target_tenant_id uuid,
    target_matter_id uuid,
    target_version_id uuid,
    target_version_number bigint,
    target_content_sha256 text
)
RETURNS sklegal_legal.work_product_versions
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    version_record sklegal_legal.work_product_versions%ROWTYPE;
    parent_work_product_id uuid;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_legal.has_matter_membership(target_tenant_id, target_matter_id) THEN
        RAISE EXCEPTION 'exact work product version lock is not authorized'
            USING ERRCODE = '42501';
    END IF;

    SELECT work_product_id INTO parent_work_product_id
    FROM sklegal_legal.work_product_versions
    WHERE tenant_id = target_tenant_id
      AND matter_id = target_matter_id
      AND id = target_version_id
      AND version_number = target_version_number
      AND content_sha256 = target_content_sha256;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'exact work product version does not exist in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;

    PERFORM 1
    FROM sklegal_legal.work_products
    WHERE tenant_id = target_tenant_id
      AND matter_id = target_matter_id
      AND id = parent_work_product_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'parent work product does not exist in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;

    SELECT * INTO version_record
    FROM sklegal_legal.work_product_versions
    WHERE tenant_id = target_tenant_id
      AND matter_id = target_matter_id
      AND id = target_version_id
      AND work_product_id = parent_work_product_id
      AND version_number = target_version_number
      AND content_sha256 = target_content_sha256
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'exact work product version changed before it could be locked'
            USING ERRCODE = '40001';
    END IF;
    RETURN version_record;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.lock_exact_work_product_version(
    uuid, uuid, uuid, bigint, text
) FROM PUBLIC;

CREATE FUNCTION sklegal_legal.transition_work_product_version(
    target_tenant_id uuid,
    target_matter_id uuid,
    target_version_id uuid,
    expected_version bigint,
    target_status text
)
RETURNS sklegal_legal.work_product_versions
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    version_record sklegal_legal.work_product_versions%ROWTYPE;
    parent_work_product_id uuid;
    parent_status text;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_legal.has_matter_membership(target_tenant_id, target_matter_id) THEN
        RAISE EXCEPTION 'work product version transition is not authorized'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO version_record
    FROM sklegal_legal.work_product_versions
    WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
      AND id = target_version_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'work product version does not exist in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;
    parent_work_product_id := version_record.work_product_id;
    SELECT * INTO version_record
    FROM sklegal_legal.lock_exact_work_product_version(
        target_tenant_id,
        target_matter_id,
        target_version_id,
        version_record.version_number,
        version_record.content_sha256
    );
    SELECT status INTO parent_status
    FROM sklegal_legal.work_products
    WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
      AND id = parent_work_product_id;
    IF version_record.version <> expected_version THEN
        RAISE EXCEPTION 'work product version conflict' USING ERRCODE = '40001';
    END IF;
    IF target_status = 'superseded'
       AND parent_status IN ('validated', 'approved')
       AND EXISTS (
           SELECT 1 FROM sklegal_legal.work_products AS parent
           WHERE parent.tenant_id = target_tenant_id
             AND parent.matter_id = target_matter_id
             AND parent.id = parent_work_product_id
             AND parent.current_version_id = target_version_id
             AND parent.current_version_number = version_record.version_number
             AND parent.current_content_sha256 = version_record.content_sha256
       ) THEN
        RAISE EXCEPTION 'a gated work product current version cannot be superseded'
            USING ERRCODE = '23514';
    END IF;
    IF target_status = 'superseded' THEN
        PERFORM 1
        FROM sklegal_legal.executions
        WHERE tenant_id = target_tenant_id
          AND matter_id = target_matter_id
          AND subject_artifact_id = target_version_id
          AND subject_artifact_version = version_record.version_number
          AND subject_content_sha256 = version_record.content_sha256
          AND status IN (
              'draft', 'validated', 'approved', 'queued', 'dispatched', 'failed'
          )
        ORDER BY id
        FOR UPDATE;
        IF FOUND THEN
            RAISE EXCEPTION 'a work product version with a live execution cannot be superseded'
                USING ERRCODE = '23514';
        END IF;

        PERFORM 1
        FROM sklegal_legal.communications
        WHERE tenant_id = target_tenant_id
          AND matter_id = target_matter_id
          AND work_product_version_id = target_version_id
          AND work_product_version_number = version_record.version_number
          AND work_product_content_sha256 = version_record.content_sha256
          AND status IN (
              'draft', 'validated', 'approved', 'queued', 'dispatched', 'failed'
          )
        ORDER BY id
        FOR UPDATE;
        IF FOUND THEN
            RAISE EXCEPTION 'a work product version with a live communication cannot be superseded'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    UPDATE sklegal_legal.work_product_versions
    SET status = target_status, version = version + 1
    WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
      AND id = target_version_id
    RETURNING * INTO version_record;
    RETURN version_record;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.transition_work_product_version(
    uuid, uuid, uuid, bigint, text
) FROM PUBLIC;

CREATE TABLE sklegal_legal.validations (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    subject_kind text NOT NULL CHECK (subject_kind IN (
        'work_product_version', 'party', 'party_role', 'matter_event',
        'fact_assertion', 'evidence_item', 'authority', 'claim', 'defense',
        'deadline'
    )),
    subject_artifact_id uuid NOT NULL,
    subject_artifact_version sklegal_legal.record_version NOT NULL,
    subject_content_sha256 sklegal_legal.sha256_digest,
    outcome text NOT NULL CHECK (outcome IN ('incomplete', 'passed', 'failed')),
    validator_principal_id uuid NOT NULL,
    validated_at timestamptz NOT NULL,
    rationale text NOT NULL CHECK (length(btrim(rationale)) > 0),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, id, subject_artifact_id, subject_artifact_version, subject_content_sha256),
    FOREIGN KEY (tenant_id, validator_principal_id) REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (validated_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.validation_checks (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    validation_id uuid NOT NULL,
    check_id text NOT NULL CHECK (length(btrim(check_id)) BETWEEN 1 AND 512),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, validation_id, check_id),
    FOREIGN KEY (tenant_id, matter_id, validation_id) REFERENCES sklegal_legal.validations(tenant_id, matter_id, id)
);

CREATE FUNCTION sklegal_legal.require_typed_validation_subject()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.subject_kind = 'work_product_version' THEN
        IF NEW.subject_content_sha256 IS NULL OR NOT EXISTS (
            SELECT 1 FROM sklegal_legal.work_product_versions
            WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
              AND id = NEW.subject_artifact_id
              AND version_number = NEW.subject_artifact_version
              AND content_sha256 = NEW.subject_content_sha256
              AND status = 'frozen'
        ) THEN
            RAISE EXCEPTION 'validation subject must be the exact frozen work product version'
                USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.subject_content_sha256 IS NOT NULL THEN
        RAISE EXCEPTION 'non-artifact validation subjects cannot carry a digest'
            USING ERRCODE = '23514';
    ELSIF NOT (CASE NEW.subject_kind
        WHEN 'party' THEN EXISTS (
            SELECT 1 FROM sklegal_legal.parties WHERE tenant_id = NEW.tenant_id
              AND matter_id = NEW.matter_id AND id = NEW.subject_artifact_id
              AND version = NEW.subject_artifact_version
        )
        WHEN 'party_role' THEN EXISTS (
            SELECT 1 FROM sklegal_legal.party_roles WHERE tenant_id = NEW.tenant_id
              AND matter_id = NEW.matter_id AND id = NEW.subject_artifact_id
              AND version = NEW.subject_artifact_version
        )
        WHEN 'matter_event' THEN EXISTS (
            SELECT 1 FROM sklegal_legal.matter_events WHERE tenant_id = NEW.tenant_id
              AND matter_id = NEW.matter_id AND id = NEW.subject_artifact_id
              AND version = NEW.subject_artifact_version
        )
        WHEN 'fact_assertion' THEN EXISTS (
            SELECT 1 FROM sklegal_legal.fact_assertions WHERE tenant_id = NEW.tenant_id
              AND matter_id = NEW.matter_id AND id = NEW.subject_artifact_id
              AND version = NEW.subject_artifact_version
        )
        WHEN 'evidence_item' THEN EXISTS (
            SELECT 1 FROM sklegal_legal.evidence_items WHERE tenant_id = NEW.tenant_id
              AND matter_id = NEW.matter_id AND id = NEW.subject_artifact_id
              AND version = NEW.subject_artifact_version
        )
        WHEN 'authority' THEN EXISTS (
            SELECT 1 FROM sklegal_legal.authorities WHERE tenant_id = NEW.tenant_id
              AND matter_id = NEW.matter_id AND id = NEW.subject_artifact_id
              AND version = NEW.subject_artifact_version
        )
        WHEN 'claim' THEN EXISTS (
            SELECT 1 FROM sklegal_legal.claims WHERE tenant_id = NEW.tenant_id
              AND matter_id = NEW.matter_id AND id = NEW.subject_artifact_id
              AND version = NEW.subject_artifact_version
        )
        WHEN 'defense' THEN EXISTS (
            SELECT 1 FROM sklegal_legal.defenses WHERE tenant_id = NEW.tenant_id
              AND matter_id = NEW.matter_id AND id = NEW.subject_artifact_id
              AND version = NEW.subject_artifact_version
        )
        WHEN 'deadline' THEN EXISTS (
            SELECT 1 FROM sklegal_legal.deadlines WHERE tenant_id = NEW.tenant_id
              AND matter_id = NEW.matter_id AND id = NEW.subject_artifact_id
              AND version = NEW.subject_artifact_version
        )
        ELSE false
    END) THEN
        RAISE EXCEPTION 'validation subject kind, scope, identity, and version do not resolve exactly'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER validation_typed_subject
BEFORE INSERT ON sklegal_legal.validations
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_typed_validation_subject();

CREATE FUNCTION sklegal_legal.validate_validation_checks()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    record_data jsonb := COALESCE(to_jsonb(NEW), to_jsonb(OLD));
    selected_tenant uuid := (record_data->>'tenant_id')::uuid;
    selected_matter uuid := (record_data->>'matter_id')::uuid;
    selected_id uuid := COALESCE(
        (record_data->>'validation_id')::uuid,
        (record_data->>'id')::uuid
    );
BEGIN
    IF EXISTS (
        SELECT 1 FROM sklegal_legal.validations
        WHERE tenant_id = selected_tenant AND matter_id = selected_matter
          AND id = selected_id
    ) AND NOT EXISTS (
        SELECT 1 FROM sklegal_legal.validation_checks
        WHERE tenant_id = selected_tenant AND matter_id = selected_matter
          AND validation_id = selected_id
    ) THEN
        RAISE EXCEPTION 'validation requires at least one check'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$function$;

CREATE CONSTRAINT TRIGGER validation_checks_complete
AFTER INSERT ON sklegal_legal.validations
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_validation_checks();
CREATE CONSTRAINT TRIGGER validation_check_links_complete
AFTER INSERT OR UPDATE OR DELETE ON sklegal_legal.validation_checks
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_validation_checks();

CREATE TABLE sklegal_legal.approvals (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    subject_artifact_id uuid NOT NULL,
    subject_artifact_version sklegal_legal.record_version NOT NULL,
    subject_content_sha256 sklegal_legal.sha256_digest NOT NULL,
    reviewer_principal_id uuid,
    decided_at timestamptz,
    rationale text,
    revoker_principal_id uuid,
    revocation_rationale text,
    revoked_at timestamptz,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'revoked')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, id, subject_artifact_id, subject_artifact_version, subject_content_sha256),
    FOREIGN KEY (tenant_id, matter_id, subject_artifact_id, subject_artifact_version, subject_content_sha256)
        REFERENCES sklegal_legal.work_product_versions(tenant_id, matter_id, id, version_number, content_sha256),
    FOREIGN KEY (tenant_id, reviewer_principal_id) REFERENCES sklegal_identity.principals(tenant_id, id),
    FOREIGN KEY (tenant_id, revoker_principal_id) REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (
        (status = 'pending' AND reviewer_principal_id IS NULL AND decided_at IS NULL AND rationale IS NULL)
        OR
        (status IN ('approved', 'rejected', 'revoked') AND reviewer_principal_id IS NOT NULL
            AND decided_at IS NOT NULL AND length(btrim(rationale)) > 0)
    ),
    CHECK (
        (status = 'revoked' AND revoker_principal_id IS NOT NULL
            AND revoked_at IS NOT NULL AND length(btrim(revocation_rationale)) > 0)
        OR
        (status <> 'revoked' AND revoker_principal_id IS NULL
            AND revoked_at IS NULL AND revocation_rationale IS NULL)
    ),
    CHECK (decided_at IS NULL OR decided_at <= updated_at),
    CHECK (revoked_at IS NULL OR revoked_at >= decided_at),
    CHECK (revoked_at IS NULL OR revoked_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.approval_history (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    approval_id uuid NOT NULL,
    approval_version sklegal_legal.record_version NOT NULL,
    subject_artifact_id uuid NOT NULL,
    subject_artifact_version sklegal_legal.record_version NOT NULL,
    subject_content_sha256 sklegal_legal.sha256_digest NOT NULL,
    reviewer_principal_id uuid NOT NULL,
    decided_at timestamptz NOT NULL,
    rationale text NOT NULL CHECK (length(btrim(rationale)) > 0),
    status text NOT NULL CHECK (status = 'approved'),
    classification sklegal_legal.data_classification NOT NULL,
    completeness sklegal_legal.record_completeness NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (
        tenant_id, matter_id, approval_id, approval_version,
        subject_artifact_id, subject_artifact_version, subject_content_sha256
    ),
    UNIQUE (tenant_id, matter_id, approval_id, approval_version),
    FOREIGN KEY (
        tenant_id, matter_id, approval_id, subject_artifact_id,
        subject_artifact_version, subject_content_sha256
    ) REFERENCES sklegal_legal.approvals(
        tenant_id, matter_id, id, subject_artifact_id,
        subject_artifact_version, subject_content_sha256
    ),
    FOREIGN KEY (tenant_id, reviewer_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (approval_version > 1),
    CHECK (created_at <= decided_at),
    CHECK (decided_at <= updated_at),
    CHECK (updated_at <= captured_at)
);

CREATE FUNCTION sklegal_legal.require_controlled_approval_history_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF current_user <> 'sklegal_migrator' OR session_user = current_user
       OR NOT EXISTS (
           SELECT 1
           FROM sklegal_legal.approvals AS approval
           WHERE approval.tenant_id = NEW.tenant_id
             AND approval.matter_id = NEW.matter_id
             AND approval.id = NEW.approval_id
             AND approval.version = NEW.approval_version
             AND approval.subject_artifact_id = NEW.subject_artifact_id
             AND approval.subject_artifact_version = NEW.subject_artifact_version
             AND approval.subject_content_sha256 = NEW.subject_content_sha256
             AND approval.reviewer_principal_id = NEW.reviewer_principal_id
             AND approval.decided_at = NEW.decided_at
             AND approval.rationale = NEW.rationale
             AND approval.status = NEW.status
             AND approval.classification = NEW.classification
             AND approval.completeness = NEW.completeness
             AND approval.created_at = NEW.created_at
             AND approval.updated_at = NEW.updated_at
             AND approval.status = 'approved'
       ) THEN
        RAISE EXCEPTION 'approval history may only capture the current controlled approved decision'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER approval_history_controlled_insert
BEFORE INSERT ON sklegal_legal.approval_history
FOR EACH ROW EXECUTE FUNCTION
    sklegal_legal.require_controlled_approval_history_insert();
CREATE TRIGGER approval_history_append_only
BEFORE UPDATE OR DELETE ON sklegal_legal.approval_history
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

CREATE FUNCTION sklegal_legal.enforce_approval_update()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.subject_artifact_id IS DISTINCT FROM OLD.subject_artifact_id
       OR NEW.subject_artifact_version IS DISTINCT FROM OLD.subject_artifact_version
       OR NEW.subject_content_sha256 IS DISTINCT FROM OLD.subject_content_sha256
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.matter_id IS DISTINCT FROM OLD.matter_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'approval identity and exact artifact binding are immutable'
            USING ERRCODE = '55000';
    END IF;
    IF OLD.status = 'pending' AND NEW.status IN ('approved', 'rejected') THEN
        IF NEW.reviewer_principal_id IS NULL OR length(btrim(NEW.rationale)) = 0 THEN
            RAISE EXCEPTION 'approval decision must be attributable and reasoned'
                USING ERRCODE = '23514';
        END IF;
        NEW.decided_at := NEW.updated_at;
        NEW.revoker_principal_id := NULL;
        NEW.revocation_rationale := NULL;
        NEW.revoked_at := NULL;
    ELSIF OLD.status = 'approved' AND NEW.status = 'revoked' THEN
        IF NEW.reviewer_principal_id IS DISTINCT FROM OLD.reviewer_principal_id
           OR NEW.decided_at IS DISTINCT FROM OLD.decided_at
           OR NEW.rationale IS DISTINCT FROM OLD.rationale
           OR NEW.revoker_principal_id IS NULL
           OR length(btrim(NEW.revocation_rationale)) = 0 THEN
            RAISE EXCEPTION 'revocation must preserve decision evidence and be attributable'
                USING ERRCODE = '23514';
        END IF;
        NEW.revoked_at := NEW.updated_at;
    ELSE
        RAISE EXCEPTION 'invalid approval transition'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER zz_approval_controlled_transition
BEFORE UPDATE ON sklegal_legal.approvals
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.enforce_approval_update();

CREATE FUNCTION sklegal_legal.require_pending_approval_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.status <> 'pending'
       OR NEW.reviewer_principal_id IS NOT NULL OR NEW.decided_at IS NOT NULL
       OR NEW.rationale IS NOT NULL OR NEW.revoker_principal_id IS NOT NULL
       OR NEW.revocation_rationale IS NOT NULL OR NEW.revoked_at IS NOT NULL THEN
        RAISE EXCEPTION 'approval must be inserted pending and transitioned explicitly'
            USING ERRCODE = '23514';
    END IF;
    PERFORM 1 FROM sklegal_legal.work_product_versions
    WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
      AND id = NEW.subject_artifact_id
      AND version_number = NEW.subject_artifact_version
      AND content_sha256 = NEW.subject_content_sha256
      AND status = 'frozen'
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'approval creation requires the exact frozen artifact'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.require_pending_approval_insert()
FROM PUBLIC;

CREATE TRIGGER approval_pending_insert_only
BEFORE INSERT ON sklegal_legal.approvals
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_pending_approval_insert();

CREATE FUNCTION sklegal_legal.lock_approval_decision(
    target_tenant_id uuid,
    target_matter_id uuid,
    target_approval_id uuid,
    target_artifact_id uuid,
    target_artifact_version bigint,
    target_content_sha256 text,
    expected_approval_version bigint,
    require_current boolean
)
RETURNS sklegal_legal.approval_history
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    approval_record sklegal_legal.approvals%ROWTYPE;
    history_record sklegal_legal.approval_history%ROWTYPE;
    selected_approval_version bigint;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_legal.has_matter_membership(target_tenant_id, target_matter_id) THEN
        RAISE EXCEPTION 'approval decision lock is not authorized'
            USING ERRCODE = '42501';
    END IF;
    PERFORM sklegal_legal.lock_exact_work_product_version(
        target_tenant_id,
        target_matter_id,
        target_artifact_id,
        target_artifact_version,
        target_content_sha256
    );
    SELECT * INTO approval_record
    FROM sklegal_legal.approvals
    WHERE tenant_id = target_tenant_id
      AND matter_id = target_matter_id
      AND id = target_approval_id
      AND subject_artifact_id = target_artifact_id
      AND subject_artifact_version = target_artifact_version
      AND subject_content_sha256 = target_content_sha256
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'approval does not bind the exact artifact in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;
    selected_approval_version := COALESCE(
        expected_approval_version,
        approval_record.version
    );
    SELECT * INTO history_record
    FROM sklegal_legal.approval_history
    WHERE tenant_id = target_tenant_id
      AND matter_id = target_matter_id
      AND approval_id = target_approval_id
      AND approval_version = selected_approval_version
      AND subject_artifact_id = target_artifact_id
      AND subject_artifact_version = target_artifact_version
      AND subject_content_sha256 = target_content_sha256;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'immutable approved decision snapshot does not exist'
            USING ERRCODE = '23514';
    END IF;
    IF require_current AND (
        approval_record.status <> 'approved'
        OR approval_record.version <> history_record.approval_version
    ) THEN
        RAISE EXCEPTION 'approval is no longer current for new progression'
            USING ERRCODE = '23514';
    END IF;
    RETURN history_record;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.lock_approval_decision(
    uuid, uuid, uuid, uuid, bigint, text, bigint, boolean
) FROM PUBLIC;

CREATE FUNCTION sklegal_legal.transition_approval(
    target_tenant_id uuid,
    target_matter_id uuid,
    target_approval_id uuid,
    expected_version bigint,
    target_status text,
    reason text
)
RETURNS sklegal_legal.approvals
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    approval_record sklegal_legal.approvals%ROWTYPE;
    artifact_record sklegal_legal.work_product_versions%ROWTYPE;
    actor_id uuid;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_legal.has_matter_membership(target_tenant_id, target_matter_id)
       OR length(btrim(reason)) = 0 THEN
        RAISE EXCEPTION 'approval transition is not authorized or reasoned'
            USING ERRCODE = '42501';
    END IF;
    actor_id := sklegal_identity.current_principal_id();
    SELECT * INTO approval_record
    FROM sklegal_legal.approvals
    WHERE tenant_id = target_tenant_id
      AND matter_id = target_matter_id
      AND id = target_approval_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'approval does not exist in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;
    SELECT * INTO artifact_record
    FROM sklegal_legal.lock_exact_work_product_version(
        target_tenant_id,
        target_matter_id,
        approval_record.subject_artifact_id,
        approval_record.subject_artifact_version,
        approval_record.subject_content_sha256
    );
    SELECT * INTO approval_record
    FROM sklegal_legal.approvals
    WHERE tenant_id = target_tenant_id
      AND matter_id = target_matter_id
      AND id = target_approval_id
      AND subject_artifact_id = approval_record.subject_artifact_id
      AND subject_artifact_version = approval_record.subject_artifact_version
      AND subject_content_sha256 = approval_record.subject_content_sha256
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'approval binding changed before it could be locked'
            USING ERRCODE = '40001';
    END IF;
    IF approval_record.version <> expected_version THEN
        RAISE EXCEPTION 'approval version conflict' USING ERRCODE = '40001';
    END IF;
    IF approval_record.status = 'pending' AND target_status IN ('approved', 'rejected') THEN
        IF artifact_record.status <> 'frozen' THEN
            RAISE EXCEPTION 'approval decision requires the exact artifact to remain frozen'
                USING ERRCODE = '23514';
        END IF;
        UPDATE sklegal_legal.approvals
        SET status = target_status,
            reviewer_principal_id = actor_id,
            rationale = reason,
            version = version + 1
        WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
          AND id = target_approval_id
        RETURNING * INTO approval_record;
        IF target_status = 'approved' THEN
            INSERT INTO sklegal_legal.approval_history (
                tenant_id, matter_id, approval_id, approval_version,
                subject_artifact_id, subject_artifact_version,
                subject_content_sha256, reviewer_principal_id, decided_at,
                rationale, status, classification, completeness, created_at,
                updated_at
            ) VALUES (
                approval_record.tenant_id,
                approval_record.matter_id,
                approval_record.id,
                approval_record.version,
                approval_record.subject_artifact_id,
                approval_record.subject_artifact_version,
                approval_record.subject_content_sha256,
                approval_record.reviewer_principal_id,
                approval_record.decided_at,
                approval_record.rationale,
                approval_record.status,
                approval_record.classification,
                approval_record.completeness,
                approval_record.created_at,
                approval_record.updated_at
            );
        END IF;
    ELSIF approval_record.status = 'approved' AND target_status = 'revoked' THEN
        PERFORM 1
        FROM sklegal_legal.approval_history
        WHERE tenant_id = target_tenant_id
          AND matter_id = target_matter_id
          AND approval_id = target_approval_id
          AND approval_version = approval_record.version
          AND subject_artifact_id = approval_record.subject_artifact_id
          AND subject_artifact_version = approval_record.subject_artifact_version
          AND subject_content_sha256 = approval_record.subject_content_sha256;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'approval revocation requires its immutable decision snapshot'
                USING ERRCODE = '23514';
        END IF;

        PERFORM 1
        FROM sklegal_legal.work_products
        WHERE tenant_id = target_tenant_id
          AND matter_id = target_matter_id
          AND approval_id = target_approval_id
          AND current_version_id = approval_record.subject_artifact_id
          AND current_version_number = approval_record.subject_artifact_version
          AND current_content_sha256 = approval_record.subject_content_sha256
          AND status = 'approved'
        ORDER BY id
        FOR UPDATE;
        IF FOUND THEN
            RAISE EXCEPTION 'approval cannot be revoked while an approved work product is live'
                USING ERRCODE = '23514';
        END IF;

        PERFORM 1
        FROM sklegal_legal.executions
        WHERE tenant_id = target_tenant_id
          AND matter_id = target_matter_id
          AND approval_id = target_approval_id
          AND approval_version = approval_record.version
          AND subject_artifact_id = approval_record.subject_artifact_id
          AND subject_artifact_version = approval_record.subject_artifact_version
          AND subject_content_sha256 = approval_record.subject_content_sha256
          AND status IN ('approved', 'queued', 'dispatched', 'failed')
        ORDER BY id
        FOR UPDATE;
        IF FOUND THEN
            RAISE EXCEPTION 'approval cannot be revoked while an execution is live'
                USING ERRCODE = '23514';
        END IF;

        PERFORM 1
        FROM sklegal_legal.communications
        WHERE tenant_id = target_tenant_id
          AND matter_id = target_matter_id
          AND approval_id = target_approval_id
          AND work_product_version_id = approval_record.subject_artifact_id
          AND work_product_version_number = approval_record.subject_artifact_version
          AND work_product_content_sha256 = approval_record.subject_content_sha256
          AND status IN ('approved', 'queued', 'dispatched', 'failed')
        ORDER BY id
        FOR UPDATE;
        IF FOUND THEN
            RAISE EXCEPTION 'approval cannot be revoked while a communication is live'
                USING ERRCODE = '23514';
        END IF;

        UPDATE sklegal_legal.approvals
        SET status = 'revoked',
            revoker_principal_id = actor_id,
            revocation_rationale = reason,
            version = version + 1
        WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
          AND id = target_approval_id
        RETURNING * INTO approval_record;
    ELSE
        RAISE EXCEPTION 'approval transition is not an adjacent declared edge'
            USING ERRCODE = '23514';
    END IF;
    RETURN approval_record;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.transition_approval(
    uuid, uuid, uuid, bigint, text, text
) FROM PUBLIC;

ALTER TABLE sklegal_legal.work_products
ADD CONSTRAINT work_product_current_version_fk
FOREIGN KEY (tenant_id, matter_id, current_version_id, id, current_version_number, current_content_sha256)
REFERENCES sklegal_legal.work_product_versions(tenant_id, matter_id, id, work_product_id, version_number, content_sha256)
DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE sklegal_legal.work_products
ADD CONSTRAINT work_product_validation_fk
FOREIGN KEY (tenant_id, matter_id, validation_result_id, current_version_id, current_version_number, current_content_sha256)
REFERENCES sklegal_legal.validations(tenant_id, matter_id, id, subject_artifact_id, subject_artifact_version, subject_content_sha256);

ALTER TABLE sklegal_legal.work_products
ADD CONSTRAINT work_product_approval_fk
FOREIGN KEY (tenant_id, matter_id, approval_id, current_version_id, current_version_number, current_content_sha256)
REFERENCES sklegal_legal.approvals(tenant_id, matter_id, id, subject_artifact_id, subject_artifact_version, subject_content_sha256);

CREATE FUNCTION sklegal_legal.enforce_work_product_transition()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    artifact_status text;
    controlled_revision boolean :=
        current_user = 'sklegal_migrator' AND session_user <> current_user
        AND (
            (OLD.status IN ('draft', 'in_review') AND NEW.status = OLD.status)
            OR (OLD.status IN ('validated', 'approved') AND NEW.status = 'in_review')
        );
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.matter_id IS DISTINCT FROM OLD.matter_id
       OR NEW.classification IS DISTINCT FROM OLD.classification
       OR NEW.completeness IS DISTINCT FROM OLD.completeness
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'controlled work product transition cannot change identity'
            USING ERRCODE = '55000';
    END IF;
    IF (
        NEW.title IS DISTINCT FROM OLD.title
        OR NEW.work_product_kind IS DISTINCT FROM OLD.work_product_kind
        OR NEW.current_version_id IS DISTINCT FROM OLD.current_version_id
        OR NEW.current_version_number IS DISTINCT FROM OLD.current_version_number
        OR NEW.current_content_sha256 IS DISTINCT FROM OLD.current_content_sha256
    ) AND NOT controlled_revision THEN
        RAISE EXCEPTION 'work product payload changes require the controlled revision writer'
            USING ERRCODE = '55000';
    END IF;
    IF NOT controlled_revision AND NOT (
        (OLD.status = 'draft' AND NEW.status IN ('in_review', 'withdrawn'))
        OR (OLD.status = 'in_review' AND NEW.status IN ('validated', 'withdrawn'))
        OR (OLD.status = 'validated' AND NEW.status IN ('approved', 'in_review'))
        OR (OLD.status = 'approved' AND NEW.status IN ('in_review', 'superseded', 'withdrawn'))
    ) THEN
        RAISE EXCEPTION 'work product transition is not an adjacent declared edge'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status IN ('validated', 'approved') THEN
        SELECT artifact.status INTO artifact_status
        FROM sklegal_legal.work_product_versions AS artifact
        WHERE artifact.tenant_id = NEW.tenant_id
          AND artifact.matter_id = NEW.matter_id
          AND artifact.id = NEW.current_version_id
          AND artifact.work_product_id = NEW.id
          AND artifact.version_number = NEW.current_version_number
          AND artifact.content_sha256 = NEW.current_content_sha256
        FOR UPDATE;
        IF artifact_status IS DISTINCT FROM 'frozen' THEN
            RAISE EXCEPTION 'work product gates require the exact current version to remain frozen'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    IF NEW.status = 'in_review'
       AND (NEW.validation_result_id IS NOT NULL OR NEW.approval_id IS NOT NULL) THEN
        RAISE EXCEPTION 'work product review reset must clear gate evidence'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'validated' AND NOT EXISTS (
        SELECT 1 FROM sklegal_legal.validations AS validation
        WHERE validation.tenant_id = NEW.tenant_id
          AND validation.matter_id = NEW.matter_id
          AND validation.id = NEW.validation_result_id
          AND validation.subject_kind = 'work_product_version'
          AND validation.subject_artifact_id = NEW.current_version_id
          AND validation.subject_artifact_version = NEW.current_version_number
          AND validation.subject_content_sha256 = NEW.current_content_sha256
          AND validation.outcome = 'passed'
          AND EXISTS (
              SELECT 1 FROM sklegal_legal.validation_checks AS check_record
              WHERE check_record.tenant_id = validation.tenant_id
                AND check_record.matter_id = validation.matter_id
                AND check_record.validation_id = validation.id
          )
    ) THEN
        RAISE EXCEPTION 'validated work product requires complete passed exact validation'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status IN ('approved', 'superseded') AND (
        NOT EXISTS (
            SELECT 1 FROM sklegal_legal.validations AS validation
            WHERE validation.tenant_id = NEW.tenant_id
              AND validation.matter_id = NEW.matter_id
              AND validation.id = NEW.validation_result_id
              AND validation.subject_kind = 'work_product_version'
              AND validation.subject_artifact_id = NEW.current_version_id
              AND validation.subject_artifact_version = NEW.current_version_number
              AND validation.subject_content_sha256 = NEW.current_content_sha256
              AND validation.outcome = 'passed'
              AND EXISTS (
                  SELECT 1 FROM sklegal_legal.validation_checks AS check_record
                  WHERE check_record.tenant_id = validation.tenant_id
                    AND check_record.matter_id = validation.matter_id
                    AND check_record.validation_id = validation.id
              )
        )
        OR NOT EXISTS (
            SELECT 1
            FROM sklegal_legal.approvals AS approval
            JOIN sklegal_legal.approval_history AS history
              ON history.tenant_id = approval.tenant_id
             AND history.matter_id = approval.matter_id
             AND history.approval_id = approval.id
             AND history.approval_version = approval.version
             AND history.subject_artifact_id = approval.subject_artifact_id
             AND history.subject_artifact_version = approval.subject_artifact_version
             AND history.subject_content_sha256 = approval.subject_content_sha256
            WHERE approval.tenant_id = NEW.tenant_id
              AND approval.matter_id = NEW.matter_id
              AND approval.id = NEW.approval_id
              AND approval.status = 'approved'
              AND approval.subject_artifact_id = NEW.current_version_id
              AND approval.subject_artifact_version = NEW.current_version_number
              AND approval.subject_content_sha256 = NEW.current_content_sha256
        )
    ) THEN
        RAISE EXCEPTION 'approved work product requires live passed validation and approval'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER zz_work_product_controlled_transition
BEFORE UPDATE ON sklegal_legal.work_products
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.enforce_work_product_transition();

CREATE FUNCTION sklegal_legal.transition_work_product(
    target_tenant_id uuid,
    target_matter_id uuid,
    target_work_product_id uuid,
    expected_version bigint,
    target_status text,
    supplied_validation_id uuid DEFAULT NULL,
    supplied_approval_id uuid DEFAULT NULL
)
RETURNS sklegal_legal.work_products
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    work_product_record sklegal_legal.work_products%ROWTYPE;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_legal.has_matter_membership(target_tenant_id, target_matter_id) THEN
        RAISE EXCEPTION 'work product transition is not authorized'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO work_product_record
    FROM sklegal_legal.work_products
    WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
      AND id = target_work_product_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'work product does not exist in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;
    IF work_product_record.version <> expected_version THEN
        RAISE EXCEPTION 'work product version conflict' USING ERRCODE = '40001';
    END IF;
    PERFORM sklegal_legal.lock_exact_work_product_version(
        target_tenant_id,
        target_matter_id,
        work_product_record.current_version_id,
        work_product_record.current_version_number,
        work_product_record.current_content_sha256
    );
    IF work_product_record.status IN ('validated', 'approved')
       AND target_status = 'in_review' THEN
        RAISE EXCEPTION 'review reset requires the complete controlled revision writer'
            USING ERRCODE = '23514';
    END IF;
    IF target_status = 'in_review' THEN
        work_product_record.validation_result_id := NULL;
        work_product_record.approval_id := NULL;
    ELSIF work_product_record.status = 'in_review' AND target_status = 'validated' THEN
        work_product_record.validation_result_id := supplied_validation_id;
    ELSIF work_product_record.status = 'validated' AND target_status = 'approved' THEN
        work_product_record.approval_id := supplied_approval_id;
        PERFORM sklegal_legal.lock_approval_decision(
            target_tenant_id,
            target_matter_id,
            supplied_approval_id,
            work_product_record.current_version_id,
            work_product_record.current_version_number,
            work_product_record.current_content_sha256,
            NULL,
            true
        );
    ELSIF supplied_validation_id IS NOT NULL OR supplied_approval_id IS NOT NULL THEN
        RAISE EXCEPTION 'gate evidence is not valid for this work product transition'
            USING ERRCODE = '23514';
    END IF;
    UPDATE sklegal_legal.work_products
    SET status = target_status,
        validation_result_id = work_product_record.validation_result_id,
        approval_id = work_product_record.approval_id,
        version = version + 1
    WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
      AND id = target_work_product_id
    RETURNING * INTO work_product_record;
    RETURN work_product_record;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.transition_work_product(
    uuid, uuid, uuid, bigint, text, uuid, uuid
) FROM PUBLIC;

CREATE FUNCTION sklegal_legal.revise_work_product(
    target_tenant_id uuid,
    target_matter_id uuid,
    target_work_product_id uuid,
    expected_version bigint,
    replacement_title text,
    replacement_kind text,
    replacement_version_id uuid,
    replacement_version_number bigint,
    replacement_content_sha256 text
)
RETURNS sklegal_legal.work_products
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    work_product_record sklegal_legal.work_products%ROWTYPE;
    replacement_parent_id uuid;
    replacement_status text;
    revised_status text;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_legal.has_matter_membership(target_tenant_id, target_matter_id) THEN
        RAISE EXCEPTION 'work product revision is not authorized'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO work_product_record
    FROM sklegal_legal.work_products
    WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
      AND id = target_work_product_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'work product does not exist in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;
    IF work_product_record.version <> expected_version THEN
        RAISE EXCEPTION 'work product version conflict' USING ERRCODE = '40001';
    END IF;
    IF work_product_record.status NOT IN ('draft', 'in_review', 'validated', 'approved') THEN
        RAISE EXCEPTION 'work product cannot be revised in its current state'
            USING ERRCODE = '23514';
    END IF;
    SELECT work_product_id INTO replacement_parent_id
    FROM sklegal_legal.work_product_versions
    WHERE tenant_id = target_tenant_id
      AND matter_id = target_matter_id
      AND id = replacement_version_id
      AND version_number = replacement_version_number
      AND content_sha256 = replacement_content_sha256;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'replacement work product version does not exist in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;
    IF replacement_parent_id IS DISTINCT FROM target_work_product_id THEN
        RAISE EXCEPTION 'replacement version must belong to the same work product'
            USING ERRCODE = '23514';
    END IF;
    SELECT artifact.status INTO replacement_status
    FROM sklegal_legal.lock_exact_work_product_version(
        target_tenant_id,
        target_matter_id,
        replacement_version_id,
        replacement_version_number,
        replacement_content_sha256
    ) AS artifact;
    IF replacement_status NOT IN ('draft', 'frozen') THEN
        RAISE EXCEPTION 'replacement must bind a complete current non-superseded version'
            USING ERRCODE = '23514';
    END IF;
    revised_status := CASE
        WHEN work_product_record.status IN ('validated', 'approved') THEN 'in_review'
        ELSE work_product_record.status
    END;
    UPDATE sklegal_legal.work_products
    SET title = replacement_title,
        work_product_kind = replacement_kind,
        current_version_id = replacement_version_id,
        current_version_number = replacement_version_number,
        current_content_sha256 = replacement_content_sha256,
        validation_result_id = NULL,
        approval_id = NULL,
        status = revised_status,
        version = version + 1
    WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
      AND id = target_work_product_id
    RETURNING * INTO work_product_record;
    RETURN work_product_record;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.revise_work_product(
    uuid, uuid, uuid, bigint, text, text, uuid, bigint, text
) FROM PUBLIC;

ALTER TABLE sklegal_legal.authorities
ADD CONSTRAINT authority_applicability_validation_fk
FOREIGN KEY (tenant_id, matter_id, applicability_validation_id)
REFERENCES sklegal_legal.validations(tenant_id, matter_id, id);

ALTER TABLE sklegal_legal.claims
ADD CONSTRAINT claim_acceptance_validation_fk
FOREIGN KEY (tenant_id, matter_id, acceptance_validation_id)
REFERENCES sklegal_legal.validations(tenant_id, matter_id, id);

ALTER TABLE sklegal_legal.defenses
ADD CONSTRAINT defense_acceptance_validation_fk
FOREIGN KEY (tenant_id, matter_id, acceptance_validation_id)
REFERENCES sklegal_legal.validations(tenant_id, matter_id, id);

ALTER TABLE sklegal_legal.deadlines
ADD CONSTRAINT deadline_review_validation_fk
FOREIGN KEY (tenant_id, matter_id, review_validation_id)
REFERENCES sklegal_legal.validations(tenant_id, matter_id, id);

CREATE TABLE sklegal_legal.executions (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    subject_artifact_id uuid NOT NULL,
    subject_artifact_version sklegal_legal.record_version NOT NULL,
    subject_content_sha256 sklegal_legal.sha256_digest NOT NULL,
    destination_sha256 sklegal_legal.sha256_digest NOT NULL,
    idempotency_key text NOT NULL CHECK (length(btrim(idempotency_key)) BETWEEN 1 AND 512),
    validation_result_id uuid,
    approval_id uuid,
    approval_version sklegal_legal.record_version,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'validated', 'approved', 'queued', 'dispatched', 'receipt_verified', 'failed', 'cancelled')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, idempotency_key),
    UNIQUE (
        tenant_id, matter_id, id, subject_artifact_id,
        subject_artifact_version, subject_content_sha256, destination_sha256
    ),
    FOREIGN KEY (tenant_id, matter_id, subject_artifact_id, subject_artifact_version, subject_content_sha256)
        REFERENCES sklegal_legal.work_product_versions(tenant_id, matter_id, id, version_number, content_sha256),
    FOREIGN KEY (tenant_id, matter_id, validation_result_id, subject_artifact_id, subject_artifact_version, subject_content_sha256)
        REFERENCES sklegal_legal.validations(tenant_id, matter_id, id, subject_artifact_id, subject_artifact_version, subject_content_sha256),
    FOREIGN KEY (tenant_id, matter_id, approval_id, subject_artifact_id, subject_artifact_version, subject_content_sha256)
        REFERENCES sklegal_legal.approvals(tenant_id, matter_id, id, subject_artifact_id, subject_artifact_version, subject_content_sha256),
    FOREIGN KEY (
        tenant_id, matter_id, approval_id, approval_version,
        subject_artifact_id, subject_artifact_version, subject_content_sha256
    ) REFERENCES sklegal_legal.approval_history(
        tenant_id, matter_id, approval_id, approval_version,
        subject_artifact_id, subject_artifact_version, subject_content_sha256
    ),
    CHECK ((approval_id IS NULL) = (approval_version IS NULL)),
    CHECK (
        (status = 'draft' AND validation_result_id IS NULL
            AND approval_id IS NULL AND approval_version IS NULL)
        OR (status = 'validated' AND validation_result_id IS NOT NULL
            AND approval_id IS NULL AND approval_version IS NULL)
        OR (status IN ('approved', 'queued', 'dispatched', 'receipt_verified', 'failed')
            AND validation_result_id IS NOT NULL
            AND approval_id IS NOT NULL AND approval_version IS NOT NULL)
        OR status = 'cancelled'
    ),
    CHECK (updated_at >= created_at)
);

CREATE FUNCTION sklegal_legal.require_execution_gate_evidence()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    artifact_record sklegal_legal.work_product_versions%ROWTYPE;
BEGIN
    SELECT * INTO artifact_record
    FROM sklegal_legal.lock_exact_work_product_version(
        NEW.tenant_id,
        NEW.matter_id,
        NEW.subject_artifact_id,
        NEW.subject_artifact_version,
        NEW.subject_content_sha256
    );
    IF artifact_record.status = 'superseded' THEN
        RAISE EXCEPTION 'execution cannot bind a superseded work product version'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'draft'
       OR (NEW.status = 'cancelled' AND NEW.validation_result_id IS NULL) THEN
        RETURN NEW;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM sklegal_legal.validations AS validation
        JOIN sklegal_legal.work_product_versions AS artifact
          ON artifact.tenant_id = validation.tenant_id
         AND artifact.matter_id = validation.matter_id
         AND artifact.id = validation.subject_artifact_id
         AND artifact.version_number = validation.subject_artifact_version
         AND artifact.content_sha256 = validation.subject_content_sha256
        WHERE validation.tenant_id = NEW.tenant_id
          AND validation.matter_id = NEW.matter_id
          AND validation.id = NEW.validation_result_id
          AND validation.subject_kind = 'work_product_version'
          AND validation.outcome = 'passed' AND artifact.status = 'frozen'
          AND validation.subject_artifact_id = NEW.subject_artifact_id
          AND validation.subject_artifact_version = NEW.subject_artifact_version
          AND validation.subject_content_sha256 = NEW.subject_content_sha256
          AND EXISTS (
              SELECT 1 FROM sklegal_legal.validation_checks AS check_record
              WHERE check_record.tenant_id = validation.tenant_id
                AND check_record.matter_id = validation.matter_id
                AND check_record.validation_id = validation.id
          )
    ) THEN
        RAISE EXCEPTION 'execution requires passed validation for the exact artifact'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'validated'
       OR (NEW.status = 'cancelled' AND NEW.approval_id IS NULL) THEN
        RETURN NEW;
    END IF;
    IF NEW.status IN ('approved', 'queued', 'dispatched') THEN
        IF NOT EXISTS (
            SELECT 1
            FROM sklegal_legal.approvals AS approval
            JOIN sklegal_legal.approval_history AS history
              ON history.tenant_id = approval.tenant_id
             AND history.matter_id = approval.matter_id
             AND history.approval_id = approval.id
             AND history.approval_version = approval.version
             AND history.subject_artifact_id = approval.subject_artifact_id
             AND history.subject_artifact_version = approval.subject_artifact_version
             AND history.subject_content_sha256 = approval.subject_content_sha256
            WHERE approval.tenant_id = NEW.tenant_id
              AND approval.matter_id = NEW.matter_id
              AND approval.id = NEW.approval_id
              AND approval.version = NEW.approval_version
              AND approval.status = 'approved'
              AND approval.subject_artifact_id = NEW.subject_artifact_id
              AND approval.subject_artifact_version = NEW.subject_artifact_version
              AND approval.subject_content_sha256 = NEW.subject_content_sha256
        ) THEN
            RAISE EXCEPTION 'execution requires current approval for the exact artifact'
                USING ERRCODE = '23514';
        END IF;
    ELSIF NOT EXISTS (
        SELECT 1
        FROM sklegal_legal.approval_history AS history
        WHERE history.tenant_id = NEW.tenant_id
          AND history.matter_id = NEW.matter_id
          AND history.approval_id = NEW.approval_id
          AND history.approval_version = NEW.approval_version
          AND history.subject_artifact_id = NEW.subject_artifact_id
          AND history.subject_artifact_version = NEW.subject_artifact_version
          AND history.subject_content_sha256 = NEW.subject_content_sha256
    ) THEN
        RAISE EXCEPTION 'execution requires its immutable approved decision snapshot'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER execution_exact_gate
BEFORE INSERT OR UPDATE ON sklegal_legal.executions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_execution_gate_evidence();

CREATE FUNCTION sklegal_legal.require_draft_execution_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    artifact_record sklegal_legal.work_product_versions%ROWTYPE;
BEGIN
    IF NEW.status <> 'draft' OR NEW.validation_result_id IS NOT NULL
       OR NEW.approval_id IS NOT NULL OR NEW.approval_version IS NOT NULL THEN
        RAISE EXCEPTION 'execution must be inserted as an ungated draft'
            USING ERRCODE = '23514';
    END IF;
    SELECT * INTO artifact_record
    FROM sklegal_legal.lock_exact_work_product_version(
        NEW.tenant_id,
        NEW.matter_id,
        NEW.subject_artifact_id,
        NEW.subject_artifact_version,
        NEW.subject_content_sha256
    );
    IF artifact_record.status = 'superseded' THEN
        RAISE EXCEPTION 'execution cannot bind a superseded work product version'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER execution_draft_insert_only
BEFORE INSERT ON sklegal_legal.executions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_draft_execution_insert();

CREATE TABLE sklegal_legal.execution_receipts (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    execution_id uuid NOT NULL,
    connector text NOT NULL CHECK (length(btrim(connector)) BETWEEN 1 AND 512),
    external_receipt_id text NOT NULL CHECK (length(btrim(external_receipt_id)) BETWEEN 1 AND 512),
    artifact_content_sha256 sklegal_legal.sha256_digest NOT NULL,
    destination_sha256 sklegal_legal.sha256_digest NOT NULL,
    received_at timestamptz NOT NULL,
    verified_at timestamptz NOT NULL,
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, execution_id, external_receipt_id),
    FOREIGN KEY (tenant_id, matter_id, execution_id) REFERENCES sklegal_legal.executions(tenant_id, matter_id, id),
    CHECK (received_at <= verified_at),
    CHECK (verified_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE FUNCTION sklegal_legal.require_matching_execution_receipt()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM sklegal_legal.executions
        WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id AND id = NEW.execution_id
          AND subject_content_sha256 = NEW.artifact_content_sha256
          AND destination_sha256 = NEW.destination_sha256
          AND status = 'dispatched'
    ) THEN
        RAISE EXCEPTION 'receipt must match a dispatched execution artifact and destination'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER execution_receipt_binding
BEFORE INSERT ON sklegal_legal.execution_receipts
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_matching_execution_receipt();

CREATE TABLE sklegal_legal.execution_events (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    execution_id uuid NOT NULL,
    sequence_no sklegal_legal.record_version NOT NULL,
    step text NOT NULL CHECK (step IN ('validated', 'approved', 'queued', 'dispatched', 'receipt_verified', 'failed', 'cancelled')),
    occurred_at timestamptz NOT NULL,
    correlation_id text NOT NULL CHECK (length(btrim(correlation_id)) BETWEEN 1 AND 512),
    actor_principal_id uuid NOT NULL,
    receipt_id uuid,
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, execution_id, step, occurred_at),
    UNIQUE (tenant_id, matter_id, execution_id, sequence_no),
    FOREIGN KEY (tenant_id, matter_id, execution_id) REFERENCES sklegal_legal.executions(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, actor_principal_id) REFERENCES sklegal_identity.principals(tenant_id, id),
    FOREIGN KEY (tenant_id, matter_id, receipt_id) REFERENCES sklegal_legal.execution_receipts(tenant_id, matter_id, id),
    CHECK ((step = 'receipt_verified') = (receipt_id IS NOT NULL)),
    CHECK (occurred_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE FUNCTION sklegal_legal.require_matching_execution_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM sklegal_legal.executions
        WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
          AND id = NEW.execution_id AND version = NEW.sequence_no
          AND (
              (status = 'draft' AND NEW.step IN ('validated', 'cancelled'))
              OR (status = 'validated' AND NEW.step IN ('approved', 'cancelled'))
              OR (status = 'approved' AND NEW.step IN ('queued', 'cancelled'))
              OR (status = 'queued' AND NEW.step IN ('dispatched', 'failed', 'cancelled'))
              OR (status = 'dispatched' AND NEW.step IN ('receipt_verified', 'failed'))
              OR (status = 'failed' AND NEW.step = 'queued')
          )
    ) THEN
        RAISE EXCEPTION 'execution event must prove the next adjacent state'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.receipt_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM sklegal_legal.execution_receipts
        WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
          AND id = NEW.receipt_id AND execution_id = NEW.execution_id
    ) THEN
        RAISE EXCEPTION 'execution event receipt must bind the same execution'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER execution_event_binding
BEFORE INSERT ON sklegal_legal.execution_events
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_matching_execution_event();

CREATE FUNCTION sklegal_legal.transition_execution(
    target_tenant_id uuid,
    target_matter_id uuid,
    target_execution_id uuid,
    expected_version bigint,
    target_status text,
    correlation text,
    supplied_validation_id uuid DEFAULT NULL,
    supplied_approval_id uuid DEFAULT NULL,
    supplied_receipt_id uuid DEFAULT NULL,
    receipt_connector text DEFAULT NULL,
    receipt_external_id text DEFAULT NULL,
    receipt_received_at timestamptz DEFAULT NULL,
    receipt_verified_at timestamptz DEFAULT NULL
)
RETURNS sklegal_legal.executions
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    execution_record sklegal_legal.executions%ROWTYPE;
    approval_snapshot sklegal_legal.approval_history%ROWTYPE;
    artifact_id uuid;
    artifact_version bigint;
    artifact_content_sha256 text;
    initial_status text;
    initial_approval_id uuid;
    initial_approval_version bigint;
    event_record record;
    simulated_status text := 'draft';
    expected_sequence bigint := 1;
    transition_at timestamptz;
    event_recorded_at timestamptz;
    transition_receipt_id uuid := NULL;
    actor_id uuid;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_legal.has_matter_membership(target_tenant_id, target_matter_id) THEN
        RAISE EXCEPTION 'execution transition is not authorized'
            USING ERRCODE = '42501';
    END IF;
    actor_id := sklegal_identity.current_principal_id();
    IF length(btrim(correlation)) NOT BETWEEN 1 AND 512 THEN
        RAISE EXCEPTION 'correlation id length is invalid' USING ERRCODE = '22023';
    END IF;

    SELECT subject_artifact_id, subject_artifact_version, subject_content_sha256,
           status, approval_id, approval_version
    INTO artifact_id, artifact_version, artifact_content_sha256,
         initial_status, initial_approval_id, initial_approval_version
    FROM sklegal_legal.executions
    WHERE tenant_id = target_tenant_id
      AND matter_id = target_matter_id
      AND id = target_execution_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'execution does not exist in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;
    PERFORM sklegal_legal.lock_exact_work_product_version(
        target_tenant_id,
        target_matter_id,
        artifact_id,
        artifact_version,
        artifact_content_sha256
    );
    IF initial_status = 'validated' AND target_status = 'approved' THEN
        SELECT * INTO approval_snapshot
        FROM sklegal_legal.lock_approval_decision(
            target_tenant_id,
            target_matter_id,
            supplied_approval_id,
            artifact_id,
            artifact_version,
            artifact_content_sha256,
            NULL,
            true
        );
    ELSIF initial_approval_id IS NOT NULL THEN
        SELECT * INTO approval_snapshot
        FROM sklegal_legal.lock_approval_decision(
            target_tenant_id,
            target_matter_id,
            initial_approval_id,
            artifact_id,
            artifact_version,
            artifact_content_sha256,
            initial_approval_version,
            (initial_status = 'approved' AND target_status = 'queued')
            OR (initial_status = 'queued' AND target_status = 'dispatched')
            OR (initial_status = 'failed' AND target_status = 'queued')
        );
    END IF;
    SELECT * INTO execution_record
    FROM sklegal_legal.executions
    WHERE tenant_id = target_tenant_id
      AND matter_id = target_matter_id
      AND id = target_execution_id
      AND subject_artifact_id = artifact_id
      AND subject_artifact_version = artifact_version
      AND subject_content_sha256 = artifact_content_sha256
      AND status = initial_status
      AND approval_id IS NOT DISTINCT FROM initial_approval_id
      AND approval_version IS NOT DISTINCT FROM initial_approval_version
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'execution binding changed before it could be locked'
            USING ERRCODE = '40001';
    END IF;
    IF execution_record.version <> expected_version THEN
        RAISE EXCEPTION 'execution version conflict'
            USING ERRCODE = '40001';
    END IF;

    FOR event_record IN
        SELECT sequence_no, step
        FROM sklegal_legal.execution_events
        WHERE tenant_id = target_tenant_id
          AND matter_id = target_matter_id
          AND execution_id = target_execution_id
        ORDER BY sequence_no
    LOOP
        IF event_record.sequence_no <> expected_sequence THEN
            RAISE EXCEPTION 'stored execution event sequence has a gap'
                USING ERRCODE = '55000';
        END IF;
        IF NOT (
            (simulated_status = 'draft' AND event_record.step IN ('validated', 'cancelled'))
            OR (simulated_status = 'validated' AND event_record.step IN ('approved', 'cancelled'))
            OR (simulated_status = 'approved' AND event_record.step IN ('queued', 'cancelled'))
            OR (simulated_status = 'queued' AND event_record.step IN ('dispatched', 'failed', 'cancelled'))
            OR (simulated_status = 'dispatched' AND event_record.step IN ('receipt_verified', 'failed'))
            OR (simulated_status = 'failed' AND event_record.step = 'queued')
        ) THEN
            RAISE EXCEPTION 'stored execution event prefix is invalid'
                USING ERRCODE = '55000';
        END IF;
        simulated_status := event_record.step;
        expected_sequence := expected_sequence + 1;
    END LOOP;
    IF simulated_status <> execution_record.status
       OR (SELECT count(*) FROM sklegal_legal.execution_events
           WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
             AND execution_id = target_execution_id) <> execution_record.version - 1 THEN
        RAISE EXCEPTION 'stored execution history does not exactly prove current state'
            USING ERRCODE = '55000';
    END IF;

    IF NOT (
        (execution_record.status = 'draft' AND target_status IN ('validated', 'cancelled'))
        OR (execution_record.status = 'validated' AND target_status IN ('approved', 'cancelled'))
        OR (execution_record.status = 'approved' AND target_status IN ('queued', 'cancelled'))
        OR (execution_record.status = 'queued' AND target_status IN ('dispatched', 'failed', 'cancelled'))
        OR (execution_record.status = 'dispatched' AND target_status IN ('receipt_verified', 'failed'))
        OR (execution_record.status = 'failed' AND target_status = 'queued')
    ) THEN
        RAISE EXCEPTION 'execution transition is not an adjacent declared edge'
            USING ERRCODE = '23514';
    END IF;

    IF execution_record.status = 'draft' AND target_status = 'validated' THEN
        execution_record.validation_result_id := supplied_validation_id;
    ELSIF supplied_validation_id IS NOT NULL
          AND supplied_validation_id IS DISTINCT FROM execution_record.validation_result_id THEN
        RAISE EXCEPTION 'execution validation evidence cannot be replaced'
            USING ERRCODE = '55000';
    END IF;
    IF execution_record.status = 'validated' AND target_status = 'approved' THEN
        execution_record.approval_id := supplied_approval_id;
        execution_record.approval_version := approval_snapshot.approval_version;
    ELSIF supplied_approval_id IS NOT NULL
          AND supplied_approval_id IS DISTINCT FROM execution_record.approval_id THEN
        RAISE EXCEPTION 'execution approval evidence cannot be replaced'
            USING ERRCODE = '55000';
    END IF;

    transition_at := clock_timestamp();
    IF target_status = 'receipt_verified' THEN
        IF supplied_receipt_id IS NULL OR receipt_connector IS NULL
           OR receipt_external_id IS NULL OR receipt_received_at IS NULL
           OR receipt_verified_at IS NULL
           OR receipt_received_at > receipt_verified_at
           OR receipt_verified_at > transition_at THEN
            RAISE EXCEPTION 'receipt verification requires complete ordered receipt evidence'
                USING ERRCODE = '23514';
        END IF;
        INSERT INTO sklegal_legal.execution_receipts (
            id, tenant_id, matter_id, execution_id, connector, external_receipt_id,
            artifact_content_sha256, destination_sha256, received_at, verified_at,
            classification, completeness
        ) VALUES (
            supplied_receipt_id, target_tenant_id, target_matter_id, target_execution_id,
            receipt_connector, receipt_external_id, execution_record.subject_content_sha256,
            execution_record.destination_sha256, receipt_received_at, receipt_verified_at,
            execution_record.classification, execution_record.completeness
        );
        transition_receipt_id := supplied_receipt_id;
    ELSIF supplied_receipt_id IS NOT NULL OR receipt_connector IS NOT NULL
          OR receipt_external_id IS NOT NULL OR receipt_received_at IS NOT NULL
          OR receipt_verified_at IS NOT NULL THEN
        RAISE EXCEPTION 'receipt evidence is valid only for receipt verification'
            USING ERRCODE = '23514';
    END IF;

    INSERT INTO sklegal_legal.execution_events (
        id, tenant_id, matter_id, execution_id, sequence_no, step, occurred_at,
        correlation_id, actor_principal_id, receipt_id, classification, completeness
    ) VALUES (
        gen_random_uuid(), target_tenant_id, target_matter_id, target_execution_id,
        execution_record.version, target_status, transition_at, correlation,
        actor_id, transition_receipt_id, execution_record.classification,
        execution_record.completeness
    ) RETURNING updated_at INTO event_recorded_at;

    UPDATE sklegal_legal.executions
    SET status = target_status,
        validation_result_id = execution_record.validation_result_id,
        approval_id = execution_record.approval_id,
        approval_version = execution_record.approval_version,
        version = execution_record.version + 1,
        updated_at = event_recorded_at
    WHERE tenant_id = target_tenant_id
      AND matter_id = target_matter_id
      AND id = target_execution_id
    RETURNING * INTO execution_record;
    RETURN execution_record;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.transition_execution(
    uuid, uuid, uuid, bigint, text, text, uuid, uuid, uuid, text, text,
    timestamptz, timestamptz
) FROM PUBLIC;

CREATE TABLE sklegal_legal.communications (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    direction text NOT NULL CHECK (direction IN ('inbound', 'outbound', 'internal')),
    channel text NOT NULL CHECK (channel IN ('email', 'mail', 'service', 'filing', 'calendar', 'other')),
    subject text NOT NULL CHECK (length(btrim(subject)) BETWEEN 1 AND 512),
    work_product_version_id uuid,
    work_product_version_number sklegal_legal.record_version,
    work_product_content_sha256 sklegal_legal.sha256_digest,
    destination_verified boolean NOT NULL DEFAULT false,
    destination_sha256 sklegal_legal.sha256_digest,
    validation_result_id uuid,
    approval_id uuid,
    execution_id uuid,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'validated', 'approved', 'queued', 'dispatched', 'receipt_verified', 'failed', 'cancelled')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, work_product_version_id, work_product_version_number, work_product_content_sha256)
        REFERENCES sklegal_legal.work_product_versions(tenant_id, matter_id, id, version_number, content_sha256),
    FOREIGN KEY (tenant_id, matter_id, validation_result_id, work_product_version_id, work_product_version_number, work_product_content_sha256)
        REFERENCES sklegal_legal.validations(tenant_id, matter_id, id, subject_artifact_id, subject_artifact_version, subject_content_sha256),
    FOREIGN KEY (tenant_id, matter_id, approval_id, work_product_version_id, work_product_version_number, work_product_content_sha256)
        REFERENCES sklegal_legal.approvals(tenant_id, matter_id, id, subject_artifact_id, subject_artifact_version, subject_content_sha256),
    FOREIGN KEY (tenant_id, matter_id, execution_id, work_product_version_id, work_product_version_number, work_product_content_sha256, destination_sha256)
        REFERENCES sklegal_legal.executions(tenant_id, matter_id, id, subject_artifact_id, subject_artifact_version, subject_content_sha256, destination_sha256),
    CHECK (
        (work_product_version_id IS NULL AND work_product_version_number IS NULL AND work_product_content_sha256 IS NULL)
        OR
        (work_product_version_id IS NOT NULL AND work_product_version_number IS NOT NULL AND work_product_content_sha256 IS NOT NULL)
    ),
    CHECK (status IN ('draft', 'cancelled') OR work_product_version_id IS NOT NULL),
    CHECK (status NOT IN ('validated', 'approved', 'queued', 'dispatched', 'receipt_verified', 'failed') OR validation_result_id IS NOT NULL),
    CHECK (status NOT IN ('approved', 'queued', 'dispatched', 'receipt_verified', 'failed') OR approval_id IS NOT NULL),
    CHECK (status NOT IN ('queued', 'dispatched', 'receipt_verified', 'failed') OR (
        destination_verified AND destination_sha256 IS NOT NULL AND execution_id IS NOT NULL
    )),
    CHECK (destination_verified OR destination_sha256 IS NULL),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.communication_participants (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    communication_id uuid NOT NULL,
    party_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, communication_id, party_id),
    FOREIGN KEY (tenant_id, matter_id, communication_id) REFERENCES sklegal_legal.communications(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, party_id) REFERENCES sklegal_legal.parties(tenant_id, matter_id, id)
);

CREATE FUNCTION sklegal_legal.validate_communication_participants()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    new_record jsonb := to_jsonb(NEW);
    old_record jsonb := to_jsonb(OLD);
    selected_tenant uuid := COALESCE(
        (new_record->>'tenant_id')::uuid, (old_record->>'tenant_id')::uuid
    );
    selected_matter uuid := COALESCE(
        (new_record->>'matter_id')::uuid, (old_record->>'matter_id')::uuid
    );
    selected_id uuid := COALESCE(
        (new_record->>'communication_id')::uuid, (new_record->>'id')::uuid,
        (old_record->>'communication_id')::uuid, (old_record->>'id')::uuid
    );
BEGIN
    IF EXISTS (
        SELECT 1 FROM sklegal_legal.communications
        WHERE tenant_id = selected_tenant AND matter_id = selected_matter
          AND id = selected_id
    ) AND NOT EXISTS (
        SELECT 1 FROM sklegal_legal.communication_participants
        WHERE tenant_id = selected_tenant AND matter_id = selected_matter
          AND communication_id = selected_id
    ) THEN
        RAISE EXCEPTION 'communication requires at least one participant'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$function$;

CREATE CONSTRAINT TRIGGER communication_participants_complete
AFTER INSERT OR UPDATE ON sklegal_legal.communications
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_communication_participants();
CREATE CONSTRAINT TRIGGER communication_participant_links_complete
AFTER INSERT OR UPDATE OR DELETE ON sklegal_legal.communication_participants
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_communication_participants();

CREATE FUNCTION sklegal_legal.require_draft_communication_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    artifact_record sklegal_legal.work_product_versions%ROWTYPE;
BEGIN
    IF NEW.status <> 'draft' OR NEW.validation_result_id IS NOT NULL
       OR NEW.approval_id IS NOT NULL OR NEW.destination_verified
       OR NEW.destination_sha256 IS NOT NULL OR NEW.execution_id IS NOT NULL THEN
        RAISE EXCEPTION 'communication must be inserted as an ungated draft'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.work_product_version_id IS NOT NULL
       AND NEW.work_product_version_number IS NOT NULL
       AND NEW.work_product_content_sha256 IS NOT NULL THEN
        SELECT * INTO artifact_record
        FROM sklegal_legal.lock_exact_work_product_version(
            NEW.tenant_id,
            NEW.matter_id,
            NEW.work_product_version_id,
            NEW.work_product_version_number,
            NEW.work_product_content_sha256
        );
        IF artifact_record.status = 'superseded' THEN
            RAISE EXCEPTION 'communication cannot bind a superseded work product version'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER communication_draft_insert_only
BEFORE INSERT ON sklegal_legal.communications
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_draft_communication_insert();

CREATE FUNCTION sklegal_legal.create_communication(
    target_tenant_id uuid,
    target_matter_id uuid,
    target_communication_id uuid,
    initial_direction text,
    initial_channel text,
    initial_subject text,
    initial_participant_ids uuid[],
    initial_work_product_version_id uuid DEFAULT NULL,
    initial_work_product_version_number bigint DEFAULT NULL,
    initial_work_product_content_sha256 text DEFAULT NULL,
    initial_classification sklegal_legal.data_classification DEFAULT 'confidential',
    initial_completeness sklegal_legal.record_completeness DEFAULT 'incomplete',
    initial_created_at timestamptz DEFAULT NULL,
    initial_updated_at timestamptz DEFAULT NULL
)
RETURNS sklegal_legal.communications
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    communication_record sklegal_legal.communications%ROWTYPE;
    created_time timestamptz := COALESCE(initial_created_at, clock_timestamp());
    updated_time timestamptz := COALESCE(initial_updated_at, created_time);
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_legal.has_matter_membership(target_tenant_id, target_matter_id) THEN
        RAISE EXCEPTION 'communication creation is not authorized'
            USING ERRCODE = '42501';
    END IF;
    IF initial_participant_ids IS NULL
       OR cardinality(initial_participant_ids) = 0
       OR cardinality(initial_participant_ids) <> (
           SELECT count(DISTINCT participant_id)
           FROM unnest(initial_participant_ids) AS participant_id
       )
       OR EXISTS (
           SELECT 1 FROM unnest(initial_participant_ids) AS participant_id
           WHERE participant_id IS NULL
              OR participant_id = '00000000-0000-0000-0000-000000000000'::uuid
       ) THEN
        RAISE EXCEPTION 'communication creation requires unique non-nil participants'
            USING ERRCODE = '23514';
    END IF;
    IF (
        initial_work_product_version_id IS NULL
        OR initial_work_product_version_number IS NULL
        OR initial_work_product_content_sha256 IS NULL
    ) AND NOT (
        initial_work_product_version_id IS NULL
        AND initial_work_product_version_number IS NULL
        AND initial_work_product_content_sha256 IS NULL
    ) THEN
        RAISE EXCEPTION 'communication artifact binding must be a complete tuple'
            USING ERRCODE = '23514';
    END IF;
    IF initial_work_product_version_id IS NOT NULL THEN
        PERFORM sklegal_legal.lock_exact_work_product_version(
            target_tenant_id,
            target_matter_id,
            initial_work_product_version_id,
            initial_work_product_version_number,
            initial_work_product_content_sha256
        );
    END IF;

    INSERT INTO sklegal_legal.communications (
        id, tenant_id, matter_id, direction, channel, subject,
        work_product_version_id, work_product_version_number,
        work_product_content_sha256, status, classification, completeness,
        version, created_at, updated_at
    ) VALUES (
        target_communication_id, target_tenant_id, target_matter_id,
        initial_direction, initial_channel, initial_subject,
        initial_work_product_version_id, initial_work_product_version_number,
        initial_work_product_content_sha256, 'draft', initial_classification,
        initial_completeness, 1, created_time, updated_time
    ) RETURNING * INTO communication_record;

    INSERT INTO sklegal_legal.communication_participants (
        tenant_id, matter_id, communication_id, party_id, created_at
    )
    SELECT target_tenant_id, target_matter_id, target_communication_id,
           participant_id, created_time
    FROM unnest(initial_participant_ids) AS participant_id;

    RETURN communication_record;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.create_communication(
    uuid, uuid, uuid, text, text, text, uuid[], uuid, bigint, text,
    sklegal_legal.data_classification, sklegal_legal.record_completeness,
    timestamptz, timestamptz
) FROM PUBLIC;

CREATE FUNCTION sklegal_legal.enforce_communication_transition()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    required_execution_status text;
    controlled_revision boolean :=
        current_user = 'sklegal_migrator' AND session_user <> current_user
        AND (
            (OLD.status = 'draft' AND NEW.status = 'draft')
            OR (OLD.status IN ('validated', 'approved') AND NEW.status = 'draft')
        );
BEGIN
    IF current_user = 'sklegal_migrator' AND session_user <> current_user
       AND NEW.status = 'draft' AND OLD.status = 'draft'
       AND (to_jsonb(NEW) - ARRAY['version', 'updated_at'])
           = (to_jsonb(OLD) - ARRAY['version', 'updated_at']) THEN
        RETURN NEW;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.matter_id IS DISTINCT FROM OLD.matter_id
       OR NEW.classification IS DISTINCT FROM OLD.classification
       OR NEW.completeness IS DISTINCT FROM OLD.completeness
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'controlled communication transition cannot change identity'
            USING ERRCODE = '55000';
    END IF;
    IF (
        NEW.direction IS DISTINCT FROM OLD.direction
        OR NEW.channel IS DISTINCT FROM OLD.channel
        OR NEW.subject IS DISTINCT FROM OLD.subject
        OR NEW.work_product_version_id IS DISTINCT FROM OLD.work_product_version_id
        OR NEW.work_product_version_number IS DISTINCT FROM OLD.work_product_version_number
        OR NEW.work_product_content_sha256 IS DISTINCT FROM OLD.work_product_content_sha256
    ) AND NOT controlled_revision THEN
        RAISE EXCEPTION 'communication payload changes require the controlled revision writer'
            USING ERRCODE = '55000';
    END IF;
    IF NOT controlled_revision AND NOT (
        (OLD.status = 'draft' AND NEW.status IN ('validated', 'cancelled'))
        OR (OLD.status = 'validated' AND NEW.status IN ('draft', 'approved', 'cancelled'))
        OR (OLD.status = 'approved' AND NEW.status IN ('draft', 'queued', 'cancelled'))
        OR (OLD.status = 'queued' AND NEW.status IN ('dispatched', 'failed', 'cancelled'))
        OR (OLD.status = 'dispatched' AND NEW.status IN ('receipt_verified', 'failed'))
        OR (OLD.status = 'failed' AND NEW.status = 'queued')
    ) THEN
        RAISE EXCEPTION 'communication transition is not an adjacent declared edge'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'draft' AND (
        NEW.validation_result_id IS NOT NULL OR NEW.approval_id IS NOT NULL
        OR NEW.destination_verified OR NEW.destination_sha256 IS NOT NULL
        OR NEW.execution_id IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'communication reset must clear every gate'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status NOT IN ('draft', 'cancelled') AND NOT EXISTS (
        SELECT 1 FROM sklegal_legal.validations AS validation
        JOIN sklegal_legal.work_product_versions AS artifact
          ON artifact.tenant_id = validation.tenant_id
         AND artifact.matter_id = validation.matter_id
         AND artifact.id = validation.subject_artifact_id
         AND artifact.version_number = validation.subject_artifact_version
         AND artifact.content_sha256 = validation.subject_content_sha256
        WHERE validation.tenant_id = NEW.tenant_id
          AND validation.matter_id = NEW.matter_id
          AND validation.id = NEW.validation_result_id
          AND validation.subject_kind = 'work_product_version'
          AND validation.subject_artifact_id = NEW.work_product_version_id
          AND validation.subject_artifact_version = NEW.work_product_version_number
          AND validation.subject_content_sha256 = NEW.work_product_content_sha256
          AND validation.outcome = 'passed' AND artifact.status = 'frozen'
          AND EXISTS (
              SELECT 1 FROM sklegal_legal.validation_checks AS check_record
              WHERE check_record.tenant_id = validation.tenant_id
                AND check_record.matter_id = validation.matter_id
                AND check_record.validation_id = validation.id
          )
    ) THEN
        RAISE EXCEPTION 'communication requires complete passed validation of a frozen exact artifact'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status IN ('approved', 'queued', 'dispatched')
       AND NOT EXISTS (
        SELECT 1
        FROM sklegal_legal.approvals AS approval
        JOIN sklegal_legal.approval_history AS history
          ON history.tenant_id = approval.tenant_id
         AND history.matter_id = approval.matter_id
         AND history.approval_id = approval.id
         AND history.approval_version = approval.version
         AND history.subject_artifact_id = approval.subject_artifact_id
         AND history.subject_artifact_version = approval.subject_artifact_version
         AND history.subject_content_sha256 = approval.subject_content_sha256
        WHERE approval.tenant_id = NEW.tenant_id
          AND approval.matter_id = NEW.matter_id
          AND approval.id = NEW.approval_id
          AND approval.status = 'approved'
          AND approval.subject_artifact_id = NEW.work_product_version_id
          AND approval.subject_artifact_version = NEW.work_product_version_number
          AND approval.subject_content_sha256 = NEW.work_product_content_sha256
    ) THEN
        RAISE EXCEPTION 'communication requires current approval for the exact artifact'
            USING ERRCODE = '23514';
    END IF;
    required_execution_status := CASE NEW.status
        WHEN 'queued' THEN 'queued'
        WHEN 'dispatched' THEN 'dispatched'
        WHEN 'receipt_verified' THEN 'receipt_verified'
        WHEN 'failed' THEN 'failed'
    END;
    IF required_execution_status IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM sklegal_legal.executions AS execution
        JOIN sklegal_legal.approval_history AS history
          ON history.tenant_id = execution.tenant_id
         AND history.matter_id = execution.matter_id
         AND history.approval_id = execution.approval_id
         AND history.approval_version = execution.approval_version
         AND history.subject_artifact_id = execution.subject_artifact_id
         AND history.subject_artifact_version = execution.subject_artifact_version
         AND history.subject_content_sha256 = execution.subject_content_sha256
        WHERE execution.tenant_id = NEW.tenant_id
          AND execution.matter_id = NEW.matter_id
          AND execution.id = NEW.execution_id
          AND execution.status = required_execution_status
          AND execution.approval_id = NEW.approval_id
          AND execution.subject_artifact_id = NEW.work_product_version_id
          AND execution.subject_artifact_version = NEW.work_product_version_number
          AND execution.subject_content_sha256 = NEW.work_product_content_sha256
          AND execution.destination_sha256 = NEW.destination_sha256
    ) THEN
        RAISE EXCEPTION 'communication state requires matching live execution state and binding'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.destination_sha256 IS NOT NULL
       AND NEW.destination_sha256 IS DISTINCT FROM OLD.destination_sha256 THEN
        RAISE EXCEPTION 'communication destination binding is set once'
            USING ERRCODE = '55000';
    END IF;
    IF OLD.execution_id IS NOT NULL AND NEW.execution_id IS DISTINCT FROM OLD.execution_id THEN
        RAISE EXCEPTION 'communication execution binding is set once'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER zz_communication_controlled_transition
BEFORE UPDATE ON sklegal_legal.communications
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.enforce_communication_transition();

CREATE FUNCTION sklegal_legal.transition_communication(
    target_tenant_id uuid,
    target_matter_id uuid,
    target_communication_id uuid,
    expected_version bigint,
    target_status text,
    supplied_validation_id uuid DEFAULT NULL,
    supplied_approval_id uuid DEFAULT NULL,
    supplied_destination_sha256 text DEFAULT NULL,
    supplied_execution_id uuid DEFAULT NULL
)
RETURNS sklegal_legal.communications
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    communication_record sklegal_legal.communications%ROWTYPE;
    approval_snapshot sklegal_legal.approval_history%ROWTYPE;
    artifact_id uuid;
    artifact_version bigint;
    artifact_content_sha256 text;
    initial_status text;
    initial_approval_id uuid;
    initial_execution_id uuid;
    selected_execution_id uuid;
    execution_approval_id uuid;
    execution_approval_version bigint;
    execution_status text;
    selected_approval_version bigint;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_legal.has_matter_membership(target_tenant_id, target_matter_id) THEN
        RAISE EXCEPTION 'communication transition is not authorized'
            USING ERRCODE = '42501';
    END IF;
    SELECT work_product_version_id, work_product_version_number,
           work_product_content_sha256, status, approval_id, execution_id
    INTO artifact_id, artifact_version, artifact_content_sha256,
         initial_status, initial_approval_id, initial_execution_id
    FROM sklegal_legal.communications
    WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
      AND id = target_communication_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'communication does not exist in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;
    IF artifact_id IS NOT NULL THEN
        PERFORM sklegal_legal.lock_exact_work_product_version(
            target_tenant_id,
            target_matter_id,
            artifact_id,
            artifact_version,
            artifact_content_sha256
        );
    END IF;
    selected_execution_id := CASE
        WHEN initial_execution_id IS NOT NULL THEN initial_execution_id
        WHEN initial_status = 'approved' AND target_status = 'queued'
            THEN supplied_execution_id
    END;
    IF selected_execution_id IS NOT NULL THEN
        SELECT approval_id, approval_version, status
        INTO execution_approval_id, execution_approval_version, execution_status
        FROM sklegal_legal.executions
        WHERE tenant_id = target_tenant_id
          AND matter_id = target_matter_id
          AND id = selected_execution_id
          AND subject_artifact_id = artifact_id
          AND subject_artifact_version = artifact_version
          AND subject_content_sha256 = artifact_content_sha256;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'communication execution does not bind the exact artifact'
                USING ERRCODE = 'P0002';
        END IF;
    END IF;
    IF initial_status = 'validated' AND target_status = 'approved' THEN
        SELECT * INTO approval_snapshot
        FROM sklegal_legal.lock_approval_decision(
            target_tenant_id,
            target_matter_id,
            supplied_approval_id,
            artifact_id,
            artifact_version,
            artifact_content_sha256,
            NULL,
            true
        );
    ELSIF initial_approval_id IS NOT NULL THEN
        selected_approval_version := execution_approval_version;
        IF selected_approval_version IS NULL THEN
            SELECT approval_version INTO selected_approval_version
            FROM sklegal_legal.approval_history
            WHERE tenant_id = target_tenant_id
              AND matter_id = target_matter_id
              AND approval_id = initial_approval_id
              AND subject_artifact_id = artifact_id
              AND subject_artifact_version = artifact_version
              AND subject_content_sha256 = artifact_content_sha256;
        END IF;
        SELECT * INTO approval_snapshot
        FROM sklegal_legal.lock_approval_decision(
            target_tenant_id,
            target_matter_id,
            initial_approval_id,
            artifact_id,
            artifact_version,
            artifact_content_sha256,
            selected_approval_version,
            (initial_status = 'approved' AND target_status = 'queued')
            OR (initial_status = 'queued' AND target_status = 'dispatched')
            OR (initial_status = 'failed' AND target_status = 'queued')
        );
    END IF;
    IF selected_execution_id IS NOT NULL THEN
        PERFORM 1
        FROM sklegal_legal.executions
        WHERE tenant_id = target_tenant_id
          AND matter_id = target_matter_id
          AND id = selected_execution_id
          AND subject_artifact_id = artifact_id
          AND subject_artifact_version = artifact_version
          AND subject_content_sha256 = artifact_content_sha256
          AND approval_id = COALESCE(initial_approval_id, supplied_approval_id)
          AND approval_version = approval_snapshot.approval_version
          AND status = CASE
              WHEN target_status IN ('queued', 'dispatched', 'receipt_verified', 'failed')
                  THEN target_status
              ELSE execution_status
          END
        FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'communication requires the locked matching execution state'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    SELECT * INTO communication_record
    FROM sklegal_legal.communications
    WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
      AND id = target_communication_id
      AND work_product_version_id IS NOT DISTINCT FROM artifact_id
      AND work_product_version_number IS NOT DISTINCT FROM artifact_version
      AND work_product_content_sha256 IS NOT DISTINCT FROM artifact_content_sha256
      AND status = initial_status
      AND approval_id IS NOT DISTINCT FROM initial_approval_id
      AND execution_id IS NOT DISTINCT FROM initial_execution_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'communication artifact binding changed before it could be locked'
            USING ERRCODE = '40001';
    END IF;
    IF communication_record.version <> expected_version THEN
        RAISE EXCEPTION 'communication version conflict' USING ERRCODE = '40001';
    END IF;
    IF communication_record.status IN ('validated', 'approved')
       AND target_status = 'draft' THEN
        RAISE EXCEPTION 'draft reset requires the complete controlled revision writer'
            USING ERRCODE = '23514';
    END IF;
    IF target_status = 'draft' THEN
        communication_record.validation_result_id := NULL;
        communication_record.approval_id := NULL;
        communication_record.destination_verified := false;
        communication_record.destination_sha256 := NULL;
        communication_record.execution_id := NULL;
    ELSIF communication_record.status = 'draft' AND target_status = 'validated' THEN
        communication_record.validation_result_id := supplied_validation_id;
    ELSIF communication_record.status = 'validated' AND target_status = 'approved' THEN
        communication_record.approval_id := supplied_approval_id;
    ELSIF communication_record.status = 'approved' AND target_status = 'queued' THEN
        communication_record.destination_verified := true;
        communication_record.destination_sha256 := supplied_destination_sha256;
        communication_record.execution_id := supplied_execution_id;
    ELSIF supplied_validation_id IS NOT NULL OR supplied_approval_id IS NOT NULL
          OR supplied_destination_sha256 IS NOT NULL OR supplied_execution_id IS NOT NULL THEN
        RAISE EXCEPTION 'gate evidence is not valid for this communication transition'
            USING ERRCODE = '23514';
    END IF;
    UPDATE sklegal_legal.communications
    SET status = target_status,
        validation_result_id = communication_record.validation_result_id,
        approval_id = communication_record.approval_id,
        destination_verified = communication_record.destination_verified,
        destination_sha256 = communication_record.destination_sha256,
        execution_id = communication_record.execution_id,
        version = version + 1
    WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
      AND id = target_communication_id
    RETURNING * INTO communication_record;
    RETURN communication_record;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.transition_communication(
    uuid, uuid, uuid, bigint, text, uuid, uuid, text, uuid
) FROM PUBLIC;

CREATE FUNCTION sklegal_legal.revise_communication(
    target_tenant_id uuid,
    target_matter_id uuid,
    target_communication_id uuid,
    expected_version bigint,
    replacement_direction text,
    replacement_channel text,
    replacement_subject text,
    replacement_work_product_version_id uuid,
    replacement_work_product_version_number bigint,
    replacement_work_product_content_sha256 text,
    replacement_participant_ids uuid[]
)
RETURNS sklegal_legal.communications
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    communication_record sklegal_legal.communications%ROWTYPE;
    current_artifact sklegal_legal.work_product_versions%ROWTYPE;
    replacement_artifact sklegal_legal.work_product_versions%ROWTYPE;
    current_parent_id uuid;
    replacement_parent_id uuid;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_legal.has_matter_membership(target_tenant_id, target_matter_id) THEN
        RAISE EXCEPTION 'communication revision is not authorized'
            USING ERRCODE = '42501';
    END IF;
    IF replacement_participant_ids IS NULL
       OR cardinality(replacement_participant_ids) = 0
       OR cardinality(replacement_participant_ids) <> (
           SELECT count(DISTINCT participant_id)
           FROM unnest(replacement_participant_ids) AS participant_id
       )
       OR EXISTS (
           SELECT 1 FROM unnest(replacement_participant_ids) AS participant_id
           WHERE participant_id IS NULL
              OR participant_id = '00000000-0000-0000-0000-000000000000'::uuid
       ) THEN
        RAISE EXCEPTION 'communication replacement requires unique non-nil participants'
            USING ERRCODE = '23514';
    END IF;
    IF (
        replacement_work_product_version_id IS NULL
        OR replacement_work_product_version_number IS NULL
        OR replacement_work_product_content_sha256 IS NULL
    ) AND NOT (
        replacement_work_product_version_id IS NULL
        AND replacement_work_product_version_number IS NULL
        AND replacement_work_product_content_sha256 IS NULL
    ) THEN
        RAISE EXCEPTION 'communication artifact replacement must be a complete tuple'
            USING ERRCODE = '23514';
    END IF;

    SELECT * INTO communication_record
    FROM sklegal_legal.communications
    WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
      AND id = target_communication_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'communication does not exist in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;
    IF communication_record.work_product_version_id IS NOT NULL THEN
        SELECT work_product_id INTO current_parent_id
        FROM sklegal_legal.work_product_versions
        WHERE tenant_id = target_tenant_id
          AND matter_id = target_matter_id
          AND id = communication_record.work_product_version_id
          AND version_number = communication_record.work_product_version_number
          AND content_sha256 = communication_record.work_product_content_sha256;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'current communication artifact does not exist in the authorized scope'
                USING ERRCODE = 'P0002';
        END IF;
    END IF;
    IF replacement_work_product_version_id IS NOT NULL THEN
        SELECT work_product_id INTO replacement_parent_id
        FROM sklegal_legal.work_product_versions
        WHERE tenant_id = target_tenant_id
          AND matter_id = target_matter_id
          AND id = replacement_work_product_version_id
          AND version_number = replacement_work_product_version_number
          AND content_sha256 = replacement_work_product_content_sha256;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'replacement communication artifact does not exist in the authorized scope'
                USING ERRCODE = 'P0002';
        END IF;
    END IF;
    IF current_parent_id IS NOT NULL AND replacement_parent_id IS NOT NULL
       AND current_parent_id IS DISTINCT FROM replacement_parent_id THEN
        RAISE EXCEPTION 'communication revision must remain within one parent work product'
            USING ERRCODE = '23514';
    END IF;

    IF communication_record.work_product_version_id IS NOT NULL
       AND replacement_work_product_version_id IS NOT NULL
       AND communication_record.work_product_version_id
           < replacement_work_product_version_id THEN
        SELECT * INTO current_artifact
        FROM sklegal_legal.lock_exact_work_product_version(
            target_tenant_id,
            target_matter_id,
            communication_record.work_product_version_id,
            communication_record.work_product_version_number,
            communication_record.work_product_content_sha256
        );
        SELECT * INTO replacement_artifact
        FROM sklegal_legal.lock_exact_work_product_version(
            target_tenant_id,
            target_matter_id,
            replacement_work_product_version_id,
            replacement_work_product_version_number,
            replacement_work_product_content_sha256
        );
    ELSIF communication_record.work_product_version_id IS NOT NULL
          AND replacement_work_product_version_id IS NOT NULL THEN
        SELECT * INTO replacement_artifact
        FROM sklegal_legal.lock_exact_work_product_version(
            target_tenant_id,
            target_matter_id,
            replacement_work_product_version_id,
            replacement_work_product_version_number,
            replacement_work_product_content_sha256
        );
        IF communication_record.work_product_version_id
           = replacement_work_product_version_id THEN
            current_artifact := replacement_artifact;
        ELSE
            SELECT * INTO current_artifact
            FROM sklegal_legal.lock_exact_work_product_version(
                target_tenant_id,
                target_matter_id,
                communication_record.work_product_version_id,
                communication_record.work_product_version_number,
                communication_record.work_product_content_sha256
            );
        END IF;
    ELSIF communication_record.work_product_version_id IS NOT NULL THEN
        SELECT * INTO current_artifact
        FROM sklegal_legal.lock_exact_work_product_version(
            target_tenant_id,
            target_matter_id,
            communication_record.work_product_version_id,
            communication_record.work_product_version_number,
            communication_record.work_product_content_sha256
        );
    ELSIF replacement_work_product_version_id IS NOT NULL THEN
        SELECT * INTO replacement_artifact
        FROM sklegal_legal.lock_exact_work_product_version(
            target_tenant_id,
            target_matter_id,
            replacement_work_product_version_id,
            replacement_work_product_version_number,
            replacement_work_product_content_sha256
        );
    END IF;

    SELECT * INTO communication_record
    FROM sklegal_legal.communications
    WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
      AND id = target_communication_id
      AND work_product_version_id IS NOT DISTINCT FROM
          communication_record.work_product_version_id
      AND work_product_version_number IS NOT DISTINCT FROM
          communication_record.work_product_version_number
      AND work_product_content_sha256 IS NOT DISTINCT FROM
          communication_record.work_product_content_sha256
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'communication artifact binding changed before it could be locked'
            USING ERRCODE = '40001';
    END IF;
    IF communication_record.version <> expected_version THEN
        RAISE EXCEPTION 'communication version conflict' USING ERRCODE = '40001';
    END IF;
    IF communication_record.status NOT IN ('draft', 'validated', 'approved') THEN
        RAISE EXCEPTION 'communication cannot be revised after queue or terminal state'
            USING ERRCODE = '23514';
    END IF;
    IF replacement_work_product_version_id IS NOT NULL
       AND replacement_artifact.status NOT IN ('draft', 'frozen') THEN
        RAISE EXCEPTION 'communication replacement artifact is not current'
            USING ERRCODE = '23514';
    END IF;
    DELETE FROM sklegal_legal.communication_participants
    WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
      AND communication_id = target_communication_id;
    INSERT INTO sklegal_legal.communication_participants (
        tenant_id, matter_id, communication_id, party_id
    )
    SELECT target_tenant_id, target_matter_id, target_communication_id, participant_id
    FROM unnest(replacement_participant_ids) AS participant_id;
    UPDATE sklegal_legal.communications
    SET direction = replacement_direction,
        channel = replacement_channel,
        subject = replacement_subject,
        work_product_version_id = replacement_work_product_version_id,
        work_product_version_number = replacement_work_product_version_number,
        work_product_content_sha256 = replacement_work_product_content_sha256,
        validation_result_id = NULL,
        approval_id = NULL,
        destination_verified = false,
        destination_sha256 = NULL,
        execution_id = NULL,
        status = 'draft',
        version = version + 1
    WHERE tenant_id = target_tenant_id AND matter_id = target_matter_id
      AND id = target_communication_id
    RETURNING * INTO communication_record;
    RETURN communication_record;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.revise_communication(
    uuid, uuid, uuid, bigint, text, text, text, uuid, bigint, text, uuid[]
) FROM PUBLIC;

CREATE FUNCTION sklegal_legal.require_runtime_initial_state_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    required_status text := CASE TG_TABLE_NAME
        WHEN 'clients' THEN 'proposed'
        WHEN 'engagements' THEN 'proposed'
        WHEN 'matters' THEN 'proposed'
        WHEN 'proceedings' THEN 'proposed'
        WHEN 'parties' THEN 'proposed'
        WHEN 'party_roles' THEN 'proposed'
        WHEN 'matter_events' THEN 'proposed'
        WHEN 'legal_transactions' THEN 'proposed'
        WHEN 'fact_assertions' THEN 'source_asserted'
        WHEN 'tension_groups' THEN 'unresolved'
        WHEN 'evidence_items' THEN 'proposed'
        WHEN 'issues' THEN 'identified'
        WHEN 'claims' THEN 'proposed'
        WHEN 'defenses' THEN 'proposed'
        WHEN 'elements' THEN 'alleged'
        WHEN 'remedies' THEN 'proposed'
        WHEN 'deadlines' THEN 'candidate'
        WHEN 'tasks' THEN 'draft'
        WHEN 'work_products' THEN 'draft'
        WHEN 'communications' THEN 'draft'
    END;
BEGIN
    IF sklegal_identity.runtime_role_is_safe()
       AND to_jsonb(NEW)->>'status' IS DISTINCT FROM required_status THEN
        RAISE EXCEPTION 'runtime aggregate insert must use initial status %', required_status
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE FUNCTION sklegal_legal.seal_relation_to_aggregate_creation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    parent_created_in_transaction boolean := false;
    caller_is_superuser boolean;
BEGIN
    SELECT rolsuper INTO caller_is_superuser
    FROM pg_catalog.pg_roles WHERE rolname = session_user;
    IF caller_is_superuser THEN
        RETURN NEW;
    END IF;
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_legal.has_matter_membership(NEW.tenant_id, NEW.matter_id) THEN
        RAISE EXCEPTION 'relationship mutation is not authorized'
            USING ERRCODE = '42501';
    END IF;

    CASE
        WHEN TG_TABLE_NAME IN ('transaction_party_roles', 'transaction_source_references') THEN
            SELECT xmin::text = pg_current_xact_id()::text
              INTO parent_created_in_transaction
            FROM sklegal_legal.legal_transactions
            WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
              AND id = NEW.transaction_id AND status = 'proposed'
            FOR UPDATE;
            IF parent_created_in_transaction THEN
                UPDATE sklegal_legal.legal_transactions
                SET version = version + 1
                WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
                  AND id = NEW.transaction_id;
            END IF;
        WHEN TG_TABLE_NAME = 'tension_assertions' THEN
            SELECT xmin::text = pg_current_xact_id()::text
              INTO parent_created_in_transaction
            FROM sklegal_legal.tension_groups
            WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
              AND id = NEW.tension_group_id AND status = 'unresolved'
            FOR UPDATE;
            IF parent_created_in_transaction THEN
                UPDATE sklegal_legal.tension_groups SET version = version + 1
                WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
                  AND id = NEW.tension_group_id;
            END IF;
        WHEN TG_TABLE_NAME = 'elements' THEN
            IF NEW.theory_kind = 'claim' THEN
                SELECT xmin::text = pg_current_xact_id()::text
                  INTO parent_created_in_transaction
                FROM sklegal_legal.claims
                WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
                  AND id = NEW.claim_id AND status = 'proposed'
                FOR UPDATE;
                IF parent_created_in_transaction THEN
                    UPDATE sklegal_legal.claims SET version = version + 1
                    WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
                      AND id = NEW.claim_id;
                END IF;
            ELSE
                SELECT xmin::text = pg_current_xact_id()::text
                  INTO parent_created_in_transaction
                FROM sklegal_legal.defenses
                WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
                  AND id = NEW.claim_id AND status = 'proposed'
                FOR UPDATE;
                IF parent_created_in_transaction THEN
                    UPDATE sklegal_legal.defenses SET version = version + 1
                    WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
                      AND id = NEW.claim_id;
                END IF;
            END IF;
        WHEN TG_TABLE_NAME = 'element_evidence' THEN
            SELECT xmin::text = pg_current_xact_id()::text
              INTO parent_created_in_transaction
            FROM sklegal_legal.elements
            WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
              AND id = NEW.element_id AND status = 'alleged'
            FOR UPDATE;
            IF parent_created_in_transaction THEN
                UPDATE sklegal_legal.elements SET version = version + 1
                WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
                  AND id = NEW.element_id;
            END IF;
        WHEN TG_TABLE_NAME IN ('theory_evidence', 'theory_authorities') THEN
            IF NEW.theory_kind = 'claim' THEN
                SELECT xmin::text = pg_current_xact_id()::text
                  INTO parent_created_in_transaction
                FROM sklegal_legal.claims
                WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
                  AND id = NEW.theory_id AND status = 'proposed'
                FOR UPDATE;
                IF parent_created_in_transaction THEN
                    UPDATE sklegal_legal.claims SET version = version + 1
                    WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
                      AND id = NEW.theory_id;
                END IF;
            ELSE
                SELECT xmin::text = pg_current_xact_id()::text
                  INTO parent_created_in_transaction
                FROM sklegal_legal.defenses
                WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
                  AND id = NEW.theory_id AND status = 'proposed'
                FOR UPDATE;
                IF parent_created_in_transaction THEN
                    UPDATE sklegal_legal.defenses SET version = version + 1
                    WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
                      AND id = NEW.theory_id;
                END IF;
            END IF;
        WHEN TG_TABLE_NAME = 'remedy_authorities' THEN
            SELECT xmin::text = pg_current_xact_id()::text
              INTO parent_created_in_transaction
            FROM sklegal_legal.remedies
            WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
              AND id = NEW.remedy_id AND status = 'proposed'
            FOR UPDATE;
            IF parent_created_in_transaction THEN
                UPDATE sklegal_legal.remedies SET version = version + 1
                WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
                  AND id = NEW.remedy_id;
            END IF;
        WHEN TG_TABLE_NAME = 'deadline_calculation_sources' THEN
            SELECT xmin::text = pg_current_xact_id()::text
              INTO parent_created_in_transaction
            FROM sklegal_legal.deadline_calculations
            WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
              AND id = NEW.calculation_id
            FOR UPDATE;
            IF parent_created_in_transaction THEN
                UPDATE sklegal_legal.deadline_calculations SET version = version + 1
                WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
                  AND id = NEW.calculation_id;
            END IF;
        WHEN TG_TABLE_NAME = 'validation_checks' THEN
            SELECT xmin::text = pg_current_xact_id()::text
              INTO parent_created_in_transaction
            FROM sklegal_legal.validations
            WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
              AND id = NEW.validation_id
            FOR UPDATE;
            IF parent_created_in_transaction THEN
                UPDATE sklegal_legal.validations SET version = version + 1
                WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
                  AND id = NEW.validation_id;
            END IF;
        WHEN TG_TABLE_NAME = 'communication_participants' THEN
            SELECT xmin::text = pg_current_xact_id()::text
              INTO parent_created_in_transaction
            FROM sklegal_legal.communications
            WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
              AND id = NEW.communication_id AND status = 'draft'
            FOR UPDATE;
            IF parent_created_in_transaction THEN
                UPDATE sklegal_legal.communications SET version = version + 1
                WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
                  AND id = NEW.communication_id;
            END IF;
    END CASE;
    IF NOT parent_created_in_transaction THEN
        RAISE EXCEPTION 'relationships are sealed after aggregate creation'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.seal_relation_to_aggregate_creation()
FROM PUBLIC;

DO $initial_state_triggers$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.clients'::regclass,
        'sklegal_legal.engagements'::regclass,
        'sklegal_legal.matters'::regclass,
        'sklegal_legal.proceedings'::regclass,
        'sklegal_legal.parties'::regclass,
        'sklegal_legal.party_roles'::regclass,
        'sklegal_legal.matter_events'::regclass,
        'sklegal_legal.legal_transactions'::regclass,
        'sklegal_legal.fact_assertions'::regclass,
        'sklegal_legal.tension_groups'::regclass,
        'sklegal_legal.evidence_items'::regclass,
        'sklegal_legal.issues'::regclass,
        'sklegal_legal.claims'::regclass,
        'sklegal_legal.defenses'::regclass,
        'sklegal_legal.elements'::regclass,
        'sklegal_legal.remedies'::regclass,
        'sklegal_legal.deadlines'::regclass,
        'sklegal_legal.tasks'::regclass,
        'sklegal_legal.work_products'::regclass,
        'sklegal_legal.communications'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER runtime_initial_state_insert '
            'BEFORE INSERT ON %s FOR EACH ROW EXECUTE FUNCTION '
            'sklegal_legal.require_runtime_initial_state_insert()', target_table
        );
    END LOOP;
END;
$initial_state_triggers$;

DO $sealed_relation_triggers$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.transaction_party_roles'::regclass,
        'sklegal_legal.transaction_source_references'::regclass,
        'sklegal_legal.tension_assertions'::regclass,
        'sklegal_legal.elements'::regclass,
        'sklegal_legal.element_evidence'::regclass,
        'sklegal_legal.theory_evidence'::regclass,
        'sklegal_legal.theory_authorities'::regclass,
        'sklegal_legal.remedy_authorities'::regclass,
        'sklegal_legal.deadline_calculation_sources'::regclass,
        'sklegal_legal.validation_checks'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER relation_creation_seal '
            'BEFORE INSERT ON %s FOR EACH ROW EXECUTE FUNCTION '
            'sklegal_legal.seal_relation_to_aggregate_creation()', target_table
        );
    END LOOP;
END;
$sealed_relation_triggers$;

CREATE FUNCTION sklegal_legal.validate_deadline_calculation_sources()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    record_data jsonb := COALESCE(to_jsonb(NEW), to_jsonb(OLD));
    selected_tenant uuid := (record_data->>'tenant_id')::uuid;
    selected_matter uuid := (record_data->>'matter_id')::uuid;
    selected_id uuid := COALESCE(
        (record_data->>'calculation_id')::uuid,
        (record_data->>'id')::uuid
    );
BEGIN
    IF EXISTS (
        SELECT 1 FROM sklegal_legal.deadline_calculations
        WHERE tenant_id = selected_tenant AND matter_id = selected_matter
          AND id = selected_id
    ) AND NOT EXISTS (
        SELECT 1 FROM sklegal_legal.deadline_calculation_sources
        WHERE tenant_id = selected_tenant AND matter_id = selected_matter
          AND calculation_id = selected_id
    ) THEN
        RAISE EXCEPTION 'deadline calculation requires source provenance'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$function$;

CREATE CONSTRAINT TRIGGER deadline_calculation_sources_complete
AFTER INSERT ON sklegal_legal.deadline_calculations
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_deadline_calculation_sources();
CREATE CONSTRAINT TRIGGER deadline_calculation_source_links_complete
AFTER INSERT OR UPDATE OR DELETE ON sklegal_legal.deadline_calculation_sources
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_deadline_calculation_sources();

CREATE FUNCTION sklegal_legal.validate_conditional_theory_cardinality()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    record_data jsonb := COALESCE(to_jsonb(NEW), to_jsonb(OLD));
    selected_tenant uuid := (record_data->>'tenant_id')::uuid;
    selected_matter uuid := (record_data->>'matter_id')::uuid;
    selected_id uuid := COALESCE(
        (record_data->>'element_id')::uuid,
        (record_data->>'theory_id')::uuid,
        (record_data->>'id')::uuid
    );
BEGIN
    IF TG_TABLE_NAME IN ('claims', 'defenses', 'elements') THEN
        IF TG_TABLE_NAME = 'elements' AND EXISTS (
            SELECT 1 FROM sklegal_legal.elements
            WHERE tenant_id = selected_tenant AND matter_id = selected_matter
              AND id = selected_id AND status = 'supported'
        ) AND NOT EXISTS (
            SELECT 1 FROM sklegal_legal.element_evidence
            WHERE tenant_id = selected_tenant AND matter_id = selected_matter
              AND element_id = selected_id
        ) THEN
            RAISE EXCEPTION 'supported element requires evidence'
                USING ERRCODE = '23514';
        ELSIF TG_TABLE_NAME = 'claims' AND EXISTS (
            SELECT 1 FROM sklegal_legal.claims
            WHERE tenant_id = selected_tenant AND matter_id = selected_matter
              AND id = selected_id AND status = 'accepted'
        ) AND NOT EXISTS (
            SELECT 1 FROM sklegal_legal.elements
            WHERE tenant_id = selected_tenant AND matter_id = selected_matter
              AND claim_id = selected_id AND theory_kind = 'claim'
        ) THEN
            RAISE EXCEPTION 'accepted claim or defense requires an element'
                USING ERRCODE = '23514';
        ELSIF TG_TABLE_NAME = 'defenses' AND EXISTS (
            SELECT 1 FROM sklegal_legal.defenses
            WHERE tenant_id = selected_tenant AND matter_id = selected_matter
              AND id = selected_id AND status = 'accepted'
        ) AND NOT EXISTS (
            SELECT 1 FROM sklegal_legal.elements
            WHERE tenant_id = selected_tenant AND matter_id = selected_matter
              AND claim_id = selected_id AND theory_kind = 'defense'
        ) THEN
            RAISE EXCEPTION 'accepted claim or defense requires an element'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NULL;
END;
$function$;

CREATE CONSTRAINT TRIGGER claim_element_cardinality
AFTER INSERT OR UPDATE ON sklegal_legal.claims
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_conditional_theory_cardinality();
CREATE CONSTRAINT TRIGGER defense_element_cardinality
AFTER INSERT OR UPDATE ON sklegal_legal.defenses
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_conditional_theory_cardinality();
CREATE CONSTRAINT TRIGGER element_evidence_cardinality
AFTER INSERT OR UPDATE ON sklegal_legal.elements
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_conditional_theory_cardinality();

CREATE FUNCTION sklegal_legal.require_exact_state_validation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    required_kind text;
    validation_id uuid;
    subject_version bigint := CASE
        WHEN TG_OP = 'UPDATE' THEN OLD.version
        ELSE NEW.version
    END;
BEGIN
    CASE TG_TABLE_NAME
        WHEN 'parties' THEN
            IF NEW.status = 'verified' THEN
                required_kind := 'party';
                validation_id := NEW.verification_reference_id;
            END IF;
        WHEN 'party_roles' THEN
            IF NEW.status = 'verified' THEN
                required_kind := 'party_role';
                validation_id := NEW.verification_reference_id;
            END IF;
        WHEN 'matter_events' THEN
            IF NEW.status = 'verified' THEN
                required_kind := 'matter_event';
                validation_id := NEW.verification_reference_id;
            END IF;
        WHEN 'fact_assertions' THEN
            IF NEW.status = 'verified' THEN
                required_kind := 'fact_assertion';
                validation_id := NEW.verification_reference_id;
            END IF;
        WHEN 'evidence_items' THEN
            IF NEW.status = 'verified' THEN
                required_kind := 'evidence_item';
                validation_id := NEW.verification_reference_id;
            END IF;
        WHEN 'claims' THEN
            IF NEW.status = 'accepted' THEN
                required_kind := 'claim';
                validation_id := NEW.acceptance_validation_id;
            END IF;
        WHEN 'defenses' THEN
            IF NEW.status = 'accepted' THEN
                required_kind := 'defense';
                validation_id := NEW.acceptance_validation_id;
            END IF;
        WHEN 'deadlines' THEN
            IF NEW.status IN ('reviewed', 'operative', 'satisfied', 'missed') THEN
                required_kind := 'deadline';
                validation_id := NEW.review_validation_id;
            END IF;
    END CASE;
    IF required_kind IS NULL THEN
        RETURN NEW;
    END IF;
    IF validation_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM sklegal_legal.validations AS validation
        WHERE validation.tenant_id = NEW.tenant_id
          AND validation.matter_id = NEW.matter_id
          AND validation.id = validation_id
          AND validation.subject_kind = required_kind
          AND validation.subject_artifact_id = NEW.id
          AND validation.subject_artifact_version = subject_version
          AND validation.subject_content_sha256 IS NULL
          AND validation.outcome = 'passed'
          AND EXISTS (
              SELECT 1 FROM sklegal_legal.validation_checks AS check_record
              WHERE check_record.tenant_id = validation.tenant_id
                AND check_record.matter_id = validation.matter_id
                AND check_record.validation_id = validation.id
          )
    ) THEN
        RAISE EXCEPTION 'state requires complete passed validation of the exact typed prior version'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

DO $exact_state_validation_triggers$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.parties'::regclass,
        'sklegal_legal.party_roles'::regclass,
        'sklegal_legal.matter_events'::regclass,
        'sklegal_legal.fact_assertions'::regclass,
        'sklegal_legal.evidence_items'::regclass,
        'sklegal_legal.claims'::regclass,
        'sklegal_legal.defenses'::regclass,
        'sklegal_legal.deadlines'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER exact_state_validation '
            'BEFORE INSERT OR UPDATE ON %s FOR EACH ROW EXECUTE FUNCTION '
            'sklegal_legal.require_exact_state_validation()', target_table
        );
    END LOOP;
END;
$exact_state_validation_triggers$;

CREATE FUNCTION sklegal_legal.bind_legacy_alias_owner_audit()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    caller_is_superuser boolean;
    owner_updated boolean := false;
BEGIN
    SELECT rolsuper INTO caller_is_superuser
    FROM pg_catalog.pg_roles WHERE rolname = session_user;
    IF NOT caller_is_superuser AND (
        NOT sklegal_identity.runtime_role_is_safe()
        OR NOT sklegal_legal.has_matter_membership(NEW.tenant_id, NEW.matter_id)
    ) THEN
        RAISE EXCEPTION 'legacy alias owner mutation is not authorized'
            USING ERRCODE = '42501';
    END IF;
    IF NEW.observed_at > clock_timestamp() THEN
        RAISE EXCEPTION 'legacy alias observation cannot be in the future'
            USING ERRCODE = '22007';
    END IF;
    IF NEW.canonical_record_kind = 'matter' THEN
        UPDATE sklegal_legal.matters
        SET version = version + 1
        WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
          AND id = NEW.canonical_record_id;
        owner_updated := FOUND;
    ELSE
        UPDATE sklegal_legal.matter_events
        SET version = version + 1
        WHERE tenant_id = NEW.tenant_id AND matter_id = NEW.matter_id
          AND id = NEW.canonical_record_id;
        owner_updated := FOUND;
    END IF;
    IF NOT owner_updated THEN
        RAISE EXCEPTION 'legacy alias canonical owner does not exist in scope'
            USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.bind_legacy_alias_owner_audit()
FROM PUBLIC;

CREATE TRIGGER legacy_alias_owner_audit
BEFORE INSERT ON sklegal_legal.legacy_aliases
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.bind_legacy_alias_owner_audit();

DO $domain_id_triggers$
DECLARE
    target_table regclass;
BEGIN
    FOR target_table IN
        SELECT relation.oid::regclass
        FROM pg_catalog.pg_class AS relation
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'sklegal_legal' AND relation.relkind = 'r'
    LOOP
        EXECUTE format(
            'CREATE TRIGGER domain_id_non_nil '
            'BEFORE INSERT OR UPDATE ON %s FOR EACH ROW EXECUTE FUNCTION '
            'sklegal_legal.reject_nil_domain_ids()', target_table
        );
    END LOOP;
END;
$domain_id_triggers$;

DO $mutable_triggers$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.clients'::regclass,
        'sklegal_legal.engagements'::regclass,
        'sklegal_legal.matters'::regclass,
        'sklegal_legal.matter_memberships'::regclass,
        'sklegal_legal.forums'::regclass,
        'sklegal_legal.proceedings'::regclass,
        'sklegal_legal.parties'::regclass,
        'sklegal_legal.party_roles'::regclass,
        'sklegal_legal.matter_events'::regclass,
        'sklegal_legal.legal_transactions'::regclass,
        'sklegal_legal.fact_assertions'::regclass,
        'sklegal_legal.tension_groups'::regclass,
        'sklegal_legal.evidence_items'::regclass,
        'sklegal_legal.issues'::regclass,
        'sklegal_legal.claims'::regclass,
        'sklegal_legal.defenses'::regclass,
        'sklegal_legal.elements'::regclass,
        'sklegal_legal.remedies'::regclass,
        'sklegal_legal.deadlines'::regclass,
        'sklegal_legal.tasks'::regclass,
        'sklegal_legal.work_products'::regclass,
        'sklegal_legal.approvals'::regclass,
        'sklegal_legal.communications'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER initialize_record '
            'BEFORE INSERT ON %s FOR EACH ROW EXECUTE FUNCTION '
            'sklegal_legal.initialize_record_audit()', target_table
        );
        EXECUTE format(
            'CREATE TRIGGER optimistic_record_update '
            'BEFORE UPDATE ON %s FOR EACH ROW EXECUTE FUNCTION '
            'sklegal_legal.enforce_optimistic_record_update()', target_table
        );
    END LOOP;
END;
$mutable_triggers$;

DO $immutable_entity_triggers$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.source_references'::regclass,
        'sklegal_legal.legacy_aliases'::regclass,
        'sklegal_legal.custody_events'::regclass,
        'sklegal_legal.deadline_calculations'::regclass,
        'sklegal_legal.validations'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER initialize_record '
            'BEFORE INSERT ON %s FOR EACH ROW EXECUTE FUNCTION '
            'sklegal_legal.initialize_record_audit()', target_table
        );
        EXECUTE format(
            'CREATE TRIGGER append_only '
            'BEFORE UPDATE OR DELETE ON %s FOR EACH ROW EXECUTE FUNCTION '
            'sklegal_legal.reject_record_change()', target_table
        );
    END LOOP;
END;
$immutable_entity_triggers$;

DROP TRIGGER initialize_record ON sklegal_legal.validations;
CREATE TRIGGER initialize_record
BEFORE INSERT ON sklegal_legal.validations
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.initialize_validation_result();

CREATE TRIGGER initialize_record
BEFORE INSERT ON sklegal_legal.executions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.initialize_record_audit();

CREATE TRIGGER initialize_record
BEFORE INSERT ON sklegal_legal.execution_receipts
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.initialize_execution_receipt();
CREATE TRIGGER append_only
BEFORE UPDATE OR DELETE ON sklegal_legal.execution_receipts
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

CREATE TRIGGER initialize_record
BEFORE INSERT ON sklegal_legal.execution_events
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.initialize_execution_event();
CREATE TRIGGER append_only
BEFORE UPDATE OR DELETE ON sklegal_legal.execution_events
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

CREATE TRIGGER initialize_record
BEFORE INSERT ON sklegal_legal.work_product_versions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.initialize_record_audit();
CREATE TRIGGER work_product_version_delete_denied
BEFORE DELETE ON sklegal_legal.work_product_versions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

DO $append_only_link_triggers$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.transaction_party_roles'::regclass,
        'sklegal_legal.transaction_source_references'::regclass,
        'sklegal_legal.tension_assertions'::regclass,
        'sklegal_legal.authority_identities'::regclass,
        'sklegal_legal.element_evidence'::regclass,
        'sklegal_legal.theory_evidence'::regclass,
        'sklegal_legal.theory_authorities'::regclass,
        'sklegal_legal.remedy_authorities'::regclass,
        'sklegal_legal.deadline_calculation_sources'::regclass,
        'sklegal_legal.validation_checks'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER initialize_created '
            'BEFORE INSERT ON %s FOR EACH ROW EXECUTE FUNCTION '
            'sklegal_legal.initialize_created_at()', target_table
        );
        EXECUTE format(
            'CREATE TRIGGER append_only '
            'BEFORE UPDATE OR DELETE ON %s FOR EACH ROW EXECUTE FUNCTION '
            'sklegal_legal.reject_record_change()', target_table
        );
    END LOOP;
END;
$append_only_link_triggers$;

-- sklegal:down
DROP FUNCTION sklegal_legal.transition_communication(
    uuid, uuid, uuid, bigint, text, uuid, uuid, text, uuid
);
DROP FUNCTION sklegal_legal.revise_communication(
    uuid, uuid, uuid, bigint, text, text, text, uuid, bigint, text, uuid[]
);
DROP FUNCTION sklegal_legal.create_communication(
    uuid, uuid, uuid, text, text, text, uuid[], uuid, bigint, text,
    sklegal_legal.data_classification, sklegal_legal.record_completeness,
    timestamptz, timestamptz
);
DROP TABLE sklegal_legal.communication_participants;
DROP TABLE sklegal_legal.communications;
DROP FUNCTION sklegal_legal.enforce_communication_transition();
DROP FUNCTION sklegal_legal.require_draft_communication_insert();
DROP FUNCTION sklegal_legal.validate_communication_participants();
DROP FUNCTION sklegal_legal.transition_execution(
    uuid, uuid, uuid, bigint, text, text, uuid, uuid, uuid, text, text,
    timestamptz, timestamptz
);
DROP TABLE sklegal_legal.execution_events;
DROP TABLE sklegal_legal.execution_receipts;
DROP TABLE sklegal_legal.executions;
DROP FUNCTION sklegal_legal.require_matching_execution_event();
DROP FUNCTION sklegal_legal.require_matching_execution_receipt();
DROP FUNCTION sklegal_legal.require_execution_gate_evidence();
DROP FUNCTION sklegal_legal.require_draft_execution_insert();
DROP TABLE sklegal_legal.tasks;
DROP TABLE sklegal_legal.deadlines;
DROP TABLE sklegal_legal.deadline_calculation_sources;
DROP TABLE sklegal_legal.deadline_calculations;
DROP TABLE sklegal_legal.remedy_authorities;
DROP TABLE sklegal_legal.remedies;
DROP TABLE sklegal_legal.element_evidence;
DROP TABLE sklegal_legal.theory_evidence;
DROP TABLE sklegal_legal.theory_authorities;
DROP FUNCTION sklegal_legal.require_scoped_theory_link();
DROP TABLE sklegal_legal.elements;
DROP FUNCTION sklegal_legal.require_scoped_theory();
DROP TABLE sklegal_legal.defenses;
DROP TABLE sklegal_legal.claims;
DROP TABLE sklegal_legal.issues;
DROP VIEW sklegal_legal.authority_current;
DROP VIEW sklegal_legal.authority_history;
DROP TABLE sklegal_legal.authorities;
DROP FUNCTION sklegal_legal.prepare_authority_version();
DROP TABLE sklegal_legal.authority_identities;
ALTER TABLE sklegal_legal.work_products DROP CONSTRAINT work_product_validation_fk;
ALTER TABLE sklegal_legal.work_products DROP CONSTRAINT work_product_approval_fk;
DROP FUNCTION sklegal_legal.transition_work_product(
    uuid, uuid, uuid, bigint, text, uuid, uuid
);
DROP FUNCTION sklegal_legal.revise_work_product(
    uuid, uuid, uuid, bigint, text, text, uuid, bigint, text
);
DROP TABLE sklegal_legal.validation_checks;
DROP FUNCTION sklegal_legal.transition_approval(uuid, uuid, uuid, bigint, text, text);
DROP FUNCTION sklegal_legal.lock_approval_decision(
    uuid, uuid, uuid, uuid, bigint, text, bigint, boolean
);
DROP TABLE sklegal_legal.approval_history;
DROP FUNCTION sklegal_legal.require_controlled_approval_history_insert();
DROP TABLE sklegal_legal.approvals;
DROP FUNCTION sklegal_legal.require_pending_approval_insert();
DROP FUNCTION sklegal_legal.enforce_approval_update();
DROP TABLE sklegal_legal.validations;
DROP FUNCTION sklegal_legal.validate_validation_checks();
DROP FUNCTION sklegal_legal.require_typed_validation_subject();
ALTER TABLE sklegal_legal.work_products DROP CONSTRAINT work_product_current_version_fk;
DROP FUNCTION sklegal_legal.transition_work_product_version(uuid, uuid, uuid, bigint, text);
DROP FUNCTION sklegal_legal.lock_exact_work_product_version(uuid, uuid, uuid, bigint, text);
DROP TABLE sklegal_legal.work_product_versions;
DROP FUNCTION sklegal_legal.require_draft_work_product_version_insert();
DROP FUNCTION sklegal_legal.control_work_product_version_update();
DROP TABLE sklegal_legal.work_products;
DROP FUNCTION sklegal_legal.enforce_work_product_transition();
DROP FUNCTION sklegal_legal.require_draft_work_product_insert();
DROP TABLE sklegal_legal.custody_events;
DROP TABLE sklegal_legal.evidence_items;
DROP TABLE sklegal_legal.tension_assertions;
ALTER TABLE sklegal_legal.fact_assertions DROP CONSTRAINT fact_tension_scope_fk;
DROP TABLE sklegal_legal.tension_groups;
DROP FUNCTION sklegal_legal.validate_tension_membership();
DROP TABLE sklegal_legal.fact_assertions;
DROP TABLE sklegal_legal.transaction_source_references;
DROP TABLE sklegal_legal.transaction_party_roles;
DROP TABLE sklegal_legal.legal_transactions;
DROP FUNCTION sklegal_legal.validate_transaction_sources();
DROP FUNCTION sklegal_legal.require_confirmed_transaction_source();
DROP TABLE sklegal_legal.legacy_aliases;
DROP TABLE sklegal_legal.matter_events;
DROP TABLE sklegal_legal.party_roles;
DROP TABLE sklegal_legal.parties;
DROP TABLE sklegal_legal.proceedings;
DROP TABLE sklegal_legal.forums;
DROP TABLE sklegal_legal.source_references;
DROP FUNCTION sklegal_legal.has_matter_membership(uuid, uuid);
DROP TRIGGER matter_status_transition ON sklegal_legal.matters;
DROP FUNCTION sklegal_legal.enforce_matter_status_transition();
DROP TABLE sklegal_legal.matter_memberships;
DROP TABLE sklegal_legal.matters;
DROP TABLE sklegal_legal.engagements;
DROP TABLE sklegal_legal.clients;
DROP FUNCTION sklegal_legal.bind_legacy_alias_owner_audit();
DROP FUNCTION sklegal_legal.require_exact_state_validation();
DROP FUNCTION sklegal_legal.seal_relation_to_aggregate_creation();
DROP FUNCTION sklegal_legal.require_runtime_initial_state_insert();
DROP FUNCTION sklegal_legal.validate_conditional_theory_cardinality();
DROP FUNCTION sklegal_legal.validate_deadline_calculation_sources();
