"""Qualification tests for the idempotent outbox projector and graph manifest."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sklegal_retrieval.errors import (
    RetrievalIntegrityError,
    RetrievalUnavailableError,
)
from sklegal_retrieval.fake import FakeProjectionSink
from sklegal_retrieval.models import RetrievalComponent
from sklegal_retrieval.projector import (
    GraphEntityManifestEntry,
    GraphRelationshipManifestEntry,
    LegacyShadowSnapshot,
    OutboxEvent,
    OutboxProjector,
    ProjectedRow,
    project_rows_digest,
    scope_sha256,
    shadow_parity,
    validate_graph_relationship,
)

from tests.support.retrieval_pins import (
    HASH_A,
    HASH_B,
    HASH_C,
    OTHER_MATTER_ID,
    OTHER_TENANT_ID,
    make_projection,
    make_scope,
    make_source,
)


def _row(
    record_id: str,
    *,
    component: RetrievalComponent = RetrievalComponent.LEXICAL,
    content: str | None = None,
    embedding: tuple[float, ...] | None = None,
) -> ProjectedRow:
    projection = make_projection(component, make_scope())
    return ProjectedRow(
        projection=projection,
        source=make_source(record_id),
        content=content or f"synthetic content {record_id}",
        embedding=embedding,
    )


def _event(sequence: int, row: ProjectedRow, *, key: str | None = None) -> OutboxEvent:
    return OutboxEvent(
        event_sequence=sequence,
        idempotency_key=key or f"event-{sequence}",
        row=row,
    )


def _events() -> tuple[OutboxEvent, ...]:
    return (
        _event(1, _row("record-1")),
        _event(2, _row("record-2")),
        _event(
            3,
            _row(
                "record-3",
                component=RetrievalComponent.VECTOR,
                embedding=(0.25, 0.75),
            ),
        ),
    )


def test_replay_applies_events_and_advances_the_watermark() -> None:
    sink = FakeProjectionSink()
    watermark = OutboxProjector().replay(_events(), sink)
    assert watermark == 3
    assert len(sink.rows()) == 3


def test_replay_is_idempotent_under_duplicate_delivery() -> None:
    events = _events()
    sink = FakeProjectionSink()
    projector = OutboxProjector()
    projector.replay(events, sink)
    first_digest = sink.state_digest()
    projector.replay(events, sink)
    projector.replay(events[-1:], sink)
    assert sink.state_digest() == first_digest
    assert len(sink.rows()) == 3


def test_rebuild_from_the_pinned_log_equals_incremental_state() -> None:
    events = _events()
    projector = OutboxProjector()
    incremental = FakeProjectionSink()
    projector.replay(events, incremental)
    rebuilt = projector.rebuild(events, FakeProjectionSink)
    assert isinstance(rebuilt, FakeProjectionSink)
    assert rebuilt.state_digest() == incremental.state_digest()


def test_out_of_order_or_regressing_sequences_are_rejected() -> None:
    sink = FakeProjectionSink()
    with pytest.raises(RetrievalIntegrityError):
        OutboxProjector().replay((_event(2, _row("a")), _event(2, _row("b"))), sink)
    with pytest.raises(RetrievalIntegrityError):
        OutboxProjector().replay((_event(3, _row("a")), _event(1, _row("b"))), sink)


def test_conflicting_redelivery_of_one_record_is_rejected() -> None:
    sink = FakeProjectionSink()
    projector = OutboxProjector()
    projector.replay((_event(1, _row("record-1"), key="shared-key"),), sink)
    with pytest.raises(RetrievalUnavailableError):
        projector.replay(
            (
                _event(
                    2,
                    _row("record-1", content="tampered content"),
                    key="other-key",
                ),
            ),
            sink,
        )


def test_sink_outage_fails_closed_without_partial_visibility() -> None:
    sink = FakeProjectionSink(available=False)
    with pytest.raises(RetrievalUnavailableError):
        OutboxProjector().replay(_events(), sink)
    assert sink.rows() == ()


def test_projected_rows_enforce_component_shape_and_finite_embeddings() -> None:
    with pytest.raises(ValidationError):
        _row("record-9", component=RetrievalComponent.VECTOR)
    with pytest.raises(ValidationError):
        _row("record-9", component=RetrievalComponent.VECTOR, embedding=(0.1,))
    with pytest.raises(ValidationError):
        _row(
            "record-9",
            component=RetrievalComponent.VECTOR,
            embedding=(0.1, float("nan")),
        )
    with pytest.raises(ValidationError):
        _row("record-9", embedding=(0.1, 0.2))


def _entity(entity_id: str, *, scope: object = None) -> GraphEntityManifestEntry:
    actual = scope if scope is not None else make_scope()
    return GraphEntityManifestEntry(
        graph_entity_id=entity_id,
        scope=actual,  # type: ignore[arg-type]
        scope_digest=scope_sha256(actual),  # type: ignore[arg-type]
        content_sha256=HASH_A,
    )


def _relationship(
    start: GraphEntityManifestEntry,
    end: GraphEntityManifestEntry,
    *,
    start_digest: str | None = None,
    end_digest: str | None = None,
) -> GraphRelationshipManifestEntry:
    return GraphRelationshipManifestEntry(
        relationship_id="relationship-1",
        relationship_type="supports",
        scope=make_scope(),
        start_entity_id=start.graph_entity_id,
        end_entity_id=end.graph_entity_id,
        start_endpoint_scope_sha256=start_digest or start.scope_digest,
        end_endpoint_scope_sha256=end_digest or end.scope_digest,
        content_sha256=HASH_B,
    )


def test_graph_relationship_with_matching_endpoints_is_accepted() -> None:
    start = _entity("entity-1")
    end = _entity("entity-2")
    relationship = _relationship(start, end)
    entities = {"entity-1": start, "entity-2": end}
    validate_graph_relationship(relationship, entities)


def test_graph_relationship_endpoint_digest_mismatch_rejects_the_build() -> None:
    start = _entity("entity-1")
    end = _entity("entity-2")
    relationship = _relationship(start, end, end_digest=HASH_C)
    entities = {"entity-1": start, "entity-2": end}
    with pytest.raises(RetrievalIntegrityError):
        validate_graph_relationship(relationship, entities)


def test_graph_cross_scope_edges_are_prohibited() -> None:
    start = _entity("entity-1")
    cross_matter = _entity("entity-2", scope=make_scope(matter_id=OTHER_MATTER_ID))
    entities = {"entity-1": start, "entity-2": cross_matter}
    relationship = GraphRelationshipManifestEntry(
        relationship_id="relationship-1",
        relationship_type="supports",
        scope=make_scope(),
        start_entity_id="entity-1",
        end_entity_id="entity-2",
        start_endpoint_scope_sha256=start.scope_digest,
        end_endpoint_scope_sha256=cross_matter.scope_digest,
        content_sha256=HASH_B,
    )
    with pytest.raises(RetrievalIntegrityError):
        validate_graph_relationship(relationship, entities)
    cross_tenant = _entity("entity-3", scope=make_scope(tenant_id=OTHER_TENANT_ID))
    relationship = GraphRelationshipManifestEntry(
        relationship_id="relationship-2",
        relationship_type="supports",
        scope=make_scope(),
        start_entity_id="entity-1",
        end_entity_id="entity-3",
        start_endpoint_scope_sha256=start.scope_digest,
        end_endpoint_scope_sha256=cross_tenant.scope_digest,
        content_sha256=HASH_B,
    )
    with pytest.raises(RetrievalIntegrityError):
        validate_graph_relationship(
            relationship, {"entity-1": start, "entity-3": cross_tenant}
        )


def test_graph_relationship_with_a_missing_endpoint_is_rejected() -> None:
    start = _entity("entity-1")
    relationship = GraphRelationshipManifestEntry(
        relationship_id="relationship-1",
        relationship_type="supports",
        scope=make_scope(),
        start_entity_id="entity-1",
        end_entity_id="entity-missing",
        start_endpoint_scope_sha256=start.scope_digest,
        end_endpoint_scope_sha256=start.scope_digest,
        content_sha256=HASH_B,
    )
    with pytest.raises(RetrievalIntegrityError):
        validate_graph_relationship(relationship, {"entity-1": start})


def test_entity_scope_digest_must_match_its_scope() -> None:
    with pytest.raises(ValidationError):
        GraphEntityManifestEntry(
            graph_entity_id="entity-1",
            scope=make_scope(),
            scope_digest=HASH_A,
            content_sha256=HASH_A,
        )


def test_scope_digest_is_deterministic_and_scope_sensitive() -> None:
    assert scope_sha256(make_scope()) == scope_sha256(make_scope())
    assert scope_sha256(make_scope()) != scope_sha256(
        make_scope(matter_id=OTHER_MATTER_ID)
    )
    assert scope_sha256(make_scope()) != scope_sha256(
        make_scope(tenant_id=OTHER_TENANT_ID)
    )


def test_shadow_parity_preserves_provenance_and_reports_mismatches() -> None:
    events = _events()
    sink = OutboxProjector().rebuild(events, FakeProjectionSink)
    rows = sink.rows()
    snapshot = LegacyShadowSnapshot(
        alias="qdrant-legacy-collection",
        record_count=len(rows),
        content_digest=project_rows_digest(rows),
        watermark=10,
        scope_digest=scope_sha256(make_scope()),
    )
    assert shadow_parity(snapshot, rows) == ()

    mismatched = LegacyShadowSnapshot(
        alias="qdrant-legacy-collection",
        record_count=len(rows) + 1,
        content_digest=HASH_C,
        watermark=9,
        scope_digest=HASH_A,
    )
    assert shadow_parity(mismatched, rows) == (
        "record_count",
        "content_digest",
        "watermark",
        "scope_digest",
    )


def test_row_digest_is_deterministic_and_order_independent() -> None:
    rows = tuple(event.row for event in _events())
    assert project_rows_digest(rows) == project_rows_digest(tuple(reversed(rows)))
    assert project_rows_digest(rows) != project_rows_digest(rows[:2])
