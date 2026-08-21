"""Coordination card-hygiene tools: describe, label, and link.

These mirror the ``coord describe`` / ``coord label`` / ``coord link`` CLI
verbs exactly: the same append-only overlay events (``CardEventLog``), the
same writer attribution (``agent`` param, empty means "default to host"),
and the same best-effort CardStore mirror for ``describe`` when dual-write
is enabled. MCP-first agents can now do routine board hygiene without
shelling out to the CLI (card 61b97e22).
"""

from __future__ import annotations

from mcp.types import TextContent, Tool

from ._helpers import _error_response, _json_response, _shared_root

TOOLS: list[Tool] = [
    Tool(
        name="coord_describe",
        description=(
            "Edit a card's title and/or description (folded, never rewrites "
            "core.json). Only the fields passed are changed; an empty string "
            "clears a field. Same appended, writer-attributed event as the CLI."
        ),
        inputSchema={
            "properties": {
                "agent": {"description": "Writer name (defaults to host)", "type": "string"},
                "description": {"description": "New card description", "type": "string"},
                "task_id": {"description": "The card/task ID", "type": "string"},
                "title": {"description": "New card title", "type": "string"},
            },
            "required": ["task_id"],
            "type": "object",
        },
    ),
    Tool(
        name="coord_label",
        description="Add (or remove) a label on a card.",
        inputSchema={
            "properties": {
                "agent": {"description": "Writer name (defaults to host)", "type": "string"},
                "label": {"description": "The label to add or remove", "type": "string"},
                "remove": {
                    "description": "Remove the label instead of adding it (default: false)",
                    "type": "boolean",
                },
                "task_id": {"description": "The card/task ID", "type": "string"},
            },
            "required": ["task_id", "label"],
            "type": "object",
        },
    ),
    Tool(
        name="coord_link",
        description="Attach a link (pr/commit/doc/...) to a card.",
        inputSchema={
            "properties": {
                "agent": {"description": "Writer name (defaults to host)", "type": "string"},
                "key": {"description": "Link key (e.g. 'pr', 'commit', 'doc')", "type": "string"},
                "task_id": {"description": "The card/task ID", "type": "string"},
                "value": {"description": "Link value (URL or ref)", "type": "string"},
            },
            "required": ["task_id", "key", "value"],
            "type": "object",
        },
    ),
]


async def _handle_coord_describe(args: dict) -> list[TextContent]:
    """Edit a card's title/description via one appended overlay event."""
    from ..card import CardEvent, CardEventLog

    task_id = args.get("task_id", "")
    if not task_id:
        return _error_response("task_id is required")
    title = args.get("title")
    description = args.get("description")
    if title is None and description is None:
        return _error_response("title and/or description are required")

    home = _shared_root()
    agent = args.get("agent", "") or ""
    CardEventLog(home).append(
        CardEvent(
            card_id=task_id,
            action="describe",
            title=title,
            description=description,
            writer=agent,
        )
    )
    from ..card_store import card_store_write_enabled, mirror_coord_describe

    if card_store_write_enabled():
        mirror_coord_describe(home, task_id, agent, title=title, description=description)
    changed = [k for k, v in (("title", title), ("description", description)) if v is not None]
    return _json_response({"described": True, "task_id": task_id, "changed": changed})


async def _handle_coord_label(args: dict) -> list[TextContent]:
    """Add or remove a label on a card via one appended overlay event."""
    from ..card import CardEvent, CardEventLog

    task_id = args.get("task_id", "")
    label = args.get("label", "")
    if not task_id or not label:
        return _error_response("task_id and label are required")

    remove = bool(args.get("remove", False))
    action = "remove_label" if remove else "add_label"
    CardEventLog(_shared_root()).append(
        CardEvent(card_id=task_id, action=action, label=label, writer=args.get("agent", "") or "")
    )
    return _json_response({"labeled": True, "task_id": task_id, "label": label, "action": action})


async def _handle_coord_link(args: dict) -> list[TextContent]:
    """Attach a link to a card via one appended overlay event."""
    from ..card import CardEvent, CardEventLog

    task_id = args.get("task_id", "")
    key = args.get("key", "")
    value = args.get("value", "")
    if not task_id or not key or not value:
        return _error_response("task_id, key, and value are required")

    CardEventLog(_shared_root()).append(
        CardEvent(
            card_id=task_id,
            action="link",
            link_key=key,
            link_value=value,
            writer=args.get("agent", "") or "",
        )
    )
    return _json_response({"linked": True, "task_id": task_id, "key": key, "value": value})


HANDLERS: dict = {
    "coord_describe": _handle_coord_describe,
    "coord_label": _handle_coord_label,
    "coord_link": _handle_coord_link,
}
