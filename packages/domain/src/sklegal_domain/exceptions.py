"""Domain-specific failures raised before persistence or transport."""


class DomainError(ValueError):
    """Base class for deterministic domain validation failures."""


class DomainTransitionError(DomainError):
    """Raised when an aggregate attempts an invalid state transition."""


class IdentityMutationError(DomainError):
    """Raised when an aggregate evolution attempts to change its identity."""
