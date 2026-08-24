-- sklegal:up
CREATE TABLE sklegal_legal.skgateway_qualification_scopes (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    resource_id uuid NOT NULL,
    resource_version sklegal_legal.record_version NOT NULL,
    source_reference_id uuid NOT NULL,
    resource_sha256 sklegal_legal.sha256_digest NOT NULL,
    route_id text NOT NULL CHECK (
        route_id = 'qwen.corpus-summary.skgateway.v1'
    ),
    model_route text NOT NULL CHECK (model_route = 'local_qwen'),
    workflow_reference_id uuid NOT NULL,
    workflow_run_id text NOT NULL CHECK (
        workflow_run_id = 'workflow:skl-s3-10d2'
    ),
    service_identity text NOT NULL CHECK (
        service_identity = 'capauth:sklegal-model-gateway@chiap01.skworld'
    ),
    active boolean NOT NULL DEFAULT true,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (
        tenant_id, matter_id, principal_id, resource_id, resource_version,
        route_id, workflow_run_id
    ),
    FOREIGN KEY (tenant_id, principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, matter_id, source_reference_id)
        REFERENCES sklegal_legal.source_references(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, workflow_reference_id)
        REFERENCES sklegal_workflow.workflow_references(tenant_id, matter_id, id),
    CHECK (resource_version > 0),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (updated_at >= created_at)
);

CREATE TRIGGER domain_id_non_nil
BEFORE INSERT OR UPDATE ON sklegal_legal.skgateway_qualification_scopes
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_nil_domain_ids();

CREATE TRIGGER initialize_record
BEFORE INSERT ON sklegal_legal.skgateway_qualification_scopes
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.initialize_record_audit();

CREATE TRIGGER optimistic_record_update
BEFORE UPDATE ON sklegal_legal.skgateway_qualification_scopes
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.enforce_optimistic_record_update();

ALTER TABLE sklegal_legal.skgateway_qualification_scopes
    ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.skgateway_qualification_scopes
    FORCE ROW LEVEL SECURITY;

CREATE POLICY qualification_scope_boundary
ON sklegal_legal.skgateway_qualification_scopes
FOR SELECT USING (
    tenant_id = sklegal_identity.current_tenant_id()
    AND principal_id = sklegal_identity.current_principal_id()
    AND sklegal_legal.has_matter_membership(tenant_id, matter_id)
);

CREATE POLICY qualification_scope_controlled_read
ON sklegal_legal.skgateway_qualification_scopes
FOR SELECT USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
);

CREATE POLICY qualification_scope_controlled_insert
ON sklegal_legal.skgateway_qualification_scopes
FOR INSERT WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
);

CREATE POLICY qualification_scope_controlled_update
ON sklegal_legal.skgateway_qualification_scopes
FOR UPDATE
USING (current_user = 'sklegal_migrator' AND session_user <> current_user)
WITH CHECK (current_user = 'sklegal_migrator' AND session_user <> current_user);

CREATE POLICY qualification_scope_controlled_delete
ON sklegal_legal.skgateway_qualification_scopes
FOR DELETE USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
);

