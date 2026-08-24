-- sklegal:up
CREATE SCHEMA sklegal_activity AUTHORIZATION sklegal_migrator;

CREATE TABLE sklegal_activity.entries (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    audit_event_id uuid NOT NULL,
    event_sequence bigint NOT NULL CHECK (event_sequence >= 1),
    event_sha256 sklegal_legal.sha256_digest NOT NULL,
    previous_event_sha256 sklegal_legal.sha256_digest,
    action text NOT NULL CHECK (action ~ '^[a-z0-9][a-z0-9._:/@-]{0,254}$'),
    boundary text NOT NULL CHECK (
        boundary IN ('api', 'workflow', 'tool', 'model', 'human', 'connector')
    ),
    outcome text NOT NULL CHECK (outcome IN ('allow', 'deny', 'success', 'failure')),
    reason_code text NOT NULL CHECK (
        reason_code ~ '^[a-z0-9][a-z0-9._:/@-]{0,254}$'
    ),
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL,
    actor_principal_id uuid NOT NULL,
    authorization_decision_id uuid,
    policy_decision_id uuid,
    correlation_id uuid NOT NULL,
    causation_id uuid,
    run_id uuid NOT NULL,
    workflow_reference_id uuid,
    agent_run_id uuid,
    tool_call_id uuid,
    source_kind text NOT NULL CHECK (source_kind IN (
        'audit_event', 'matter_event', 'workflow_reference', 'agent_run',
        'tool_call', 'source_reference', 'work_product_version', 'approval',
        'execution_event', 'receipt', 'correction'
    )),
    source_id uuid NOT NULL,
    source_version integer NOT NULL CHECK (source_version >= 1),
    source_sha256 sklegal_legal.sha256_digest NOT NULL,
    source_status text NOT NULL CHECK (
        source_status ~ '^[a-z0-9][a-z0-9._:/@-]{0,254}$'
    ),
    source_recorded_at timestamptz NOT NULL,
    corrects_source_id uuid,
    superseded_by_source_id uuid,
    superseded_by_source_version integer CHECK (
        superseded_by_source_version IS NULL OR superseded_by_source_version >= 1
    ),
    idempotency_key_sha256 sklegal_legal.sha256_digest NOT NULL,
    request_sha256 sklegal_legal.sha256_digest NOT NULL,
    projected_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, event_sequence),
    UNIQUE (tenant_id, audit_event_id),
    UNIQUE (tenant_id, matter_id, idempotency_key_sha256),
    UNIQUE (tenant_id, matter_id, source_kind, source_id, source_version),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, audit_event_id)
        REFERENCES sklegal_audit.events(tenant_id, id),
    FOREIGN KEY (tenant_id, actor_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (recorded_at >= occurred_at),
    CHECK (projected_at >= recorded_at),
    CHECK (corrects_source_id IS NULL OR corrects_source_id <> source_id),
    CHECK (
        (source_status = 'superseded') = (superseded_by_source_id IS NOT NULL)
    ),
    CHECK (
        (superseded_by_source_id IS NULL) =
        (superseded_by_source_version IS NULL)
    ),
    CHECK (
        superseded_by_source_id IS NULL OR superseded_by_source_id <> source_id
    ),
    CHECK (
        superseded_by_source_version IS NULL
        OR superseded_by_source_version > source_version
    )
);

CREATE TABLE sklegal_activity.projection_receipts (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    operation text NOT NULL CHECK (
        operation IN ('project', 'create_activity_export')
    ),
    principal_id uuid,
    authorization_decision_id uuid,
    policy_decision_id uuid,
    policy_revision sklegal_legal.sha256_digest,
    idempotency_key_sha256 sklegal_legal.sha256_digest NOT NULL,
    request_sha256 sklegal_legal.sha256_digest NOT NULL,
    result_id uuid NOT NULL,
    result_sha256 sklegal_legal.sha256_digest NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, operation, idempotency_key_sha256),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (
        operation <> 'create_activity_export'
        OR (
            principal_id IS NOT NULL
            AND authorization_decision_id IS NOT NULL
            AND policy_decision_id IS NOT NULL
            AND policy_revision IS NOT NULL
        )
    )
);

CREATE UNIQUE INDEX activity_receipt_key_idx
ON sklegal_activity.projection_receipts (idempotency_key_sha256);

