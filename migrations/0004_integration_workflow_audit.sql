-- sklegal:up
CREATE TABLE sklegal_integrations.connections (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL REFERENCES sklegal_identity.tenants(id),
    connector_kind text NOT NULL CHECK (connector_kind IN ('hammertime', 'courtlistener', 'email', 'filing', 'service', 'mailing', 'calendar', 'other')),
    display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 512),
    secret_reference text CHECK (secret_reference IS NULL OR secret_reference ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{2,255}$'),
    encrypted_configuration bytea,
    encryption_key_ref text,
    encryption_algorithm text,
    encrypted_at timestamptz,
    simulation_only boolean NOT NULL DEFAULT true,
    status text NOT NULL DEFAULT 'disabled' CHECK (status IN ('disabled', 'simulation', 'active', 'suspended')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, id),
    CHECK (sklegal_identity.encrypted_payload_is_complete(
        encrypted_configuration, encryption_key_ref, encryption_algorithm, encrypted_at
    )),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_integrations.external_references (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    connection_id uuid NOT NULL,
    resource_kind text NOT NULL CHECK (length(btrim(resource_kind)) BETWEEN 1 AND 100),
    external_id text NOT NULL CHECK (length(btrim(external_id)) BETWEEN 1 AND 512),
    source_sha256 sklegal_legal.sha256_digest,
    observed_at timestamptz NOT NULL,
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, connection_id, resource_kind, external_id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, connection_id) REFERENCES sklegal_integrations.connections(tenant_id, id),
    CHECK (observed_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_integrations.corpus_release_references (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    source_system text NOT NULL CHECK (source_system = 'hammertime'),
    release_id text NOT NULL CHECK (length(btrim(release_id)) BETWEEN 1 AND 255),
    manifest_sha256 sklegal_legal.sha256_digest NOT NULL,
    source_path_alias text NOT NULL CHECK (
        source_path_alias !~ '^/' AND source_path_alias !~ '\\'
        AND source_path_alias !~ '(^|/)\.\.?(/|$)'
    ),
    observed_at timestamptz NOT NULL,
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, source_system, release_id, manifest_sha256),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    CHECK (observed_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_workflow.workflow_references (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    workflow_id text NOT NULL CHECK (length(workflow_id) BETWEEN 1 AND 255),
    run_id text NOT NULL CHECK (length(run_id) BETWEEN 1 AND 255),
    workflow_type text NOT NULL CHECK (length(workflow_type) BETWEEN 1 AND 255),
    task_queue text NOT NULL CHECK (length(task_queue) BETWEEN 1 AND 255),
    input_sha256 sklegal_legal.sha256_digest NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'running', 'waiting', 'completed', 'failed', 'cancelled')),
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, workflow_id, run_id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_workflow.policy_decision_references (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    workflow_reference_id uuid,
    policy_decision_id text NOT NULL CHECK (length(policy_decision_id) BETWEEN 8 AND 255),
    purpose text NOT NULL CHECK (length(btrim(purpose)) BETWEEN 1 AND 255),
    effect text NOT NULL CHECK (effect IN ('allow', 'deny')),
    decided_at timestamptz NOT NULL,
    classification sklegal_legal.data_classification NOT NULL DEFAULT 'confidential',
    completeness sklegal_legal.record_completeness NOT NULL DEFAULT 'incomplete',
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, policy_decision_id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, matter_id, workflow_reference_id) REFERENCES sklegal_workflow.workflow_references(tenant_id, matter_id, id),
    CHECK (decided_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_audit.events (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL REFERENCES sklegal_identity.tenants(id),
    matter_id uuid,
    principal_id uuid NOT NULL,
    action text NOT NULL CHECK (length(btrim(action)) BETWEEN 1 AND 255),
    resource_kind text NOT NULL CHECK (length(btrim(resource_kind)) BETWEEN 1 AND 100),
    resource_id uuid,
    policy_decision_id text,
    correlation_id text NOT NULL CHECK (length(correlation_id) BETWEEN 8 AND 255),
    event_sha256 sklegal_legal.sha256_digest NOT NULL,
    previous_event_sha256 sklegal_legal.sha256_digest,
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, event_sha256),
    FOREIGN KEY (tenant_id, principal_id) REFERENCES sklegal_identity.principals(tenant_id, id),
    FOREIGN KEY (tenant_id, matter_id) REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    CHECK (occurred_at <= recorded_at)
);

CREATE FUNCTION sklegal_audit.reject_insert_until_chain_writer()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    RAISE EXCEPTION 'audit insertion is unavailable until the SKL-S1-05 chain writer is installed'
        USING ERRCODE = '55000';
END;
$function$;

CREATE TRIGGER audit_writer_not_installed
BEFORE INSERT ON sklegal_audit.events
FOR EACH ROW EXECUTE FUNCTION sklegal_audit.reject_insert_until_chain_writer();

CREATE TRIGGER audit_events_append_only
BEFORE UPDATE OR DELETE ON sklegal_audit.events
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

DO $mutable_triggers$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_integrations.connections'::regclass,
        'sklegal_workflow.workflow_references'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER initialize_record BEFORE INSERT ON %s '
            'FOR EACH ROW EXECUTE FUNCTION sklegal_legal.initialize_record_audit()', target_table
        );
        EXECUTE format(
            'CREATE TRIGGER optimistic_record_update BEFORE UPDATE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION sklegal_legal.enforce_optimistic_record_update()', target_table
        );
    END LOOP;
END;
$mutable_triggers$;

DO $immutable_triggers$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_integrations.external_references'::regclass,
        'sklegal_integrations.corpus_release_references'::regclass,
        'sklegal_workflow.policy_decision_references'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER initialize_record BEFORE INSERT ON %s '
            'FOR EACH ROW EXECUTE FUNCTION sklegal_legal.initialize_record_audit()', target_table
        );
        EXECUTE format(
            'CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change()', target_table
        );
    END LOOP;
END;
$immutable_triggers$;

DO $domain_id_triggers$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_integrations.connections'::regclass,
        'sklegal_integrations.external_references'::regclass,
        'sklegal_integrations.corpus_release_references'::regclass,
        'sklegal_workflow.workflow_references'::regclass,
        'sklegal_workflow.policy_decision_references'::regclass,
        'sklegal_audit.events'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER domain_id_non_nil BEFORE INSERT OR UPDATE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_nil_domain_ids()',
            target_table
        );
    END LOOP;
END;
$domain_id_triggers$;

-- sklegal:down
DROP TABLE sklegal_audit.events;
DROP FUNCTION sklegal_audit.reject_insert_until_chain_writer();
DROP TABLE sklegal_workflow.policy_decision_references;
DROP TABLE sklegal_workflow.workflow_references;
DROP TABLE sklegal_integrations.corpus_release_references;
DROP TABLE sklegal_integrations.external_references;
DROP TABLE sklegal_integrations.connections;
