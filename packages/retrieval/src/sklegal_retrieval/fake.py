"""Pure in-memory retrieval fakes for the first governed adapter slice.

The fakes model only the approved local PostgreSQL routes. They do not open a
socket, resolve a credential, or consult any legacy retrieval service.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from threading import RLock

from .errors import RetrievalAuthorizationError, RetrievalUnavailableError
from .models import (
    AuthorizationPins,
    BackendAggregateRecord,
    BackendHybridRecord,
    BackendScoredRecord,
    BackendUnavailableComponent,
    CredentialBindingPins,
    DistanceMetric,
    IncompleteReason,
    ProjectionPins,
    RetrievalAggregateKind,
    RetrievalComponent,
    RetrievalRequest,
    RetrievalScope,
    SourceProvenance,
)
from .query_templates import BoundQueryTemplate

_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+")


def _scope_key(scope: RetrievalScope) -> tuple[object, str, object | None]:
    return (scope.tenant_id, scope.scope_kind.value, scope.matter_id)


@dataclass(frozen=True, slots=True)
class FakeRetrievalRecord:
    """One synthetic derived row with enough data for both approved paths."""

    projection: ProjectionPins
    source: SourceProvenance
    content: str
    searchable_text: str | None = None
    embedding: tuple[float, ...] | None = None

    def lexical_text(self) -> str:
        return (
            self.searchable_text if self.searchable_text is not None else self.content
        )


AGE_UNAVAILABLE = BackendUnavailableComponent(
    component=RetrievalComponent.GRAPH,
    reason=IncompleteReason.BACKEND_UNAVAILABLE,
)


class FakeAuthorizer:
    """Return current immutable authorization pins or fail closed.

    ``decisions`` may contain two entries to simulate a revision change between
    the pre-query authorization and the mandatory post-query recheck.
    """

    def __init__(
        self,
        decisions: AuthorizationPins | None | Sequence[AuthorizationPins | None],
        *,
        event_log: list[str] | None = None,
    ) -> None:
        if isinstance(decisions, Sequence):
            self._decisions = tuple(decisions)
        else:
            self._decisions = (decisions,)
        if not self._decisions:
            raise ValueError("at least one authorization decision is required")
        self._event_log = event_log
        self.call_count = 0

    def authorize(self, request: RetrievalRequest) -> AuthorizationPins:
        if self._event_log is not None:
            self._event_log.append("authorization")
        index = min(self.call_count, len(self._decisions) - 1)
        self.call_count += 1
        decision = self._decisions[index]
        if decision is None:
            raise RetrievalAuthorizationError(
                "current policy denied retrieval",
                request_id=request.request_id,
            )
        return decision


class FakeCredentialBindingResolver:
    """Resolve the current broker binding without exposing a raw credential."""

    def __init__(
        self,
        binding: CredentialBindingPins | None,
        *,
        event_log: list[str] | None = None,
    ) -> None:
        self._binding = binding
        self._event_log = event_log
        self.call_count = 0

    def resolve(self, request: RetrievalRequest) -> CredentialBindingPins:
        if self._event_log is not None:
            self._event_log.append("credential_binding")
        self.call_count += 1
        if self._binding is None:
            raise RetrievalAuthorizationError(
                "current retrieval credential binding is unavailable",
                request_id=request.request_id,
            )
        return self._binding


class FakeActiveProjectionRegistry:
    """Expose an immutable active manifest through one atomic selection call."""

    def __init__(
        self,
        projections: Iterable[ProjectionPins],
        *,
        event_log: list[str] | None = None,
    ) -> None:
        self._projections = tuple(projections)
        self._event_log = event_log
        self._lock = RLock()
        self.selection_count = 0

    def select_active(
        self,
        scope: RetrievalScope,
        components: tuple[RetrievalComponent, ...],
        *,
        request_id: object | None = None,
    ) -> tuple[ProjectionPins, ...]:
        """Select all requested components from one immutable snapshot."""

        del request_id
        if self._event_log is not None:
            self._event_log.append("registry")
        with self._lock:
            self.selection_count += 1
            snapshot = self._projections

        scope_key = _scope_key(scope)
        selected: list[ProjectionPins] = []
        for component in components:
            matches = tuple(
                projection
                for projection in snapshot
                if _scope_key(projection.scope) == scope_key
                and projection.component is component
            )
            if len(matches) != 1:
                raise RetrievalUnavailableError(
                    "active projection selection is unavailable"
                )
            selected.append(matches[0])
        return tuple(selected)


class FakeRetrievalExecutor:
    """Execute fixed lexical and exact-vector operations over synthetic rows."""

    supported_components = frozenset(
        {
            RetrievalComponent.LEXICAL,
            RetrievalComponent.VECTOR,
            RetrievalComponent.GRAPH,
        }
    )

    def __init__(
        self,
        records: Iterable[FakeRetrievalRecord] = (),
        *,
        graph_result: BackendUnavailableComponent = AGE_UNAVAILABLE,
        unavailable_components: Iterable[RetrievalComponent] = (),
        leak_partition_rows: bool = False,
        event_log: list[str] | None = None,
    ) -> None:
        self._records = tuple(records)
        self._graph_result = graph_result
        self._unavailable_components = frozenset(unavailable_components)
        self._leak_partition_rows = leak_partition_rows
        self._event_log = event_log
        self.call_count = 0

    def execute(
        self,
        bound: BoundQueryTemplate,
        projections: tuple[ProjectionPins, ...],
    ) -> (
        tuple[BackendScoredRecord | BackendHybridRecord, ...]
        | BackendAggregateRecord
        | BackendUnavailableComponent
    ):
        if bound.template.component == "hybrid":
            if self._event_log is not None:
                self._event_log.extend(("backend:lexical", "backend:vector"))
            self.call_count += 1
            if {
                RetrievalComponent.LEXICAL,
                RetrievalComponent.VECTOR,
            } & self._unavailable_components:
                raise RetrievalUnavailableError(
                    "selected retrieval backend is unavailable"
                )
            return self._execute_hybrid(bound, projections)

        component = RetrievalComponent(bound.template.component)
        if self._event_log is not None:
            self._event_log.append(f"backend:{component.value}")
        self.call_count += 1

        if component in self._unavailable_components:
            raise RetrievalUnavailableError("selected retrieval backend is unavailable")
        if component is RetrievalComponent.GRAPH:
            return self._graph_result
        if component is RetrievalComponent.LEXICAL:
            return self._execute_lexical(bound, projections[0])
        if component is RetrievalComponent.VECTOR:
            return self._execute_vector(bound, projections[0])
        raise RetrievalUnavailableError("retrieval component is not implemented")

    def _candidate_records(
        self, projection: ProjectionPins
    ) -> tuple[FakeRetrievalRecord, ...]:
        candidates = tuple(
            record
            for record in self._records
            if record.projection.component is projection.component
        )
        if self._leak_partition_rows:
            return candidates
        return tuple(
            record
            for record in candidates
            if record.projection.physical_partition_id
            == projection.physical_partition_id
        )

    def _execute_lexical(
        self,
        bound: BoundQueryTemplate,
        projection: ProjectionPins,
    ) -> tuple[BackendScoredRecord, ...] | BackendAggregateRecord:
        query_terms = _tokens(_text_parameter(bound, "query_text"))
        if bound.template.template_id == "lexical.count.v1":
            if self._leak_partition_rows:
                raise RetrievalUnavailableError(
                    "aggregate scope cannot be proven during leak simulation"
                )
            candidates = self._candidate_records(projection)
            if any(record.projection != projection for record in candidates):
                raise RetrievalUnavailableError(
                    "aggregate candidates do not match the selected projection"
                )
            count = sum(
                1
                for record in candidates
                if _lexical_score(query_terms, record.lexical_text()) > 0
            )
            return BackendAggregateRecord(
                projection=projection,
                aggregate_kind=RetrievalAggregateKind.COUNT,
                value=count,
            )
        if bound.template.template_id != "lexical.search.v1":
            raise RetrievalUnavailableError(
                "lexical operation is not implemented in this slice"
            )
        limit = _integer_parameter(bound, "max_results")
        return self._rank_lexical(query_terms, projection, limit)

    def _rank_lexical(
        self,
        query_terms: tuple[str, ...],
        projection: ProjectionPins,
        limit: int,
    ) -> tuple[BackendScoredRecord, ...]:
        scored: list[BackendScoredRecord] = []
        for record in self._candidate_records(projection):
            score = _lexical_score(query_terms, record.lexical_text())
            if score > 0:
                scored.append(
                    BackendScoredRecord(
                        projection=record.projection,
                        source=record.source,
                        content=record.content,
                        score=score,
                    )
                )
        scored.sort(key=lambda item: (-item.score, item.source.retrieval_record_id))
        return tuple(scored[:limit])

    def _execute_vector(
        self,
        bound: BoundQueryTemplate,
        projection: ProjectionPins,
    ) -> tuple[BackendScoredRecord, ...]:
        if bound.template.template_id != "vector.exact.v1" or projection.vector is None:
            raise RetrievalUnavailableError("exact vector operation is unavailable")
        query = _embedding_parameter(bound)
        limit = _integer_parameter(bound, "max_results")
        return self._rank_vector(query, projection, limit)

    def _rank_vector(
        self,
        query: tuple[float, ...],
        projection: ProjectionPins,
        limit: int,
    ) -> tuple[BackendScoredRecord, ...]:
        if projection.vector is None:
            raise RetrievalUnavailableError("exact vector operation is unavailable")
        scored: list[BackendScoredRecord] = []
        for record in self._candidate_records(projection):
            if record.embedding is None or len(record.embedding) != len(query):
                raise RetrievalUnavailableError("stored vector shape is invalid")
            if not all(math.isfinite(value) for value in record.embedding):
                raise RetrievalUnavailableError(
                    "stored vector contains a non-finite value"
                )
            distance = _distance(
                query, record.embedding, projection.vector.distance_metric
            )
            scored.append(
                BackendScoredRecord(
                    projection=record.projection,
                    source=record.source,
                    content=record.content,
                    score=1.0 / (1.0 + distance),
                    distance=distance,
                )
            )
        scored.sort(key=lambda item: (item.distance, item.source.retrieval_record_id))
        return tuple(scored[:limit])

    def _execute_hybrid(
        self,
        bound: BoundQueryTemplate,
        projections: tuple[ProjectionPins, ...],
    ) -> tuple[BackendHybridRecord, ...]:
        if len(projections) != 2:
            raise RetrievalUnavailableError("hybrid projection set is incomplete")
        lexical_projection, vector_projection = projections
        if (
            lexical_projection.component is not RetrievalComponent.LEXICAL
            or vector_projection.component is not RetrievalComponent.VECTOR
        ):
            raise RetrievalUnavailableError("hybrid projection order is invalid")

        limit = _integer_parameter(bound, "max_results")
        lexical = self._rank_lexical(
            _tokens(_text_parameter(bound, "query_text")),
            lexical_projection,
            limit,
        )
        vector = self._rank_vector(
            _embedding_parameter(bound),
            vector_projection,
            limit,
        )
        fused: dict[str, tuple[float, BackendScoredRecord]] = {}
        for ranking in (lexical, vector):
            for rank, item in enumerate(ranking, start=1):
                record_id = item.source.retrieval_record_id
                increment = 1.0 / (60.0 + rank)
                if record_id in fused:
                    score, existing = fused[record_id]
                    if (
                        existing.source != item.source
                        or existing.content != item.content
                    ):
                        raise RetrievalUnavailableError(
                            "hybrid source provenance is inconsistent"
                        )
                    preferred = (
                        existing
                        if existing.projection.component is RetrievalComponent.LEXICAL
                        else item
                    )
                    fused[record_id] = (score + increment, preferred)
                else:
                    fused[record_id] = (increment, item)

        results = [
            BackendHybridRecord(
                projections=(lexical_projection, vector_projection),
                source=item.source,
                content=item.content,
                score=score,
            )
            for score, item in fused.values()
        ]
        results.sort(key=lambda item: (-item.score, item.source.retrieval_record_id))
        return tuple(results[:limit])


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in _WORD_PATTERN.findall(value))


def _lexical_score(query_terms: tuple[str, ...], value: str) -> float:
    frequencies = Counter(_tokens(value))
    return float(sum(frequencies[term] for term in query_terms))


def _text_parameter(bound: BoundQueryTemplate, name: str) -> str:
    value = bound.parameters.get(name)
    if not isinstance(value, str):
        raise RetrievalUnavailableError("bound text parameter is invalid")
    return value


def _integer_parameter(bound: BoundQueryTemplate, name: str) -> int:
    value = bound.parameters.get(name)
    if type(value) is not int:
        raise RetrievalUnavailableError("bound integer parameter is invalid")
    return value


def _embedding_parameter(bound: BoundQueryTemplate) -> tuple[float, ...]:
    value = bound.parameters.get("query_embedding")
    if not isinstance(value, tuple):
        raise RetrievalUnavailableError("bound embedding parameter is invalid")
    if any(type(item) not in (int, float) for item in value):
        raise RetrievalUnavailableError("bound embedding parameter is invalid")
    return tuple(float(item) for item in value)


def _distance(
    left: tuple[float, ...],
    right: tuple[float, ...],
    metric: DistanceMetric,
) -> float:
    if metric is DistanceMetric.L2:
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)))

    dot = sum(a * b for a, b in zip(left, right, strict=True))
    if metric is DistanceMetric.COSINE:
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0.0 or right_norm == 0.0:
            raise RetrievalUnavailableError(
                "cosine distance is undefined for a zero vector"
            )
        cosine = dot / (left_norm * right_norm)
        return max(0.0, min(2.0, 1.0 - cosine))

    if dot >= 0.0:
        exp_negative = math.exp(-dot)
        return exp_negative / (1.0 + exp_negative)
    return 1.0 / (1.0 + math.exp(dot))


__all__ = [
    "AGE_UNAVAILABLE",
    "FakeActiveProjectionRegistry",
    "FakeAuthorizer",
    "FakeCredentialBindingResolver",
    "FakeRetrievalExecutor",
    "FakeRetrievalRecord",
]
