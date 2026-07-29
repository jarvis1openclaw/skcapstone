"""Operator loop, report-only v1. The operator's first heartbeat.

One pass: check the freeze first, observe every adapter, triage into a brief,
route to the cheap or the decision brain, let the agent propose, and report.
This version WRITES NOTHING to the fleet: proposals are reported, never applied.
The act channel arrives with the approval surface (O5); until then the loop is a
pure observer that tells the human what it would do.
"""

from __future__ import annotations

from typing import Any, Callable

from ..fleet import store
from ..fleet.paths import default_paths
from . import brain, brief, fleet_adapter

#: The registered app adapters: name -> observe callable(paths, now_iso) -> {conditions}.
#: The fleet is the reference adapter; app adapters (skchat, ...) register here.
ADAPTERS: dict[str, Callable[..., dict]] = {"fleet": fleet_adapter.fleet_observe}


#: Default agent: report-only proposes nothing. The real hybrid-brain proposer
#: (ornith/Claude via skgateway) is injected by the CLI/scheduler card; the
#: default keeps run_once safe and testable with no live model call.
def _no_proposals(brief_dict: dict, route: str) -> list[dict]:
    return []


def run_once(
    paths=None,
    *,
    now_iso: str,
    problem_types: set[str] | None = None,
    propose: Callable[[dict, str], list[dict]] = _no_proposals,
    emit: Callable[[str], Any] = print,
) -> dict:
    """Run one report-only operator pass. Never writes to the fleet.

    Returns a dict describing the pass: {frozen, brief, route, proposals, report}.
    """
    paths = paths or default_paths()

    # Freeze wins, always, and first: a frozen fleet gets no observation and no
    # action, only a stand-down report.
    if store.is_frozen(paths):
        report = "operator: freeze is on, standing down. No observation, no action."
        emit(report)
        return {"frozen": True, "brief": None, "route": None, "proposals": [], "report": report}

    ptypes = problem_types if problem_types is not None else set(fleet_adapter.PROBLEM_WHEN_TRUE)
    observations = {
        name: fn(paths, now_iso).get("conditions", []) for name, fn in ADAPTERS.items()
    }
    the_brief = brief.build_brief(observations, ptypes)
    route = brain.route_brain(the_brief)
    proposals = list(propose(the_brief, route))
    report = brain.format_report(the_brief, proposals)
    emit(report)
    return {
        "frozen": False,
        "brief": the_brief,
        "route": route,
        "proposals": proposals,
        "report": report,
    }
