"""Immutable request, result, provenance, and cache values for retrieval."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
OpaqueId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*$",
    ),
]
SafeVersion = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:+-]*$",
    ),
]
PhysicalPartitionId = Annotated[
    str, StringConstraints(pattern=r"^(?:rp|rg)_[0-9a-f]{32}$")
]
PostgresLsn = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9A-F]+/[0-9A-F]+$")
]


def _contains_nil_uuid(value: object) -> bool:
    if isinstance(value, UUID):
        return value.int == 0
    if isinstance(value, BaseModel):
        return any(_contains_nil_uuid(item) for item in value.__dict__.values())
    if isinstance(value, Mapping):
        return any(_contains_nil_uuid(item) for item in value.values())
    if isinstance(value, (tuple, list, set, frozenset)):
        return any(_contains_nil_uuid(item) for item in value)
    return False


class RetrievalValue(BaseModel):
    """Strict immutable value with deterministic serialization."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
        revalidate_instances="always",
    )

    @model_validator(mode="after")
    def reject_nil_identifiers(self) -> Self:
        if _contains_nil_uuid(self.__dict__):
            raise ValueError("retrieval identifiers cannot be nil UUIDs")
        return self

    def canonical_json(self) -> str:
        """Return stable JSON suitable for hashes, traces, and cache keys."""
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self:
        if update:
            raise ValueError("unvalidated retrieval copy updates are disabled")
        return super().model_copy(deep=deep)

    @classmethod
    def model_construct(
        cls, _fields_set: set[str] | None = None, **values: Any
    ) -> Self:
        del _fields_set, values
        raise ValueError("unvalidated retrieval construction is disabled")


class ScopeKind(StrEnum):
    MATTER = "matter"
    TENANT_SHARED = "tenant_shared"


class RetrievalComponent(StrEnum):
    LEXICAL = "lexical"
    VECTOR = "vector"
    GRAPH = "graph"


class RetrievalMode(StrEnum):
    LEXICAL = "lexical"
    VECTOR_EXACT = "vector_exact"
    HYBRID_RRF = "hybrid_rrf"
    GRAPH = "graph"


class QueryTemplateId(StrEnum):
    LEXICAL_SEARCH_V1 = "lexical.search.v1"
    LEXICAL_COUNT_V1 = "lexical.count.v1"
    VECTOR_EXACT_V1 = "vector.exact.v1"
    HYBRID_RRF_V1 = "hybrid.rrf.v1"
    GRAPH_ENTITY_V1 = "graph.entity.v1"
    GRAPH_NEIGHBORS_V1 = "graph.neighbors.v1"
    GRAPH_PATHS_BOUNDED_V1 = "graph.paths_bounded.v1"
    GRAPH_CLAIM_SUPPORT_V1 = "graph.claim_support.v1"
    GRAPH_AUTHORITY_CITATIONS_V1 = "graph.authority_citations.v1"
    GRAPH_SOURCE_LINEAGE_V1 = "graph.source_lineage.v1"
    GRAPH_SCOPE_COUNT_V1 = "graph.scope_count.v1"
    GRAPH_EXISTS_V1 = "graph.exists.v1"


class DistanceMetric(StrEnum):
    COSINE = "cosine"
    L2 = "l2"
    INNER_PRODUCT = "inner_product"


class RankSignal(StrEnum):
    LEXICAL_RANK = "lexical_rank"
    VECTOR_DISTANCE = "vector_distance"
    HYBRID_RRF = "hybrid_rrf"
    GRAPH_TRAVERSAL = "graph_traversal"
    SCOPE_AGGREGATE = "scope_aggregate"


class RetrievalAggregateKind(StrEnum):
    COUNT = "count"
    EXISTS = "exists"


class IncompleteReason(StrEnum):
    UNAVAILABLE_UNQUALIFIED = "unavailable_unqualified"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    OPTIONAL_COMPONENT_STALE = "optional_component_stale"


