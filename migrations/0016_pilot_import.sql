-- sklegal:up
CREATE TABLE sklegal_migrations.pilot_import_batches (
    batch_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    source_snapshot text NOT NULL CHECK (length(btrim(source_snapshot)) > 0),
    adapter_version text NOT NULL CHECK (adapter_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
    source_file_count integer NOT NULL CHECK (source_file_count >= 0),
    approved_by text NOT NULL CHECK (length(btrim(approved_by)) > 0),
    approved_at timestamptz NOT NULL,
    review_artifact text NOT NULL CHECK (length(btrim(review_artifact)) > 0),
    status text NOT NULL DEFAULT 'imported' CHECK (status IN ('imported', 'withdrawn')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    withdrawn_at timestamptz,
    PRIMARY KEY (batch_id),
    CHECK ((status = 'withdrawn') = (withdrawn_at IS NOT NULL))
);

CREATE TABLE sklegal_migrations.pilot_import_source_files (
    batch_id uuid NOT NULL,
    relative_path text NOT NULL CHECK (
        length(relative_path) > 0
        AND relative_path = btrim(relative_path)
        AND relative_path ~ '^[^/]+(/[^/]+)*$'
        AND relative_path !~ '\\'
        AND relative_path !~ '(^|/)\.\.?(/|$)'
    ),
    content_sha256 sklegal_legal.sha256_digest NOT NULL,
    byte_count integer CHECK (byte_count IS NULL OR byte_count >= 0),
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (batch_id, relative_path),
    FOREIGN KEY (batch_id) REFERENCES sklegal_migrations.pilot_import_batches(batch_id)
);

CREATE TABLE sklegal_migrations.pilot_import_records (
    idempotency_key sklegal_legal.sha256_digest NOT NULL,
    batch_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    target_type text NOT NULL CHECK (target_type IN ('matter', 'matter_event')),
    target_id uuid NOT NULL,
    source_reference_id uuid NOT NULL,
    mapping_rule text NOT NULL CHECK (length(btrim(mapping_rule)) > 0),
    mapping_status text NOT NULL DEFAULT 'proposed' CHECK (mapping_status = 'proposed'),
    revision integer NOT NULL CHECK (revision >= 1),
    reviewed_by text NOT NULL CHECK (length(btrim(reviewed_by)) > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (idempotency_key),
    UNIQUE (target_type, target_id, revision),
    FOREIGN KEY (batch_id) REFERENCES sklegal_migrations.pilot_import_batches(batch_id)
);

CREATE TABLE sklegal_migrations.pilot_import_facts (
    fact_assertion_id uuid NOT NULL,
    batch_id uuid NOT NULL,
    predicate text NOT NULL CHECK (length(btrim(predicate)) > 0),
    asserted_value jsonb NOT NULL,
    value_type text NOT NULL CHECK (
        value_type IN ('null', 'boolean', 'integer', 'number', 'string')
    ),
    source_reference_id uuid NOT NULL,
    source_locator text NOT NULL CHECK (length(btrim(source_locator)) > 0),
    review_status text NOT NULL DEFAULT 'source_asserted' CHECK (
        review_status IN ('verified', 'source_asserted', 'ambiguous', 'superseded')
    ),
    tension_group_key text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (fact_assertion_id),
    FOREIGN KEY (batch_id) REFERENCES sklegal_migrations.pilot_import_batches(batch_id)
);

CREATE TABLE sklegal_migrations.pilot_import_tension_groups (
    tension_key text NOT NULL CHECK (length(btrim(tension_key)) > 0),
    batch_id uuid NOT NULL,
    assertion_ids uuid[] NOT NULL CHECK (cardinality(assertion_ids) >= 2),
    status text NOT NULL DEFAULT 'unresolved' CHECK (status = 'unresolved'),
    review_required boolean NOT NULL DEFAULT true CHECK (review_required),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tension_key, batch_id),
    FOREIGN KEY (batch_id) REFERENCES sklegal_migrations.pilot_import_batches(batch_id)
);

CREATE TABLE sklegal_migrations.pilot_import_states (
    idempotency_key sklegal_legal.sha256_digest NOT NULL,
    batch_id uuid NOT NULL,
    target_type text NOT NULL CHECK (target_type IN ('matter', 'matter_event')),
    target_id uuid NOT NULL,
    state_kind text NOT NULL CHECK (state_kind IN ('approval', 'execution')),
    state_value text NOT NULL CHECK (
        (state_kind = 'approval' AND state_value = 'pending_review')
        OR (state_kind = 'execution' AND state_value = 'not_started')
    ),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (idempotency_key),
    UNIQUE (target_type, target_id, state_kind),
    FOREIGN KEY (batch_id) REFERENCES sklegal_migrations.pilot_import_batches(batch_id)
);

CREATE TABLE sklegal_migrations.pilot_import_withdrawn_targets (
    target_type text NOT NULL CHECK (target_type IN ('matter', 'matter_event')),
    target_id uuid NOT NULL,
    batch_id uuid NOT NULL,
    withdrawn_at timestamptz NOT NULL,
    PRIMARY KEY (target_type, target_id),
    FOREIGN KEY (batch_id) REFERENCES sklegal_migrations.pilot_import_batches(batch_id)
);

-- sklegal:down
DROP TABLE sklegal_migrations.pilot_import_withdrawn_targets;
DROP TABLE sklegal_migrations.pilot_import_states;
DROP TABLE sklegal_migrations.pilot_import_tension_groups;
DROP TABLE sklegal_migrations.pilot_import_facts;
DROP TABLE sklegal_migrations.pilot_import_records;
DROP TABLE sklegal_migrations.pilot_import_source_files;
DROP TABLE sklegal_migrations.pilot_import_batches;
