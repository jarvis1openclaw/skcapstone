"""Focused tests for immutable retrieval boundary values."""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError
from sklegal_retrieval.errors import (
    RetrievalAuthorizationError,
    RetrievalUnavailableError,
)
from sklegal_retrieval.models import (
    AuthorizationPins,
    BackendAggregateRecord,
    BackendHybridRecord,
    BackendScoredRecord,
    BackendUnavailableComponent,
    CredentialBindingPins,
    DistanceMetric,
    GraphAuthorityCitationsParameters,
    GraphClaimSupportParameters,
    GraphEntityParameters,
    GraphExistsParameters,
    GraphNeighborsParameters,
    GraphPathsBoundedParameters,
    GraphPins,
    GraphScopeCountParameters,
    GraphSourceLineageParameters,
    IncompleteComponent,
    IncompleteReason,
    LexicalCountParameters,
    LexicalPins,
    LexicalSearchParameters,
    ProjectionLag,
    ProjectionPins,
    QueryTemplateId,
    QueryTemplatePins,
    RankSignal,
    ReplicaPins,
    RetrievalAggregateKind,
    RetrievalAggregateValue,
    RetrievalCacheKey,
    RetrievalComponent,
    RetrievalHit,
    RetrievalMode,
    RetrievalProvenance,
    RetrievalRequest,
    RetrievalResult,
    RetrievalScope,
    ScopeKind,
    SourceProvenance,
    VectorExactParameters,
    VectorPins,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
MATTER_ID = UUID("22222222-2222-2222-2222-222222222222")
PRINCIPAL_ID = UUID("33333333-3333-3333-3333-333333333333")
PROJECTION_SET_ID = UUID("44444444-4444-4444-4444-444444444444")


def matter_scope() -> RetrievalScope:
    return RetrievalScope(
        tenant_id=TENANT_ID,
        scope_kind=ScopeKind.MATTER,
        matter_id=MATTER_ID,
    )


def authorization(
    scope: RetrievalScope | None = None,
    tenant_shared_policy_decision_id: UUID | None = None,
) -> AuthorizationPins:
    return AuthorizationPins(
        principal_id=PRINCIPAL_ID,
        scope=scope or matter_scope(),
        authorization_decision_id=UUID("55555555-5555-5555-5555-555555555555"),
        principal_policy_context_sha256=HASH_A,
        policy_revision=HASH_B,
        rights_revision=HASH_C,
        required_core_watermark=40,
        tenant_shared_policy_decision_id=tenant_shared_policy_decision_id,
    )


def credential(scope: RetrievalScope | None = None) -> CredentialBindingPins:
    return CredentialBindingPins(
        credential_ref="credential.binding.1",
        database_principal="retrieval_principal_1",
        principal_id=PRINCIPAL_ID,
        scope=scope or matter_scope(),
        projection_set_id=PROJECTION_SET_ID,
        projection_generation=7,
        policy_revision=HASH_B,
        rights_revision=HASH_C,
        authorization_event_sequence=39,
        authorization_event_sha256=HASH_D,
    )


def template(template_id: QueryTemplateId) -> QueryTemplatePins:
    return QueryTemplatePins(
        query_template_id=template_id,
        query_template_version="1.0.0",
        query_template_sha256=HASH_A,
    )


def lexical_projection(scope: RetrievalScope | None = None) -> ProjectionPins:
    return ProjectionPins(
        component=RetrievalComponent.LEXICAL,
        scope=scope or matter_scope(),
        projection_set_id=PROJECTION_SET_ID,
        physical_partition_id="rp_11111111111111111111111111111111",
        projection_generation=7,
        projection_schema_version="1.0.0",
        release_id="release-2026-08-21",
        release_manifest_sha256=HASH_A,
        component_access_ref="access.lexical.1",
        projection_adapter_version="1.0.0",
        projector_version="1.0.0",
        backend_watermark=40,
        lexical=LexicalPins(
            text_search_configuration="english",
            text_search_configuration_sha256=HASH_B,
        ),
    )


def vector_projection(scope: RetrievalScope | None = None) -> ProjectionPins:
    return ProjectionPins(
        component=RetrievalComponent.VECTOR,
        scope=scope or matter_scope(),
        projection_set_id=PROJECTION_SET_ID,
        physical_partition_id="rp_22222222222222222222222222222222",
        projection_generation=7,
        projection_schema_version="1.0.0",
        release_id="release-2026-08-21",
        release_manifest_sha256=HASH_A,
        component_access_ref="access.vector.1",
        projection_adapter_version="1.0.0",
        projector_version="1.0.0",
        backend_watermark=40,
        vector=VectorPins(
            embedding_model_id="embedding.local.1",
            embedding_model_revision="revision-1",
            embedding_dimension=3,
            distance_metric=DistanceMetric.COSINE,
        ),
    )


def graph_projection(scope: RetrievalScope | None = None) -> ProjectionPins:
    return ProjectionPins(
        component=RetrievalComponent.GRAPH,
        scope=scope or matter_scope(),
        projection_set_id=PROJECTION_SET_ID,
        physical_partition_id="rg_33333333333333333333333333333333",
        projection_generation=7,
        projection_schema_version="1.0.0",
        release_id="release-2026-08-21",
        release_manifest_sha256=HASH_A,
        component_access_ref="access.graph.1",
        projection_adapter_version="1.0.0",
        projector_version="1.0.0",
        backend_watermark=40,
        graph=GraphPins(
            graph_registry_id="graph.registry.1",
            exact_graph_gateway_credential_mapping_ref="gateway.mapping.1",
            graph_gateway_function_definition_sha256=HASH_B,
            apache_age_qualification_evidence_sha256=HASH_C,
        ),
    )


def lexical_request() -> RetrievalRequest:
    return RetrievalRequest(
        request_id=UUID("66666666-6666-6666-6666-666666666666"),
        scope=matter_scope(),
        authorization=authorization(),
        credential_binding=credential(),
        projections=(lexical_projection(),),
        template=template(QueryTemplateId.LEXICAL_SEARCH_V1),
        retrieval_mode=RetrievalMode.LEXICAL,
        parameters=LexicalSearchParameters(
            query_text="adverse possession", max_results=10
        ),
        structured_filter_sha256=HASH_D,
        retrieval_adapter_version="1.0.0",
    )


def source() -> SourceProvenance:
    return SourceProvenance(
        retrieval_record_id="record-1",
        source_id="source-1",
        source_version="1.0.0",
        source_sha256=HASH_A,
        source_locator="release/source-1.md",
        document_id="document-1",
        chunk_id="chunk-1",
        chunk_ordinal=0,
        span_kind="paragraph",
        span_start=0,
        span_end=42,
        chunk_sha256=HASH_B,
        classification="privileged_work_product",
    )


def provenance(
    projections: tuple[ProjectionPins, ...] | None = None,
    template_id: QueryTemplateId = QueryTemplateId.LEXICAL_SEARCH_V1,
    rank_path: tuple[RankSignal, ...] = (RankSignal.LEXICAL_RANK,),
) -> RetrievalProvenance:
    selected = projections or (lexical_projection(),)
    return RetrievalProvenance(
        projections=selected,
        authorization=authorization(selected[0].scope),
        credential_binding=credential(selected[0].scope),
        template=template(template_id),
        retrieval_adapter_version="1.0.0",
        structured_filter_sha256=HASH_D,
        source_ids=("source-1",),
        source_hashes=(HASH_A,),
        rank_path=rank_path,
        projection_lag=ProjectionLag(lag_events=0, lag_seconds=0.25),
    )


def hit(projection: ProjectionPins | None = None) -> RetrievalHit:
    return RetrievalHit(
        provenance=provenance((projection or lexical_projection(),)),
        source=source(),
        rank=1,
        score=0.9,
        content="Pinned legal source excerpt.",
    )


def test_exact_scope_invariants_and_explicit_tenant_shared_decision() -> None:
    with pytest.raises(ValidationError, match="exact Matter"):
        RetrievalScope(tenant_id=TENANT_ID, scope_kind=ScopeKind.MATTER, matter_id=None)
    with pytest.raises(ValidationError, match="null Matter"):
        RetrievalScope(
            tenant_id=TENANT_ID,
            scope_kind=ScopeKind.TENANT_SHARED,
            matter_id=MATTER_ID,
        )

    shared = RetrievalScope(
        tenant_id=TENANT_ID,
        scope_kind=ScopeKind.TENANT_SHARED,
        matter_id=None,
    )
    with pytest.raises(ValidationError, match="explicit policy decision"):
        authorization(shared)
    allowed = authorization(
        shared,
        UUID("77777777-7777-7777-7777-777777777777"),
    )
    assert allowed.scope == shared


def test_request_is_immutable_extra_forbid_and_deterministic() -> None:
    request = lexical_request()
    assert request.canonical_json() == lexical_request().canonical_json()
    assert request.canonical_sha256() == lexical_request().canonical_sha256()
    with pytest.raises(ValidationError, match="frozen"):
        request.retrieval_adapter_version = "2.0.0"
    payload = request.model_dump()
    payload["raw_sql"] = "select * from protected"
    with pytest.raises(ValidationError, match="Extra inputs"):
        RetrievalRequest.model_validate(payload)
    with pytest.raises(ValueError, match="copy updates"):
        request.model_copy(update={"retrieval_adapter_version": "2.0.0"})


@pytest.mark.parametrize(
    "forbidden", ["raw_sql", "raw_filter", "raw_cypher", "graph_name"]
)
def test_query_parameters_reject_arbitrary_backend_inputs(forbidden: str) -> None:
    values = {"query_text": "fixed query", forbidden: "attacker controlled"}
    with pytest.raises(ValidationError, match="Extra inputs"):
        LexicalSearchParameters.model_validate(values)


def test_query_text_uses_utf8_byte_limit() -> None:
    with pytest.raises(ValidationError, match="UTF-8 byte limit"):
        LexicalSearchParameters(query_text="x" * 16383 + "é")


def test_template_id_selects_one_exact_parameter_model() -> None:
    values = lexical_request().model_dump()
    values["template"] = template(QueryTemplateId.LEXICAL_COUNT_V1)
    values["parameters"] = {"query_text": "adverse possession"}
    request = RetrievalRequest.model_validate(values)
    assert type(request.parameters) is LexicalCountParameters
    restored = RetrievalRequest.model_validate_json(
        request.canonical_json(), strict=False
    )
    assert type(restored.parameters) is LexicalCountParameters

    values["parameters"] = {"query_text": "fixed", "max_results": 10}
    with pytest.raises(ValidationError, match="Extra inputs"):
        RetrievalRequest.model_validate(values)


def test_graph_templates_have_closed_distinct_parameter_shapes() -> None:
    parameters = (
        GraphEntityParameters(entity_ids=("entity-1",), max_results=5),
        GraphNeighborsParameters(
            entity_ids=("entity-1",), graph_depth=2, max_results=5
        ),
        GraphPathsBoundedParameters(
            source_entity_ids=("entity-1",),
            target_entity_ids=("entity-2",),
            graph_depth=3,
            max_results=5,
        ),
        GraphClaimSupportParameters(claim_entity_ids=("claim-1",), max_results=5),
        GraphAuthorityCitationsParameters(
            authority_entity_ids=("authority-1",), max_results=5
        ),
        GraphSourceLineageParameters(
            source_ids=("source-1",), graph_depth=2, max_results=5
        ),
        GraphScopeCountParameters(),
        GraphExistsParameters(entity_ids=("entity-1",)),
    )
    assert len({type(item) for item in parameters}) == 8
    with pytest.raises(ValidationError, match="Extra inputs"):
        GraphScopeCountParameters.model_validate({"graph_name": "rg_attacker"})


def test_backend_values_are_neutral_immutable_records() -> None:
    scored = BackendScoredRecord(
        projection=lexical_projection(),
        source=source(),
        content="Pinned legal source excerpt.",
        score=0.8,
    )
    aggregate = BackendAggregateRecord(
        projection=lexical_projection(),
        aggregate_kind=RetrievalAggregateKind.COUNT,
        value=4,
    )
    hybrid = BackendHybridRecord(
        projections=(lexical_projection(), vector_projection()),
        source=source(),
        content="Fused pinned source excerpt.",
        score=0.85,
    )
    unavailable = BackendUnavailableComponent(
        component=RetrievalComponent.GRAPH,
        reason=IncompleteReason.UNAVAILABLE_UNQUALIFIED,
    )
    assert scored.source.source_id == "source-1"
    assert aggregate.value == 4
    assert hybrid.projections[1].component is RetrievalComponent.VECTOR
    assert unavailable.component is RetrievalComponent.GRAPH

    with pytest.raises(ValidationError, match="ordered lexical then vector"):
        BackendHybridRecord(
            projections=(vector_projection(), lexical_projection()),
            source=source(),
            content="Invalid fused source excerpt.",
            score=0.85,
        )


def test_hybrid_provenance_pins_both_ordered_contributing_projections() -> None:
    lexical = lexical_projection()
    vector = vector_projection()
    trace = provenance(
        (lexical, vector),
        QueryTemplateId.HYBRID_RRF_V1,
        (
            RankSignal.LEXICAL_RANK,
            RankSignal.VECTOR_DISTANCE,
            RankSignal.HYBRID_RRF,
        ),
    )
    fused = RetrievalHit(
        provenance=trace,
        source=source(),
        rank=1,
        score=0.9,
        content="Fused pinned source excerpt.",
    )
    assert tuple(item.component for item in fused.provenance.projections) == (
        RetrievalComponent.LEXICAL,
        RetrievalComponent.VECTOR,
    )
    with pytest.raises(ValidationError, match="pinned query template"):
        provenance(
            (vector, lexical),
            QueryTemplateId.HYBRID_RRF_V1,
            (RankSignal.HYBRID_RRF,),
        )


def test_vector_request_requires_finite_exact_dimension() -> None:
    with pytest.raises(ValidationError, match="finite"):
        VectorExactParameters(query_embedding=(0.0, float("nan"), 1.0))
    with pytest.raises(ValidationError, match="dimension"):
        RetrievalRequest(
            request_id=UUID("66666666-6666-6666-6666-666666666666"),
            scope=matter_scope(),
            authorization=authorization(),
            credential_binding=credential(),
            projections=(vector_projection(),),
            template=template(QueryTemplateId.VECTOR_EXACT_V1),
            retrieval_mode=RetrievalMode.VECTOR_EXACT,
            parameters=VectorExactParameters(query_embedding=(0.0, 1.0)),
            structured_filter_sha256=HASH_D,
            retrieval_adapter_version="1.0.0",
        )


def test_projection_requires_exact_component_and_replica_pins() -> None:
    values = lexical_projection().model_dump()
    values["lexical"] = None
    values["vector"] = vector_projection().vector
    with pytest.raises(ValidationError, match="incomplete or mixed"):
        ProjectionPins.model_validate(values)

    replica_values = lexical_projection().model_dump()
    replica_values["replica"] = ReplicaPins(replica_replay_lsn="0/16B6C50")
    replica = ProjectionPins.model_validate(replica_values)
    assert replica.replica is not None
    assert replica.replica.replica_replay_lsn == "0/16B6C50"


def test_request_rejects_scope_and_revision_mismatch() -> None:
    values = lexical_request().model_dump()
    other_scope = RetrievalScope(
        tenant_id=UUID("88888888-8888-8888-8888-888888888888"),
        scope_kind=ScopeKind.MATTER,
        matter_id=MATTER_ID,
    )
    values["authorization"] = authorization(other_scope)
    with pytest.raises(ValidationError, match="scopes must match"):
        RetrievalRequest.model_validate(values)


def test_result_rejects_entire_mixed_scope_or_stale_response() -> None:
    valid_hit = hit()
    result = RetrievalResult(
        request_sha256=lexical_request().canonical_sha256(),
        provenance=valid_hit.provenance,
        scope=matter_scope(),
        projection_set_id=PROJECTION_SET_ID,
        projection_generation=7,
        release_id="release-2026-08-21",
        policy_revision=HASH_B,
        rights_revision=HASH_C,
        hits=(valid_hit,),
    )
    assert result.hits == (valid_hit,)

    values = result.model_dump()
    values["projection_generation"] = 8
    with pytest.raises(ValidationError, match="rejects the entire response"):
        RetrievalResult.model_validate(values)


def test_empty_and_unavailable_results_keep_response_provenance() -> None:
    trace = provenance()
    empty = RetrievalResult(
        request_sha256=lexical_request().canonical_sha256(),
        provenance=trace,
        scope=matter_scope(),
        projection_set_id=PROJECTION_SET_ID,
        projection_generation=7,
        release_id="release-2026-08-21",
        policy_revision=HASH_B,
        rights_revision=HASH_C,
        hits=(),
    )
    unavailable = RetrievalResult(
        request_sha256=lexical_request().canonical_sha256(),
        provenance=trace,
        scope=matter_scope(),
        projection_set_id=PROJECTION_SET_ID,
        projection_generation=7,
        release_id="release-2026-08-21",
        policy_revision=HASH_B,
        rights_revision=HASH_C,
        hits=(),
        incomplete_components=(
            IncompleteComponent(
                component=RetrievalComponent.GRAPH,
                reason=IncompleteReason.UNAVAILABLE_UNQUALIFIED,
            ),
        ),
    )
    restored = RetrievalResult.model_validate_json(
        unavailable.canonical_json(), strict=False
    )
    assert empty.provenance.structured_filter_sha256 == HASH_D
    assert restored.provenance == trace


def test_response_rejects_child_structured_filter_mismatch() -> None:
    valid_hit = hit()
    trace_values = valid_hit.provenance.model_dump()
    trace_values["structured_filter_sha256"] = HASH_A
    mismatched_response_trace = RetrievalProvenance.model_validate(trace_values)
    with pytest.raises(ValidationError, match="child provenance mismatch"):
        RetrievalResult(
            request_sha256=lexical_request().canonical_sha256(),
            provenance=mismatched_response_trace,
            scope=matter_scope(),
            projection_set_id=PROJECTION_SET_ID,
            projection_generation=7,
            release_id="release-2026-08-21",
            policy_revision=HASH_B,
            rights_revision=HASH_C,
            hits=(valid_hit,),
        )


def test_incomplete_component_cannot_contribute_content() -> None:
    valid_hit = hit()
    with pytest.raises(ValidationError, match="cannot contribute content"):
        RetrievalResult(
            request_sha256=lexical_request().canonical_sha256(),
            provenance=valid_hit.provenance,
            scope=matter_scope(),
            projection_set_id=PROJECTION_SET_ID,
            projection_generation=7,
            release_id="release-2026-08-21",
            policy_revision=HASH_B,
            rights_revision=HASH_C,
            hits=(valid_hit,),
            incomplete_components=(
                IncompleteComponent(
                    component=RetrievalComponent.LEXICAL,
                    reason=IncompleteReason.BACKEND_UNAVAILABLE,
                ),
            ),
        )


def test_count_and_existence_aggregates_keep_full_provenance() -> None:
    count = RetrievalAggregateValue(
        provenance=provenance(
            (lexical_projection(),),
            QueryTemplateId.LEXICAL_COUNT_V1,
            (RankSignal.SCOPE_AGGREGATE,),
        ),
        aggregate_kind=RetrievalAggregateKind.COUNT,
        value=4,
    )
    exists = RetrievalAggregateValue(
        provenance=provenance(
            (graph_projection(),),
            QueryTemplateId.GRAPH_EXISTS_V1,
            (RankSignal.SCOPE_AGGREGATE,),
        ),
        aggregate_kind=RetrievalAggregateKind.EXISTS,
        value=True,
    )
    count_result = RetrievalResult(
        request_sha256=lexical_request().canonical_sha256(),
        provenance=count.provenance,
        scope=matter_scope(),
        projection_set_id=PROJECTION_SET_ID,
        projection_generation=7,
        release_id="release-2026-08-21",
        policy_revision=HASH_B,
        rights_revision=HASH_C,
        hits=(),
        aggregates=(count,),
    )
    assert count_result.aggregates[0].value == 4
    assert exists.value is True

    zero_trace_values = count.provenance.model_dump()
    zero_trace_values["source_ids"] = ()
    zero_trace_values["source_hashes"] = ()
    zero_count = RetrievalAggregateValue(
        provenance=RetrievalProvenance.model_validate(zero_trace_values),
        aggregate_kind=RetrievalAggregateKind.COUNT,
        value=0,
    )
    assert zero_count.value == 0


def test_aggregate_result_rejects_wrong_scope_stale_and_incomplete_data() -> None:
    count = RetrievalAggregateValue(
        provenance=provenance(
            (lexical_projection(),),
            QueryTemplateId.LEXICAL_COUNT_V1,
            (RankSignal.SCOPE_AGGREGATE,),
        ),
        aggregate_kind=RetrievalAggregateKind.COUNT,
        value=4,
    )
    common = {
        "request_sha256": lexical_request().canonical_sha256(),
        "provenance": count.provenance,
        "scope": matter_scope(),
        "projection_set_id": PROJECTION_SET_ID,
        "projection_generation": 8,
        "release_id": "release-2026-08-21",
        "policy_revision": HASH_B,
        "rights_revision": HASH_C,
        "hits": (),
        "aggregates": (count,),
    }
    with pytest.raises(ValidationError, match="rejects the entire response"):
        RetrievalResult.model_validate(common)

    common["projection_generation"] = 7
    common["incomplete_components"] = (
        IncompleteComponent(
            component=RetrievalComponent.LEXICAL,
            reason=IncompleteReason.BACKEND_UNAVAILABLE,
        ),
    )
    with pytest.raises(ValidationError, match="cannot contribute"):
        RetrievalResult.model_validate(common)


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        (RetrievalAggregateKind.COUNT, True),
        (RetrievalAggregateKind.COUNT, -1),
        (RetrievalAggregateKind.EXISTS, 1),
    ],
)
def test_aggregate_value_shape_is_strict(
    kind: RetrievalAggregateKind, value: int | bool
) -> None:
    with pytest.raises(ValidationError, match="aggregates require"):
        BackendAggregateRecord(
            projection=(
                graph_projection()
                if kind is RetrievalAggregateKind.EXISTS
                else lexical_projection()
            ),
            aggregate_kind=kind,
            value=value,
        )


