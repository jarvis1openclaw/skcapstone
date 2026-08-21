-- sklegal:up
CREATE FUNCTION sklegal_identity.runtime_role_is_safe()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
RETURN COALESCE(
    EXISTS (
        SELECT 1
        FROM pg_roles AS role_record
        WHERE role_record.rolname = session_user
          AND role_record.rolcanlogin
          AND NOT role_record.rolsuper
          AND NOT role_record.rolbypassrls
          AND NOT role_record.rolcreaterole
          AND NOT role_record.rolcreatedb
          AND NOT role_record.rolreplication
          AND NOT role_record.rolinherit
    )
    AND sklegal_identity.current_principal_id() IS NOT NULL
    AND sklegal_identity.has_tenant_membership(sklegal_identity.current_tenant_id())
    AND NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members AS membership
        JOIN pg_catalog.pg_roles AS role_record
          ON role_record.oid IN (membership.member, membership.roleid)
        WHERE role_record.rolname = session_user
    ),
    false
);

CREATE FUNCTION sklegal_identity.record_is_authorized(target_tenant_id uuid, target_matter_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
RETURN COALESCE(
    sklegal_identity.runtime_role_is_safe()
    AND CASE
        WHEN target_matter_id IS NULL THEN sklegal_identity.has_tenant_membership(target_tenant_id)
        ELSE sklegal_legal.has_matter_membership(target_tenant_id, target_matter_id)
    END,
    false
);

ALTER TABLE sklegal_identity.database_role_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_identity.database_role_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY binding_self_select ON sklegal_identity.database_role_bindings
FOR SELECT USING (database_role = session_user AND active);
CREATE POLICY binding_status_controlled_read
ON sklegal_identity.database_role_bindings
FOR SELECT USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
);
CREATE POLICY binding_status_sync_update
ON sklegal_identity.database_role_bindings
FOR UPDATE
USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
)
WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
);

ALTER TABLE sklegal_identity.tenant_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_identity.tenant_memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_membership_self_select ON sklegal_identity.tenant_memberships
FOR SELECT USING (
    tenant_id = sklegal_identity.current_tenant_id()
    AND principal_id = sklegal_identity.current_principal_id()
    AND active
);

ALTER TABLE sklegal_legal.matter_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.matter_memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY matter_membership_self_select ON sklegal_legal.matter_memberships
FOR SELECT USING (
    tenant_id = sklegal_identity.current_tenant_id()
    AND principal_id = sklegal_identity.current_principal_id()
    AND active
);

ALTER TABLE sklegal_identity.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_identity.tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON sklegal_identity.tenants
FOR ALL
USING (sklegal_identity.record_is_authorized(id, NULL))
WITH CHECK (sklegal_identity.record_is_authorized(id, NULL));
CREATE POLICY tenant_status_controlled_read ON sklegal_identity.tenants
FOR SELECT USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
);

ALTER TABLE sklegal_legal.matters ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.matters FORCE ROW LEVEL SECURITY;
CREATE POLICY matter_boundary ON sklegal_legal.matters
FOR ALL
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));

DO $tenant_policies$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_identity.principals'::regclass,
        'sklegal_legal.clients'::regclass,
        'sklegal_legal.engagements'::regclass,
        'sklegal_integrations.connections'::regclass
    ]
    LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', target_table);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', target_table);
        EXECUTE format(
            'CREATE POLICY tenant_boundary ON %s FOR ALL '
            'USING (sklegal_identity.record_is_authorized(tenant_id, NULL)) '
            'WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, NULL))', target_table
        );
    END LOOP;
END;
$tenant_policies$;

CREATE POLICY principal_status_controlled_read ON sklegal_identity.principals
FOR SELECT USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
);

DO $mutable_matter_policies$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
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
        'sklegal_integrations.connections'::regclass,
        'sklegal_workflow.workflow_references'::regclass
    ]
    LOOP
        IF target_table::text <> 'sklegal_integrations.connections' THEN
            EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', target_table);
            EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', target_table);
            EXECUTE format(
                'CREATE POLICY matter_boundary ON %s FOR ALL '
                'USING (sklegal_identity.record_is_authorized(tenant_id, matter_id)) '
                'WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id))', target_table
            );
        END IF;
    END LOOP;
END;
$mutable_matter_policies$;

