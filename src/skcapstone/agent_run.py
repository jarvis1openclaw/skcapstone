"""AI next-steps runner: attach an instruction to a card for an agent to execute.

Phase 4 of the SKDashboard. An operator (or the assistant) attaches an
``AgentRun`` to a card; a background runner (pinned to one node) claims it under
a lease, dispatches a local agent to carry out the instruction, and reports
typed activity back onto the card. Everything is append-only ``agent_run_*``
events on the CardStore, folded into ``card.meta.agent_run``.

Safety (rule-based gate by card kind):
- task/epic: propose + dry-run freely; execute produces a draft PR for review.
- incident/problem: propose freely; a real fix lands in review.
- change: the agent may DRAFT (proposed/reviewing) but may only enter
  ``implementing`` after a human/CAB vote to ``approved`` (no self-approval).

Real execution is gated behind ``live_execution`` (default OFF), mirroring the
autopilot canary: the runner claims + plans + reports without spawning a live
agent until explicitly enabled.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .card_store import CardCore, CardStore

logger = logging.getLogger("skcapstone.agent_run")

MODES = ("propose", "dry-run", "execute")
AGENTS = ("lumina", "opus", "jarvis")

# States
QUEUED = "queued"
RUNNING = "running"
NEEDS_REVIEW = "needs-review"
DONE = "done"
FAILED = "failed"

_HOST = socket.gethostname()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Attach + fold
# ---------------------------------------------------------------------------


# GTD store layout (mirrors mcp_tools.gtd_tools._GTD_LISTS; kept local so
# ensure_card reads the store under its own ``home`` param, not a global root).
_GTD_LIST_FILES = {
    "inbox": "inbox.json",
    "next-actions": "next-actions.json",
    "projects": "projects.json",
    "waiting-for": "waiting-for.json",
    "someday-maybe": "someday-maybe.json",
    "archive": "archive.json",
}


def _find_gtd_item(home: Path, item_id: str) -> tuple[Optional[str], Optional[dict]]:
    """Locate a GTD item by id across all list files under ``home``.

    Returns ``(list_name, item)`` or ``(None, None)`` if not found. Reads the
    unified GTD store at ``<home>/coordination/gtd`` (the same flat JSON files
    the skcapstone GTD MCP path and the skos.gtd_ingest sink write).
    """
    gtd = Path(home).expanduser() / "coordination" / "gtd"
    for list_name, fname in _GTD_LIST_FILES.items():
        try:
            items = json.loads((gtd / fname).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            continue
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and item.get("id") == item_id:
                return list_name, item
    return None, None


def ensure_card(home: Path, card_id: str) -> bool:
    """Make sure ``card_id`` exists in the CardStore.

    ITIL records (inc-/prb-/chg-) and GTD next-actions (gtd-) created outside
    the CardStore are lazily materialized into a shadow card here, so AI
    next-steps attach uniformly across tasks, ITIL tickets, and GTD items. The
    shadow card carries a ``meta.origin`` backlink to the source surface.
    """
    store = CardStore(home)
    if store.fold(card_id) is not None:
        return True

    if card_id.startswith("gtd-"):
        item_id = card_id[len("gtd-") :]
        list_name, item = _find_gtd_item(home, item_id)
        if item is None:
            return False
        context = (item.get("context") or "").strip()
        labels = ["gtd"]
        if context:
            labels.append(context)
        store.create(
            CardCore(
                id=card_id,
                kind="task",
                title=(item.get("text") or "").strip() or f"GTD {item_id}",
                description="",
                created_by=item.get("source", "gtd"),
                created_at=item.get("created_at") or _iso(_now()),
                initial_priority=(item.get("priority") or "medium"),
                initial_swimlane="feature",
                initial_labels=labels,
                meta={
                    "origin": {
                        "surface": "gtd",
                        "id": item_id,
                        "list": list_name,
                        "privacy": item.get("privacy", "private"),
                    }
                },
            )
        )
        store.append_event(card_id, "move", "gtd-import", column="backlog")
        return True

    from .card import card_from_change, card_from_incident, card_from_problem
    from .itil import ITILManager

    mgr = ITILManager(Path(home).expanduser())
    card = None
    if card_id.startswith("inc-"):
        rec = next((i for i in mgr.list_incidents() if i.id == card_id), None)
        card = card_from_incident(rec) if rec else None
    elif card_id.startswith("prb-"):
        rec = next((p for p in mgr.list_problems() if p.id == card_id), None)
        card = card_from_problem(rec) if rec else None
    elif card_id.startswith("chg-"):
        rec = next((c for c in mgr.list_changes() if c.id == card_id), None)
        card = card_from_change(rec) if rec else None
    if card is None:
        return False
    store.create(
        CardCore(
            id=card.id,
            kind=card.kind.value,
            title=card.title,
            description=card.description,
            created_by=card.originator,
            created_at=card.created_at or _iso(_now()),
            initial_priority=card.priority,
            initial_swimlane=card.swimlane,
            initial_labels=list(card.labels),
            meta=dict(card.meta),
        )
    )
    store.append_event(card_id, "move", "itil-import", column=card.status.value)
    return True


def request_run(
    home: Path,
    card_id: str,
    instruction: str,
    agent: str = "lumina",
    mode: str = "propose",
    requester: str = "operator",
) -> dict:
    """Attach an AgentRun instruction to a card. Returns the new run summary."""
    if mode not in MODES:
        return {"error": f"invalid mode '{mode}'"}
    if not (instruction or "").strip():
        return {"error": "instruction required"}
    ensure_card(home, card_id)
    store = CardStore(home)
    card = store.fold(card_id)
    if card is None:
        return {"error": "card not found", "id": card_id}
    run_id = "run-" + uuid.uuid4().hex[:10]
    store.append_event(
        card_id,
        "agent_run_request",
        requester,
        run_id=run_id,
        instruction=instruction,
        run_agent=agent,
        mode=mode,
        kind=card.kind.value,
    )
    return {"ok": True, "run_id": run_id, "card_id": card_id, "state": QUEUED}


def current_run(home: Path, card_id: str) -> Optional[dict]:
    """The card's current/latest AgentRun (folded), or None."""
    card = CardStore(home).fold(card_id)
    if card is None:
        return None
    return card.meta.get("agent_run")


