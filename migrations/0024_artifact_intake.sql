-- sklegal:up
CREATE SCHEMA sklegal_artifact;

CREATE TABLE sklegal_artifact.artifacts (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    id uuid NOT NULL,
    artifact_kind text NOT NULL CHECK (artifact_kind IN ('original', 'derived')),
    parent_artifact_id uuid,
    source_identity text NOT NULL CHECK (length(btrim(source_identity)) BETWEEN 1 AND 255),
    source_version text NOT NULL CHECK (length(btrim(source_version)) BETWEEN 1 AND 255),
    source_identity_sha256 sklegal_legal.sha256_digest NOT NULL,
    filename text NOT NULL CHECK (length(btrim(filename)) BETWEEN 1 AND 512),
    media_type text NOT NULL CHECK (media_type ~ '^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$'),
    byte_count bigint NOT NULL CHECK (byte_count >= 0),
    content_sha256 sklegal_legal.sha256_digest NOT NULL,
    original_sha256 sklegal_legal.sha256_digest NOT NULL,
    storage_locator text NOT NULL CHECK (length(btrim(storage_locator)) BETWEEN 1 AND 512),
    acquisition_method text NOT NULL CHECK (acquisition_method IN (
        'synthetic_adapter', 'user_upload', 'document_import',
        'recording_import', 'derived', 'human_correction'
    )),
    quarantine_state text NOT NULL CHECK (quarantine_state IN ('quarantined', 'released', 'rejected')),
    extraction_state text NOT NULL CHECK (extraction_state IN ('not_requested', 'pending', 'complete', 'failed')),
    classification sklegal_legal.data_classification NOT NULL,
    privilege_state text NOT NULL CHECK (privilege_state IN ('not_privileged', 'privilege_claimed', 'privilege_reviewed')),
    retention_policy_id uuid NOT NULL,
    legal_hold_ids uuid[] NOT NULL DEFAULT '{}',
    ethical_wall_ids uuid[] NOT NULL DEFAULT '{}',
    policy_decision_id uuid NOT NULL,
    policy_revision sklegal_legal.sha256_digest NOT NULL,
    projection_revision bigint NOT NULL DEFAULT 1 CHECK (projection_revision >= 1),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, content_sha256),
    FOREIGN KEY (tenant_id, matter_id)
        REFERENCES sklegal_legal.matters(tenant_id, matter_id),
    FOREIGN KEY (tenant_id, matter_id, parent_artifact_id)
        REFERENCES sklegal_artifact.artifacts(tenant_id, matter_id, id),
    CHECK (
        (artifact_kind = 'original' AND parent_artifact_id IS NULL AND content_sha256 = original_sha256)
        OR (artifact_kind = 'derived' AND parent_artifact_id IS NOT NULL)
    )
);

