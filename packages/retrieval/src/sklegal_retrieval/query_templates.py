"""Closed, digest-pinned retrieval query-template registry."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .contract import RetrievalContract, load_retrieval_contract

MAX_IDENTIFIER_BYTES = 255
MAX_VECTOR_VALUES = 16_384
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*$")


class QueryTemplateError(ValueError):
    """A template ID or parameter set is not part of the closed registry."""


class ParameterKind(StrEnum):
    """Supported closed parameter types."""

    TEXT = "text"
    INTEGER = "integer"
    IDENTIFIER_LIST = "identifier_list"
    NUMBER_LIST = "number_list"


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """One immutable type and size contract for a template parameter."""

    name: str
    kind: ParameterKind
    min_value: int | float | None = None
    max_value: int | float | None = None
    min_items: int | None = None
    max_items: int | None = None
    max_utf8_bytes: int | None = None

    def manifest(self) -> dict[str, object]:
        """Return the exact JSON-compatible representation used by the pin."""

        return {
            "kind": self.kind.value,
            "max_items": self.max_items,
            "max_utf8_bytes": self.max_utf8_bytes,
            "max_value": self.max_value,
            "min_items": self.min_items,
            "min_value": self.min_value,
            "name": self.name,
            "required": True,
        }

    def bind(self, value: object) -> object:
        """Validate and normalize one parameter value."""

        if self.kind is ParameterKind.TEXT:
            return self._bind_text(value)
        if self.kind is ParameterKind.INTEGER:
            return self._bind_integer(value)
        if self.kind is ParameterKind.IDENTIFIER_LIST:
            return self._bind_identifier_list(value)
        if self.kind is ParameterKind.NUMBER_LIST:
            return self._bind_number_list(value)
        raise QueryTemplateError(f"unsupported parameter type for {self.name}")

    def _bind_text(self, value: object) -> str:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise QueryTemplateError(f"{self.name} must be non-empty text")
        if self.max_utf8_bytes is None:
            raise QueryTemplateError(f"{self.name} has no text bound")
        if len(value.encode("utf-8")) > self.max_utf8_bytes:
            raise QueryTemplateError(f"{self.name} exceeds its byte limit")
        return value

    def _bind_integer(self, value: object) -> int:
        if type(value) is not int:
            raise QueryTemplateError(f"{self.name} must be an integer")
        if self.min_value is None or self.max_value is None:
            raise QueryTemplateError(f"{self.name} has no numeric bound")
        if value < self.min_value or value > self.max_value:
            raise QueryTemplateError(f"{self.name} is outside its approved range")
        return value

    def _bind_identifier_list(self, value: object) -> tuple[str, ...]:
        values = self._bounded_sequence(value)
        normalized: list[str] = []
        for item in values:
            if not isinstance(item, str) or not item or "\x00" in item:
                raise QueryTemplateError(
                    f"{self.name} must contain non-empty identifiers"
                )
            if len(item.encode("utf-8")) > MAX_IDENTIFIER_BYTES:
                raise QueryTemplateError(
                    f"{self.name} contains an oversized identifier"
                )
            if _IDENTIFIER_PATTERN.fullmatch(item) is None:
                raise QueryTemplateError(f"{self.name} contains an invalid identifier")
            normalized.append(item)
        if len(set(normalized)) != len(normalized):
            raise QueryTemplateError(f"{self.name} must contain unique identifiers")
        return tuple(normalized)

    def _bind_number_list(self, value: object) -> tuple[float, ...]:
        values = self._bounded_sequence(value)
        normalized: list[float] = []
        for item in values:
            if not isinstance(item, (int, float)) or isinstance(item, bool):
                raise QueryTemplateError(f"{self.name} must contain only numbers")
            number = float(item)
            if not math.isfinite(number):
                raise QueryTemplateError(
                    f"{self.name} must contain only finite numbers"
                )
            normalized.append(number)
        return tuple(normalized)

    def _bounded_sequence(self, value: object) -> tuple[object, ...] | list[object]:
        if not isinstance(value, (tuple, list)):
            raise QueryTemplateError(f"{self.name} must be a list or tuple")
        if self.min_items is None or self.max_items is None:
            raise QueryTemplateError(f"{self.name} has no item bound")
        if len(value) < self.min_items or len(value) > self.max_items:
            raise QueryTemplateError(f"{self.name} is outside its item limit")
        return value


@dataclass(frozen=True, slots=True)
class QueryTemplate:
    """Immutable closed query operation and its exact definition pin."""

    template_id: str
    version: str
    component: str
    operation: str
    parameters: tuple[ParameterSpec, ...]
    graph_optional: bool
    definition_sha256: str

    @property
    def query_template_id(self) -> str:
        """Return the trace-compatible template identifier."""

        return self.template_id

    @property
    def query_template_version(self) -> str:
        """Return the trace-compatible template version."""

        return self.version

    @property
    def query_template_sha256(self) -> str:
        """Return the trace-compatible exact definition hash."""

        return self.definition_sha256

    def canonical_bytes(self) -> bytes:
        """Return the canonical bytes covered by ``definition_sha256``."""

        manifest = {
            "component": self.component,
            "graph_optional": self.graph_optional,
            "operation": self.operation,
            "parameters": [parameter.manifest() for parameter in self.parameters],
            "template_id": self.template_id,
            "version": self.version,
        }
        return json.dumps(
            manifest,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")


@dataclass(frozen=True, slots=True)
class BoundQueryTemplate:
    """A closed template paired with validated immutable parameters."""

    template: QueryTemplate
    parameters: Mapping[str, object]


def _text(name: str) -> ParameterSpec:
    return ParameterSpec(
        name=name,
        kind=ParameterKind.TEXT,
        max_utf8_bytes=16_384,
    )


def _max_results() -> ParameterSpec:
    return ParameterSpec(
        name="max_results",
        kind=ParameterKind.INTEGER,
        min_value=1,
        max_value=100,
    )


def _graph_depth() -> ParameterSpec:
    return ParameterSpec(
        name="graph_depth",
        kind=ParameterKind.INTEGER,
        min_value=1,
        max_value=3,
    )


def _identifiers(name: str) -> ParameterSpec:
    return ParameterSpec(
        name=name,
        kind=ParameterKind.IDENTIFIER_LIST,
        min_items=1,
        max_items=100,
        max_utf8_bytes=MAX_IDENTIFIER_BYTES,
    )


def _embedding() -> ParameterSpec:
    return ParameterSpec(
        name="query_embedding",
        kind=ParameterKind.NUMBER_LIST,
        min_items=1,
        max_items=MAX_VECTOR_VALUES,
    )


_TEMPLATE_DEFINITIONS: tuple[
    tuple[str, str, str, bool, tuple[ParameterSpec, ...]], ...
] = (
    (
        "lexical.search.v1",
        "lexical",
        "postgresql.lexical.search",
        False,
        (_text("query_text"), _max_results()),
    ),
    (
        "lexical.count.v1",
        "lexical",
        "postgresql.lexical.count",
        False,
        (_text("query_text"),),
    ),
    (
        "vector.exact.v1",
        "vector",
        "postgresql.vector.exact",
        False,
        (_embedding(), _max_results()),
    ),
    (
        "hybrid.rrf.v1",
        "hybrid",
        "postgresql.hybrid.reciprocal_rank_fusion",
        False,
        (_text("query_text"), _embedding(), _max_results()),
    ),
    (
        "graph.entity.v1",
        "graph",
        "apache_age.graph.entity",
        True,
        (_identifiers("entity_ids"), _max_results()),
    ),
    (
        "graph.neighbors.v1",
        "graph",
        "apache_age.graph.neighbors",
        True,
        (_identifiers("entity_ids"), _graph_depth(), _max_results()),
    ),
    (
        "graph.paths_bounded.v1",
        "graph",
        "apache_age.graph.paths_bounded",
        True,
        (
            _identifiers("source_entity_ids"),
            _identifiers("target_entity_ids"),
            _graph_depth(),
            _max_results(),
        ),
    ),
    (
        "graph.claim_support.v1",
        "graph",
        "apache_age.graph.claim_support",
        True,
        (_identifiers("claim_entity_ids"), _max_results()),
    ),
    (
        "graph.authority_citations.v1",
        "graph",
        "apache_age.graph.authority_citations",
        True,
        (_identifiers("authority_entity_ids"), _max_results()),
    ),
    (
        "graph.source_lineage.v1",
        "graph",
        "apache_age.graph.source_lineage",
        True,
        (_identifiers("source_ids"), _graph_depth(), _max_results()),
    ),
    (
        "graph.scope_count.v1",
        "graph",
        "apache_age.graph.scope_count",
        True,
        (),
    ),
    (
        "graph.exists.v1",
        "graph",
        "apache_age.graph.exists",
        True,
        (_identifiers("entity_ids"),),
    ),
)

_PINNED_TEMPLATE_HASHES = {
    "lexical.search.v1": "7ad994a83043212c7ba22cffd45bf94f1e49b4971963ea78197cebefa4709db0",  # pragma: allowlist secret
    "lexical.count.v1": "ccb53c082c64d60a5dbee8a8a4d711db80f8f8aaa0bac0298461ed50febe2158",  # pragma: allowlist secret
    "vector.exact.v1": "cb90345bd9682b5b9fd4d08a1f919f27713870e059d765e9b33893b278d8cf61",  # pragma: allowlist secret
    "hybrid.rrf.v1": "45806ef8534bf283ff7dc3e40c96d5a3175df6fe2d2f9e682ab9798553e49c2a",  # pragma: allowlist secret
    "graph.entity.v1": "2a17f4fc0a68c19a8dc583525488487bb7cc844e6e3e35b773f0d00c502e17d5",  # pragma: allowlist secret
    "graph.neighbors.v1": "f77e9a793de8404db3067e127be3a9dc38a6a4f56e9da318961dc16cb6fabbf2",  # pragma: allowlist secret
    "graph.paths_bounded.v1": "e140ea905bce30df965a9fd056710f19b370cc64ccc80d718094ff117848333e",  # pragma: allowlist secret
    "graph.claim_support.v1": "9f7526f944c26fcf2934f7fc07e333ac03b03b2ecb713a8c112cd6d6e933d388",  # pragma: allowlist secret
    "graph.authority_citations.v1": "b056f75c677f29e0c9fabfdd827e2a0e4b34df61951e5e72cc7cb2f0778d47f6",  # pragma: allowlist secret
    "graph.source_lineage.v1": "8cd3b4c26088315b893e5fab79be4f40b0df1d1e5dfe0c870057cec3350844a1",  # pragma: allowlist secret
    "graph.scope_count.v1": "798d2866bc7425a29cde59cffec6159b8b6f811bb89167028f62cbc325e94dbd",  # pragma: allowlist secret
    "graph.exists.v1": "b973f32887c58b857ccf10a225d6056ffd435c77de046746d9d035a9a31f8dce",  # pragma: allowlist secret
}


def _make_template(
    template_id: str,
    component: str,
    operation: str,
    graph_optional: bool,
    parameters: tuple[ParameterSpec, ...],
) -> QueryTemplate:
    template = QueryTemplate(
        template_id=template_id,
        version="1",
        component=component,
        operation=operation,
        parameters=parameters,
        graph_optional=graph_optional,
        definition_sha256=_PINNED_TEMPLATE_HASHES[template_id],
    )
    calculated = hashlib.sha256(template.canonical_bytes()).hexdigest()
    if calculated != template.definition_sha256:
        raise RuntimeError(f"query-template pin mismatch: {template_id}")
    return template


_templates = {
    template_id: _make_template(template_id, component, operation, optional, parameters)
    for template_id, component, operation, optional, parameters in _TEMPLATE_DEFINITIONS
}
QUERY_TEMPLATES: Mapping[str, QueryTemplate] = MappingProxyType(_templates)
QUERY_TEMPLATE_HASHES: Mapping[str, str] = MappingProxyType(
    {
        template_id: template.definition_sha256
        for template_id, template in _templates.items()
    }
)

_FORBIDDEN_PARAMETER_NAMES = frozenset(
    {
        "config",
        "configuration",
        "cypher",
        "filter",
        "filters",
        "graph",
        "graph_name",
        "order",
        "order_by",
        "order_expression",
        "raw_cypher",
        "raw_filter",
        "raw_sql",
        "raw_tsquery",
        "sql",
        "text_search_config",
        "text_search_configuration",
        "tsquery",
    }
)


def _normalized_parameter_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _validate_registry_contract(contract: RetrievalContract) -> None:
    if tuple(QUERY_TEMPLATES) != contract.query_template_ids:
        raise QueryTemplateError("query-template registry does not match the contract")
    limits = contract.parameter_limits
    if (
        limits.max_results != 100
        or limits.max_entity_ids != 100
        or limits.max_graph_depth != 3
        or limits.max_query_bytes != 16_384
    ):
        raise QueryTemplateError("query-template limits do not match the contract")


def get_query_template(template_id: str) -> QueryTemplate:
    """Resolve one fixed template ID after validating the machine contract."""

    if not isinstance(template_id, str):
        raise QueryTemplateError("query-template ID must be text")
    contract = load_retrieval_contract()
    _validate_registry_contract(contract)
    try:
        return QUERY_TEMPLATES[template_id]
    except KeyError as exc:
        raise QueryTemplateError("query-template ID is not approved") from exc


def bind_query_template(
    template_id: str, parameters: Mapping[str, object]
) -> BoundQueryTemplate:
    """Bind typed values to a fixed template without accepting query syntax."""

    template = get_query_template(template_id)
    if not isinstance(parameters, Mapping):
        raise QueryTemplateError("query-template parameters must be a mapping")

    provided: dict[str, object] = {}
    for name, value in parameters.items():
        if not isinstance(name, str):
            raise QueryTemplateError("query-template parameter names must be text")
        normalized_name = _normalized_parameter_name(name)
        if normalized_name in _FORBIDDEN_PARAMETER_NAMES:
            raise QueryTemplateError("raw query controls are not accepted")
        provided[name] = value

    specifications = {parameter.name: parameter for parameter in template.parameters}
    if set(provided) != set(specifications):
        raise QueryTemplateError(
            "query-template parameters do not match the approved set"
        )

    bound = {name: specifications[name].bind(provided[name]) for name in specifications}
    all_entity_ids: set[str] = set()
    for name, value in bound.items():
        if specifications[name].kind is not ParameterKind.IDENTIFIER_LIST:
            continue
        if not isinstance(value, tuple) or not all(
            isinstance(item, str) for item in value
        ):
            raise QueryTemplateError("identifier normalization failed")
        all_entity_ids.update(value)
    if len(all_entity_ids) > 100:
        raise QueryTemplateError("query-template parameters exceed the entity limit")
    return BoundQueryTemplate(template=template, parameters=MappingProxyType(bound))


__all__ = [
    "BoundQueryTemplate",
    "ParameterKind",
    "ParameterSpec",
    "QUERY_TEMPLATES",
    "QUERY_TEMPLATE_HASHES",
    "QueryTemplate",
    "QueryTemplateError",
    "bind_query_template",
    "get_query_template",
]