class RetrievalScope(RetrievalValue):
    tenant_id: UUID
    scope_kind: ScopeKind
    matter_id: UUID | None

    @model_validator(mode="after")
    def validate_exact_scope(self) -> Self:
        if self.scope_kind is ScopeKind.MATTER and self.matter_id is None:
            raise ValueError("Matter scope requires an exact Matter identifier")
        if self.scope_kind is ScopeKind.TENANT_SHARED and self.matter_id is not None:
            raise ValueError("Tenant-shared scope requires a null Matter identifier")
        return self


class AuthorizationPins(RetrievalValue):
    principal_id: UUID
    scope: RetrievalScope
    authorization_decision_id: UUID
    principal_policy_context_sha256: Sha256
    policy_revision: Sha256
    rights_revision: Sha256
    required_core_watermark: int = Field(ge=0)
    tenant_shared_policy_decision_id: UUID | None = None

    @model_validator(mode="after")
    def validate_tenant_shared_decision(self) -> Self:
        shared = self.scope.scope_kind is ScopeKind.TENANT_SHARED
        if shared != (self.tenant_shared_policy_decision_id is not None):
            raise ValueError(
                "Tenant-shared scope requires one explicit policy decision"
            )
        return self


class CredentialBindingPins(RetrievalValue):
    credential_ref: OpaqueId
    database_principal: OpaqueId
    principal_id: UUID
    scope: RetrievalScope
    projection_set_id: UUID
    projection_generation: int = Field(ge=1)
    policy_revision: Sha256
    rights_revision: Sha256
    authorization_event_sequence: int = Field(ge=1)
    authorization_event_sha256: Sha256
    revoked_at: None = None


class QueryTemplatePins(RetrievalValue):
    query_template_id: QueryTemplateId
    query_template_version: SafeVersion
    query_template_sha256: Sha256


class LexicalPins(RetrievalValue):
    text_search_configuration: OpaqueId
    text_search_configuration_sha256: Sha256


class VectorPins(RetrievalValue):
    embedding_model_id: OpaqueId
    embedding_model_revision: SafeVersion
    embedding_dimension: int = Field(ge=1, le=16384)
    distance_metric: DistanceMetric


class GraphPins(RetrievalValue):
    graph_registry_id: OpaqueId
    exact_graph_gateway_credential_mapping_ref: OpaqueId
    graph_gateway_function_definition_sha256: Sha256
    apache_age_qualification_evidence_sha256: Sha256
    qualified: Literal[True] = True


class ReplicaPins(RetrievalValue):
    replica_replay_lsn: PostgresLsn
    required_replay_lsn: PostgresLsn | None = None