CREATE TABLE sklegal_artifact.artifact_projection_revisions (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    artifact_id uuid NOT NULL,
    revision bigint NOT NULL CHECK (revision >= 1),
    review_state text NOT NULL CHECK (review_state IN ('proposed', 'accepted', 'changes_requested', 'rejected', 'superseded')),
    reviewed_by_principal_id uuid,
    reviewed_at timestamptz,
    recorded_at timestamptz NOT NULL,
    projection_document jsonb,
    PRIMARY KEY (tenant_id, matter_id, artifact_id, revision),
    FOREIGN KEY (tenant_id, matter_id, artifact_id)
        REFERENCES sklegal_artifact.artifacts(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, reviewed_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (
        (review_state = 'proposed'
         AND reviewed_at IS NULL
         AND reviewed_by_principal_id IS NULL)
        OR
        (review_state <> 'proposed'
         AND reviewed_at IS NOT NULL
         AND reviewed_by_principal_id IS NOT NULL)
    ),
    CHECK (
        projection_document IS NULL
        OR jsonb_typeof(projection_document) = 'object'
    )
);

CREATE FUNCTION sklegal_artifact.validate_artifact_governance()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF current_user <> 'sklegal_migrator'
       OR session_user = current_user
       OR NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_identity.record_is_authorized(NEW.tenant_id, NEW.matter_id)
    THEN
        RAISE EXCEPTION 'artifact governance runtime boundary denied'
            USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM sklegal_legal.retention_policies AS retention
        WHERE retention.tenant_id = NEW.tenant_id
          AND retention.matter_id = NEW.matter_id
          AND retention.id = NEW.retention_policy_id
          AND retention.effective_from <= NEW.created_at
          AND (retention.effective_to IS NULL OR retention.effective_to > NEW.created_at)
          AND NOT EXISTS (
              SELECT 1
              FROM sklegal_legal.retention_policies AS successor
              WHERE successor.tenant_id = retention.tenant_id
                AND successor.matter_id = retention.matter_id
                AND successor.supersedes_retention_policy_id = retention.id
          )
    ) THEN
        RAISE EXCEPTION 'artifact binds a stale or cross-scope retention policy'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.unnest(NEW.legal_hold_ids) AS requested(id)
        WHERE NOT EXISTS (
            SELECT 1
            FROM sklegal_legal.legal_holds AS hold
            WHERE hold.tenant_id = NEW.tenant_id
              AND hold.matter_id = NEW.matter_id
              AND hold.id = requested.id
              AND hold.status = 'active'
              AND hold.effective_from <= NEW.created_at
              AND NOT EXISTS (
                  SELECT 1
                  FROM sklegal_legal.legal_holds AS release
                  WHERE release.tenant_id = hold.tenant_id
                    AND release.matter_id = hold.matter_id
                    AND release.supersedes_hold_id = hold.id
              )
        )
    ) THEN
        RAISE EXCEPTION 'artifact binds a stale, released, or cross-scope legal hold'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.unnest(NEW.ethical_wall_ids) AS requested(id)
        WHERE NOT EXISTS (
            SELECT 1
            FROM sklegal_legal.ethical_walls AS wall
            WHERE wall.tenant_id = NEW.tenant_id
              AND wall.matter_id = NEW.matter_id
              AND wall.id = requested.id
              AND wall.active
              AND wall.membership_complete
              AND wall.effective_from <= NEW.created_at
              AND (wall.effective_to IS NULL OR wall.effective_to > NEW.created_at)
        )
    ) THEN
        RAISE EXCEPTION 'artifact binds an inactive or cross-scope ethical wall'
            USING ERRCODE = '23514';
    END IF;

    IF pg_catalog.cardinality(NEW.legal_hold_ids) <> (
        SELECT pg_catalog.count(DISTINCT value.id)
        FROM pg_catalog.unnest(NEW.legal_hold_ids) AS value(id)
    ) OR pg_catalog.cardinality(NEW.ethical_wall_ids) <> (
        SELECT pg_catalog.count(DISTINCT value.id)
        FROM pg_catalog.unnest(NEW.ethical_wall_ids) AS value(id)
    ) THEN
        RAISE EXCEPTION 'artifact governance identifiers must be unique'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

ALTER FUNCTION sklegal_artifact.validate_artifact_governance() OWNER TO sklegal_migrator;

REVOKE ALL ON FUNCTION sklegal_artifact.validate_artifact_governance()
FROM PUBLIC;

CREATE TRIGGER artifact_governance_current
BEFORE INSERT ON sklegal_artifact.artifacts
FOR EACH ROW EXECUTE FUNCTION sklegal_artifact.validate_artifact_governance();

CREATE FUNCTION sklegal_artifact.validate_projection_revision()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    prior_revision bigint;
    artifact_kind_value text;
BEGIN
    IF current_user <> 'sklegal_migrator'
       OR session_user = current_user
       OR NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_identity.record_is_authorized(NEW.tenant_id, NEW.matter_id)
    THEN
        RAISE EXCEPTION 'artifact projection runtime boundary denied'
            USING ERRCODE = '42501';
    END IF;
    SELECT artifact.artifact_kind
      INTO artifact_kind_value
      FROM sklegal_artifact.artifacts AS artifact
     WHERE artifact.tenant_id = NEW.tenant_id
       AND artifact.matter_id = NEW.matter_id
       AND artifact.id = NEW.artifact_id;
    IF artifact_kind_value IS NULL THEN
        RAISE EXCEPTION 'artifact projection has no scoped artifact'
            USING ERRCODE = '23503';
    END IF;
    SELECT pg_catalog.max(revision)
      INTO prior_revision
      FROM sklegal_artifact.artifact_projection_revisions
     WHERE tenant_id = NEW.tenant_id
       AND matter_id = NEW.matter_id
       AND artifact_id = NEW.artifact_id;
    IF (prior_revision IS NULL AND NEW.revision <> 1)
       OR (prior_revision IS NOT NULL AND NEW.revision <> prior_revision + 1)
    THEN
        RAISE EXCEPTION 'artifact projection revision is not sequential'
            USING ERRCODE = '23514';
    END IF;
    IF artifact_kind_value = 'original' AND NEW.review_state = 'superseded' THEN
        RAISE EXCEPTION 'original artifact cannot be superseded'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