DO $append_only_matter_policies$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.source_references'::regclass,
        'sklegal_legal.legacy_aliases'::regclass,
        'sklegal_legal.transaction_party_roles'::regclass,
        'sklegal_legal.transaction_source_references'::regclass,
        'sklegal_legal.tension_assertions'::regclass,
        'sklegal_legal.custody_events'::regclass,
        'sklegal_legal.authority_identities'::regclass,
        'sklegal_legal.authorities'::regclass,
        'sklegal_legal.element_evidence'::regclass,
        'sklegal_legal.theory_evidence'::regclass,
        'sklegal_legal.theory_authorities'::regclass,
        'sklegal_legal.remedy_authorities'::regclass,
        'sklegal_legal.deadline_calculations'::regclass,
        'sklegal_legal.deadline_calculation_sources'::regclass,
        'sklegal_legal.validations'::regclass,
        'sklegal_legal.validation_checks'::regclass,
        'sklegal_integrations.external_references'::regclass,
        'sklegal_integrations.corpus_release_references'::regclass,
        'sklegal_workflow.policy_decision_references'::regclass
    ]
    LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', target_table);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', target_table);
        EXECUTE format(
            'CREATE POLICY matter_select ON %s FOR SELECT '
            'USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))', target_table
        );
        EXECUTE format(
            'CREATE POLICY matter_insert ON %s FOR INSERT '
            'WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id))', target_table
        );
    END LOOP;
END;
$append_only_matter_policies$;

ALTER TABLE sklegal_legal.communication_participants ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.communication_participants FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.communications ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.communications FORCE ROW LEVEL SECURITY;
CREATE POLICY communication_select
ON sklegal_legal.communications
FOR SELECT USING (
    sklegal_identity.record_is_authorized(tenant_id, matter_id)
);
CREATE POLICY communication_controlled_insert
ON sklegal_legal.communications
FOR INSERT WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
);
CREATE POLICY communication_controlled_update
ON sklegal_legal.communications
FOR UPDATE
USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
)
WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
);
CREATE POLICY communication_participant_select
ON sklegal_legal.communication_participants
FOR SELECT USING (
    sklegal_identity.record_is_authorized(tenant_id, matter_id)
);
CREATE POLICY communication_participant_controlled_insert
ON sklegal_legal.communication_participants
FOR INSERT WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
);
CREATE POLICY communication_participant_controlled_delete
ON sklegal_legal.communication_participants
FOR DELETE USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
);

DO $controlled_relation_owner_policies$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.deadline_calculations'::regclass,
        'sklegal_legal.validations'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE POLICY controlled_relation_owner_update ON %s FOR UPDATE '
            'USING (current_user = %L AND session_user <> current_user '
            'AND sklegal_identity.record_is_authorized(tenant_id, matter_id)) '
            'WITH CHECK (current_user = %L AND session_user <> current_user '
            'AND sklegal_identity.record_is_authorized(tenant_id, matter_id))',
            target_table, 'sklegal_migrator', 'sklegal_migrator'
        );
    END LOOP;
END;
$controlled_relation_owner_policies$;

ALTER TABLE sklegal_legal.work_product_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.work_product_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY work_product_version_select ON sklegal_legal.work_product_versions
FOR SELECT USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY work_product_version_draft_insert ON sklegal_legal.work_product_versions
FOR INSERT WITH CHECK (
    sklegal_identity.record_is_authorized(tenant_id, matter_id) AND status = 'draft'
);
CREATE POLICY work_product_version_controlled_update ON sklegal_legal.work_product_versions
FOR UPDATE
USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
)
WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
);

ALTER TABLE sklegal_legal.approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.approvals FORCE ROW LEVEL SECURITY;
CREATE POLICY approval_select ON sklegal_legal.approvals
FOR SELECT USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY approval_pending_insert ON sklegal_legal.approvals
FOR INSERT WITH CHECK (
    sklegal_identity.record_is_authorized(tenant_id, matter_id) AND status = 'pending'
);
CREATE POLICY approval_controlled_update ON sklegal_legal.approvals
FOR UPDATE
USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
)
WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
);

ALTER TABLE sklegal_legal.executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.executions FORCE ROW LEVEL SECURITY;
CREATE POLICY execution_select ON sklegal_legal.executions
FOR SELECT USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY execution_draft_insert ON sklegal_legal.executions
FOR INSERT WITH CHECK (
    sklegal_identity.record_is_authorized(tenant_id, matter_id)
    AND status = 'draft'
    AND validation_result_id IS NULL
    AND approval_id IS NULL
    AND approval_version IS NULL
);
CREATE POLICY execution_controlled_update ON sklegal_legal.executions
FOR UPDATE
USING (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
)
WITH CHECK (
    current_user = 'sklegal_migrator' AND session_user <> current_user
    AND sklegal_identity.record_is_authorized(tenant_id, matter_id)
);

