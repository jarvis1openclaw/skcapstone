"""Governed orchestration for the first local retrieval adapter slice."""

from __future__ import annotations

import math
from typing import Protocol

from pydantic import ValidationError

from .contract import ContractValidationError
from .errors import (
    RetrievalAuthorizationError,
    RetrievalError,
    RetrievalIntegrityError,
    RetrievalRequestError,
    RetrievalUnavailableError,
)
from .models import (
    AuthorizationPins,
    BackendAggregateRecord,
    BackendHybridRecord,
    BackendScoredRecord,
    BackendUnavailableComponent,
    CredentialBindingPins,
    IncompleteComponent,
    ProjectionLag,
    ProjectionPins,
    RankSignal,
    RetrievalAggregateValue,
    RetrievalComponent,
    RetrievalHit,
    RetrievalMode,
    RetrievalProvenance,
    RetrievalRequest,
    RetrievalResult,
    RetrievalScope,
)
from .query_templates import BoundQueryTemplate, QueryTemplateError, bind_query_template


class CurrentAuthorizer(Protocol):
    """Resolve the current policy decision without exposing policy internals."""

    def authorize(self, request: RetrievalRequest) -> AuthorizationPins: ...


class ActiveProjectionRegistry(Protocol):
    """Select a complete active component set through one atomic read."""

    def select_active(
        self,
        scope: RetrievalScope,
        components: tuple[RetrievalComponent, ...],
        *,
        request_id: object | None = None,
    ) -> tuple[ProjectionPins, ...]: ...


class CredentialBindingResolver(Protocol):
    """Resolve the current database-owned broker binding after authorization."""

    def resolve(self, request: RetrievalRequest) -> CredentialBindingPins: ...


class RetrievalExecutor(Protocol):
    """Execute one already-bound closed template against selected projections."""

    def execute(
        self,
        bound: BoundQueryTemplate,
        projections: tuple[ProjectionPins, ...],
    ) -> (
        tuple[BackendScoredRecord | BackendHybridRecord, ...]
        | BackendAggregateRecord
        | BackendUnavailableComponent
    ): ...