def list_queued(home: Path) -> list[dict]:
    """All cards with a queued AgentRun (what the runner claims)."""
    out = []
    store = CardStore(home)
    for card in store.list_cards(include_archived=False):
        run = card.meta.get("agent_run")
        if run and run.get("state") == QUEUED:
            out.append({"card_id": card.id, "kind": card.kind.value, "run": run})
    return out


# ---------------------------------------------------------------------------
# Recommended next-steps (shown by default in the composer)
# ---------------------------------------------------------------------------

# Instant, always-available defaults by card kind. Each is {text, mode}.
_HEURISTIC = {
    "task": [
        {"text": "Draft an implementation plan and list the files to touch.", "mode": "propose"},
        {"text": "Implement it behind a flag, add tests, and open a draft PR.", "mode": "execute"},
        {"text": "Write and run tests for this in a scratch worktree.", "mode": "dry-run"},
    ],
    "bug": [
        {"text": "Reproduce the bug and write a failing test.", "mode": "dry-run"},
        {"text": "Fix the root cause and open a draft PR with the test.", "mode": "execute"},
        {"text": "Investigate and summarize the likely root cause.", "mode": "propose"},
    ],
    "incident": [
        {
            "text": "Investigate the root cause and post findings on the incident.",
            "mode": "propose",
        },
        {"text": "Propose remediation steps (do not apply them yet).", "mode": "propose"},
        {"text": "Draft a KEDB entry with symptoms and a workaround.", "mode": "dry-run"},
    ],
    "problem": [
        {"text": "Analyze the root cause and propose a permanent fix.", "mode": "propose"},
        {"text": "Draft a workaround and a KEDB entry.", "mode": "dry-run"},
        {"text": "Link the related incidents and open a change to fix it.", "mode": "propose"},
    ],
    "change": [
        {
            "text": "Draft the implementation plan and rollback plan (do not implement).",
            "mode": "propose",
        },
        {"text": "Assess the risk and prepare the CAB summary.", "mode": "propose"},
        {
            "text": "Prepare the implementation in a worktree for review after CAB approval.",
            "mode": "dry-run",
        },
    ],
}


