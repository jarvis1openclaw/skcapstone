-- sklegal:up
CREATE TABLE sklegal_legal.work_product_templates (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL REFERENCES sklegal_identity.tenants(id),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 512),
    work_product_kind text NOT NULL CHECK (work_product_kind IN ('memo', 'letter', 'pleading', 'contract', 'packet', 'report', 'other')),
    current_version_id uuid NOT NULL,
    current_version_number sklegal_legal.record_version NOT NULL,
    current_content_sha256 sklegal_legal.sha256_digest NOT NULL,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'retired')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, id),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.work_product_template_versions (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    template_id uuid NOT NULL,
    version_number sklegal_legal.record_version NOT NULL,
    content_sha256 sklegal_legal.sha256_digest NOT NULL,
    encrypted_content bytea,
    encryption_key_ref text,
    encryption_algorithm text,
    encrypted_at timestamptz,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'frozen', 'archived')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, template_id, version_number),
    UNIQUE (tenant_id, id, template_id, version_number, content_sha256),
    FOREIGN KEY (tenant_id, template_id)
        REFERENCES sklegal_legal.work_product_templates(tenant_id, id)
        DEFERRABLE INITIALLY DEFERRED,
    CHECK (sklegal_identity.encrypted_payload_is_complete(
        encrypted_content, encryption_key_ref, encryption_algorithm, encrypted_at
    )),
    CHECK (updated_at >= created_at)
);

ALTER TABLE sklegal_legal.work_product_templates
ADD CONSTRAINT work_product_template_current_version_fk
FOREIGN KEY (tenant_id, current_version_id, id, current_version_number, current_content_sha256)
REFERENCES sklegal_legal.work_product_template_versions(tenant_id, id, template_id, version_number, content_sha256)
DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE sklegal_legal.work_product_unknowns (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    work_product_version_id uuid NOT NULL,
    work_product_version_number sklegal_legal.record_version NOT NULL,
    work_product_content_sha256 sklegal_legal.sha256_digest NOT NULL,
    placeholder_key text NOT NULL CHECK (placeholder_key ~ '^[a-z][a-z0-9_]{0,63}$'),
    hint text CHECK (hint IS NULL OR length(btrim(hint)) BETWEEN 1 AND 512),
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    resolved_by_principal_id uuid,
    resolved_at timestamptz,
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, work_product_version_id,
            work_product_version_number, work_product_content_sha256, placeholder_key),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, matter_id, work_product_version_id,
                 work_product_version_number, work_product_content_sha256)
        REFERENCES sklegal_legal.work_product_versions(tenant_id, matter_id, id, version_number, content_sha256),
    FOREIGN KEY (tenant_id, resolved_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK ((status = 'resolved') = (resolved_by_principal_id IS NOT NULL AND resolved_at IS NOT NULL)),
    CHECK (updated_at >= created_at)
);

CREATE FUNCTION sklegal_legal.require_draft_work_product_template_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.status <> 'draft' THEN
        RAISE EXCEPTION 'work product template must be inserted as a draft'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.require_draft_work_product_template_insert()
FROM PUBLIC;

CREATE FUNCTION sklegal_legal.enforce_work_product_template_transition()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    artifact_status text;
    controlled_revision boolean :=
        current_user = 'sklegal_migrator' AND session_user <> current_user
        AND OLD.status = NEW.status;
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.work_product_kind IS DISTINCT FROM OLD.work_product_kind
       OR NEW.classification IS DISTINCT FROM OLD.classification
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'template transition cannot change identity or kind'
            USING ERRCODE = '55000';
    END IF;
    IF (
        NEW.name IS DISTINCT FROM OLD.name
        OR NEW.current_version_id IS DISTINCT FROM OLD.current_version_id
        OR NEW.current_version_number IS DISTINCT FROM OLD.current_version_number
        OR NEW.current_content_sha256 IS DISTINCT FROM OLD.current_content_sha256
    ) AND NOT controlled_revision
       AND NOT (OLD.status = 'draft' AND NEW.status = 'draft') THEN
        RAISE EXCEPTION 'template payload changes require draft state or the controlled writer'
            USING ERRCODE = '55000';
    END IF;
    IF NOT controlled_revision AND NOT (
        (OLD.status = 'draft' AND NEW.status IN ('draft', 'active', 'retired'))
        OR (OLD.status = 'active' AND NEW.status = 'retired')
    ) THEN
        RAISE EXCEPTION 'template transition is not an adjacent declared edge'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'active' THEN
        SELECT artifact.status INTO artifact_status
        FROM sklegal_legal.work_product_template_versions AS artifact
        WHERE artifact.tenant_id = NEW.tenant_id
          AND artifact.id = NEW.current_version_id
          AND artifact.template_id = NEW.id
          AND artifact.version_number = NEW.current_version_number
          AND artifact.content_sha256 = NEW.current_content_sha256
        FOR UPDATE;
        IF artifact_status IS DISTINCT FROM 'frozen' THEN
            RAISE EXCEPTION 'an active template requires the exact current version to be frozen'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.enforce_work_product_template_transition()