class ProjectionLag(RetrievalValue):
    lag_events: int = Field(ge=0)
    lag_seconds: float = Field(ge=0)

    @field_validator("lag_seconds")
    @classmethod
    def require_finite_lag(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("projection lag must be finite")
        return value


class ProjectionPins(RetrievalValue):
    component: RetrievalComponent
    scope: RetrievalScope
    projection_set_id: UUID
    physical_partition_id: PhysicalPartitionId
    projection_generation: int = Field(ge=1)
    projection_schema_version: SafeVersion
    release_id: OpaqueId
    release_manifest_sha256: Sha256
    component_access_ref: OpaqueId
    projection_adapter_version: SafeVersion
    projector_version: SafeVersion
    backend_watermark: int = Field(ge=0)
    lag: ProjectionLag = Field(
        default_factory=lambda: ProjectionLag(lag_events=0, lag_seconds=0.0)
    )
    lexical: LexicalPins | None = None
    vector: VectorPins | None = None
    graph: GraphPins | None = None
    replica: ReplicaPins | None = None

    @model_validator(mode="after")
    def validate_component_pins(self) -> Self:
        expected_prefix = "rg_" if self.component is RetrievalComponent.GRAPH else "rp_"
        if not self.physical_partition_id.startswith(expected_prefix):
            raise ValueError(
                "physical partition kind does not match retrieval component"
            )
        expected = {
            RetrievalComponent.LEXICAL: (
                self.lexical is not None,
                self.vector is None,
                self.graph is None,
            ),
            RetrievalComponent.VECTOR: (
                self.lexical is None,
                self.vector is not None,
                self.graph is None,
            ),
            RetrievalComponent.GRAPH: (
                self.lexical is None,
                self.vector is None,
                self.graph is not None,
            ),
        }[self.component]
        if expected != (True, True, True):
            raise ValueError(
                "component-specific projection pins are incomplete or mixed"
            )
        return self


class _BoundedQueryText(RetrievalValue):
    query_text: str = Field(min_length=1, max_length=16384)

    @field_validator("query_text")
    @classmethod
    def validate_query_bytes(cls, value: str) -> str:
        if "\x00" in value or len(value.encode("utf-8")) > 16384:
            raise ValueError("query text exceeds the UTF-8 byte limit")
        return value


def _finite_embedding(value: tuple[float, ...]) -> tuple[float, ...]:
    if not all(math.isfinite(item) for item in value):
        raise ValueError("query embedding must contain only finite values")
    return value


def _unique_ids(value: tuple[str, ...]) -> tuple[str, ...]:
    if len(value) != len(set(value)):
        raise ValueError("query identifiers must be unique")
    return value


class LexicalSearchParameters(_BoundedQueryText):
    max_results: int = Field(default=10, ge=1, le=100)


class LexicalCountParameters(_BoundedQueryText):
    pass


class VectorExactParameters(RetrievalValue):
    query_embedding: tuple[float, ...] = Field(min_length=1, max_length=16384)
    max_results: int = Field(default=10, ge=1, le=100)

    @field_validator("query_embedding")
    @classmethod
    def validate_finite_embedding(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        return _finite_embedding(value)


class HybridRrfParameters(_BoundedQueryText):
    query_embedding: tuple[float, ...] = Field(min_length=1, max_length=16384)
    max_results: int = Field(default=10, ge=1, le=100)

    @field_validator("query_embedding")
    @classmethod
    def validate_finite_embedding(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        return _finite_embedding(value)


class GraphEntityParameters(RetrievalValue):
    entity_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=100)
    max_results: int = Field(default=10, ge=1, le=100)

    @field_validator("entity_ids")
    @classmethod
    def require_unique_entity_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_ids(value)


class GraphNeighborsParameters(GraphEntityParameters):
    graph_depth: int = Field(default=1, ge=1, le=3)


class GraphPathsBoundedParameters(RetrievalValue):
    source_entity_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=100)
    target_entity_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=100)
    graph_depth: int = Field(default=1, ge=1, le=3)
    max_results: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def validate_entity_ids(self) -> Self:
        _unique_ids(self.source_entity_ids)
        _unique_ids(self.target_entity_ids)
        if len(set(self.source_entity_ids + self.target_entity_ids)) > 100:
            raise ValueError(
                "bounded path queries accept at most 100 entity identifiers"
            )
        return self


class GraphClaimSupportParameters(RetrievalValue):
    claim_entity_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=100)
    max_results: int = Field(default=10, ge=1, le=100)

    @field_validator("claim_entity_ids")
    @classmethod
    def require_unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_ids(value)


class GraphAuthorityCitationsParameters(RetrievalValue):
    authority_entity_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=100)
    max_results: int = Field(default=10, ge=1, le=100)

    @field_validator("authority_entity_ids")
    @classmethod
    def require_unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_ids(value)


class GraphSourceLineageParameters(RetrievalValue):
    source_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=100)
    graph_depth: int = Field(default=1, ge=1, le=3)
    max_results: int = Field(default=10, ge=1, le=100)

    @field_validator("source_ids")
    @classmethod
    def require_unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_ids(value)


class GraphScopeCountParameters(RetrievalValue):
    pass


class GraphExistsParameters(RetrievalValue):
    entity_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=100)

    @field_validator("entity_ids")
    @classmethod
    def require_unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_ids(value)