# GTD next-actions are often outbound comms (email/DM/call). Execute must never
# auto-send; it drafts for a human to send. See memory outbound-comms-draft-by-default.
_HEURISTIC_GTD = [
    {
        "text": "Clarify this into a concrete next action (and a project if it needs one).",
        "mode": "propose",
    },
    {
        "text": "Draft the outbound message or gather the research for review (do not send).",
        "mode": "dry-run",
    },
    {
        "text": "Prepare the work as a draft for review; never auto-send or auto-complete.",
        "mode": "execute",
    },
]

# Verbs that mean a real outbound side effect. A gtd execute suggesting one of
# these is downgraded to dry-run (draft), so the runner drafts, the human sends.
_SEND_VERBS = (
    "send",
    "publish",
    "post ",
    "email ",
    "e-mail",
    "reply",
    "message ",
    "deliver",
    "submit",
    "dispatch",
    "text ",
    "dm ",
)


def _is_gtd(card) -> bool:
    return ((getattr(card, "meta", None) or {}).get("origin") or {}).get("surface") == "gtd"


def _clamp_gtd_suggestions(suggestions: list[dict]) -> list[dict]:
    """Downgrade any send-like ``execute`` suggestion to ``dry-run`` (draft).

    Keeps GTD execute draft-only: the agent prepares an outbound artifact, a
    human sends it. Non-send execute (e.g. "prepare a draft") is left as-is.
    """
    out = []
    for s in suggestions:
        s = dict(s)
        if s.get("mode") == "execute":
            text = s.get("text", "").lower()
            if any(v in text for v in _SEND_VERBS):
                s["mode"] = "dry-run"
        out.append(s)
    return out


def _heuristic_suggestions(card) -> list[dict]:
    if _is_gtd(card):
        return [dict(s) for s in _HEURISTIC_GTD]
    kind = card.kind.value
    if kind == "task" and "bug" in {label.lower() for label in card.labels}:
        kind = "bug"
    return list(_HEURISTIC.get(kind, _HEURISTIC["task"]))


def suggest_next_steps(
    home: Path, card_id: str, use_llm: bool = True, timeout: float = 12.0
) -> dict:
    """Recommend a few AI next-step options for a card.

    Tries skgateway for card-tailored suggestions; always falls back to instant
    heuristics so the composer is never blank or slow.

    Returns ``{"suggestions": [{"text","mode"}...], "source": "llm"|"heuristic"}``.
    """
    ensure_card(home, card_id)
    store = CardStore(home)
    card = store.fold(card_id)
    if card is None:
        return {"error": "card not found", "suggestions": []}
    heuristics = _heuristic_suggestions(card)
    if not use_llm:
        return {"suggestions": heuristics, "source": "heuristic"}

    try:
        from . import skgateway_client as gw

        recent = "; ".join(a.get("text", "") for a in card.meta.get("comments", [])[-3:])
        gtd_rule = (
            "This is a GTD next-action, which may be an outbound message. NEVER suggest "
            "'execute' that sends, publishes, or delivers anything; drafting for review is "
            "'dry-run'. Reserve 'execute' for preparing a draft only.\n"
            if _is_gtd(card)
            else ""
        )
        prompt = (
            "You suggest next-step instructions an AI agent can execute on a work item. "
            'Return ONLY a JSON array of 3 objects, each {"text": <one concise imperative '
            "instruction>, \"mode\": one of propose|dry-run|execute}. Prefer 'propose' for "
            "analysis, 'dry-run' for reversible/scratch work, 'execute' only for a change that "
            "should produce a draft PR. For kind 'change', never suggest 'execute'.\n"
            f"{gtd_rule}\n"
            f"Kind: {card.kind.value}\nTitle: {card.title}\n"
            f"Description: {(card.description or '')[:400]}\n"
            f"Status: {card.status.value}\nLabels: {', '.join(card.labels)}\n"
            f"Recent notes: {recent}\n"
        )
        text = gw.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.4,
            timeout=timeout,
        )
        parsed = _parse_suggestions(text)
        if parsed:
            # never let the LLM propose execute on a change
            if card.kind.value == "change":
                for s in parsed:
                    if s["mode"] == "execute":
                        s["mode"] = "propose"
            # gtd: downgrade any send-like execute to a draft (dry-run)
            if _is_gtd(card):
                parsed = _clamp_gtd_suggestions(parsed)
            return {"suggestions": parsed[:4], "source": "llm"}
    except Exception as exc:  # noqa: BLE001
        logger.info("suggest_next_steps LLM path failed: %s", exc)
    return {"suggestions": heuristics, "source": "heuristic"}