DO $controlled_evidence_policies$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.approval_history'::regclass,
        'sklegal_legal.execution_events'::regclass,
        'sklegal_legal.execution_receipts'::regclass
    ]
    LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', target_table);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', target_table);
        EXECUTE format(
            'CREATE POLICY matter_select ON %s FOR SELECT '
            'USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))', target_table
        );
        EXECUTE format(
            'CREATE POLICY controlled_writer_insert ON %s FOR INSERT '
            'WITH CHECK (current_user = %L AND session_user <> current_user '
            'AND sklegal_identity.record_is_authorized(tenant_id, matter_id))',
            target_table, 'sklegal_migrator'
        );
    END LOOP;
END;
$controlled_evidence_policies$;

ALTER TABLE sklegal_audit.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_audit.events FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_select ON sklegal_audit.events
FOR SELECT USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));

CREATE INDEX database_role_binding_scope_idx
ON sklegal_identity.database_role_bindings (tenant_id, principal_id) WHERE active;
CREATE INDEX tenant_membership_principal_idx
ON sklegal_identity.tenant_memberships (principal_id, tenant_id) WHERE active;
CREATE INDEX matter_membership_principal_idx
ON sklegal_legal.matter_memberships (principal_id, tenant_id, matter_id) WHERE active;
CREATE INDEX audit_event_matter_time_idx
ON sklegal_audit.events (tenant_id, matter_id, occurred_at);
CREATE INDEX authority_version_time_idx
ON sklegal_legal.authorities (tenant_id, matter_id, id, system_from DESC);

REVOKE ALL ON SCHEMA sklegal_identity, sklegal_legal, sklegal_integrations, sklegal_workflow, sklegal_audit FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA sklegal_identity, sklegal_legal, sklegal_integrations, sklegal_workflow, sklegal_audit FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA sklegal_identity, sklegal_legal, sklegal_integrations, sklegal_workflow, sklegal_audit FROM PUBLIC;

-- sklegal:down
DROP INDEX sklegal_legal.authority_version_time_idx;
DROP INDEX sklegal_audit.audit_event_matter_time_idx;
DROP INDEX sklegal_legal.matter_membership_principal_idx;
DROP INDEX sklegal_identity.tenant_membership_principal_idx;
DROP INDEX sklegal_identity.database_role_binding_scope_idx;

DROP POLICY audit_select ON sklegal_audit.events;
ALTER TABLE sklegal_audit.events NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_audit.events DISABLE ROW LEVEL SECURITY;

DO $controlled_relation_owner_policies$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.deadline_calculations'::regclass,
        'sklegal_legal.validations'::regclass
    ]
    LOOP
        EXECUTE format(
            'DROP POLICY controlled_relation_owner_update ON %s', target_table
        );
    END LOOP;
END;
$controlled_relation_owner_policies$;

DO $controlled_evidence_policies$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.approval_history'::regclass,
        'sklegal_legal.execution_events'::regclass,
        'sklegal_legal.execution_receipts'::regclass
    ]
    LOOP
        EXECUTE format('DROP POLICY controlled_writer_insert ON %s', target_table);
        EXECUTE format('DROP POLICY matter_select ON %s', target_table);
        EXECUTE format('ALTER TABLE %s NO FORCE ROW LEVEL SECURITY', target_table);
        EXECUTE format('ALTER TABLE %s DISABLE ROW LEVEL SECURITY', target_table);
    END LOOP;
END;
$controlled_evidence_policies$;

DROP POLICY execution_draft_insert ON sklegal_legal.executions;
DROP POLICY execution_controlled_update ON sklegal_legal.executions;
DROP POLICY execution_select ON sklegal_legal.executions;
ALTER TABLE sklegal_legal.executions NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.executions DISABLE ROW LEVEL SECURITY;

DROP POLICY communication_participant_controlled_delete
ON sklegal_legal.communication_participants;
DROP POLICY communication_participant_controlled_insert
ON sklegal_legal.communication_participants;
DROP POLICY communication_participant_select
ON sklegal_legal.communication_participants;
ALTER TABLE sklegal_legal.communication_participants NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.communication_participants DISABLE ROW LEVEL SECURITY;
DROP POLICY communication_controlled_update ON sklegal_legal.communications;
DROP POLICY communication_controlled_insert ON sklegal_legal.communications;
DROP POLICY communication_select ON sklegal_legal.communications;
ALTER TABLE sklegal_legal.communications NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.communications DISABLE ROW LEVEL SECURITY;

