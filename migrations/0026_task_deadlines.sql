-- sklegal:up
CREATE SCHEMA sklegal_task_deadline;

CREATE TABLE sklegal_task_deadline.task_versions (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    task_id uuid NOT NULL,
    version bigint NOT NULL CHECK (version >= 1),
    state text NOT NULL CHECK (state IN (
        'draft', 'ready', 'in_progress', 'blocked', 'completed', 'cancelled',
        'failed', 'retry_pending', 'reconciliation_required', 'reconciled'
    )),
    record jsonb NOT NULL CHECK (jsonb_typeof(record) = 'object'),
    record_sha256 sklegal_legal.sha256_digest NOT NULL,
    policy_decision_id uuid NOT NULL,
    policy_revision sklegal_legal.sha256_digest NOT NULL,
    actor_principal_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, task_id, version),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, actor_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id)
);

CREATE TABLE sklegal_task_deadline.deadline_versions (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    deadline_id uuid NOT NULL,
    version bigint NOT NULL CHECK (version >= 1),
    state text NOT NULL CHECK (state IN (
        'blocked', 'uncertain', 'calculated', 'reviewed', 'operative',
        'cancelled', 'failed', 'retry_pending', 'reconciliation_required',
        'reconciled', 'superseded'
    )),
    review_state text NOT NULL CHECK (review_state IN ('pending', 'accepted', 'rejected')),
    candidate_due_at timestamptz,
    operative_due_at timestamptz,
    calculation_sha256 sklegal_legal.sha256_digest NOT NULL,
    trigger_evidence_sha256 sklegal_legal.sha256_digest NOT NULL,
    rule_authority_sha256 sklegal_legal.sha256_digest NOT NULL,
    holiday_calendar_sha256 sklegal_legal.sha256_digest NOT NULL,
    record jsonb NOT NULL CHECK (jsonb_typeof(record) = 'object'),
    record_sha256 sklegal_legal.sha256_digest NOT NULL,
    policy_decision_id uuid NOT NULL,
    policy_revision sklegal_legal.sha256_digest NOT NULL,
    actor_principal_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, deadline_id, version),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, actor_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (
        (state = 'operative' AND review_state = 'accepted'
         AND candidate_due_at IS NOT NULL AND operative_due_at = candidate_due_at)
        OR (state <> 'operative' AND operative_due_at IS NULL)
    ),
    CHECK (
        (state IN ('blocked', 'uncertain') AND candidate_due_at IS NULL)
        OR (state NOT IN ('blocked', 'uncertain') AND candidate_due_at IS NOT NULL)
    )
);

CREATE TABLE sklegal_task_deadline.simulation_receipts (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    receipt_id uuid NOT NULL,
    task_id uuid NOT NULL,
    deadline_id uuid,
    work_product_id uuid NOT NULL,
    work_product_version_id uuid NOT NULL,
    work_product_version_number bigint NOT NULL
        CHECK (work_product_version_number >= 1),
    work_product_content_sha256 sklegal_legal.sha256_digest NOT NULL,
    approval_id uuid NOT NULL,
    approval_snapshot_sha256 sklegal_legal.sha256_digest NOT NULL,
    destination_sha256 sklegal_legal.sha256_digest NOT NULL,
    state text NOT NULL CHECK (state = 'simulated'),
    external_effect boolean NOT NULL CHECK (NOT external_effect),
    connector_invoked boolean NOT NULL CHECK (NOT connector_invoked),
    dispatch_attempted boolean NOT NULL CHECK (NOT dispatch_attempted),
    record jsonb NOT NULL CHECK (jsonb_typeof(record) = 'object'),
    record_sha256 sklegal_legal.sha256_digest NOT NULL,
    policy_decision_id uuid NOT NULL,
    policy_revision sklegal_legal.sha256_digest NOT NULL,
    actor_principal_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, receipt_id),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, actor_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id)
);

CREATE TABLE sklegal_task_deadline.idempotency_receipts (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    operation text NOT NULL CHECK (length(btrim(operation)) BETWEEN 1 AND 255),
    idempotency_key_sha256 sklegal_legal.sha256_digest NOT NULL,
    request_sha256 sklegal_legal.sha256_digest NOT NULL,
    resource_kind text NOT NULL CHECK (resource_kind IN ('task', 'deadline', 'action_simulation')),
    resource_id uuid NOT NULL,
    resource_version bigint NOT NULL CHECK (resource_version >= 1),
    response_record jsonb NOT NULL CHECK (jsonb_typeof(response_record) = 'object'),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, operation, idempotency_key_sha256),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id)
);

