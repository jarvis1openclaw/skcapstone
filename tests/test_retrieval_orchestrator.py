"""Focused qualification tests for the first retrieval orchestrator slice."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError
from sklegal_retrieval.contract import ContractValidationError
from sklegal_retrieval.errors import (
    RetrievalAuthorizationError,
    RetrievalIntegrityError,
    RetrievalRequestError,
    RetrievalUnavailableError,
)
from sklegal_retrieval.fake import (
    AGE_UNAVAILABLE,
    FakeActiveProjectionRegistry,
    FakeAuthorizer,
    FakeCredentialBindingResolver,
    FakeRetrievalExecutor,
    FakeRetrievalRecord,
)
from sklegal_retrieval.models import (
    AuthorizationPins,
    BackendAggregateRecord,
    BackendHybridRecord,
    BackendUnavailableComponent,
    CredentialBindingPins,
    DistanceMetric,
    GraphPins,
    GraphScopeCountParameters,
    HybridRrfParameters,
    IncompleteReason,
    LexicalCountParameters,
    LexicalPins,
    LexicalSearchParameters,
    ProjectionPins,
    QueryTemplateId,
    QueryTemplatePins,
    RankSignal,
    RetrievalAggregateKind,
    RetrievalComponent,
    RetrievalMode,
    RetrievalRequest,
    RetrievalScope,
    ScopeKind,
    SourceProvenance,
    VectorExactParameters,
    VectorPins,
)
from sklegal_retrieval.orchestrator import RetrievalOrchestrator
from sklegal_retrieval.query_templates import QUERY_TEMPLATES

TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
OTHER_TENANT_ID = UUID("20000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("10000000-0000-4000-8000-000000000002")
OTHER_MATTER_ID = UUID("10000000-0000-4000-8000-000000000003")
PRINCIPAL_ID = UUID("10000000-0000-4000-8000-000000000004")
OTHER_PRINCIPAL_ID = UUID("10000000-0000-4000-8000-000000000005")
DECISION_ID = UUID("10000000-0000-4000-8000-000000000006")
PROJECTION_SET_ID = UUID("10000000-0000-4000-8000-000000000007")
REQUEST_ID = UUID("10000000-0000-4000-8000-000000000008")

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
FILTER_HASH = "f" * 64


def _scope(
    *,
    tenant_id: UUID = TENANT_ID,
    matter_id: UUID = MATTER_ID,
) -> RetrievalScope:
    return RetrievalScope(
        tenant_id=tenant_id,
        scope_kind=ScopeKind.MATTER,
        matter_id=matter_id,
    )


def _authorization(
    scope: RetrievalScope,
    *,
    principal_id: UUID = PRINCIPAL_ID,
    policy_revision: str = HASH_B,
    rights_revision: str = HASH_C,
    required_core_watermark: int = 5,
) -> AuthorizationPins:
    return AuthorizationPins(
        principal_id=principal_id,
        scope=scope,
        authorization_decision_id=DECISION_ID,
        principal_policy_context_sha256=HASH_A,
        policy_revision=policy_revision,
        rights_revision=rights_revision,
        required_core_watermark=required_core_watermark,
    )


def _projection(
    component: RetrievalComponent,
    scope: RetrievalScope,
    *,
    projection_set_id: UUID = PROJECTION_SET_ID,
    generation: int = 3,
    release_id: str = "release-1",
    watermark: int = 10,
) -> ProjectionPins:
    suffix = {
        RetrievalComponent.LEXICAL: "1" * 32,
        RetrievalComponent.VECTOR: "2" * 32,
        RetrievalComponent.GRAPH: "3" * 32,
    }[component]
    common: dict[str, Any] = {
        "component": component,
        "scope": scope,
        "projection_set_id": projection_set_id,
        "physical_partition_id": (
            f"rg_{suffix}" if component is RetrievalComponent.GRAPH else f"rp_{suffix}"
        ),
        "projection_generation": generation,
        "projection_schema_version": "1.0.0",
        "release_id": release_id,
        "release_manifest_sha256": HASH_D,
        "component_access_ref": f"access:{component.value}",
        "projection_adapter_version": "1.0.0",
        "projector_version": "1.0.0",
        "backend_watermark": watermark,
    }
    if component is RetrievalComponent.LEXICAL:
        common["lexical"] = LexicalPins(
            text_search_configuration="english",
            text_search_configuration_sha256=HASH_E,
        )
    elif component is RetrievalComponent.VECTOR:
        common["vector"] = VectorPins(
            embedding_model_id="embedding-local",
            embedding_model_revision="revision-1",
            embedding_dimension=2,
            distance_metric=DistanceMetric.L2,
        )
    else:
        common["graph"] = GraphPins(
            graph_registry_id="graph-registry-1",
            exact_graph_gateway_credential_mapping_ref="graph-access-1",
            graph_gateway_function_definition_sha256=HASH_A,
            apache_age_qualification_evidence_sha256=HASH_B,
            qualified=True,
        )
    return ProjectionPins(**common)


def _binding(
    scope: RetrievalScope,
    projection: ProjectionPins,
    *,
    principal_id: UUID = PRINCIPAL_ID,
    policy_revision: str = HASH_B,
    rights_revision: str = HASH_C,
    generation: int | None = None,
) -> CredentialBindingPins:
    return CredentialBindingPins(
        credential_ref="credential:retrieval-1",
        database_principal="principal-db-1",
        principal_id=principal_id,
        scope=scope,
        projection_set_id=projection.projection_set_id,
        projection_generation=generation or projection.projection_generation,
        policy_revision=policy_revision,
        rights_revision=rights_revision,
        authorization_event_sequence=11,
        authorization_event_sha256=HASH_D,
    )


def _template_pins(
    template_id: QueryTemplateId,
    *,
    version: str | None = None,
    digest: str | None = None,
) -> QueryTemplatePins:
    template = QUERY_TEMPLATES[template_id.value]
    return QueryTemplatePins(
        query_template_id=template_id,
        query_template_version=version or str(template.version),
        query_template_sha256=digest or template.definition_sha256,
    )


def _request(
    *,
    mode: RetrievalMode,
    template_id: QueryTemplateId,
    parameters: object,
    projections: tuple[ProjectionPins, ...],
    authorization: AuthorizationPins | None = None,
    binding: CredentialBindingPins | None = None,
    optional_components: tuple[RetrievalComponent, ...] = (),
    template_version: str | None = None,
    template_digest: str | None = None,
) -> RetrievalRequest:
    scope = projections[0].scope
    current = authorization or _authorization(scope)
    credential = binding or _binding(scope, projections[0])
    return RetrievalRequest(
        request_id=REQUEST_ID,
        scope=scope,
        authorization=current,
        credential_binding=credential,
        projections=projections,
        template=_template_pins(
            template_id,
            version=template_version,
            digest=template_digest,
        ),
        retrieval_mode=mode,
        parameters=parameters,
        structured_filter_sha256=FILTER_HASH,
        retrieval_adapter_version="1.0.0",
        optional_components=optional_components,
    )


def _source(record_id: str, *, source_hash: str = HASH_A) -> SourceProvenance:
    return SourceProvenance(
        retrieval_record_id=record_id,
        source_id=f"source-{record_id}",
        source_version="1",
        source_sha256=source_hash,
        source_locator=f"fixture/{record_id}",
        document_id=f"document-{record_id}",
        chunk_id=f"chunk-{record_id}",
        chunk_ordinal=0,
        span_kind="text",
        span_start=0,
        span_end=10,
        chunk_sha256=HASH_E,
        classification="protected",
    )


def _record(
    projection: ProjectionPins,
    record_id: str,
    content: str,
    *,
    source: SourceProvenance | None = None,
    embedding: tuple[float, ...] | None = None,
) -> FakeRetrievalRecord:
    return FakeRetrievalRecord(
        projection=projection,
        source=source or _source(record_id),
        content=content,
        embedding=embedding,
    )


def _orchestrator(
    request: RetrievalRequest,
    executor: object,
    *,
    authorizer: FakeAuthorizer | None = None,
    resolver: FakeCredentialBindingResolver | None = None,
    registry: FakeActiveProjectionRegistry | None = None,
    event_log: list[str] | None = None,
) -> RetrievalOrchestrator:
    return RetrievalOrchestrator(
        authorizer=authorizer
        or FakeAuthorizer(request.authorization, event_log=event_log),
        credential_resolver=resolver
        or FakeCredentialBindingResolver(
            request.credential_binding,
            event_log=event_log,
        ),
        registry=registry
        or FakeActiveProjectionRegistry(request.projections, event_log=event_log),
        executor=executor,  # type: ignore[arg-type]
    )


def test_authorization_denial_precedes_binding_registry_and_backend() -> None:
    scope = _scope()
    lexical = _projection(RetrievalComponent.LEXICAL, scope)
    request = _request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="alpha", max_results=10),
        projections=(lexical,),
    )
    events: list[str] = []
    registry = FakeActiveProjectionRegistry((lexical,), event_log=events)
    executor = FakeRetrievalExecutor(event_log=events)
    orchestrator = _orchestrator(
        request,
        executor,
        authorizer=FakeAuthorizer(None, event_log=events),
        registry=registry,
        event_log=events,
    )

    with pytest.raises(RetrievalAuthorizationError):
        orchestrator.retrieve(request)

    assert events == ["authorization"]
    assert registry.selection_count == 0
    assert executor.call_count == 0


@pytest.mark.parametrize(
    "binding_factory", ["principal", "matter", "generation", "policy", "revoked"]
)
def test_current_credential_binding_denies_before_registry(
    binding_factory: str,
) -> None:
    scope = _scope()
    lexical = _projection(RetrievalComponent.LEXICAL, scope)
    request = _request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="alpha"),
        projections=(lexical,),
    )
    wrong: CredentialBindingPins | None
    if binding_factory == "principal":
        wrong = _binding(scope, lexical, principal_id=OTHER_PRINCIPAL_ID)
    elif binding_factory == "matter":
        wrong = _binding(_scope(matter_id=OTHER_MATTER_ID), lexical)
    elif binding_factory == "generation":
        wrong = _binding(scope, lexical, generation=4)
    elif binding_factory == "policy":
        wrong = _binding(scope, lexical, policy_revision=HASH_E)
    else:
        wrong = None
    events: list[str] = []
    registry = FakeActiveProjectionRegistry((lexical,), event_log=events)
    orchestrator = _orchestrator(
        request,
        FakeRetrievalExecutor(event_log=events),
        resolver=FakeCredentialBindingResolver(wrong, event_log=events),
        registry=registry,
        event_log=events,
    )

    with pytest.raises(RetrievalAuthorizationError):
        orchestrator.retrieve(request)

    assert events == ["authorization", "credential_binding"]
    assert registry.selection_count == 0


def test_lexical_ranking_trace_and_empty_response_are_deterministic() -> None:
    scope = _scope()
    lexical = _projection(RetrievalComponent.LEXICAL, scope)
    request = _request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="alpha", max_results=10),
        projections=(lexical,),
    )
    events: list[str] = []
    records = (
        _record(lexical, "record-b", "alpha"),
        _record(lexical, "record-c", "alpha alpha"),
        _record(lexical, "record-a", "alpha"),
    )
    registry = FakeActiveProjectionRegistry((lexical,), event_log=events)
    result = _orchestrator(
        request,
        FakeRetrievalExecutor(records, event_log=events),
        registry=registry,
        event_log=events,
    ).retrieve(request)

    assert [hit.source.retrieval_record_id for hit in result.hits] == [
        "record-c",
        "record-a",
        "record-b",
    ]
    assert [hit.rank for hit in result.hits] == [1, 2, 3]
    assert result.provenance.structured_filter_sha256 == FILTER_HASH
    assert result.provenance.rank_path == (RankSignal.LEXICAL_RANK,)
    assert all(hit.provenance == result.provenance for hit in result.hits)
    assert events == [
        "authorization",
        "credential_binding",
        "registry",
        "backend:lexical",
        "authorization",
    ]
    assert registry.selection_count == 1

    empty_request = _request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="absent"),
        projections=(lexical,),
    )
    empty = _orchestrator(
        empty_request,
        FakeRetrievalExecutor(records),
    ).retrieve(empty_request)
    assert empty.hits == ()
    assert empty.provenance.structured_filter_sha256 == FILTER_HASH


def test_exact_vector_ties_use_retrieval_record_id() -> None:
    scope = _scope()
    vector = _projection(RetrievalComponent.VECTOR, scope)
    request = _request(
        mode=RetrievalMode.VECTOR_EXACT,
        template_id=QueryTemplateId.VECTOR_EXACT_V1,
        parameters=VectorExactParameters(query_embedding=(0.0, 0.0), max_results=10),
        projections=(vector,),
    )
    records = (
        _record(vector, "record-b", "B", embedding=(1.0, 0.0)),
        _record(vector, "record-a", "A", embedding=(0.0, 1.0)),
    )

    result = _orchestrator(request, FakeRetrievalExecutor(records)).retrieve(request)

    assert [hit.source.retrieval_record_id for hit in result.hits] == [
        "record-a",
        "record-b",
    ]
    assert [hit.distance for hit in result.hits] == [1.0, 1.0]
    assert result.provenance.rank_path == (RankSignal.VECTOR_DISTANCE,)


def test_hybrid_rrf_is_deterministic_and_traces_both_projections() -> None:
    scope = _scope()
    lexical = _projection(RetrievalComponent.LEXICAL, scope)
    vector = _projection(RetrievalComponent.VECTOR, scope)
    request = _request(
        mode=RetrievalMode.HYBRID_RRF,
        template_id=QueryTemplateId.HYBRID_RRF_V1,
        parameters=HybridRrfParameters(
            query_text="alpha",
            query_embedding=(1.0, 0.0),
            max_results=10,
        ),
        projections=(lexical, vector),
    )
    source_a = _source("record-a")
    source_b = _source("record-b")
    records = (
        _record(lexical, "record-a", "alpha", source=source_a),
        _record(lexical, "record-b", "alpha", source=source_b),
        _record(vector, "record-a", "alpha", source=source_a, embedding=(0.8, 0.2)),
        _record(vector, "record-b", "alpha", source=source_b, embedding=(1.0, 0.0)),
    )

    result = _orchestrator(request, FakeRetrievalExecutor(records)).retrieve(request)

    assert [hit.source.retrieval_record_id for hit in result.hits] == [
        "record-a",
        "record-b",
    ]
    assert all(hit.distance is None for hit in result.hits)
    assert result.provenance.projections == (lexical, vector)
    assert result.provenance.rank_path == (RankSignal.HYBRID_RRF,)


def test_hybrid_duplicate_record_requires_full_source_and_content_equality() -> None:
    scope = _scope()
    lexical = _projection(RetrievalComponent.LEXICAL, scope)
    vector = _projection(RetrievalComponent.VECTOR, scope)
    request = _request(
        mode=RetrievalMode.HYBRID_RRF,
        template_id=QueryTemplateId.HYBRID_RRF_V1,
        parameters=HybridRrfParameters(
            query_text="alpha",
            query_embedding=(1.0, 0.0),
        ),
        projections=(lexical, vector),
    )
    records = (
        _record(lexical, "same", "alpha"),
        _record(vector, "same", "changed", embedding=(1.0, 0.0)),
    )

    with pytest.raises(RetrievalUnavailableError):
        _orchestrator(request, FakeRetrievalExecutor(records)).retrieve(request)


def test_lexical_count_returns_scoped_aggregate_and_leak_mode_denies() -> None:
    scope = _scope()
    lexical = _projection(RetrievalComponent.LEXICAL, scope)
    request = _request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_COUNT_V1,
        parameters=LexicalCountParameters(query_text="alpha"),
        projections=(lexical,),
    )
    records = (
        _record(lexical, "record-a", "alpha"),
        _record(lexical, "record-b", "alpha beta"),
    )

    result = _orchestrator(request, FakeRetrievalExecutor(records)).retrieve(request)

    assert result.hits == ()
    assert len(result.aggregates) == 1
    assert result.aggregates[0].aggregate_kind is RetrievalAggregateKind.COUNT
    assert result.aggregates[0].value == 2
    assert result.aggregates[0].provenance == result.provenance
    assert result.provenance.source_ids == ()
    assert result.provenance.rank_path == (RankSignal.SCOPE_AGGREGATE,)

    with pytest.raises(RetrievalUnavailableError):
        _orchestrator(
            request,
            FakeRetrievalExecutor(records, leak_partition_rows=True),
        ).retrieve(request)


def test_wrong_scope_row_rejects_the_whole_response() -> None:
    scope = _scope()
    lexical = _projection(RetrievalComponent.LEXICAL, scope)
    other = _projection(
        RetrievalComponent.LEXICAL,
        _scope(tenant_id=OTHER_TENANT_ID),
    )
    request = _request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="alpha"),
        projections=(lexical,),
    )
    executor = FakeRetrievalExecutor(
        (
            _record(lexical, "record-good", "alpha"),
            _record(other, "record-leak", "alpha"),
        ),
        leak_partition_rows=True,
    )

    with pytest.raises(RetrievalIntegrityError):
        _orchestrator(request, executor).retrieve(request)


class _StaticExecutor:
    def __init__(self, value: object) -> None:
        self.value = value
        self.call_count = 0

    def execute(self, bound: object, projections: object) -> object:
        del bound, projections
        self.call_count += 1
        return self.value


def test_wrong_scope_aggregate_rejects_the_whole_response() -> None:
    scope = _scope()
    lexical = _projection(RetrievalComponent.LEXICAL, scope)
    other = _projection(
        RetrievalComponent.LEXICAL,
        _scope(tenant_id=OTHER_TENANT_ID),
    )
    request = _request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_COUNT_V1,
        parameters=LexicalCountParameters(query_text="alpha"),
        projections=(lexical,),
    )
    aggregate = BackendAggregateRecord(
        projection=other,
        aggregate_kind=RetrievalAggregateKind.COUNT,
        value=1,
    )

    with pytest.raises(RetrievalIntegrityError):
        _orchestrator(request, _StaticExecutor(aggregate)).retrieve(request)


def test_stale_watermark_and_release_mismatch_deny_before_backend() -> None:
    scope = _scope()
    stale = _projection(RetrievalComponent.LEXICAL, scope, watermark=4)
    authorization = _authorization(scope, required_core_watermark=5)
    stale_request = _request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="alpha"),
        projections=(stale,),
        authorization=authorization,
        binding=_binding(scope, stale),
    )
    executor = FakeRetrievalExecutor()
    with pytest.raises(RetrievalUnavailableError):
        _orchestrator(stale_request, executor).retrieve(stale_request)
    assert executor.call_count == 0

    expected = _projection(RetrievalComponent.LEXICAL, scope)
    selected = _projection(RetrievalComponent.LEXICAL, scope, release_id="release-2")
    request = _request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="alpha"),
        projections=(expected,),
    )
    executor = FakeRetrievalExecutor()
    with pytest.raises(RetrievalIntegrityError):
        _orchestrator(
            request,
            executor,
            registry=FakeActiveProjectionRegistry((selected,)),
        ).retrieve(request)
    assert executor.call_count == 0


def test_revision_change_after_backend_rejects_result() -> None:
    scope = _scope()
    lexical = _projection(RetrievalComponent.LEXICAL, scope)
    initial = _authorization(scope)
    changed = _authorization(scope, rights_revision=HASH_E)
    request = _request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="alpha"),
        projections=(lexical,),
        authorization=initial,
    )
    authorizer = FakeAuthorizer((initial, changed))

    with pytest.raises(RetrievalIntegrityError):
        _orchestrator(
            request,
            FakeRetrievalExecutor((_record(lexical, "record-a", "alpha"),)),
            authorizer=authorizer,
        ).retrieve(request)

    assert authorizer.call_count == 2


def test_optional_age_unavailable_is_explicit_but_mandatory_denies() -> None:
    scope = _scope()
    graph = _projection(RetrievalComponent.GRAPH, scope)
    optional_request = _request(
        mode=RetrievalMode.GRAPH,
        template_id=QueryTemplateId.GRAPH_SCOPE_COUNT_V1,
        parameters=GraphScopeCountParameters(),
        projections=(graph,),
        optional_components=(RetrievalComponent.GRAPH,),
    )

    result = _orchestrator(
        optional_request,
        FakeRetrievalExecutor(graph_result=AGE_UNAVAILABLE),
    ).retrieve(optional_request)

    assert result.hits == ()
    assert result.aggregates == ()
    assert result.provenance.projections == (graph,)
    assert result.incomplete_components[0].component is RetrievalComponent.GRAPH
    assert (
        result.incomplete_components[0].reason is IncompleteReason.BACKEND_UNAVAILABLE
    )

    mandatory_request = _request(
        mode=RetrievalMode.GRAPH,
        template_id=QueryTemplateId.GRAPH_SCOPE_COUNT_V1,
        parameters=GraphScopeCountParameters(),
        projections=(graph,),
    )
    with pytest.raises(RetrievalUnavailableError):
        _orchestrator(
            mandatory_request,
            FakeRetrievalExecutor(graph_result=AGE_UNAVAILABLE),
        ).retrieve(mandatory_request)


def test_mandatory_aggregate_unavailable_denies() -> None:
    scope = _scope()
    lexical = _projection(RetrievalComponent.LEXICAL, scope)
    request = _request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_COUNT_V1,
        parameters=LexicalCountParameters(query_text="alpha"),
        projections=(lexical,),
    )
    unavailable = BackendUnavailableComponent(
        component=RetrievalComponent.LEXICAL,
        reason=IncompleteReason.BACKEND_UNAVAILABLE,
    )
    with pytest.raises(RetrievalUnavailableError):
        _orchestrator(request, _StaticExecutor(unavailable)).retrieve(request)


def test_template_pin_mismatch_denies_before_execution() -> None:
    scope = _scope()
    lexical = _projection(RetrievalComponent.LEXICAL, scope)
    executor = FakeRetrievalExecutor()
    request = _request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="alpha"),
        projections=(lexical,),
        template_digest="0" * 64,
    )

    with pytest.raises(RetrievalIntegrityError):
        _orchestrator(request, executor).retrieve(request)

    assert executor.call_count == 0


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda lexical, vector: {
                "projections": [lexical, vector],
                "source": _source("malformed"),
                "content": "content",
                "score": 1.0,
            },
            "projections",
        ),
        (
            lambda lexical, vector: {
                "projections": (lexical, vector),
                "source": "not-source-provenance",
                "content": "content",
                "score": 1.0,
            },
            "source",
        ),
        (
            lambda lexical, vector: {
                "projections": (lexical, vector),
                "source": _source("malformed"),
                "content": "content",
                "score": float("nan"),
            },
            "score",
        ),
    ],
)
def test_backend_hybrid_record_rejects_malformed_runtime_values(
    factory: Callable[[ProjectionPins, ProjectionPins], dict[str, object]],
    message: str,
) -> None:
    scope = _scope()
    lexical = _projection(RetrievalComponent.LEXICAL, scope)
    vector = _projection(RetrievalComponent.VECTOR, scope)
    with pytest.raises(ValidationError, match=message):
        BackendHybridRecord(**factory(lexical, vector))


class _ExplodingExecutor:
    def __init__(self, sentinel: str) -> None:
        self.sentinel = sentinel

    def execute(self, bound: object, projections: object) -> object:
        del bound, projections
        raise RuntimeError(self.sentinel)


def _exception_chain_text(error: BaseException) -> str:
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    values: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        values.append(str(current))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return " ".join(values)


def test_sensitive_backend_causes_have_identical_sanitized_shape_and_no_chain() -> None:
    scope = _scope()
    lexical = _projection(RetrievalComponent.LEXICAL, scope)
    request = _request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="alpha"),
        projections=(lexical,),
    )
    sentinels = ("secret-rg_deadbeef", "secret-rp_cafebabe")
    errors: list[RetrievalUnavailableError] = []
    for sentinel in sentinels:
        with pytest.raises(RetrievalUnavailableError) as captured:
            _orchestrator(request, _ExplodingExecutor(sentinel)).retrieve(request)
        errors.append(captured.value)

    assert errors[0].public_error() == errors[1].public_error()
    for error in errors:
        chain = _exception_chain_text(error)
        assert all(sentinel not in chain for sentinel in sentinels)
        assert error.__cause__ is None
        assert error.__context__ is None


def test_contract_failure_is_translated_without_exposing_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _scope()
    lexical = _projection(RetrievalComponent.LEXICAL, scope)
    request = _request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="alpha"),
        projections=(lexical,),
    )
    sentinel = "sensitive-contract-path"

    def fail_closed(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise ContractValidationError(sentinel)

    monkeypatch.setattr(
        "sklegal_retrieval.orchestrator.bind_query_template",
        fail_closed,
    )
    with pytest.raises(RetrievalRequestError) as captured:
        _orchestrator(request, FakeRetrievalExecutor()).retrieve(request)

    assert sentinel not in _exception_chain_text(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_fake_executor_exposes_no_alternate_backend_route() -> None:
    assert FakeRetrievalExecutor.supported_components == {
        RetrievalComponent.LEXICAL,
        RetrievalComponent.VECTOR,
        RetrievalComponent.GRAPH,
    }
