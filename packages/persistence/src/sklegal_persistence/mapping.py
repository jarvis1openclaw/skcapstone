"""Database-driver-neutral domain decomposition and strict reconstruction.

Rows must contain native driver values such as ``UUID`` and UTC ``datetime``
objects. Normalized relations are supplied separately and are mandatory when
declared, including when an optional or many-valued relation is empty. This
keeps absent joins distinguishable from intentionally empty domain values.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal, cast

import sklegal_domain
from sklegal_domain.base import DomainEntity

RelationMode = Literal[
    "ids",
    "source_one",
    "source_optional",
    "sources",
    "aliases",
    "entity_one",
    "entity_optional",
    "entities",
]
WriteAuthority = Literal[
    "administrative_bootstrap",
    "runtime_rls",
    "runtime_rls_with_controlled_evolution",
    "controlled_writer",
]


class MappingContractError(ValueError):
    """Raised when normalized persistence input is incomplete or ambiguous."""


@dataclass(frozen=True)
class CompositeField:
    field: str
    adapter: Literal["effective_interval", "typed_value", "artifact_binding"]
    columns: tuple[str, ...]


@dataclass(frozen=True)
class RelationField:
    field: str
    relation: str
    mode: RelationMode
    value_column: str | None = None
    entity_name: str | None = None
    persistence_columns: tuple[str, ...] = ()
    required_persistence_columns: tuple[str, ...] = ()
    write_relation: str | None = None
    value_relation: str | None = None
    owner_bindings: tuple[tuple[str, str], ...] = ()
    literal_bindings: tuple[tuple[str, Any], ...] = ()
    reference_binding: tuple[str, str] | None = None
    nested_owner_bindings: tuple[tuple[str, str], ...] = ()
    nested_literal_bindings: tuple[tuple[str, Any], ...] = ()
    nested_reference_binding: tuple[str, str] | None = None
    parent_presence_columns: tuple[str, ...] = ()
    read_relation: str | None = None
    nested_column_bindings: tuple[tuple[str, str], ...] = ()
    nested_default_columns: tuple[tuple[str, Any], ...] = ()
    required_read_persistence_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityMapping:
    entity_name: str
    table: str
    direct: tuple[tuple[str, str], ...]
    composites: tuple[CompositeField, ...] = ()
    relations: tuple[RelationField, ...] = ()
    derived_columns: tuple[str, ...] = ()
    persistence_columns: tuple[str, ...] = ()
    required_persistence_columns: tuple[str, ...] = ()
    read_relation: str | None = None
    history_relation: str | None = None


@dataclass(frozen=True)
class PersistenceMetadata:
    """Closed DB-only values kept outside the canonical legal entity."""

    scalar: Mapping[str, Any] = field(default_factory=dict)
    relations: Mapping[str, tuple[Mapping[str, Any], ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        scalar = MappingProxyType(dict(self.scalar))
        relations = MappingProxyType(
            {
                relation: tuple(MappingProxyType(dict(row)) for row in rows)
                for relation, rows in self.relations.items()
            }
        )
        object.__setattr__(self, "scalar", scalar)
        object.__setattr__(self, "relations", relations)


@dataclass(frozen=True)
class Reconstruction:
    """A strict legal entity plus its separately retained DB-only metadata."""

    entity: DomainEntity
    metadata: PersistenceMetadata


@dataclass(frozen=True)
class AuxiliaryWrite:
    """One explicit physical row required before the canonical primary row."""

    operation: str
    table: str
    row: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "row", MappingProxyType(dict(self.row)))


@dataclass(frozen=True)
class DecompositionWriteContract:
    """Closed disposition of every leaf emitted by one decomposition."""

    authority: WriteAuthority
    operation: str
    canonical_input_paths: tuple[str, ...]
    database_output_paths: tuple[str, ...]
    auxiliary_writes: tuple[AuxiliaryWrite, ...] = ()

    def __post_init__(self) -> None:
        canonical = set(self.canonical_input_paths)
        outputs = set(self.database_output_paths)
        if len(canonical) != len(self.canonical_input_paths):
            raise MappingContractError("write contract repeats a canonical input path")
        if len(outputs) != len(self.database_output_paths):
            raise MappingContractError("write contract repeats a database output path")
        overlap = sorted(canonical & outputs)
        if overlap:
            raise MappingContractError(
                f"write contract path has two dispositions: {overlap[0]}"
            )


@dataclass(frozen=True)
class DecomposedEntity:
    """Normalized write payload without a database-driver dependency."""

    entity_name: str
    table: str
    row: Mapping[str, Any]
    relations: Mapping[str, tuple[Mapping[str, Any], ...]]
    write_contract: DecompositionWriteContract

    def __post_init__(self) -> None:
        object.__setattr__(self, "row", MappingProxyType(dict(self.row)))
        object.__setattr__(
            self,
            "relations",
            MappingProxyType(
                {
                    relation: tuple(MappingProxyType(dict(row)) for row in rows)
                    for relation, rows in self.relations.items()
                }
            ),
        )
        actual_paths = set(_decomposition_leaf_paths(self.row, self.relations))
        declared_paths = set(self.write_contract.canonical_input_paths) | set(
            self.write_contract.database_output_paths
        )
        if actual_paths != declared_paths:
            missing = sorted(actual_paths - declared_paths)
            extra = sorted(declared_paths - actual_paths)
            raise MappingContractError(
                "write contract does not cover decomposition leaves; "
                f"missing={missing}, extra={extra}"
            )


AUDIT = (
    ("id", "id"),
    ("version", "version"),
    ("created_at", "created_at"),
    ("updated_at", "updated_at"),
)
PROTECTED = AUDIT + (("tenant_id", "tenant_id"), ("classification", "classification"))
SCOPED = PROTECTED + (("matter_id", "matter_id"), ("completeness", "completeness"))


def _stateful(base: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    return base + (("status", "status"),)


def _m(
    entity_name: str,
    table: str,
    direct: tuple[tuple[str, str], ...],
    *,
    composites: tuple[CompositeField, ...] = (),
    relations: tuple[RelationField, ...] = (),
    derived_columns: tuple[str, ...] = (),
    persistence_columns: tuple[str, ...] = (),
    required_persistence_columns: tuple[str, ...] = (),
    read_relation: str | None = None,
    history_relation: str | None = None,
) -> EntityMapping:
    return EntityMapping(
        entity_name,
        table,
        direct,
        composites,
        relations,
        derived_columns,
        persistence_columns,
        required_persistence_columns,
        read_relation,
        history_relation,
    )


INTERVAL = CompositeField(
    "effective_interval", "effective_interval", ("valid_from", "valid_to")
)
SUBJECT = CompositeField(
    "subject",
    "artifact_binding",
    ("subject_artifact_id", "subject_artifact_version", "subject_content_sha256"),
)
SOURCE_ROW_PERSISTENCE = (
    "tenant_id",
    "matter_id",
    "classification",
    "completeness",
    "version",
    "created_at",
    "updated_at",
)
LINKED_SOURCE_PERSISTENCE = SOURCE_ROW_PERSISTENCE + ("relation_created_at",)
SCOPE_OWNER_BINDINGS = (("tenant_id", "tenant_id"), ("matter_id", "matter_id"))
SOURCE_ONE = RelationField(
    "source_reference",
    "source_reference",
    "source_one",
    persistence_columns=SOURCE_ROW_PERSISTENCE,
    required_persistence_columns=SOURCE_ROW_PERSISTENCE,
    write_relation="sklegal_legal.source_references",
    owner_bindings=SCOPE_OWNER_BINDINGS,
    reference_binding=("id", "source_reference_id"),
)
SOURCE_OPTIONAL = RelationField(
    "source_reference",
    "source_reference",
    "source_optional",
    persistence_columns=SOURCE_ROW_PERSISTENCE,
    required_persistence_columns=SOURCE_ROW_PERSISTENCE,
    write_relation="sklegal_legal.source_references",
    owner_bindings=SCOPE_OWNER_BINDINGS,
    reference_binding=("id", "source_reference_id"),
)
TRANSACTION_ROLE_PERSISTENCE = (
    "tenant_id",
    "matter_id",
    "transaction_id",
    "created_at",
)
TENSION_ASSERTION_PERSISTENCE = (
    "tenant_id",
    "matter_id",
    "tension_group_id",
    "created_at",
)
THEORY_ELEMENT_PERSISTENCE = (
    "tenant_id",
    "matter_id",
    "theory_kind",
    "claim_id",
    "created_at",
)
THEORY_LINK_PERSISTENCE = (
    "tenant_id",
    "matter_id",
    "theory_kind",
    "theory_id",
    "created_at",
)
ELEMENT_EVIDENCE_PERSISTENCE = ("tenant_id", "matter_id", "element_id", "created_at")
REMEDY_AUTHORITY_PERSISTENCE = ("tenant_id", "matter_id", "remedy_id", "created_at")
VALIDATION_CHECK_PERSISTENCE = ("tenant_id", "matter_id", "validation_id", "created_at")
COMMUNICATION_PARTICIPANT_PERSISTENCE = (
    "tenant_id",
    "matter_id",
    "communication_id",
    "created_at",
)
ALIAS_PERSISTENCE = (
    "id",
    "tenant_id",
    "matter_id",
    "canonical_record_kind",
    "canonical_record_id",
    "canonical_matter_event_id",
    "import_batch_id",
    "classification",
    "completeness",
    "version",
    "created_at",
    "updated_at",
)
EXECUTION_SUBJECT_BINDINGS = (
    ("subject_artifact_id", "subject_artifact_id"),
    ("subject_artifact_version", "subject_artifact_version"),
    ("subject_content_sha256", "subject_content_sha256"),
)


MAPPINGS: dict[str, EntityMapping] = {
    item.entity_name: item
    for item in (
        _m(
            "Tenant",
            "sklegal_identity.tenants",
            _stateful(PROTECTED) + (("name", "name"),),
            persistence_columns=("slug",),
            required_persistence_columns=("slug",),
        ),
        _m(
            "Client",
            "sklegal_legal.clients",
            _stateful(PROTECTED)
            + (("display_name", "display_name"), ("client_kind", "client_kind")),
        ),
        _m(
            "Engagement",
            "sklegal_legal.engagements",
            _stateful(PROTECTED)
            + (("client_id", "client_id"), ("title", "title"), ("scope", "scope")),
            composites=(INTERVAL,),
        ),
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
            _stateful(SCOPED)
            + (("claim_id", "claim_id"), ("description", "description")),
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
        _m(
            "WorkProduct",
            "sklegal_legal.work_products",
            _stateful(SCOPED)
            + (
                ("title", "title"),
                ("work_product_kind", "work_product_kind"),
                ("current_version_id", "current_version_id"),
                ("validation_result_id", "validation_result_id"),
                ("approval_id", "approval_id"),
            ),
            persistence_columns=("current_version_number", "current_content_sha256"),
            required_persistence_columns=(
                "current_version_number",
                "current_content_sha256",
            ),
        ),
        _m(
            "WorkProductVersion",
            "sklegal_legal.work_product_versions",
            _stateful(SCOPED)
            + (
                ("work_product_id", "work_product_id"),
                ("version_number", "version_number"),
                ("content_sha256", "content_sha256"),
                ("source_artifact_id", "source_artifact_id"),
            ),
            persistence_columns=(
                "encrypted_content",
                "encryption_key_ref",
                "encryption_algorithm",
                "encrypted_at",
            ),
            required_persistence_columns=(
                "encrypted_content",
                "encryption_key_ref",
                "encryption_algorithm",
                "encrypted_at",
            ),
        ),
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
                    nested_owner_bindings=SCOPE_OWNER_BINDINGS
                    + EXECUTION_SUBJECT_BINDINGS,
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
                    nested_owner_bindings=SCOPE_OWNER_BINDINGS
                    + (("execution_id", "id"),),
                ),
                RelationField(
                    "receipt",
                    "receipt",
                    "entity_optional",
                    entity_name="ExecutionReceipt",
                    nested_owner_bindings=SCOPE_OWNER_BINDINGS
                    + (("execution_id", "id"),),
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
}


SOURCE_COLUMNS = (
    "id",
    "source_system",
    "source_version",
    "content_sha256",
    "locator",
    "observed_at",
)
ALIAS_COLUMNS = (
    "source_system",
    "legacy_record_kind",
    "legacy_id",
    "legacy_slug",
    "legacy_path",
    "source_version",
    "content_sha256",
    "observed_at",
)


def _strict_columns(
    row: Mapping[str, Any],
    *,
    required: Sequence[str],
    allowed: Sequence[str],
    context: str,
) -> None:
    missing = sorted(set(required) - set(row))
    if missing:
        raise MappingContractError(
            f"{context} is missing columns: {', '.join(missing)}"
        )
    unknown = sorted(set(row) - set(allowed))
    if unknown:
        raise MappingContractError(
            f"{context} has undeclared columns: {', '.join(unknown)}"
        )


def _source(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_reference_id": row["id"],
        **{column: row[column] for column in SOURCE_COLUMNS if column != "id"},
    }


def _alias(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_system": row["source_system"],
        "record_kind": sklegal_domain.LegacyRecordKind(row["legacy_record_kind"]),
        **{
            column: row[column]
            for column in ALIAS_COLUMNS
            if column not in {"source_system", "legacy_record_kind"}
        },
    }


def _composite(spec: CompositeField, row: Mapping[str, Any]) -> dict[str, Any]:
    if spec.adapter == "effective_interval":
        return {"valid_from": row[spec.columns[0]], "valid_to": row[spec.columns[1]]}
    if spec.adapter == "typed_value":
        return {"value_type": row[spec.columns[0]], "value": row[spec.columns[1]]}
    binding = {
        "artifact_id": row[spec.columns[0]],
        "artifact_version": row[spec.columns[1]],
        "content_sha256": row[spec.columns[2]],
    }
    subject_kind = row.get("subject_kind")
    if subject_kind is not None and subject_kind != "work_product_version":
        binding["subject_kind"] = subject_kind
    return binding


def _relation_payload_row(
    spec: RelationField,
    relation_row: Mapping[str, Any],
    *,
    nested: bool,
) -> Mapping[str, Any]:
    if not nested:
        return relation_row
    payload_row = relation_row.get("row")
    if not isinstance(payload_row, Mapping):
        raise MappingContractError(f"{spec.relation} must contain a row mapping")
    return payload_row


def _binding_value(row: Mapping[str, Any], column: str, *, context: str) -> Any:
    if column not in row:
        raise MappingContractError(f"{context} binding is missing column: {column}")
    return row[column]


def _validate_binding_pairs(
    spec: RelationField,
    parent_row: Mapping[str, Any],
    relation_rows: Sequence[Mapping[str, Any]],
    bindings: Sequence[tuple[str, str]],
    *,
    nested: bool,
) -> None:
    if not bindings:
        return
    for relation_row in relation_rows:
        payload_row = _relation_payload_row(spec, relation_row, nested=nested)
        for relation_column, parent_column in bindings:
            actual = _binding_value(payload_row, relation_column, context=spec.relation)
            expected = _binding_value(parent_row, parent_column, context=spec.relation)
            if actual != expected:
                raise MappingContractError(
                    f"{spec.relation} owner binding disagrees with {parent_column}"
                )


def _validate_literal_bindings(
    spec: RelationField,
    relation_rows: Sequence[Mapping[str, Any]],
    bindings: Sequence[tuple[str, Any]],
    *,
    nested: bool,
) -> None:
    if not bindings:
        return
    for relation_row in relation_rows:
        payload_row = _relation_payload_row(spec, relation_row, nested=nested)
        for relation_column, expected in bindings:
            actual = _binding_value(payload_row, relation_column, context=spec.relation)
            if actual != expected:
                raise MappingContractError(
                    f"{spec.relation} discriminator binding disagrees with "
                    f"{relation_column}"
                )


def _validate_reference_binding(
    spec: RelationField,
    parent_row: Mapping[str, Any],
    relation_rows: Sequence[Mapping[str, Any]],
    binding: tuple[str, str] | None,
    *,
    nested: bool,
) -> None:
    if binding is None:
        return
    relation_column, parent_column = binding
    expected = _binding_value(parent_row, parent_column, context=spec.relation)
    if expected is None:
        if relation_rows:
            raise MappingContractError(
                f"{spec.relation} join is present while {parent_column} is null"
            )
        return
    if not relation_rows:
        raise MappingContractError(
            f"{spec.relation} join is missing while {parent_column} is present"
        )
    payload_row = _relation_payload_row(spec, relation_rows[0], nested=nested)
    actual = _binding_value(payload_row, relation_column, context=spec.relation)
    if actual != expected:
        raise MappingContractError(
            f"{spec.relation} reference binding disagrees with {parent_column}"
        )


def _validate_relation_bindings(
    spec: RelationField,
    parent_row: Mapping[str, Any],
    relation_rows: Sequence[Mapping[str, Any]],
) -> None:
    if spec.mode in {"source_one", "source_optional"}:
        minimum = 1 if spec.mode == "source_one" else 0
        if not minimum <= len(relation_rows) <= 1:
            raise MappingContractError(f"{spec.relation} has invalid cardinality")
    if spec.mode in {"entity_one", "entity_optional"}:
        minimum = 1 if spec.mode == "entity_one" else 0
        if not minimum <= len(relation_rows) <= 1:
            raise MappingContractError(f"{spec.relation} has invalid cardinality")

    if spec.parent_presence_columns:
        present = tuple(
            _binding_value(parent_row, column, context=spec.relation) is not None
            for column in spec.parent_presence_columns
        )
        if any(present) and not all(present):
            raise MappingContractError(
                f"{spec.relation} parent reference columns have mixed nullability"
            )

    _validate_binding_pairs(
        spec,
        parent_row,
        relation_rows,
        spec.owner_bindings,
        nested=False,
    )
    _validate_literal_bindings(
        spec,
        relation_rows,
        spec.literal_bindings,
        nested=False,
    )
    _validate_reference_binding(
        spec,
        parent_row,
        relation_rows,
        spec.reference_binding,
        nested=False,
    )
    _validate_reference_binding(
        spec,
        parent_row,
        relation_rows,
        spec.nested_reference_binding,
        nested=True,
    )
    _validate_binding_pairs(
        spec,
        parent_row,
        relation_rows,
        spec.nested_owner_bindings,
        nested=True,
    )
    _validate_literal_bindings(
        spec,
        relation_rows,
        spec.nested_literal_bindings,
        nested=True,
    )


def _decode_nested_snapshot_row(
    spec: RelationField, nested_row: Mapping[str, Any]
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    canonical = dict(nested_row)
    snapshot_metadata: dict[str, Any] = {}
    required_metadata = (
        spec.required_read_persistence_columns or spec.required_persistence_columns
    )
    missing = sorted(set(required_metadata) - set(canonical))
    if missing:
        raise MappingContractError(
            f"{spec.relation} is missing snapshot metadata: {missing[0]}"
        )
    for column in spec.persistence_columns:
        if column in canonical:
            snapshot_metadata[column] = canonical.pop(column)
    for physical_column, canonical_column in spec.nested_column_bindings:
        if physical_column not in canonical:
            raise MappingContractError(
                f"{spec.relation} snapshot is missing column: {physical_column}"
            )
        if canonical_column in canonical:
            raise MappingContractError(
                f"{spec.relation} snapshot contains ambiguous column: "
                f"{canonical_column}"
            )
        canonical[canonical_column] = canonical.pop(physical_column)
    for column, value in spec.nested_default_columns:
        if column in canonical:
            raise MappingContractError(
                f"{spec.relation} snapshot has undeclared column: {column}"
            )
        canonical[column] = value
    return canonical, snapshot_metadata


def _decode_relation(
    spec: RelationField,
    rows: Sequence[Mapping[str, Any]],
    parent_row: Mapping[str, Any],
) -> tuple[Any, tuple[Mapping[str, Any], ...]]:
    _validate_relation_bindings(spec, parent_row, rows)

    values: list[Any] = []
    metadata_rows: list[Mapping[str, Any]] = []
    for row in rows:
        if spec.mode == "ids":
            assert spec.value_column is not None
            domain_columns = (spec.value_column,)
            _strict_columns(
                row,
                required=domain_columns + spec.required_persistence_columns,
                allowed=domain_columns + spec.persistence_columns,
                context=spec.relation,
            )
            values.append(row[spec.value_column])
            metadata_rows.append(
                {key: row[key] for key in spec.persistence_columns if key in row}
            )
            continue
        if spec.mode in {"source_one", "source_optional", "sources"}:
            _strict_columns(
                row,
                required=SOURCE_COLUMNS + spec.required_persistence_columns,
                allowed=SOURCE_COLUMNS + spec.persistence_columns,
                context=spec.relation,
            )
            values.append(_source(row))
            metadata_rows.append(
                {key: row[key] for key in spec.persistence_columns if key in row}
            )
            continue
        if spec.mode == "aliases":
            _strict_columns(
                row,
                required=ALIAS_COLUMNS + spec.required_persistence_columns,
                allowed=ALIAS_COLUMNS + spec.persistence_columns,
                context=spec.relation,
            )
            values.append(_alias(row))
            metadata_rows.append(
                {key: row[key] for key in spec.persistence_columns if key in row}
            )
            continue
        if spec.entity_name is None:
            raise MappingContractError(
                f"{spec.relation} does not declare an entity type"
            )
        _strict_columns(
            row,
            required=("row", "relations"),
            allowed=("row", "relations"),
            context=spec.relation,
        )
        nested_row = row["row"]
        nested_relations = row["relations"]
        if not isinstance(nested_row, Mapping) or not isinstance(
            nested_relations, Mapping
        ):
            raise MappingContractError(
                f"{spec.relation} must contain row and relations"
            )
        canonical_nested_row, snapshot_metadata = _decode_nested_snapshot_row(
            spec, nested_row
        )
        reconstructed = reconstruct_with_metadata(
            spec.entity_name, canonical_nested_row, nested_relations
        )
        values.append(reconstructed.entity)
        metadata_rows.append(
            {
                "nested_metadata": reconstructed.metadata,
                **snapshot_metadata,
            }
        )

    if spec.mode in {"source_one", "source_optional", "entity_one", "entity_optional"}:
        return (values[0] if values else None), tuple(metadata_rows)
    return tuple(values), tuple(metadata_rows)


def reconstruct_with_metadata(
    entity_name: str,
    row: Mapping[str, Any],
    relations: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Reconstruction:
    """Strictly reconstruct and retain every declared DB-only value."""

    try:
        spec = MAPPINGS[entity_name]
    except KeyError as exc:
        raise MappingContractError(f"unknown domain entity: {entity_name}") from exc
    direct_columns = tuple(column for _, column in spec.direct)
    composite_columns = tuple(
        column for composite in spec.composites for column in composite.columns
    )
    required_columns = (
        direct_columns
        + composite_columns
        + spec.derived_columns
        + spec.required_persistence_columns
    )
    allowed_columns = (
        direct_columns
        + composite_columns
        + spec.derived_columns
        + spec.persistence_columns
    )
    _strict_columns(
        row,
        required=required_columns,
        allowed=allowed_columns,
        context=entity_name,
    )
    expected_relations = {relation.relation for relation in spec.relations}
    missing_relations = sorted(expected_relations - set(relations))
    unknown_relations = sorted(set(relations) - expected_relations)
    if missing_relations:
        raise MappingContractError(
            f"{entity_name} is missing normalized relation: {missing_relations[0]}"
        )
    if unknown_relations:
        raise MappingContractError(
            f"{entity_name} has undeclared normalized relation: {unknown_relations[0]}"
        )
    entity_type = getattr(sklegal_domain, entity_name)
    payload: dict[str, Any] = {}
    for domain_field, column in spec.direct:
        value = row[column]
        annotation = entity_type.model_fields[domain_field].annotation
        if inspect.isclass(annotation) and issubclass(annotation, Enum):
            value = annotation(value)
        payload[domain_field] = value
    for composite in spec.composites:
        payload[composite.field] = _composite(composite, row)
    relation_metadata: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for relation in spec.relations:
        value, metadata_rows = _decode_relation(
            relation, relations[relation.relation], row
        )
        payload[relation.field] = value
        relation_metadata[relation.relation] = metadata_rows
    entity = entity_type.model_validate(payload, strict=True)
    scalar_metadata = {
        column: row[column] for column in spec.persistence_columns if column in row
    }
    return Reconstruction(
        entity,
        PersistenceMetadata(scalar=scalar_metadata, relations=relation_metadata),
    )


def reconstruct(
    entity_name: str,
    row: Mapping[str, Any],
    relations: Mapping[str, Sequence[Mapping[str, Any]]],
) -> DomainEntity:
    """Strictly reconstruct one canonical entity from normalized rows."""

    return reconstruct_with_metadata(entity_name, row, relations).entity


def _plain(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _encode_composite(
    spec: CompositeField,
    value: Any,
    row: dict[str, Any],
    *,
    include_subject_kind: bool,
) -> None:
    if spec.adapter == "effective_interval":
        row[spec.columns[0]] = value.valid_from
        row[spec.columns[1]] = value.valid_to
    elif spec.adapter == "typed_value":
        row[spec.columns[0]] = value.value_type
        row[spec.columns[1]] = value.value
    else:
        row[spec.columns[0]] = value.artifact_id
        row[spec.columns[1]] = value.artifact_version
        row[spec.columns[2]] = value.content_sha256
        if include_subject_kind:
            if isinstance(value, sklegal_domain.ArtifactBinding):
                row["subject_kind"] = "work_product_version"
            else:
                row["subject_kind"] = value.subject_kind


def _relation_domain_values(
    entity: DomainEntity, spec: RelationField
) -> tuple[Any, ...]:
    value = getattr(entity, spec.field)
    if spec.mode in {"source_one", "source_optional", "entity_one", "entity_optional"}:
        return () if value is None else (value,)
    return tuple(value)


ADMINISTRATIVE_BOOTSTRAP_ENTITIES = frozenset(
    {"Tenant", "Client", "Engagement", "Matter"}
)
CONTROLLED_WRITER_ENTITIES = frozenset(
    {"Communication", "ExecutionEvent", "ExecutionReceipt"}
)
CONTROLLED_EVOLUTION_ENTITIES = frozenset(
    {"Approval", "Execution", "WorkProduct", "WorkProductVersion"}
)
DATABASE_OUTPUT_LEAVES = frozenset(
    {
        "captured_at",
        "created_at",
        "relation_created_at",
        "sequence_no",
        "system_from",
        "system_to",
        "updated_at",
        "version",
    }
)
EXECUTION_EVENT_OUTPUT_LEAVES = frozenset(
    {
        "actor_principal_id",
        "classification",
        "completeness",
        "id",
        "occurred_at",
    }
)
EXECUTION_RECEIPT_OUTPUT_LEAVES = frozenset(
    {
        "artifact_content_sha256",
        "classification",
        "completeness",
        "destination_sha256",
    }
)
EXECUTION_RECEIPT_CANONICAL_INPUT_LEAVES = frozenset(
    {
        "received_at",
        "verified_at",
    }
)
APPROVAL_OUTPUT_LEAVES = frozenset(
    {
        "decided_at",
        "reviewer_principal_id",
        "revoked_at",
        "revoker_principal_id",
    }
)


def _leaf_paths(value: Any, prefix: str) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        if not value:
            return (prefix,)
        return tuple(
            path
            for key, nested in value.items()
            for path in _leaf_paths(nested, f"{prefix}.{key}")
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not value:
            return (prefix,)
        return tuple(
            path
            for index, nested in enumerate(value)
            for path in _leaf_paths(nested, f"{prefix}[{index}]")
        )
    return (prefix,)


def _decomposition_leaf_paths(
    row: Mapping[str, Any],
    relations: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[str, ...]:
    paths = list(_leaf_paths(row, "row"))
    for relation, relation_rows in relations.items():
        paths.extend(_leaf_paths(relation_rows, f"relations.{relation}"))
    return tuple(paths)


def _write_authority(entity_name: str) -> WriteAuthority:
    if entity_name in ADMINISTRATIVE_BOOTSTRAP_ENTITIES:
        return "administrative_bootstrap"
    if entity_name in CONTROLLED_WRITER_ENTITIES:
        return "controlled_writer"
    if entity_name in CONTROLLED_EVOLUTION_ENTITIES:
        return "runtime_rls_with_controlled_evolution"
    return "runtime_rls"


def _write_operation(entity_name: str) -> str:
    return {
        "Approval": "insert_pending_then_transition_approval",
        "Communication": "create_or_revise_communication",
        "Execution": "insert_draft_then_transition_execution",
        "ExecutionEvent": "transition_execution_generated_event",
        "ExecutionReceipt": "transition_execution_generated_receipt",
        "WorkProduct": "insert_draft_then_controlled_evolution",
        "WorkProductVersion": "insert_draft_then_transition_version",
    }.get(entity_name, "insert_normalized_rows")


def _is_database_output_path(entity_name: str, path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1]
    receipt_path = entity_name == "ExecutionReceipt" or ".receipt[" in path
    if receipt_path and leaf in EXECUTION_RECEIPT_CANONICAL_INPUT_LEAVES:
        return False
    if leaf in DATABASE_OUTPUT_LEAVES:
        return True
    if ".aliases[" in path and leaf == "id":
        return True
    event_path = entity_name == "ExecutionEvent" or ".events[" in path
    if event_path and leaf in EXECUTION_EVENT_OUTPUT_LEAVES:
        return True
    if receipt_path and leaf in EXECUTION_RECEIPT_OUTPUT_LEAVES:
        return True
    if entity_name == "Approval" and leaf in APPROVAL_OUTPUT_LEAVES:
        return True
    if entity_name == "Execution" and ".approval[" in path:
        return leaf in APPROVAL_OUTPUT_LEAVES
    return False


def _build_write_contract(
    entity_name: str,
    row: Mapping[str, Any],
    relations: Mapping[str, Sequence[Mapping[str, Any]]],
) -> DecompositionWriteContract:
    all_paths = _decomposition_leaf_paths(row, relations)
    database_outputs = tuple(
        path for path in all_paths if _is_database_output_path(entity_name, path)
    )
    canonical_inputs = tuple(path for path in all_paths if path not in database_outputs)
    auxiliary_writes: tuple[AuxiliaryWrite, ...] = ()
    if entity_name == "Authority":
        auxiliary_writes = (
            AuxiliaryWrite(
                operation="ensure_authority_identity",
                table="sklegal_legal.authority_identities",
                row={
                    "tenant_id": row["tenant_id"],
                    "matter_id": row["matter_id"],
                    "id": row["id"],
                },
            ),
        )
    return DecompositionWriteContract(
        authority=_write_authority(entity_name),
        operation=_write_operation(entity_name),
        canonical_input_paths=canonical_inputs,
        database_output_paths=database_outputs,
        auxiliary_writes=auxiliary_writes,
    )


def decompose(
    entity: DomainEntity,
    metadata: PersistenceMetadata | None = None,
) -> DecomposedEntity:
    """Serialize all canonical fields into a closed normalized write payload."""

    entity_name = type(entity).__name__
    try:
        spec = MAPPINGS[entity_name]
    except KeyError as exc:
        raise MappingContractError(f"unknown domain entity: {entity_name}") from exc
    supplied = metadata or PersistenceMetadata()
    scalar_metadata = dict(supplied.scalar)
    if entity_name == "Execution":
        execution = cast("sklegal_domain.Execution", entity)
        derived_values = {
            "validation_result_id": (
                execution.validation_result.id if execution.validation_result else None
            ),
            "approval_id": execution.approval.id if execution.approval else None,
            "approval_version": (
                execution.approval.version if execution.approval else None
            ),
        }
        for column, value in derived_values.items():
            if column in scalar_metadata and scalar_metadata[column] != value:
                raise MappingContractError(
                    f"Execution persistence metadata disagrees with {column}"
                )
            scalar_metadata[column] = value
    unknown_scalar = sorted(set(scalar_metadata) - set(spec.persistence_columns))
    if unknown_scalar:
        raise MappingContractError(
            f"{entity_name} has undeclared persistence metadata: {unknown_scalar[0]}"
        )
    missing_scalar = sorted(
        set(spec.required_persistence_columns) - set(scalar_metadata)
    )
    if missing_scalar:
        raise MappingContractError(
            f"{entity_name} is missing persistence metadata: {missing_scalar[0]}"
        )
    expected_relations = {relation.relation for relation in spec.relations}
    unknown_relation_metadata = sorted(set(supplied.relations) - expected_relations)
    if unknown_relation_metadata:
        raise MappingContractError(
            f"{entity_name} has undeclared relation metadata: "
            f"{unknown_relation_metadata[0]}"
        )
    row: dict[str, Any] = {
        column: _plain(getattr(entity, domain_field))
        for domain_field, column in spec.direct
    }
    for composite in spec.composites:
        _encode_composite(
            composite,
            getattr(entity, composite.field),
            row,
            include_subject_kind="subject_kind" in spec.derived_columns,
        )
    row.update(scalar_metadata)
    relation_rows: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for relation in spec.relations:
        values = _relation_domain_values(entity, relation)
        metadata_rows = supplied.relations.get(relation.relation)
        if metadata_rows is None:
            metadata_rows = tuple({} for _ in values)
        if len(metadata_rows) != len(values):
            raise MappingContractError(
                f"{relation.relation} persistence metadata cardinality differs"
            )
        encoded_rows: list[Mapping[str, Any]] = []
        for value, metadata_row in zip(values, metadata_rows, strict=True):
            if relation.mode in {"entity_one", "entity_optional", "entities"}:
                required_nested_metadata = {
                    "nested_metadata",
                    *relation.required_persistence_columns,
                }
                allowed_nested_metadata = {
                    "nested_metadata",
                    *relation.persistence_columns,
                }
                if not required_nested_metadata <= set(metadata_row):
                    raise MappingContractError(
                        f"{relation.relation} is missing nested snapshot metadata"
                    )
                if not set(metadata_row) <= allowed_nested_metadata:
                    raise MappingContractError(
                        f"{relation.relation} has undeclared nested snapshot metadata"
                    )
                nested_metadata = metadata_row["nested_metadata"]
                if not isinstance(nested_metadata, PersistenceMetadata):
                    raise MappingContractError(
                        f"{relation.relation} nested metadata is invalid"
                    )
                nested = decompose(value, nested_metadata)
                physical_nested_row = dict(nested.row)
                for (
                    physical_column,
                    canonical_column,
                ) in relation.nested_column_bindings:
                    if physical_column in physical_nested_row:
                        raise MappingContractError(
                            f"{relation.relation} nested snapshot column is ambiguous: "
                            f"{physical_column}"
                        )
                    physical_nested_row[physical_column] = physical_nested_row.pop(
                        canonical_column
                    )
                for column, expected in relation.nested_default_columns:
                    actual = physical_nested_row.pop(column)
                    if actual != expected:
                        raise MappingContractError(
                            f"{relation.relation} nested snapshot disagrees with "
                            f"{column}"
                        )
                for column in relation.persistence_columns:
                    if column in metadata_row:
                        physical_nested_row[column] = metadata_row[column]
                encoded_rows.append(
                    {
                        "row": physical_nested_row,
                        "relations": dict(nested.relations),
                    }
                )
                continue
            unknown = sorted(set(metadata_row) - set(relation.persistence_columns))
            missing = sorted(
                set(relation.required_persistence_columns) - set(metadata_row)
            )
            if unknown:
                raise MappingContractError(
                    f"{relation.relation} has undeclared persistence metadata: "
                    f"{unknown[0]}"
                )
            if missing:
                raise MappingContractError(
                    f"{relation.relation} is missing persistence metadata: {missing[0]}"
                )
            encoded: dict[str, Any] = dict(metadata_row)
            if relation.mode == "ids":
                assert relation.value_column is not None
                encoded[relation.value_column] = value
            elif relation.mode in {"source_one", "source_optional", "sources"}:
                encoded.update(
                    {
                        "id": value.source_reference_id,
                        "source_system": value.source_system,
                        "source_version": value.source_version,
                        "content_sha256": value.content_sha256,
                        "locator": value.locator,
                        "observed_at": value.observed_at,
                    }
                )
                if relation.field == "source_reference":
                    row["source_reference_id"] = value.source_reference_id
            else:
                encoded.update(
                    {
                        "source_system": value.source_system,
                        "legacy_record_kind": _plain(value.record_kind),
                        "legacy_id": value.legacy_id,
                        "legacy_slug": value.legacy_slug,
                        "legacy_path": value.legacy_path,
                        "source_version": value.source_version,
                        "content_sha256": value.content_sha256,
                        "observed_at": value.observed_at,
                    }
                )
            encoded_rows.append(encoded)
        relation_rows[relation.relation] = tuple(encoded_rows)
    if "source_reference_id" in spec.derived_columns:
        row.setdefault("source_reference_id", None)
    for relation in spec.relations:
        _validate_relation_bindings(
            relation,
            row,
            relation_rows[relation.relation],
        )
    return DecomposedEntity(
        entity_name,
        spec.table,
        row,
        relation_rows,
        _build_write_contract(entity_name, row, relation_rows),
    )


def validate_mapping_contract() -> None:
    """Prove bidirectional coverage of exported entities and model fields."""

    exported = {
        name
        for name in sklegal_domain.__all__
        if inspect.isclass(entity_type := getattr(sklegal_domain, name, None))
        and issubclass(entity_type, DomainEntity)
        and entity_type is not DomainEntity
    }
    if exported != set(MAPPINGS):
        raise MappingContractError("mapping set differs from exported DomainEntity set")
    for name, spec in MAPPINGS.items():
        covered = {field for field, _ in spec.direct}
        covered.update(item.field for item in spec.composites)
        covered.update(item.field for item in spec.relations)
        fields = set(getattr(sklegal_domain, name).model_fields)
        if covered != fields:
            missing = sorted(fields - covered)
            extra = sorted(covered - fields)
            raise MappingContractError(
                f"{name} mapping differs from model fields; missing={missing}, extra={extra}"
            )