QueryParameters = (
    LexicalSearchParameters
    | LexicalCountParameters
    | VectorExactParameters
    | HybridRrfParameters
    | GraphEntityParameters
    | GraphNeighborsParameters
    | GraphPathsBoundedParameters
    | GraphClaimSupportParameters
    | GraphAuthorityCitationsParameters
    | GraphSourceLineageParameters
    | GraphScopeCountParameters
    | GraphExistsParameters
)

_PARAMETER_TYPES: dict[QueryTemplateId, type[RetrievalValue]] = {
    QueryTemplateId.LEXICAL_SEARCH_V1: LexicalSearchParameters,
    QueryTemplateId.LEXICAL_COUNT_V1: LexicalCountParameters,
    QueryTemplateId.VECTOR_EXACT_V1: VectorExactParameters,
    QueryTemplateId.HYBRID_RRF_V1: HybridRrfParameters,
    QueryTemplateId.GRAPH_ENTITY_V1: GraphEntityParameters,
    QueryTemplateId.GRAPH_NEIGHBORS_V1: GraphNeighborsParameters,
    QueryTemplateId.GRAPH_PATHS_BOUNDED_V1: GraphPathsBoundedParameters,
    QueryTemplateId.GRAPH_CLAIM_SUPPORT_V1: GraphClaimSupportParameters,
    QueryTemplateId.GRAPH_AUTHORITY_CITATIONS_V1: GraphAuthorityCitationsParameters,
    QueryTemplateId.GRAPH_SOURCE_LINEAGE_V1: GraphSourceLineageParameters,
    QueryTemplateId.GRAPH_SCOPE_COUNT_V1: GraphScopeCountParameters,
    QueryTemplateId.GRAPH_EXISTS_V1: GraphExistsParameters,
}


