"""Trace, replica-lag, scope-denial, and recall qualification tests."""

from __future__ import annotations

import pytest
from sklegal_retrieval.errors import (
    RetrievalAuthorizationError,
    RetrievalErrorCode,
    RetrievalReplicaLagError,
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
    DistanceMetric,
    GraphEntityParameters,
    GraphExistsParameters,
    GraphScopeCountParameters,
    IncompleteReason,
    LexicalSearchParameters,
    ProjectionLag,
    QueryTemplateId,
    ReplicaPins,
    RetrievalCacheKey,
    RetrievalComponent,
    RetrievalMode,
    RetrievalRequest,
    VectorExactParameters,
    postgres_lsn_value,
)
from sklegal_retrieval.orchestrator import RetrievalOrchestrator
from sklegal_retrieval.query_templates import (
    QUERY_TEMPLATES,
    QueryTemplateError,
    bind_query_template,
)

from tests.support.retrieval_pins import (
    FILTER_HASH,
    HASH_B,
    HASH_C,
    OTHER_MATTER_ID,
    OTHER_TENANT_ID,
    make_binding,
    make_projection,
    make_request,
    make_scope,
    make_source,
    make_template_pins,
)


def _orchestrator(
    request: RetrievalRequest,
    executor: FakeRetrievalExecutor,
    *,
    event_log: list[str] | None = None,
    deny: bool = False,
) -> RetrievalOrchestrator:
    return RetrievalOrchestrator(
        authorizer=FakeAuthorizer(
            None if deny else request.authorization,
            event_log=event_log,
        ),
        credential_resolver=FakeCredentialBindingResolver(
            request.credential_binding, event_log=event_log
        ),
        registry=FakeActiveProjectionRegistry(request.projections, event_log=event_log),
        executor=executor,
    )


def _lexical_request(**projection_kwargs: object) -> RetrievalRequest:
    scope = make_scope()
    projection = make_projection(
        RetrievalComponent.LEXICAL,
        scope,
        **projection_kwargs,  # type: ignore[arg-type]
    )
    return make_request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="liberty mutual"),
        projections=(projection,),
    )


def _vector_request(**projection_kwargs: object) -> RetrievalRequest:
    scope = make_scope()
    projection = make_projection(
        RetrievalComponent.VECTOR,
        scope,
        **projection_kwargs,  # type: ignore[arg-type]
    )
    return make_request(
        mode=RetrievalMode.VECTOR_EXACT,
        template_id=QueryTemplateId.VECTOR_EXACT_V1,
        parameters=VectorExactParameters(query_embedding=(1.0, 0.0)),
        projections=(projection,),
    )


def _graph_request(
    template_id: QueryTemplateId,
    parameters: object,
    *,
    optional: bool = False,
) -> RetrievalRequest:
    scope = make_scope()
    projection = make_projection(RetrievalComponent.GRAPH, scope)
    return make_request(
        mode=RetrievalMode.GRAPH,
        template_id=template_id,
        parameters=parameters,
        projections=(projection,),
        optional_components=(RetrievalComponent.GRAPH,) if optional else (),
    )


def test_replica_behind_the_required_lsn_is_denied() -> None:
    request = _lexical_request(
        replica=ReplicaPins(
            replica_replay_lsn="0/00000100", required_replay_lsn="0/00000200"
        )
    )
    orchestrator = _orchestrator(request, FakeRetrievalExecutor())
    with pytest.raises(RetrievalReplicaLagError) as failure:
        orchestrator.retrieve(request)
    assert failure.value.code is RetrievalErrorCode.REPLICA_LAG


def test_replica_without_a_required_lsn_pin_fails_closed() -> None:
    request = _lexical_request(replica=ReplicaPins(replica_replay_lsn="0/00000200"))
    orchestrator = _orchestrator(request, FakeRetrievalExecutor())
    with pytest.raises(RetrievalReplicaLagError):
        orchestrator.retrieve(request)


def test_replica_at_or_beyond_the_required_lsn_serves_and_traces() -> None:
    replica = ReplicaPins(
        replica_replay_lsn="0/00000200", required_replay_lsn="0/00000100"
    )
    request = _lexical_request(replica=replica)
    executor = FakeRetrievalExecutor(
        [
            FakeRetrievalRecord(
                projection=request.projections[0],
                source=make_source("record-1"),
                content="liberty mutual release packet",
            )
        ]
    )
    result = _orchestrator(request, executor).retrieve(request)
    assert len(result.hits) == 1
    traced = result.provenance.projections[0].replica
    assert traced is not None
    assert traced.replica_replay_lsn == "0/00000200"