FROM PUBLIC;

CREATE FUNCTION sklegal_legal.require_draft_work_product_template_version_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.status <> 'draft' THEN
        RAISE EXCEPTION 'work product template version must be inserted as draft'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.require_draft_work_product_template_version_insert()
FROM PUBLIC;

CREATE FUNCTION sklegal_legal.control_work_product_template_version_update()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.template_id IS DISTINCT FROM OLD.template_id
       OR NEW.version_number IS DISTINCT FROM OLD.version_number
       OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
       OR NEW.encrypted_content IS DISTINCT FROM OLD.encrypted_content
       OR NEW.encryption_key_ref IS DISTINCT FROM OLD.encryption_key_ref
       OR NEW.encryption_algorithm IS DISTINCT FROM OLD.encryption_algorithm
       OR NEW.encrypted_at IS DISTINCT FROM OLD.encrypted_at
       OR NEW.classification IS DISTINCT FROM OLD.classification
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'template version payload is immutable'
            USING ERRCODE = '55000';
    END IF;
    IF NEW.version <> OLD.version + 1
       OR NOT (
           (OLD.status = 'draft' AND NEW.status = 'frozen')
           OR (OLD.status = 'frozen' AND NEW.status = 'archived')
       ) THEN
        RAISE EXCEPTION 'invalid template version transition'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'archived' AND EXISTS (
        SELECT 1
        FROM sklegal_legal.work_product_templates AS template
        WHERE template.tenant_id = NEW.tenant_id
          AND template.current_version_id = NEW.id
          AND template.current_version_number = NEW.version_number
          AND template.current_content_sha256 = NEW.content_sha256
    ) THEN
        RAISE EXCEPTION 'a template current version cannot be archived'
            USING ERRCODE = '23514';
    END IF;
    NEW.updated_at := GREATEST(OLD.updated_at, clock_timestamp());
    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.control_work_product_template_version_update()
FROM PUBLIC;

CREATE FUNCTION sklegal_legal.lock_exact_work_product_template_version(
    target_tenant_id uuid,
    target_version_id uuid,
    target_version_number bigint,
    target_content_sha256 text
)
RETURNS sklegal_legal.work_product_template_versions
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    version_record sklegal_legal.work_product_template_versions%ROWTYPE;
    parent_template_id uuid;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_identity.has_tenant_membership(target_tenant_id) THEN
        RAISE EXCEPTION 'exact template version lock is not authorized'
            USING ERRCODE = '42501';
    END IF;

    SELECT template_id INTO parent_template_id
    FROM sklegal_legal.work_product_template_versions
    WHERE tenant_id = target_tenant_id
      AND id = target_version_id
      AND version_number = target_version_number
      AND content_sha256 = target_content_sha256;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'exact template version does not exist in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;

    PERFORM 1
    FROM sklegal_legal.work_product_templates
    WHERE tenant_id = target_tenant_id
      AND id = parent_template_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'parent template does not exist in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;

    SELECT * INTO version_record
    FROM sklegal_legal.work_product_template_versions
    WHERE tenant_id = target_tenant_id
      AND id = target_version_id
      AND template_id = parent_template_id
      AND version_number = target_version_number
      AND content_sha256 = target_content_sha256
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'exact template version changed before it could be locked'
            USING ERRCODE = '40001';
    END IF;
    RETURN version_record;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.lock_exact_work_product_template_version(
    uuid, uuid, bigint, text
) FROM PUBLIC;

