"""Provider-neutral typed records for the SKLegal model gateway.

A Proposal is the only output of the gateway. It is immutable, carries
response evidence, and holds no transport, registry, or store handle, so
provider output can never mutate workflow or domain state directly. Only
deterministic reducers and human approvals apply state changes.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sklegal_domain import DataClassification

from .errors import ModelCancelledError

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ShortCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9][a-z0-9._:/-]*$",
    ),
]

RETRY_CLASSES = ("interactive", "batch", "long_context", "model")


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("gateway timestamps must use UTC offset zero")
    return value


class GatewayValue(BaseModel):
    """Immutable strict base for gateway boundary values."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class Provider(StrEnum):
    QWEN_LOCAL = "qwen_local"
    OPENAI = "openai"


class TransportKind(StrEnum):
    """Deployment-only transport binding behind a logical route.

    A transport kind never changes the logical provider, the prompt or output
    schema pins, or any legal gate. Switching a route between ``direct_qwen``
    and ``skgateway_chat`` changes only which adapter carries the call.
    """

    DIRECT_QWEN = "direct_qwen"
    SKGATEWAY_CHAT = "skgateway_chat"
    OPENAI_RESPONSES = "openai_responses"


class WorkloadClass(StrEnum):
    """Required task capability and risk, never model parameter size."""

    S = "S"
    M = "M"
    L = "L"
    XL = "XL"


class TrustZone(StrEnum):
    """Trust zone of a served model or bucket member."""

    PUBLIC = "public"
    INTERNAL = "internal"
    SOVEREIGN_LOCAL = "sovereign_local"


ENV_REFERENCE_PATTERN = r"^[A-Z][A-Z0-9_]*$"


def _require_env_reference(value: str, field: str) -> str:
    if not re.match(ENV_REFERENCE_PATTERN, value):
        raise ValueError(
            f"{field} must be an environment variable name, not a literal address"
        )
    return value


class TransportProfile(GatewayValue):
    """Deployment-only transport binding resolved outside domain state.

    Endpoint addresses never appear literally: the profile carries an
    environment variable name that the deployment resolves at start time.
    SKGateway profiles require a CapAuth-attributed service identity and the
    ``skgateway.infer`` capability scope before any protected call.
    """

    profile_id: ShortCode
    kind: TransportKind
    enabled: bool
    config_sha256: Sha256
    endpoint_reference: str
    service_identity: str | None = None
    capability_scope: str | None = None
    secret_reference: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_kind_requirements(self) -> Self:
        _require_env_reference(self.endpoint_reference, "endpoint_reference")
        if self.kind is TransportKind.SKGATEWAY_CHAT:
            if not self.service_identity:
                raise ValueError(
                    "an skgateway_chat profile requires a CapAuth service identity"
                )
            if not self.capability_scope:
                raise ValueError(
                    "an skgateway_chat profile requires a capability scope"
                )
            if self.secret_reference is not None:
                raise ValueError(
                    "an skgateway_chat profile must not carry a provider secret; "
                    "upstream keys live only in SKGateway configuration"
                )
        elif self.kind is TransportKind.DIRECT_QWEN:
            if self.service_identity is not None or self.capability_scope is not None:
                raise ValueError(
                    "a direct_qwen profile is secret-free and unauthenticated"
                )
            if self.secret_reference is not None:
                raise ValueError("a direct_qwen profile must not carry a secret")
        elif self.kind is TransportKind.OPENAI_RESPONSES:
            if self.service_identity is not None or self.capability_scope is not None:
                raise ValueError(
                    "an openai_responses profile uses a secret reference, not a "
                    "service identity"
                )
            if self.secret_reference is None:
                raise ValueError("an openai_responses profile requires a secret")
            if not self.secret_reference.startswith("vault:"):
                raise ValueError(
                    "an openai_responses secret reference must use the vault scheme"
                )
        for reference in (self.secret_reference,):
            if reference is not None and reference.startswith("sk-"):
                raise ValueError(
                    "a raw provider API key is never a valid secret reference"
                )
        return self

    def content_sha256_fields(self) -> dict[str, Any]:
        """Canonical content used to verify the pinned configuration hash."""

        return {
            "profile_id": self.profile_id,
            "kind": str(self.kind),
            "enabled": self.enabled,
            "endpoint_reference": self.endpoint_reference,
            "service_identity": self.service_identity,
            "capability_scope": self.capability_scope,
            "secret_reference": self.secret_reference,
            "notes": self.notes,
        }


class ModelPin(GatewayValue):
    """Exact model identity pinned by a route."""

    name: ShortCode
    revision: ShortCode

    @property
    def deployed_name(self) -> str:
        """Provider-facing model identifier derived from the pin."""

        return f"{self.name}-{self.revision}"