def test_replica_lag_denial_shares_the_sanitized_external_shape() -> None:
    lagging = _lexical_request(
        replica=ReplicaPins(
            replica_replay_lsn="0/00000100", required_replay_lsn="0/00000200"
        )
    )
    with pytest.raises(RetrievalReplicaLagError) as lag_failure:
        _orchestrator(lagging, FakeRetrievalExecutor()).retrieve(lagging)
    missing_registry = FakeActiveProjectionRegistry(())
    request = _lexical_request()
    orchestrator = RetrievalOrchestrator(
        authorizer=FakeAuthorizer(request.authorization),
        credential_resolver=FakeCredentialBindingResolver(request.credential_binding),
        registry=missing_registry,
        executor=FakeRetrievalExecutor(),
    )
    with pytest.raises(RetrievalUnavailableError) as missing_failure:
        orchestrator.retrieve(request)
    assert lag_failure.value.public_error() == missing_failure.value.public_error()


def test_postgres_lsn_comparison_is_hexadecimal_and_strict() -> None:
    assert postgres_lsn_value("0/00000200") > postgres_lsn_value("0/000001FF")
    assert postgres_lsn_value("1/00000000") > postgres_lsn_value("0/FFFFFFFF")
    with pytest.raises(ValueError):
        postgres_lsn_value("not-an-lsn")


def test_trace_records_the_registry_observed_projection_lag() -> None:
    request = _lexical_request(lag=ProjectionLag(lag_events=4, lag_seconds=1.25))
    executor = FakeRetrievalExecutor(
        [
            FakeRetrievalRecord(
                projection=request.projections[0],
                source=make_source("record-1"),
                content="liberty mutual release packet",
            )
        ]
    )
    result = _orchestrator(request, executor).retrieve(request)
    assert result.provenance.projection_lag == ProjectionLag(
        lag_events=4, lag_seconds=1.25
    )


def test_trace_shape_is_deterministic_across_identical_requests() -> None:
    def _run() -> str:
        request = _lexical_request()
        executor = FakeRetrievalExecutor(
            [
                FakeRetrievalRecord(
                    projection=request.projections[0],
                    source=make_source("record-1"),
                    content="liberty mutual release packet",
                ),
                FakeRetrievalRecord(
                    projection=request.projections[0],
                    source=make_source("record-2"),
                    content="liberty mutual settlement draft",
                ),
            ]
        )
        result = _orchestrator(request, executor).retrieve(request)
        return result.canonical_sha256()

    assert _run() == _run()


def test_cache_keys_never_collide_across_revisions() -> None:
    scope = make_scope()
    projection = make_projection(RetrievalComponent.LEXICAL, scope)
    base = {
        "scope": scope,
        "principal_policy_context_sha256": "a" * 64,
        "policy_revision": HASH_B,
        "rights_revision": HASH_C,
        "projection_set_id": projection.projection_set_id,
        "projection_generation": projection.projection_generation,
        "release_id": projection.release_id,
        "physical_partition_ids": (projection.physical_partition_id,),
        "credential_binding_event_sha256": "d" * 64,
        "projection_schema_version": "1.0.0",
        "projection_adapter_version": "1.0.0",
        "projector_version": "1.0.0",
        "retrieval_adapter_version": "1.0.0",
        "template": make_template_pins(QueryTemplateId.LEXICAL_SEARCH_V1),
        "retrieval_mode": RetrievalMode.LEXICAL,
        "canonical_query_sha256": "2" * 64,
        "structured_filter_sha256": FILTER_HASH,
        "required_core_watermark": 5,
        "text_search_configuration_sha256": "e" * 64,
    }
    current = RetrievalCacheKey(**base)  # type: ignore[arg-type]
    for field in ("policy_revision", "rights_revision"):
        stale = RetrievalCacheKey(**{**base, field: "9" * 64})  # type: ignore[arg-type]
        assert stale.canonical_sha256() != current.canonical_sha256()
    other_partition = make_projection(
        RetrievalComponent.LEXICAL, scope, partition_suffix="4" * 32
    )
    foreign = RetrievalCacheKey(
        **{  # type: ignore[arg-type]
            **base,
            "physical_partition_ids": (other_partition.physical_partition_id,),
        }
    )
    assert foreign.canonical_sha256() != current.canonical_sha256()


def test_authorization_denial_precedes_any_vector_backend_access() -> None:
    request = _vector_request()
    event_log: list[str] = []
    orchestrator = _orchestrator(
        request, FakeRetrievalExecutor(), event_log=event_log, deny=True
    )
    with pytest.raises(RetrievalAuthorizationError):
        orchestrator.retrieve(request)
    assert event_log == ["authorization"]


