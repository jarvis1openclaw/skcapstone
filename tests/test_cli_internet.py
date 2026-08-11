"""Tests for ``skcapstone internet`` CLI commands."""

from __future__ import annotations

import json

from click.testing import CliRunner

from skcapstone.cli import main
from skcapstone.internet import InternetResult


def test_internet_doctor_json(monkeypatch) -> None:
    """doctor --json-out should print machine-readable status."""
    monkeypatch.setattr(
        "skcapstone.internet.doctor",
        lambda timeout=30: {"web": {"status": "ok"}},
    )

    result = CliRunner().invoke(main, ["internet", "doctor", "--json-out"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"web": {"status": "ok"}}


def test_internet_search_json(monkeypatch) -> None:
    """search --json-out should include fetched content and provenance."""
    monkeypatch.setattr(
        "skcapstone.internet.search",
        lambda query, limit=5, timeout=45: InternetResult(
            kind="search",
            query=query,
            backend="agent-reach:exa/mcporter",
            content="result body",
            metadata={"limit": limit},
        ),
    )

    result = CliRunner().invoke(
        main,
        ["internet", "search", "sovereign agents", "--limit", "2", "--json-out"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["query"] == "sovereign agents"
    assert payload["metadata"]["limit"] == 2
    assert payload["content"] == "result body"


def test_internet_read_store(monkeypatch, tmp_agent_home) -> None:
    """read --store should persist output and print the memory id."""
    result_obj = InternetResult(
        kind="read",
        url="https://example.com",
        backend="agent-reach:jina-reader",
        content="page body",
    )
    monkeypatch.setattr("skcapstone.internet.read_url", lambda url, timeout=30: result_obj)

    result = CliRunner().invoke(
        main,
        [
            "internet",
            "read",
            "https://example.com",
            "--store",
            "--home",
            str(tmp_agent_home),
        ],
    )

    assert result.exit_code == 0
    assert "page body" in result.output
    assert "Stored memory" in result.output
