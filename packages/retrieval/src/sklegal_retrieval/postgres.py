"""Bounded PostgreSQL executor for the governed retrieval boundary.

The adapter accepts only already-bound entries from the closed query-template
registry. A trusted runner owns connection and credential resolution. The
adapter supplies that runner with an opaque registry access reference, one
fixed schema-qualified function call, and typed positional parameters.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from pydantic import ValidationError

from .contract import ContractValidationError
from .errors import (
    RetrievalIntegrityError,
    RetrievalRequestError,
    RetrievalUnavailableError,
)
from .models import (
    BackendAggregateRecord,
    BackendHybridRecord,
    BackendScoredRecord,
    BackendUnavailableComponent,
    IncompleteReason,
    ProjectionPins,
    RetrievalAggregateKind,
    RetrievalComponent,
    SourceProvenance,
)
from .query_templates import (
    BoundQueryTemplate,
    QueryTemplateError,
    bind_query_template,
)

type PostgresBackendResult = (
    tuple[BackendScoredRecord | BackendHybridRecord, ...]
    | BackendAggregateRecord
    | BackendUnavailableComponent
)


class PostgresQueryRunner(Protocol):
    """Driver-neutral bridge to one already-authorized read session.

    Implementations resolve the opaque access references without returning a
    credential to this adapter or its caller. They must execute the supplied
    statement with the supplied parameters as a bound operation.
    """

    def fetch_all(
        self,
        *,
        component_access_refs: tuple[str, ...],
        statement: str,
        parameters: tuple[object, ...],
    ) -> Sequence[Mapping[str, object]]: ...


#: The only statement this adapter may issue for exact vector retrieval.
VECTOR_EXACT_STATEMENT = "SELECT * FROM sklegal_retrieval.vector_exact_v1(%s, %s)"

LEXICAL_SEARCH_STATEMENT = "SELECT * FROM sklegal_retrieval.lexical_search_v1(%s, %s)"


@dataclass(frozen=True, slots=True)
class _PostgresCall:
    statement: str
    parameter_names: tuple[str, ...]
    expected_components: tuple[RetrievalComponent, ...]
    aggregate_kind: RetrievalAggregateKind | None = None


_POSTGRES_CALLS: Mapping[str, _PostgresCall] = MappingProxyType(
    {
        "lexical.search.v1": _PostgresCall(
            statement=(LEXICAL_SEARCH_STATEMENT),
            parameter_names=("query_text", "max_results"),
            expected_components=(RetrievalComponent.LEXICAL,),
        ),
        "lexical.count.v1": _PostgresCall(
            statement="SELECT * FROM sklegal_retrieval.lexical_count_v1(%s)",
            parameter_names=("query_text",),
            expected_components=(RetrievalComponent.LEXICAL,),
            aggregate_kind=RetrievalAggregateKind.COUNT,
        ),
        "vector.exact.v1": _PostgresCall(
            statement=VECTOR_EXACT_STATEMENT,
            parameter_names=("query_embedding", "max_results"),
            expected_components=(RetrievalComponent.VECTOR,),
        ),
        "hybrid.rrf.v1": _PostgresCall(
            statement=("SELECT * FROM sklegal_retrieval.hybrid_rrf_v1(%s, %s, %s)"),
            parameter_names=("query_text", "query_embedding", "max_results"),
            expected_components=(
                RetrievalComponent.LEXICAL,
                RetrievalComponent.VECTOR,
            ),
        ),
    }
)

_SCORED_REQUIRED_KEYS = frozenset({"projection", "source", "content", "score"})
_SCORED_ALLOWED_KEYS = _SCORED_REQUIRED_KEYS | {"distance"}
_HYBRID_KEYS = frozenset({"projections", "source", "content", "score"})
_AGGREGATE_KEYS = frozenset({"projection", "aggregate_kind", "value"})


class PostgresRetrievalAdapter:
    """Execute fixed PostgreSQL functions and validate every returned row."""

    def __init__(self, runner: PostgresQueryRunner) -> None:
        self._runner = runner

    def execute(
        self,
        bound: BoundQueryTemplate,
        projections: tuple[ProjectionPins, ...],
    ) -> PostgresBackendResult:
        """Execute one closed template with no alternate-backend fallback."""

        verified = self._verify_bound_template(bound)
        template_id = verified.template.template_id
        if verified.template.component == "graph":
            self._validate_projections(
                projections,
                (RetrievalComponent.GRAPH,),
            )
            return BackendUnavailableComponent(
                component=RetrievalComponent.GRAPH,
                reason=IncompleteReason.BACKEND_UNAVAILABLE,
            )

        try:
            call = _POSTGRES_CALLS[template_id]
        except KeyError:
            raise RetrievalRequestError(
                "query template has no approved PostgreSQL function"
            ) from None

        self._validate_projections(projections, call.expected_components)
        parameters = tuple(verified.parameters[name] for name in call.parameter_names)
        access_refs = tuple(item.component_access_ref for item in projections)
        try:
            rows = self._runner.fetch_all(
                component_access_refs=access_refs,
                statement=call.statement,
                parameters=parameters,
            )
        except Exception:
            raise RetrievalUnavailableError(
                "PostgreSQL retrieval backend is unavailable"
            ) from None

        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
            raise RetrievalIntegrityError(
                "PostgreSQL retrieval response has an invalid shape"
            )
        if call.aggregate_kind is not None:
            return self._parse_aggregate(rows, projections, call.aggregate_kind)
        if template_id == "hybrid.rrf.v1":
            return self._parse_hybrid_rows(rows, projections, verified)
        return self._parse_scored_rows(rows, projections, verified)

    @staticmethod
    def _verify_bound_template(bound: BoundQueryTemplate) -> BoundQueryTemplate:
        if not isinstance(bound, BoundQueryTemplate):
            raise RetrievalRequestError("query template is not a closed binding")
        verified: BoundQueryTemplate | None = None
        try:
            verified = bind_query_template(
                bound.template.template_id,
                bound.parameters,
            )
        except (ContractValidationError, QueryTemplateError):
            pass
        if verified is None:
            raise RetrievalRequestError(
                "query template is not an approved closed binding"
            ) from None
        if bound.template != verified.template or dict(bound.parameters) != dict(
            verified.parameters
        ):
            raise RetrievalRequestError(
                "query template definition does not match its approved pin"
            )
        return verified

    @staticmethod
    def _validate_projections(
        projections: tuple[ProjectionPins, ...],
        expected_components: tuple[RetrievalComponent, ...],
    ) -> None:
        if not isinstance(projections, tuple) or not projections:
            raise RetrievalIntegrityError("selected projection set is invalid")
        if any(not isinstance(item, ProjectionPins) for item in projections):
            raise RetrievalIntegrityError("selected projection set is invalid")
        components = tuple(item.component for item in projections)
        if components != expected_components:
            raise RetrievalIntegrityError(
                "selected projections do not match the closed query template"
            )
        first = projections[0]
        for item in projections:
            if (
                item.scope != first.scope
                or item.projection_set_id != first.projection_set_id
                or item.projection_generation != first.projection_generation
                or item.release_id != first.release_id
            ):
                raise RetrievalIntegrityError(
                    "selected projections are not one atomic projection set"
                )

    @staticmethod
    def _parse_projection(value: object) -> ProjectionPins:
        if isinstance(value, ProjectionPins):
            return value
        if not isinstance(value, Mapping):
            raise RetrievalIntegrityError(
                "PostgreSQL retrieval response failed validation"
            )
        try:
            return ProjectionPins.model_validate(value, strict=False)
        except ValidationError:
            raise RetrievalIntegrityError(
                "PostgreSQL retrieval response failed validation"
            ) from None

    @staticmethod
    def _parse_source(value: object) -> SourceProvenance:
        if isinstance(value, SourceProvenance):
            return value
        if not isinstance(value, Mapping):
            raise RetrievalIntegrityError(
                "PostgreSQL retrieval response failed validation"
            )
        try:
            return SourceProvenance.model_validate(value, strict=False)
        except ValidationError:
            raise RetrievalIntegrityError(
                "PostgreSQL retrieval response failed validation"
            ) from None

    @classmethod
    def _parse_scored_rows(
        cls,
        rows: Sequence[Mapping[str, object]],
        projections: tuple[ProjectionPins, ...],
        bound: BoundQueryTemplate,
    ) -> tuple[BackendScoredRecord, ...]:
        max_results = bound.parameters.get("max_results")
        if type(max_results) is not int or len(rows) > max_results:
            raise RetrievalIntegrityError(
                "PostgreSQL retrieval response exceeded its approved bound"
            )
        selected = set(projections)
        parsed: list[BackendScoredRecord] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise RetrievalIntegrityError(
                    "PostgreSQL retrieval response failed validation"
                )
            keys = frozenset(row)
            if not _SCORED_REQUIRED_KEYS <= keys or not keys <= _SCORED_ALLOWED_KEYS:
                raise RetrievalIntegrityError(
                    "PostgreSQL retrieval response failed validation"
                )
            projection = cls._parse_projection(row["projection"])
            if projection not in selected:
                raise RetrievalIntegrityError(
                    "PostgreSQL retrieval response disagrees with the selected projection"
                )
            source = cls._parse_source(row["source"])
            content = row["content"]
            score = row["score"]
            distance = row.get("distance")
            if not isinstance(content, str):
                raise RetrievalIntegrityError(
                    "PostgreSQL retrieval response failed validation"
                )
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                raise RetrievalIntegrityError(
                    "PostgreSQL retrieval response failed validation"
                )
            if distance is not None and (
                not isinstance(distance, (int, float)) or isinstance(distance, bool)
            ):
                raise RetrievalIntegrityError(
                    "PostgreSQL retrieval response failed validation"
                )
            try:
                parsed.append(
                    BackendScoredRecord(
                        projection=projection,
                        source=source,
                        content=content,
                        score=float(score),
                        distance=float(distance) if distance is not None else None,
                    )
                )
            except ValidationError:
                raise RetrievalIntegrityError(
                    "PostgreSQL retrieval response failed validation"
                ) from None
        record_ids = [item.source.retrieval_record_id for item in parsed]
        if len(record_ids) != len(set(record_ids)):
            raise RetrievalIntegrityError(
                "PostgreSQL retrieval response contains duplicate records"
            )
        if bound.template.template_id == "lexical.search.v1":
            parsed.sort(key=lambda item: (-item.score, item.source.retrieval_record_id))
        elif bound.template.template_id == "vector.exact.v1":
            parsed.sort(
                key=lambda item: (
                    item.distance if item.distance is not None else float("inf"),
                    item.source.retrieval_record_id,
                )
            )
        else:
            raise RetrievalRequestError(
                "query template has no approved scored-row ordering"
            )
        return tuple(parsed)

    @classmethod
    def _parse_aggregate(
        cls,
        rows: Sequence[Mapping[str, object]],
        projections: tuple[ProjectionPins, ...],
        expected_kind: RetrievalAggregateKind,
    ) -> BackendAggregateRecord:
        if len(rows) != 1 or not isinstance(rows[0], Mapping):
            raise RetrievalIntegrityError(
                "PostgreSQL aggregate response failed validation"
            )
        row = rows[0]
        if frozenset(row) != _AGGREGATE_KEYS:
            raise RetrievalIntegrityError(
                "PostgreSQL aggregate response failed validation"
            )
        projection = cls._parse_projection(row["projection"])
        if projection not in set(projections):
            raise RetrievalIntegrityError(
                "PostgreSQL aggregate response disagrees with the selected projection"
            )
        if row["aggregate_kind"] not in (expected_kind, expected_kind.value):
            raise RetrievalIntegrityError(
                "PostgreSQL aggregate response failed validation"
            )
        value = row["value"]
        if isinstance(value, bool) or type(value) is not int:
            raise RetrievalIntegrityError(
                "PostgreSQL aggregate response failed validation"
            )
        try:
            return BackendAggregateRecord(
                projection=projection,
                aggregate_kind=expected_kind,
                value=value,
            )
        except ValidationError:
            raise RetrievalIntegrityError(
                "PostgreSQL aggregate response failed validation"
            ) from None

    @classmethod
    def _parse_hybrid_rows(
        cls,
        rows: Sequence[Mapping[str, object]],
        projections: tuple[ProjectionPins, ...],
        bound: BoundQueryTemplate,
    ) -> tuple[BackendHybridRecord, ...]:
        max_results = bound.parameters.get("max_results")
        if type(max_results) is not int or len(rows) > max_results:
            raise RetrievalIntegrityError(
                "PostgreSQL retrieval response exceeded its approved bound"
            )
        if len(projections) != 2:
            raise RetrievalIntegrityError(
                "PostgreSQL hybrid response failed validation"
            )
        expected = (projections[0], projections[1])
        parsed: list[BackendHybridRecord] = []
        for row in rows:
            if not isinstance(row, Mapping) or frozenset(row) != _HYBRID_KEYS:
                raise RetrievalIntegrityError(
                    "PostgreSQL hybrid response failed validation"
                )
            raw_projections = row["projections"]
            if (
                not isinstance(raw_projections, (tuple, list))
                or len(raw_projections) != 2
            ):
                raise RetrievalIntegrityError(
                    "PostgreSQL hybrid response failed validation"
                )
            row_projections = (
                cls._parse_projection(raw_projections[0]),
                cls._parse_projection(raw_projections[1]),
            )
            if row_projections != expected:
                raise RetrievalIntegrityError(
                    "PostgreSQL hybrid response disagrees with the selected projections"
                )
            source = cls._parse_source(row["source"])
            content = row["content"]
            score = row["score"]
            if not isinstance(content, str) or not isinstance(score, (int, float)):
                raise RetrievalIntegrityError(
                    "PostgreSQL hybrid response failed validation"
                )
            if isinstance(score, bool):
                raise RetrievalIntegrityError(
                    "PostgreSQL hybrid response failed validation"
                )
            try:
                parsed.append(
                    BackendHybridRecord(
                        projections=row_projections,
                        source=source,
                        content=content,
                        score=float(score),
                    )
                )
            except ValueError:
                raise RetrievalIntegrityError(
                    "PostgreSQL hybrid response failed validation"
                ) from None
        record_ids = [item.source.retrieval_record_id for item in parsed]
        if len(record_ids) != len(set(record_ids)):
            raise RetrievalIntegrityError(
                "PostgreSQL hybrid response contains duplicate records"
            )
        parsed.sort(key=lambda item: (-item.score, item.source.retrieval_record_id))
        return tuple(parsed)


__all__ = [
    "LEXICAL_SEARCH_STATEMENT",
    "PostgresBackendResult",
    "PostgresQueryRunner",
    "PostgresRetrievalAdapter",
    "VECTOR_EXACT_STATEMENT",
]