def test_authorization_denial_precedes_any_graph_backend_access() -> None:
    request = _graph_request(
        QueryTemplateId.GRAPH_ENTITY_V1,
        GraphEntityParameters(entity_ids=("entity-1",)),
    )
    event_log: list[str] = []
    orchestrator = _orchestrator(
        request, FakeRetrievalExecutor(), event_log=event_log, deny=True
    )
    with pytest.raises(RetrievalAuthorizationError):
        orchestrator.retrieve(request)
    assert event_log == ["authorization"]


def test_approximate_vector_search_is_denied_before_registry_promotion() -> None:
    assert not any("ann" in template_id for template_id in QUERY_TEMPLATES)
    assert not any("approximate" in template_id for template_id in QUERY_TEMPLATES)
    with pytest.raises(QueryTemplateError):
        bind_query_template("vector.ann.v1", {"query_embedding": (1.0, 0.0)})
    scope = make_scope()
    projection = make_projection(RetrievalComponent.VECTOR, scope)
    request = make_request(
        mode=RetrievalMode.VECTOR_EXACT,
        template_id=QueryTemplateId.VECTOR_EXACT_V1,
        parameters=VectorExactParameters(query_embedding=(1.0, 0.0)),
        projections=(projection,),
    )
    with pytest.raises(ValueError, match="unvalidated retrieval copy"):
        request.model_copy(update={"retrieval_mode": "ann"})


def test_graph_count_and_existence_keep_mandatory_scope() -> None:
    count_request = _graph_request(
        QueryTemplateId.GRAPH_SCOPE_COUNT_V1, GraphScopeCountParameters()
    )
    with pytest.raises(RetrievalUnavailableError):
        _orchestrator(count_request, FakeRetrievalExecutor()).retrieve(count_request)

    exists_request = _graph_request(
        QueryTemplateId.GRAPH_EXISTS_V1,
        GraphExistsParameters(entity_ids=("entity-1",)),
    )
    with pytest.raises(RetrievalUnavailableError):
        _orchestrator(exists_request, FakeRetrievalExecutor()).retrieve(exists_request)


def test_optional_unqualified_graph_is_explicitly_incomplete() -> None:
    request = _graph_request(
        QueryTemplateId.GRAPH_ENTITY_V1,
        GraphEntityParameters(entity_ids=("entity-1",)),
        optional=True,
    )
    unqualified = FakeRetrievalExecutor(
        graph_result=AGE_UNAVAILABLE.model_copy().__class__(
            component=RetrievalComponent.GRAPH,
            reason=IncompleteReason.UNAVAILABLE_UNQUALIFIED,
        )
    )
    result = _orchestrator(request, unqualified).retrieve(request)
    assert result.hits == ()
    assert len(result.incomplete_components) == 1
    assert result.incomplete_components[0].component is RetrievalComponent.GRAPH
    assert result.incomplete_components[0].reason is (
        IncompleteReason.UNAVAILABLE_UNQUALIFIED
    )


def test_cross_tenant_partition_rows_reject_the_whole_lexical_response() -> None:
    request = _lexical_request()
    foreign = make_projection(
        RetrievalComponent.LEXICAL, make_scope(tenant_id=OTHER_TENANT_ID)
    )
    executor = FakeRetrievalExecutor(
        [
            FakeRetrievalRecord(
                projection=request.projections[0],
                source=make_source("record-1"),
                content="liberty mutual release packet",
            ),
            FakeRetrievalRecord(
                projection=foreign,
                source=make_source("record-1"),
                content="liberty mutual foreign tenant row",
            ),
        ],
        leak_partition_rows=True,
    )
    with pytest.raises(Exception) as failure:
        _orchestrator(request, executor).retrieve(request)
    assert failure.value.public_error().error == "retrieval_unavailable"  # type: ignore[attr-defined]


def test_cross_tenant_partition_rows_reject_the_whole_vector_response() -> None:
    request = _vector_request()
    foreign = make_projection(
        RetrievalComponent.VECTOR, make_scope(tenant_id=OTHER_TENANT_ID)
    )
    executor = FakeRetrievalExecutor(
        [
            FakeRetrievalRecord(
                projection=foreign,
                source=make_source("record-9"),
                content="foreign tenant embedding row",
                embedding=(1.0, 0.0),
            ),
        ],
        leak_partition_rows=True,
    )
    with pytest.raises(Exception) as failure:
        _orchestrator(request, executor).retrieve(request)
    assert failure.value.public_error().error == "retrieval_unavailable"  # type: ignore[attr-defined]


def test_cross_matter_rows_without_a_filter_reject_the_whole_response() -> None:
    request = _lexical_request()
    same_partition_other_matter = make_projection(
        RetrievalComponent.LEXICAL,
        make_scope(matter_id=OTHER_MATTER_ID),
    )
    executor = FakeRetrievalExecutor(
        [
            FakeRetrievalRecord(
                projection=same_partition_other_matter,
                source=make_source("record-7"),
                content="liberty mutual same tenant other matter row",
            ),
        ],
        leak_partition_rows=True,
    )
    with pytest.raises(Exception) as failure:
        _orchestrator(request, executor).retrieve(request)
    assert failure.value.public_error().error == "retrieval_unavailable"  # type: ignore[attr-defined]