def _parse_suggestions(text: Optional[str]) -> list[dict]:
    """Extract a [{text,mode}] list from an LLM response (tolerant)."""
    if not text:
        return []
    import json as _json
    import re

    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        raw = _json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and item.get("text"):
            mode = item.get("mode", "propose")
            if mode not in MODES:
                mode = "propose"
            out.append({"text": str(item["text"]).strip(), "mode": mode})
    return out


# ---------------------------------------------------------------------------
# Lifecycle events
# ---------------------------------------------------------------------------


def claim_run(home: Path, card_id: str, run_id: str, worker: str, lease_seconds: int = 900) -> str:
    """Claim a run under a lease. Returns the lease-expiry ISO string."""
    expires = _iso(_now() + timedelta(seconds=lease_seconds))
    CardStore(home).append_event(
        card_id,
        "agent_run_claim",
        worker,
        run_id=run_id,
        worker=f"{worker}@{_HOST}",
        lease_expires=expires,
    )
    return expires


def add_activity(
    home: Path, card_id: str, run_id: str, atype: str, text: str, writer: str = "runner"
) -> None:
    """Append a typed activity entry (thought/action/elicitation/response/error)."""
    CardStore(home).append_event(
        card_id,
        "agent_run_activity",
        writer,
        run_id=run_id,
        atype=atype,
        text=text,
    )


def set_state(
    home: Path,
    card_id: str,
    run_id: str,
    state: str,
    writer: str = "runner",
    last_error: str = "",
    **links,
) -> None:
    """Transition a run's state (and optionally attach links / error)."""
    CardStore(home).append_event(
        card_id,
        "agent_run_state",
        writer,
        run_id=run_id,
        state=state,
        last_error=last_error,
        **links,
    )


# ---------------------------------------------------------------------------
# Safety gate
# ---------------------------------------------------------------------------


def gate(kind: str, mode: str, origin: Optional[str] = None) -> dict:
    """Decide whether a run may execute now, given the card kind and mode.

    Returns ``{"allow_execute": bool, "reason": str}``. propose/dry-run are
    always allowed (no real side effects); execute is gated by kind and by the
    originating surface. ``origin`` is the source surface (e.g. "gtd"), so an
    outbound-comms surface can be clamped to draft-only.
    """
    if mode in ("propose", "dry-run"):
        return {"allow_execute": True, "reason": f"{mode} has no real side effects"}
    # mode == execute
    if origin == "gtd":
        # GTD next-actions may be outbound comms; execute prepares a draft for
        # review and must never auto-send or auto-complete the item.
        return {
            "allow_execute": True,
            "reason": "gtd execute is draft-only: prepare a draft for review, never auto-send",
        }
    if kind == "change":
        return {
            "allow_execute": False,
            "reason": (
                "change tickets require a human/CAB vote to 'approved' "
                "before implementing; the agent may draft only (no self-approval)"
            ),
        }
    # task/epic/incident/problem: execute produces a reviewable artifact (draft PR),
    # never an auto-merge / auto-close.
    return {"allow_execute": True, "reason": "execute produces a draft for review"}


def live_execution_enabled() -> bool:
    """True only when real agent dispatch is explicitly turned on."""
    return os.environ.get("SKAI_RUNNER_LIVE") == "1"


# ---------------------------------------------------------------------------
# Execute-mode dispatch seam (R1, card 182b947f)
# ---------------------------------------------------------------------------
#
# The raw ``claude_dispatcher`` runs an agent with real tools. Letting it handle
# an EXECUTE run would be an ungraded agent making real changes. So execute must
# route through a sandboxed, graded executor (skharness.autocode:
# sandbox -> grade -> twin-gate -> draft PR), wired here explicitly. Until one is
# wired, execute is FAIL-CLOSED: even with SKAI_RUNNER_LIVE=1 it is recorded as a
# plan and moved to review, never dispatched. Propose/dry-run keep using the
# passed-in dispatcher (no real side effects). See docs/runbooks/ai-runner-go-live.md.

_execute_dispatcher = None  # Optional[Callable[[dict], dict]]