ALTER FUNCTION sklegal_artifact.validate_projection_revision() OWNER TO sklegal_migrator;
REVOKE ALL ON FUNCTION sklegal_artifact.validate_projection_revision() FROM PUBLIC;
CREATE TRIGGER artifact_projection_revision_valid
BEFORE INSERT ON sklegal_artifact.artifact_projection_revisions
FOR EACH ROW EXECUTE FUNCTION sklegal_artifact.validate_projection_revision();

CREATE TABLE sklegal_artifact.artifact_scan_events (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    id uuid NOT NULL,
    artifact_id uuid NOT NULL,
    scanner_name text NOT NULL CHECK (length(btrim(scanner_name)) BETWEEN 1 AND 255),
    scanner_version text NOT NULL CHECK (length(btrim(scanner_version)) BETWEEN 1 AND 255),
    signature_revision sklegal_legal.sha256_digest NOT NULL,
    scan_state text NOT NULL CHECK (scan_state IN ('pending', 'clean', 'unsafe', 'failed')),
    detail_code text NOT NULL CHECK (detail_code IN ('clean', 'pending', 'unsafe', 'scanner_failed')),
    scanned_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, artifact_id)
        REFERENCES sklegal_artifact.artifacts(tenant_id, matter_id, id)
);

CREATE TABLE sklegal_artifact.artifact_custody_events (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    id uuid NOT NULL,
    artifact_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('acquired', 'duplicate_observed', 'derived', 'corrected')),
    custodian_principal_id uuid NOT NULL,
    source_identity text NOT NULL CHECK (length(btrim(source_identity)) BETWEEN 1 AND 255),
    idempotency_key_sha256 sklegal_legal.sha256_digest NOT NULL,
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, artifact_id, idempotency_key_sha256),
    FOREIGN KEY (tenant_id, matter_id, artifact_id)
        REFERENCES sklegal_artifact.artifacts(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, custodian_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id)
);

CREATE TABLE sklegal_artifact.artifact_derivations (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    parent_artifact_id uuid NOT NULL,
    child_artifact_id uuid NOT NULL,
    derivation_kind text NOT NULL CHECK (derivation_kind IN ('text_extraction', 'ocr', 'transcript', 'human_correction')),
    tool_name text NOT NULL CHECK (length(btrim(tool_name)) BETWEEN 1 AND 255),
    tool_version text NOT NULL CHECK (length(btrim(tool_version)) BETWEEN 1 AND 255),
    input_sha256 sklegal_legal.sha256_digest NOT NULL,
    output_sha256 sklegal_legal.sha256_digest NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, child_artifact_id),
    FOREIGN KEY (tenant_id, matter_id, parent_artifact_id)
        REFERENCES sklegal_artifact.artifacts(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, child_artifact_id)
        REFERENCES sklegal_artifact.artifacts(tenant_id, matter_id, id),
    CHECK (parent_artifact_id <> child_artifact_id)
);

CREATE FUNCTION sklegal_artifact.validate_derivation_hashes()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    parent_content sklegal_legal.sha256_digest;
    parent_original sklegal_legal.sha256_digest;
    child_content sklegal_legal.sha256_digest;
    child_original sklegal_legal.sha256_digest;
    child_parent uuid;
    child_kind text;
BEGIN
    IF current_user <> 'sklegal_migrator'
       OR session_user = current_user
       OR NOT sklegal_identity.runtime_role_is_safe()
       OR NOT sklegal_identity.record_is_authorized(NEW.tenant_id, NEW.matter_id)
    THEN
        RAISE EXCEPTION 'artifact lineage runtime boundary denied'
            USING ERRCODE = '42501';
    END IF;
    SELECT content_sha256, original_sha256
      INTO parent_content, parent_original
      FROM sklegal_artifact.artifacts
     WHERE tenant_id = NEW.tenant_id
       AND matter_id = NEW.matter_id
       AND id = NEW.parent_artifact_id;
    SELECT content_sha256, original_sha256, parent_artifact_id, artifact_kind
      INTO child_content, child_original, child_parent, child_kind
      FROM sklegal_artifact.artifacts
     WHERE tenant_id = NEW.tenant_id
       AND matter_id = NEW.matter_id
       AND id = NEW.child_artifact_id;
    IF parent_content IS NULL OR child_content IS NULL
       OR child_kind <> 'derived'
       OR child_parent IS DISTINCT FROM NEW.parent_artifact_id
       OR NEW.input_sha256 IS DISTINCT FROM parent_content
       OR NEW.output_sha256 IS DISTINCT FROM child_content
       OR child_original IS DISTINCT FROM parent_original
    THEN
        RAISE EXCEPTION 'artifact derivation is not content-hash connected'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

