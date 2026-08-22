"""Typed model-gateway failures.

Every gateway failure is explicit and typed so Temporal activities can map
denials to non-retryable classes and outages to bounded retry classes.
"""

from __future__ import annotations


class ModelGatewayError(ValueError):
    """Base class for provider-neutral gateway failures."""


class RouteNotFoundError(ModelGatewayError):
    """Raised when a request names a route absent from the pinned registry."""


class RouteDisabledError(ModelGatewayError):
    """Raised when a pinned route is present but disabled."""


class RouteIntegrityError(ModelGatewayError):
    """Raised when a pinned hash or revision does not match loaded material."""


class CapabilityDeniedError(ModelGatewayError):
    """Raised when capability verification fails or is unavailable."""


class EgressDeniedError(ModelGatewayError):
    """Raised when the egress policy denies the classification to a provider."""


class ContextBudgetExceededError(ModelGatewayError):
    """Raised when a rendered prompt exceeds the pinned context budget."""


class PromptRenderError(ModelGatewayError):
    """Raised when a pinned prompt template cannot be rendered exactly."""


class ProviderSaturationError(ModelGatewayError):
    """Raised when every admission slot for a provider is in flight."""


class ModelTimeoutError(ModelGatewayError):
    """Raised when a provider call exceeds the pinned route timeout."""


class ModelCancelledError(ModelGatewayError):
    """Raised when a caller cancels a submission before a proposal exists."""


class ProviderUnavailableError(ModelGatewayError):
    """Raised when a provider transport fails or no adapter is bound."""


class ProviderContractError(ModelGatewayError):
    """Raised when a provider returns a malformed or unexpected payload."""


class SchemaValidationError(ModelGatewayError):
    """Raised when provider output is not valid JSON for the pinned schema."""


class SecretResolutionError(ModelGatewayError):
    """Raised when a secret reference cannot be resolved; fails closed."""


class PolicyUnavailableError(ModelGatewayError):
    """Raised when the egress policy cannot be loaded; fails closed."""