CREATE FUNCTION sklegal_legal.transition_work_product_template_version(
    target_tenant_id uuid,
    target_version_id uuid,
    expected_version bigint,
    target_status text
)
RETURNS sklegal_legal.work_product_template_versions
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    version_record sklegal_legal.work_product_template_versions%ROWTYPE;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_identity.has_tenant_membership(target_tenant_id) THEN
        RAISE EXCEPTION 'template version transition is not authorized'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO version_record
    FROM sklegal_legal.lock_exact_work_product_template_version(
        target_tenant_id,
        target_version_id,
        (SELECT candidate.version_number
         FROM sklegal_legal.work_product_template_versions AS candidate
         WHERE candidate.tenant_id = target_tenant_id
           AND candidate.id = target_version_id),
        (SELECT candidate.content_sha256
         FROM sklegal_legal.work_product_template_versions AS candidate
         WHERE candidate.tenant_id = target_tenant_id
           AND candidate.id = target_version_id)
    );
    IF version_record.version <> expected_version THEN
        RAISE EXCEPTION 'template version conflict' USING ERRCODE = '40001';
    END IF;
    UPDATE sklegal_legal.work_product_template_versions
    SET status = target_status, version = version + 1
    WHERE tenant_id = target_tenant_id
      AND id = target_version_id
    RETURNING * INTO version_record;
    RETURN version_record;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.transition_work_product_template_version(
    uuid, uuid, bigint, text
) FROM PUBLIC;

CREATE FUNCTION sklegal_legal.transition_work_product_template(
    target_tenant_id uuid,
    target_template_id uuid,
    expected_version bigint,
    target_status text
)
RETURNS sklegal_legal.work_product_templates
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    template_record sklegal_legal.work_product_templates%ROWTYPE;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_identity.has_tenant_membership(target_tenant_id) THEN
        RAISE EXCEPTION 'template transition is not authorized'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO template_record
    FROM sklegal_legal.work_product_templates
    WHERE tenant_id = target_tenant_id
      AND id = target_template_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'template does not exist in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;
    IF template_record.version <> expected_version THEN
        RAISE EXCEPTION 'template version conflict' USING ERRCODE = '40001';
    END IF;
    PERFORM sklegal_legal.lock_exact_work_product_template_version(
        target_tenant_id,
        template_record.current_version_id,
        template_record.current_version_number,
        template_record.current_content_sha256
    );
    UPDATE sklegal_legal.work_product_templates
    SET status = target_status,
        version = version + 1
    WHERE tenant_id = target_tenant_id
      AND id = target_template_id
    RETURNING * INTO template_record;
    RETURN template_record;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.transition_work_product_template(
    uuid, uuid, bigint, text
) FROM PUBLIC;

CREATE FUNCTION sklegal_legal.advance_work_product_template(
    target_tenant_id uuid,
    target_template_id uuid,
    expected_version bigint,
    replacement_version_id uuid,
    replacement_version_number bigint,
    replacement_content_sha256 text
)
RETURNS sklegal_legal.work_product_templates
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    template_record sklegal_legal.work_product_templates%ROWTYPE;
    replacement_record sklegal_legal.work_product_template_versions%ROWTYPE;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_identity.has_tenant_membership(target_tenant_id) THEN
        RAISE EXCEPTION 'template advance is not authorized'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO template_record
    FROM sklegal_legal.work_product_templates
    WHERE tenant_id = target_tenant_id
      AND id = target_template_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'template does not exist in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;
    IF template_record.version <> expected_version THEN
        RAISE EXCEPTION 'template version conflict' USING ERRCODE = '40001';
    END IF;
    IF template_record.status <> 'active' THEN
        RAISE EXCEPTION 'only an active template can advance its current version'
            USING ERRCODE = '23514';
    END IF;
    SELECT * INTO replacement_record
    FROM sklegal_legal.lock_exact_work_product_template_version(
        target_tenant_id,
        replacement_version_id,
        replacement_version_number,
        replacement_content_sha256
    );
    IF replacement_record.template_id <> target_template_id THEN
        RAISE EXCEPTION 'replacement version must belong to the same template'
            USING ERRCODE = '23514';
    END IF;
    IF replacement_record.status <> 'frozen' THEN
        RAISE EXCEPTION 'replacement template version must be frozen'
            USING ERRCODE = '23514';
    END IF;
    IF replacement_record.version_number <= template_record.current_version_number THEN
        RAISE EXCEPTION 'template supersession chain advances forward only'
            USING ERRCODE = '23514';
    END IF;
    UPDATE sklegal_legal.work_product_templates
    SET current_version_id = replacement_version_id,
        current_version_number = replacement_version_number,
        current_content_sha256 = replacement_content_sha256,
        version = version + 1
    WHERE tenant_id = target_tenant_id
      AND id = target_template_id
    RETURNING * INTO template_record;
    RETURN template_record;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.advance_work_product_template(
    uuid, uuid, bigint, uuid, bigint, text
) FROM PUBLIC;