ALTER FUNCTION sklegal_artifact.validate_derivation_hashes() OWNER TO sklegal_migrator;
REVOKE ALL ON FUNCTION sklegal_artifact.validate_derivation_hashes() FROM PUBLIC;
CREATE TRIGGER artifact_derivation_hashes_valid
BEFORE INSERT ON sklegal_artifact.artifact_derivations
FOR EACH ROW EXECUTE FUNCTION sklegal_artifact.validate_derivation_hashes();

CREATE FUNCTION sklegal_artifact.require_derived_lineage()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.artifact_kind = 'derived' AND NOT EXISTS (
        SELECT 1
          FROM sklegal_artifact.artifact_derivations AS edge
         WHERE edge.tenant_id = NEW.tenant_id
           AND edge.matter_id = NEW.matter_id
           AND edge.child_artifact_id = NEW.id
           AND edge.parent_artifact_id = NEW.parent_artifact_id
           AND edge.input_sha256 = (
               SELECT parent.content_sha256
                 FROM sklegal_artifact.artifacts AS parent
                WHERE parent.tenant_id = NEW.tenant_id
                  AND parent.matter_id = NEW.matter_id
                  AND parent.id = NEW.parent_artifact_id
           )
           AND edge.output_sha256 = NEW.content_sha256
    ) THEN
        RAISE EXCEPTION 'derived artifact has no content-hash-connected lineage'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$function$;

ALTER FUNCTION sklegal_artifact.require_derived_lineage() OWNER TO sklegal_migrator;
REVOKE ALL ON FUNCTION sklegal_artifact.require_derived_lineage() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER artifact_derived_lineage_required
AFTER INSERT ON sklegal_artifact.artifacts
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sklegal_artifact.require_derived_lineage();

CREATE TABLE sklegal_artifact.artifact_record_links (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    id uuid NOT NULL,
    artifact_id uuid NOT NULL,
    target_type text NOT NULL CHECK (target_type IN (
        'matter_event', 'communication', 'fact_assertion', 'evidence_item',
        'issue', 'claim', 'element', 'task', 'work_product'
    )),
    target_id uuid NOT NULL,
    rationale text NOT NULL CHECK (length(btrim(rationale)) BETWEEN 1 AND 512),
    proposed_by_principal_id uuid NOT NULL,
    review_state text NOT NULL DEFAULT 'proposed' CHECK (review_state IN ('proposed', 'accepted', 'changes_requested', 'rejected', 'superseded')),
    reviewed_by_principal_id uuid,
    proposed_at timestamptz NOT NULL,
    reviewed_at timestamptz,
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, artifact_id)
        REFERENCES sklegal_artifact.artifacts(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, proposed_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    FOREIGN KEY (tenant_id, reviewed_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (
        (review_state = 'proposed'
         AND reviewed_at IS NULL
         AND reviewed_by_principal_id IS NULL)
        OR
        (review_state <> 'proposed'
         AND reviewed_at IS NOT NULL
         AND reviewed_by_principal_id IS NOT NULL)
    )
);

CREATE TABLE sklegal_artifact.artifact_reviews (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    id uuid NOT NULL,
    artifact_id uuid NOT NULL,
    artifact_projection_revision bigint NOT NULL CHECK (artifact_projection_revision >= 1),
    decision text NOT NULL CHECK (decision IN ('accepted', 'changes_requested', 'rejected')),
    rationale text NOT NULL CHECK (length(btrim(rationale)) BETWEEN 1 AND 512),
    reviewer_principal_id uuid NOT NULL,
    policy_decision_id uuid NOT NULL,
    policy_revision sklegal_legal.sha256_digest NOT NULL,
    reviewed_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, artifact_id, artifact_projection_revision),
    FOREIGN KEY (tenant_id, matter_id, artifact_id)
        REFERENCES sklegal_artifact.artifacts(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, reviewer_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id)
);

CREATE TABLE sklegal_artifact.artifact_corrections (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    id uuid NOT NULL,
    target_artifact_id uuid NOT NULL,
    corrected_artifact_id uuid NOT NULL,
    reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 512),
    corrected_by_principal_id uuid NOT NULL,
    corrected_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, corrected_artifact_id),
    FOREIGN KEY (tenant_id, matter_id, target_artifact_id)
        REFERENCES sklegal_artifact.artifacts(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, corrected_artifact_id)
        REFERENCES sklegal_artifact.artifacts(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, corrected_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (target_artifact_id <> corrected_artifact_id)
);

