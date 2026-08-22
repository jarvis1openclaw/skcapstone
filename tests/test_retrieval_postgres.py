"""Focused tests for the bounded driver-neutral PostgreSQL adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from sklegal_retrieval.contract import ContractValidationError
from sklegal_retrieval.errors import (
    RetrievalIntegrityError,
    RetrievalRequestError,
    RetrievalUnavailableError,
)
from sklegal_retrieval.models import (
    BackendAggregateRecord,
    BackendHybridRecord,
    BackendScoredRecord,
    BackendUnavailableComponent,
    DistanceMetric,
    GraphPins,
    IncompleteReason,
    LexicalPins,
    ProjectionPins,
    RetrievalAggregateKind,
    RetrievalComponent,
    RetrievalScope,
    ScopeKind,
    SourceProvenance,
    VectorPins,
)
from sklegal_retrieval.postgres import PostgresRetrievalAdapter
from sklegal_retrieval.query_templates import (
    BoundQueryTemplate,
    QueryTemplateError,
    bind_query_template,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
MATTER_ID = UUID("22222222-2222-2222-2222-222222222222")
PROJECTION_SET_ID = UUID("33333333-3333-3333-3333-333333333333")


class RecordingRunner:
    def __init__(
        self,
        rows: Sequence[Mapping[str, object]] | object = (),
        *,
        error: Exception | None = None,
    ) -> None:
        self.rows = rows
        self.error = error
        self.calls: list[dict[str, object]] = []

    def fetch_all(
        self,
        *,
        component_access_refs: tuple[str, ...],
        statement: str,
        parameters: tuple[object, ...],
    ) -> Any:
        self.calls.append(
            {
                "component_access_refs": component_access_refs,
                "statement": statement,
                "parameters": parameters,
            }
        )
        if self.error is not None:
            raise self.error
        return self.rows


def scope(*, tenant_id: UUID = TENANT_ID) -> RetrievalScope:
    return RetrievalScope(
        tenant_id=tenant_id,
        scope_kind=ScopeKind.MATTER,
        matter_id=MATTER_ID,
    )


def lexical_projection(
    *, selected_scope: RetrievalScope | None = None
) -> ProjectionPins:
    return ProjectionPins(
        component=RetrievalComponent.LEXICAL,
        scope=selected_scope or scope(),
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


def vector_projection() -> ProjectionPins:
    return ProjectionPins(
        component=RetrievalComponent.VECTOR,
        scope=scope(),
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


def graph_projection() -> ProjectionPins:
    return ProjectionPins(
        component=RetrievalComponent.GRAPH,
        scope=scope(),
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
            exact_graph_gateway_credential_mapping_ref="mapping.graph.1",
            graph_gateway_function_definition_sha256=HASH_A,
            apache_age_qualification_evidence_sha256=HASH_B,
        ),
    )


def source(record_id: str = "record-1") -> SourceProvenance:
    return SourceProvenance(
        retrieval_record_id=record_id,
        source_id=f"source-{record_id}",
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


def scored_row(
    projection: ProjectionPins | None = None,
    *,
    score: object = 0.75,
    distance: object | None = None,
    record_id: str = "record-1",
) -> dict[str, object]:
    row: dict[str, object] = {
        "projection": (projection or lexical_projection()).model_dump(mode="json"),
        "source": source(record_id).model_dump(mode="json"),
        "content": "Pinned legal source excerpt.",
        "score": score,
    }
    if distance is not None:
        row["distance"] = distance
    return row


def test_lexical_search_uses_one_fixed_parameterized_function_call() -> None:
    injected_text = "possession'); SELECT secret FROM hidden; --"
    runner = RecordingRunner((scored_row(),))
    result = PostgresRetrievalAdapter(runner).execute(
        bind_query_template(
            "lexical.search.v1",
            {"query_text": injected_text, "max_results": 10},
        ),
        (lexical_projection(),),
    )

    assert isinstance(result, tuple)
    assert len(result) == 1
    assert isinstance(result[0], BackendScoredRecord)
    assert result[0].source == source()
    assert runner.calls == [
        {
            "component_access_refs": ("access.lexical.1",),
            "statement": ("SELECT * FROM sklegal_retrieval.lexical_search_v1(%s, %s)"),
            "parameters": (injected_text, 10),
        }
    ]
    assert injected_text not in str(runner.calls[0]["statement"])


def test_vector_exact_uses_fixed_function_and_validates_distance() -> None:
    projection = vector_projection()
    runner = RecordingRunner((scored_row(projection, distance=0.25),))
    result = PostgresRetrievalAdapter(runner).execute(
        bind_query_template(
            "vector.exact.v1",
            {"query_embedding": (0.0, 1.0, 0.5), "max_results": 5},
        ),
        (projection,),
    )

    assert isinstance(result, tuple)
    assert result[0].distance == 0.25
    assert runner.calls[0] == {
        "component_access_refs": ("access.vector.1",),
        "statement": "SELECT * FROM sklegal_retrieval.vector_exact_v1(%s, %s)",
        "parameters": ((0.0, 1.0, 0.5), 5),
    }


def test_hybrid_rrf_uses_only_ordered_lexical_and_vector_projections() -> None:
    lexical = lexical_projection()
    vector = vector_projection()
    runner = RecordingRunner(
        (
            {
                "projections": (
                    lexical.model_dump(mode="json"),
                    vector.model_dump(mode="json"),
                ),
                "source": source().model_dump(mode="json"),
                "content": "Pinned legal source excerpt.",
                "score": 0.032,
            },
        )
    )
    result = PostgresRetrievalAdapter(runner).execute(
        bind_query_template(
            "hybrid.rrf.v1",
            {
                "query_text": "adverse possession",
                "query_embedding": (0.0, 1.0, 0.5),
                "max_results": 3,
            },
        ),
        (lexical, vector),
    )

    assert isinstance(result, tuple)
    assert isinstance(result[0], BackendHybridRecord)
    assert result[0].projections == (lexical, vector)
    assert runner.calls[0] == {
        "component_access_refs": ("access.lexical.1", "access.vector.1"),
        "statement": ("SELECT * FROM sklegal_retrieval.hybrid_rrf_v1(%s, %s, %s)"),
        "parameters": ("adverse possession", (0.0, 1.0, 0.5), 3),
    }


def test_lexical_count_returns_one_typed_non_negative_aggregate() -> None:
    projection = lexical_projection()
    runner = RecordingRunner(
        (
            {
                "projection": projection.model_dump(mode="json"),
                "aggregate_kind": "count",
                "value": 12,
            },
        )
    )
    result = PostgresRetrievalAdapter(runner).execute(
        bind_query_template(
            "lexical.count.v1",
            {"query_text": "adverse possession"},
        ),
        (projection,),
    )

    assert result == BackendAggregateRecord(
        projection=projection,
        aggregate_kind=RetrievalAggregateKind.COUNT,
        value=12,
    )
    assert runner.calls[0]["statement"] == (
        "SELECT * FROM sklegal_retrieval.lexical_count_v1(%s)"
    )
    assert runner.calls[0]["parameters"] == ("adverse possession",)


def test_qualified_graph_backend_is_explicitly_unavailable_without_backend_call() -> (
    None
):
    runner = RecordingRunner(error=AssertionError("runner must not be called"))
    result = PostgresRetrievalAdapter(runner).execute(
        bind_query_template(
            "graph.entity.v1",
            {"entity_ids": ("entity-1",), "max_results": 5},
        ),
        (graph_projection(),),
    )

    assert result == BackendUnavailableComponent(
        component=RetrievalComponent.GRAPH,
        reason=IncompleteReason.BACKEND_UNAVAILABLE,
    )
    assert runner.calls == []


def test_forged_template_definition_and_raw_controls_fail_before_runner() -> None:
    runner = RecordingRunner()
    valid = bind_query_template(
        "lexical.search.v1",
        {"query_text": "fixed", "max_results": 5},
    )
    forged = BoundQueryTemplate(
        template=replace(valid.template, operation="attacker.sql"),
        parameters=valid.parameters,
    )
    with pytest.raises(RetrievalRequestError, match="approved pin"):
        PostgresRetrievalAdapter(runner).execute(forged, (lexical_projection(),))
    with pytest.raises(QueryTemplateError, match="raw query controls"):
        bind_query_template(
            "lexical.search.v1",
            {
                "query_text": "fixed",
                "max_results": 5,
                "raw_sql": "SELECT secret",
            },
        )
    assert runner.calls == []


def test_wrong_component_order_and_mixed_projection_set_fail_before_runner() -> None:
    runner = RecordingRunner()
    bound = bind_query_template(
        "hybrid.rrf.v1",
        {
            "query_text": "fixed",
            "query_embedding": (0.0, 1.0, 0.5),
            "max_results": 5,
        },
    )
    with pytest.raises(RetrievalIntegrityError, match="do not match"):
        PostgresRetrievalAdapter(runner).execute(
            bound,
            (vector_projection(), lexical_projection()),
        )

    vector_values = vector_projection().model_dump()
    vector_values["release_id"] = "different-release"
    mixed_vector = ProjectionPins.model_validate(vector_values)
    with pytest.raises(RetrievalIntegrityError, match="atomic projection set"):
        PostgresRetrievalAdapter(runner).execute(
            bound,
            (lexical_projection(), mixed_vector),
        )
    assert runner.calls == []


def test_wrong_scope_or_extra_column_rejects_entire_backend_response() -> None:
    other = lexical_projection(
        selected_scope=scope(tenant_id=UUID("99999999-9999-9999-9999-999999999999"))
    )
    runner = RecordingRunner((scored_row(), scored_row(other)))
    adapter = PostgresRetrievalAdapter(runner)
    bound = bind_query_template(
        "lexical.search.v1",
        {"query_text": "fixed", "max_results": 5},
    )
    with pytest.raises(RetrievalIntegrityError, match="selected projection"):
        adapter.execute(bound, (lexical_projection(),))

    leaked = scored_row()
    leaked["physical_partition_id"] = "rp_secret"
    runner.rows = (leaked,)
    with pytest.raises(RetrievalIntegrityError, match="failed validation"):
        adapter.execute(bound, (lexical_projection(),))


@pytest.mark.parametrize(
    ("row"),
    [
        scored_row(score=float("nan")),
        scored_row(score="0.5"),
        scored_row(vector_projection()),
    ],
)
def test_invalid_scored_rows_raise_sanitized_integrity_error(
    row: Mapping[str, object],
) -> None:
    projection = (
        vector_projection()
        if row.get("projection") == vector_projection().model_dump(mode="json")
        else lexical_projection()
    )
    template_id = (
        "vector.exact.v1"
        if projection.component is RetrievalComponent.VECTOR
        else "lexical.search.v1"
    )
    parameters: dict[str, object] = (
        {"query_embedding": (0.0, 1.0, 0.5), "max_results": 5}
        if projection.component is RetrievalComponent.VECTOR
        else {"query_text": "fixed", "max_results": 5}
    )
    with pytest.raises(RetrievalIntegrityError) as error:
        PostgresRetrievalAdapter(RecordingRunner((row,))).execute(
            bind_query_template(template_id, parameters),
            (projection,),
        )
    assert error.value.public_error().message == "Retrieval could not be completed."
    assert error.value.__cause__ is None
    assert error.value.__context__ is None or error.value.__suppress_context__ is True


@pytest.mark.parametrize("value", [-1, True, "12"])
def test_count_rejects_invalid_or_coerced_values(value: object) -> None:
    projection = lexical_projection()
    runner = RecordingRunner(
        (
            {
                "projection": projection,
                "aggregate_kind": "count",
                "value": value,
            },
        )
    )
    with pytest.raises(RetrievalIntegrityError, match="aggregate response"):
        PostgresRetrievalAdapter(runner).execute(
            bind_query_template("lexical.count.v1", {"query_text": "fixed"}),
            (projection,),
        )


def test_server_cannot_return_more_rows_than_the_bound() -> None:
    runner = RecordingRunner((scored_row(), scored_row()))
    with pytest.raises(RetrievalIntegrityError, match="approved bound"):
        PostgresRetrievalAdapter(runner).execute(
            bind_query_template(
                "lexical.search.v1",
                {"query_text": "fixed", "max_results": 1},
            ),
            (lexical_projection(),),
        )


def test_adapter_enforces_deterministic_lexical_and_vector_order() -> None:
    lexical_rows = (
        scored_row(score=0.5, record_id="record-b"),
        scored_row(score=0.9, record_id="record-c"),
        scored_row(score=0.5, record_id="record-a"),
    )
    lexical_result = PostgresRetrievalAdapter(RecordingRunner(lexical_rows)).execute(
        bind_query_template(
            "lexical.search.v1",
            {"query_text": "fixed", "max_results": 3},
        ),
        (lexical_projection(),),
    )
    assert isinstance(lexical_result, tuple)
    assert [item.source.retrieval_record_id for item in lexical_result] == [
        "record-c",
        "record-a",
        "record-b",
    ]

    vector = vector_projection()
    vector_rows = (
        scored_row(vector, distance=0.5, record_id="record-b"),
        scored_row(vector, distance=0.1, record_id="record-c"),
        scored_row(vector, distance=0.5, record_id="record-a"),
    )
    vector_result = PostgresRetrievalAdapter(RecordingRunner(vector_rows)).execute(
        bind_query_template(
            "vector.exact.v1",
            {"query_embedding": (0.0, 1.0, 0.5), "max_results": 3},
        ),
        (vector,),
    )
    assert isinstance(vector_result, tuple)
    assert [item.source.retrieval_record_id for item in vector_result] == [
        "record-c",
        "record-a",
        "record-b",
    ]


def test_adapter_enforces_deterministic_hybrid_order() -> None:
    lexical = lexical_projection()
    vector = vector_projection()

    def hybrid_row(record_id: str, score: float) -> dict[str, object]:
        return {
            "projections": (lexical, vector),
            "source": source(record_id),
            "content": "Pinned legal source excerpt.",
            "score": score,
        }

    rows = (
        hybrid_row("record-b", 0.02),
        hybrid_row("record-c", 0.04),
        hybrid_row("record-a", 0.02),
    )
    result = PostgresRetrievalAdapter(RecordingRunner(rows)).execute(
        bind_query_template(
            "hybrid.rrf.v1",
            {
                "query_text": "fixed",
                "query_embedding": (0.0, 1.0, 0.5),
                "max_results": 3,
            },
        ),
        (lexical, vector),
    )
    assert isinstance(result, tuple)
    assert [item.source.retrieval_record_id for item in result] == [
        "record-c",
        "record-a",
        "record-b",
    ]


def test_duplicate_record_ids_reject_each_ranked_response_shape() -> None:
    lexical = lexical_projection()
    lexical_adapter = PostgresRetrievalAdapter(
        RecordingRunner((scored_row(), scored_row(score=0.5)))
    )
    with pytest.raises(RetrievalIntegrityError, match="duplicate records"):
        lexical_adapter.execute(
            bind_query_template(
                "lexical.search.v1",
                {"query_text": "fixed", "max_results": 2},
            ),
            (lexical,),
        )

    vector = vector_projection()
    vector_adapter = PostgresRetrievalAdapter(
        RecordingRunner(
            (
                scored_row(vector, distance=0.1),
                scored_row(vector, score=0.5, distance=0.2),
            )
        )
    )
    with pytest.raises(RetrievalIntegrityError, match="duplicate records"):
        vector_adapter.execute(
            bind_query_template(
                "vector.exact.v1",
                {"query_embedding": (0.0, 1.0, 0.5), "max_results": 2},
            ),
            (vector,),
        )

    hybrid_row = {
        "projections": (lexical, vector),
        "source": source(),
        "content": "Pinned legal source excerpt.",
        "score": 0.02,
    }
    hybrid_adapter = PostgresRetrievalAdapter(
        RecordingRunner((hybrid_row, dict(hybrid_row)))
    )
    with pytest.raises(RetrievalIntegrityError, match="duplicate records"):
        hybrid_adapter.execute(
            bind_query_template(
                "hybrid.rrf.v1",
                {
                    "query_text": "fixed",
                    "query_embedding": (0.0, 1.0, 0.5),
                    "max_results": 2,
                },
            ),
            (lexical, vector),
        )


def test_backend_exception_is_typed_and_does_not_expose_internal_details() -> None:
    runner = RecordingRunner(
        error=RuntimeError("credential secret-value opened rp_secret")
    )
    with pytest.raises(RetrievalUnavailableError) as error:
        PostgresRetrievalAdapter(runner).execute(
            bind_query_template(
                "lexical.search.v1",
                {"query_text": "fixed", "max_results": 1},
            ),
            (lexical_projection(),),
        )
    assert "secret-value" not in str(error.value)
    assert "rp_secret" not in error.value.public_error().model_dump_json()
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


def test_contract_failure_is_translated_without_exposing_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound = bind_query_template(
        "lexical.search.v1",
        {"query_text": "fixed", "max_results": 1},
    )
    sentinel = "sensitive-contract-path"

    def fail_closed(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise ContractValidationError(sentinel)

    monkeypatch.setattr(
        "sklegal_retrieval.postgres.bind_query_template",
        fail_closed,
    )
    with pytest.raises(RetrievalRequestError) as captured:
        PostgresRetrievalAdapter(RecordingRunner()).execute(
            bound,
            (lexical_projection(),),
        )

    assert sentinel not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_non_sequence_backend_response_is_rejected() -> None:
    runner = RecordingRunner({"row": "not a sequence"})
    with pytest.raises(RetrievalIntegrityError, match="invalid shape"):
        PostgresRetrievalAdapter(runner).execute(
            bind_query_template(
                "lexical.search.v1",
                {"query_text": "fixed", "max_results": 1},
            ),
            (lexical_projection(),),
        )