def set_execute_dispatcher(fn) -> None:
    """Wire (or clear, with ``None``) the sandboxed/graded execute dispatcher.

    ``fn(context) -> {"summary", "activity", "links"}``, same shape as
    ``claude_dispatcher``. Default ``None`` keeps execute fail-closed.
    """
    global _execute_dispatcher
    _execute_dispatcher = fn


def execute_dispatch_available() -> bool:
    """True when a sandboxed execute dispatcher has been wired."""
    return _execute_dispatcher is not None


# ---------------------------------------------------------------------------
# The runner step
# ---------------------------------------------------------------------------


def process_one(home: Path, item: dict, worker: str = "runner", dispatcher=None) -> dict:
    """Claim and process a single queued run.

    Args:
        home: shared root.
        item: an entry from ``list_queued`` (card_id, kind, run).
        worker: the runner's logical name.
        dispatcher: optional callable(context) -> {"summary","links"} that
            actually runs the agent. When None (or live execution is off), the
            runner records a proposal/plan instead of spawning an agent.

    Returns:
        dict summary of the outcome.
    """
    card_id = item["card_id"]
    run = item["run"]
    run_id = run["run_id"]
    kind = item["kind"]
    mode = run.get("mode", "propose")

    claim_run(home, card_id, run_id, worker)
    add_activity(
        home,
        card_id,
        run_id,
        "thought",
        f"claimed by {worker}@{_HOST}; kind={kind} mode={mode}",
        worker,
    )

    card = CardStore(home).fold(card_id)
    origin = ((getattr(card, "meta", None) or {}).get("origin") or {}).get("surface")
    decision = gate(kind, mode, origin=origin)
    if mode == "execute" and not decision["allow_execute"]:
        add_activity(
            home, card_id, run_id, "elicitation", f"execution gated: {decision['reason']}", worker
        )
        set_state(home, card_id, run_id, NEEDS_REVIEW, worker)
        _move_card(home, card_id, "review", worker)
        return {
            "card_id": card_id,
            "run_id": run_id,
            "state": NEEDS_REVIEW,
            "gated": True,
            "reason": decision["reason"],
        }

    # Build the execution context.
    context = {
        "card_id": card_id,
        "kind": kind,
        "title": card.title if card else "",
        "instruction": run.get("instruction", ""),
        "agent": run.get("agent"),
        "mode": mode,
    }

    if live_execution_enabled():
        # Pick the dispatcher by mode. Execute MUST use the wired sandboxed/graded
        # executor and is fail-closed when none is set (R1); propose/dry-run use
        # the passed-in dispatcher (no real side effects).
        if mode == "execute":
            run_dispatcher = _execute_dispatcher
            if run_dispatcher is None:
                add_activity(
                    home,
                    card_id,
                    run_id,
                    "elicitation",
                    "execute gated (R1): requires the sandboxed skharness.autocode "
                    "executor; none wired, so it was NOT dispatched. Recording plan only.",
                    worker,
                )
                add_activity(
                    home,
                    card_id,
                    run_id,
                    "response",
                    "execute NOT dispatched: sandboxed executor unavailable",
                    worker,
                )
                set_state(home, card_id, run_id, NEEDS_REVIEW, worker)
                _move_card(home, card_id, "review", worker)
                return {
                    "card_id": card_id,
                    "run_id": run_id,
                    "state": NEEDS_REVIEW,
                    "gated": True,
                    "reason": "execute requires the sandboxed executor (R1)",
                }
        else:
            run_dispatcher = dispatcher

        if run_dispatcher is not None:
            try:
                result = run_dispatcher(context)
            except Exception as exc:  # noqa: BLE001
                add_activity(home, card_id, run_id, "error", str(exc), worker)
                set_state(home, card_id, run_id, FAILED, worker, last_error=str(exc))
                return {"card_id": card_id, "run_id": run_id, "state": FAILED, "error": str(exc)}
            for a in result.get("activity", []):
                add_activity(
                    home, card_id, run_id, a.get("atype", "action"), a.get("text", ""), worker
                )
            add_activity(home, card_id, run_id, "response", result.get("summary", "done"), worker)
            set_state(home, card_id, run_id, NEEDS_REVIEW, worker, **result.get("links", {}))
            _move_card(home, card_id, "review", worker)
            return {
                "card_id": card_id,
                "run_id": run_id,
                "state": NEEDS_REVIEW,
                "summary": result.get("summary", ""),
            }

    # No live execution (or no dispatcher for propose/dry-run): record a plan.
    add_activity(
        home,
        card_id,
        run_id,
        "action",
        f"planned (live execution off): would run agent {run.get('agent')} "
        f"in {mode} mode on this {kind}",
        worker,
    )
    add_activity(
        home,
        card_id,
        run_id,
        "response",
        "proposal recorded; enable SKAI_RUNNER_LIVE=1 to dispatch",
        worker,
    )
    set_state(home, card_id, run_id, NEEDS_REVIEW, worker)
    _move_card(home, card_id, "review", worker)
    return {"card_id": card_id, "run_id": run_id, "state": NEEDS_REVIEW, "planned": True}