CREATE TABLE sklegal_activity.export_proposals (
    proposal_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 512),
    first_event_sequence bigint NOT NULL CHECK (first_event_sequence >= 1),
    last_event_sequence bigint NOT NULL CHECK (
        last_event_sequence >= first_event_sequence
    ),
    item_count integer NOT NULL CHECK (item_count BETWEEN 1 AND 10000),
    event_ids uuid[] NOT NULL,
    event_sha256s sklegal_legal.sha256_digest[] NOT NULL,
    selection_sha256 sklegal_legal.sha256_digest NOT NULL,
    content_sha256 sklegal_legal.sha256_digest NOT NULL,
    projected_sequence bigint NOT NULL CHECK (projected_sequence >= last_event_sequence),
    projected_event_sha256 sklegal_legal.sha256_digest NOT NULL,
    tenant_head_sequence bigint NOT NULL CHECK (
        tenant_head_sequence >= projected_sequence
    ),
    tenant_head_sha256 sklegal_legal.sha256_digest NOT NULL,
    proposed_by_principal_id uuid NOT NULL,
    authorization_decision_id uuid NOT NULL,
    policy_decision_id uuid NOT NULL,
    idempotency_key_sha256 sklegal_legal.sha256_digest NOT NULL,
    request_sha256 sklegal_legal.sha256_digest NOT NULL,
    status text NOT NULL DEFAULT 'proposed' CHECK (status = 'proposed'),
    approval_id uuid,
    dispatch_state text NOT NULL DEFAULT 'not_requested' CHECK (
        dispatch_state = 'not_requested'
    ),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, proposal_id),
    UNIQUE (tenant_id, matter_id, idempotency_key_sha256),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, proposed_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (cardinality(event_ids) = item_count),
    CHECK (cardinality(event_sha256s) = item_count),
    CHECK (approval_id IS NULL)
);

CREATE FUNCTION sklegal_activity.reject_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    RAISE EXCEPTION 'Matter activity history is append only' USING ERRCODE = '55000';
END;
$function$;

CREATE TRIGGER activity_entries_append_only
BEFORE UPDATE OR DELETE ON sklegal_activity.entries
FOR EACH ROW EXECUTE FUNCTION sklegal_activity.reject_change();
CREATE TRIGGER activity_receipts_append_only
BEFORE UPDATE OR DELETE ON sklegal_activity.projection_receipts
FOR EACH ROW EXECUTE FUNCTION sklegal_activity.reject_change();
CREATE TRIGGER activity_exports_append_only
BEFORE UPDATE OR DELETE ON sklegal_activity.export_proposals
FOR EACH ROW EXECUTE FUNCTION sklegal_activity.reject_change();

ALTER TABLE sklegal_activity.entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_activity.entries FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_activity.projection_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_activity.projection_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_activity.export_proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_activity.export_proposals FORCE ROW LEVEL SECURITY;

CREATE POLICY activity_entry_select ON sklegal_activity.entries
FOR SELECT USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY activity_entry_controlled_insert ON sklegal_activity.entries
FOR INSERT WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
);
CREATE POLICY activity_receipt_select ON sklegal_activity.projection_receipts
FOR SELECT USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND (
        sklegal_identity.record_is_authorized(tenant_id, matter_id)
        OR current_setting(
            'sklegal.activity_replay_key_sha256', true
        ) = idempotency_key_sha256::text
    )
);
CREATE POLICY activity_receipt_controlled_insert
ON sklegal_activity.projection_receipts
FOR INSERT WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
);
CREATE POLICY activity_export_select ON sklegal_activity.export_proposals
FOR SELECT USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY activity_export_controlled_insert ON sklegal_activity.export_proposals
FOR INSERT WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
);

CREATE FUNCTION sklegal_activity.runtime_ready()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
RETURN COALESCE(
    sklegal_identity.runtime_role_is_safe()
    AND has_schema_privilege(session_user, 'sklegal_activity', 'USAGE')
    AND has_schema_privilege(session_user, 'sklegal_audit', 'USAGE')
    AND has_table_privilege(session_user, 'sklegal_activity.entries', 'SELECT')
    AND has_table_privilege(
        session_user, 'sklegal_activity.export_proposals', 'SELECT'
    )
    AND has_table_privilege(
        session_user, 'sklegal_audit.chain_heads', 'SELECT'
    )
    AND has_table_privilege(
        session_user, 'sklegal_audit.projection_watermarks', 'SELECT'
    ),
    false
);