CREATE TABLE sklegal_artifact.artifact_supersessions (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    id uuid NOT NULL,
    superseded_artifact_id uuid NOT NULL,
    successor_artifact_id uuid NOT NULL,
    reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 512),
    recorded_by_principal_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, superseded_artifact_id),
    FOREIGN KEY (tenant_id, matter_id, superseded_artifact_id)
        REFERENCES sklegal_artifact.artifacts(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, matter_id, successor_artifact_id)
        REFERENCES sklegal_artifact.artifacts(tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, recorded_by_principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    CHECK (superseded_artifact_id <> successor_artifact_id)
);

CREATE FUNCTION sklegal_artifact.validate_artifact_review()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    current_revision bigint;
    current_state text;
BEGIN
    SELECT revision, review_state
      INTO current_revision, current_state
      FROM sklegal_artifact.artifact_projection_revisions
     WHERE tenant_id = NEW.tenant_id
       AND matter_id = NEW.matter_id
       AND artifact_id = NEW.artifact_id
     ORDER BY revision DESC
     LIMIT 1;
    IF current_revision IS NULL
       OR NEW.artifact_projection_revision <> current_revision
       OR current_state = 'superseded'
       OR EXISTS (
           SELECT 1
             FROM sklegal_artifact.artifact_supersessions
            WHERE tenant_id = NEW.tenant_id
              AND matter_id = NEW.matter_id
              AND superseded_artifact_id = NEW.artifact_id
       )
    THEN
        RAISE EXCEPTION 'artifact review is stale or targets a superseded artifact'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

ALTER FUNCTION sklegal_artifact.validate_artifact_review() OWNER TO sklegal_migrator;
REVOKE ALL ON FUNCTION sklegal_artifact.validate_artifact_review() FROM PUBLIC;
CREATE TRIGGER artifact_review_current
BEFORE INSERT ON sklegal_artifact.artifact_reviews
FOR EACH ROW EXECUTE FUNCTION sklegal_artifact.validate_artifact_review();

CREATE FUNCTION sklegal_artifact.validate_artifact_correction()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    target_kind text;
    target_original sklegal_legal.sha256_digest;
    corrected_kind text;
    corrected_original sklegal_legal.sha256_digest;
    corrected_parent uuid;
    corrected_derivation text;
BEGIN
    SELECT artifact_kind, original_sha256
      INTO target_kind, target_original
      FROM sklegal_artifact.artifacts
     WHERE tenant_id = NEW.tenant_id
       AND matter_id = NEW.matter_id
       AND id = NEW.target_artifact_id;
    SELECT artifact.artifact_kind, artifact.original_sha256,
           artifact.parent_artifact_id, edge.derivation_kind
      INTO corrected_kind, corrected_original, corrected_parent, corrected_derivation
      FROM sklegal_artifact.artifacts AS artifact
      JOIN sklegal_artifact.artifact_derivations AS edge
        ON edge.tenant_id = artifact.tenant_id
       AND edge.matter_id = artifact.matter_id
       AND edge.child_artifact_id = artifact.id
     WHERE artifact.tenant_id = NEW.tenant_id
       AND artifact.matter_id = NEW.matter_id
       AND artifact.id = NEW.corrected_artifact_id;
    IF target_kind IS DISTINCT FROM 'derived'
       OR corrected_kind IS DISTINCT FROM 'derived'
       OR corrected_parent IS DISTINCT FROM NEW.target_artifact_id
       OR corrected_derivation IS DISTINCT FROM 'human_correction'
       OR corrected_original IS DISTINCT FROM target_original
       OR EXISTS (
           SELECT 1 FROM sklegal_artifact.artifact_supersessions
            WHERE tenant_id = NEW.tenant_id
              AND matter_id = NEW.matter_id
              AND superseded_artifact_id = NEW.target_artifact_id
       )
    THEN
        RAISE EXCEPTION 'artifact correction lifecycle is invalid'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

ALTER FUNCTION sklegal_artifact.validate_artifact_correction() OWNER TO sklegal_migrator;
REVOKE ALL ON FUNCTION sklegal_artifact.validate_artifact_correction() FROM PUBLIC;
CREATE TRIGGER artifact_correction_valid
BEFORE INSERT ON sklegal_artifact.artifact_corrections
FOR EACH ROW EXECUTE FUNCTION sklegal_artifact.validate_artifact_correction();

