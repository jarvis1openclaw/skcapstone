"""Known domain-tool catalog for agent specification allowlists.

The catalog is the definition-time boundary for tool authority. A spec may
only allowlist tools pinned here; every other tool id is rejected at load
with an UnknownToolError. No catalog tool holds shell, network, email,
filing, service, calendar, or browser credentials, and no tool mutates
workflow or domain state directly: read tools return scoped matter context
and write-class tools emit typed proposals for deterministic reducers and
human approvals.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .models import ShortCode


class KnownTool(BaseModel):
    """One tool an agent spec is permitted to allowlist."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: ShortCode
    domain: str
    description: str
    read_only: bool


TOOL_CATALOG: tuple[KnownTool, ...] = (
    KnownTool(
        tool_id="matter.read",
        domain="Matter",
        description="Read matter structure, parties, and issues for one matter.",
        read_only=True,
    ),
    KnownTool(
        tool_id="evidence.search",
        domain="Evidence Item",
        description="Search evidence items within one matter partition.",
        read_only=True,
    ),
    KnownTool(
        tool_id="evidence.read",
        domain="Evidence Item",
        description="Read one evidence item with its source hash and provenance.",
        read_only=True,
    ),
    KnownTool(
        tool_id="authority.search",
        domain="Authority",
        description="Search authorities with jurisdiction and status metadata.",
        read_only=True,
    ),
    KnownTool(
        tool_id="deadline.compute",
        domain="Deadline",
        description="Compute deadline candidates through the deterministic calendar.",
        read_only=True,
    ),
    KnownTool(
        tool_id="workproduct.draft",
        domain="Work Product",
        description=(
            "Emit a typed work-product draft proposal. The proposal never "
            "mutates state; only a deterministic reducer with human approval "
            "may accept it."
        ),
        read_only=False,
    ),
)

KNOWN_TOOL_IDS: frozenset[str] = frozenset(tool.tool_id for tool in TOOL_CATALOG)

WILDCARD_MARKERS: frozenset[str] = frozenset({"*", "?", "["})