CREATE FUNCTION sklegal_activity.snapshot_is_valid(
    p_tenant_id uuid,
    p_event_sequence bigint,
    p_event_sha256 sklegal_legal.sha256_digest
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    valid boolean;
BEGIN
    PERFORM set_config('sklegal.audit_integrity_verification', '', true);
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR sklegal_identity.current_tenant_id() IS DISTINCT FROM p_tenant_id
       OR p_event_sequence < 1
       OR NOT sklegal_identity.record_is_authorized(p_tenant_id, NULL)
       OR NOT sklegal_audit.verify_current_tenant_chain() THEN
        RETURN false;
    END IF;
    PERFORM set_config('sklegal.audit_integrity_verification', 'on', true);
    SELECT EXISTS (
        SELECT 1
        FROM sklegal_audit.events AS event
        JOIN sklegal_audit.projection_watermarks AS watermark
          ON watermark.tenant_id = event.tenant_id
         AND watermark.projection = 'matter_activity.v1'
         AND watermark.event_sequence >= event.event_sequence
        WHERE event.tenant_id = p_tenant_id
          AND event.event_sequence = p_event_sequence
          AND event.event_sha256 = p_event_sha256
    ) INTO valid;
    PERFORM set_config('sklegal.audit_integrity_verification', '', true);
    RETURN valid;
END;
$function$;

CREATE FUNCTION sklegal_activity.supersession_graph_is_valid(
    p_tenant_id uuid,
    p_matter_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
RETURN COALESCE(
    sklegal_identity.runtime_role_is_safe()
    AND sklegal_identity.current_tenant_id() = p_tenant_id
    AND sklegal_identity.record_is_authorized(p_tenant_id, p_matter_id)
    AND NOT EXISTS (
        SELECT 1
        FROM sklegal_activity.entries AS source
        WHERE source.tenant_id = p_tenant_id
          AND source.matter_id = p_matter_id
          AND source.superseded_by_source_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM sklegal_activity.entries AS successor
              WHERE successor.tenant_id = source.tenant_id
                AND successor.matter_id = source.matter_id
                AND successor.source_kind = source.source_kind
                AND successor.source_id = source.superseded_by_source_id
                AND successor.source_version =
                    source.superseded_by_source_version
                AND successor.source_version > source.source_version
                AND successor.event_sequence > source.event_sequence
          )
    ),
    false
);

CREATE FUNCTION sklegal_activity.project_event(
    p_tenant_id uuid,
    p_matter_id uuid,
    p_audit_event_id uuid,
    p_event_sequence bigint,
    p_event_sha256 sklegal_legal.sha256_digest,
    p_source_kind text,
    p_source_id uuid,
    p_source_version integer,
    p_source_sha256 sklegal_legal.sha256_digest,
    p_source_status text,
    p_source_recorded_at timestamptz,
    p_corrects_source_id uuid,
    p_superseded_by_source_id uuid,
    p_superseded_by_source_version integer,
    p_causation_id uuid,
    p_workflow_reference_id uuid,
    p_agent_run_id uuid,
    p_tool_call_id uuid,
    p_idempotency_key_sha256 sklegal_legal.sha256_digest,
    p_request_sha256 sklegal_legal.sha256_digest,
    p_projection_audit_event_id uuid,
    p_projected_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    source_event sklegal_audit.events%ROWTYPE;
    prior sklegal_activity.projection_receipts%ROWTYPE;
    current_watermark sklegal_audit.projection_watermarks%ROWTYPE;
BEGIN
    PERFORM set_config('sklegal.audit_integrity_verification', '', true);
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_identity.record_is_authorized(p_tenant_id, p_matter_id)
       OR sklegal_identity.current_tenant_id() IS DISTINCT FROM p_tenant_id
       OR p_source_kind NOT IN (
            'audit_event', 'matter_event', 'workflow_reference', 'agent_run',
            'tool_call', 'source_reference', 'work_product_version', 'approval',
            'execution_event', 'receipt', 'correction'
       )
       OR p_source_version < 1
       OR p_source_status !~ '^[a-z0-9][a-z0-9._:/@-]{0,254}$'
       OR p_corrects_source_id = p_source_id
       OR p_superseded_by_source_id = p_source_id
       OR ((p_source_status = 'superseded') IS DISTINCT FROM
           (p_superseded_by_source_id IS NOT NULL))
       OR ((p_superseded_by_source_id IS NULL) IS DISTINCT FROM
           (p_superseded_by_source_version IS NULL))
       OR (
            p_superseded_by_source_version IS NOT NULL
            AND p_superseded_by_source_version <= p_source_version
       )
       OR p_projected_at < p_source_recorded_at
       OR p_projection_audit_event_id IS NULL THEN
        RAISE EXCEPTION 'Matter activity projection is unauthorized'
            USING ERRCODE = '42501';
    END IF;

    SELECT * INTO prior
    FROM sklegal_activity.projection_receipts
    WHERE tenant_id = p_tenant_id
      AND matter_id = p_matter_id
      AND operation = 'project'
      AND idempotency_key_sha256 = p_idempotency_key_sha256;
    IF FOUND THEN
        IF prior.request_sha256 <> p_request_sha256
           OR prior.result_id <> p_audit_event_id
           OR prior.result_sha256 <> p_event_sha256 THEN
            RAISE EXCEPTION 'Matter activity projection idempotency conflict'
                USING ERRCODE = '23505';
        END IF;
        RETURN false;
    END IF;

    IF NOT sklegal_audit.verify_current_tenant_chain() THEN
        RAISE EXCEPTION 'Tenant audit chain verification failed'
            USING ERRCODE = '55000';
    END IF;
    PERFORM set_config('sklegal.audit_integrity_verification', 'on', true);
    SELECT * INTO source_event
    FROM sklegal_audit.events
    WHERE tenant_id = p_tenant_id
      AND matter_id = p_matter_id
      AND id = p_audit_event_id
      AND event_sequence = p_event_sequence
      AND event_sha256 = p_event_sha256;
    IF NOT FOUND
       OR source_event.resource_id IS DISTINCT FROM p_source_id
       OR source_event.resource_kind IS DISTINCT FROM p_source_kind
       OR NOT source_event.attributes ? 'resource_sha256'
       OR source_event.attributes->>'resource_sha256' <> p_source_sha256
       OR NOT source_event.attributes ? 'resource_version'
       OR (source_event.attributes->>'resource_version')::integer
            <> p_source_version
       OR p_workflow_reference_id IS DISTINCT FROM (CASE
            WHEN p_source_kind = 'workflow_reference' THEN p_source_id
            ELSE NULL
       END)
       OR p_agent_run_id IS DISTINCT FROM (CASE
            WHEN p_source_kind = 'agent_run' THEN p_source_id
            ELSE NULL
       END)
       OR p_tool_call_id IS DISTINCT FROM (CASE
            WHEN p_source_kind = 'tool_call' THEN p_source_id
            ELSE NULL
       END)
       OR (
            p_causation_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM sklegal_audit.events AS cause
                WHERE cause.tenant_id = p_tenant_id
                  AND cause.matter_id = p_matter_id
                  AND cause.id = p_causation_id
                  AND cause.event_sequence < p_event_sequence
            )
       )
       OR (
            p_corrects_source_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM sklegal_activity.entries AS corrected
                WHERE corrected.tenant_id = p_tenant_id
                  AND corrected.matter_id = p_matter_id
                  AND corrected.source_id = p_corrects_source_id
            )
       )
       OR (
            p_superseded_by_source_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM sklegal_audit.events AS successor
                WHERE successor.tenant_id = p_tenant_id
                  AND successor.matter_id = p_matter_id
                  AND successor.resource_kind = p_source_kind
                  AND successor.resource_id = p_superseded_by_source_id
                  AND successor.event_sequence > p_event_sequence
                  AND successor.attributes ? 'resource_version'
                  AND (successor.attributes->>'resource_version')::integer =
                      p_superseded_by_source_version
                  AND (successor.attributes->>'resource_version')::integer >
                      p_source_version
            )
       ) THEN
        RAISE EXCEPTION 'Matter activity source evidence is unavailable'
            USING ERRCODE = '55000';
    END IF;
    PERFORM set_config('sklegal.audit_integrity_verification', '', true);

    INSERT INTO sklegal_activity.entries (
        tenant_id, matter_id, audit_event_id, event_sequence, event_sha256,
        previous_event_sha256, action, boundary, outcome, reason_code,
        occurred_at, recorded_at, actor_principal_id,
        authorization_decision_id, policy_decision_id, correlation_id,
        causation_id, run_id, workflow_reference_id, agent_run_id,
        tool_call_id, source_kind, source_id, source_version, source_sha256,
        source_status, source_recorded_at, corrects_source_id,
        superseded_by_source_id, superseded_by_source_version,
        idempotency_key_sha256, request_sha256,
        projected_at
    ) VALUES (
        source_event.tenant_id, source_event.matter_id, source_event.id,
        source_event.event_sequence, source_event.event_sha256,
        source_event.previous_event_sha256, source_event.action,
        source_event.boundary, source_event.outcome, source_event.reason_code,
        source_event.occurred_at, source_event.recorded_at,
        source_event.principal_id, source_event.authorization_decision_id,
        source_event.policy_decision_id, source_event.correlation_id,
        p_causation_id, source_event.run_id, p_workflow_reference_id,
        p_agent_run_id, p_tool_call_id, p_source_kind, p_source_id,
        p_source_version, p_source_sha256, p_source_status,
        p_source_recorded_at, p_corrects_source_id,
        p_superseded_by_source_id, p_superseded_by_source_version,
        p_idempotency_key_sha256,
        p_request_sha256, p_projected_at
    );

    INSERT INTO sklegal_activity.projection_receipts (
        tenant_id, matter_id, operation, idempotency_key_sha256,
        request_sha256, result_id, result_sha256, recorded_at
    ) VALUES (
        p_tenant_id, p_matter_id, 'project', p_idempotency_key_sha256,
        p_request_sha256, p_audit_event_id, p_event_sha256, p_projected_at
    );

    PERFORM sklegal_audit.append_event(
        p_projection_audit_event_id, p_tenant_id, p_matter_id,
        sklegal_identity.current_principal_id(), source_event.run_id,
        source_event.correlation_id,
        replace(p_projection_audit_event_id::text, '-', ''),
        substr(replace(p_projection_audit_event_id::text, '-', ''), 1, 16),
        '01', 'workflow', 'matter_activity.projected', 'audit_event',
        p_audit_event_id, source_event.authorization_decision_id,
        source_event.policy_decision_id, 'success', 'projected', p_projected_at,
        jsonb_build_object(
            'operation', 'project',
            'resource_version', p_source_version,
            'resource_sha256', p_source_sha256
        )
    );

    SELECT * INTO current_watermark
    FROM sklegal_audit.projection_watermarks
    WHERE tenant_id = p_tenant_id AND projection = 'matter_activity.v1';
    PERFORM sklegal_audit.advance_projection_watermark(
        'matter_activity.v1',
        COALESCE(current_watermark.event_sequence, 0),
        current_watermark.event_sha256,
        p_event_sequence,
        p_event_sha256
    );
    RETURN true;