CREATE FUNCTION sklegal_artifact.validate_artifact_supersession()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    superseded_kind text;
    superseded_original sklegal_legal.sha256_digest;
    successor_kind text;
    successor_original sklegal_legal.sha256_digest;
BEGIN
    SELECT artifact_kind, original_sha256
      INTO superseded_kind, superseded_original
      FROM sklegal_artifact.artifacts
     WHERE tenant_id = NEW.tenant_id
       AND matter_id = NEW.matter_id
       AND id = NEW.superseded_artifact_id;
    SELECT artifact_kind, original_sha256
      INTO successor_kind, successor_original
      FROM sklegal_artifact.artifacts
     WHERE tenant_id = NEW.tenant_id
       AND matter_id = NEW.matter_id
       AND id = NEW.successor_artifact_id;
    IF superseded_kind IS DISTINCT FROM 'derived'
       OR successor_kind IS DISTINCT FROM 'derived'
       OR superseded_original IS DISTINCT FROM successor_original
       OR EXISTS (
           SELECT 1 FROM sklegal_artifact.artifact_supersessions
            WHERE tenant_id = NEW.tenant_id
              AND matter_id = NEW.matter_id
              AND superseded_artifact_id IN (
                  NEW.superseded_artifact_id, NEW.successor_artifact_id
              )
       )
    THEN
        RAISE EXCEPTION 'artifact supersession lifecycle is invalid'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

ALTER FUNCTION sklegal_artifact.validate_artifact_supersession() OWNER TO sklegal_migrator;
REVOKE ALL ON FUNCTION sklegal_artifact.validate_artifact_supersession() FROM PUBLIC;
CREATE TRIGGER artifact_supersession_valid
BEFORE INSERT ON sklegal_artifact.artifact_supersessions
FOR EACH ROW EXECUTE FUNCTION sklegal_artifact.validate_artifact_supersession();

CREATE TABLE sklegal_artifact.artifact_idempotency_receipts (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    idempotency_key_sha256 sklegal_legal.sha256_digest NOT NULL,
    operation text NOT NULL CHECK (operation IN ('create', 'review', 'correct', 'supersede')),
    request_sha256 sklegal_legal.sha256_digest NOT NULL,
    result_artifact_id uuid NOT NULL,
    duplicate boolean NOT NULL,
    projection_revision bigint NOT NULL CHECK (projection_revision >= 1),
    created_at timestamptz NOT NULL,
    response_document jsonb,
    PRIMARY KEY (tenant_id, matter_id, idempotency_key_sha256),
    FOREIGN KEY (tenant_id, matter_id, result_artifact_id)
        REFERENCES sklegal_artifact.artifacts(tenant_id, matter_id, id),
    CHECK (
        response_document IS NULL
        OR jsonb_typeof(response_document) = 'object'
    )
);

CREATE TABLE sklegal_artifact.artifact_audit_facts (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    id uuid NOT NULL,
    principal_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('artifact.intake.recorded', 'artifact.read', 'artifact.review.recorded', 'artifact.correction.recorded', 'artifact.supersession.recorded')),
    resource_id uuid NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('success', 'failure', 'deny')),
    policy_decision_id uuid NOT NULL,
    policy_revision sklegal_legal.sha256_digest NOT NULL,
    request_sha256 sklegal_legal.sha256_digest NOT NULL,
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, id),
    FOREIGN KEY (tenant_id, principal_id)
        REFERENCES sklegal_identity.principals(tenant_id, id),
    FOREIGN KEY (tenant_id, matter_id, resource_id)
        REFERENCES sklegal_artifact.artifacts(tenant_id, matter_id, id)
);

CREATE TABLE sklegal_artifact.artifact_outbox (
    tenant_id uuid NOT NULL,
    matter_id uuid NOT NULL,
    id uuid NOT NULL,
    audit_event_id uuid NOT NULL,
    destination text NOT NULL CHECK (destination = 'artifact.activity.local'),
    payload_sha256 sklegal_legal.sha256_digest NOT NULL,
    available_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, matter_id, id),
    UNIQUE (tenant_id, matter_id, audit_event_id, destination),
    FOREIGN KEY (tenant_id, matter_id, audit_event_id)
        REFERENCES sklegal_artifact.artifact_audit_facts(tenant_id, matter_id, id)
);