CREATE TABLE sklegal_task_deadline.audit_events (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    audit_id uuid NOT NULL,
    resource_kind text NOT NULL CHECK (resource_kind IN ('task', 'deadline', 'action_simulation')),
    resource_id uuid NOT NULL,
    resource_version bigint NOT NULL CHECK (resource_version >= 1),
    action text NOT NULL CHECK (length(btrim(action)) BETWEEN 1 AND 255),
    outcome text NOT NULL CHECK (outcome IN (
        'succeeded', 'cancelled', 'failed', 'retry_pending',
        'reconciliation_required', 'reconciled'
    )),
    actor_principal_id uuid NOT NULL,
    policy_decision_id uuid NOT NULL,
    policy_revision sklegal_legal.sha256_digest NOT NULL,
    correlation_id uuid NOT NULL,
    request_sha256 sklegal_legal.sha256_digest NOT NULL,
    resource_sha256 sklegal_legal.sha256_digest NOT NULL,
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, audit_id),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, actor_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id)
);

CREATE TABLE sklegal_task_deadline.outbox (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    outbox_id uuid NOT NULL,
    audit_id uuid NOT NULL,
    resource_kind text NOT NULL CHECK (resource_kind IN ('task', 'deadline', 'action_simulation')),
    resource_id uuid NOT NULL,
    topic text NOT NULL CHECK (topic IN (
        'task.projection', 'deadline.projection', 'external_action.simulation'
    )),
    payload_sha256 sklegal_legal.sha256_digest NOT NULL,
    dispatch_allowed boolean NOT NULL CHECK (NOT dispatch_allowed),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, outbox_id),
    FOREIGN KEY (tenant_id, matter_id, audit_id)
        REFERENCES sklegal_task_deadline.audit_events(tenant_id, matter_id, audit_id)
);

CREATE TRIGGER task_versions_append_only
BEFORE UPDATE OR DELETE ON sklegal_task_deadline.task_versions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER deadline_versions_append_only
BEFORE UPDATE OR DELETE ON sklegal_task_deadline.deadline_versions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER simulation_receipts_append_only
BEFORE UPDATE OR DELETE ON sklegal_task_deadline.simulation_receipts
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER idempotency_receipts_append_only
BEFORE UPDATE OR DELETE ON sklegal_task_deadline.idempotency_receipts
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER audit_events_append_only
BEFORE UPDATE OR DELETE ON sklegal_task_deadline.audit_events
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER outbox_append_only
BEFORE UPDATE OR DELETE ON sklegal_task_deadline.outbox
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

ALTER TABLE sklegal_task_deadline.task_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_task_deadline.task_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_task_deadline.deadline_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_task_deadline.deadline_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_task_deadline.simulation_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_task_deadline.simulation_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_task_deadline.idempotency_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_task_deadline.idempotency_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_task_deadline.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_task_deadline.audit_events FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_task_deadline.outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_task_deadline.outbox FORCE ROW LEVEL SECURITY;

CREATE POLICY task_versions_scope ON sklegal_task_deadline.task_versions
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY deadline_versions_scope ON sklegal_task_deadline.deadline_versions
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY simulation_receipts_scope ON sklegal_task_deadline.simulation_receipts
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY idempotency_receipts_scope ON sklegal_task_deadline.idempotency_receipts
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY audit_events_scope ON sklegal_task_deadline.audit_events
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY outbox_scope ON sklegal_task_deadline.outbox
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));

CREATE INDEX task_current_idx
ON sklegal_task_deadline.task_versions (tenant_id, matter_id, task_id, version DESC);
CREATE INDEX deadline_current_idx
ON sklegal_task_deadline.deadline_versions (tenant_id, matter_id, deadline_id, version DESC);
CREATE INDEX deadline_due_idx
ON sklegal_task_deadline.deadline_versions (tenant_id, matter_id, operative_due_at)
WHERE operative_due_at IS NOT NULL;
CREATE INDEX outbox_activity_idx
ON sklegal_task_deadline.outbox (tenant_id, matter_id, created_at);

REVOKE ALL ON SCHEMA sklegal_task_deadline FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA sklegal_task_deadline FROM PUBLIC;
GRANT USAGE ON SCHEMA sklegal_task_deadline TO sklegal_runtime;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA sklegal_task_deadline
TO sklegal_runtime;

-- sklegal:down
REVOKE SELECT, INSERT ON ALL TABLES IN SCHEMA sklegal_task_deadline
FROM sklegal_runtime;
REVOKE USAGE ON SCHEMA sklegal_task_deadline FROM sklegal_runtime;
DROP INDEX sklegal_task_deadline.outbox_activity_idx;
DROP INDEX sklegal_task_deadline.deadline_due_idx;
DROP INDEX sklegal_task_deadline.deadline_current_idx;
DROP INDEX sklegal_task_deadline.task_current_idx;
DROP TABLE sklegal_task_deadline.outbox;
DROP TABLE sklegal_task_deadline.audit_events;
DROP TABLE sklegal_task_deadline.idempotency_receipts;
DROP TABLE sklegal_task_deadline.simulation_receipts;
DROP TABLE sklegal_task_deadline.deadline_versions;
DROP TABLE sklegal_task_deadline.task_versions;
DROP SCHEMA sklegal_task_deadline;