END;
$function$;

CREATE FUNCTION sklegal_activity.export_proposal_json(
    value sklegal_activity.export_proposals
)
RETURNS jsonb
LANGUAGE sql
STABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
RETURN jsonb_build_object(
    'proposalId', value.proposal_id,
    'tenantId', value.tenant_id,
    'matterId', value.matter_id,
    'workProductKind', 'report',
    'status', value.status,
    'title', value.title,
    'firstEventSequence', value.first_event_sequence,
    'lastEventSequence', value.last_event_sequence,
    'itemCount', value.item_count,
    'eventIds', value.event_ids,
    'eventSha256s', value.event_sha256s,
    'selectionSha256', value.selection_sha256,
    'contentSha256', value.content_sha256,
    'projectedSequence', value.projected_sequence,
    'projectedEventSha256', value.projected_event_sha256,
    'tenantHeadSequence', value.tenant_head_sequence,
    'tenantHeadSha256', value.tenant_head_sha256,
    'proposedByPrincipalId', value.proposed_by_principal_id,
    'authorizationDecisionId', value.authorization_decision_id,
    'policyDecisionId', value.policy_decision_id,
    'idempotencyKeySha256', value.idempotency_key_sha256,
    'approvalId', value.approval_id,
    'dispatchState', value.dispatch_state,
    'createdAt', value.created_at
);