CREATE TRIGGER artifacts_append_only
BEFORE UPDATE OR DELETE ON sklegal_artifact.artifacts
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER artifact_projection_revisions_append_only
BEFORE UPDATE OR DELETE ON sklegal_artifact.artifact_projection_revisions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER artifact_scan_events_append_only
BEFORE UPDATE OR DELETE ON sklegal_artifact.artifact_scan_events
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER artifact_custody_events_append_only
BEFORE UPDATE OR DELETE ON sklegal_artifact.artifact_custody_events
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER artifact_derivations_append_only
BEFORE UPDATE OR DELETE ON sklegal_artifact.artifact_derivations
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER artifact_record_links_append_only
BEFORE UPDATE OR DELETE ON sklegal_artifact.artifact_record_links
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER artifact_reviews_append_only
BEFORE UPDATE OR DELETE ON sklegal_artifact.artifact_reviews
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER artifact_corrections_append_only
BEFORE UPDATE OR DELETE ON sklegal_artifact.artifact_corrections
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER artifact_supersessions_append_only
BEFORE UPDATE OR DELETE ON sklegal_artifact.artifact_supersessions
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER artifact_idempotency_receipts_append_only
BEFORE UPDATE OR DELETE ON sklegal_artifact.artifact_idempotency_receipts
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER artifact_audit_facts_append_only
BEFORE UPDATE OR DELETE ON sklegal_artifact.artifact_audit_facts
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();
CREATE TRIGGER artifact_outbox_append_only
BEFORE UPDATE OR DELETE ON sklegal_artifact.artifact_outbox
FOR EACH ROW EXECUTE FUNCTION sklegal_legal.reject_record_change();

ALTER TABLE sklegal_artifact.artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifacts FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_projection_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_projection_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_scan_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_scan_events FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_custody_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_custody_events FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_derivations ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_derivations FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_record_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_record_links FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_reviews FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_corrections ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_corrections FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_supersessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_supersessions FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_idempotency_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_idempotency_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_audit_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_audit_facts FORCE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE sklegal_artifact.artifact_outbox FORCE ROW LEVEL SECURITY;

CREATE POLICY artifacts_select
ON sklegal_artifact.artifacts FOR SELECT
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifacts_insert
ON sklegal_artifact.artifacts FOR INSERT
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_projection_revisions_select
ON sklegal_artifact.artifact_projection_revisions FOR SELECT
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_projection_revisions_insert
ON sklegal_artifact.artifact_projection_revisions FOR INSERT
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_scan_events_select
ON sklegal_artifact.artifact_scan_events FOR SELECT
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_scan_events_insert
ON sklegal_artifact.artifact_scan_events FOR INSERT
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_custody_events_select
ON sklegal_artifact.artifact_custody_events FOR SELECT
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_custody_events_insert
ON sklegal_artifact.artifact_custody_events FOR INSERT
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_derivations_select
ON sklegal_artifact.artifact_derivations FOR SELECT
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_derivations_insert
ON sklegal_artifact.artifact_derivations FOR INSERT
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_record_links_select
ON sklegal_artifact.artifact_record_links FOR SELECT
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_record_links_insert
ON sklegal_artifact.artifact_record_links FOR INSERT
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_reviews_select
ON sklegal_artifact.artifact_reviews FOR SELECT
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_reviews_insert
ON sklegal_artifact.artifact_reviews FOR INSERT
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_corrections_select
ON sklegal_artifact.artifact_corrections FOR SELECT
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_corrections_insert
ON sklegal_artifact.artifact_corrections FOR INSERT
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_supersessions_select
ON sklegal_artifact.artifact_supersessions FOR SELECT
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_supersessions_insert
ON sklegal_artifact.artifact_supersessions FOR INSERT
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_idempotency_receipts_select
ON sklegal_artifact.artifact_idempotency_receipts FOR SELECT
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_idempotency_receipts_insert
ON sklegal_artifact.artifact_idempotency_receipts FOR INSERT
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_audit_facts_select
ON sklegal_artifact.artifact_audit_facts FOR SELECT
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_audit_facts_insert
ON sklegal_artifact.artifact_audit_facts FOR INSERT
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_outbox_select
ON sklegal_artifact.artifact_outbox FOR SELECT
USING (sklegal_identity.record_is_authorized(tenant_id, matter_id));
CREATE POLICY artifact_outbox_insert
ON sklegal_artifact.artifact_outbox FOR INSERT
WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id));

REVOKE ALL ON SCHEMA sklegal_artifact FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA sklegal_artifact FROM PUBLIC;
GRANT USAGE ON SCHEMA sklegal_artifact TO sklegal_runtime;
GRANT USAGE ON SCHEMA sklegal_identity, sklegal_legal TO sklegal_runtime;
GRANT USAGE ON DOMAIN
    sklegal_legal.sha256_digest,
    sklegal_legal.data_classification
