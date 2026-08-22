"""Fail-closed loader for the approved retrieval partition contract."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTRACT_SCHEMA = "sklegal-retrieval-partition/v2"
EXPECTED_CONTRACT_SHA256 = "bad12b82baa7133114a23a7fb2313ef871a513777c0c17cabe017b57f9f06e7f"  # pragma: allowlist secret
DEFAULT_CONTRACT_PATH = (
    Path(__file__).resolve().parents[4]
    / "config"
    / "retrieval"
    / "tenant-partition-contract.json"
)
MAX_CONTRACT_BYTES = 1_048_576

EXPECTED_QUERY_TEMPLATE_IDS = (
    "lexical.search.v1",
    "lexical.count.v1",
    "vector.exact.v1",
    "hybrid.rrf.v1",
    "graph.entity.v1",
    "graph.neighbors.v1",
    "graph.paths_bounded.v1",
    "graph.claim_support.v1",
    "graph.authority_citations.v1",
    "graph.source_lineage.v1",
    "graph.scope_count.v1",
    "graph.exists.v1",
)

_EXPECTED_PARAMETER_LIMITS = {
    "max_results": 100,
    "max_entity_ids": 100,
    "max_graph_depth": 3,
    "max_query_bytes": 16_384,
}

_EXACT_CONTRACT_VALUES: tuple[tuple[tuple[str, ...], object], ...] = (
    (("topology", "live_skmem_pg_reuse"), "forbidden"),
    (("topology", "shared_core_retrieval_cluster"), "forbidden"),
    (
        ("topology", "retrieval", "initial_backends"),
        ["postgresql_full_text_search", "pgvector"],
    ),
    (
        ("topology", "retrieval", "graph_backend", "activation"),
        "optional_qualification_gated",
    ),
    (("registry", "caller_may_supply_physical_name"), False),
    (("registry", "caller_may_derive_physical_name"), False),
    (("database_identity", "shared_runtime_login_allowed"), False),
    (("database_identity", "raw_credential_exposed_to_caller_model_or_log"), False),
    (("database_identity", "caller_set_guc_is_authorization_fact"), False),
    (
        ("database_identity", "authorization_identity"),
        "session_user",
    ),
    (
        (
            "database_identity",
            "all_gateway_and_template_database_references_schema_qualified",
        ),
        True,
    ),
    (
        ("database_identity", "pg_temp_may_shadow_referenced_relation_or_function"),
        False,
    ),
    (("lexical", "query_function"), "websearch_to_tsquery"),
    (("lexical", "text_search_configuration"), "registry_pinned"),
    (("lexical", "caller_may_supply_raw_sql"), False),
    (("lexical", "caller_may_supply_raw_tsquery"), False),
    (("lexical", "caller_may_supply_text_search_configuration"), False),
    (("lexical", "caller_may_supply_raw_filter"), False),
    (("lexical", "caller_may_supply_order_expression"), False),
    (("vector", "initial_search"), "exact"),
    (("vector", "caller_may_supply_raw_sql"), False),
    (("vector", "caller_may_supply_raw_filter"), False),
    (("graph", "activation"), "optional_qualification_gated"),
    (("graph", "caller_may_supply_raw_cypher"), False),
    (("graph", "caller_may_supply_graph_name"), False),
    (("query_scope", "authorization_before_registry_read"), True),
    (("query_scope", "authorization_before_backend_call"), True),
    (("query_scope", "authorization_after_backend_call"), True),
    (("query_scope", "template_parameters"), "typed_bounded_and_hash_pinned"),
    (("query_scope", "mixed_scope_response"), "reject_entire_response"),
    (("projection_consistency", "fallback_backend_on_outage"), "none"),
    (("legacy_backends", "new_protected_projection_allowed"), False),
    (("legacy_backends", "direct_protected_runtime_query_allowed"), False),
    (("legacy_backends", "legacy_alias_is_routing_eligible"), False),
)


class ContractValidationError(ValueError):
    """The retrieval contract is absent, malformed, or not approved."""


@dataclass(frozen=True, slots=True)
class ParameterLimits:
    """Approved hard limits for all closed query templates."""

    max_results: int
    max_entity_ids: int
    max_graph_depth: int
    max_query_bytes: int


@dataclass(frozen=True, slots=True)
class RetrievalContract:
    """Validated security-relevant slice of the machine contract."""

    schema: str
    query_template_ids: tuple[str, ...]
    parameter_limits: ParameterLimits
    graph_is_optional: bool
    source_path: Path
    file_sha256: str


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractValidationError(f"duplicate contract key: {key}")
        result[key] = value
    return result


def _read_contract_bytes(path: Path) -> bytes:
    if path.is_symlink():
        raise ContractValidationError("retrieval contract must not be a symlink")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except (FileNotFoundError, OSError) as exc:
        raise ContractValidationError("retrieval contract is unavailable") from exc

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ContractValidationError("retrieval contract must be a regular file")
        if metadata.st_size <= 0 or metadata.st_size > MAX_CONTRACT_BYTES:
            raise ContractValidationError("retrieval contract size is invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(MAX_CONTRACT_BYTES + 1)
    finally:
        os.close(descriptor)

    if not payload or len(payload) > MAX_CONTRACT_BYTES:
        raise ContractValidationError("retrieval contract size is invalid")
    return payload


def _contract_value(document: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = document
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise ContractValidationError(f"missing contract field: {'.'.join(path)}")
        current = current[key]
    return current


def _expect_exact(
    document: dict[str, Any], path: tuple[str, ...], expected: object
) -> None:
    actual = _contract_value(document, path)
    if type(actual) is not type(expected) or actual != expected:
        raise ContractValidationError(f"unapproved contract value: {'.'.join(path)}")


def _validate_document(
    document: Any, source_path: Path, file_sha256: str
) -> RetrievalContract:
    if not isinstance(document, dict):
        raise ContractValidationError("retrieval contract root must be an object")

    if file_sha256 != EXPECTED_CONTRACT_SHA256:
        raise ContractValidationError("retrieval contract digest is not approved")

    _expect_exact(document, ("schema",), CONTRACT_SCHEMA)
    _expect_exact(
        document,
        ("query_scope", "query_templates"),
        list(EXPECTED_QUERY_TEMPLATE_IDS),
    )
    _expect_exact(
        document,
        ("query_scope", "parameter_limits"),
        _EXPECTED_PARAMETER_LIMITS,
    )
    for path, expected in _EXACT_CONTRACT_VALUES:
        _expect_exact(document, path, expected)

    return RetrievalContract(
        schema=CONTRACT_SCHEMA,
        query_template_ids=EXPECTED_QUERY_TEMPLATE_IDS,
        parameter_limits=ParameterLimits(**_EXPECTED_PARAMETER_LIMITS),
        graph_is_optional=True,
        source_path=source_path,
        file_sha256=file_sha256,
    )


def load_retrieval_contract(path: Path | None = None) -> RetrievalContract:
    """Load and validate the approved retrieval contract.

    No environment or caller-provided document can replace the default. A
    ``Path`` is accepted only to support isolated qualification fixtures.
    Invalid JSON, duplicate keys, missing gates, relaxed prohibitions, and
    unknown template or limit sets all fail closed.
    """

    if path is None:
        path = DEFAULT_CONTRACT_PATH
    if not isinstance(path, Path):
        raise ContractValidationError("retrieval contract path must be a Path")

    source_path = path.absolute()
    payload = _read_contract_bytes(source_path)
    try:
        document = json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    except ContractValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError("retrieval contract is not valid JSON") from exc

    return _validate_document(
        document,
        source_path=source_path,
        file_sha256=hashlib.sha256(payload).hexdigest(),
    )


load_contract = load_retrieval_contract


__all__ = [
    "CONTRACT_SCHEMA",
    "DEFAULT_CONTRACT_PATH",
    "EXPECTED_CONTRACT_SHA256",
    "EXPECTED_QUERY_TEMPLATE_IDS",
    "ContractValidationError",
    "ParameterLimits",
    "RetrievalContract",
    "load_contract",
    "load_retrieval_contract",
]