class ModelRouteRecord(GatewayValue):
    """One pinned route in the versioned model-route registry."""

    route_id: ShortCode
    provider: Provider
    enabled: bool
    model: ModelPin
    prompt_template_id: ShortCode
    prompt_template_sha256: Sha256
    output_schema_id: ShortCode
    output_schema_sha256: Sha256
    context_token_budget: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0, le=3600)
    retry_class: Literal["interactive", "batch", "long_context", "model"]
    egress_classification_ceiling: DataClassification
    max_concurrent: int = Field(default=4, ge=1, le=64)
    secret_reference: str | None = None
    transport_profile_id: ShortCode | None = None
    capacity_domain_id: ShortCode | None = None
    workload_class: WorkloadClass | None = None
    allowed_served_model_alias: ShortCode | None = None
    allowed_bucket_id: ShortCode | None = None

    @model_validator(mode="after")
    def validate_secret_reference(self) -> Self:
        if self.transport_profile_id is None:
            if (self.provider is Provider.OPENAI) != (
                self.secret_reference is not None
            ):
                raise ValueError(
                    "an OpenAI route requires a secret reference and a local "
                    "route must not carry one"
                )
        elif self.secret_reference is not None:
            raise ValueError(
                "a route with a transport profile resolves base addresses and "
                "secret references through the profile, not the route"
            )
        if self.secret_reference is not None:
            reference = self.secret_reference
            if ":" not in reference:
                raise ValueError("secret reference must use a store scheme prefix")
            if reference.startswith("sk-"):
                raise ValueError(
                    "a raw provider API key is never a valid secret reference"
                )
        if (
            self.allowed_served_model_alias is not None
            and self.allowed_bucket_id is not None
        ):
            raise ValueError(
                "a route pins either an exact served-model alias or one bucket, "
                "never both"
            )
        return self


class ProposalRequest(GatewayValue):
    """Typed, approved activity input routed to a provider."""

    request_id: ShortCode
    tenant_id: ShortCode
    matter_id: ShortCode
    route_id: ShortCode
    purpose: ShortCode
    classification: DataClassification
    prompt_inputs: dict[str, str] = Field(min_length=1)
    protected_fields: frozenset[str] = frozenset()
    human_approval_ref: str | None = None
    workflow_run_id: str | None = None

    @model_validator(mode="after")
    def validate_inputs(self) -> Self:
        if not self.protected_fields <= self.prompt_inputs.keys():
            raise ValueError("protected fields must name prompt input keys")
        if any(not self.prompt_inputs[field] for field in self.protected_fields):
            raise ValueError("protected prompt inputs cannot be empty")
        if self.human_approval_ref is not None and not self.human_approval_ref:
            raise ValueError("human approval reference cannot be empty")
        return self


class RedactionRecord(GatewayValue):
    """Evidence that one protected field was redacted before egress."""

    field: str
    value_sha256: Sha256


class TokenUsage(GatewayValue):
    """Token accounting reported by the provider."""

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("total tokens must equal prompt plus completion")
        return self


class PolicyEvidence(GatewayValue):
    """Egress decision evidence bound to the exact policy revision."""

    policy_revision: Sha256
    classification: DataClassification
    egress_rule: str
    human_approval_ref: str | None = None


class TransportEvidence(GatewayValue):
    """Routing and attribution evidence for one transported call.

    Records the transport binding plus every gateway routing choice: request
    identifier, backend, requested alias or bucket, bucket member, exact
    served model, catalog generation, gateway policy revision, retries,
    failover, and saturation. A successful HTTP response is evidence only;
    it is never Approval or a state mutation.
    """

    transport_profile_id: str | None = None
    transport_kind: TransportKind | None = None
    gateway_request_id: str | None = None
    backend: str | None = None
    requested_model: str | None = None
    served_model: str | None = None
    bucket_id: str | None = None
    bucket_member_id: str | None = None
    catalog_generation: str | None = None
    gateway_policy_revision: str | None = None
    retries: int = Field(default=0, ge=0)
    failover: bool = False
    saturation_observed: bool = False


class ProviderExecutionContext(GatewayValue):
    """Non-protected request context a transport adapter may need.

    Carries identity and policy references only: never prompt text, source
    documents, secrets, or raw capability tokens.
    """

    request_id: ShortCode
    tenant_id: ShortCode
    matter_id: ShortCode
    purpose: ShortCode
    classification: DataClassification
    human_approval_ref: str | None = None
    source_rights_state: str | None = None


class ResponseEvidence(GatewayValue):
    """Provider response provenance for one proposal."""

    provider_request_id: str | None = None
    started_at: datetime
    ended_at: datetime
    duration_ms: int = Field(ge=0)
    token_usage: TokenUsage
    transport: TransportEvidence | None = None

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        require_utc(self.started_at)
        require_utc(self.ended_at)
        if self.ended_at < self.started_at:
            raise ValueError("response evidence cannot end before it starts")
        expected = round((self.ended_at - self.started_at).total_seconds() * 1000)
        if self.duration_ms != expected:
            raise ValueError("duration must match the recorded interval")
        return self


class Proposal(GatewayValue):
    """Immutable provider output with complete response evidence.

    A Proposal holds no transport, registry, or store handle and exposes no
    mutating methods. It is inert data: only deterministic reducers and
    human approvals may turn it into workflow or domain state.
    """

    proposal_id: str
    request_id: ShortCode
    route_id: ShortCode
    registry_revision: Sha256
    provider: Provider
    model: ModelPin
    prompt_template_id: ShortCode
    prompt_template_sha256: Sha256
    output_schema_id: ShortCode
    output_schema_sha256: Sha256
    payload: dict[str, Any]
    payload_sha256: Sha256
    redactions: tuple[RedactionRecord, ...]
    policy: PolicyEvidence
    evidence: ResponseEvidence


class CancellationToken:
    """Cooperative cancellation flag shared by the gateway and providers.

    A token may wrap a parent token so the gateway can cancel its own
    internal token on timeout without mutating the caller's token.
    """

    def __init__(self, parent: CancellationToken | None = None) -> None:
        self._event = threading.Event()
        self._parent = parent

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set() or (
            self._parent is not None and self._parent.cancelled
        )

    def wait(self, timeout: float) -> bool:
        """Block until cancelled or the timeout elapses."""

        deadline = time.monotonic() + timeout
        while not self.cancelled:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._event.wait(min(remaining, 0.05))
        return True

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise ModelCancelledError("model submission was cancelled")
