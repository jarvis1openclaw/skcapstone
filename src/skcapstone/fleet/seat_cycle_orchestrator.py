"""Run one non-overlapping Tank, Seraph, and Niobe seat generation."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from skcapstone.niobe_activation import parse_activation

_TANK = "skfleet-tank.service"
_SERAPH = "skfleet-seraph.service"
_NIOBE_LIVE = "skfleet-niobe-live.service"
_NIOBE_SHADOW = "skfleet-niobe.service"


def select_niobe_service(home: Path) -> str:
    """Select live Niobe only when its existing activation still validates."""

    activation = home / "coordination" / "niobe-activation.json"
    try:
        value = json.loads(activation.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            return _NIOBE_SHADOW
        parse_activation(value, home=home)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return _NIOBE_SHADOW
    return _NIOBE_LIVE


def select_niobe_timer(home: Path) -> str:
    """Select the independent Niobe timer matching the activation contract."""

    if select_niobe_service(home) == _NIOBE_LIVE:
        return "skfleet-niobe-live.timer"
    return "skfleet-niobe.timer"


def _append_receipt(home: Path, receipt: dict[str, Any]) -> None:
    """Append and fsync one generation receipt under an advisory file lock."""

    path = home / "coordination" / "seat-cycles" / "orchestrator.health.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _stop_and_prove_inactive(unit: str, runner: Callable[..., Any]) -> bool:
    """Stop a timed-out seat and prove its service reached exact inactivity."""

    try:
        stopped = runner(
            ["systemctl", "--user", "stop", unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        shown = runner(
            [
                "systemctl",
                "--user",
                "show",
                unit,
                "--property=LoadState,ActiveState",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    state = dict(
        line.split("=", 1)
        for line in str(getattr(shown, "stdout", "")).splitlines()
        if "=" in line
    )
    return (
        int(getattr(stopped, "returncode", 1)) == 0
        and int(getattr(shown, "returncode", 1)) == 0
        and state.get("LoadState") == "loaded"
        and state.get("ActiveState") == "inactive"
    )


def run_generation(
    home: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Start every governed seat in order and continue after bounded failures."""

    started_at = datetime.now(timezone.utc).isoformat()
    units = (_TANK, _SERAPH, select_niobe_service(home))
    seats: list[dict[str, Any]] = []
    aborted = False
    for unit in units:
        try:
            completed = runner(
                ["systemctl", "--user", "start", "--wait", unit],
                check=False,
                timeout=310,
            )
            returncode = int(completed.returncode)
            error = None
        except subprocess.TimeoutExpired:
            returncode = 124
            error = "systemctl_wait_timeout"
            cleanup_ok = _stop_and_prove_inactive(unit, runner)
        except OSError as exc:
            returncode = 126
            error = type(exc).__name__
            cleanup_ok = None
        else:
            cleanup_ok = None
        seats.append(
            {
                "unit": unit,
                "returncode": returncode,
                "error": error,
                "timeout_cleanup_proven": cleanup_ok,
            }
        )
        if cleanup_ok is False:
            aborted = True
            break
    receipt = {
        "schema": "skfleet.seat-cycle-generation/v1",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "seats": seats,
        "failures": sum(seat["returncode"] != 0 for seat in seats),
        "aborted": aborted,
    }
    _append_receipt(home, receipt)
    return receipt


def main() -> int:
    """Run one generation from the systemd entrypoint."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    run_generation(args.home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
