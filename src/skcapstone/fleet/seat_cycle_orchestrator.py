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
    except (OSError, ValueError, json.JSONDecodeError):
        return _NIOBE_SHADOW
    return _NIOBE_LIVE


def _append_receipt(home: Path, receipt: dict[str, Any]) -> None:
    """Append and fsync one generation receipt under an advisory file lock."""

    path = home / "coordination" / "seat-cycles" / "orchestrator.health.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def run_generation(
    home: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Start every governed seat in order and continue after bounded failures."""

    started_at = datetime.now(timezone.utc).isoformat()
    units = (_TANK, _SERAPH, select_niobe_service(home))
    seats: list[dict[str, Any]] = []
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
        except OSError as exc:
            returncode = 126
            error = type(exc).__name__
        seats.append({"unit": unit, "returncode": returncode, "error": error})
    receipt = {
        "schema": "skfleet.seat-cycle-generation/v1",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "seats": seats,
        "failures": sum(seat["returncode"] != 0 for seat in seats),
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
