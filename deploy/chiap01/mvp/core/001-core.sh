#!/bin/sh
set -eu

psql --set=ON_ERROR_STOP=1 \
  --set=app_password="$SKLEGAL_APP_PASSWORD" \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --file /docker-entrypoint-initdb.d/core.sql.tmpl

for migration in /opt/sklegal/core-migrations/[0-9][0-9][0-9][0-9]_*.sql; do
  {
    printf 'SET ROLE sklegal_migrator;\n'
    sed '/^-- sklegal:down$/,$d' "$migration"
  } | psql --set=ON_ERROR_STOP=1 \
      --username "$POSTGRES_USER" \
      --dbname "$POSTGRES_DB"
done

psql --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" <<'SQL'
GRANT USAGE ON SCHEMA sklegal_identity, sklegal_legal, sklegal_workflow,
  sklegal_audit, sklegal_artifact, sklegal_task_deadline, sklegal_activity,
  sklegal_governed_corpus TO sklegal_core_app;
GRANT USAGE ON DOMAIN sklegal_legal.record_version,
  sklegal_legal.sha256_digest, sklegal_legal.data_classification,
  sklegal_legal.record_completeness TO sklegal_core_app;
GRANT SELECT ON sklegal_identity.database_role_bindings,
  sklegal_identity.tenant_memberships,
  sklegal_legal.matter_memberships,
  sklegal_legal.ledger_claim_identities,
  sklegal_legal.joined_analysis_snapshots,
  sklegal_legal.joined_analysis_snapshot_current,
  sklegal_audit.chain_heads,
  sklegal_audit.projection_watermarks TO sklegal_core_app;
GRANT SELECT, INSERT ON sklegal_workflow.agent_run_identities,
  sklegal_workflow.agent_runs,
  sklegal_workflow.agent_run_attempts,
  sklegal_workflow.agent_tool_calls,
  sklegal_workflow.agent_recommendations,
  sklegal_workflow.agent_blind_challenges,
  sklegal_workflow.agent_human_dispositions,
  sklegal_workflow.agent_run_idempotency,
  sklegal_workflow.agent_run_audit_events TO sklegal_core_app;
GRANT SELECT ON sklegal_workflow.agent_run_current,
  sklegal_workflow.agent_run_history TO sklegal_core_app;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA sklegal_artifact,
  sklegal_task_deadline, sklegal_governed_corpus TO sklegal_core_app;
GRANT SELECT ON sklegal_activity.entries,
  sklegal_activity.export_proposals TO sklegal_core_app;
GRANT INSERT, UPDATE ON sklegal_legal.work_product_feature_identities
  TO sklegal_core_app;
GRANT INSERT ON sklegal_legal.work_product_feature_versions,
  sklegal_legal.work_product_feature_idempotency,
  sklegal_audit.work_product_feature_events,
  sklegal_audit.work_product_feature_outbox TO sklegal_core_app;
GRANT EXECUTE ON FUNCTION
  sklegal_identity.current_tenant_id(),
  sklegal_identity.current_principal_id(),
  sklegal_identity.has_tenant_membership(uuid),
  sklegal_identity.runtime_role_is_safe(),
  sklegal_identity.record_is_authorized(uuid, uuid),
  sklegal_identity.capability_revocation_snapshot(
    uuid, sklegal_legal.sha256_digest[]
  ),
  sklegal_legal.has_matter_membership(uuid, uuid)
  TO sklegal_core_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA sklegal_activity
  TO sklegal_core_app;
SQL
