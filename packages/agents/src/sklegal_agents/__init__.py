"""Versioned, hash-pinned, bounded SKLegal agent specifications and gateway.

An AgentSpec is an immutable role contract validated at definition time:
explicit tool allowlist from the known catalog, finite budgets, matter
scoped context with a classification ceiling, model routes pinned in the
gateway registry, a fixed retry class, and a human escalation target. The
registry pins every version by the SHA-256 of its source file. The tool
gateway mediates every domain tool call at run time: a scoped CapAuth
capability verified at invocation, argument and result validation against
hash-pinned schema artifacts, and per-run budget accounting. Handlers
receive sanitized decision context only and never see raw credentials.
"""

from .contracts import TOOL_CONTRACT_INDEX, TOOL_CONTRACTS, ToolContract, tool_contract
from .errors import (
    AgentSpecError,
    ModelRouteNotEnabledError,
    RunInputValidationError,
    SpecIntegrityError,
    SpecNotFoundError,
    SpecValidationError,
    SpecVersionImmutableError,
    ToolArgumentValidationError,
    ToolBudgetExhaustedError,
    ToolGatewayError,
    ToolHandlerUnavailableError,
    ToolNotAllowlistedError,
    ToolResultValidationError,
    ToolSchemaIntegrityError,
    UnboundedAuthorityError,
    UnknownModelRouteError,
    UnknownToolError,
)
from .gateway import (
    AgentRun,
    DenialRecord,
    ToolCallContext,
    ToolCallRecord,
    ToolGateway,
    ToolHandler,
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
    "TOOL_CONTRACTS",
    "TOOL_CONTRACT_INDEX",
    "AgentRun",
    "AgentSpec",
    "AgentSpecError",
    "AgentSpecRecord",
    "AgentSpecRegistry",
    "AgentSpecValue",
    "AllowedContext",
    "DenialRecord",
    "KnownTool",
    "ModelRouteNotEnabledError",
    "RunInputValidationError",
    "SchemaPin",
    "SpecBudgets",
    "SpecIntegrityError",
    "SpecNotFoundError",
    "SpecValidationError",
    "SpecVersionImmutableError",
    "ToolArgumentValidationError",
    "ToolBudgetExhaustedError",
    "ToolCallContext",
    "ToolCallRecord",
    "ToolContract",
    "ToolGateway",
    "ToolGatewayError",
    "ToolHandler",
    "ToolHandlerUnavailableError",
    "ToolNotAllowlistedError",
    "ToolResultValidationError",
    "ToolSchemaIntegrityError",
    "UnboundedAuthorityError",
    "UnknownModelRouteError",
    "UnknownToolError",
    "classification_rank",
    "tool_contract",
]
