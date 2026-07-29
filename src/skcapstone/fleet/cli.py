"""The skfleet CLI: fleet inventory, cordon, freeze, explain, sknoded.

Available standalone as `skfleet` and as `skcapstone fleet ...`.
"""

from __future__ import annotations

import json as jsonlib
from datetime import datetime, timezone

import click

from . import (
    admission,
    alerts,
    cron_controller,
    modelserver_controller,
    node_controller,
    service_controller,
    store,
)
from . import services as services_mod
from . import sknoded as sknoded_mod
from .explain import explain as explain_kind
from .paths import default_paths, self_node_name


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _operator() -> store.Writer:
    return store.Writer(role="operator", node=self_node_name(), identity=store.writer_identity())


@click.group(name="fleet")
def fleet() -> None:
    """SKWorld fleet control plane (skfleet)."""


@fleet.command("nodes")
def nodes_cmd() -> None:
    """List all fleet nodes with phase, labels, and capacity."""
    for v in node_controller.node_views(default_paths()):
        labels = ",".join(f"{k}={val}" for k, val in sorted(v.labels.items()))
        cordoned = " CORDONED" if v.cordoned else ""
        age = "never" if v.heartbeat_age_s is None else f"{int(v.heartbeat_age_s)}s"
        click.echo(
            f"{v.name}\t{v.phase}{cordoned}\t[{labels}]\t"
            f"cores={v.capacity.get('cores', '?')} "
            f"ram={v.capacity.get('ram_gb', '?')}GB "
            f"disk={v.capacity.get('disk_gb', '?')}GB\tbeat={age}"
        )


@fleet.command("describe")
@click.argument("kind")
@click.argument("name")
def describe_cmd(kind: str, name: str) -> None:
    """Show the merged object (spec + placement + statuses) as JSON."""
    payload = store.merged(default_paths(), kind, name)
    if payload is None:
        raise click.ClickException(f"no such object: {kind}/{name}")
    click.echo(jsonlib.dumps(payload, indent=2, sort_keys=True))


@fleet.command("placements")
@click.option("--kind", "kind", default=None, help="Filter by kind (e.g. job, service).")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def placements_cmd(kind: str | None, as_json: bool) -> None:
    """Show current placements with the scheduler's reason for each decision."""
    records = store.list_placements(default_paths(), kind)
    if as_json:
        click.echo(jsonlib.dumps(records, indent=2, sort_keys=True))
        return
    if not records:
        click.echo("no placements")
        return
    for r in records:
        click.echo(
            f"{r['kind'].lower()}/{r['name']}\t-> {r['node']}\t"
            f"gen={r['placementGeneration']}\t{r['reason']}"
        )


@fleet.command("cordon")
@click.argument("name")
def cordon_cmd(name: str) -> None:
    """Mark a node unschedulable."""
    node_controller.cordon(default_paths(), name, True, writer=_operator())
    click.echo(f"{name} cordoned")


@fleet.command("uncordon")
@click.argument("name")
def uncordon_cmd(name: str) -> None:
    """Mark a node schedulable again."""
    node_controller.cordon(default_paths(), name, False, writer=_operator())
    click.echo(f"{name} uncordoned")


@fleet.command("drain")
@click.argument("name")
def drain_cmd(name: str) -> None:
    """Cordon a node and alert with its residents (manual move in v1)."""
    paths_ = default_paths()
    residents = service_controller.node_residents(paths_, name)
    try:
        node_controller.cordon(paths_, name, True, writer=_operator())
    except LookupError as exc:
        raise click.ClickException(str(exc)) from exc
    names = ", ".join(r["name"] for r in residents) or "none"
    alerts.send_alert(
        f"fleet: drain {name}: cordoned; residents: {names}; "
        f"move them manually (v1 drains never auto-move)",
        level="warn",
    )
    click.echo(f"{name} cordoned (drain)")
    for r in residents:
        click.echo(f"  resident: {r['name']}\tvia={r['via']}\tstate={r['state']}")
    click.echo("manual move required in v1: re-place or migrate each resident, then uncordon")


@fleet.command("explain")
@click.argument("kind", required=False)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def explain_cmd(kind: str | None, as_json: bool) -> None:
    """Describe the fleet object model (kinds, fields, conditions, actions)."""
    try:
        payload = explain_kind(kind)
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(jsonlib.dumps(payload, indent=2, sort_keys=True))
    else:
        click.echo(jsonlib.dumps(payload, indent=2, sort_keys=True))


@fleet.command("freeze")
@click.option("--reason", default="", help="Why the fleet is frozen.")
def freeze_cmd(reason: str) -> None:
    """Halt ALL fleet actuation (services keep running). Kill-switch on."""
    store.set_frozen(default_paths(), True, writer=_operator(), reason=reason)
    click.echo("fleet FROZEN: actuation halted, services untouched")


@fleet.command("unfreeze")
def unfreeze_cmd() -> None:
    """Kill-switch off: actuation resumes."""
    store.set_frozen(default_paths(), False, writer=_operator())
    click.echo("fleet unfrozen")


