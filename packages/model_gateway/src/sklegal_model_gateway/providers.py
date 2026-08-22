"""Provider adapters behind transport protocols.

The gateway never performs network I/O directly. Qwen uses a local endpoint
transport and OpenAI uses a Responses API transport; tests bind fakes. Raw
secrets never enter the gateway: the OpenAI adapter resolves a secret
reference through a SecretResolver at call time and never logs the value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .errors import (
    ModelGatewayError,
    ProviderContractError,
    ProviderUnavailableError,
    SecretResolutionError,
)
from .models import (
    CancellationToken,
    ModelPin,
    ModelRouteRecord,
    Provider,
    ProviderExecutionContext,
    TokenUsage,
    TransportEvidence,
    TransportKind,
    TransportProfile,
)


@dataclass(frozen=True, slots=True)
class ProviderCall:
    """Everything a provider needs for one pinned, redacted call."""

    route: ModelRouteRecord
    prompt: str
    output_schema: Mapping[str, Any]
    cancel_token: CancellationToken
    context: ProviderExecutionContext | None = None
    capability_ref: str | None = None
    transport_profile: TransportProfile | None = None
    transport_evidence: TransportEvidence | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """Raw provider output plus observed provenance."""

    raw_text: str
    model_revision: str
    provider_request_id: str | None
    usage: TokenUsage
    transport: TransportEvidence | None = None


class ModelProvider(Protocol):
    """Provider-neutral completion adapter."""

    provider: Provider
    transport_kind: TransportKind

    def complete(self, call: ProviderCall) -> ProviderResult: ...


def _deployed_model(model: ModelPin) -> str:
    return model.deployed_name


class QwenTransport(Protocol):
    """Local Qwen endpoint transport; tests bind fakes, never live calls."""

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_output_tokens: int,
        timeout_seconds: float,
        cancel_token: CancellationToken,
    ) -> Mapping[str, Any]: ...


class QwenLocalProvider:
    """Adapter for the pinned local Qwen endpoint."""

    provider = Provider.QWEN_LOCAL
    transport_kind = TransportKind.DIRECT_QWEN

    def __init__(self, transport: QwenTransport) -> None:
        self._transport = transport

    def complete(self, call: ProviderCall) -> ProviderResult:
        call.cancel_token.raise_if_cancelled()
        try:
            payload = self._transport.generate(
                model=_deployed_model(call.route.model),
                prompt=call.prompt,
                max_output_tokens=call.route.max_output_tokens,
                timeout_seconds=call.route.timeout_seconds,
                cancel_token=call.cancel_token,
            )
        except ModelGatewayError:
            raise
        except Exception as exc:
            raise ProviderUnavailableError("qwen_local transport failed") from exc
        text = payload.get("response")
        if not isinstance(text, str) or not text:
            raise ProviderContractError("qwen_local response text is missing")
        usage = payload.get("usage")
        if not isinstance(usage, Mapping):
            raise ProviderContractError("qwen_local token usage is missing")
        revision = payload.get("revision")
        if not isinstance(revision, str) or not revision:
            raise ProviderContractError("qwen_local model revision is missing")
        request_id = payload.get("request_id")
        try:
            token_usage = TokenUsage(
                prompt_tokens=int(usage["prompt_tokens"]),
                completion_tokens=int(usage["completion_tokens"]),
                total_tokens=int(usage["total_tokens"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderContractError("qwen_local token usage is malformed") from exc
        return ProviderResult(
            raw_text=text,
            model_revision=revision,
            provider_request_id=(
                str(request_id) if isinstance(request_id, str) else None
            ),
            usage=token_usage,
        )


class SecretResolver(Protocol):
    """Resolve a secret-store reference to a credential at call time."""

    def resolve(self, reference: str) -> str: ...


class OpenAiTransport(Protocol):
    """OpenAI Responses API transport; tests bind fakes, never live calls."""

    def create_response(
        self,
        *,
        body: Mapping[str, Any],
        api_key: str,
        timeout_seconds: float,
        cancel_token: CancellationToken,
    ) -> Mapping[str, Any]: ...


class OpenAiResponsesProvider:
    """OpenAI Responses API adapter with structured-output requests.

    The adapter pins the deployment model, requires a JSON Schema response
    format, disables provider-side storage, and resolves the API key only
    through a secret reference. A consumer ChatGPT session or subscription
    is never an application credential.
    """

    provider = Provider.OPENAI
    transport_kind = TransportKind.OPENAI_RESPONSES

    def __init__(
        self,
        transport: OpenAiTransport,
        secret_resolver: SecretResolver,
    ) -> None:
        self._transport = transport
        self._secret_resolver = secret_resolver

    def complete(self, call: ProviderCall) -> ProviderResult:
        call.cancel_token.raise_if_cancelled()
        route = call.route
        if route.secret_reference is None:
            raise ProviderContractError("an OpenAI route requires a secret reference")
        try:
            api_key = self._secret_resolver.resolve(route.secret_reference)
        except ModelGatewayError:
            raise
        except Exception as exc:
            raise SecretResolutionError(
                "openai secret reference could not be resolved"
            ) from exc
        if not api_key:
            raise SecretResolutionError("openai secret reference resolved empty")
        body: dict[str, Any] = {
            "model": _deployed_model(route.model),
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": call.prompt}],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": route.output_schema_id.replace("/", "_"),
                    "schema": dict(call.output_schema),
                    "strict": True,
                }
            },
            "max_output_tokens": route.max_output_tokens,
            "store": False,
        }
        try:
            payload = self._transport.create_response(
                body=body,
                api_key=api_key,
                timeout_seconds=route.timeout_seconds,
                cancel_token=call.cancel_token,
            )
        except ModelGatewayError:
            raise
        except Exception as exc:
            raise ProviderUnavailableError("openai transport failed") from exc
        return self._extract(call, payload)

    @staticmethod
    def _extract(call: ProviderCall, payload: Mapping[str, Any]) -> ProviderResult:
        observed_model = payload.get("model")
        if observed_model != _deployed_model(call.route.model):
            raise ProviderContractError(
                "openai response does not match the pinned deployment model"
            )
        text: str | None = None
        output = payload.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, Mapping):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if isinstance(part, Mapping) and part.get("type") == "output_text":
                        candidate = part.get("text")
                        if isinstance(candidate, str) and candidate:
                            text = candidate
        if text is None:
            raise ProviderContractError("openai output text is missing")
        usage = payload.get("usage")
        if not isinstance(usage, Mapping):
            raise ProviderContractError("openai token usage is missing")
        request_id = payload.get("id")
        try:
            token_usage = TokenUsage(
                prompt_tokens=int(usage["input_tokens"]),
                completion_tokens=int(usage["output_tokens"]),
                total_tokens=int(usage["total_tokens"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderContractError("openai token usage is malformed") from exc
        return ProviderResult(
            raw_text=text,
            model_revision=call.route.model.revision,
            provider_request_id=(
                str(request_id) if isinstance(request_id, str) else None
            ),
            usage=token_usage,
        )