CREATE FUNCTION sklegal_legal.require_open_work_product_unknown_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.status <> 'open' OR NEW.resolved_by_principal_id IS NOT NULL
       OR NEW.resolved_at IS NOT NULL THEN
        RAISE EXCEPTION 'work product unknown must be inserted as an open blocker'
            USING ERRCODE = '23514';
    END IF;
    PERFORM sklegal_legal.lock_exact_work_product_version(
        NEW.tenant_id,
        NEW.matter_id,
        NEW.work_product_version_id,
        NEW.work_product_version_number,
        NEW.work_product_content_sha256
    );
    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.require_open_work_product_unknown_insert()
FROM PUBLIC;

CREATE FUNCTION sklegal_legal.control_work_product_unknown_update()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.matter_id IS DISTINCT FROM OLD.matter_id
       OR NEW.work_product_version_id IS DISTINCT FROM OLD.work_product_version_id
       OR NEW.work_product_version_number IS DISTINCT FROM OLD.work_product_version_number
       OR NEW.work_product_content_sha256 IS DISTINCT FROM OLD.work_product_content_sha256
       OR NEW.placeholder_key IS DISTINCT FROM OLD.placeholder_key
       OR NEW.hint IS DISTINCT FROM OLD.hint
       OR NEW.classification IS DISTINCT FROM OLD.classification
       OR NEW.completeness IS DISTINCT FROM OLD.completeness
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'work product unknown identity and version binding are immutable'
            USING ERRCODE = '55000';
    END IF;
    IF NEW.version <> OLD.version + 1
       OR NOT (OLD.status = 'open' AND NEW.status = 'resolved') THEN
        RAISE EXCEPTION 'invalid work product unknown transition'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.resolved_by_principal_id IS NOT NULL OR OLD.resolved_at IS NOT NULL
       OR NEW.resolved_by_principal_id IS NULL OR NEW.resolved_at IS NULL THEN
        RAISE EXCEPTION 'unknown resolution evidence is set once at resolution'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.resolved_at > clock_timestamp() THEN
        RAISE EXCEPTION 'unknown resolution time cannot be in the future'
            USING ERRCODE = '22007';
    END IF;
    NEW.updated_at := GREATEST(OLD.updated_at, clock_timestamp());
    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.control_work_product_unknown_update()
FROM PUBLIC;

CREATE FUNCTION sklegal_legal.resolve_work_product_unknown(
    target_tenant_id uuid,
    target_matter_id uuid,
    target_unknown_id uuid,
    expected_version bigint,
    resolution_at timestamptz
)
RETURNS sklegal_legal.work_product_unknowns
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    unknown_record sklegal_legal.work_product_unknowns%ROWTYPE;
    actor_id uuid;
