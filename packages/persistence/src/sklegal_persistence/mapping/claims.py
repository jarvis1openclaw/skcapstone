"""Issue, claim, defense, element, and remedy mapping entries."""

from .contract import (
    ELEMENT_EVIDENCE_PERSISTENCE,
    REMEDY_AUTHORITY_PERSISTENCE,
    SCOPE_OWNER_BINDINGS,
    SCOPED,
    THEORY_ELEMENT_PERSISTENCE,
    THEORY_LINK_PERSISTENCE,
    EntityMapping,
    RelationField,
    _m,
    _stateful,
)

CLAIM_MAPPINGS: tuple[EntityMapping, ...] = (
    _m(
        "Issue",
        "sklegal_legal.issues",
        _stateful(SCOPED) + (("question", "question"),),
    ),
    _m(
        "Claim",
        "sklegal_legal.claims",
        _stateful(SCOPED)
        + (
            ("issue_id", "issue_id"),
            ("label", "label"),
            ("statement", "statement"),
            ("acceptance_validation_id", "acceptance_validation_id"),
        ),
        relations=(
            RelationField(
                "element_ids",
                "elements",
                "ids",
                "id",
                persistence_columns=THEORY_ELEMENT_PERSISTENCE,
                required_persistence_columns=THEORY_ELEMENT_PERSISTENCE,
                owner_bindings=SCOPE_OWNER_BINDINGS + (("claim_id", "id"),),
                literal_bindings=(("theory_kind", "claim"),),
            ),
            RelationField(
                "evidence_item_ids",
                "evidence",
                "ids",
                "evidence_item_id",
                persistence_columns=THEORY_LINK_PERSISTENCE,
                required_persistence_columns=THEORY_LINK_PERSISTENCE,
                write_relation="sklegal_legal.theory_evidence",
                owner_bindings=SCOPE_OWNER_BINDINGS + (("theory_id", "id"),),
                literal_bindings=(("theory_kind", "claim"),),
            ),
            RelationField(
                "authority_ids",
                "authorities",
                "ids",
                "authority_id",
                persistence_columns=THEORY_LINK_PERSISTENCE,
                required_persistence_columns=THEORY_LINK_PERSISTENCE,
                write_relation="sklegal_legal.theory_authorities",
                owner_bindings=SCOPE_OWNER_BINDINGS + (("theory_id", "id"),),
                literal_bindings=(("theory_kind", "claim"),),
            ),
        ),
    ),
    _m(
        "Defense",
        "sklegal_legal.defenses",
        _stateful(SCOPED)
        + (
            ("issue_id", "issue_id"),
            ("label", "label"),
            ("statement", "statement"),
            ("acceptance_validation_id", "acceptance_validation_id"),
        ),
        relations=(
            RelationField(
                "element_ids",
                "elements",
                "ids",
                "id",
                persistence_columns=THEORY_ELEMENT_PERSISTENCE,
                required_persistence_columns=THEORY_ELEMENT_PERSISTENCE,
                owner_bindings=SCOPE_OWNER_BINDINGS + (("claim_id", "id"),),
                literal_bindings=(("theory_kind", "defense"),),
            ),
            RelationField(
                "evidence_item_ids",
                "evidence",
                "ids",
                "evidence_item_id",
                persistence_columns=THEORY_LINK_PERSISTENCE,
                required_persistence_columns=THEORY_LINK_PERSISTENCE,
                write_relation="sklegal_legal.theory_evidence",
                owner_bindings=SCOPE_OWNER_BINDINGS + (("theory_id", "id"),),
                literal_bindings=(("theory_kind", "defense"),),
            ),
            RelationField(
                "authority_ids",
                "authorities",
                "ids",
                "authority_id",
                persistence_columns=THEORY_LINK_PERSISTENCE,
                required_persistence_columns=THEORY_LINK_PERSISTENCE,
                write_relation="sklegal_legal.theory_authorities",
                owner_bindings=SCOPE_OWNER_BINDINGS + (("theory_id", "id"),),
                literal_bindings=(("theory_kind", "defense"),),
            ),
        ),
    ),
    _m(
        "Element",
        "sklegal_legal.elements",
        _stateful(SCOPED)
        + (
            ("theory_kind", "theory_kind"),
            ("claim_id", "claim_id"),
            ("description", "description"),
        ),
        relations=(
            RelationField(
                "evidence_item_ids",
                "evidence",
                "ids",
                "evidence_item_id",
                persistence_columns=ELEMENT_EVIDENCE_PERSISTENCE,
                required_persistence_columns=ELEMENT_EVIDENCE_PERSISTENCE,
                write_relation="sklegal_legal.element_evidence",
                owner_bindings=SCOPE_OWNER_BINDINGS + (("element_id", "id"),),
            ),
        ),
    ),
    _m(
        "Remedy",
        "sklegal_legal.remedies",
        _stateful(SCOPED) + (("claim_id", "claim_id"), ("description", "description")),
        relations=(
            RelationField(
                "authority_ids",
                "authorities",
                "ids",
                "authority_id",
                persistence_columns=REMEDY_AUTHORITY_PERSISTENCE,
                required_persistence_columns=REMEDY_AUTHORITY_PERSISTENCE,
                write_relation="sklegal_legal.remedy_authorities",
                owner_bindings=SCOPE_OWNER_BINDINGS + (("remedy_id", "id"),),
            ),
        ),
    ),
)
