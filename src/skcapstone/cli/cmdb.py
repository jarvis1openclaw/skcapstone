"""CMDB / asset management CLI commands.

The CMDB had a dashboard surface and no CLI, so the only way to populate or
inspect assets was the web UI. That also left
``cronjob-skbrain-cmdb-reconcile.json`` in the skbrain pack calling
``skcapstone cmdb reconcile``, a verb that did not exist.

``scan`` and ``reconcile`` are read-only unless ``--apply`` is passed. A scan
that writes by default is a scan nobody can safely run twice.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import click

from ._common import SHARED_ROOT, console

_STATUS_COLOR = {
    "operational": "green",
    "degraded": "yellow",
    "down": "red",
    "retired": "dim",
}


def _manager():
    from skcoord.cmdb import CMDBManager

    return CMDBManager(Path(SHARED_ROOT).expanduser())


def _discovery():
    """Import skcoord.discovery, or say plainly which package is too old.

    The discovery collectors ship in skcoord. Without this the failure is a
    bare ImportError naming a module the operator has never heard of.
    """
    try:
        import skcoord.discovery as mod
    except ImportError as exc:  # pragma: no cover - depends on installed version
        raise click.ClickException(
            "skcoord.discovery is missing: this skcoord is too old for "
            "`cmdb scan/reconcile/drift`. Upgrade skcoord, then retry. "
            f"({exc})"
        ) from exc
    return mod


def _build_runners(hosts: tuple[str, ...], local: bool):
    """Turn --host/--local into runners. No host means no observation."""
    disc = _discovery()

    runners = []
    if local:
        runners.append(disc.LocalRunner())
    for spec in hosts:
        name, _, target = spec.partition("=")
        runners.append(disc.SSHRunner(host=name, target=target or name))
    return runners


def register_cmdb_commands(main: click.Group) -> None:
    """Register the cmdb command group."""

    @main.group()
    def cmdb():
        """CMDB - configuration items, discovery, and drift."""

    # ── cmdb list ─────────────────────────────────────────────────────

    @cmdb.command("list")
    @click.option("--type", "ci_type", default=None, help="Filter by CI type.")
    @click.option("--tag", default=None, help="Filter by tag.")
    @click.option("--json", "as_json", is_flag=True, help="Emit the CI list as JSON.")
    def cmdb_list(ci_type, tag, as_json):
        """List configuration items."""
        cis = _manager().list_cis(ci_type)
        if tag:
            cis = [c for c in cis if tag in (c.tags or [])]

        if as_json:
            click.echo(_json.dumps([c.model_dump() for c in cis], indent=2, default=str))
            return

        if not cis:
            console.print("[dim]No configuration items.[/dim]")
            return
        console.print(f"\n[bold]Configuration Items[/bold] ({len(cis)})")
        for ci in cis:
            color = _STATUS_COLOR.get(ci.status, "white")
            node = f" @ {ci.node}" if ci.node else ""
            console.print(f"  [{color}]{ci.status:<12}[/{color}] {ci.ci_type:<10} {ci.name}{node}")

    # ── cmdb show ─────────────────────────────────────────────────────

    @cmdb.command("show")
    @click.argument("ci_id")
    @click.option("--json", "as_json", is_flag=True, help="Emit the CI as JSON.")
    def cmdb_show(ci_id, as_json):
        """Show one configuration item, folded from its event log."""
        ci = _manager().get_ci(ci_id)
        if ci is None:
            raise click.ClickException(f"CI not found: {ci_id}")

        if as_json:
            click.echo(_json.dumps(ci.model_dump(), indent=2, default=str))
            return

        console.print(f"\n[bold]{ci.name}[/bold]  [dim]{ci.id}[/dim]")
        console.print(f"  type:    {ci.ci_type}")
        console.print(f"  status:  {ci.status}")
        if ci.node:
            console.print(f"  node:    {ci.node}")
        if ci.description:
            console.print(f"  desc:    {ci.description}")
        if ci.tags:
            console.print(f"  tags:    {', '.join(ci.tags)}")
        for key, value in sorted(ci.attributes.items()):
            console.print(f"  [dim]{key}[/dim]: {value}")
        for rel in ci.relationships:
            console.print(f"  [cyan]{rel.rel_type}[/cyan] -> {rel.target}")

    # ── cmdb scan ─────────────────────────────────────────────────────

    @cmdb.command("scan")
    @click.option(
        "--host",
        multiple=True,
        help="Observe a remote node over ssh. NAME or NAME=ssh-target. Repeatable.",
    )
    @click.option("--local/--no-local", default=True, help="Observe this machine.")
    @click.option(
        "--declared/--no-declared", default=True, help="Read fleet objects, registry, agents."
    )
    @click.option("--json", "as_json", is_flag=True, help="Emit discovered CIs as JSON.")
    def cmdb_scan(host, local, declared, as_json):
        """Scan declared and observed state. Read-only: never writes.

        Use `cmdb reconcile --apply` to persist what a scan finds.
        """
        run_scan = _discovery().scan

        runners = _build_runners(host, local)
        found = run_scan(
            Path(SHARED_ROOT).expanduser(), runners=runners, include_declared=declared
        )

        if as_json:
            click.echo(
                _json.dumps(
                    [
                        {
                            "ci_id": d.ci_id,
                            "ci_type": d.ci_type,
                            "name": d.name,
                            "source": d.source,
                            "observed": d.observed,
                            "node": d.node,
                            "attributes": d.attributes,
                            "tags": list(d.tags),
                            "relationships": [
                                {"rel_type": r, "target": t} for r, t in d.relationships
                            ],
                        }
                        for d in found
                    ],
                    indent=2,
                    default=str,
                )
            )
            return

        observed = sum(1 for d in found if d.observed)
        console.print(
            f"\n[bold]Discovered[/bold] {len(found)} CIs "
            f"([green]{observed} observed[/green], {len(found) - observed} declared only)"
        )
        if not runners:
            console.print("[yellow]  No runners: nothing was observed, only specs read.[/yellow]")
        for d in found:
            mark = "[green]*[/green]" if d.observed else " "
            console.print(f"  {mark} {d.ci_type:<10} {d.name:<40} [dim]{d.source}[/dim]")

    # ── cmdb reconcile ────────────────────────────────────────────────

    @cmdb.command("reconcile")
    @click.option("--host", multiple=True, help="Observe a remote node over ssh. Repeatable.")
    @click.option("--local/--no-local", default=True, help="Observe this machine.")
    @click.option("--apply", is_flag=True, help="Write the changes. Off by default.")
    @click.option("--agent", default="cmdb-discovery", help="Writer name for the event log.")
    @click.option("--json", "as_json", is_flag=True, help="Emit the report as JSON.")
    def cmdb_reconcile(host, local, apply, agent, as_json):
        """Converge the CMDB on discovered state. Additive: never deletes."""
        disc = _discovery()
        run_scan, run_reconcile = disc.scan, disc.reconcile

        mgr = _manager()
        found = run_scan(Path(SHARED_ROOT).expanduser(), runners=_build_runners(host, local))
        report = run_reconcile(mgr, found, agent=agent, apply=apply)

        if as_json:
            click.echo(_json.dumps(report.as_dict(), indent=2, default=str))
            return

        mode = "[green]applied[/green]" if apply else "[yellow]dry run[/yellow]"
        console.print(f"\n[bold]CMDB reconcile[/bold] ({mode})")
        console.print(f"  created:   {len(report.created)}")
        console.print(f"  updated:   {len(report.updated)}")
        console.print(f"  unchanged: {len(report.unchanged)}")
        console.print(f"  orphans:   {len(report.orphans)}")
        for ci_id in report.created[:20]:
            console.print(f"    [green]+[/green] {ci_id}")
        for ci_id, keys in list(report.updated.items())[:20]:
            console.print(f"    [yellow]~[/yellow] {ci_id}: {', '.join(keys)}")
        for ci_id in report.orphans[:20]:
            console.print(f"    [dim]?[/dim] {ci_id} (not seen; left in place)")
        if not apply:
            console.print("\n[dim]Nothing was written. Re-run with --apply.[/dim]")

    # ── cmdb drift ────────────────────────────────────────────────────

    @cmdb.command("drift")
    @click.option("--host", multiple=True, help="Observe a remote node over ssh. Repeatable.")
    @click.option("--local/--no-local", default=True, help="Observe this machine.")
    @click.option("--json", "as_json", is_flag=True, help="Emit findings as JSON.")
    def cmdb_drift(host, local, as_json):
        """Where the specs and the machines disagree."""
        disc = _discovery()
        run_scan, run_drift = disc.scan, disc.drift

        runners = _build_runners(host, local)
        mgr = _manager()
        found = run_scan(Path(SHARED_ROOT).expanduser(), runners=runners)
        findings = run_drift(found, mgr)

        if as_json:
            click.echo(_json.dumps([f.as_dict() for f in findings], indent=2, default=str))
            return

        if not runners:
            console.print(
                "[yellow]No runners: without observation, drift cannot be measured.[/yellow]"
            )
        if not findings:
            console.print("[green]No drift.[/green]")
            return
        console.print(f"\n[bold]Drift[/bold] ({len(findings)} findings)")
        for finding in findings:
            color = "red" if finding.kind == "declared_not_observed" else "yellow"
            console.print(f"  [{color}]{finding.kind}[/{color}] {finding.ci_id}")
            console.print(f"    [dim]{finding.detail}[/dim]")

    # ── cmdb impact ───────────────────────────────────────────────────

    @cmdb.command("impact")
    @click.argument("ci_id")
    @click.option("--json", "as_json", is_flag=True, help="Emit the analysis as JSON.")
    def cmdb_impact(ci_id, as_json):
        """What breaks if this CI does, plus its open incidents."""
        result = _manager().impact_analysis(ci_id)
        if result.get("error"):
            raise click.ClickException(f"{result['error']}: {ci_id}")

        if as_json:
            click.echo(_json.dumps(result, indent=2, default=str))
            return

        ci = result["ci"]
        console.print(f"\n[bold]Impact: {ci['name']}[/bold]  [dim]{ci['id']}[/dim]")
        dependents = result["dependents"]
        console.print(f"  dependents: {len(dependents)}")
        for dep in dependents:
            console.print(f"    [cyan]{dep['rel']}[/cyan] {dep['ci_type']} {dep['name']}")
        incidents = result["open_incidents"]
        console.print(f"  open incidents: {len(incidents)}")
        for inc in incidents:
            console.print(f"    [red]{inc['severity']}[/red] {inc['id']} {inc['title']}")
