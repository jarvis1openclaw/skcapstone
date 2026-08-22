"""SKLegal provider-neutral model gateway.

Routes approved typed activities to local Qwen or OpenAI through pinned
routes without provider-specific domain coupling. Provider output is always
an immutable Proposal and can never mutate workflow or domain state.
"""

from .admission import AdmissionController
from .egress import EgressDecision, EgressGate, PolicyFileEgressGate
from .errors import (
    CapabilityDeniedError,
    ContextBudgetExceededError,
    EgressDeniedError,
    ModelCancelledError,
    ModelGatewayError,
    ModelTimeoutError,
    PolicyUnavailableError,
    PromptRenderError,
    ProviderContractError,
    ProviderSaturationError,
    ProviderUnavailableError,
    RouteDisabledError,
    RouteIntegrityError,
    RouteNotFoundError,
    SchemaValidationError,
    SecretResolutionError,
)
from .gateway import CapabilityGate, ModelGateway
from .models import (
    CancellationToken,
    GatewayValue,
    ModelPin,
    ModelRouteRecord,
    PolicyEvidence,
    Proposal,
    ProposalRequest,
    Provider,
    RedactionRecord,
    ResponseEvidence,
    TokenUsage,
)
from .prompts import FilePromptStore, PromptStore, estimate_tokens, render_prompt
from .providers import (
    ModelProvider,
    OpenAiResponsesProvider,
    OpenAiTransport,
    ProviderCall,
    ProviderResult,
    QwenLocalProvider,
    QwenTransport,
    SecretResolver,
)
from .registry import REGISTRY_SCHEMA, RouteRegistry, sha256_text
from .schemas import (
    CORPUS_SUMMARY_SCHEMA_ID,
    CorpusSummaryProposalPayload,
    SchemaRegistry,
    schema_sha256,
)

__all__ = [
    "CORPUS_SUMMARY_SCHEMA_ID",
    "PACKAGE_NAME",
    "REGISTRY_SCHEMA",
    "AdmissionController",
    "CancellationToken",
    "CapabilityDeniedError",
    "CapabilityGate",
    "ContextBudgetExceededError",
    "CorpusSummaryProposalPayload",
    "EgressDecision",
    "EgressDeniedError",
    "EgressGate",
    "FilePromptStore",
    "GatewayValue",
    "ModelCancelledError",
    "ModelGateway",
    "ModelGatewayError",
    "ModelPin",
    "ModelProvider",
    "ModelRouteRecord",
    "ModelTimeoutError",
    "OpenAiResponsesProvider",
    "OpenAiTransport",
    "PolicyEvidence",
    "PolicyFileEgressGate",
    "PolicyUnavailableError",
    "PromptRenderError",
    "PromptStore",
    "Proposal",
    "ProposalRequest",
    "Provider",
    "ProviderCall",
    "ProviderContractError",
    "ProviderResult",
    "ProviderSaturationError",
    "ProviderUnavailableError",
    "QwenLocalProvider",
    "QwenTransport",
    "RedactionRecord",
    "ResponseEvidence",
    "RouteDisabledError",
    "RouteIntegrityError",
    "RouteNotFoundError",
    "RouteRegistry",
    "SchemaRegistry",
    "SchemaValidationError",
    "SecretResolutionError",
    "SecretResolver",
    "TokenUsage",
    "estimate_tokens",
    "render_prompt",
    "schema_sha256",
    "sha256_text",
]

PACKAGE_NAME = "sklegal-model-gateway"