BEGIN
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_legal.has_matter_membership(target_tenant_id, target_matter_id) THEN
        RAISE EXCEPTION 'unknown resolution is not authorized'
            USING ERRCODE = '42501';
    END IF;
    actor_id := sklegal_identity.current_principal_id();
    IF actor_id IS NULL THEN
        RAISE EXCEPTION 'unknown resolution requires a bound principal'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO unknown_record
    FROM sklegal_legal.work_product_unknowns
    WHERE tenant_id = target_tenant_id
      AND matter_id = target_matter_id
      AND id = target_unknown_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'work product unknown does not exist in the authorized scope'
            USING ERRCODE = 'P0002';
    END IF;
    IF unknown_record.version <> expected_version THEN
        RAISE EXCEPTION 'work product unknown conflict' USING ERRCODE = '40001';
    END IF;
    IF resolution_at IS NULL OR resolution_at > clock_timestamp() THEN
        RAISE EXCEPTION 'unknown resolution time must be present and not in the future'
            USING ERRCODE = '22007';
    END IF;
    PERFORM sklegal_legal.lock_exact_work_product_version(
        target_tenant_id,
        target_matter_id,
        unknown_record.work_product_version_id,
        unknown_record.work_product_version_number,
        unknown_record.work_product_content_sha256
    );
    UPDATE sklegal_legal.work_product_unknowns
    SET status = 'resolved',
        resolved_by_principal_id = actor_id,
        resolved_at = resolution_at,
        version = version + 1
    WHERE tenant_id = target_tenant_id
      AND matter_id = target_matter_id
      AND id = target_unknown_id
    RETURNING * INTO unknown_record;
    RETURN unknown_record;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.resolve_work_product_unknown(
    uuid, uuid, uuid, bigint, timestamptz
) FROM PUBLIC;

CREATE OR REPLACE FUNCTION sklegal_legal.transition_work_product_version(
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
    IF target_status = 'frozen' AND EXISTS (
        SELECT 1
        FROM sklegal_legal.work_product_unknowns AS unknown
        WHERE unknown.tenant_id = target_tenant_id
          AND unknown.matter_id = target_matter_id
          AND unknown.work_product_version_id = target_version_id
          AND unknown.work_product_version_number = version_record.version_number
          AND unknown.work_product_content_sha256 = version_record.content_sha256
          AND unknown.status = 'open'
    ) THEN
        RAISE EXCEPTION 'work product version has unresolved bracketed unknowns'
            USING ERRCODE = '23514';
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

CREATE TRIGGER domain_id_non_nil
BEFORE INSERT OR UPDATE ON sklegal_legal.work_product_templates
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_nil_domain_ids();
CREATE TRIGGER initialize_record
BEFORE INSERT ON sklegal_legal.work_product_templates
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.initialize_record_audit();
CREATE TRIGGER optimistic_record_update
BEFORE UPDATE ON sklegal_legal.work_product_templates
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.enforce_optimistic_record_update();
CREATE TRIGGER work_product_template_draft_insert
BEFORE INSERT ON sklegal_legal.work_product_templates
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_draft_work_product_template_insert();
CREATE TRIGGER zz_work_product_template_controlled_transition
BEFORE UPDATE ON sklegal_legal.work_product_templates
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.enforce_work_product_template_transition();

CREATE TRIGGER domain_id_non_nil
BEFORE INSERT OR UPDATE ON sklegal_legal.work_product_template_versions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_nil_domain_ids();
CREATE TRIGGER initialize_record
BEFORE INSERT ON sklegal_legal.work_product_template_versions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.initialize_record_audit();
CREATE TRIGGER work_product_template_version_draft_insert
BEFORE INSERT ON sklegal_legal.work_product_template_versions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_draft_work_product_template_version_insert();
CREATE TRIGGER work_product_template_version_controlled_update
BEFORE UPDATE ON sklegal_legal.work_product_template_versions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.control_work_product_template_version_update();
CREATE TRIGGER work_product_template_version_delete_denied
BEFORE DELETE ON sklegal_legal.work_product_template_versions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

CREATE TRIGGER domain_id_non_nil
BEFORE INSERT OR UPDATE ON sklegal_legal.work_product_unknowns
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_nil_domain_ids();
CREATE TRIGGER initialize_record
BEFORE INSERT ON sklegal_legal.work_product_unknowns
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.initialize_record_audit();
CREATE TRIGGER work_product_unknown_open_insert
BEFORE INSERT ON sklegal_legal.work_product_unknowns
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.require_open_work_product_unknown_insert();
CREATE TRIGGER work_product_unknown_controlled_update
BEFORE UPDATE ON sklegal_legal.work_product_unknowns
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.control_work_product_unknown_update();
CREATE TRIGGER work_product_unknown_delete_denied
BEFORE DELETE ON sklegal_legal.work_product_unknowns
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

ALTER TABLE sklegal_legal.work_product_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.work_product_templates FORCE ROW LEVEL SECURITY;
CREATE POLICY work_product_template_boundary ON sklegal_legal.work_product_templates
FOR ALL
USING (sklegal_identity.record_is_authorized(tenant_id, NULL))
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, NULL));