def test_cache_key_requires_conditional_pins_and_is_deterministic() -> None:
    key = RetrievalCacheKey(
        scope=matter_scope(),
        principal_policy_context_sha256=HASH_A,
        policy_revision=HASH_B,
        rights_revision=HASH_C,
        projection_set_id=PROJECTION_SET_ID,
        projection_generation=7,
        release_id="release-2026-08-21",
        physical_partition_ids=("rp_11111111111111111111111111111111",),
        credential_binding_event_sha256=HASH_D,
        projection_schema_version="1.0.0",
        projection_adapter_version="1.0.0",
        projector_version="1.0.0",
        retrieval_adapter_version="1.0.0",
        template=template(QueryTemplateId.LEXICAL_SEARCH_V1),
        retrieval_mode=RetrievalMode.LEXICAL,
        canonical_query_sha256=HASH_A,
        structured_filter_sha256=HASH_D,
        required_core_watermark=40,
        text_search_configuration_sha256=HASH_B,
    )
    assert (
        key.canonical_sha256()
        == RetrievalCacheKey.model_validate(key.model_dump()).canonical_sha256()
    )
    values = key.model_dump()
    values["text_search_configuration_sha256"] = None
    with pytest.raises(ValidationError, match="text-search configuration"):
        RetrievalCacheKey.model_validate(values)


def test_error_envelope_is_uniform_and_sanitized() -> None:
    request_id = UUID("66666666-6666-6666-6666-666666666666")
    denied = RetrievalAuthorizationError(
        "matter rg_secret exists", request_id=request_id
    )
    missing = RetrievalUnavailableError(
        "partition rp_secret is absent", request_id=request_id
    )
    assert denied.public_error() == missing.public_error()
    serialized = denied.public_error().model_dump_json()
    assert "rg_secret" not in serialized
    assert "rp_secret" not in serialized
