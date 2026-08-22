"""Claim ledger mapping entries."""

from .contract import (
    SCOPE_OWNER_BINDINGS,
    SCOPED,
    SOURCE_ONE,
    EntityMapping,
    RelationField,
    _m,
    _stateful,
)

CLAIM_LEDGER_MAPPINGS: tuple[EntityMapping, ...] = (
    _m(
        "LedgerClaim",
        "sklegal_legal.ledger_claims",
        _stateful(SCOPED)
        + (
            ("statement", "statement"),
            ("policy_revision", "policy_revision"),
        ),
        relations=(
            RelationField(
                "support",
                "support",
                "entities",
                entity_name="ClaimSupport",
                nested_owner_bindings=SCOPE_OWNER_BINDINGS + (("claim_id", "id"),),
            ),
        ),
        persistence_columns=("system_from", "system_to"),
        required_persistence_columns=("system_from",),
        read_relation="sklegal_legal.ledger_claim_current",
        history_relation="sklegal_legal.ledger_claim_history",
    ),
    _m(
        "ClaimSupport",
        "sklegal_legal.ledger_claim_support",
        SCOPED
        + (
            ("claim_id", "claim_id"),
            ("kind", "kind"),
            ("span_start", "span_start"),
            ("span_end", "span_end"),
            ("excerpt_sha256", "excerpt_sha256"),
            ("note", "note"),
            ("recorded_by_principal_id", "recorded_by_principal_id"),
            ("policy_revision", "policy_revision"),
        ),
        relations=(SOURCE_ONE,),
        derived_columns=("source_reference_id",),
    ),
)
