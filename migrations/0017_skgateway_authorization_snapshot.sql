-- sklegal:up
CREATE FUNCTION sklegal_legal.skgateway_authorization_snapshot(
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
    target_material_id uuid;
    target_material_version bigint;
    target_principal_id uuid;
    principal_snapshot jsonb;
    policy_snapshot jsonb;
    normalized_resource jsonb;
    normalized_context jsonb;
    effective_classification text;
    privilege_state text;
    wall_state text := 'clear';
    protective_label_active boolean;
    observed_at timestamptz := clock_timestamp();
    wall jsonb;
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
           SELECT 1
           FROM jsonb_each(p_resource) AS item
           WHERE jsonb_typeof(item.value) <> 'string'
       )
       OR EXISTS (
           SELECT 1
           FROM jsonb_each(p_context) AS item
           WHERE jsonb_typeof(item.value) <> 'string'
       )
       OR p_resource->>'route_id' !~ '^[a-z0-9][a-z0-9._:/-]{0,159}$'
       OR p_context->>'purpose' <> 'legal_research' THEN
        RAISE EXCEPTION 'SKGateway authorization scope is unavailable'
            USING ERRCODE = '42501';
    END IF;

    BEGIN
        target_tenant_id := (p_resource->>'tenant_id')::uuid;
        target_matter_id := (p_resource->>'matter_id')::uuid;
        target_material_id := (p_resource->>'material_id')::uuid;
        target_material_version := (p_resource->>'material_version')::bigint;
    EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
        RAISE EXCEPTION 'SKGateway authorization scope is unavailable'
            USING ERRCODE = '42501';
    END;

    target_principal_id := sklegal_identity.current_principal_id();
    IF target_material_version < 1
       OR target_tenant_id IS DISTINCT FROM sklegal_identity.current_tenant_id()
       OR target_principal_id IS NULL
       OR NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_legal.has_matter_membership(
           target_tenant_id, target_matter_id
       ) THEN
        RAISE EXCEPTION 'SKGateway authorization scope is unavailable'
            USING ERRCODE = '42501';
    END IF;

    principal_snapshot := sklegal_identity.capability_principal_snapshot(
        target_tenant_id, target_principal_id
    );
    IF NOT (principal_snapshot->>'active')::boolean
       OR principal_snapshot#>>'{principal,subject}' IS DISTINCT FROM p_subject THEN
        RAISE EXCEPTION 'SKGateway authorization identity is unavailable'
            USING ERRCODE = '42501';
    END IF;

    policy_snapshot := sklegal_legal.material_policy_snapshot(
        target_tenant_id,
        target_matter_id,
        target_material_id,
        target_material_version,
        target_principal_id
    );
    IF NOT (policy_snapshot->>'classification_state_complete')::boolean
       OR NOT (policy_snapshot->>'wall_state_complete')::boolean THEN
        RAISE EXCEPTION 'SKGateway authorization policy is unavailable'
            USING ERRCODE = '55000';
    END IF;

    SELECT source->>'classification'
    INTO effective_classification
    FROM jsonb_array_elements(
        policy_snapshot->'classification_sources'
    ) AS source
    ORDER BY CASE source->>'classification'
        WHEN 'public' THEN 0
        WHEN 'internal' THEN 1
        WHEN 'confidential' THEN 2
        WHEN 'privileged_work_product' THEN 3
        WHEN 'highly_restricted' THEN 4
        ELSE -1
    END DESC
    LIMIT 1;
    IF effective_classification IS NULL THEN
        RAISE EXCEPTION 'SKGateway authorization classification is unavailable'
            USING ERRCODE = '55000';
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM jsonb_array_elements(
            policy_snapshot->'privilege_labels'
        ) AS label
        WHERE (label->>'active')::boolean
    ) OR EXISTS (
        SELECT 1
        FROM jsonb_array_elements(
            policy_snapshot->'work_product_labels'
        ) AS label
        WHERE (label->>'active')::boolean
    ) INTO protective_label_active;
    IF protective_label_active
       AND effective_classification IN ('public', 'internal', 'confidential') THEN
        effective_classification := 'privileged_work_product';
    END IF;
    privilege_state := CASE
        WHEN protective_label_active THEN 'protected'
        ELSE 'none'
    END;

    FOR wall IN
        SELECT item
        FROM jsonb_array_elements(policy_snapshot->'walls') AS item
        WHERE (item->>'active')::boolean
          AND (item->>'effective_from')::timestamptz <= observed_at
          AND (
              item->>'effective_to' IS NULL
              OR (item->>'effective_to')::timestamptz > observed_at
          )
    LOOP
        IF NOT (wall->>'membership_complete')::boolean
           OR EXISTS (
               SELECT 1
               FROM jsonb_array_elements(
                   policy_snapshot->'wall_memberships'
               ) AS membership
               WHERE membership->>'wall_id' = wall->>'wall_id'
                 AND membership->>'disposition' = 'excluded'
                 AND (membership->>'effective_from')::timestamptz <= observed_at
                 AND (
                     membership->>'effective_to' IS NULL
                     OR (membership->>'effective_to')::timestamptz > observed_at
                 )
           )
           OR NOT EXISTS (
               SELECT 1
               FROM jsonb_array_elements(
                   policy_snapshot->'wall_memberships'
               ) AS membership
               WHERE membership->>'wall_id' = wall->>'wall_id'
                 AND membership->>'disposition' = 'allowed'
                 AND (membership->>'effective_from')::timestamptz <= observed_at
                 AND (
                     membership->>'effective_to' IS NULL
                     OR (membership->>'effective_to')::timestamptz > observed_at
                 )
           ) THEN
            RAISE EXCEPTION 'SKGateway ethical-wall scope is unavailable'
                USING ERRCODE = '42501';
        END IF;
        wall_state := 'allowed';
    END LOOP;

    normalized_resource := jsonb_build_object(
        'tenant_id', target_tenant_id::text,
        'matter_id', target_matter_id::text,
        'material_id', target_material_id::text,
        'material_version', target_material_version::text,
        'route_id', p_resource->>'route_id'
    );
    normalized_context := jsonb_build_object(
        'purpose', 'legal_research',
        'classification', effective_classification,
        'privilege', privilege_state,
        'ethical_wall', wall_state
    );
    IF normalized_resource IS DISTINCT FROM p_resource
       OR normalized_context IS DISTINCT FROM p_context THEN
        RAISE EXCEPTION 'SKGateway authorization facts do not match current state'
            USING ERRCODE = '42501';
    END IF;

    RETURN jsonb_build_object(
        'revision', sklegal_audit.payload_sha256(jsonb_build_object(
            'principal_revision', principal_snapshot->>'revision',
            'policy_revision', policy_snapshot->>'policy_revision',
            'service_identity', p_service_identity,
            'resource', normalized_resource,
            'context', normalized_context
        )),
        'service_identity', p_service_identity,
        'facts', jsonb_build_object(
            'subject', principal_snapshot#>>'{principal,subject}',
            'capability', 'skgateway.infer',
            'resource', normalized_resource,
            'context', normalized_context
        )
    );
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.skgateway_authorization_snapshot(
    text, text, jsonb, jsonb
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sklegal_legal.skgateway_authorization_snapshot(
    text, text, jsonb, jsonb
) TO sklegal_runtime;

-- sklegal:down
DROP FUNCTION sklegal_legal.skgateway_authorization_snapshot(
    text, text, jsonb, jsonb
);
