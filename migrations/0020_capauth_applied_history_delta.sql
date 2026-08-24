-- sklegal:up
DO $capauth_applied_history_assertion$
DECLARE
    reject_function_oids oid[];
    snapshot_function_oids oid[];
    runtime_role_oids oid[];
    trigger_total bigint;
    trigger_valid bigint;
    trigger_relations bigint;
    public_execute_entries bigint;
    runtime_execute_entries bigint;
    runtime_grantable_entries bigint;
BEGIN
    SELECT array_agg(procedure.oid ORDER BY procedure.oid)
    INTO reject_function_oids
    FROM pg_catalog.pg_proc AS procedure
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname = 'sklegal_legal'
      AND procedure.proname = 'reject_nil_domain_ids'
      AND pg_catalog.oidvectortypes(procedure.proargtypes) = '';

    SELECT array_agg(procedure.oid ORDER BY procedure.oid)
    INTO snapshot_function_oids
    FROM pg_catalog.pg_proc AS procedure
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname = 'sklegal_identity'
      AND procedure.proname = 'capability_principal_snapshot'
      AND pg_catalog.oidvectortypes(procedure.proargtypes) = 'uuid, uuid';

    SELECT array_agg(role_record.oid ORDER BY role_record.oid)
    INTO runtime_role_oids
    FROM pg_catalog.pg_roles AS role_record
    WHERE role_record.rolname = 'sklegal_runtime';

    IF COALESCE(cardinality(reject_function_oids), 0) <> 1
       OR COALESCE(cardinality(snapshot_function_oids), 0) <> 1
       OR COALESCE(cardinality(runtime_role_oids), 0) <> 1 THEN
        RAISE EXCEPTION 'CapAuth applied-history state drifted'
            USING ERRCODE = '55000';
    END IF;

    SELECT count(*),
           count(*) FILTER (
               WHERE NOT trigger_record.tgisinternal
                 AND trigger_record.tgenabled = 'O'
                 AND trigger_record.tgtype = 23
                 AND trigger_record.tgfoid = reject_function_oids[1]
           ),
           count(DISTINCT relation.oid)
    INTO trigger_total, trigger_valid, trigger_relations
    FROM pg_catalog.pg_trigger AS trigger_record
    JOIN pg_catalog.pg_class AS relation
      ON relation.oid = trigger_record.tgrelid
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'sklegal_identity'
      AND relation.relname = ANY (ARRAY[
          'capability_revocations',
          'capability_replay_reservations'
      ]::name[])
      AND trigger_record.tgname = 'domain_id_non_nil';

    IF trigger_total <> 2
       OR trigger_valid <> 2
       OR trigger_relations <> 2 THEN
        RAISE EXCEPTION 'CapAuth applied-history state drifted'
            USING ERRCODE = '55000';
    END IF;

    SELECT count(*) FILTER (
               WHERE acl.grantee = 0
                 AND acl.privilege_type = 'EXECUTE'
           ),
           count(*) FILTER (
               WHERE acl.grantee = runtime_role_oids[1]
                 AND acl.privilege_type = 'EXECUTE'
           ),
           count(*) FILTER (
               WHERE acl.grantee = runtime_role_oids[1]
                 AND acl.privilege_type = 'EXECUTE'
                 AND acl.is_grantable
           )
    INTO public_execute_entries,
         runtime_execute_entries,
         runtime_grantable_entries
    FROM pg_catalog.pg_proc AS procedure
    CROSS JOIN LATERAL pg_catalog.aclexplode(
        COALESCE(
            procedure.proacl,
            pg_catalog.acldefault('f', procedure.proowner)
        )
    ) AS acl
    WHERE procedure.oid = snapshot_function_oids[1];

    IF public_execute_entries <> 0
       OR runtime_execute_entries <> 1
       OR runtime_grantable_entries <> 0
       OR NOT pg_catalog.has_function_privilege(
           runtime_role_oids[1], snapshot_function_oids[1], 'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'CapAuth applied-history state drifted'
            USING ERRCODE = '55000';
    END IF;
END;
$capauth_applied_history_assertion$;

-- sklegal:down
DO $capauth_applied_history_assertion$
DECLARE
    reject_function_oids oid[];
    snapshot_function_oids oid[];
    runtime_role_oids oid[];
    trigger_total bigint;
    trigger_valid bigint;
    trigger_relations bigint;
    public_execute_entries bigint;
    runtime_execute_entries bigint;
    runtime_grantable_entries bigint;
BEGIN
    SELECT array_agg(procedure.oid ORDER BY procedure.oid)
    INTO reject_function_oids
    FROM pg_catalog.pg_proc AS procedure
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname = 'sklegal_legal'
      AND procedure.proname = 'reject_nil_domain_ids'
      AND pg_catalog.oidvectortypes(procedure.proargtypes) = '';

    SELECT array_agg(procedure.oid ORDER BY procedure.oid)
    INTO snapshot_function_oids
    FROM pg_catalog.pg_proc AS procedure
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname = 'sklegal_identity'
      AND procedure.proname = 'capability_principal_snapshot'
      AND pg_catalog.oidvectortypes(procedure.proargtypes) = 'uuid, uuid';

    SELECT array_agg(role_record.oid ORDER BY role_record.oid)
    INTO runtime_role_oids
    FROM pg_catalog.pg_roles AS role_record
    WHERE role_record.rolname = 'sklegal_runtime';

    IF COALESCE(cardinality(reject_function_oids), 0) <> 1
       OR COALESCE(cardinality(snapshot_function_oids), 0) <> 1
       OR COALESCE(cardinality(runtime_role_oids), 0) <> 1 THEN
        RAISE EXCEPTION 'CapAuth applied-history state drifted'
            USING ERRCODE = '55000';
    END IF;

    SELECT count(*),
           count(*) FILTER (
               WHERE NOT trigger_record.tgisinternal
                 AND trigger_record.tgenabled = 'O'
                 AND trigger_record.tgtype = 23
                 AND trigger_record.tgfoid = reject_function_oids[1]
           ),
           count(DISTINCT relation.oid)
    INTO trigger_total, trigger_valid, trigger_relations
    FROM pg_catalog.pg_trigger AS trigger_record
    JOIN pg_catalog.pg_class AS relation
      ON relation.oid = trigger_record.tgrelid
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'sklegal_identity'
      AND relation.relname = ANY (ARRAY[
          'capability_revocations',
          'capability_replay_reservations'
      ]::name[])
      AND trigger_record.tgname = 'domain_id_non_nil';

    IF trigger_total <> 2
       OR trigger_valid <> 2
       OR trigger_relations <> 2 THEN
        RAISE EXCEPTION 'CapAuth applied-history state drifted'
            USING ERRCODE = '55000';
    END IF;

    SELECT count(*) FILTER (
               WHERE acl.grantee = 0
                 AND acl.privilege_type = 'EXECUTE'
           ),
           count(*) FILTER (
               WHERE acl.grantee = runtime_role_oids[1]
                 AND acl.privilege_type = 'EXECUTE'
           ),
           count(*) FILTER (
               WHERE acl.grantee = runtime_role_oids[1]
                 AND acl.privilege_type = 'EXECUTE'
                 AND acl.is_grantable
           )
    INTO public_execute_entries,
         runtime_execute_entries,
         runtime_grantable_entries
    FROM pg_catalog.pg_proc AS procedure
    CROSS JOIN LATERAL pg_catalog.aclexplode(
        COALESCE(
            procedure.proacl,
            pg_catalog.acldefault('f', procedure.proowner)
        )
    ) AS acl
    WHERE procedure.oid = snapshot_function_oids[1];

    IF public_execute_entries <> 0
       OR runtime_execute_entries <> 1
       OR runtime_grantable_entries <> 0
       OR NOT pg_catalog.has_function_privilege(
           runtime_role_oids[1], snapshot_function_oids[1], 'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'CapAuth applied-history state drifted'
            USING ERRCODE = '55000';
    END IF;
END;
$capauth_applied_history_assertion$;