@fleet.command("sknoded")
@click.option("--once", is_flag=True, help="One self-report + converge pass, then exit.")
@click.option("--interval", default=sknoded_mod.HEARTBEAT_INTERVAL_S, show_default=True)
@click.option(
    "--actuation-interval",
    "actuation_interval",
    default=None,
    type=int,
    help="Seconds between converge passes (default 30).",
)
def sknoded_cmd(once: bool, interval: int, actuation_interval: int | None) -> None:
    """Run the node agent loop (self-report + Phase 3 converge)."""
    sknoded_mod.main_loop(
        default_paths(),
        self_node_name(),
        interval=interval,
        once=once,
        actuation_interval=actuation_interval,
    )


@fleet.command("apply")
@click.option(
    "-f", "--file", "file_path", required=True, type=click.Path(exists=True, dir_okay=False)
)
def apply_cmd(file_path: str) -> None:
    """Write one object spec from a JSON doc {kind, name, labels?, spec}."""
    from pathlib import Path

    try:
        doc = jsonlib.loads(Path(file_path).read_text(encoding="utf-8"))
    except ValueError as exc:
        raise click.ClickException(f"not valid JSON: {exc}") from exc
    kind, name = doc.get("kind"), doc.get("name")
    if not kind or not name:
        raise click.ClickException("doc must carry 'kind' and 'name'")
    spec = doc.get("spec", {})
    if kind == "service":
        try:
            services_mod.normalize_service_spec(spec)
        except services_mod.ServiceSpecError as exc:
            raise click.ClickException(f"invalid service spec: {exc}") from exc
    try:
        payload = store.write_spec(
            default_paths(), kind, name, spec, writer=_operator(), labels=doc.get("labels")
        )
    except store.OwnershipError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"applied {kind}/{name} (generation {payload['generation']})")


@fleet.command("services")
def services_cmd() -> None:
    """List all Services with placement, observed state, and readiness."""
    rows = service_controller.service_rows(default_paths())
    if not rows:
        click.echo("no services")
        return
    for r in rows:
        flags = "".join([" PAUSED" if r.paused else "", " STALE" if r.stale else ""])
        click.echo(
            f"{r.name}\t-> {r.node or 'unplaced'}\t" f"state={r.state}\tready={r.ready}{flags}"
        )


@fleet.command("get")
@click.argument("resource")
def get_cmd(resource: str) -> None:
    """List objects of one kind (currently: cronjobs, modelservers)."""
    if resource == "cronjobs":
        rows = cron_controller.cron_rows(default_paths(), _now_iso())
        if not rows:
            click.echo("no cronjobs")
            return
        click.echo("NAME\tNODE\tSCHEDULE\tENABLED\tLAST\tNEXT\tMISSED")
        for r in rows:
            click.echo(
                f"{r.name}\t{r.node or 'unplaced'}\t{r.schedule}\t{r.enabled}\t"
                f"{r.last_run or 'never'}\t{r.next_run}\t{r.missed}"
            )
        return
    if resource == "modelservers":
        rows = modelserver_controller.modelserver_rows(default_paths(), _now_iso())
        if not rows:
            click.echo("no modelservers")
            return
        click.echo("NAME\tNODE\tPORTS\tSERVING\tVRAM")
        for r in rows:
            ports = ",".join(str(p) for p in r.ports)
            click.echo(f"{r.name}\t{r.node or 'unplaced'}\t{ports}\t{r.serving}\t{r.vram}")
        return
    raise click.ClickException(f"unknown resource: {resource!r} (known: cronjobs, modelservers)")


@fleet.command("reconcile")
def reconcile_cmd() -> None:
    """One ServiceController pass (place-once + failover watch)."""
    out = service_controller.reconcile_once(default_paths(), node=self_node_name())
    click.echo(
        f"placed={len(out['placed'])} kept={len(out['kept'])} "
        f"failovers={len(out['failovers'])} alerted={len(out['alerted'])} "
        f"skipped={len(out['skipped'])}"
    )


@fleet.command("actuation")
@click.argument("name")
@click.option(
    "--enable/--disable",
    "enabled",
    required=True,
    help="Opt this node in or out of actuation (default is report-only).",
)
def actuation_cmd(name: str, enabled: bool) -> None:
    """Toggle sknoded actuation for one node (report-only by default)."""
    try:
        node_controller.set_actuation(default_paths(), name, enabled, writer=_operator())
    except LookupError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{name} actuation {'ENABLED' if enabled else 'disabled (report-only)'}")


@fleet.command("admit")
@click.argument("name")
@click.option("--label", "labels", multiple=True, help="k=v, repeatable.")
@click.option("--preset", is_flag=True, help="Use the known-node preset labels/taints.")
@click.option("--bootstrap", is_flag=True, help="First node: admit without a join request.")
def admit_cmd(name: str, labels: tuple[str, ...], preset: bool, bootstrap: bool) -> None:
    """Admit a joining node, minting its node object."""
    label_map = dict(part.split("=", 1) for part in labels) if labels else None
    try:
        spec = admission.admit(
            default_paths(),
            name,
            writer=_operator(),
            labels=label_map,
            preset=preset,
            bootstrap=bootstrap,
        )
    except LookupError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"admitted {name} (generation {spec['generation']})")


def register_fleet_commands(main: click.Group) -> None:
    """Register the fleet group on the skcapstone CLI."""
    main.add_command(fleet)


def main() -> None:
    """Console script entry point (skfleet)."""
    fleet()