TO sklegal_runtime;
GRANT EXECUTE ON FUNCTION
    sklegal_identity.current_tenant_id(),
    sklegal_identity.current_principal_id(),
    sklegal_identity.has_tenant_membership(uuid),
    sklegal_identity.runtime_role_is_safe(),
    sklegal_identity.record_is_authorized(uuid, uuid),
    sklegal_legal.has_matter_membership(uuid, uuid)
TO sklegal_runtime;
GRANT SELECT ON
    sklegal_identity.database_role_bindings,
    sklegal_identity.tenant_memberships,
    sklegal_legal.matter_memberships
TO sklegal_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA sklegal_artifact TO sklegal_runtime;
GRANT INSERT ON
    sklegal_artifact.artifacts,
    sklegal_artifact.artifact_projection_revisions,
    sklegal_artifact.artifact_scan_events,
    sklegal_artifact.artifact_custody_events,
    sklegal_artifact.artifact_derivations,
    sklegal_artifact.artifact_record_links,
    sklegal_artifact.artifact_reviews,
    sklegal_artifact.artifact_corrections,
    sklegal_artifact.artifact_supersessions,
    sklegal_artifact.artifact_idempotency_receipts,
    sklegal_artifact.artifact_audit_facts,
    sklegal_artifact.artifact_outbox
TO sklegal_runtime;

CREATE INDEX artifact_original_hash_idx
ON sklegal_artifact.artifacts (tenant_id, matter_id, original_sha256);
CREATE INDEX artifact_parent_idx
ON sklegal_artifact.artifacts (tenant_id, matter_id, parent_artifact_id)
WHERE parent_artifact_id IS NOT NULL;
CREATE INDEX artifact_custody_timeline_idx
ON sklegal_artifact.artifact_custody_events (tenant_id, matter_id, artifact_id, occurred_at);
CREATE INDEX artifact_outbox_pending_idx
ON sklegal_artifact.artifact_outbox (tenant_id, matter_id, available_at);

-- sklegal:down
REVOKE SELECT ON
    sklegal_identity.database_role_bindings,
    sklegal_identity.tenant_memberships,
    sklegal_legal.matter_memberships
FROM sklegal_runtime;
REVOKE EXECUTE ON FUNCTION
    sklegal_identity.current_tenant_id(),
    sklegal_identity.current_principal_id(),
    sklegal_identity.has_tenant_membership(uuid),
    sklegal_identity.runtime_role_is_safe(),
    sklegal_identity.record_is_authorized(uuid, uuid),
    sklegal_legal.has_matter_membership(uuid, uuid)
FROM sklegal_runtime;
REVOKE USAGE ON DOMAIN
    sklegal_legal.sha256_digest,
    sklegal_legal.data_classification
FROM sklegal_runtime;
DROP INDEX sklegal_artifact.artifact_outbox_pending_idx;
DROP INDEX sklegal_artifact.artifact_custody_timeline_idx;
DROP INDEX sklegal_artifact.artifact_parent_idx;
DROP INDEX sklegal_artifact.artifact_original_hash_idx;
DROP TABLE sklegal_artifact.artifact_outbox;
DROP TABLE sklegal_artifact.artifact_audit_facts;
DROP TABLE sklegal_artifact.artifact_idempotency_receipts;
DROP TABLE sklegal_artifact.artifact_supersessions;
DROP TABLE sklegal_artifact.artifact_corrections;
DROP TABLE sklegal_artifact.artifact_reviews;
DROP TABLE sklegal_artifact.artifact_record_links;
DROP TABLE sklegal_artifact.artifact_derivations;
DROP TABLE sklegal_artifact.artifact_custody_events;
DROP TABLE sklegal_artifact.artifact_scan_events;
DROP TABLE sklegal_artifact.artifact_projection_revisions;
DROP TABLE sklegal_artifact.artifacts;
REVOKE ALL ON SCHEMA sklegal_artifact FROM sklegal_runtime;
DROP FUNCTION sklegal_artifact.validate_projection_revision();
DROP FUNCTION sklegal_artifact.validate_artifact_supersession();
DROP FUNCTION sklegal_artifact.validate_artifact_correction();
DROP FUNCTION sklegal_artifact.validate_artifact_review();
DROP FUNCTION sklegal_artifact.require_derived_lineage();
DROP FUNCTION sklegal_artifact.validate_derivation_hashes();
DROP FUNCTION sklegal_artifact.validate_artifact_governance();
DROP SCHEMA sklegal_artifact;
