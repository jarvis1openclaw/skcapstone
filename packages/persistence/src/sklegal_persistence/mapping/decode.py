"""Strict normalized row reconstruction into canonical legal entities."""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

import sklegal_domain
from sklegal_domain.base import DomainEntity

from .contract import (
    CompositeField,
    MappingContractError,
    PersistenceMetadata,
    Reconstruction,
    RelationField,
)
from .mappings import MAPPINGS

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