DO $append_only_matter_policies$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.source_references'::regclass,
        'sklegal_legal.legacy_aliases'::regclass,
        'sklegal_legal.transaction_party_roles'::regclass,
        'sklegal_legal.transaction_source_references'::regclass,
        'sklegal_legal.tension_assertions'::regclass,
        'sklegal_legal.custody_events'::regclass,
        'sklegal_legal.authority_identities'::regclass,
        'sklegal_legal.authorities'::regclass,
        'sklegal_legal.element_evidence'::regclass,
        'sklegal_legal.theory_evidence'::regclass,
        'sklegal_legal.theory_authorities'::regclass,
        'sklegal_legal.remedy_authorities'::regclass,
        'sklegal_legal.deadline_calculations'::regclass,
        'sklegal_legal.deadline_calculation_sources'::regclass,
        'sklegal_legal.validations'::regclass,
        'sklegal_legal.validation_checks'::regclass,
        'sklegal_integrations.external_references'::regclass,
        'sklegal_integrations.corpus_release_references'::regclass,
        'sklegal_workflow.policy_decision_references'::regclass
    ]
    LOOP
        EXECUTE format('DROP POLICY matter_insert ON %s', target_table);
        EXECUTE format('DROP POLICY matter_select ON %s', target_table);
        EXECUTE format('ALTER TABLE %s NO FORCE ROW LEVEL SECURITY', target_table);
        EXECUTE format('ALTER TABLE %s DISABLE ROW LEVEL SECURITY', target_table);
    END LOOP;
END;
$append_only_matter_policies$;

DROP POLICY approval_controlled_update ON sklegal_legal.approvals;
DROP POLICY approval_pending_insert ON sklegal_legal.approvals;
DROP POLICY approval_select ON sklegal_legal.approvals;
ALTER TABLE sklegal_legal.approvals NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.approvals DISABLE ROW LEVEL SECURITY;

DROP POLICY work_product_version_controlled_update ON sklegal_legal.work_product_versions;
DROP POLICY work_product_version_draft_insert ON sklegal_legal.work_product_versions;
DROP POLICY work_product_version_select ON sklegal_legal.work_product_versions;
ALTER TABLE sklegal_legal.work_product_versions NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.work_product_versions DISABLE ROW LEVEL SECURITY;

DO $mutable_matter_policies$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
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
        'sklegal_workflow.workflow_references'::regclass
    ]
    LOOP
        EXECUTE format('DROP POLICY matter_boundary ON %s', target_table);
        EXECUTE format('ALTER TABLE %s NO FORCE ROW LEVEL SECURITY', target_table);
        EXECUTE format('ALTER TABLE %s DISABLE ROW LEVEL SECURITY', target_table);
    END LOOP;
END;
$mutable_matter_policies$;

DO $tenant_policies$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_identity.principals'::regclass,
        'sklegal_legal.clients'::regclass,
        'sklegal_legal.engagements'::regclass,
        'sklegal_integrations.connections'::regclass
    ]
    LOOP
        EXECUTE format('DROP POLICY tenant_boundary ON %s', target_table);
        EXECUTE format('ALTER TABLE %s NO FORCE ROW LEVEL SECURITY', target_table);
        EXECUTE format('ALTER TABLE %s DISABLE ROW LEVEL SECURITY', target_table);
    END LOOP;
END;
$tenant_policies$;

DROP POLICY principal_status_controlled_read ON sklegal_identity.principals;

DROP POLICY matter_boundary ON sklegal_legal.matters;
ALTER TABLE sklegal_legal.matters NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.matters DISABLE ROW LEVEL SECURITY;
DROP POLICY tenant_boundary ON sklegal_identity.tenants;
DROP POLICY tenant_status_controlled_read ON sklegal_identity.tenants;
ALTER TABLE sklegal_identity.tenants NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_identity.tenants DISABLE ROW LEVEL SECURITY;
DROP POLICY matter_membership_self_select ON sklegal_legal.matter_memberships;
ALTER TABLE sklegal_legal.matter_memberships NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_legal.matter_memberships DISABLE ROW LEVEL SECURITY;
DROP POLICY tenant_membership_self_select ON sklegal_identity.tenant_memberships;
ALTER TABLE sklegal_identity.tenant_memberships NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_identity.tenant_memberships DISABLE ROW LEVEL SECURITY;
DROP POLICY binding_status_sync_update ON sklegal_identity.database_role_bindings;
DROP POLICY binding_status_controlled_read ON sklegal_identity.database_role_bindings;
DROP POLICY binding_self_select ON sklegal_identity.database_role_bindings;
ALTER TABLE sklegal_identity.database_role_bindings NO FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_identity.database_role_bindings DISABLE ROW LEVEL SECURITY;
DROP FUNCTION sklegal_identity.record_is_authorized(uuid, uuid);
DROP FUNCTION sklegal_identity.runtime_role_is_safe();