def test_vector_cross_matter_rows_without_a_filter_reject_the_response() -> None:
    request = _vector_request()
    same_partition_other_matter = make_projection(
        RetrievalComponent.VECTOR,
        make_scope(matter_id=OTHER_MATTER_ID),
    )
    executor = FakeRetrievalExecutor(
        [
            FakeRetrievalRecord(
                projection=same_partition_other_matter,
                source=make_source("record-8"),
                content="same tenant other matter embedding row",
                embedding=(1.0, 0.0),
            ),
        ],
        leak_partition_rows=True,
    )
    with pytest.raises(Exception) as failure:
        _orchestrator(request, executor).retrieve(request)
    assert failure.value.public_error().error == "retrieval_unavailable"  # type: ignore[attr-defined]


def test_exact_vector_recall_baseline_matches_brute_force() -> None:
    embeddings = {
        "record-1": (1.0, 0.0),
        "record-2": (0.9, 0.1),
        "record-3": (0.0, 1.0),
        "record-4": (0.5, 0.5),
        "record-5": (-1.0, 0.0),
    }
    scope = make_scope()
    projection = make_projection(RetrievalComponent.VECTOR, scope)
    request = make_request(
        mode=RetrievalMode.VECTOR_EXACT,
        template_id=QueryTemplateId.VECTOR_EXACT_V1,
        parameters=VectorExactParameters(query_embedding=(1.0, 0.0), max_results=3),
        projections=(projection,),
    )
    executor = FakeRetrievalExecutor(
        [
            FakeRetrievalRecord(
                projection=projection,
                source=make_source(record_id),
                content=f"embedding row {record_id}",
                embedding=embedding,
            )
            for record_id, embedding in embeddings.items()
        ]
    )
    result = _orchestrator(request, executor).retrieve(request)

    query = (1.0, 0.0)
    reference = sorted(
        embeddings,
        key=lambda record_id: (
            sum((a - b) ** 2 for a, b in zip(query, embeddings[record_id])),
            record_id,
        ),
    )[:3]
    ranked = [hit.source.retrieval_record_id for hit in result.hits]
    assert ranked == reference
    recall_at_3 = len(set(ranked) & set(reference)) / len(reference)
    assert recall_at_3 == 1.0
    assert result.provenance.projections[0].vector is not None
    assert result.provenance.projections[0].vector.distance_metric is (
        DistanceMetric.L2
    )


def test_credential_binding_mismatch_denies_before_registry_access() -> None:
    request = _lexical_request()
    other = make_binding(
        request.scope, request.projections[0], policy_revision="8" * 64
    )
    event_log: list[str] = []
    orchestrator = RetrievalOrchestrator(
        authorizer=FakeAuthorizer(request.authorization, event_log=event_log),
        credential_resolver=FakeCredentialBindingResolver(other, event_log=event_log),
        registry=FakeActiveProjectionRegistry(request.projections, event_log=event_log),
        executor=FakeRetrievalExecutor(),
    )
    with pytest.raises(RetrievalAuthorizationError):
        orchestrator.retrieve(request)
    assert event_log == ["authorization", "credential_binding"]


def test_request_rejects_a_template_digest_mismatch() -> None:
    scope = make_scope()
    projection = make_projection(RetrievalComponent.LEXICAL, scope)
    request = make_request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="liberty mutual"),
        projections=(projection,),
    )
    tampered = request.model_copy(deep=True)
    object.__setattr__(  # bypass frozen only to simulate a forged pin carrier
        tampered,
        "template",
        make_template_pins(QueryTemplateId.LEXICAL_SEARCH_V1, digest="0" * 64),
    )
    orchestrator = _orchestrator(tampered, FakeRetrievalExecutor())
    with pytest.raises(Exception) as failure:
        orchestrator.retrieve(tampered)
    assert failure.value.public_error().error == "retrieval_unavailable"  # type: ignore[attr-defined]


def test_retrieval_request_error_is_a_request_error() -> None:
    with pytest.raises(RetrievalRequestError):
        raise RetrievalRequestError("closed query-template binding failed")


def test_graph_templates_are_read_only_and_closed() -> None:
    graph_templates = {
        template_id: template
        for template_id, template in QUERY_TEMPLATES.items()
        if template_id.startswith("graph.")
    }
    assert len(graph_templates) == 8
    for template in graph_templates.values():
        assert template.graph_optional
        operation = template.operation
        for verb in ("write", "create", "merge", "delete", "set", "mutate", "load"):
            assert f".{verb}" not in operation