CREATE FUNCTION sklegal_activity.propose_export(
    p_proposal_id uuid,
    p_tenant_id uuid,
    p_matter_id uuid,
    p_title text,
    p_first_event_sequence bigint,
    p_last_event_sequence bigint,
    p_item_count integer,
    p_event_ids uuid[],
    p_event_sha256s sklegal_legal.sha256_digest[],
    p_selection_sha256 sklegal_legal.sha256_digest,
    p_content_sha256 sklegal_legal.sha256_digest,
    p_projected_sequence bigint,
    p_projected_event_sha256 sklegal_legal.sha256_digest,
    p_tenant_head_sequence bigint,
    p_tenant_head_sha256 sklegal_legal.sha256_digest,
    p_proposed_by_principal_id uuid,
    p_authorization_decision_id uuid,
    p_policy_decision_id uuid,
    p_policy_revision sklegal_legal.sha256_digest,
    p_idempotency_key_sha256 sklegal_legal.sha256_digest,
    p_request_sha256 sklegal_legal.sha256_digest,
    p_audit_event_id uuid,
    p_outbox_id uuid,
    p_created_at timestamptz
)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    prior sklegal_activity.projection_receipts%ROWTYPE;
    prior_found boolean;
    watermark sklegal_audit.projection_watermarks%ROWTYPE;
    chain_head sklegal_audit.chain_heads%ROWTYPE;
    expected_ids uuid[];
    expected_hashes sklegal_legal.sha256_digest[];
    computed_selection sklegal_legal.sha256_digest;
    computed_content sklegal_legal.sha256_digest;
    proposal sklegal_activity.export_proposals%ROWTYPE;