class RetrievalOrchestrator:
    """Authorize, select, execute, validate, reauthorize, and trace a query."""

    def __init__(
        self,
        *,
        authorizer: CurrentAuthorizer,
        credential_resolver: CredentialBindingResolver,
        registry: ActiveProjectionRegistry,
        executor: RetrievalExecutor,
    ) -> None:
        self._authorizer = authorizer
        self._credential_resolver = credential_resolver
        self._registry = registry
        self._executor = executor

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """Run one request without any alternate-backend routing."""

        current = self._authorize(request, recheck=False)
        self._resolve_credential_binding(request)
        components = tuple(projection.component for projection in request.projections)
        selected = self._select_active(request, components)
        self._validate_selected(request, current, selected)
        bound = self._bind_template(request)
        execution = self._execute(request, bound, selected)

        if isinstance(execution, BackendUnavailableComponent):
            return self._unavailable_result(request, current, selected, execution)

        if isinstance(execution, BackendAggregateRecord):
            return self._aggregate_result(request, current, selected, execution)

        self._validate_execution(request, current, selected, execution)
        self._authorize(request, recheck=True)
        hits, provenance = self._build_hits(request, current, selected, execution)
        first = selected[0]
        result: RetrievalResult | None = None
        try:
            result = RetrievalResult(
                request_sha256=request.canonical_sha256(),
                provenance=provenance,
                scope=request.scope,
                projection_set_id=first.projection_set_id,
                projection_generation=first.projection_generation,
                release_id=first.release_id,
                policy_revision=current.policy_revision,
                rights_revision=current.rights_revision,
                hits=hits,
            )
        except ValidationError:
            pass
        if result is None:
            raise RetrievalIntegrityError(
                "validated backend rows did not form one response",
                request_id=request.request_id,
            ) from None
        return result

    def _authorize(
        self,
        request: RetrievalRequest,
        *,
        recheck: bool,
    ) -> AuthorizationPins:
        current: AuthorizationPins | None = None
        failure_message: str | None = None
        try:
            current = self._authorizer.authorize(request)
        except RetrievalAuthorizationError:
            failure_message = "current retrieval authorization denied"
        except Exception:
            failure_message = "current retrieval authorization is unavailable"
        if failure_message is not None:
            raise RetrievalAuthorizationError(
                failure_message,
                request_id=request.request_id,
            ) from None

        if not isinstance(current, AuthorizationPins):
            raise RetrievalAuthorizationError(
                "authorizer returned no typed current decision",
                request_id=request.request_id,
            )
        if current != request.authorization:
            error_type = (
                RetrievalIntegrityError if recheck else RetrievalAuthorizationError
            )
            raise error_type(
                "authorization revisions changed during retrieval",
                request_id=request.request_id,
            )
        return current

    def _resolve_credential_binding(
        self,
        request: RetrievalRequest,
    ) -> CredentialBindingPins:
        binding: CredentialBindingPins | None = None
        try:
            binding = self._credential_resolver.resolve(request)
        except RetrievalAuthorizationError:
            pass
        except Exception:
            pass
        if not isinstance(binding, CredentialBindingPins):
            raise RetrievalAuthorizationError(
                "current retrieval credential binding is unavailable",
                request_id=request.request_id,
            ) from None
        if binding != request.credential_binding:
            raise RetrievalAuthorizationError(
                "current retrieval credential binding does not match the request",
                request_id=request.request_id,
            )
        return binding

    def _select_active(
        self,
        request: RetrievalRequest,
        components: tuple[RetrievalComponent, ...],
    ) -> tuple[ProjectionPins, ...]:
        selected: tuple[ProjectionPins, ...] | None = None
        try:
            selected = self._registry.select_active(
                request.scope,
                components,
                request_id=request.request_id,
            )
        except RetrievalError:
            pass
        except Exception:
            pass
        if selected is None:
            raise RetrievalUnavailableError(
                "active projection registry is unavailable",
                request_id=request.request_id,
            ) from None
        if not isinstance(selected, tuple):
            raise RetrievalIntegrityError(
                "active projection selection was not atomic",
                request_id=request.request_id,
            )
        return selected

    def _validate_selected(
        self,
        request: RetrievalRequest,
        current: AuthorizationPins,
        selected: tuple[ProjectionPins, ...],
    ) -> None:
        if len(selected) != len(request.projections):
            raise RetrievalUnavailableError(
                "active projection set is incomplete",
                request_id=request.request_id,
            )
        if any(not isinstance(projection, ProjectionPins) for projection in selected):
            raise RetrievalIntegrityError(
                "registry returned an untyped projection",
                request_id=request.request_id,
            )

        first = selected[0]
        for expected, projection in zip(request.projections, selected, strict=True):
            if projection.component is not expected.component:
                raise RetrievalIntegrityError(
                    "registry component selection changed",
                    request_id=request.request_id,
                )
            if projection.scope != request.scope:
                raise RetrievalIntegrityError(
                    "registry returned a wrong-scope projection",
                    request_id=request.request_id,
                )
            if (
                projection.projection_set_id != first.projection_set_id
                or projection.projection_generation != first.projection_generation
                or projection.release_id != first.release_id
            ):
                raise RetrievalIntegrityError(
                    "registry returned a mixed active projection set",
                    request_id=request.request_id,
                )
            if projection != expected:
                raise RetrievalIntegrityError(
                    "requested projection pins are stale or mismatched",
                    request_id=request.request_id,
                )
            if projection.backend_watermark < current.required_core_watermark:
                raise RetrievalUnavailableError(
                    "required core watermark is not visible",
                    request_id=request.request_id,
                )

        binding = request.credential_binding
        if (
            binding.projection_set_id != first.projection_set_id
            or binding.projection_generation != first.projection_generation
            or binding.policy_revision != current.policy_revision
            or binding.rights_revision != current.rights_revision
        ):
            raise RetrievalAuthorizationError(
                "credential binding is stale for the active projection",
                request_id=request.request_id,
            )

    def _bind_template(self, request: RetrievalRequest) -> BoundQueryTemplate:
        bound: BoundQueryTemplate | None = None
        try:
            bound = bind_query_template(
                request.template.query_template_id.value,
                request.parameters.model_dump(mode="python"),
            )
        except (ContractValidationError, QueryTemplateError):
            pass
        if bound is None:
            raise RetrievalRequestError(
                "closed query-template binding failed",
                request_id=request.request_id,
            ) from None

        template = bound.template
        if (
            template.template_id != request.template.query_template_id.value
            or str(template.version) != request.template.query_template_version
            or template.definition_sha256 != request.template.query_template_sha256
        ):
            raise RetrievalIntegrityError(
                "query-template pins do not match the closed registry",
                request_id=request.request_id,
            )
        return bound

    def _execute(
        self,
        request: RetrievalRequest,
        bound: BoundQueryTemplate,
        selected: tuple[ProjectionPins, ...],
    ) -> (
        tuple[BackendScoredRecord | BackendHybridRecord, ...]
        | BackendAggregateRecord
        | BackendUnavailableComponent
    ):
        execution: (
            tuple[BackendScoredRecord | BackendHybridRecord, ...]
            | BackendAggregateRecord
            | BackendUnavailableComponent
            | None
        ) = None
        try:
            execution = self._executor.execute(bound, selected)
        except RetrievalError:
            pass
        except Exception:
            pass
        if execution is None:
            raise RetrievalUnavailableError(
                "selected retrieval backend is unavailable",
                request_id=request.request_id,
            ) from None
        if not isinstance(
            execution,
            (tuple, BackendAggregateRecord, BackendUnavailableComponent),
        ):
            raise RetrievalIntegrityError(
                "backend returned an invalid response shape",
                request_id=request.request_id,
            )
        return execution

    def _unavailable_result(
        self,
        request: RetrievalRequest,
        current: AuthorizationPins,
        selected: tuple[ProjectionPins, ...],
        unavailable: BackendUnavailableComponent,
    ) -> RetrievalResult:
        if unavailable.component not in request.optional_components:
            raise RetrievalUnavailableError(
                "requested component is unavailable",
                request_id=request.request_id,
            )
        if unavailable.component not in {
            projection.component for projection in selected
        }:
            raise RetrievalIntegrityError(
                "backend unavailable signal named an unselected component",
                request_id=request.request_id,
            )
        self._authorize(request, recheck=True)
        first = selected[0]
        provenance = self._make_provenance(
            request,
            current,
            selected,
            source_ids=(),
            source_hashes=(),
            rank_signal=self._rank_signal(request),
        )
        return RetrievalResult(
            request_sha256=request.canonical_sha256(),
            provenance=provenance,
            scope=request.scope,
            projection_set_id=first.projection_set_id,
            projection_generation=first.projection_generation,
            release_id=first.release_id,
            policy_revision=current.policy_revision,
            rights_revision=current.rights_revision,
            hits=(),
            incomplete_components=(
                IncompleteComponent(
                    component=unavailable.component,
                    reason=unavailable.reason,
                ),
            ),
        )

    def _aggregate_result(
        self,
        request: RetrievalRequest,
        current: AuthorizationPins,
        selected: tuple[ProjectionPins, ...],
        aggregate: BackendAggregateRecord,
    ) -> RetrievalResult:
        expected = next(
            (
                projection
                for projection in selected
                if projection.component is aggregate.projection.component
            ),
            None,
        )
        if expected is None or aggregate.projection != expected:
            raise RetrievalIntegrityError(
                "one wrong-scope aggregate rejects the entire response",
                request_id=request.request_id,
            )
        if (
            aggregate.projection.scope != request.scope
            or aggregate.projection.release_id != selected[0].release_id
            or aggregate.projection.projection_generation
            != selected[0].projection_generation
            or aggregate.projection.backend_watermark < current.required_core_watermark
        ):
            raise RetrievalIntegrityError(
                "one stale aggregate rejects the entire response",
                request_id=request.request_id,
            )

        self._authorize(request, recheck=True)
        result: RetrievalResult | None = None
        try:
            trace = RetrievalProvenance(
                projections=selected,
                authorization=current,
                credential_binding=request.credential_binding,
                template=request.template,
                retrieval_adapter_version=request.retrieval_adapter_version,
                structured_filter_sha256=request.structured_filter_sha256,
                source_ids=(),
                source_hashes=(),
                rank_path=(RankSignal.SCOPE_AGGREGATE,),
                projection_lag=ProjectionLag(lag_events=0, lag_seconds=0.0),
            )
            value = RetrievalAggregateValue(
                provenance=trace,
                aggregate_kind=aggregate.aggregate_kind,
                value=aggregate.value,
            )
            first = selected[0]
            result = RetrievalResult(
                request_sha256=request.canonical_sha256(),
                provenance=trace,
                scope=request.scope,
                projection_set_id=first.projection_set_id,
                projection_generation=first.projection_generation,
                release_id=first.release_id,
                policy_revision=current.policy_revision,
                rights_revision=current.rights_revision,
                hits=(),
                aggregates=(value,),
            )
        except ValidationError:
            pass
        if result is None:
            raise RetrievalIntegrityError(
                "backend aggregate failed trace validation",
                request_id=request.request_id,
            ) from None
        return result

    def _validate_execution(
        self,
        request: RetrievalRequest,
        current: AuthorizationPins,
        selected: tuple[ProjectionPins, ...],
        execution: tuple[BackendScoredRecord | BackendHybridRecord, ...],
    ) -> None:
        hybrid = request.retrieval_mode is RetrievalMode.HYBRID_RRF
        selected_by_component = {
            projection.component: projection for projection in selected
        }
        record_ids: set[str] = set()
        for item in execution:
            row_projections: tuple[ProjectionPins, ...]
            if hybrid:
                if (
                    not isinstance(item, BackendHybridRecord)
                    or item.projections != selected
                ):
                    raise RetrievalIntegrityError(
                        "one incomplete or stale hybrid row rejects the entire response",
                        request_id=request.request_id,
                    )
                row_projections = item.projections
            else:
                if not isinstance(item, BackendScoredRecord):
                    raise RetrievalIntegrityError(
                        "backend returned an untyped row",
                        request_id=request.request_id,
                    )
                projection = item.projection
                expected = selected_by_component.get(projection.component)
                if expected is None or projection != expected:
                    raise RetrievalIntegrityError(
                        "one wrong-scope or stale row rejects the entire response",
                        request_id=request.request_id,
                    )
                row_projections = (projection,)
            if any(
                projection.scope != request.scope
                or projection.release_id != selected[0].release_id
                or projection.projection_generation != selected[0].projection_generation
                or projection.backend_watermark < current.required_core_watermark
                for projection in row_projections
            ):
                raise RetrievalIntegrityError(
                    "one mixed-scope release or watermark rejects the entire response",
                    request_id=request.request_id,
                )
            record_id = item.source.retrieval_record_id
            if record_id in record_ids:
                raise RetrievalIntegrityError(
                    "duplicate retrieval records make ranking ambiguous",
                    request_id=request.request_id,
                )
            record_ids.add(record_id)
            if not math.isfinite(item.score):
                raise RetrievalIntegrityError(
                    "backend returned a non-finite score",
                    request_id=request.request_id,
                )

    def _build_hits(
        self,
        request: RetrievalRequest,
        current: AuthorizationPins,
        selected: tuple[ProjectionPins, ...],
        execution: tuple[BackendScoredRecord | BackendHybridRecord, ...],
    ) -> tuple[tuple[RetrievalHit, ...], RetrievalProvenance]:
        rank_signal = self._rank_signal(request)
        hits: list[RetrievalHit] = []
        failed = False
        try:
            source_pins: dict[str, str] = {}
            for item in execution:
                source = item.source
                prior_hash = source_pins.setdefault(
                    source.source_id, source.source_sha256
                )
                if prior_hash != source.source_sha256:
                    raise ValueError("source identifiers have conflicting hashes")
            trace = self._make_provenance(
                request,
                current,
                selected,
                source_ids=tuple(source_pins),
                source_hashes=tuple(source_pins.values()),
                rank_signal=rank_signal,
            )
            for rank, item in enumerate(execution, start=1):
                source = item.source
                hits.append(
                    RetrievalHit(
                        provenance=trace,
                        source=source,
                        rank=rank,
                        score=item.score,
                        distance=(
                            item.distance
                            if isinstance(item, BackendScoredRecord)
                            and request.retrieval_mode is RetrievalMode.VECTOR_EXACT
                            else None
                        ),
                        content=item.content,
                    )
                )
        except (ValidationError, KeyError):
            failed = True
        except ValueError:
            failed = True
        if failed:
            raise RetrievalIntegrityError(
                "backend content failed trace validation",
                request_id=request.request_id,
            ) from None
        return tuple(hits), trace

    def _make_provenance(
        self,
        request: RetrievalRequest,
        current: AuthorizationPins,
        selected: tuple[ProjectionPins, ...],
        *,
        source_ids: tuple[str, ...],
        source_hashes: tuple[str, ...],
        rank_signal: RankSignal,
    ) -> RetrievalProvenance:
        return RetrievalProvenance(
            projections=selected,
            authorization=current,
            credential_binding=request.credential_binding,
            template=request.template,
            retrieval_adapter_version=request.retrieval_adapter_version,
            structured_filter_sha256=request.structured_filter_sha256,
            source_ids=source_ids,
            source_hashes=source_hashes,
            rank_path=(rank_signal,),
            projection_lag=ProjectionLag(lag_events=0, lag_seconds=0.0),
        )

    @staticmethod
    def _rank_signal(request: RetrievalRequest) -> RankSignal:
        return {
            RetrievalMode.LEXICAL: RankSignal.LEXICAL_RANK,
            RetrievalMode.VECTOR_EXACT: RankSignal.VECTOR_DISTANCE,
            RetrievalMode.HYBRID_RRF: RankSignal.HYBRID_RRF,
            RetrievalMode.GRAPH: RankSignal.GRAPH_TRAVERSAL,
        }[request.retrieval_mode]


__all__ = [
    "ActiveProjectionRegistry",
    "CredentialBindingResolver",
    "CurrentAuthorizer",
    "RetrievalExecutor",
    "RetrievalOrchestrator",
]
