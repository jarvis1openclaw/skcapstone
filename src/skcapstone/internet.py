"""Internet capability facade backed by Agent Reach upstream tools.

This module intentionally shells out to stable user-facing CLIs instead of
importing Agent Reach internals. Agent Reach remains the selector/doctor layer;
SKCapstone owns policy, memory capture, and user-facing command shape.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class InternetError(RuntimeError):
    """Raised when an internet capability cannot complete."""


@dataclass
class InternetResult:
    """Fetched internet content plus provenance."""

    kind: str
    content: str
    backend: str
    query: str | None = None
    url: str | None = None
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "kind": self.kind,
            "backend": self.backend,
            "query": self.query,
            "url": self.url,
            "fetched_at": self.fetched_at,
            "content": self.content,
            "metadata": self.metadata,
        }


def _require_command(name: str) -> str:
    """Resolve a command from PATH or raise a helpful error."""
    path = shutil.which(name)
    if not path:
        raise InternetError(
            f"{name} is not installed or not on PATH. "
            "Run `agent-reach doctor` for setup guidance."
        )
    return path


def _run(
    cmd: list[str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and normalize failure into InternetError."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InternetError(f"{cmd[0]} failed: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise InternetError(f"{cmd[0]} exited {result.returncode}: {detail}")
    return result


def doctor(timeout: int = 30) -> dict[str, Any]:
    """Return Agent Reach channel status as a dict.

    Args:
        timeout: Maximum command runtime in seconds.

    Returns:
        Parsed ``agent-reach doctor --json`` output.
    """
    agent_reach = _require_command("agent-reach")
    result = _run([agent_reach, "doctor", "--json"], timeout=timeout)
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InternetError("agent-reach doctor returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise InternetError("agent-reach doctor returned a non-object JSON value")
    return parsed


def search(query: str, limit: int = 5, timeout: int = 45) -> InternetResult:
    """Search the web via Agent Reach's Exa/mcporter route.

    Args:
        query: Search query.
        limit: Maximum result count.
        timeout: Maximum command runtime in seconds.

    Returns:
        Search output with provenance.
    """
    cleaned = query.strip()
    if not cleaned:
        raise InternetError("query is required")
    if limit < 1:
        raise InternetError("limit must be at least 1")

    mcporter = _require_command("mcporter")
    result = _run(
        [
            mcporter,
            "call",
            "exa.web_search_exa",
            f"query={cleaned}",
            f"numResults={limit}",
        ],
        timeout=timeout,
    )
    return InternetResult(
        kind="search",
        query=cleaned,
        backend="agent-reach:exa/mcporter",
        content=result.stdout.strip(),
        metadata={"limit": limit},
    )


def read_url(url: str, timeout: int = 30) -> InternetResult:
    """Read a public URL through Jina Reader.

    Args:
        url: HTTP or HTTPS URL to read.
        timeout: Maximum request runtime in seconds.

    Returns:
        Page text with provenance.
    """
    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise InternetError("url must be an absolute http(s) URL")

    reader_url = f"https://r.jina.ai/{cleaned}"
    req = urllib.request.Request(
        reader_url,
        headers={"User-Agent": "skcapstone-internet/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        backend = "agent-reach:jina-reader"
        metadata = {"reader_url": reader_url}
    except (OSError, urllib.error.HTTPError) as reader_exc:
        try:
            direct_req = urllib.request.Request(
                cleaned,
                headers={"User-Agent": "skcapstone-internet/1.0"},
            )
            with urllib.request.urlopen(direct_req, timeout=timeout) as resp:
                content = resp.read().decode("utf-8", errors="replace")
        except OSError as direct_exc:
            raise InternetError(
                f"Jina Reader failed: {reader_exc}; direct fetch failed: {direct_exc}"
            ) from direct_exc
        backend = "agent-reach:jina-reader+direct-fallback"
        metadata = {
            "reader_url": reader_url,
            "fallback": "direct",
            "reader_error": str(reader_exc),
        }

    return InternetResult(
        kind="read",
        url=cleaned,
        backend=backend,
        content=content.strip(),
        metadata=metadata,
    )


def store_result(
    home: Path,
    result: InternetResult,
    tags: list[str] | None = None,
    importance: float = 0.55,
) -> Any:
    """Store fetched internet content as an SKMemory entry.

    Args:
        home: Agent home directory.
        result: InternetResult to persist.
        tags: Optional memory tags.
        importance: Memory importance score.

    Returns:
        The created MemoryEntry.
    """
    from .memory_engine import store

    label = result.query or result.url or result.kind
    content = (
        f"Internet {result.kind}: {label}\n"
        f"Backend: {result.backend}\n"
        f"Fetched at: {result.fetched_at}\n\n"
        f"{result.content}"
    )
    memory_tags = ["internet", "agent-reach", result.kind]
    if tags:
        memory_tags.extend(tags)

    return store(
        home,
        content,
        tags=memory_tags,
        source="internet:agent-reach",
        importance=importance,
        metadata={
            "internet_kind": result.kind,
            "backend": result.backend,
            "query": result.query,
            "url": result.url,
            "fetched_at": result.fetched_at,
            **result.metadata,
        },
    )
