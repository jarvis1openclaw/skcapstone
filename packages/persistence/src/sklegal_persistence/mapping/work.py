"""Deadline calculation, deadline, task, and communication mapping entries."""

from .contract import (
    COMMUNICATION_PARTICIPANT_PERSISTENCE,
    LINKED_SOURCE_PERSISTENCE,
    SCOPE_OWNER_BINDINGS,
    SCOPED,
    EntityMapping,
    RelationField,
    _m,
    _stateful,
)

WORK_MAPPINGS: tuple[EntityMapping, ...] = (
    _m(
        "DeadlineCalculation",
        "sklegal_legal.deadline_calculations",
        SCOPED
        + (
            ("trigger_fact_id", "trigger_fact_id"),
            ("calculation_rule", "calculation_rule"),
            ("candidate_due_at", "candidate_due_at"),
            ("calculated_at", "calculated_at"),
            ("calculation_version", "calculation_version"),
        ),
        relations=(
            RelationField(
                "source_references",
                "source_references",
                "sources",
                persistence_columns=LINKED_SOURCE_PERSISTENCE + ("calculation_id",),
                required_persistence_columns=LINKED_SOURCE_PERSISTENCE
                + ("calculation_id",),
                write_relation="sklegal_legal.deadline_calculation_sources",
                value_relation="sklegal_legal.source_references",
                owner_bindings=SCOPE_OWNER_BINDINGS + (("calculation_id", "id"),),
            ),
        ),
    ),
    _m(
        "Deadline",
        "sklegal_legal.deadlines",
        _stateful(SCOPED)
        + (
            ("title", "title"),
            ("candidate_due_at", "candidate_due_at"),
            ("operative_due_at", "operative_due_at"),
            ("trigger_fact_id", "trigger_fact_id"),
            ("calculation_id", "calculation_id"),
            ("review_validation_id", "review_validation_id"),
            ("completed_at", "completed_at"),
        ),
    ),
    _m(
        "Task",
        "sklegal_legal.tasks",
        _stateful(SCOPED)
        + (
            ("title", "title"),
            ("description", "description"),
            ("assigned_principal_id", "assigned_principal_id"),
            ("due_at", "due_at"),
            ("blocked_reason", "blocked_reason"),
            ("completed_at", "completed_at"),
        ),
    ),
    _m(
        "Communication",
        "sklegal_legal.communications",
        _stateful(SCOPED)
        + (
            ("direction", "direction"),
            ("channel", "channel"),
            ("subject", "subject"),
            ("work_product_version_id", "work_product_version_id"),
            ("destination_verified", "destination_verified"),
            ("destination_sha256", "destination_sha256"),
            ("validation_result_id", "validation_result_id"),
            ("approval_id", "approval_id"),
            ("execution_id", "execution_id"),
        ),
        relations=(
            RelationField(
                "participant_ids",
                "participants",
                "ids",
                "party_id",
                persistence_columns=COMMUNICATION_PARTICIPANT_PERSISTENCE,
                required_persistence_columns=COMMUNICATION_PARTICIPANT_PERSISTENCE,
                write_relation="sklegal_legal.communication_participants",
                owner_bindings=SCOPE_OWNER_BINDINGS + (("communication_id", "id"),),
            ),
        ),
        persistence_columns=(
            "work_product_version_number",
            "work_product_content_sha256",
        ),
        required_persistence_columns=(
            "work_product_version_number",
            "work_product_content_sha256",
        ),
    ),
)
