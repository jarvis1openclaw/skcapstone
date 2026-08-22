"""Matter, party, forum, proceeding, matter event, and transaction mapping entries."""

from .contract import (
    ALIAS_PERSISTENCE,
    INTERVAL,
    LINKED_SOURCE_PERSISTENCE,
    PROTECTED,
    SCOPE_OWNER_BINDINGS,
    SCOPED,
    SOURCE_ONE,
    SOURCE_OPTIONAL,
    TRANSACTION_ROLE_PERSISTENCE,
    EntityMapping,
    RelationField,
    _m,
    _stateful,
)

STRUCTURE_MAPPINGS: tuple[EntityMapping, ...] = (
    _m(
        "Matter",
        "sklegal_legal.matters",
        _stateful(PROTECTED)
        + (
            ("matter_id", "matter_id"),
            ("client_id", "client_id"),
            ("engagement_id", "engagement_id"),
            ("title", "title"),
            ("summary", "summary"),
            ("completeness", "completeness"),
            ("opened_at", "opened_at"),
            ("closed_at", "closed_at"),
        ),
        relations=(
            RelationField(
                "aliases",
                "aliases",
                "aliases",
                persistence_columns=ALIAS_PERSISTENCE,
                required_persistence_columns=ALIAS_PERSISTENCE,
                write_relation="sklegal_legal.legacy_aliases",
                owner_bindings=SCOPE_OWNER_BINDINGS
                + (("canonical_record_id", "matter_id"),),
                literal_bindings=(
                    ("canonical_record_kind", "matter"),
                    ("canonical_matter_event_id", None),
                ),
            ),
        ),
    ),
    _m(
        "Party",
        "sklegal_legal.parties",
        _stateful(SCOPED)
        + (
            ("display_name", "display_name"),
            ("party_kind", "party_kind"),
            ("verification_reference_id", "verification_reference_id"),
        ),
        relations=(SOURCE_ONE,),
        derived_columns=("source_reference_id",),
    ),
    _m(
        "PartyRole",
        "sklegal_legal.party_roles",
        _stateful(SCOPED)
        + (
            ("party_id", "party_id"),
            ("role", "role"),
            ("verification_reference_id", "verification_reference_id"),
        ),
        composites=(INTERVAL,),
        relations=(SOURCE_ONE,),
        derived_columns=("source_reference_id",),
        persistence_columns=("proceeding_id",),
        required_persistence_columns=("proceeding_id",),
    ),
    _m(
        "Forum",
        "sklegal_legal.forums",
        SCOPED
        + (
            ("name", "name"),
            ("jurisdiction", "jurisdiction"),
            ("forum_kind", "forum_kind"),
        ),
        relations=(SOURCE_OPTIONAL,),
        derived_columns=("source_reference_id",),
    ),
    _m(
        "Proceeding",
        "sklegal_legal.proceedings",
        _stateful(SCOPED)
        + (
            ("title", "title"),
            ("forum_id", "forum_id"),
            ("docket_number", "docket_number"),
        ),
        composites=(INTERVAL,),
    ),
    _m(
        "MatterEvent",
        "sklegal_legal.matter_events",
        _stateful(SCOPED)
        + (
            ("event_type", "event_type"),
            ("description", "description"),
            ("occurred_at", "occurred_at"),
            ("observed_at", "observed_at"),
            ("verification_reference_id", "verification_reference_id"),
        ),
        relations=(
            SOURCE_ONE,
            RelationField(
                "aliases",
                "aliases",
                "aliases",
                persistence_columns=ALIAS_PERSISTENCE,
                required_persistence_columns=ALIAS_PERSISTENCE,
                write_relation="sklegal_legal.legacy_aliases",
                owner_bindings=SCOPE_OWNER_BINDINGS
                + (
                    ("canonical_record_id", "id"),
                    ("canonical_matter_event_id", "id"),
                ),
                literal_bindings=(("canonical_record_kind", "matter_event"),),
            ),
        ),
        derived_columns=("source_reference_id",),
    ),
    _m(
        "Transaction",
        "sklegal_legal.legal_transactions",
        _stateful(SCOPED)
        + (
            ("title", "title"),
            ("description", "description"),
            ("effective_at", "effective_at"),
        ),
        relations=(
            RelationField(
                "party_role_ids",
                "party_roles",
                "ids",
                "party_role_id",
                persistence_columns=TRANSACTION_ROLE_PERSISTENCE,
                required_persistence_columns=TRANSACTION_ROLE_PERSISTENCE,
                write_relation="sklegal_legal.transaction_party_roles",
                owner_bindings=SCOPE_OWNER_BINDINGS + (("transaction_id", "id"),),
            ),
            RelationField(
                "source_references",
                "source_references",
                "sources",
                persistence_columns=LINKED_SOURCE_PERSISTENCE + ("transaction_id",),
                required_persistence_columns=LINKED_SOURCE_PERSISTENCE
                + ("transaction_id",),
                write_relation="sklegal_legal.transaction_source_references",
                value_relation="sklegal_legal.source_references",
                owner_bindings=SCOPE_OWNER_BINDINGS + (("transaction_id", "id"),),
            ),
        ),
    ),
)
