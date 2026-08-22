"""Versioned, hash-pinned, bounded SKLegal agent specifications.

An AgentSpec is an immutable role contract validated at definition time:
explicit tool allowlist from the known catalog, finite budgets, matter
scoped context with a classification ceiling, model routes pinned in the
gateway registry, a fixed retry class, and a human escalation target. The
registry pins every version by the SHA-256 of its source file. This package
grants no runtime authority and performs no tool or model calls.
"""

from .errors import (
    AgentSpecError,
    ModelRouteNotEnabledError,
    SpecIntegrityError,
    SpecNotFoundError,
    SpecValidationError,
    SpecVersionImmutableError,
    UnboundedAuthorityError,
    UnknownModelRouteError,
    UnknownToolError,
)
from .models import (
    HUMAN_ESCALATION_PREFIX,
    MAX_RETRIES,
    MAX_TOOL_CALLS_PER_RUN,
    MAX_WALL_CLOCK_SECONDS,
    RETRY_CLASSES,
    SPEC_SCHEMA,
    AgentSpec,
    AgentSpecRecord,
    AgentSpecValue,
    AllowedContext,
    SchemaPin,
    SpecBudgets,
    classification_rank,
)
from .registry import AgentSpecRegistry
from .tools import KNOWN_TOOL_IDS, TOOL_CATALOG, KnownTool

__all__ = [
    "HUMAN_ESCALATION_PREFIX",
    "KNOWN_TOOL_IDS",
    "MAX_RETRIES",
    "MAX_TOOL_CALLS_PER_RUN",
    "MAX_WALL_CLOCK_SECONDS",
    "RETRY_CLASSES",
    "SPEC_SCHEMA",
    "TOOL_CATALOG",
    "AgentSpec",
    "AgentSpecError",
    "AgentSpecRecord",
    "AgentSpecRegistry",
    "AgentSpecValue",
    "AllowedContext",
    "KnownTool",
    "ModelRouteNotEnabledError",
    "SchemaPin",
    "SpecBudgets",
    "SpecIntegrityError",
    "SpecNotFoundError",
    "SpecValidationError",
    "SpecVersionImmutableError",
    "UnboundedAuthorityError",
    "UnknownModelRouteError",
    "UnknownToolError",
    "classification_rank",
]
