"""Internet command group backed by Agent Reach."""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.panel import Panel

from ._common import AGENT_HOME, console


def register_internet_commands(main: click.Group) -> None:
    """Register ``skcapstone internet`` commands."""

    @main.group("internet")
    def internet_group() -> None:
        """Internet search/read facade using Agent Reach routes."""

    @internet_group.command("doctor")
    @click.option("--json-out", is_flag=True, help="Output raw JSON.")
    @click.option("--timeout", default=30, show_default=True, help="Timeout in seconds.")
    def internet_doctor(json_out: bool, timeout: int) -> None:
        """Show Agent Reach channel status."""
        from ..internet import InternetError, doctor

        try:
            status = doctor(timeout=timeout)
        except InternetError as exc:
            raise click.ClickException(str(exc)) from exc

        if json_out:
            click.echo(json.dumps(status, indent=2, ensure_ascii=False))
            return

        ok = sum(1 for item in status.values() if item.get("status") == "ok")
        warn = sum(1 for item in status.values() if item.get("status") == "warn")
        off = sum(1 for item in status.values() if item.get("status") in {"off", "error"})
        console.print(
            Panel(
                f"[green]{ok} ok[/]  [yellow]{warn} warn[/]  [red]{off} off/error[/]",
                title="Internet Capability Status",
                border_style="cyan",
            )
        )
        for key, item in sorted(status.items()):
            state = item.get("status", "?")
            backend = item.get("active_backend") or "-"
            name = item.get("name", key)
            console.print(f"[bold]{key}[/] [{state}] {name}  [dim]{backend}[/]")

    @internet_group.command("search")
    @click.argument("query")
    @click.option("--limit", "-n", default=5, show_default=True, help="Result count.")
    @click.option("--timeout", default=45, show_default=True, help="Timeout in seconds.")
    @click.option("--json-out", is_flag=True, help="Output structured JSON.")
    @click.option("--store", is_flag=True, help="Store fetched output in SKMemory.")
    @click.option("--home", default=AGENT_HOME, type=click.Path(), help="Agent home.")
    @click.option("--tag", "tags", multiple=True, help="Additional memory tag.")
    @click.option(
        "--importance",
        default=0.55,
        show_default=True,
        help="Memory importance when --store is used.",
    )
    def internet_search(
        query: str,
        limit: int,
        timeout: int,
        json_out: bool,
        store: bool,
        home: str,
        tags: tuple[str, ...],
        importance: float,
    ) -> None:
        """Search the web via Exa/mcporter."""
        from ..internet import InternetError, search, store_result

        try:
            result = search(query=query, limit=limit, timeout=timeout)
            memory_id = None
            if store:
                entry = store_result(
                    Path(home).expanduser(),
                    result,
                    tags=list(tags),
                    importance=importance,
                )
                memory_id = entry.memory_id
        except InternetError as exc:
            raise click.ClickException(str(exc)) from exc

        if json_out:
            payload = result.to_dict()
            if memory_id:
                payload["memory_id"] = memory_id
            click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        console.print(result.content)
        if memory_id:
            console.print(f"\n[green]Stored memory:[/] {memory_id}")

    @internet_group.command("read")
    @click.argument("url")
    @click.option("--timeout", default=30, show_default=True, help="Timeout in seconds.")
    @click.option("--json-out", is_flag=True, help="Output structured JSON.")
    @click.option("--store", is_flag=True, help="Store fetched output in SKMemory.")
    @click.option("--home", default=AGENT_HOME, type=click.Path(), help="Agent home.")
    @click.option("--tag", "tags", multiple=True, help="Additional memory tag.")
    @click.option(
        "--importance",
        default=0.55,
        show_default=True,
        help="Memory importance when --store is used.",
    )
    def internet_read(
        url: str,
        timeout: int,
        json_out: bool,
        store: bool,
        home: str,
        tags: tuple[str, ...],
        importance: float,
    ) -> None:
        """Read a public URL through Jina Reader."""
        from ..internet import InternetError, read_url, store_result

        try:
            result = read_url(url=url, timeout=timeout)
            memory_id = None
            if store:
                entry = store_result(
                    Path(home).expanduser(),
                    result,
                    tags=list(tags),
                    importance=importance,
                )
                memory_id = entry.memory_id
        except InternetError as exc:
            raise click.ClickException(str(exc)) from exc

        if json_out:
            payload = result.to_dict()
            if memory_id:
                payload["memory_id"] = memory_id
            click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        console.print(result.content)
        if memory_id:
            console.print(f"\n[green]Stored memory:[/] {memory_id}")