CREATE FUNCTION sklegal_legal.skgateway_qualification_scope_snapshot(
    p_service_identity text,
    p_subject text,
    p_resource jsonb,
    p_context jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    target_tenant_id uuid;
    target_matter_id uuid;
    target_resource_id uuid;
    target_resource_version bigint;
    target_principal_id uuid;
    principal_snapshot jsonb;
    authorization_snapshot jsonb;
    current_scope sklegal_legal.skgateway_qualification_scopes%ROWTYPE;
    normalized_scope jsonb;
BEGIN
    IF p_service_identity IS DISTINCT FROM
           'capauth:sklegal-model-gateway@chiap01.skworld'
       OR p_subject IS NULL
       OR length(btrim(p_subject)) NOT BETWEEN 1 AND 256
       OR jsonb_typeof(p_resource) <> 'object'
       OR jsonb_typeof(p_context) <> 'object'
       OR (SELECT count(*) FROM jsonb_object_keys(p_resource)) <> 5
       OR (SELECT count(*) FROM jsonb_object_keys(p_context)) <> 4
       OR NOT p_resource ?& ARRAY[
           'tenant_id', 'matter_id', 'material_id', 'material_version', 'route_id'
       ]
       OR NOT p_context ?& ARRAY[
           'purpose', 'classification', 'privilege', 'ethical_wall'
       ]
       OR EXISTS (
           SELECT 1 FROM jsonb_each(p_resource) AS item
           WHERE jsonb_typeof(item.value) <> 'string'
              OR length(item.value #>> '{}') = 0
       )
       OR EXISTS (
           SELECT 1 FROM jsonb_each(p_context) AS item
           WHERE jsonb_typeof(item.value) <> 'string'
              OR length(item.value #>> '{}') = 0
       )
       OR p_resource->>'route_id' IS DISTINCT FROM
          'qwen.corpus-summary.skgateway.v1'
       OR p_context->>'purpose' IS DISTINCT FROM 'legal_research' THEN
        RAISE EXCEPTION 'SKGateway qualification scope is unavailable'
            USING ERRCODE = '42501';
    END IF;

    BEGIN
        target_tenant_id := (p_resource->>'tenant_id')::uuid;
        target_matter_id := (p_resource->>'matter_id')::uuid;
        target_resource_id := (p_resource->>'material_id')::uuid;
        target_resource_version := (p_resource->>'material_version')::bigint;
    EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
        RAISE EXCEPTION 'SKGateway qualification scope is unavailable'
            USING ERRCODE = '42501';
    END;

    target_principal_id := sklegal_identity.current_principal_id();
    IF target_resource_version < 1
       OR target_tenant_id IS DISTINCT FROM sklegal_identity.current_tenant_id()
       OR target_principal_id IS NULL
       OR NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_legal.has_matter_membership(
           target_tenant_id, target_matter_id
       ) THEN
        RAISE EXCEPTION 'SKGateway qualification scope is unavailable'
            USING ERRCODE = '42501';
    END IF;

    principal_snapshot := sklegal_identity.capability_principal_snapshot(
        target_tenant_id, target_principal_id
    );
    IF NOT (principal_snapshot->>'active')::boolean
       OR principal_snapshot#>>'{principal,subject}' IS DISTINCT FROM p_subject THEN
        RAISE EXCEPTION 'SKGateway qualification identity is unavailable'
            USING ERRCODE = '42501';
    END IF;

    SELECT scope.* INTO STRICT current_scope
    FROM sklegal_legal.skgateway_qualification_scopes AS scope
    JOIN sklegal_legal.source_references AS source
      ON source.tenant_id = scope.tenant_id
     AND source.matter_id = scope.matter_id
     AND source.id = scope.source_reference_id
    JOIN sklegal_workflow.workflow_references AS workflow
      ON workflow.tenant_id = scope.tenant_id
     AND workflow.matter_id = scope.matter_id
     AND workflow.id = scope.workflow_reference_id
    WHERE scope.tenant_id = target_tenant_id
      AND scope.matter_id = target_matter_id
      AND scope.principal_id = target_principal_id
      AND scope.resource_id = target_resource_id
      AND scope.resource_version = target_resource_version
      AND scope.route_id = p_resource->>'route_id'
      AND scope.service_identity = p_service_identity
      AND scope.active
      AND scope.valid_from <= clock_timestamp()
      AND (scope.valid_to IS NULL OR scope.valid_to > clock_timestamp())
      AND source.version = scope.resource_version
      AND source.content_sha256 = scope.resource_sha256
      AND length(source.content_sha256::text) = 64
      AND workflow.run_id = scope.workflow_run_id
      AND workflow.run_id = 'workflow:skl-s3-10d2'
      AND workflow.status IN ('pending', 'running', 'waiting')
      AND workflow.completeness = 'complete';

    authorization_snapshot := sklegal_legal.skgateway_authorization_snapshot(
        p_service_identity,
        p_subject,
        p_resource,
        p_context
    );
    IF authorization_snapshot#>>'{facts,subject}' IS DISTINCT FROM p_subject
       OR authorization_snapshot#>>'{facts,capability}' IS DISTINCT FROM
          'skgateway.infer'
       OR authorization_snapshot#>'{facts,resource}' IS DISTINCT FROM p_resource
       OR authorization_snapshot#>'{facts,context}' IS DISTINCT FROM p_context THEN
        RAISE EXCEPTION 'SKGateway qualification facts are unavailable'
            USING ERRCODE = '42501';
    END IF;

    normalized_scope := jsonb_build_object(
        'tenant_id', target_tenant_id::text,
        'matter_id', target_matter_id::text,
        'resource_id', target_resource_id::text,
        'resource_version', target_resource_version,
        'resource_sha256', current_scope.resource_sha256::text,
        'model_route', current_scope.model_route,
        'workflow_run_id', current_scope.workflow_run_id
    );
    RETURN jsonb_build_object(
        'revision', sklegal_audit.payload_sha256(jsonb_build_object(
            'principal_revision', principal_snapshot->>'revision',
            'authorization_revision', authorization_snapshot->>'revision',
            'scope_id', current_scope.id,
            'scope_version', current_scope.version,
            'scope', normalized_scope
        )),
        'service_identity', p_service_identity,
        'principal', principal_snapshot->'principal',
        'scope', normalized_scope,
        'facts', authorization_snapshot->'facts'
    );
EXCEPTION
    WHEN no_data_found OR too_many_rows THEN
        RAISE EXCEPTION 'SKGateway qualification scope is unavailable'
            USING ERRCODE = '42501';
END;
$function$;

REVOKE ALL ON TABLE sklegal_legal.skgateway_qualification_scopes FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_legal.skgateway_qualification_scope_snapshot(
    text, text, jsonb, jsonb
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sklegal_legal.skgateway_qualification_scope_snapshot(
    text, text, jsonb, jsonb
) TO sklegal_runtime;

-- sklegal:down
DROP FUNCTION sklegal_legal.skgateway_qualification_scope_snapshot(
    text, text, jsonb, jsonb
);
DROP POLICY qualification_scope_controlled_delete
ON sklegal_legal.skgateway_qualification_scopes;
DROP POLICY qualification_scope_controlled_update
ON sklegal_legal.skgateway_qualification_scopes;
DROP POLICY qualification_scope_controlled_insert
ON sklegal_legal.skgateway_qualification_scopes;
DROP POLICY qualification_scope_controlled_read
ON sklegal_legal.skgateway_qualification_scopes;
DROP POLICY qualification_scope_boundary
ON sklegal_legal.skgateway_qualification_scopes;
ALTER TABLE sklegal_legal.skgateway_qualification_scopes
    NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.skgateway_qualification_scopes
    DISABLE ROW LEVEL SECURITY;
DROP TRIGGER optimistic_record_update
ON sklegal_legal.skgateway_qualification_scopes;
DROP TRIGGER initialize_record
ON sklegal_legal.skgateway_qualification_scopes;
DROP TRIGGER domain_id_non_nil
ON sklegal_legal.skgateway_qualification_scopes;
DROP TABLE sklegal_legal.skgateway_qualification_scopes;
