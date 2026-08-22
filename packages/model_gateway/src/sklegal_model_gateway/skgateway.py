"""SKGateway OpenAI-compatible Chat Completions adapter (SKL-S3-10).

The adapter carries the SKLegal request through SKGateway
``/v1/chat/completions`` behind a CapAuth-attributed service identity. It is
a transport only: a successful HTTP response is evidence, never Approval or
a state mutation. Structured output is requested only after the exact
upstream path is qualified, and returned JSON is always validated client
side against the pinned SKLegal schema by the gateway.

Live-path control gate. The upstream production ``routeAndSend`` path is
known to invoke routing, SIEM, and metrics but not every implemented
control. Before any protected SKLegal traffic may traverse SKGateway, the
deployed commit must pass ``SkGatewayLivePathGate``, which checks the exact
live entrypoint for:

- CapAuth identity verification and the ``skgateway.infer`` capability
- SKLegal Tenant, Matter, purpose, classification, and egress decision
- body and system limits plus secret and sensitive-data handling
- tool-budget stripping or rejection appropriate to a model-only route
- rate limits and the qualified shared Qwen capacity domain
- attributable audit with no prompt, source, secret, or capability leak
- deterministic denial when identity, policy, catalog, or audit is absent

A control that exists only in an alternate library path or the test suite
does not satisfy this gate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from sklegal_domain import DataClassification

from .egress import EgressGate
from .errors import (
    CapabilityDeniedError,
    EgressDeniedError,
    ModelGatewayError,
    ProviderContractError,
    ProviderUnavailableError,
    ServedModelAttributionError,
)
from .models import (
    CancellationToken,
    Provider,
    TokenUsage,
    TransportEvidence,
    TransportKind,
)
from .providers import ProviderCall, ProviderResult
from .registry import RouteRegistry
from .transport_profiles import TransportProfileStore

LIVE_PATH_CONTROLS: tuple[str, ...] = (
    "capauth_identity",
    "capauth_capability",
    "sklegal_policy_decision",
    "classification_egress",
    "body_and_system_limits",
    "secret_handling",
    "tool_budget_stripping",
    "rate_limits",
    "shared_capacity_domain",
    "attributable_audit",
    "no_prompt_or_secret_leak",
    "deterministic_denial",
)

CONTROL_GATE_REVISION = "skgateway-live-path-gate/v1"


@dataclass(frozen=True, slots=True)
class SkGatewayRouteAuthorizationEvidence:
    """Revisions proving one trusted gateway route authorization check."""

    route_registry_revision: str
    transport_profile_revision: str
    egress_policy_revision: str


class SkGatewayRoutePolicyVerifier:
    """Verify trusted SKGateway facts against canonical routing controls."""

    def __init__(
        self,
        *,
        routes: RouteRegistry,
        transport_profiles: TransportProfileStore,
        egress: EgressGate,
    ) -> None:
        self._routes = routes
        self._transport_profiles = transport_profiles
        self._egress = egress

    def verify(
        self,
        *,
        service_identity: str,
        capability: str,
        resource: Mapping[str, str],
        context: Mapping[str, str],
    ) -> SkGatewayRouteAuthorizationEvidence:
        if capability != "skgateway.infer":
            raise CapabilityDeniedError("gateway capability is not permitted")
        route_id = resource.get("route_id")
        classification_value = context.get("classification")
        if not route_id or not classification_value:
            raise CapabilityDeniedError(
                "gateway route and classification selectors are required"
            )
        try:
            classification = DataClassification(classification_value)
        except ValueError as exc:
            raise CapabilityDeniedError(
                "gateway classification selector is not permitted"
            ) from exc
        route = self._routes.route(route_id)
        if not route.enabled:
            raise CapabilityDeniedError("gateway route is disabled")
        profile = self._transport_profiles.resolve(route)
        if profile.kind is not TransportKind.SKGATEWAY_CHAT:
            raise CapabilityDeniedError("route is not bound to SKGateway")
        if profile.service_identity != service_identity:
            raise CapabilityDeniedError("gateway service identity is not permitted")
        if profile.capability_scope != capability:
            raise CapabilityDeniedError("gateway capability scope is not permitted")
        decision = self._egress.decide(
            route=route,
            classification=classification,
            human_approval_ref=context.get("human_approval_ref"),
        )
        if not decision.allow:
            raise EgressDeniedError("gateway classification egress is denied")
        return SkGatewayRouteAuthorizationEvidence(
            route_registry_revision=self._routes.registry_revision,
            transport_profile_revision=self._transport_profiles.store_revision,
            egress_policy_revision=decision.policy_revision,
        )


class SkGatewayLivePathReport(Protocol):
    """Qualification report bound to the exact deployed commit."""

    deployed_commit: str
    revision: str

    def enforced(self, control: str) -> bool: ...


class SkGatewayLivePathGate:
    """Fails closed unless every control is proven on the live entrypoint."""

    def __init__(self, report: SkGatewayLivePathReport) -> None:
        self._report = report

    @property
    def deployed_commit(self) -> str:
        return self._report.deployed_commit

    def require_qualified(self) -> None:
        if self._report.revision != CONTROL_GATE_REVISION:
            raise ProviderUnavailableError(
                "skgateway live-path report revision is not the gate revision"
            )
        missing = [
            control
            for control in LIVE_PATH_CONTROLS
            if not self._report.enforced(control)
        ]
        if missing:
            raise ProviderUnavailableError(
                "skgateway live entrypoint is not qualified for protected "
                f"traffic; unproven controls: {', '.join(missing)}"
            )


class SkGatewayChatTransport(Protocol):
    """Chat Completions transport; tests bind fakes, never live calls."""

    def chat_completions(
        self,
        *,
        base_url_reference: str,
        service_identity: str,
        capability_token_ref: str,
        body: Mapping[str, Any],
        timeout_seconds: float,
        cancel_token: CancellationToken,
    ) -> Mapping[str, Any]: ...


class SkGatewayChatProvider:
    """Adapter for SKGateway /v1/chat/completions behind CapAuth."""

    provider = Provider.QWEN_LOCAL
    transport_kind = TransportKind.SKGATEWAY_CHAT

    def __init__(
        self,
        transport: SkGatewayChatTransport,
        *,
        live_path_gate: SkGatewayLivePathGate,
        endpoint_env: str | None = None,
    ) -> None:
        self._transport = transport
        self._live_path_gate = live_path_gate
        self._endpoint_env = endpoint_env

    def complete(self, call: ProviderCall) -> ProviderResult:
        call.cancel_token.raise_if_cancelled()
        profile = call.transport_profile
        if profile is None or profile.kind is not TransportKind.SKGATEWAY_CHAT:
            raise ProviderContractError(
                "an skgateway_chat call requires its pinned transport profile"
            )
        if call.capability_ref is None or not call.capability_ref:
            raise CapabilityDeniedError(
                "an skgateway_chat call requires a capability reference"
            )
        # The live-path gate must pass on the exact deployed commit before
        # any request leaves SKLegal. Deny deterministically otherwise.
        self._live_path_gate.require_qualified()
        route = call.route
        if call.context is None:
            raise ProviderContractError(
                "an skgateway_chat call requires non-protected request context"
            )
        evidence = TransportEvidence(
            transport_profile_id=profile.profile_id,
            transport_kind=profile.kind,
            requested_model=(
                call.transport_evidence.requested_model
                if call.transport_evidence is not None
                else route.allowed_served_model_alias
                or route.allowed_bucket_id
                or route.model.deployed_name
            ),
            bucket_id=route.allowed_bucket_id,
        )
        body: dict[str, Any] = {
            # Submit the logical request alias, not a hidden backend model:
            # the gateway resolves the alias to the exact served model.
            "model": evidence.requested_model,
            "messages": [
                {
                    "role": "user",
                    "content": call.prompt,
                }
            ],
            "max_tokens": route.max_output_tokens,
            # Structured output stays off until the exact upstream path is
            # qualified; validation is always client side against the pin.
            "stream": False,
        }
        if self._endpoint_env is not None and self._endpoint_env != (
            profile.endpoint_reference
        ):
            raise ProviderContractError(
                "resolved endpoint environment does not match the profile pin"
            )
        try:
            payload = self._transport.chat_completions(
                base_url_reference=profile.endpoint_reference,
                service_identity=profile.service_identity or "",
                capability_token_ref=call.capability_ref,
                body=body,
                timeout_seconds=route.timeout_seconds,
                cancel_token=call.cancel_token,
            )
        except ModelGatewayError:
            raise
        except Exception as exc:
            raise ProviderUnavailableError(
                "skgateway chat transport failed"
            ) from exc
        return self._extract(call, payload, evidence)

    @staticmethod
    def _extract(
        call: ProviderCall,
        payload: Mapping[str, Any],
        evidence: TransportEvidence,
    ) -> ProviderResult:
        # Served-model attribution must be present and non-conflicting across
        # the body, the attribution block, and the response headers view.
        body_model = payload.get("model")
        if not isinstance(body_model, str) or not body_model:
            raise ServedModelAttributionError(
                "skgateway response is missing the served model"
            )
        candidates: dict[str, str] = {"body": body_model}
        backend: str | None = None
        generation: str | None = None
        policy_revision: str | None = None
        attribution_request_id: str | None = None
        attribution = payload.get("x_skgateway")
        if attribution is not None:
            if not isinstance(attribution, Mapping):
                raise ServedModelAttributionError(
                    "skgateway attribution block is malformed"
                )
            served = attribution.get("served_model")
            if not isinstance(served, str) or not served:
                raise ServedModelAttributionError(
                    "skgateway attribution block is missing the served model"
                )
            candidates["attribution"] = served
            observed_backend = attribution.get("backend")
            if isinstance(observed_backend, str) and observed_backend:
                backend = observed_backend
            observed_generation = attribution.get("catalog_generation")
            if isinstance(observed_generation, str) and observed_generation:
                generation = observed_generation
            observed_policy = attribution.get("policy_revision")
            if isinstance(observed_policy, str) and observed_policy:
                policy_revision = observed_policy
            observed_request = attribution.get("request_id")
            if isinstance(observed_request, str) and observed_request:
                attribution_request_id = observed_request
        header_model = payload.get("header_served_model")
        if isinstance(header_model, str) and header_model:
            candidates["header"] = header_model
        if len(set(candidates.values())) != 1:
            raise ServedModelAttributionError(
                "skgateway served-model attribution conflicts: "
                + ", ".join(f"{k}={v}" for k, v in sorted(candidates.items()))
            )
        body_request_id = payload.get("id")
        gateway_request_id = (
            body_request_id
            if isinstance(body_request_id, str) and body_request_id
            else attribution_request_id
        )
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderContractError("skgateway choices are missing")
        first = choices[0]
        if not isinstance(first, Mapping):
            raise ProviderContractError("skgateway choice is malformed")
        message = first.get("message")
        if not isinstance(message, Mapping):
            raise ProviderContractError("skgateway message is missing")
        text = message.get("content")
        if not isinstance(text, str) or not text:
            raise ProviderContractError("skgateway message content is missing")
        usage = payload.get("usage")
        if not isinstance(usage, Mapping):
            raise ProviderContractError("skgateway token usage is missing")
        try:
            token_usage = TokenUsage(
                prompt_tokens=int(usage["prompt_tokens"]),
                completion_tokens=int(usage["completion_tokens"]),
                total_tokens=int(usage["total_tokens"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderContractError(
                "skgateway token usage is malformed"
            ) from exc
        retries = payload.get("x_skgateway_retries")
        if retries is not None and not isinstance(retries, int):
            raise ProviderContractError("skgateway retry count is malformed")
        failover = payload.get("x_skgateway_failover")
        if failover is not None and not isinstance(failover, bool):
            raise ProviderContractError("skgateway failover flag is malformed")
        # Rebuild the evidence record once with every observed attribution
        # field instead of mutating the frozen seed built before the call.
        observed = evidence.model_copy(
            update={
                "backend": backend,
                "served_model": body_model,
                "gateway_request_id": gateway_request_id,
                "catalog_generation": generation,
                "gateway_policy_revision": policy_revision,
                "retries": retries if isinstance(retries, int) else evidence.retries,
                "failover": failover
                if isinstance(failover, bool)
                else evidence.failover,
            }
        )
        return ProviderResult(
            raw_text=text,
            # The route pins the exact revision; the gateway may not rewrite
            # it. Served-model identity is carried in transport evidence.
            model_revision=call.route.model.revision,
            provider_request_id=gateway_request_id,
            usage=token_usage,
            transport=observed,
        )
