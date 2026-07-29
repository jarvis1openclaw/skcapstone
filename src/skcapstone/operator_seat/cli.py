"""The skoperator CLI: Atlas's control surface.

Available as `skoperator`. Commands:
  run       one operator pass (report-only by default; reasons via the hybrid brain)
  pending   list parked decisions awaiting a human
  decide    approve or reject a parked decision (human only)
  status    freeze state
  freeze / unfreeze   toggle the kill switch (human only)

Report-only by default. With --execute, auto-normal proposals are applied via
the fleet act verb (signed spec annotations); majors still park for approval and
freeze always wins.
"""

from __future__ import annotations

import functools
import os
from datetime import datetime, timezone

import click

from ..fleet import store
from ..fleet.paths import default_paths
from . import decisions, fleet_adapter, loop, proposer


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gateway() -> str:
    return os.environ.get("SKOPERATOR_GATEWAY", "http://localhost:18780/v1")


def _decisions_dir(paths) -> str:
    return str(paths.root / "decisions")


def _human_writer() -> store.Writer:
    # A CLI invocation is a human at a terminal, never the autonomous seat.
    return store.Writer(
        role="operator", node="cli", identity=store.writer_identity() or "human", agent_seat=False
    )


@click.group(name="operator")
def operator() -> None:
    """Atlas, the SKWorld operator seat."""


@operator.command("run")
@click.option(
    "--execute",
    is_flag=True,
    default=False,
    help="Enable actuation: apply auto-normal fixes via the fleet act verb (majors still park).",
)
def run_cmd(execute: bool) -> None:
    """One operator pass: observe, reason, report. Report-only by default."""
    paths = default_paths()

    now = _now_iso()

    def _propose(brief, route):
        if brief.get("quiet"):
            return []
        model = "ornith-1.0-35b" if route == "ornith" else "sk-default"
        chat = functools.partial(proposer.default_chat, base_url=_gateway(), model=model)
        return proposer.propose(brief, fleet_adapter.fleet_explain(), chat=chat)

    apply_fn = None
    if execute:

        def apply_fn(prop, cls):  # noqa: E731 - the fleet act verb, only when --execute
            return fleet_adapter.fleet_act(paths, prop, cls, now_iso=now)

    res = loop.run_once(
        paths,
        now_iso=now,
        propose=_propose,
        decisions_dir=_decisions_dir(paths),
        apply_fn=apply_fn,
        execute=execute,
        emit=click.echo,
    )
    if res.get("outcomes"):
        click.echo(f"({len(res['outcomes'])} proposal(s); parked escalations await approval)")


@operator.command("pending")
def pending_cmd() -> None:
    """List decisions parked for a human."""
    rows = decisions.list_pending(_decisions_dir(default_paths()))
    if not rows:
        click.echo("no pending decisions")
        return
    for d in rows:
        opts = "; ".join(f"[{i}] {o.get('action')}" for i, o in enumerate(d.get("options", [])))
        click.echo(f"{d['id']}  {opts}")


@operator.command("decide")
@click.argument("decision_id")
@click.option("--approve/--reject", required=True)
@click.option("--choice", type=int, default=None, help="Option index when several are offered.")
def decide_cmd(decision_id: str, approve: bool, choice: int | None) -> None:
    """Approve or reject a parked decision (human only)."""
    out = decisions.resolve(
        _decisions_dir(default_paths()),
        decision_id,
        approve=approve,
        choice=choice,
        by="human",
        resolved_iso=_now_iso(),
    )
    click.echo(f"{decision_id} -> {out['status']}")


@operator.command("status")
def status_cmd() -> None:
    """Show the freeze state."""
    frozen = store.is_frozen(default_paths())
    click.echo("FROZEN (Atlas stands down)" if frozen else "active (freeze off)")


@operator.command("freeze")
@click.option("--reason", default="", help="Why the fleet is being frozen.")
def freeze_cmd(reason: str) -> None:
    """Freeze the fleet: Atlas halts all actuation (human only)."""
    store.set_frozen(default_paths(), True, writer=_human_writer(), reason=reason)
    click.echo("frozen: Atlas will stand down until unfrozen")


@operator.command("unfreeze")
def unfreeze_cmd() -> None:
    """Lift the freeze (human only)."""
    store.set_frozen(default_paths(), False, writer=_human_writer())
    click.echo("unfrozen: Atlas resumes")


def main() -> None:
    operator()


if __name__ == "__main__":
    main()
