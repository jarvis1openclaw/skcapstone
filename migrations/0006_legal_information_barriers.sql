-- sklegal:up
CREATE SEQUENCE sklegal_legal.policy_change_sequence AS bigint;

CREATE TABLE sklegal_legal.party_normalizations (
    id uuid NOT NULL,
    policy_change_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    party_id uuid NOT NULL,
    party_kind text NOT NULL CHECK (
        party_kind IN ('person', 'family', 'trust', 'estate', 'company', 'other')
    ),
    normalized_identity_digest sklegal_legal.sha256_digest NOT NULL,
    normalization_version text NOT NULL
        CHECK (normalization_version = 'sklegal-party-normalization/v1'),
    reviewed_by_principal_id uuid NOT NULL,
    reviewed_at timestamptz NOT NULL,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, party_id, normalization_version),
    FOREIGN KEY (tenant_id, matter_id, party_id)
        REFERENCES sklegal_legal.parties(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, reviewed_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (reviewed_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.party_associations (
    id uuid NOT NULL,
    policy_change_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    party_id uuid NOT NULL,
    relationship text NOT NULL
        CHECK (relationship IN ('client', 'adverse_party', 'related', 'other')),
    active boolean NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    source_reference_id uuid NOT NULL,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, party_id)
        REFERENCES sklegal_legal.parties(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, source_reference_id)
        REFERENCES sklegal_legal.source_references(tenant_id, matter_id, id),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.conflict_checks (
    id uuid NOT NULL,
    policy_change_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    checked_by_principal_id uuid NOT NULL,
    checked_at timestamptz NOT NULL,
    normalization_version text NOT NULL
        CHECK (normalization_version = 'sklegal-party-normalization/v1'),
    result text NOT NULL CHECK (result IN ('clear', 'hold')),
    complete boolean NOT NULL,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, checked_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (checked_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.conflict_matches (
    id uuid NOT NULL,
    policy_change_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    conflict_check_id uuid NOT NULL,
    candidate_party_id uuid NOT NULL,
    existing_matter_id uuid NOT NULL,
    association_id uuid NOT NULL,
    normalized_identity_digest sklegal_legal.sha256_digest NOT NULL,
    collision_kind text NOT NULL CHECK (collision_kind = 'exact_normalized_name'),
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, conflict_check_id)
        REFERENCES sklegal_legal.conflict_checks(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, candidate_party_id)
        REFERENCES sklegal_legal.parties(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, existing_matter_id, association_id)
        REFERENCES sklegal_legal.party_associations(tenant_id, matter_id, id),
    CHECK (existing_matter_id <> matter_id),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.conflict_waiver_references (
    id uuid NOT NULL,
    policy_change_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    artifact_id uuid NOT NULL,
    artifact_version bigint NOT NULL CHECK (artifact_version >= 1),
    content_sha256 sklegal_legal.sha256_digest NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz NOT NULL,
    recorded_by_principal_id uuid NOT NULL,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, recorded_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (valid_to > valid_from),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.conflict_decisions (
    id uuid NOT NULL,
    policy_change_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    conflict_check_id uuid NOT NULL,
    disposition text NOT NULL CHECK (disposition IN ('clear', 'hold', 'waived')),
    waiver_reference_id uuid,
    decided_by_principal_id uuid NOT NULL,
    decided_at timestamptz NOT NULL,
    supersedes_decision_id uuid,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, conflict_check_id)
        REFERENCES sklegal_legal.conflict_checks(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, waiver_reference_id)
        REFERENCES sklegal_legal.conflict_waiver_references(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, supersedes_decision_id)
        REFERENCES sklegal_legal.conflict_decisions(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, decided_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK ((disposition = 'waived') = (waiver_reference_id IS NOT NULL)),
    CHECK (decided_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.conflict_holds (
    id uuid NOT NULL,
    policy_change_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    conflict_decision_id uuid NOT NULL,
    reason_code text NOT NULL CHECK (
        reason_code ~ '^[a-z0-9][a-z0-9._:-]{0,159}$'
    ),
    effective_from timestamptz NOT NULL,
    effective_to timestamptz,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, conflict_decision_id)
        REFERENCES sklegal_legal.conflict_decisions(tenant_id, matter_id, id),
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.ethical_walls (
    id uuid NOT NULL,
    policy_change_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    wall_code text NOT NULL CHECK (wall_code ~ '^[a-z0-9][a-z0-9._:-]{0,159}$'),
    active boolean NOT NULL,
    membership_complete boolean NOT NULL,
    effective_from timestamptz NOT NULL,
    effective_to timestamptz,
    decided_by_principal_id uuid NOT NULL,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, wall_code, effective_from),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, decided_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.ethical_wall_memberships (
    id uuid NOT NULL,
    policy_change_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    wall_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    disposition text NOT NULL CHECK (disposition IN ('allowed', 'excluded')),
    effective_from timestamptz NOT NULL,
    effective_to timestamptz,
    decided_by_principal_id uuid NOT NULL,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, wall_id)
        REFERENCES sklegal_legal.ethical_walls(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    FOREIGN KEY (tenant_id, decided_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.protected_access_grants (
    id uuid NOT NULL,
    policy_change_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    access_level text NOT NULL CHECK (
        access_level IN ('privileged_work_product', 'highly_restricted')
    ),
    purpose text NOT NULL CHECK (purpose ~ '^[a-z0-9][a-z0-9_]{0,159}$'),
    effective_from timestamptz NOT NULL,
    effective_to timestamptz,
    granted_by_principal_id uuid NOT NULL,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    FOREIGN KEY (tenant_id, granted_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.material_classifications (
    id uuid NOT NULL,
    policy_change_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    material_id uuid NOT NULL,
    material_version bigint NOT NULL CHECK (material_version >= 1),
    source_kind text NOT NULL CHECK (
        source_kind ~ '^[a-z0-9][a-z0-9._:-]{0,159}$'
    ),
    source_id uuid NOT NULL,
    classification sklegal_legal.data_classification NOT NULL,
    classified_by_principal_id uuid NOT NULL,
    classified_at timestamptz NOT NULL,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (
        tenant_id, matter_id, material_id, material_version, source_kind, source_id
    ),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, classified_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (classified_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.material_protection_labels (
    id uuid NOT NULL,
    policy_change_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    material_id uuid NOT NULL,
    material_version bigint NOT NULL CHECK (material_version >= 1),
    label_family text NOT NULL CHECK (label_family IN ('privilege', 'work_product')),
    label_code text NOT NULL,
    active boolean NOT NULL,
    labeled_by_principal_id uuid NOT NULL,
    labeled_at timestamptz NOT NULL,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, labeled_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (
        (label_family = 'privilege' AND label_code IN (
            'attorney_client', 'common_interest', 'joint_defense',
            'other_privilege_review'
        ))
        OR
        (label_family = 'work_product' AND label_code IN (
            'attorney_work_product', 'opinion_work_product', 'other_work_product'
        ))
    ),
    CHECK (labeled_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.retention_policies (
    id uuid NOT NULL,
    policy_change_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    retain_for_days integer NOT NULL CHECK (retain_for_days BETWEEN 1 AND 36500),
    effective_from timestamptz NOT NULL,
    effective_to timestamptz,
    decided_by_principal_id uuid NOT NULL,
    supersedes_retention_policy_id uuid,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, decided_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    FOREIGN KEY (tenant_id, matter_id, supersedes_retention_policy_id)
        REFERENCES sklegal_legal.retention_policies(tenant_id, matter_id, id),
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.legal_holds (
    id uuid NOT NULL,
    policy_change_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('active', 'released')),
    hold_scope text NOT NULL CHECK (hold_scope IN ('matter', 'material')),
    material_id uuid,
    issued_by_principal_id uuid NOT NULL,
    effective_from timestamptz NOT NULL,
    supersedes_hold_id uuid,
    released_by_principal_id uuid,
    released_at timestamptz,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, matter_id, supersedes_hold_id)
        REFERENCES sklegal_legal.legal_holds(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, issued_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    FOREIGN KEY (tenant_id, released_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK ((hold_scope = 'material') = (material_id IS NOT NULL)),
    CHECK (
        (status = 'active' AND supersedes_hold_id IS NULL
            AND released_by_principal_id IS NULL AND released_at IS NULL)
        OR
        (status = 'released' AND supersedes_hold_id IS NOT NULL
            AND released_by_principal_id IS NOT NULL AND released_at IS NOT NULL)
    ),
    CHECK (released_at IS NULL OR released_at >= effective_from),
    CHECK (updated_at >= created_at)
);

CREATE TABLE sklegal_legal.matter_policy_states (
    id uuid NOT NULL,
    policy_change_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    policy_revision sklegal_legal.sha256_digest NOT NULL,
    conflict_decision_id uuid,
    retention_policy_id uuid,
    conflict_state_complete boolean NOT NULL,
    wall_state_complete boolean NOT NULL,
    classification_state_complete boolean NOT NULL,
    legal_hold_state_complete boolean NOT NULL,
    ownership_resolved boolean NOT NULL,
    pending_export boolean NOT NULL,
    preservation_required boolean NOT NULL,
    recorded_by_principal_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL,
    supersedes_state_id uuid,
    version sklegal_legal.record_version NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, policy_revision),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, matter_id, supersedes_state_id)
        REFERENCES sklegal_legal.matter_policy_states(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, conflict_decision_id)
        REFERENCES sklegal_legal.conflict_decisions(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, retention_policy_id)
        REFERENCES sklegal_legal.retention_policies(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, recorded_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (recorded_at <= updated_at),
    CHECK (updated_at >= created_at)
);

CREATE FUNCTION sklegal_legal.validate_conflict_decision_evidence()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    check_result text;
    check_complete boolean;
    prior_decided_at timestamptz;
BEGIN
    SELECT conflict_check.result, conflict_check.complete
    INTO check_result, check_complete
    FROM sklegal_legal.conflict_checks AS conflict_check
    WHERE conflict_check.tenant_id = NEW.tenant_id
      AND conflict_check.matter_id = NEW.matter_id
      AND conflict_check.id = NEW.conflict_check_id;
    IF NOT FOUND OR NOT check_complete
       OR (NEW.disposition = 'clear' AND check_result <> 'clear')
       OR (NEW.disposition IN ('hold', 'waived') AND check_result <> 'hold') THEN
        RAISE EXCEPTION 'conflict decision lacks matching complete check evidence'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.decided_at > clock_timestamp() THEN
        RAISE EXCEPTION 'conflict decision cannot be a future head'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.supersedes_decision_id IS NULL THEN
        IF EXISTS (
            SELECT 1 FROM sklegal_legal.conflict_decisions AS existing
            WHERE existing.tenant_id = NEW.tenant_id
              AND existing.matter_id = NEW.matter_id
        ) THEN
            RAISE EXCEPTION 'conflict decisions require one linear current head'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        SELECT prior.decided_at INTO prior_decided_at
        FROM sklegal_legal.conflict_decisions AS prior
        WHERE prior.tenant_id = NEW.tenant_id
          AND prior.matter_id = NEW.matter_id
          AND prior.id = NEW.supersedes_decision_id
          AND NOT EXISTS (
              SELECT 1 FROM sklegal_legal.conflict_decisions AS child
              WHERE child.tenant_id = prior.tenant_id
                AND child.matter_id = prior.matter_id
                AND child.supersedes_decision_id = prior.id
          );
        IF NOT FOUND THEN
            RAISE EXCEPTION 'conflict decisions require one linear current head'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.decided_at <= prior_decided_at THEN
            RAISE EXCEPTION 'conflict decision time must strictly advance'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER conflict_decision_evidence
BEFORE INSERT ON sklegal_legal.conflict_decisions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_conflict_decision_evidence();

CREATE FUNCTION sklegal_legal.validate_conflict_hold_evidence()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM sklegal_legal.conflict_decisions AS decision
        WHERE decision.tenant_id = NEW.tenant_id
          AND decision.matter_id = NEW.matter_id
          AND decision.id = NEW.conflict_decision_id
          AND decision.disposition = 'hold'
    ) THEN
        RAISE EXCEPTION 'conflict hold lacks an exact hold decision'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER conflict_hold_evidence
BEFORE INSERT ON sklegal_legal.conflict_holds
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_conflict_hold_evidence();

CREATE FUNCTION sklegal_legal.validate_legal_hold_release()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.status = 'active' THEN
        RETURN NEW;
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM sklegal_legal.legal_holds AS prior_hold
        WHERE prior_hold.tenant_id = NEW.tenant_id
          AND prior_hold.matter_id = NEW.matter_id
          AND prior_hold.id = NEW.supersedes_hold_id
          AND prior_hold.status = 'active'
          AND prior_hold.hold_scope = NEW.hold_scope
          AND prior_hold.material_id IS NOT DISTINCT FROM NEW.material_id
          AND prior_hold.issued_by_principal_id = NEW.issued_by_principal_id
          AND prior_hold.effective_from = NEW.effective_from
    ) THEN
        RAISE EXCEPTION 'legal hold release does not match an exact active hold'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER legal_hold_release_evidence
BEFORE INSERT ON sklegal_legal.legal_holds
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_legal_hold_release();

CREATE FUNCTION sklegal_legal.validate_retention_policy_head()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    prior_from timestamptz;
    prior_to timestamptz;
BEGIN
    IF NEW.effective_from > clock_timestamp() THEN
        RAISE EXCEPTION 'retention policy cannot be a future head'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.supersedes_retention_policy_id IS NULL THEN
        IF EXISTS (
            SELECT 1 FROM sklegal_legal.retention_policies AS existing
            WHERE existing.tenant_id = NEW.tenant_id
              AND existing.matter_id = NEW.matter_id
        ) THEN
            RAISE EXCEPTION 'retention policies require one linear current head'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        SELECT prior.effective_from, prior.effective_to
        INTO prior_from, prior_to
        FROM sklegal_legal.retention_policies AS prior
        WHERE prior.tenant_id = NEW.tenant_id
          AND prior.matter_id = NEW.matter_id
          AND prior.id = NEW.supersedes_retention_policy_id
          AND NOT EXISTS (
              SELECT 1 FROM sklegal_legal.retention_policies AS child
              WHERE child.tenant_id = prior.tenant_id
                AND child.matter_id = prior.matter_id
                AND child.supersedes_retention_policy_id = prior.id
          );
        IF NOT FOUND THEN
            RAISE EXCEPTION 'retention policies require one linear current head'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.effective_from <= prior_from THEN
            RAISE EXCEPTION 'retention policy time must strictly advance'
                USING ERRCODE = '23514';
        END IF;
        IF prior_to IS NULL OR NEW.effective_from < prior_to THEN
            RAISE EXCEPTION 'retention policy heads cannot overlap'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER retention_policy_head
BEFORE INSERT ON sklegal_legal.retention_policies
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_retention_policy_head();

CREATE FUNCTION sklegal_legal.validate_matter_policy_state_head()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    prior_recorded_at timestamptz;
BEGIN
    IF NEW.recorded_at > clock_timestamp() THEN
        RAISE EXCEPTION 'matter policy state cannot be a future head'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.supersedes_state_id IS NULL THEN
        IF EXISTS (
            SELECT 1 FROM sklegal_legal.matter_policy_states AS existing
            WHERE existing.tenant_id = NEW.tenant_id
              AND existing.matter_id = NEW.matter_id
        ) THEN
            RAISE EXCEPTION 'matter policy states require one linear current head'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        SELECT prior.recorded_at INTO prior_recorded_at
        FROM sklegal_legal.matter_policy_states AS prior
        WHERE prior.tenant_id = NEW.tenant_id
          AND prior.matter_id = NEW.matter_id
          AND prior.id = NEW.supersedes_state_id
          AND NOT EXISTS (
              SELECT 1 FROM sklegal_legal.matter_policy_states AS child
              WHERE child.tenant_id = prior.tenant_id
                AND child.matter_id = prior.matter_id
                AND child.supersedes_state_id = prior.id
          );
        IF NOT FOUND THEN
            RAISE EXCEPTION 'matter policy states require one linear current head'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.recorded_at <= prior_recorded_at THEN
            RAISE EXCEPTION 'matter policy state time must strictly advance'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    IF NEW.conflict_state_complete <> (NEW.conflict_decision_id IS NOT NULL) THEN
        RAISE EXCEPTION 'complete conflict state requires one exact decision head'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.conflict_decision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM sklegal_legal.conflict_decisions AS decision
        WHERE decision.tenant_id = NEW.tenant_id
          AND decision.matter_id = NEW.matter_id
          AND decision.id = NEW.conflict_decision_id
          AND decision.decided_at <= NEW.recorded_at
          AND NOT EXISTS (
              SELECT 1 FROM sklegal_legal.conflict_decisions AS child
              WHERE child.tenant_id = decision.tenant_id
                AND child.matter_id = decision.matter_id
                AND child.supersedes_decision_id = decision.id
          )
    ) THEN
        RAISE EXCEPTION 'matter policy state binds a stale conflict decision head'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.retention_policy_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM sklegal_legal.retention_policies AS retention
        WHERE retention.tenant_id = NEW.tenant_id
          AND retention.matter_id = NEW.matter_id
          AND retention.id = NEW.retention_policy_id
          AND retention.effective_from <= NEW.recorded_at
          AND NOT EXISTS (
              SELECT 1 FROM sklegal_legal.retention_policies AS child
              WHERE child.tenant_id = retention.tenant_id
                AND child.matter_id = retention.matter_id
                AND child.supersedes_retention_policy_id = retention.id
          )
    ) THEN
        RAISE EXCEPTION 'matter policy state binds a stale retention policy head'
            USING ERRCODE = '23514';
    END IF;

    NEW.policy_revision := pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                pg_catalog.concat_ws(
                    '|',
                    'sklegal-policy-state/v1',
                    NEW.tenant_id::text,
                    NEW.matter_id::text,
                    NEW.id::text,
                    NEW.policy_change_id::text,
                    COALESCE(NEW.supersedes_state_id::text, 'none'),
                    COALESCE(NEW.conflict_decision_id::text, 'none'),
                    COALESCE(NEW.retention_policy_id::text, 'none'),
                    NEW.conflict_state_complete::text,
                    NEW.wall_state_complete::text,
                    NEW.classification_state_complete::text,
                    NEW.legal_hold_state_complete::text,
                    NEW.ownership_resolved::text,
                    NEW.pending_export::text,
                    NEW.preservation_required::text,
                    NEW.recorded_at::text
                ),
                'UTF8'
            )
        ),
        'hex'
    );
    RETURN NEW;
END;
$function$;

CREATE TRIGGER matter_policy_state_head
BEFORE INSERT ON sklegal_legal.matter_policy_states
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.validate_matter_policy_state_head();

REVOKE ALL ON FUNCTION sklegal_legal.validate_conflict_decision_evidence()
FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_legal.validate_conflict_hold_evidence()
FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_legal.validate_legal_hold_release()
FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_legal.validate_retention_policy_head()
FROM PUBLIC;
REVOKE ALL ON FUNCTION sklegal_legal.validate_matter_policy_state_head()
FROM PUBLIC;

CREATE FUNCTION sklegal_legal.assign_policy_change_id()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
BEGIN
    NEW.policy_change_id := nextval('sklegal_legal.policy_change_sequence');
    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.assign_policy_change_id() FROM PUBLIC;

DO $information_barrier_triggers$
DECLARE
    target_table regclass;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'sklegal_legal.party_normalizations'::regclass,
        'sklegal_legal.party_associations'::regclass,
        'sklegal_legal.conflict_checks'::regclass,
        'sklegal_legal.conflict_matches'::regclass,
        'sklegal_legal.conflict_waiver_references'::regclass,
        'sklegal_legal.conflict_decisions'::regclass,
        'sklegal_legal.conflict_holds'::regclass,
        'sklegal_legal.ethical_walls'::regclass,
        'sklegal_legal.ethical_wall_memberships'::regclass,
        'sklegal_legal.protected_access_grants'::regclass,
        'sklegal_legal.material_classifications'::regclass,
        'sklegal_legal.material_protection_labels'::regclass,
        'sklegal_legal.retention_policies'::regclass,
        'sklegal_legal.legal_holds'::regclass,
        'sklegal_legal.matter_policy_states'::regclass
    ]
    LOOP
        EXECUTE format(
            'CREATE TRIGGER assign_policy_change BEFORE INSERT ON %s '
            'FOR EACH ROW EXECUTE FUNCTION sklegal_legal.assign_policy_change_id()',
            target_table
        );
        EXECUTE format(
            'CREATE TRIGGER initialize_record BEFORE INSERT ON %s '
            'FOR EACH ROW EXECUTE FUNCTION sklegal_legal.initialize_record_audit()',
            target_table
        );
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
            'CREATE POLICY policy_record_select ON %s FOR SELECT '
            'USING (current_user = %L AND session_user <> current_user '
            'AND sklegal_identity.record_is_authorized(tenant_id, matter_id))',
            target_table,
            'sklegal_migrator'
        );
    END LOOP;
END;
$information_barrier_triggers$;

CREATE INDEX party_normalization_collision_idx
ON sklegal_legal.party_normalizations
    (tenant_id, party_kind, normalized_identity_digest);
CREATE INDEX party_association_collision_idx
ON sklegal_legal.party_associations
    (tenant_id, relationship, matter_id, party_id) WHERE active;
CREATE INDEX conflict_decision_current_idx
ON sklegal_legal.conflict_decisions (tenant_id, matter_id, policy_change_id DESC);
CREATE UNIQUE INDEX conflict_decision_one_genesis_idx
ON sklegal_legal.conflict_decisions (tenant_id, matter_id)
WHERE supersedes_decision_id IS NULL;
CREATE UNIQUE INDEX conflict_decision_one_successor_idx
ON sklegal_legal.conflict_decisions
    (tenant_id, matter_id, supersedes_decision_id)
WHERE supersedes_decision_id IS NOT NULL;
CREATE INDEX conflict_hold_current_idx
ON sklegal_legal.conflict_holds (tenant_id, matter_id, effective_from DESC);
CREATE INDEX ethical_wall_current_idx
ON sklegal_legal.ethical_walls (tenant_id, matter_id, effective_from DESC)
WHERE active;
CREATE INDEX protected_access_current_idx
ON sklegal_legal.protected_access_grants
    (tenant_id, matter_id, principal_id, purpose, effective_from DESC);
CREATE INDEX material_protection_current_idx
ON sklegal_legal.material_protection_labels
    (tenant_id, matter_id, material_id, material_version, labeled_at DESC)
WHERE active;
CREATE INDEX material_classification_current_idx
ON sklegal_legal.material_classifications
    (tenant_id, matter_id, material_id, material_version, classified_at DESC);
CREATE INDEX legal_hold_current_idx
ON sklegal_legal.legal_holds
    (tenant_id, matter_id, material_id, policy_change_id DESC);
CREATE UNIQUE INDEX legal_hold_one_release_idx
ON sklegal_legal.legal_holds (tenant_id, matter_id, supersedes_hold_id)
WHERE status = 'released';
CREATE INDEX retention_policy_current_idx
ON sklegal_legal.retention_policies
    (tenant_id, matter_id, policy_change_id DESC);
CREATE UNIQUE INDEX retention_policy_one_genesis_idx
ON sklegal_legal.retention_policies (tenant_id, matter_id)
WHERE supersedes_retention_policy_id IS NULL;
CREATE UNIQUE INDEX retention_policy_one_successor_idx
ON sklegal_legal.retention_policies
    (tenant_id, matter_id, supersedes_retention_policy_id)
WHERE supersedes_retention_policy_id IS NOT NULL;
CREATE INDEX matter_policy_state_current_idx
ON sklegal_legal.matter_policy_states (tenant_id, matter_id, policy_change_id DESC);
CREATE UNIQUE INDEX matter_policy_state_one_genesis_idx
ON sklegal_legal.matter_policy_states (tenant_id, matter_id)
WHERE supersedes_state_id IS NULL;
CREATE UNIQUE INDEX matter_policy_state_one_successor_idx
ON sklegal_legal.matter_policy_states
    (tenant_id, matter_id, supersedes_state_id)
WHERE supersedes_state_id IS NOT NULL;

CREATE FUNCTION sklegal_legal.material_policy_snapshot(
    target_tenant_id uuid,
    target_matter_id uuid,
    target_material_id uuid,
    target_material_version bigint,
    target_principal_id uuid
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    current_state sklegal_legal.matter_policy_states%ROWTYPE;
    head_count bigint;
    snapshot jsonb;
BEGIN
    IF target_material_version < 1
       OR target_principal_id IS DISTINCT FROM sklegal_identity.current_principal_id()
       OR target_tenant_id IS DISTINCT FROM sklegal_identity.current_tenant_id()
       OR NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_legal.has_matter_membership(
           target_tenant_id, target_matter_id
       ) THEN
        RAISE EXCEPTION 'policy snapshot scope is not authorized'
            USING ERRCODE = '42501';
    END IF;

    SELECT pg_catalog.count(*) INTO head_count
    FROM sklegal_legal.matter_policy_states AS state
    WHERE state.tenant_id = target_tenant_id
      AND state.matter_id = target_matter_id
      AND NOT EXISTS (
          SELECT 1 FROM sklegal_legal.matter_policy_states AS child
          WHERE child.tenant_id = state.tenant_id
            AND child.matter_id = state.matter_id
            AND child.supersedes_state_id = state.id
      );
    IF head_count <> 1 THEN
        RAISE EXCEPTION 'current matter policy state head is unavailable'
            USING ERRCODE = '55000';
    END IF;
    SELECT state.* INTO STRICT current_state
    FROM sklegal_legal.matter_policy_states AS state
    WHERE state.tenant_id = target_tenant_id
      AND state.matter_id = target_matter_id
      AND NOT EXISTS (
          SELECT 1 FROM sklegal_legal.matter_policy_states AS child
          WHERE child.tenant_id = state.tenant_id
            AND child.matter_id = state.matter_id
            AND child.supersedes_state_id = state.id
      );

    IF current_state.recorded_at > clock_timestamp()
       OR (
           current_state.conflict_state_complete
           AND NOT EXISTS (
               SELECT 1 FROM sklegal_legal.conflict_decisions AS decision
               WHERE decision.tenant_id = target_tenant_id
                 AND decision.matter_id = target_matter_id
                 AND decision.id = current_state.conflict_decision_id
                 AND decision.decided_at <= current_state.recorded_at
                 AND NOT EXISTS (
                     SELECT 1 FROM sklegal_legal.conflict_decisions AS child
                     WHERE child.tenant_id = decision.tenant_id
                       AND child.matter_id = decision.matter_id
                       AND child.supersedes_decision_id = decision.id
                 )
           )
       )
       OR (
           current_state.retention_policy_id IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM sklegal_legal.retention_policies AS retention
               WHERE retention.tenant_id = target_tenant_id
                 AND retention.matter_id = target_matter_id
                 AND retention.id = current_state.retention_policy_id
                 AND retention.effective_from <= current_state.recorded_at
                 AND NOT EXISTS (
                     SELECT 1 FROM sklegal_legal.retention_policies AS child
                     WHERE child.tenant_id = retention.tenant_id
                       AND child.matter_id = retention.matter_id
                       AND child.supersedes_retention_policy_id = retention.id
                 )
           )
       )
       OR EXISTS (
           SELECT 1
           FROM (
               SELECT policy_change_id
               FROM sklegal_legal.party_normalizations
               WHERE tenant_id = target_tenant_id
                 AND matter_id = target_matter_id
               UNION ALL
               SELECT policy_change_id
               FROM sklegal_legal.party_associations
               WHERE tenant_id = target_tenant_id
                 AND matter_id = target_matter_id
               UNION ALL
               SELECT policy_change_id
               FROM sklegal_legal.conflict_checks
               WHERE tenant_id = target_tenant_id
                 AND matter_id = target_matter_id
               UNION ALL
               SELECT policy_change_id
               FROM sklegal_legal.conflict_matches
               WHERE tenant_id = target_tenant_id
                 AND matter_id = target_matter_id
               UNION ALL
               SELECT policy_change_id
               FROM sklegal_legal.conflict_waiver_references
               WHERE tenant_id = target_tenant_id
                 AND matter_id = target_matter_id
               UNION ALL
               SELECT policy_change_id
               FROM sklegal_legal.conflict_decisions
               WHERE tenant_id = target_tenant_id
                 AND matter_id = target_matter_id
               UNION ALL
               SELECT policy_change_id
               FROM sklegal_legal.conflict_holds
               WHERE tenant_id = target_tenant_id
                 AND matter_id = target_matter_id
               UNION ALL
               SELECT policy_change_id
               FROM sklegal_legal.ethical_walls
               WHERE tenant_id = target_tenant_id
                 AND matter_id = target_matter_id
               UNION ALL
               SELECT policy_change_id
               FROM sklegal_legal.ethical_wall_memberships
               WHERE tenant_id = target_tenant_id
                 AND matter_id = target_matter_id
               UNION ALL
               SELECT policy_change_id
               FROM sklegal_legal.protected_access_grants
               WHERE tenant_id = target_tenant_id
                 AND matter_id = target_matter_id
               UNION ALL
               SELECT policy_change_id
               FROM sklegal_legal.material_classifications
               WHERE tenant_id = target_tenant_id
                 AND matter_id = target_matter_id
               UNION ALL
               SELECT policy_change_id
               FROM sklegal_legal.material_protection_labels
               WHERE tenant_id = target_tenant_id
                 AND matter_id = target_matter_id
               UNION ALL
               SELECT policy_change_id
               FROM sklegal_legal.retention_policies
               WHERE tenant_id = target_tenant_id
                 AND matter_id = target_matter_id
               UNION ALL
               SELECT policy_change_id
               FROM sklegal_legal.legal_holds
               WHERE tenant_id = target_tenant_id
                 AND matter_id = target_matter_id
           ) AS policy_change
           WHERE policy_change.policy_change_id > current_state.policy_change_id
       ) THEN
        RAISE EXCEPTION 'current matter policy state is stale'
            USING ERRCODE = '55000';
    END IF;

    SELECT pg_catalog.jsonb_build_object(
        'policy_revision', current_state.policy_revision,
        'tenant_id', target_tenant_id,
        'matter_id', target_matter_id,
        'material_id', target_material_id,
        'material_version', target_material_version,
        'tenant_membership', 'active',
        'matter_membership', 'active',
        'conflict_state_complete', current_state.conflict_state_complete,
        'wall_state_complete', current_state.wall_state_complete,
        'classification_state_complete',
            current_state.classification_state_complete,
        'conflict_decision', (
            SELECT pg_catalog.jsonb_build_object(
                'decision_id', decision.id,
                'tenant_id', decision.tenant_id,
                'matter_id', decision.matter_id,
                'conflict_check_id', decision.conflict_check_id,
                'disposition', decision.disposition,
                'waiver_reference', CASE
                    WHEN waiver.id IS NULL THEN NULL
                    ELSE pg_catalog.jsonb_build_object(
                        'waiver_id', waiver.id,
                        'artifact_id', waiver.artifact_id,
                        'artifact_version', waiver.artifact_version,
                        'content_sha256', waiver.content_sha256,
                        'tenant_id', waiver.tenant_id,
                        'matter_id', waiver.matter_id,
                        'valid_from', waiver.valid_from,
                        'valid_to', waiver.valid_to
                    )
                END,
                'decided_by_principal_id', decision.decided_by_principal_id,
                'decided_at', decision.decided_at
            )
            FROM sklegal_legal.conflict_decisions AS decision
            LEFT JOIN sklegal_legal.conflict_waiver_references AS waiver
              ON waiver.tenant_id = decision.tenant_id
             AND waiver.matter_id = decision.matter_id
             AND waiver.id = decision.waiver_reference_id
            WHERE decision.tenant_id = target_tenant_id
              AND decision.matter_id = target_matter_id
              AND decision.id = current_state.conflict_decision_id
              AND decision.policy_change_id < current_state.policy_change_id
        ),
        'conflict_holds', COALESCE((
            SELECT pg_catalog.jsonb_agg(
                pg_catalog.jsonb_build_object(
                    'hold_id', hold.id,
                    'tenant_id', hold.tenant_id,
                    'matter_id', hold.matter_id,
                    'conflict_decision_id', hold.conflict_decision_id,
                    'reason_code', hold.reason_code,
                    'effective_from', hold.effective_from,
                    'effective_to', hold.effective_to
                ) ORDER BY hold.effective_from, hold.id
            )
            FROM sklegal_legal.conflict_holds AS hold
            WHERE hold.tenant_id = target_tenant_id
              AND hold.matter_id = target_matter_id
              AND hold.policy_change_id < current_state.policy_change_id
        ), '[]'::jsonb),
        'walls', COALESCE((
            SELECT pg_catalog.jsonb_agg(
                pg_catalog.jsonb_build_object(
                    'wall_id', wall.id,
                    'tenant_id', wall.tenant_id,
                    'matter_id', wall.matter_id,
                    'name', wall.wall_code,
                    'active', wall.active,
                    'membership_complete', wall.membership_complete,
                    'effective_from', wall.effective_from,
                    'effective_to', wall.effective_to
                ) ORDER BY wall.effective_from, wall.id
            )
            FROM sklegal_legal.ethical_walls AS wall
            WHERE wall.tenant_id = target_tenant_id
              AND wall.matter_id = target_matter_id
              AND wall.policy_change_id < current_state.policy_change_id
        ), '[]'::jsonb),
        'wall_memberships', COALESCE((
            SELECT pg_catalog.jsonb_agg(
                pg_catalog.jsonb_build_object(
                    'wall_id', membership.wall_id,
                    'tenant_id', membership.tenant_id,
                    'matter_id', membership.matter_id,
                    'principal_id', membership.principal_id,
                    'disposition', membership.disposition,
                    'effective_from', membership.effective_from,
                    'effective_to', membership.effective_to
                ) ORDER BY membership.effective_from, membership.id
            )
            FROM sklegal_legal.ethical_wall_memberships AS membership
            WHERE membership.tenant_id = target_tenant_id
              AND membership.matter_id = target_matter_id
              AND membership.principal_id = target_principal_id
              AND membership.policy_change_id < current_state.policy_change_id
        ), '[]'::jsonb),
        'protected_access_grants', COALESCE((
            SELECT pg_catalog.jsonb_agg(
                pg_catalog.jsonb_build_object(
                    'grant_id', access_grant.id,
                    'tenant_id', access_grant.tenant_id,
                    'matter_id', access_grant.matter_id,
                    'principal_id', access_grant.principal_id,
                    'access_level', access_grant.access_level,
                    'purpose', access_grant.purpose,
                    'granted_by_principal_id',
                        access_grant.granted_by_principal_id,
                    'effective_from', access_grant.effective_from,
                    'effective_to', access_grant.effective_to
                ) ORDER BY access_grant.effective_from, access_grant.id
            )
            FROM sklegal_legal.protected_access_grants AS access_grant
            WHERE access_grant.tenant_id = target_tenant_id
              AND access_grant.matter_id = target_matter_id
              AND access_grant.principal_id = target_principal_id
              AND access_grant.policy_change_id < current_state.policy_change_id
        ), '[]'::jsonb),
        'classification_sources', COALESCE((
            SELECT pg_catalog.jsonb_agg(
                pg_catalog.jsonb_build_object(
                    'source_kind', classification.source_kind,
                    'source_id', classification.source_id,
                    'classification', classification.classification
                ) ORDER BY classification.classified_at, classification.id
            )
            FROM sklegal_legal.material_classifications AS classification
            WHERE classification.tenant_id = target_tenant_id
              AND classification.matter_id = target_matter_id
              AND classification.material_id = target_material_id
              AND classification.material_version = target_material_version
              AND classification.policy_change_id < current_state.policy_change_id
        ), '[]'::jsonb),
        'privilege_labels', COALESCE((
            SELECT pg_catalog.jsonb_agg(
                pg_catalog.jsonb_build_object(
                    'label_id', label.id,
                    'tenant_id', label.tenant_id,
                    'matter_id', label.matter_id,
                    'material_id', label.material_id,
                    'label', label.label_code,
                    'active', label.active,
                    'labeled_by_principal_id', label.labeled_by_principal_id,
                    'labeled_at', label.labeled_at
                ) ORDER BY label.labeled_at, label.id
            )
            FROM sklegal_legal.material_protection_labels AS label
            WHERE label.tenant_id = target_tenant_id
              AND label.matter_id = target_matter_id
              AND label.material_id = target_material_id
              AND label.material_version = target_material_version
              AND label.label_family = 'privilege'
              AND label.policy_change_id < current_state.policy_change_id
        ), '[]'::jsonb),
        'work_product_labels', COALESCE((
            SELECT pg_catalog.jsonb_agg(
                pg_catalog.jsonb_build_object(
                    'label_id', label.id,
                    'tenant_id', label.tenant_id,
                    'matter_id', label.matter_id,
                    'material_id', label.material_id,
                    'label', label.label_code,
                    'active', label.active,
                    'labeled_by_principal_id', label.labeled_by_principal_id,
                    'labeled_at', label.labeled_at
                ) ORDER BY label.labeled_at, label.id
            )
            FROM sklegal_legal.material_protection_labels AS label
            WHERE label.tenant_id = target_tenant_id
              AND label.matter_id = target_matter_id
              AND label.material_id = target_material_id
              AND label.material_version = target_material_version
              AND label.label_family = 'work_product'
              AND label.policy_change_id < current_state.policy_change_id
        ), '[]'::jsonb),
        'retention_policy', (
            SELECT pg_catalog.jsonb_build_object(
                'retention_policy_id', retention.id,
                'tenant_id', retention.tenant_id,
                'matter_id', retention.matter_id,
                'retain_for_days', retention.retain_for_days,
                'effective_from', retention.effective_from,
                'effective_to', retention.effective_to
            )
            FROM sklegal_legal.retention_policies AS retention
            WHERE retention.tenant_id = target_tenant_id
              AND retention.matter_id = target_matter_id
              AND retention.id = current_state.retention_policy_id
              AND retention.policy_change_id < current_state.policy_change_id
        ),
        'legal_holds', COALESCE((
            SELECT pg_catalog.jsonb_agg(
                pg_catalog.jsonb_build_object(
                    'legal_hold_id', legal_hold.id,
                    'tenant_id', legal_hold.tenant_id,
                    'matter_id', legal_hold.matter_id,
                    'status', legal_hold.status,
                    'scope', legal_hold.hold_scope,
                    'material_id', legal_hold.material_id,
                    'issued_by_principal_id', legal_hold.issued_by_principal_id,
                    'effective_from', legal_hold.effective_from,
                    'supersedes_hold_id', legal_hold.supersedes_hold_id,
                    'released_by_principal_id',
                        legal_hold.released_by_principal_id,
                    'released_at', legal_hold.released_at
                ) ORDER BY legal_hold.effective_from, legal_hold.id
            )
            FROM sklegal_legal.legal_holds AS legal_hold
            WHERE legal_hold.tenant_id = target_tenant_id
              AND legal_hold.matter_id = target_matter_id
              AND (
                  legal_hold.hold_scope = 'matter'
                  OR legal_hold.material_id = target_material_id
              )
              AND legal_hold.policy_change_id < current_state.policy_change_id
        ), '[]'::jsonb),
        'legal_hold_state_complete', current_state.legal_hold_state_complete,
        'ownership_resolved', current_state.ownership_resolved,
        'pending_export', current_state.pending_export,
        'preservation_required', current_state.preservation_required,
        'profile_owner_principal_id', NULL
    ) INTO snapshot;
    RETURN snapshot;
END;
$function$;

REVOKE ALL ON FUNCTION sklegal_legal.material_policy_snapshot(
    uuid, uuid, uuid, bigint, uuid
) FROM PUBLIC;

REVOKE ALL ON ALL TABLES IN SCHEMA sklegal_legal FROM PUBLIC;
REVOKE ALL ON SEQUENCE sklegal_legal.policy_change_sequence FROM PUBLIC;

-- sklegal:down
DROP FUNCTION sklegal_legal.material_policy_snapshot(uuid, uuid, uuid, bigint, uuid);
DROP TRIGGER matter_policy_state_head ON sklegal_legal.matter_policy_states;
DROP TRIGGER retention_policy_head ON sklegal_legal.retention_policies;
DROP TRIGGER legal_hold_release_evidence ON sklegal_legal.legal_holds;
DROP TRIGGER conflict_hold_evidence ON sklegal_legal.conflict_holds;
DROP TRIGGER conflict_decision_evidence ON sklegal_legal.conflict_decisions;
DROP FUNCTION sklegal_legal.validate_conflict_hold_evidence();
DROP FUNCTION sklegal_legal.validate_conflict_decision_evidence();
DROP FUNCTION sklegal_legal.validate_legal_hold_release();
DROP FUNCTION sklegal_legal.validate_retention_policy_head();
DROP FUNCTION sklegal_legal.validate_matter_policy_state_head();
DROP INDEX sklegal_legal.matter_policy_state_one_successor_idx;
DROP INDEX sklegal_legal.matter_policy_state_one_genesis_idx;
DROP INDEX sklegal_legal.matter_policy_state_current_idx;
DROP INDEX sklegal_legal.retention_policy_one_successor_idx;
DROP INDEX sklegal_legal.retention_policy_one_genesis_idx;
DROP INDEX sklegal_legal.retention_policy_current_idx;
DROP INDEX sklegal_legal.legal_hold_one_release_idx;
DROP INDEX sklegal_legal.legal_hold_current_idx;
DROP INDEX sklegal_legal.material_protection_current_idx;
DROP INDEX sklegal_legal.material_classification_current_idx;
DROP INDEX sklegal_legal.protected_access_current_idx;
DROP INDEX sklegal_legal.ethical_wall_current_idx;
DROP INDEX sklegal_legal.conflict_hold_current_idx;
DROP INDEX sklegal_legal.conflict_decision_one_successor_idx;
DROP INDEX sklegal_legal.conflict_decision_one_genesis_idx;
DROP INDEX sklegal_legal.conflict_decision_current_idx;
DROP INDEX sklegal_legal.party_association_collision_idx;
DROP INDEX sklegal_legal.party_normalization_collision_idx;

DROP TABLE sklegal_legal.matter_policy_states;
DROP TABLE sklegal_legal.legal_holds;
DROP TABLE sklegal_legal.retention_policies;
DROP TABLE sklegal_legal.material_protection_labels;
DROP TABLE sklegal_legal.material_classifications;
DROP TABLE sklegal_legal.protected_access_grants;
DROP TABLE sklegal_legal.ethical_wall_memberships;
DROP TABLE sklegal_legal.ethical_walls;
DROP TABLE sklegal_legal.conflict_holds;
DROP TABLE sklegal_legal.conflict_decisions;
DROP TABLE sklegal_legal.conflict_waiver_references;
DROP TABLE sklegal_legal.conflict_matches;
DROP TABLE sklegal_legal.conflict_checks;
DROP TABLE sklegal_legal.party_associations;
DROP TABLE sklegal_legal.party_normalizations;
DROP FUNCTION sklegal_legal.assign_policy_change_id();
DROP SEQUENCE sklegal_legal.policy_change_sequence;