BEGIN
    PERFORM set_config('sklegal.audit_integrity_verification', '', true);
    IF NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_identity.record_is_authorized(p_tenant_id, p_matter_id)
       OR sklegal_identity.current_tenant_id() IS DISTINCT FROM p_tenant_id
       OR sklegal_identity.current_principal_id()
            IS DISTINCT FROM p_proposed_by_principal_id
       OR p_proposal_id IS NULL OR p_audit_event_id IS NULL
       OR p_outbox_id IS DISTINCT FROM p_audit_event_id
       OR p_authorization_decision_id IS NULL OR p_policy_decision_id IS NULL
       OR p_policy_revision IS NULL
       OR length(btrim(p_title)) NOT BETWEEN 1 AND 512
       OR p_first_event_sequence < 1
       OR p_last_event_sequence < p_first_event_sequence
       OR p_item_count NOT BETWEEN 1 AND 10000
       OR cardinality(p_event_ids) <> p_item_count
       OR cardinality(p_event_sha256s) <> p_item_count THEN
        RAISE EXCEPTION 'Matter activity export proposal is unauthorized'
            USING ERRCODE = '42501';
    END IF;

    PERFORM set_config(
        'sklegal.activity_replay_key_sha256',
        p_idempotency_key_sha256::text,
        true
    );
    SELECT * INTO prior
    FROM sklegal_activity.projection_receipts
    WHERE idempotency_key_sha256 = p_idempotency_key_sha256;
    prior_found := FOUND;
    PERFORM set_config('sklegal.activity_replay_key_sha256', '', true);
    IF prior_found THEN
        IF prior.tenant_id IS DISTINCT FROM p_tenant_id
           OR prior.matter_id IS DISTINCT FROM p_matter_id
           OR prior.operation IS DISTINCT FROM 'create_activity_export'
           OR prior.principal_id IS DISTINCT FROM p_proposed_by_principal_id
           OR prior.authorization_decision_id IS DISTINCT FROM
                p_authorization_decision_id
           OR prior.policy_decision_id IS DISTINCT FROM p_policy_decision_id
           OR prior.policy_revision IS DISTINCT FROM p_policy_revision
           OR prior.request_sha256 IS DISTINCT FROM p_request_sha256 THEN
            RETURN jsonb_build_object('conflict', true);
        END IF;
        SELECT * INTO STRICT proposal
        FROM sklegal_activity.export_proposals
        WHERE tenant_id = p_tenant_id
          AND matter_id = p_matter_id
          AND proposal_id = prior.result_id;
        RETURN jsonb_build_object(
            'conflict', false,
            'replayed', true,
            'proposal', sklegal_activity.export_proposal_json(proposal)
        );
    END IF;

    SELECT * INTO watermark
    FROM sklegal_audit.projection_watermarks
    WHERE tenant_id = p_tenant_id AND projection = 'matter_activity.v1';
    IF NOT FOUND
       OR watermark.event_sequence <> p_projected_sequence
       OR watermark.event_sha256 <> p_projected_event_sha256 THEN
        RAISE EXCEPTION 'Matter activity export watermark is stale'
            USING ERRCODE = '40001';
    END IF;
    SELECT * INTO chain_head
    FROM sklegal_audit.chain_heads
    WHERE tenant_id = p_tenant_id;
    IF NOT FOUND
       OR chain_head.last_event_sequence <> p_tenant_head_sequence
       OR chain_head.last_event_sha256 <> p_tenant_head_sha256
       OR p_tenant_head_sequence < p_projected_sequence THEN
        RAISE EXCEPTION 'Matter activity export chain head is stale'
            USING ERRCODE = '40001';
    END IF;

    SELECT array_agg(audit_event_id ORDER BY event_sequence),
           array_agg(event_sha256 ORDER BY event_sequence)
    INTO expected_ids, expected_hashes
    FROM sklegal_activity.entries
    WHERE tenant_id = p_tenant_id
      AND matter_id = p_matter_id
      AND event_sequence BETWEEN p_first_event_sequence AND p_last_event_sequence;
    IF expected_ids IS DISTINCT FROM p_event_ids
       OR expected_hashes IS DISTINCT FROM p_event_sha256s THEN
        RAISE EXCEPTION 'Matter activity export selection is unavailable'
            USING ERRCODE = '55000';
    END IF;

    computed_selection := sklegal_audit.payload_sha256(jsonb_build_object(
        'eventIds', p_event_ids,
        'eventSha256s', p_event_sha256s,
        'firstEventSequence', p_first_event_sequence,
        'lastEventSequence', p_last_event_sequence,
        'matterId', p_matter_id,
        'projectedEventSha256', p_projected_event_sha256,
        'projectedSequence', p_projected_sequence,
        'tenantId', p_tenant_id,
        'version', 'sklegal-matter-activity-selection/v1'
    ));
    computed_content := sklegal_audit.payload_sha256(jsonb_build_object(
        'itemCount', p_item_count,
        'selectionSha256', computed_selection,
        'title', p_title,
        'version', 'sklegal-matter-activity-export/v1'
    ));
    IF computed_selection <> p_selection_sha256
       OR computed_content <> p_content_sha256 THEN
        RAISE EXCEPTION 'Matter activity export hashes are invalid'
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO sklegal_activity.export_proposals (
        proposal_id, tenant_id, matter_id, title, first_event_sequence,
        last_event_sequence, item_count, event_ids, event_sha256s,
        selection_sha256, content_sha256, projected_sequence,
        projected_event_sha256, tenant_head_sequence, tenant_head_sha256,
        proposed_by_principal_id,
        authorization_decision_id, policy_decision_id,
        idempotency_key_sha256, request_sha256, created_at
    ) VALUES (
        p_proposal_id, p_tenant_id, p_matter_id, p_title,
        p_first_event_sequence, p_last_event_sequence, p_item_count,
        p_event_ids, p_event_sha256s, p_selection_sha256, p_content_sha256,
        p_projected_sequence, p_projected_event_sha256,
        p_tenant_head_sequence, p_tenant_head_sha256,
        p_proposed_by_principal_id, p_authorization_decision_id,
        p_policy_decision_id, p_idempotency_key_sha256, p_request_sha256,
        p_created_at
    ) RETURNING * INTO proposal;

    INSERT INTO sklegal_activity.projection_receipts (
        tenant_id, matter_id, operation, principal_id,
        authorization_decision_id, policy_decision_id, policy_revision,
        idempotency_key_sha256, request_sha256, result_id, result_sha256,
        recorded_at
    ) VALUES (
        p_tenant_id, p_matter_id, 'create_activity_export',
        p_proposed_by_principal_id, p_authorization_decision_id,
        p_policy_decision_id, p_policy_revision, p_idempotency_key_sha256,
        p_request_sha256, p_proposal_id, p_content_sha256, p_created_at
    );

    PERFORM sklegal_audit.append_event(
        p_audit_event_id, p_tenant_id, p_matter_id,
        p_proposed_by_principal_id, p_proposal_id, p_proposal_id,
        replace(p_audit_event_id::text, '-', ''),
        substr(replace(p_audit_event_id::text, '-', ''), 1, 16),
        '01', 'api', 'matter.activity_export.created',
        'work_product_proposal', p_proposal_id,
        p_authorization_decision_id, p_policy_decision_id,
        'success', 'proposed', p_created_at,
        jsonb_build_object(
            'operation', 'create_activity_export',
            'resource_identity', p_proposal_id::text,
            'resource_version', 1,
            'resource_sha256', p_content_sha256
        )
    );

    RETURN jsonb_build_object(
        'conflict', false,
        'replayed', false,
        'proposal', sklegal_activity.export_proposal_json(proposal)
    );
