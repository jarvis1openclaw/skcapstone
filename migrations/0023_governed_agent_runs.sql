-- sklegal:up
CREATE TABLE sklegal_workflow.agent_run_identities (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    run_id uuid NOT NULL,
    request_id uuid NOT NULL,
    requested_by_principal_id uuid NOT NULL,
    retry_of_run_id uuid,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, run_id),
    UNIQUE (tenant_id, matter_id, request_id),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, requested_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    FOREIGN KEY (tenant_id, matter_id, retry_of_run_id)
        REFERENCES sklegal_workflow.agent_run_identities(tenant_id, matter_id, run_id)
);

CREATE TABLE sklegal_workflow.agent_runs (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    run_id uuid NOT NULL,
    version sklegal_legal.record_version NOT NULL,
    schema_version text NOT NULL CHECK (schema_version = 'sklegal.agent-run/v1'),
    status text NOT NULL
        CHECK (status IN ('completed', 'failed', 'cancelled', 'timed_out')),
    classification sklegal_legal.data_classification NOT NULL
        DEFAULT 'public' CHECK (classification = 'public'),
    public_synthetic boolean NOT NULL DEFAULT true CHECK (public_synthetic),
    purpose text NOT NULL CHECK (length(btrim(purpose)) > 0),
    capability text NOT NULL CHECK (length(btrim(capability)) > 0),
    capability_decision_id uuid NOT NULL,
    verifier_policy_version text NOT NULL
        CHECK (length(btrim(verifier_policy_version)) > 0),
    revocation_revision text NOT NULL CHECK (length(btrim(revocation_revision)) > 0),
    credential_digest sklegal_legal.sha256_digest NOT NULL,
    agent_spec_id text NOT NULL CHECK (length(btrim(agent_spec_id)) > 0),
    agent_spec_version sklegal_legal.record_version NOT NULL,
    agent_spec_sha256 sklegal_legal.sha256_digest NOT NULL,
    deployment_revision text NOT NULL CHECK (length(btrim(deployment_revision)) > 0),
    deployment_sha256 sklegal_legal.sha256_digest NOT NULL,
    logical_route_id text NOT NULL CHECK (length(btrim(logical_route_id)) > 0),
    prompt_template_sha256 sklegal_legal.sha256_digest NOT NULL,
    output_schema_sha256 sklegal_legal.sha256_digest NOT NULL,
    scoring_policy_sha256 sklegal_legal.sha256_digest NOT NULL,
    matter_snapshot_sha256 sklegal_legal.sha256_digest NOT NULL,
    corpus_snapshot_sha256 sklegal_legal.sha256_digest NOT NULL,
    authority_snapshot_sha256 sklegal_legal.sha256_digest NOT NULL,
    policy_snapshot_sha256 sklegal_legal.sha256_digest NOT NULL,
    proposal_payload_sha256 sklegal_legal.sha256_digest,
    record_sha256 sklegal_legal.sha256_digest NOT NULL,
    record jsonb NOT NULL CHECK (jsonb_typeof(record) = 'object'),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    system_from timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, run_id, version),
    FOREIGN KEY (tenant_id, matter_id, run_id)
        REFERENCES sklegal_workflow.agent_run_identities(tenant_id, matter_id, run_id),
    CHECK (updated_at >= created_at),
    CHECK ((status = 'completed') = (proposal_payload_sha256 IS NOT NULL))
);

CREATE TABLE sklegal_workflow.agent_run_attempts (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    run_id uuid NOT NULL,
    run_version sklegal_legal.record_version NOT NULL,
    attempt_id uuid NOT NULL,
    attempt_number integer NOT NULL CHECK (attempt_number BETWEEN 1 AND 9),
    outcome text NOT NULL
        CHECK (outcome IN ('completed', 'failed', 'cancelled', 'timed_out')),
    error_code text,
    retryable boolean NOT NULL,
    started_at timestamptz NOT NULL,
    completed_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, run_id, run_version, attempt_id),
    UNIQUE (tenant_id, matter_id, run_id, run_version, attempt_number),
    FOREIGN KEY (tenant_id, matter_id, run_id, run_version)
        REFERENCES sklegal_workflow.agent_runs(tenant_id, matter_id, run_id, version),
    CHECK (completed_at >= started_at),
    CHECK ((outcome = 'completed') = (error_code IS NULL))
);

CREATE TABLE sklegal_workflow.agent_tool_calls (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    run_id uuid NOT NULL,
    run_version sklegal_legal.record_version NOT NULL,
    tool_call_id uuid NOT NULL,
    sequence integer NOT NULL CHECK (sequence >= 1),
    tool_id text NOT NULL CHECK (length(btrim(tool_id)) > 0),
    capability_decision_id uuid NOT NULL,
    arguments_sha256 sklegal_legal.sha256_digest NOT NULL,
    result_sha256 sklegal_legal.sha256_digest NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('completed', 'denied', 'failed')),
    error_code text,
    started_at timestamptz NOT NULL,
    completed_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, run_id, run_version, tool_call_id),
    UNIQUE (tenant_id, matter_id, run_id, run_version, sequence),
    FOREIGN KEY (tenant_id, matter_id, run_id, run_version)
        REFERENCES sklegal_workflow.agent_runs(tenant_id, matter_id, run_id, version),
    CHECK (completed_at >= started_at),
    CHECK ((outcome = 'completed') = (error_code IS NULL))
);

