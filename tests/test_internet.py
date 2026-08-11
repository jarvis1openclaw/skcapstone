"""Tests for the Agent Reach-backed internet facade."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from skcapstone import internet


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


def test_doctor_parses_agent_reach_json(monkeypatch) -> None:
    """doctor() should invoke agent-reach and parse its JSON output."""
    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = json.dumps({"web": {"status": "ok"}})
    completed.stderr = ""

    monkeypatch.setattr(internet.shutil, "which", lambda name: f"/bin/{name}")
    run = MagicMock(return_value=completed)
    monkeypatch.setattr(internet.subprocess, "run", run)

    assert internet.doctor() == {"web": {"status": "ok"}}
    run.assert_called_once()
    assert run.call_args.args[0] == ["/bin/agent-reach", "doctor", "--json"]


def test_search_calls_mcporter(monkeypatch) -> None:
    """search() should use the Agent Reach Exa/mcporter route."""
    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = "Title: Example\nURL: https://example.com"
    completed.stderr = ""

    monkeypatch.setattr(internet.shutil, "which", lambda name: f"/bin/{name}")
    run = MagicMock(return_value=completed)
    monkeypatch.setattr(internet.subprocess, "run", run)

    result = internet.search("agent reach", limit=3)

    assert result.kind == "search"
    assert result.query == "agent reach"
    assert result.backend == "agent-reach:exa/mcporter"
    assert "Example" in result.content
    assert run.call_args.args[0] == [
        "/bin/mcporter",
        "call",
        "exa.web_search_exa",
        "query=agent reach",
        "numResults=3",
    ]


def test_read_url_uses_jina_reader(monkeypatch) -> None:
    """read_url() should fetch through Jina Reader."""
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return _Response(b"Readable page")

    monkeypatch.setattr(internet.urllib.request, "urlopen", fake_urlopen)

    result = internet.read_url("https://example.com/page", timeout=7)

    assert result.kind == "read"
    assert result.url == "https://example.com/page"
    assert result.content == "Readable page"
    assert seen == {
        "url": "https://r.jina.ai/https://example.com/page",
        "timeout": 7,
    }


def test_read_url_falls_back_to_direct_fetch(monkeypatch) -> None:
    """read_url() should fall back when Jina Reader is unavailable."""
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        if req.full_url.startswith("https://r.jina.ai/"):
            raise internet.urllib.error.HTTPError(
                req.full_url,
                403,
                "Forbidden",
                hdrs=None,
                fp=None,
            )
        return _Response(b"Direct page")

    monkeypatch.setattr(internet.urllib.request, "urlopen", fake_urlopen)

    result = internet.read_url("https://example.com/page")

    assert result.content == "Direct page"
    assert result.backend == "agent-reach:jina-reader+direct-fallback"
    assert result.metadata["fallback"] == "direct"
    assert calls == [
        "https://r.jina.ai/https://example.com/page",
        "https://example.com/page",
    ]


def test_read_url_rejects_non_http_url() -> None:
    """Only absolute HTTP(S) URLs should be accepted."""
    with pytest.raises(internet.InternetError):
        internet.read_url("file:///etc/passwd")


def test_store_result_writes_memory(tmp_agent_home) -> None:
    """store_result() should persist internet output with provenance tags."""
    result = internet.InternetResult(
        kind="search",
        query="test query",
        backend="agent-reach:exa/mcporter",
        content="search body",
    )

    entry = internet.store_result(tmp_agent_home, result, tags=["research"])

    assert entry.source == "internet:agent-reach"
    assert "internet" in entry.tags
    assert "agent-reach" in entry.tags
    assert "research" in entry.tags
    assert entry.metadata["query"] == "test query"