EXCEPTION
    WHEN unique_violation THEN
        PERFORM set_config('sklegal.activity_replay_key_sha256', '', true);
        RETURN jsonb_build_object('conflict', true);
END;
$function$;

CREATE INDEX activity_entries_page_idx
ON sklegal_activity.entries (tenant_id, matter_id, event_sequence, audit_event_id);

REVOKE ALL ON SCHEMA sklegal_activity FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA sklegal_activity FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA sklegal_activity FROM PUBLIC;

GRANT USAGE ON SCHEMA sklegal_activity TO sklegal_runtime;
GRANT SELECT ON sklegal_activity.entries,
    sklegal_activity.export_proposals TO sklegal_runtime;
GRANT EXECUTE ON FUNCTION sklegal_activity.runtime_ready() TO sklegal_runtime;
GRANT EXECUTE ON FUNCTION sklegal_activity.snapshot_is_valid(
    uuid, bigint, sklegal_legal.sha256_digest
) TO sklegal_runtime;
GRANT EXECUTE ON FUNCTION sklegal_activity.supersession_graph_is_valid(
    uuid, uuid
) TO sklegal_runtime;
GRANT EXECUTE ON FUNCTION sklegal_activity.project_event(
    uuid, uuid, uuid, bigint, sklegal_legal.sha256_digest, text, uuid,
    integer, sklegal_legal.sha256_digest, text, timestamptz, uuid, uuid,
    integer, uuid, uuid, uuid, uuid, sklegal_legal.sha256_digest,
    sklegal_legal.sha256_digest, uuid, timestamptz
) TO sklegal_runtime;
GRANT EXECUTE ON FUNCTION sklegal_activity.propose_export(
    uuid, uuid, uuid, text, bigint, bigint, integer, uuid[],
    sklegal_legal.sha256_digest[], sklegal_legal.sha256_digest,
    sklegal_legal.sha256_digest, bigint, sklegal_legal.sha256_digest,
    bigint, sklegal_legal.sha256_digest,
    uuid, uuid, uuid, sklegal_legal.sha256_digest,
    sklegal_legal.sha256_digest,
    sklegal_legal.sha256_digest, uuid, uuid, timestamptz
) TO sklegal_runtime;

