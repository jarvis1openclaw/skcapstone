"""Fact, tension, evidence, custody, and authority mapping entries."""

from .contract import (
    INTERVAL,
    SCOPE_OWNER_BINDINGS,
    SCOPED,
    SOURCE_ONE,
    TENSION_ASSERTION_PERSISTENCE,
    CompositeField,
    EntityMapping,
    RelationField,
    _m,
    _stateful,
)

FACT_MAPPINGS: tuple[EntityMapping, ...] = (
    _m(
        "FactAssertion",
        "sklegal_legal.fact_assertions",
        _stateful(SCOPED)
        + (
            ("subject_ref", "subject_ref"),
            ("predicate", "predicate"),
            ("source_locator", "source_locator"),
            ("observed_at", "observed_at"),
            ("tension_group_id", "tension_group_id"),
            ("verification_reference_id", "verification_reference_id"),
        ),
        composites=(
            CompositeField(
                "asserted_value", "typed_value", ("value_type", "asserted_value")
            ),
            INTERVAL,
        ),
        relations=(SOURCE_ONE,),
        derived_columns=("source_reference_id",),
    ),
    _m(
        "TensionGroup",
        "sklegal_legal.tension_groups",
        _stateful(SCOPED)
        + (
            ("title", "title"),
            ("selected_assertion_id", "selected_assertion_id"),
            ("resolution_rationale", "resolution_rationale"),
            ("resolved_by", "resolved_by"),
            ("resolved_at", "resolved_at"),
        ),
        relations=(
            RelationField(
                "assertion_ids",
                "assertions",
                "ids",
                "assertion_id",
                persistence_columns=TENSION_ASSERTION_PERSISTENCE,
                required_persistence_columns=TENSION_ASSERTION_PERSISTENCE,
                write_relation="sklegal_legal.tension_assertions",
                owner_bindings=SCOPE_OWNER_BINDINGS + (("tension_group_id", "id"),),
            ),
        ),
    ),
    _m(
        "EvidenceItem",
        "sklegal_legal.evidence_items",
        _stateful(SCOPED)
        + (
            ("title", "title"),
            ("media_type", "media_type"),
            ("content_sha256", "content_sha256"),
            ("verification_reference_id", "verification_reference_id"),
        ),
        relations=(SOURCE_ONE,),
        derived_columns=("source_reference_id",),
    ),
    _m(
        "CustodyEvent",
        "sklegal_legal.custody_events",
        SCOPED
        + (
            ("evidence_item_id", "evidence_item_id"),
            ("action", "action"),
            ("custodian_id", "custodian_id"),
            ("occurred_at", "occurred_at"),
        ),
        relations=(SOURCE_ONE,),
        derived_columns=("source_reference_id",),
    ),
    _m(
        "Authority",
        "sklegal_legal.authorities",
        _stateful(SCOPED)
        + (
            ("title", "title"),
            ("citation", "citation"),
            ("jurisdiction", "jurisdiction"),
            ("authority_kind", "authority_kind"),
            ("applicability_validation_id", "applicability_validation_id"),
        ),
        composites=(INTERVAL,),
        relations=(SOURCE_ONE,),
        derived_columns=("source_reference_id",),
        persistence_columns=("system_from", "system_to"),
        required_persistence_columns=("system_from",),
        read_relation="sklegal_legal.authority_current",
        history_relation="sklegal_legal.authority_history",
    ),
)
