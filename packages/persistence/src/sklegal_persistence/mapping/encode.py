"""Canonical entity decomposition and mapping contract validation."""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any, cast

import sklegal_domain
from sklegal_domain.base import DomainEntity

from .contract import (
    AuxiliaryWrite,
    CompositeField,
    DecomposedEntity,
    DecompositionWriteContract,
    MappingContractError,
    PersistenceMetadata,
    RelationField,
    WriteAuthority,
    _decomposition_leaf_paths,
)
from .decode import _validate_relation_bindings
from .mappings import MAPPINGS


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