ALTER TABLE sklegal_legal.work_product_template_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.work_product_template_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY work_product_template_version_select ON sklegal_legal.work_product_template_versions
FOR SELECT USING (sklegal_identity.record_is_authorized(tenant_id, NULL));
CREATE POLICY work_product_template_version_draft_insert ON sklegal_legal.work_product_template_versions
FOR INSERT WITH CHECK (
    sklegal_identity.record_is_authorized(tenant_id, NULL) AND status = 'draft'
);
CREATE POLICY work_product_template_version_controlled_update ON sklegal_legal.work_product_template_versions
FOR UPDATE
USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, NULL)
)
WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, NULL)
);

ALTER TABLE sklegal_legal.work_product_unknowns ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.work_product_unknowns FORCE ROW LEVEL SECURITY;
CREATE POLICY work_product_unknown_select ON sklegal_legal.work_product_unknowns
FOR SELECT USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY work_product_unknown_open_insert ON sklegal_legal.work_product_unknowns
FOR INSERT WITH CHECK (
    sklegal_identity.record_is_authorized(tenant_id, matter_id) AND status = 'open'
);
CREATE POLICY work_product_unknown_controlled_update ON sklegal_legal.work_product_unknowns
FOR UPDATE
USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
)
WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
);

-- sklegal:down
DROP POLICY work_product_unknown_controlled_update ON sklegal_legal.work_product_unknowns;
DROP POLICY work_product_unknown_open_insert ON sklegal_legal.work_product_unknowns;
DROP POLICY work_product_unknown_select ON sklegal_legal.work_product_unknowns;
ALTER TABLE sklegal_legal.work_product_unknowns NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.work_product_unknowns DISABLE ROW LEVEL SECURITY;

DROP POLICY work_product_template_version_controlled_update ON sklegal_legal.work_product_template_versions;
DROP POLICY work_product_template_version_draft_insert ON sklegal_legal.work_product_template_versions;
DROP POLICY work_product_template_version_select ON sklegal_legal.work_product_template_versions;
ALTER TABLE sklegal_legal.work_product_template_versions NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.work_product_template_versions DISABLE ROW LEVEL SECURITY;

DROP POLICY work_product_template_boundary ON sklegal_legal.work_product_templates;
ALTER TABLE sklegal_legal.work_product_templates NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.work_product_templates DISABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION sklegal_legal.transition_work_product_version(
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

DROP FUNCTION sklegal_legal.resolve_work_product_unknown(uuid, uuid, uuid, bigint, timestamptz);
DROP FUNCTION sklegal_legal.advance_work_product_template(uuid, uuid, bigint, uuid, bigint, text);
DROP FUNCTION sklegal_legal.transition_work_product_template(uuid, uuid, bigint, text);
DROP FUNCTION sklegal_legal.transition_work_product_template_version(uuid, uuid, bigint, text);
DROP FUNCTION sklegal_legal.lock_exact_work_product_template_version(uuid, uuid, bigint, text);

DROP TABLE sklegal_legal.work_product_unknowns;
ALTER TABLE sklegal_legal.work_product_templates
DROP CONSTRAINT work_product_template_current_version_fk;
DROP TABLE sklegal_legal.work_product_template_versions;
DROP TABLE sklegal_legal.work_product_templates;

DROP FUNCTION sklegal_legal.control_work_product_unknown_update();
DROP FUNCTION sklegal_legal.require_open_work_product_unknown_insert();
DROP FUNCTION sklegal_legal.control_work_product_template_version_update();
DROP FUNCTION sklegal_legal.require_draft_work_product_template_version_insert();
DROP FUNCTION sklegal_legal.enforce_work_product_template_transition();
DROP FUNCTION sklegal_legal.require_draft_work_product_template_insert();
