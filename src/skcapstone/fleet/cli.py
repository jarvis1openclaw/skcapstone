"""The skfleet CLI: fleet inventory, cordon, freeze, explain, sknoded.

Available standalone as `skfleet` and as `skcapstone fleet ...`.
"""

from __future__ import annotations

import json as jsonlib

import click

from . import admission, node_controller, store
from . import sknoded as sknoded_mod
from .explain import explain as explain_kind
from .paths import default_paths, self_node_name


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
@click.option("--once", is_flag=True, help="One self-report pass, then exit.")
@click.option("--interval", default=sknoded_mod.HEARTBEAT_INTERVAL_S, show_default=True)
def sknoded_cmd(once: bool, interval: int) -> None:
    """Run the node agent self-report loop for this machine."""
    sknoded_mod.main_loop(default_paths(), self_node_name(), interval=interval, once=once)


@fleet.command("admit")
@click.argument("name")
@click.option("--label", "labels", multiple=True, help="k=v, repeatable.")
@click.option("--preset", is_flag=True, help="Use the known-node preset labels/taints.")
@click.option("--bootstrap", is_flag=True, help="First node: admit without a join request.")
def admit_cmd(name: str, labels: tuple[str, ...], preset: bool, bootstrap: bool) -> None:
    """Admit a joining node, minting its node object."""
    label_map = dict(part.split("=", 1) for part in labels) if labels else None
    try:
        spec = admission.admit(default_paths(), name, writer=_operator(),
                               labels=label_map, preset=preset, bootstrap=bootstrap)
    except LookupError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"admitted {name} (generation {spec['generation']})")


def register_fleet_commands(main: click.Group) -> None:
    """Register the fleet group on the skcapstone CLI."""
    main.add_command(fleet)


def main() -> None:
    """Console script entry point (skfleet)."""
    fleet()
