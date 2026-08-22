"""Mapping contract dataclasses and shared relation persistence constants.

Rows must contain native driver values such as ``UUID`` and UTC ``datetime``
objects. Normalized relations are supplied separately and are mandatory when
declared, including when an optional or many-valued relation is empty. This
keeps absent joins distinguishable from intentionally empty domain values.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

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
