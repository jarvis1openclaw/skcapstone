"""Validation, approval, and execution mapping entries."""

from .contract import (
    EXECUTION_SUBJECT_BINDINGS,
    SCOPE_OWNER_BINDINGS,
    SCOPED,
    SUBJECT,
    VALIDATION_CHECK_PERSISTENCE,
    EntityMapping,
    RelationField,
    _m,
    _stateful,
)

INTEGRATION_MAPPINGS: tuple[EntityMapping, ...] = (
    _m(
        "ValidationResult",
        "sklegal_legal.validations",
        SCOPED
        + (
            ("outcome", "outcome"),
            ("validator_principal_id", "validator_principal_id"),
            ("validated_at", "validated_at"),
            ("rationale", "rationale"),
        ),
        composites=(SUBJECT,),
        relations=(
            RelationField(
                "check_ids",
                "checks",
                "ids",
                "check_id",
                persistence_columns=VALIDATION_CHECK_PERSISTENCE,
                required_persistence_columns=VALIDATION_CHECK_PERSISTENCE,
                write_relation="sklegal_legal.validation_checks",
                owner_bindings=SCOPE_OWNER_BINDINGS + (("validation_id", "id"),),
            ),
        ),
        derived_columns=("subject_kind",),
    ),
    _m(
        "Approval",
        "sklegal_legal.approvals",
        _stateful(SCOPED)
        + (
            ("reviewer_principal_id", "reviewer_principal_id"),
            ("decided_at", "decided_at"),
            ("rationale", "rationale"),
            ("revoker_principal_id", "revoker_principal_id"),
            ("revocation_rationale", "revocation_rationale"),
            ("revoked_at", "revoked_at"),
        ),
        composites=(SUBJECT,),
    ),
    _m(
        "Execution",
        "sklegal_legal.executions",
        _stateful(SCOPED)
        + (
            ("destination_sha256", "destination_sha256"),
            ("idempotency_key", "idempotency_key"),
        ),
        composites=(SUBJECT,),
        relations=(
            RelationField(
                "validation_result",
                "validation_result",
                "entity_optional",
                entity_name="ValidationResult",
                nested_owner_bindings=SCOPE_OWNER_BINDINGS + EXECUTION_SUBJECT_BINDINGS,
                nested_literal_bindings=(("subject_kind", "work_product_version"),),
                nested_reference_binding=("id", "validation_result_id"),
            ),
            RelationField(
                "approval",
                "approval",
                "entity_optional",
                entity_name="Approval",
                persistence_columns=("captured_at",),
                required_read_persistence_columns=("captured_at",),
                nested_owner_bindings=SCOPE_OWNER_BINDINGS
                + EXECUTION_SUBJECT_BINDINGS
                + (("approval_version", "approval_version"),),
                nested_literal_bindings=(("status", "approved"),),
                nested_reference_binding=("approval_id", "approval_id"),
                parent_presence_columns=("approval_id", "approval_version"),
                read_relation="sklegal_legal.approval_history",
                nested_column_bindings=(
                    ("approval_id", "id"),
                    ("approval_version", "version"),
                ),
                nested_default_columns=(
                    ("revoker_principal_id", None),
                    ("revocation_rationale", None),
                    ("revoked_at", None),
                ),
            ),
            RelationField(
                "events",
                "events",
                "entities",
                entity_name="ExecutionEvent",
                nested_owner_bindings=SCOPE_OWNER_BINDINGS + (("execution_id", "id"),),
            ),
            RelationField(
                "receipt",
                "receipt",
                "entity_optional",
                entity_name="ExecutionReceipt",
                nested_owner_bindings=SCOPE_OWNER_BINDINGS + (("execution_id", "id"),),
            ),
        ),
        persistence_columns=(
            "validation_result_id",
            "approval_id",
            "approval_version",
        ),
        required_persistence_columns=(
            "validation_result_id",
            "approval_id",
            "approval_version",
        ),
    ),
    _m(
        "ExecutionEvent",
        "sklegal_legal.execution_events",
        SCOPED
        + (
            ("execution_id", "execution_id"),
            ("step", "step"),
            ("occurred_at", "occurred_at"),
            ("correlation_id", "correlation_id"),
            ("actor_principal_id", "actor_principal_id"),
            ("receipt_id", "receipt_id"),
        ),
        persistence_columns=("sequence_no",),
        required_persistence_columns=("sequence_no",),
    ),
    _m(
        "ExecutionReceipt",
        "sklegal_legal.execution_receipts",
        SCOPED
        + (
            ("execution_id", "execution_id"),
            ("connector", "connector"),
            ("external_receipt_id", "external_receipt_id"),
            ("artifact_content_sha256", "artifact_content_sha256"),
            ("destination_sha256", "destination_sha256"),
            ("received_at", "received_at"),
            ("verified_at", "verified_at"),
        ),
    ),
)