def _move_card(home: Path, card_id: str, column: str, writer: str) -> None:
    try:
        CardStore(home).append_event(card_id, "move", writer, column=column)
    except Exception as exc:  # noqa: BLE001
        logger.debug("move after run failed: %s", exc)


def run_once(home: Path, worker: str = "ai-runner", dispatcher=None, limit: int = 5) -> list[dict]:
    """Process up to ``limit`` queued runs (one scheduler tick)."""
    results = []
    for item in list_queued(home)[:limit]:
        results.append(process_one(home, item, worker=worker, dispatcher=dispatcher))
    return results


# ---------------------------------------------------------------------------
# Live agent dispatch (only invoked when SKAI_RUNNER_LIVE=1)
# ---------------------------------------------------------------------------


def claude_dispatcher(context: dict) -> dict:
    """Dispatch the instruction to a local agent via ``claude -p``.

    Only called by ``process_one`` when live execution is enabled. Runs the
    agent headlessly; the agent uses its own MCP tools (coord/itil/etc.) to
    act. Returns a summary + activity + links for the run to record.
    """
    import subprocess

    agent = context.get("agent") or "lumina"
    mode = context.get("mode", "propose")
    if mode == "execute":
        # Defense in depth (R1): the raw claude -p path must NEVER run an execute
        # run, even if mis-wired as the execute dispatcher. Execute goes through
        # the sandboxed skharness.autocode engine (set_execute_dispatcher).
        return {
            "summary": "raw claude dispatch refused for execute mode (R1: execute "
            "requires the sandboxed skharness.autocode engine)",
            "activity": [{"atype": "error", "text": "raw execute dispatch refused (R1)"}],
            "links": {},
        }
    prompt = (
        f"You are executing an AI next-step for card {context['card_id']} "
        f"({context['kind']}): {context['title']}.\n\n"
        f"Instruction: {context['instruction']}\n\n"
        f"Mode: {mode}. "
        + (
            "PROPOSE ONLY: do not make real changes; produce a plan/diff and summarize.\n"
            if mode == "propose"
            else (
                "DRY-RUN: work in a scratch/worktree; show the would-be diff, do not commit/push.\n"  # noqa: E501
                if mode == "dry-run"
                else "EXECUTE: make the change but open a DRAFT PR for review; never auto-merge, "
                "never self-approve a change ticket.\n"
            )
        )
        + "Report concisely what you did."
    )
    timeout_s = 900
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--agent", agent],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {"summary": f"timed out after {timeout_s}s", "activity": [], "links": {}}
    except FileNotFoundError:
        return {"summary": "claude CLI not found; cannot dispatch", "activity": [], "links": {}}
    out = (proc.stdout or "").strip()
    summary = out[-1500:] if out else (proc.stderr or "no output")[-500:]
    return {
        "summary": summary,
        "activity": [{"atype": "action", "text": "ran claude -p"}],
        "links": {},
    }


def run_ai_runner_job() -> None:
    """Zero-arg entrypoint for the ``ai-runner`` jobs.yaml job (one tick).

    Processes queued AgentRuns. With ``SKAI_RUNNER_LIVE`` unset the runner only
    records a plan and moves the card to review (safe canary); set it to ``1``
    to actually dispatch the agent.
    """
    from . import SHARED_ROOT

    home = Path(SHARED_ROOT).expanduser()
    results = run_once(home, worker="ai-runner", dispatcher=claude_dispatcher)
    if results:
        logger.info(
            "ai-runner processed %d run(s): %s", len(results), [r.get("state") for r in results]
        )