CREATE TABLE sklegal_workflow.agent_recommendations (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    run_id uuid NOT NULL,
    run_version sklegal_legal.record_version NOT NULL,
    recommendation_id uuid NOT NULL,
    recommendation_version sklegal_legal.record_version NOT NULL,
    target_kind text NOT NULL
        CHECK (target_kind IN ('issue', 'claim', 'defense', 'element', 'proceeding')),
    target_id uuid NOT NULL,
    proposed_output text NOT NULL CHECK (proposed_output IN ('task', 'work_product')),
    score_total integer NOT NULL CHECK (score_total BETWEEN 0 AND 100),
    confidence_basis_points integer NOT NULL
        CHECK (confidence_basis_points BETWEEN 0 AND 10000),
    review_state text NOT NULL CHECK (review_state IN ('pending', 'challenged', 'disposed')),
    recommendation_sha256 sklegal_legal.sha256_digest NOT NULL,
    recommendation jsonb NOT NULL CHECK (jsonb_typeof(recommendation) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (
        tenant_id,
        matter_id,
        run_id,
        run_version,
        recommendation_id,
        recommendation_version
    ),
    FOREIGN KEY (tenant_id, matter_id, run_id, run_version)
        REFERENCES sklegal_workflow.agent_runs(tenant_id, matter_id, run_id, version)
);

CREATE TABLE sklegal_workflow.agent_blind_challenges (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    run_id uuid NOT NULL,
    run_version sklegal_legal.record_version NOT NULL,
    challenge_id uuid NOT NULL,
    challenge_version sklegal_legal.record_version NOT NULL,
    recommendation_id uuid NOT NULL,
    recommendation_version sklegal_legal.record_version NOT NULL,
    challenger_spec_sha256 sklegal_legal.sha256_digest NOT NULL,
    challenger_route_sha256 sklegal_legal.sha256_digest NOT NULL,
    blind_input_sha256 sklegal_legal.sha256_digest NOT NULL,
    independent_output_sha256 sklegal_legal.sha256_digest NOT NULL,
    initiated_by_principal_id uuid NOT NULL,
    capability_decision_id uuid NOT NULL,
    verifier_policy_version text NOT NULL
        CHECK (length(btrim(verifier_policy_version)) > 0),
    revocation_revision text NOT NULL CHECK (length(btrim(revocation_revision)) > 0),
    credential_digest sklegal_legal.sha256_digest NOT NULL,
    saw_challenged_conclusion boolean NOT NULL
        CHECK (NOT saw_challenged_conclusion),
    outcome text NOT NULL CHECK (outcome IN ('no_defect', 'defect_found')),
    challenge_sha256 sklegal_legal.sha256_digest NOT NULL,
    challenge jsonb NOT NULL CHECK (jsonb_typeof(challenge) = 'object'),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (
        tenant_id,
        matter_id,
        run_id,
        run_version,
        challenge_id,
        challenge_version
    ),
    FOREIGN KEY (tenant_id, matter_id, run_id, run_version)
        REFERENCES sklegal_workflow.agent_runs(tenant_id, matter_id, run_id, version),
    FOREIGN KEY (tenant_id, initiated_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id)
);

CREATE TABLE sklegal_workflow.agent_human_dispositions (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    run_id uuid NOT NULL,
    run_version sklegal_legal.record_version NOT NULL,
    disposition_id uuid NOT NULL,
    disposition_version sklegal_legal.record_version NOT NULL,
    recommendation_id uuid NOT NULL,
    recommendation_version sklegal_legal.record_version NOT NULL,
    reviewer_principal_id uuid NOT NULL,
    capability_decision_id uuid NOT NULL,
    verifier_policy_version text NOT NULL
        CHECK (length(btrim(verifier_policy_version)) > 0),
    revocation_revision text NOT NULL CHECK (length(btrim(revocation_revision)) > 0),
    credential_digest sklegal_legal.sha256_digest NOT NULL,
    decision text NOT NULL CHECK (decision IN (
        'accept_as_proposed_task',
        'request_work_product_proposal',
        'reject',
        'changes_requested'
    )),
    creates_domain_record boolean NOT NULL CHECK (NOT creates_domain_record),
    external_effect boolean NOT NULL CHECK (NOT external_effect),
    policy_revision text NOT NULL CHECK (length(btrim(policy_revision)) > 0),
    disposition_sha256 sklegal_legal.sha256_digest NOT NULL,
    disposition jsonb NOT NULL CHECK (jsonb_typeof(disposition) = 'object'),
    decided_at timestamptz NOT NULL,
    PRIMARY KEY (
        tenant_id,
        matter_id,
        run_id,
        run_version,
        disposition_id,
        disposition_version
    ),
    FOREIGN KEY (tenant_id, matter_id, run_id, run_version)
        REFERENCES sklegal_workflow.agent_runs(tenant_id, matter_id, run_id, version),
    FOREIGN KEY (tenant_id, reviewer_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id)
);

CREATE TABLE sklegal_workflow.agent_run_idempotency (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    request_sha256 sklegal_legal.sha256_digest NOT NULL,
    run_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, idempotency_key),
    FOREIGN KEY (tenant_id, matter_id, run_id)
        REFERENCES sklegal_workflow.agent_run_identities(tenant_id, matter_id, run_id)
);

