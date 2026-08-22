"""Provider-neutral model gateway.

``ModelGateway.submit`` resolves a pinned route, verifies capability and
egress policy, redacts protected fields, enforces the context budget,
timeout, and cancellation, admits the call under bounded concurrency, and
validates provider output against the pinned schema. The only output is an
immutable Proposal; a schema failure or any denial raises a typed error and
never returns partial provider output.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from .admission import AdmissionController
from .capacity import CapacityDomainController
from .catalog import ModelCatalog
from .egress import EgressDecision, EgressGate
from .errors import (
    CapabilityDeniedError,
    ContextBudgetExceededError,
    EgressDeniedError,
    ModelGatewayError,
    ModelTimeoutError,
    ProviderUnavailableError,
    RouteDisabledError,
    RouteIntegrityError,
    TransportBindingError,
    TransportProfileNotFoundError,
)
from .models import (
    CancellationToken,
    ModelRouteRecord,
    PolicyEvidence,
    Proposal,
    ProposalRequest,
    Provider,
    ProviderExecutionContext,
    RedactionRecord,
    ResponseEvidence,
    TransportEvidence,
)
from .prompts import PromptStore, estimate_tokens, render_prompt
from .providers import ModelProvider, ProviderCall, ProviderResult
from .registry import RouteRegistry, sha256_text
from .schemas import SchemaRegistry, canonical_json, schema_sha256
from .transport_profiles import TransportProfileStore


class CapabilityGate(Protocol):
    """Verify an opaque, scoped capability before any provider call."""

    def verify(
        self,
        *,
        route: ModelRouteRecord,
        request: ProposalRequest,
        capability_ref: str,
    ) -> bool: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _DirectTarget:
    """Route-pinned target used when no catalog is bound."""

    requested_model: str
    served_model: str
    revision: str
    bucket_id: str | None = None
    bucket_member_id: str | None = None

    @classmethod
    def from_route(cls, route: ModelRouteRecord) -> _DirectTarget:
        deployed = route.model.deployed_name
        return cls(
            requested_model=deployed,
            served_model=deployed,
            revision=route.model.revision,
        )


class ModelGateway:
    """Provider-neutral submit path from typed request to typed proposal."""

    def __init__(
        self,
        *,
        registry: RouteRegistry,
        prompt_store: PromptStore,
        schema_registry: SchemaRegistry,
        egress_gate: EgressGate,
        capability_gate: CapabilityGate,
        providers: Mapping[Provider, ModelProvider],
        admission: AdmissionController | None = None,
        transport_profiles: TransportProfileStore | None = None,
        capacity: CapacityDomainController | None = None,
        catalog: ModelCatalog | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._registry = registry
        self._prompt_store = prompt_store
        self._schema_registry = schema_registry
        self._egress_gate = egress_gate
        self._capability_gate = capability_gate
        self._providers = dict(providers)
        self._admission = admission or AdmissionController()
        self._transport_profiles = transport_profiles
        self._capacity = capacity
        self._catalog = catalog
        self._clock = clock

    @property
    def admission(self) -> AdmissionController:
        return self._admission

    def submit(
        self,
        request: ProposalRequest,
        *,
        capability_ref: str,
        cancel_token: CancellationToken | None = None,
    ) -> Proposal:
        token = CancellationToken(parent=cancel_token)
        token.raise_if_cancelled()
        route = self._registry.route(request.route_id)
        if not route.enabled:
            raise RouteDisabledError(f"route is disabled: {route.route_id}")
        self._verify_capability(route, request, capability_ref)
        decision = self._decide_egress(route, request)
        target = self._resolve_target(route, request)
        redacted_inputs, redactions = self._redact(request)
        prompt = self._render(route, redacted_inputs)
        estimated = estimate_tokens(prompt)
        if estimated > route.context_token_budget:
            raise ContextBudgetExceededError(
                f"rendered prompt needs {estimated} tokens; "
                f"route budget is {route.context_token_budget}"
            )
        schema_model = self._schema_registry.require(route.output_schema_id)
        if schema_sha256(schema_model) != route.output_schema_sha256:
            raise RouteIntegrityError(
                f"output schema hash mismatch for {route.output_schema_id}"
            )
        provider = self._select_provider(route)
        token.raise_if_cancelled()
        context = ProviderExecutionContext(
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            matter_id=request.matter_id,
            purpose=request.purpose,
            classification=request.classification,
            human_approval_ref=request.human_approval_ref,
            source_rights_state=getattr(request, "source_rights_state", None),
        )
        profile = self._resolve_profile(route)
        seed_evidence: TransportEvidence | None = None
        if profile is not None:
            seed_evidence = TransportEvidence(
                transport_profile_id=profile.profile_id,
                transport_kind=profile.kind,
                requested_model=target.requested_model,
                served_model=target.served_model,
                bucket_id=target.bucket_id,
                bucket_member_id=target.bucket_member_id,
                catalog_generation=(
                    self._catalog.generation if self._catalog is not None else None
                ),
            )
        call = ProviderCall(
            route=route,
            prompt=prompt,
            output_schema=self._schema_registry.json_schema(route.output_schema_id),
            cancel_token=token,
            context=context,
            capability_ref=capability_ref,
            transport_profile=profile,
            transport_evidence=seed_evidence,
        )
        started = self._clock()
        with self._admission_scope(route) as admission_evidence:
            result = self._invoke(provider, call, route.timeout_seconds, token)
        result = self._record_admission(result, admission_evidence)
        ended = self._clock()
        token.raise_if_cancelled()
        return self._build_proposal(
            request=request,
            route=route,
            result=result,
            redactions=redactions,
            policy=PolicyEvidence(
                policy_revision=decision.policy_revision,
                classification=request.classification,
                egress_rule=decision.rule,
                human_approval_ref=request.human_approval_ref,
            ),
            started=started,
            ended=ended,
        )

    def _select_provider(self, route: ModelRouteRecord) -> ModelProvider:
        """Bind the provider adapter honoring the transport binding.

        Deployment-only selection: a profile-bound route must use an adapter
        whose transport kind matches the profile, and a profile-less route
        uses the direct provider binding. The logical provider stays pinned
        by the route either way.
        """

        provider = self._providers.get(route.provider)
        if provider is None:
            raise ProviderUnavailableError(
                f"no provider adapter bound for {route.provider}"
            )
        profile = self._resolve_profile(route)
        if profile is not None and provider.transport_kind != profile.kind:
            raise TransportBindingError(
                f"bound {route.provider} adapter carries transport "
                f"{provider.transport_kind}, but the route pins profile "
                f"{profile.profile_id} of kind {profile.kind}"
            )
        return provider

    def _resolve_profile(self, route: ModelRouteRecord):
        if route.transport_profile_id is None:
            return None
        if self._transport_profiles is None:
            raise TransportProfileNotFoundError(
                "route names a transport profile but no profile store is bound"
            )
        return self._transport_profiles.resolve(route)

    def _resolve_target(self, route: ModelRouteRecord, request: ProposalRequest):
        """Resolve the exact served-model target through the catalog."""

        if self._catalog is None:
            return _DirectTarget.from_route(route)
        if route.allowed_bucket_id is not None:
            return self._catalog.resolve_bucket(
                route.allowed_bucket_id,
                workload_class=route.workload_class,
                classification=request.classification,
                human_approval_ref=request.human_approval_ref,
                source_rights_state=getattr(request, "source_rights_state", None),
            )
        alias = route.allowed_served_model_alias
        if alias is not None:
            return self._catalog.resolve_alias(alias)
        return _DirectTarget.from_route(route)

    def _admission_scope(self, route: ModelRouteRecord):
        """Admission honoring the shared capacity domain.

        A route that names a capacity domain draws from the shared envelope so
        direct and SKGateway aliases cannot double-count Qwen slots. Routes
        without a domain keep the original per-provider fail-fast admission.
        """

        if route.capacity_domain_id is not None:
            if self._capacity is None:
                raise ProviderUnavailableError(
                    "route names a capacity domain but no controller is bound"
                )
            return self._capacity.admission(route.capacity_domain_id)
        return self._admission.acquire(
            route.provider, max_concurrent=route.max_concurrent
        )

    @staticmethod
    def _record_admission(
        result: ProviderResult, evidence: Mapping[str, bool] | None
    ) -> ProviderResult:
        """Fold observed admission evidence into the transport record."""

        if not evidence or result.transport is None:
            return result
        update: dict[str, bool] = {}
        if evidence.get("saturation"):
            update["saturation_observed"] = True
        if not update:
            return result
        transport = result.transport.model_copy(update=update)
        return ProviderResult(
            raw_text=result.raw_text,
            model_revision=result.model_revision,
            provider_request_id=result.provider_request_id,
            usage=result.usage,
            transport=transport,
        )

    def _verify_capability(
        self,
        route: ModelRouteRecord,
        request: ProposalRequest,
        capability_ref: str,
    ) -> None:
        if not capability_ref:
            raise CapabilityDeniedError("capability reference is required")
        try:
            allowed = self._capability_gate.verify(
                route=route,
                request=request,
                capability_ref=capability_ref,
            )
        except ModelGatewayError:
            raise
        except Exception as exc:
            raise CapabilityDeniedError(
                "capability verification failed closed"
            ) from exc
        if not allowed:
            raise CapabilityDeniedError("capability verification denied the route")

    def _decide_egress(
        self,
        route: ModelRouteRecord,
        request: ProposalRequest,
    ) -> EgressDecision:
        try:
            decision = self._egress_gate.decide(
                route=route,
                classification=request.classification,
                human_approval_ref=request.human_approval_ref,
            )
        except ModelGatewayError:
            raise
        except Exception as exc:
            raise EgressDeniedError("egress policy failed closed") from exc
        if not decision.allow:
            raise EgressDeniedError(f"egress denied: {decision.reason}")
        return decision

    @staticmethod
    def _redact(
        request: ProposalRequest,
    ) -> tuple[dict[str, str], tuple[RedactionRecord, ...]]:
        inputs = dict(request.prompt_inputs)
        records = []
        for field in sorted(request.protected_fields):
            digest = sha256_text(inputs[field])
            inputs[field] = f"[REDACTED:{digest[:12]}]"
            records.append(RedactionRecord(field=field, value_sha256=digest))
        return inputs, tuple(records)

    def _render(self, route: ModelRouteRecord, inputs: Mapping[str, str]) -> str:
        try:
            template = self._prompt_store.load(route.prompt_template_id)
        except ModelGatewayError:
            raise
        except Exception as exc:
            raise RouteIntegrityError(
                f"prompt template is unavailable: {route.prompt_template_id}"
            ) from exc
        if sha256_text(template) != route.prompt_template_sha256:
            raise RouteIntegrityError(
                f"prompt template hash mismatch for {route.prompt_template_id}"
            )
        return render_prompt(template, inputs)

    @staticmethod
    def _invoke(
        provider: ModelProvider,
        call: ProviderCall,
        timeout_seconds: float,
        token: CancellationToken,
    ) -> ProviderResult:
        outcome: dict[str, Any] = {}

        def runner() -> None:
            try:
                outcome["result"] = provider.complete(call)
            except BaseException as exc:  # re-raised by the caller thread
                outcome["error"] = exc

        thread = threading.Thread(
            target=runner,
            daemon=True,
            name=f"model-gateway-{call.route.route_id}",
        )
        thread.start()
        thread.join(timeout_seconds)
        if thread.is_alive():
            token.cancel()
            raise ModelTimeoutError(
                f"provider exceeded the pinned timeout of {timeout_seconds}s"
            )
        if "error" in outcome:
            raise outcome["error"]
        result = outcome.get("result")
        if not isinstance(result, ProviderResult):
            raise ProviderUnavailableError("provider returned no result and no error")
        return result

    def _build_proposal(
        self,
        *,
        request: ProposalRequest,
        route: ModelRouteRecord,
        result: ProviderResult,
        redactions: tuple[RedactionRecord, ...],
        policy: PolicyEvidence,
        started: datetime,
        ended: datetime,
    ) -> Proposal:
        if result.model_revision != route.model.revision:
            raise RouteIntegrityError(
                "provider model revision does not match the pinned route"
            )
        payload = self._schema_registry.validate(
            route.output_schema_id, result.raw_text
        )
        duration_ms = round((ended - started).total_seconds() * 1000)
        return Proposal(
            proposal_id=f"proposal-{uuid4().hex}",
            request_id=request.request_id,
            route_id=route.route_id,
            registry_revision=self._registry.registry_revision,
            provider=route.provider,
            model=route.model,
            prompt_template_id=route.prompt_template_id,
            prompt_template_sha256=route.prompt_template_sha256,
            output_schema_id=route.output_schema_id,
            output_schema_sha256=route.output_schema_sha256,
            payload=payload,
            payload_sha256=hashlib.sha256(
                canonical_json(payload).encode("utf-8")
            ).hexdigest(),
            redactions=redactions,
            policy=policy,
            evidence=ResponseEvidence(
                provider_request_id=result.provider_request_id,
                started_at=started,
                ended_at=ended,
                duration_ms=duration_ms,
                token_usage=result.usage,
                transport=result.transport,
            ),
        )