-- sklegal:down
REVOKE EXECUTE ON FUNCTION sklegal_activity.propose_export(
    uuid, uuid, uuid, text, bigint, bigint, integer, uuid[],
    sklegal_legal.sha256_digest[], sklegal_legal.sha256_digest,
    sklegal_legal.sha256_digest, bigint, sklegal_legal.sha256_digest,
    bigint, sklegal_legal.sha256_digest,
    uuid, uuid, uuid, sklegal_legal.sha256_digest,
    sklegal_legal.sha256_digest,
    sklegal_legal.sha256_digest, uuid, uuid, timestamptz
) FROM sklegal_runtime;
REVOKE EXECUTE ON FUNCTION sklegal_activity.project_event(
    uuid, uuid, uuid, bigint, sklegal_legal.sha256_digest, text, uuid,
    integer, sklegal_legal.sha256_digest, text, timestamptz, uuid, uuid,
    integer, uuid, uuid, uuid, uuid, sklegal_legal.sha256_digest,
    sklegal_legal.sha256_digest, uuid, timestamptz
) FROM sklegal_runtime;
REVOKE EXECUTE ON FUNCTION sklegal_activity.supersession_graph_is_valid(
    uuid, uuid
) FROM sklegal_runtime;
REVOKE EXECUTE ON FUNCTION sklegal_activity.runtime_ready() FROM sklegal_runtime;
REVOKE EXECUTE ON FUNCTION sklegal_activity.snapshot_is_valid(
    uuid, bigint, sklegal_legal.sha256_digest
) FROM sklegal_runtime;
REVOKE SELECT ON sklegal_activity.entries,
    sklegal_activity.export_proposals FROM sklegal_runtime;
REVOKE USAGE ON SCHEMA sklegal_activity FROM sklegal_runtime;
DROP INDEX sklegal_activity.activity_entries_page_idx;
DROP FUNCTION sklegal_activity.propose_export(
    uuid, uuid, uuid, text, bigint, bigint, integer, uuid[],
    sklegal_legal.sha256_digest[], sklegal_legal.sha256_digest,
    sklegal_legal.sha256_digest, bigint, sklegal_legal.sha256_digest,
    bigint, sklegal_legal.sha256_digest,
    uuid, uuid, uuid, sklegal_legal.sha256_digest,
    sklegal_legal.sha256_digest,
    sklegal_legal.sha256_digest, uuid, uuid, timestamptz
);
DROP FUNCTION sklegal_activity.export_proposal_json(
    sklegal_activity.export_proposals
);
DROP FUNCTION sklegal_activity.project_event(
    uuid, uuid, uuid, bigint, sklegal_legal.sha256_digest, text, uuid,
    integer, sklegal_legal.sha256_digest, text, timestamptz, uuid, uuid,
    integer, uuid, uuid, uuid, uuid, sklegal_legal.sha256_digest,
    sklegal_legal.sha256_digest, uuid, timestamptz
);
DROP FUNCTION sklegal_activity.supersession_graph_is_valid(uuid, uuid);
DROP FUNCTION sklegal_activity.snapshot_is_valid(
    uuid, bigint, sklegal_legal.sha256_digest
);
DROP FUNCTION sklegal_activity.runtime_ready();
DROP TABLE sklegal_activity.export_proposals;
DROP TABLE sklegal_activity.projection_receipts;
DROP TABLE sklegal_activity.entries;
DROP FUNCTION sklegal_activity.reject_change();
DROP SCHEMA sklegal_activity;