CREATE TABLE sklegal_workflow.agent_run_audit_events (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    event_id uuid NOT NULL,
    run_id uuid NOT NULL,
    run_version sklegal_legal.record_version NOT NULL,
    correlation_id uuid NOT NULL,
    actor_principal_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN (
        'agent_run.recorded',
        'agent_run.challenge_recorded',
        'agent_run.disposition_recorded'
    )),
    outcome text NOT NULL CHECK (outcome IN ('completed', 'failed', 'denied')),
    subject_sha256 sklegal_legal.sha256_digest NOT NULL,
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, event_id),
    FOREIGN KEY (tenant_id, matter_id, run_id, run_version)
        REFERENCES sklegal_workflow.agent_runs(tenant_id, matter_id, run_id, version),
    FOREIGN KEY (tenant_id, actor_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id)
);

DO $agent_run_boundaries$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_workflow.agent_run_identities'::regclass,
        'sklegal_workflow.agent_runs'::regclass,
        'sklegal_workflow.agent_run_attempts'::regclass,
        'sklegal_workflow.agent_tool_calls'::regclass,
        'sklegal_workflow.agent_recommendations'::regclass,
        'sklegal_workflow.agent_blind_challenges'::regclass,
        'sklegal_workflow.agent_human_dispositions'::regclass,
        'sklegal_workflow.agent_run_idempotency'::regclass,
        'sklegal_workflow.agent_run_audit_events'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER domain_id_non_nil BEFORE INSERT OR UPDATE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_nil_domain_ids()',
            target_table
        );
        EXECUTE format(
            'CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change()',
            target_table
        );
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', target_table);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', target_table);
        EXECUTE format(
            'CREATE POLICY matter_select ON %s FOR SELECT '
            'USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))',
            target_table
        );
        EXECUTE format(
            'CREATE POLICY matter_insert ON %s FOR INSERT '
            'WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id))',
            target_table
        );
    END LOOP;
END;
$agent_run_boundaries$;

CREATE VIEW sklegal_workflow.agent_run_history
WITH (security_invoker = true, security_barrier = true)
AS
SELECT run.*,
       lead(system_from) OVER (
           PARTITION BY tenant_id, matter_id, run_id ORDER BY version
       ) AS system_to
FROM sklegal_workflow.agent_runs AS run;

CREATE VIEW sklegal_workflow.agent_run_current
WITH (security_invoker = true, security_barrier = true)
AS
SELECT * FROM sklegal_workflow.agent_run_history WHERE system_to IS NULL;

CREATE INDEX agent_run_matter_time_idx
ON sklegal_workflow.agent_runs (tenant_id, matter_id, updated_at DESC);
CREATE INDEX agent_run_recommendation_idx
ON sklegal_workflow.agent_recommendations (
    tenant_id,
    matter_id,
    recommendation_id,
    recommendation_version
);
CREATE INDEX agent_run_challenge_recommendation_idx
ON sklegal_workflow.agent_blind_challenges (
    tenant_id,
    matter_id,
    recommendation_id,
    recommendation_version
);
CREATE INDEX agent_run_disposition_recommendation_idx
ON sklegal_workflow.agent_human_dispositions (
    tenant_id,
    matter_id,
    recommendation_id,
    recommendation_version
);

-- sklegal:down
DROP INDEX sklegal_workflow.agent_run_disposition_recommendation_idx;
DROP INDEX sklegal_workflow.agent_run_challenge_recommendation_idx;
DROP INDEX sklegal_workflow.agent_run_recommendation_idx;
DROP INDEX sklegal_workflow.agent_run_matter_time_idx;
DROP VIEW sklegal_workflow.agent_run_current;
DROP VIEW sklegal_workflow.agent_run_history;
DROP TABLE sklegal_workflow.agent_run_audit_events;
DROP TABLE sklegal_workflow.agent_run_idempotency;
DROP TABLE sklegal_workflow.agent_human_dispositions;
DROP TABLE sklegal_workflow.agent_blind_challenges;
DROP TABLE sklegal_workflow.agent_recommendations;
DROP TABLE sklegal_workflow.agent_tool_calls;
DROP TABLE sklegal_workflow.agent_run_attempts;
DROP TABLE sklegal_workflow.agent_runs;
DROP TABLE sklegal_workflow.agent_run_identities;
