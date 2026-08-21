"""Translate production decomposition output into synthetic PostgreSQL writes.

This adapter is deliberately test-only. It consumes the closed production
mapping contract and renders literals solely for synthetic values created by
the persistence acceptance suite. Application code must use a database driver
with bound parameters.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sklegal_persistence.mapping import (
    MAPPINGS,
    SOURCE_COLUMNS,
    SOURCE_ROW_PERSISTENCE,
    DecomposedEntity,
    MappingContractError,
)

READ_ONLY_COLUMNS = frozenset({"canonical_matter_event_id", "system_to"})


def _literal(value: Any, *, column: str) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, UUID):
        return "'" + str(value) + "'::uuid"
    if isinstance(value, datetime):
        return "'" + value.isoformat().replace("'", "''") + "'::timestamptz"
    if isinstance(value, bytes):
        return "decode('" + value.hex() + "', 'hex')"
    if column == "asserted_value" or isinstance(value, (dict, list)):
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return "'" + encoded.replace("'", "''") + "'::jsonb"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise MappingContractError(
        f"synthetic write adapter cannot encode {column}: {type(value).__name__}"
    )


def insert_statement(
    table: str,
    row: Mapping[str, Any],
    *,
    overrides: Mapping[str, Any] | None = None,
) -> str:
    """Render one closed synthetic INSERT from a decomposed physical row."""

    values = dict(row)
    values.update(overrides or {})
    for column in READ_ONLY_COLUMNS:
        values.pop(column, None)
    if not values:
        raise MappingContractError(f"{table} write row is empty")
    columns = tuple(values)
    rendered = ", ".join(_literal(values[column], column=column) for column in columns)
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({rendered});"


def auxiliary_statements(payload: DecomposedEntity) -> tuple[str, ...]:
    """Render only auxiliary rows declared by the production write contract."""

    return tuple(
        insert_statement(write.table, write.row)
        for write in payload.write_contract.auxiliary_writes
    )


def create_communication_statement(payload: DecomposedEntity) -> str:
    """Render the controlled Communication creation command from decomposition."""

    if (
        payload.entity_name != "Communication"
        or payload.write_contract.operation != "create_or_revise_communication"
    ):
        raise MappingContractError("payload is not a controlled Communication write")
    participants = payload.relations["participants"]
    participant_values = ", ".join(
        _literal(row["party_id"], column="party_id") for row in participants
    )
    participant_array = f"ARRAY[{participant_values}]::uuid[]"
    row = payload.row
    arguments = (
        _literal(row["tenant_id"], column="tenant_id"),
        _literal(row["matter_id"], column="matter_id"),
        _literal(row["id"], column="id"),
        _literal(row["direction"], column="direction"),
        _literal(row["channel"], column="channel"),
        _literal(row["subject"], column="subject"),
        participant_array,
        _literal(row["work_product_version_id"], column="work_product_version_id"),
        _literal(
            row["work_product_version_number"],
            column="work_product_version_number",
        ),
        _literal(
            row["work_product_content_sha256"],
            column="work_product_content_sha256",
        ),
        _literal(row["classification"], column="classification"),
        _literal(row["completeness"], column="completeness"),
        _literal(row["created_at"], column="created_at"),
        _literal(row["updated_at"], column="updated_at"),
    )
    return (
        "SELECT status FROM sklegal_legal.create_communication("
        + ", ".join(arguments)
        + ");"
    )


def relation_statements(payload: DecomposedEntity) -> tuple[str, ...]:
    """Render declared non-nested relation writes without duplicating mappings."""

    mapping = MAPPINGS[payload.entity_name]
    statements: list[str] = []
    for relation in mapping.relations:
        rows = payload.relations[relation.relation]
        if relation.mode in {"entity_one", "entity_optional", "entities"}:
            continue
        if relation.mode in {"source_one", "source_optional", "sources"}:
            value_table = relation.value_relation or relation.write_relation
            if value_table is None:
                raise MappingContractError(
                    f"{payload.entity_name}.{relation.field} has no value write relation"
                )
            for row in rows:
                source_row = {
                    column: row[column]
                    for column in SOURCE_COLUMNS + SOURCE_ROW_PERSISTENCE
                }
                statements.append(insert_statement(value_table, source_row))
                if relation.value_relation is not None:
                    if relation.write_relation is None:
                        raise MappingContractError(
                            f"{payload.entity_name}.{relation.field} has no link relation"
                        )
                    owner_columns = {
                        column: row[column]
                        for column in relation.persistence_columns
                        if column not in SOURCE_ROW_PERSISTENCE
                        and column != "relation_created_at"
                    }
                    link_row = {
                        "tenant_id": row["tenant_id"],
                        "matter_id": row["matter_id"],
                        **owner_columns,
                        "source_reference_id": row["id"],
                        "created_at": row["relation_created_at"],
                    }
                    statements.append(
                        insert_statement(relation.write_relation, link_row)
                    )
            continue
        if relation.write_relation is not None:
            statements.extend(
                insert_statement(relation.write_relation, row) for row in rows
            )
    return tuple(statements)
