"""Typed agent-specification failures.

Every definition-time failure is explicit and typed so unbounded authority
can never enter the registry silently. Loaders fail closed: a spec that
cannot be fully validated is rejected, never partially admitted.
"""


class AgentSpecError(ValueError):
    """Base class for agent specification and registry failures."""


class SpecValidationError(AgentSpecError):
    """Raised when a spec document fails schema validation."""


class SpecIntegrityError(AgentSpecError):
    """Raised when a spec file hash does not match its pinned hash."""


class UnknownToolError(AgentSpecError):
    """Raised when a spec allowlists a tool absent from the known catalog."""


class UnboundedAuthorityError(AgentSpecError):
    """Raised when a spec requests authority that is not explicitly bounded."""


class UnknownModelRouteError(AgentSpecError):
    """Raised when a spec pins a route absent from the route registry."""


class ModelRouteNotEnabledError(AgentSpecError):
    """Raised when a spec pins a route that is present but disabled."""


class SpecVersionImmutableError(AgentSpecError):
    """Raised when a different document claims an existing spec version."""


class SpecNotFoundError(AgentSpecError):
    """Raised when a lookup names a spec or version absent from the registry."""


class ToolGatewayError(RuntimeError):
    """Base class for runtime tool gateway failures."""


class ToolSchemaIntegrityError(ToolGatewayError):
    """Raised when a pinned schema artifact is missing or hash-mismatched."""


class RunInputValidationError(ToolGatewayError):
    """Raised when run input fails the spec's pinned input schema."""


class ToolNotAllowlistedError(ToolGatewayError):
    """Raised when a run calls a tool outside its spec's allowlist."""


class ToolArgumentValidationError(ToolGatewayError):
    """Raised when tool arguments fail the pinned input schema."""


class ToolResultValidationError(ToolGatewayError):
    """Raised when a tool result fails the pinned output schema."""


class ToolBudgetExhaustedError(ToolGatewayError):
    """Raised when a run budget is exhausted before or during a call."""


class ToolHandlerUnavailableError(ToolGatewayError):
    """Raised when no handler is registered for an allowlisted tool."""