class RetrievalRequest(RetrievalValue):
    request_id: UUID
    scope: RetrievalScope
    authorization: AuthorizationPins
    credential_binding: CredentialBindingPins
    projections: tuple[ProjectionPins, ...] = Field(min_length=1, max_length=2)
    template: QueryTemplatePins
    retrieval_mode: RetrievalMode
    parameters: QueryParameters
    structured_filter_sha256: Sha256
    retrieval_adapter_version: SafeVersion
    optional_components: tuple[RetrievalComponent, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def parse_template_parameters(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        template = data.get("template")
        if isinstance(template, QueryTemplatePins):
            template_id = template.query_template_id
        elif isinstance(template, Mapping):
            raw_template_id = template.get("query_template_id")
            if not isinstance(raw_template_id, str):
                return value
            try:
                template_id = QueryTemplateId(raw_template_id)
            except (TypeError, ValueError):
                return value
        else:
            return value
        parameters = data.get("parameters")
        if isinstance(parameters, Mapping):
            data["parameters"] = _PARAMETER_TYPES[template_id].model_validate(
                parameters
            )
        return data

    @model_validator(mode="after")
    def validate_closed_request(self) -> Self:
        if (
            self.authorization.scope != self.scope
            or self.credential_binding.scope != self.scope
        ):
            raise ValueError(
                "authorization and credential scopes must match the request"
            )
        if self.authorization.principal_id != self.credential_binding.principal_id:
            raise ValueError("credential Principal must match the authorized Principal")
        if (
            self.authorization.policy_revision
            != self.credential_binding.policy_revision
            or self.authorization.rights_revision
            != self.credential_binding.rights_revision
        ):
            raise ValueError("credential revisions must match current authorization")
        components = tuple(projection.component for projection in self.projections)
        required = {
            RetrievalMode.LEXICAL: (RetrievalComponent.LEXICAL,),
            RetrievalMode.VECTOR_EXACT: (RetrievalComponent.VECTOR,),
            RetrievalMode.HYBRID_RRF: (
                RetrievalComponent.LEXICAL,
                RetrievalComponent.VECTOR,
            ),
            RetrievalMode.GRAPH: (RetrievalComponent.GRAPH,),
        }[self.retrieval_mode]
        if components != required:
            raise ValueError("projection components do not match the retrieval mode")
        projection = self.projections[0]
        for item in self.projections:
            if item.scope != self.scope:
                raise ValueError("every projection must match the exact request scope")
            if (
                item.projection_set_id != projection.projection_set_id
                or item.projection_generation != projection.projection_generation
                or item.release_id != projection.release_id
            ):
                raise ValueError(
                    "retrieval components must share projection set, generation, and release"
                )
        binding = self.credential_binding
        if (
            binding.projection_set_id != projection.projection_set_id
            or binding.projection_generation != projection.projection_generation
        ):
            raise ValueError(
                "credential binding must match the selected projection generation"
            )
        expected_parameters = _PARAMETER_TYPES[self.template.query_template_id]
        if type(self.parameters) is not expected_parameters:
            raise ValueError("query parameters do not match the retrieval mode")
        templates = {
            RetrievalMode.LEXICAL: {
                QueryTemplateId.LEXICAL_SEARCH_V1,
                QueryTemplateId.LEXICAL_COUNT_V1,
            },
            RetrievalMode.VECTOR_EXACT: {QueryTemplateId.VECTOR_EXACT_V1},
            RetrievalMode.HYBRID_RRF: {QueryTemplateId.HYBRID_RRF_V1},
            RetrievalMode.GRAPH: {
                item for item in QueryTemplateId if item.value.startswith("graph.")
            },
        }[self.retrieval_mode]
        if self.template.query_template_id not in templates:
            raise ValueError("query template does not match the retrieval mode")
        vector_projection = next(
            (
                item
                for item in self.projections
                if item.component is RetrievalComponent.VECTOR
            ),
            None,
        )
        if vector_projection is not None:
            vector_pins = vector_projection.vector
            if vector_pins is None:
                raise ValueError("vector projection is missing its model pins")
            if not isinstance(
                self.parameters, (VectorExactParameters, HybridRrfParameters)
            ):
                raise ValueError("vector retrieval requires typed embedding parameters")
            embedding = self.parameters.query_embedding
            if len(embedding) != vector_pins.embedding_dimension:
                raise ValueError(
                    "query embedding dimension does not match the pinned model"
                )
        if len(self.optional_components) != len(set(self.optional_components)):
            raise ValueError("optional retrieval components must be unique")
        if any(
            component is not RetrievalComponent.GRAPH
            for component in self.optional_components
        ):
            raise ValueError(
                "only the qualification-gated graph component may be optional"
            )
        return self


class SourceProvenance(RetrievalValue):
    retrieval_record_id: OpaqueId
    source_id: OpaqueId
    source_version: SafeVersion
    source_sha256: Sha256
    source_locator: str = Field(min_length=1, max_length=2048)
    document_id: OpaqueId
    chunk_id: OpaqueId
    chunk_ordinal: int = Field(ge=0)
    span_kind: OpaqueId
    span_start: int = Field(ge=0)
    span_end: int = Field(ge=0)
    span_page: int | None = Field(default=None, ge=1)
    chunk_sha256: Sha256
    classification: OpaqueId

    @model_validator(mode="after")
    def validate_span(self) -> Self:
        if self.span_end < self.span_start:
            raise ValueError("source span end cannot precede its start")
        return self


def _validate_aggregate_value(kind: RetrievalAggregateKind, value: int | bool) -> None:
    if kind is RetrievalAggregateKind.COUNT:
        if isinstance(value, bool) or value < 0:
            raise ValueError("count aggregates require a non-negative integer")
    elif type(value) is not bool:
        raise ValueError("existence aggregates require a boolean")


class BackendScoredRecord(RetrievalValue):
    """Neutral ranked row returned by one closed backend query."""

    projection: ProjectionPins
    source: SourceProvenance
    content: str = Field(min_length=1, max_length=1048576)
    score: float
    distance: float | None = None

    @model_validator(mode="after")
    def validate_score(self) -> Self:
        if not math.isfinite(self.score):
            raise ValueError("backend score must be finite")
        vector = self.projection.component is RetrievalComponent.VECTOR
        if vector != (self.distance is not None):
            raise ValueError("distance is required only for vector backend rows")
        if self.distance is not None and (
            not math.isfinite(self.distance) or self.distance < 0
        ):
            raise ValueError("vector distance must be finite and non-negative")
        return self


class BackendHybridRecord(RetrievalValue):
    """Neutral fused row with both ordered contributing projections."""

    projections: tuple[ProjectionPins, ProjectionPins]
    source: SourceProvenance
    content: str = Field(min_length=1, max_length=1048576)
    score: float

    @model_validator(mode="after")
    def validate_hybrid(self) -> Self:
        if not math.isfinite(self.score):
            raise ValueError("hybrid score must be finite")
        lexical, vector = self.projections
        if (
            lexical.component is not RetrievalComponent.LEXICAL
            or vector.component is not RetrievalComponent.VECTOR
        ):
            raise ValueError("hybrid projections must be ordered lexical then vector")
        if (
            lexical.scope != vector.scope
            or lexical.projection_set_id != vector.projection_set_id
            or lexical.projection_generation != vector.projection_generation
            or lexical.release_id != vector.release_id
        ):
            raise ValueError(
                "hybrid projections must share scope, set, generation, and release"
            )
        return self


class BackendAggregateRecord(RetrievalValue):
    """Neutral scoped aggregate returned by one closed backend query."""

    projection: ProjectionPins
    aggregate_kind: RetrievalAggregateKind
    value: int | bool

    @model_validator(mode="after")
    def validate_aggregate(self) -> Self:
        _validate_aggregate_value(self.aggregate_kind, self.value)
        if (
            self.aggregate_kind is RetrievalAggregateKind.EXISTS
            and self.projection.component is not RetrievalComponent.GRAPH
        ):
            raise ValueError("existence aggregates require the graph component")
        return self


class BackendUnavailableComponent(RetrievalValue):
    """Neutral backend signal for one unavailable optional component."""

    component: RetrievalComponent
    reason: IncompleteReason


class RetrievalProvenance(RetrievalValue):
    projections: tuple[ProjectionPins, ...] = Field(min_length=1, max_length=2)
    authorization: AuthorizationPins
    credential_binding: CredentialBindingPins
    template: QueryTemplatePins
    retrieval_adapter_version: SafeVersion
    structured_filter_sha256: Sha256
    source_ids: tuple[OpaqueId, ...]
    source_hashes: tuple[Sha256, ...]
    rank_path: tuple[RankSignal, ...] = Field(min_length=1)
    projection_lag: ProjectionLag

    @model_validator(mode="after")
    def validate_trace_pins(self) -> Self:
        if len(self.source_ids) != len(self.source_hashes):
            raise ValueError("trace source identifiers and hashes must align")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("trace source identifiers must be unique")
        components = tuple(item.component for item in self.projections)
        required = {
            QueryTemplateId.LEXICAL_SEARCH_V1: (RetrievalComponent.LEXICAL,),
            QueryTemplateId.LEXICAL_COUNT_V1: (RetrievalComponent.LEXICAL,),
            QueryTemplateId.VECTOR_EXACT_V1: (RetrievalComponent.VECTOR,),
            QueryTemplateId.HYBRID_RRF_V1: (
                RetrievalComponent.LEXICAL,
                RetrievalComponent.VECTOR,
            ),
            QueryTemplateId.GRAPH_ENTITY_V1: (RetrievalComponent.GRAPH,),
            QueryTemplateId.GRAPH_NEIGHBORS_V1: (RetrievalComponent.GRAPH,),
            QueryTemplateId.GRAPH_PATHS_BOUNDED_V1: (RetrievalComponent.GRAPH,),
            QueryTemplateId.GRAPH_CLAIM_SUPPORT_V1: (RetrievalComponent.GRAPH,),
            QueryTemplateId.GRAPH_AUTHORITY_CITATIONS_V1: (RetrievalComponent.GRAPH,),
            QueryTemplateId.GRAPH_SOURCE_LINEAGE_V1: (RetrievalComponent.GRAPH,),
            QueryTemplateId.GRAPH_SCOPE_COUNT_V1: (RetrievalComponent.GRAPH,),
            QueryTemplateId.GRAPH_EXISTS_V1: (RetrievalComponent.GRAPH,),
        }[self.template.query_template_id]
        if components != required:
            raise ValueError("trace projections do not match the pinned query template")
        projection = self.projections[0]
        for item in self.projections:
            if item.scope != self.authorization.scope:
                raise ValueError("trace projection and authorization scopes must match")
            if (
                item.projection_set_id != projection.projection_set_id
                or item.projection_generation != projection.projection_generation
                or item.release_id != projection.release_id
            ):
                raise ValueError(
                    "trace projections must share scope, set, generation, and release"
                )
        if len(components) != len(set(components)):
            raise ValueError("trace projection components must be unique")
        binding = self.credential_binding
        if binding.scope != self.authorization.scope:
            raise ValueError("trace credential scope must match authorization")
        if binding.principal_id != self.authorization.principal_id:
            raise ValueError("trace credential Principal must match authorization")
        if (
            binding.projection_set_id != projection.projection_set_id
            or binding.projection_generation != projection.projection_generation
        ):
            raise ValueError("trace credential must match the projection generation")
        if (
            binding.policy_revision != self.authorization.policy_revision
            or binding.rights_revision != self.authorization.rights_revision
        ):
            raise ValueError("trace credential revisions must match authorization")
        return self


class RetrievalHit(RetrievalValue):
    provenance: RetrievalProvenance
    source: SourceProvenance
    rank: int = Field(ge=1, le=100)
    score: float
    distance: float | None = None
    content: str = Field(min_length=1, max_length=1048576)

    @model_validator(mode="after")
    def validate_hit(self) -> Self:
        if not math.isfinite(self.score):
            raise ValueError("retrieval score must be finite")
        components = tuple(item.component for item in self.provenance.projections)
        vector = components == (RetrievalComponent.VECTOR,)
        if vector != (self.distance is not None):
            raise ValueError("distance is required only for vector results")
        if self.distance is not None and (
            not math.isfinite(self.distance) or self.distance < 0
        ):
            raise ValueError("vector distance must be finite and non-negative")
        if self.source.source_id not in self.provenance.source_ids:
            raise ValueError("result source is absent from the retrieval trace")
        index = self.provenance.source_ids.index(self.source.source_id)
        if self.provenance.source_hashes[index] != self.source.source_sha256:
            raise ValueError("result source hash disagrees with the retrieval trace")
        return self


class RetrievalAggregateValue(RetrievalValue):
    provenance: RetrievalProvenance
    aggregate_kind: RetrievalAggregateKind
    value: int | bool

    @model_validator(mode="after")
    def validate_aggregate(self) -> Self:
        _validate_aggregate_value(self.aggregate_kind, self.value)
        template_id = self.provenance.template.query_template_id
        allowed = {
            RetrievalAggregateKind.COUNT: {
                QueryTemplateId.LEXICAL_COUNT_V1,
                QueryTemplateId.GRAPH_SCOPE_COUNT_V1,
            },
            RetrievalAggregateKind.EXISTS: {QueryTemplateId.GRAPH_EXISTS_V1},
        }[self.aggregate_kind]
        if template_id not in allowed:
            raise ValueError("aggregate kind does not match the pinned query template")
        if RankSignal.SCOPE_AGGREGATE not in self.provenance.rank_path:
            raise ValueError(
                "aggregate provenance requires the scoped aggregate rank path"
            )
        return self


class IncompleteComponent(RetrievalValue):
    component: RetrievalComponent
    reason: IncompleteReason
    requested_optional: Literal[True] = True


class RetrievalResult(RetrievalValue):
    request_sha256: Sha256
    provenance: RetrievalProvenance
    scope: RetrievalScope
    projection_set_id: UUID
    projection_generation: int = Field(ge=1)
    release_id: OpaqueId
    policy_revision: Sha256
    rights_revision: Sha256
    hits: tuple[RetrievalHit, ...] = Field(max_length=100)
    aggregates: tuple[RetrievalAggregateValue, ...] = Field(default=(), max_length=100)
    incomplete_components: tuple[IncompleteComponent, ...] = ()

    @model_validator(mode="after")
    def reject_mixed_response(self) -> Self:
        incomplete = {item.component for item in self.incomplete_components}
        if len(incomplete) != len(self.incomplete_components):
            raise ValueError("incomplete component entries must be unique")
        ranks = [hit.rank for hit in self.hits]
        if len(ranks) != len(set(ranks)):
            raise ValueError("result ranks must be unique")
        aggregate_keys = [
            (item.provenance.template.query_template_id, item.aggregate_kind)
            for item in self.aggregates
        ]
        if len(aggregate_keys) != len(set(aggregate_keys)):
            raise ValueError("aggregate result entries must be unique")
        for projection in self.provenance.projections:
            if (
                projection.scope != self.scope
                or projection.projection_set_id != self.projection_set_id
                or projection.projection_generation != self.projection_generation
                or projection.release_id != self.release_id
                or self.provenance.authorization.policy_revision != self.policy_revision
                or self.provenance.authorization.rights_revision != self.rights_revision
            ):
                raise ValueError(
                    "one wrong-scope or stale result rejects the entire response"
                )
        child_traces = [hit.provenance for hit in self.hits]
        child_traces.extend(item.provenance for item in self.aggregates)
        for trace in child_traces:
            if trace != self.provenance:
                raise ValueError(
                    "child provenance mismatch rejects the entire response"
                )
            for projection in trace.projections:
                if projection.component in incomplete:
                    raise ValueError(
                        "an incomplete component cannot contribute content or aggregates"
                    )
        return self


class RetrievalCacheKey(RetrievalValue):
    scope: RetrievalScope
    principal_policy_context_sha256: Sha256
    policy_revision: Sha256
    rights_revision: Sha256
    projection_set_id: UUID
    projection_generation: int = Field(ge=1)
    release_id: OpaqueId
    physical_partition_ids: tuple[PhysicalPartitionId, ...] = Field(
        min_length=1, max_length=2
    )
    credential_binding_event_sha256: Sha256
    projection_schema_version: SafeVersion
    projection_adapter_version: SafeVersion
    projector_version: SafeVersion
    retrieval_adapter_version: SafeVersion
    template: QueryTemplatePins
    retrieval_mode: RetrievalMode
    canonical_query_sha256: Sha256
    structured_filter_sha256: Sha256
    required_core_watermark: int = Field(ge=0)
    text_search_configuration_sha256: Sha256 | None = None
    vector: VectorPins | None = None

    @model_validator(mode="after")
    def validate_conditional_cache_pins(self) -> Self:
        lexical = self.retrieval_mode in {
            RetrievalMode.LEXICAL,
            RetrievalMode.HYBRID_RRF,
        }
        vector = self.retrieval_mode in {
            RetrievalMode.VECTOR_EXACT,
            RetrievalMode.HYBRID_RRF,
        }
        if lexical != (self.text_search_configuration_sha256 is not None):
            raise ValueError(
                "lexical cache keys require the text-search configuration hash"
            )
        if vector != (self.vector is not None):
            raise ValueError(
                "vector cache keys require embedding revision, dimension, and metric"
            )
        if len(self.physical_partition_ids) != len(set(self.physical_partition_ids)):
            raise ValueError("cache partition identifiers must be unique")
        return self


def postgres_lsn_value(lsn: str) -> int:
    """Return the comparable integer value of one pinned PostgreSQL LSN."""

    high, separator, low = lsn.partition("/")
    if not separator or not high or not low:
        raise ValueError("PostgreSQL LSN must use the HEX/HEX shape")
    try:
        return (int(high, 16) << 32) + int(low, 16)
    except ValueError:
        raise ValueError("PostgreSQL LSN must use hexadecimal segments") from None
