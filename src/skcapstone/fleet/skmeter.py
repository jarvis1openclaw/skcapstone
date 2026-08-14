"""skmeter: the per-node energy counter.

The RTX 5060 Ti exposes instantaneous `power.draw` but has no cumulative
`total_energy_consumption` counter, and no RAPL exists anywhere on this fleet.
So we synthesize the counter: sample power continuously and integrate.

This module keeps the arithmetic pure and separate from the sampling loop and
the HTTP surface, following the pattern in sknoded.py, so the math is testable
without a GPU present.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def parse_power_line(line: str) -> float | None:
    """Parse one `nvidia-smi --query-gpu=power.draw` output line into watts.

    Returns None for blanks, '[N/A]', negative values (corrupt samples), and
    anything else unparseable. Tolerates NUL padding, which appears when the
    sampler's output file is read while nvidia-smi is still writing to it.
    """
    if not line:
        return None
    cleaned = line.replace("\x00", "").strip()
    if not cleaned or "N/A" in cleaned:
        return None
    match = _NUMBER.search(cleaned)
    if not match:
        return None
    try:
        value = float(match.group(0))
        if value < 0.0:
            return None
        return value
    except ValueError:
        return None


def integrate(samples_w: list[float], dt_s: float, idle_w: float = 0.0) -> dict:
    """Integrate power samples into joules over a fixed sample interval.

    `marginal_j` subtracts the idle baseline and is floored at zero per sample,
    so below-idle readings cannot create energy credits.
    """
    n = len(samples_w)
    if n == 0:
        return {
            "total_j": 0.0,
            "marginal_j": 0.0,
            "window_s": 0.0,
            "samples_n": 0,
            "mean_w": 0.0,
            "peak_w": 0.0,
        }
    total_j = sum(w * dt_s for w in samples_w)
    marginal_j = sum(max(0.0, w - idle_w) * dt_s for w in samples_w)
    return {
        "total_j": total_j,
        "marginal_j": marginal_j,
        "window_s": n * dt_s,
        "samples_n": n,
        "mean_w": sum(samples_w) / n,
        "peak_w": max(samples_w),
    }


class EnergyCounter:
    """A monotonic joule counter, the thing the GPU refuses to give us.

    Callers read `marginal_j` before and after a unit of work; the delta is that
    work's energy. Monotonicity is what makes the delta meaningful, so nothing
    here may ever decrease.
    """

    def __init__(self, idle_w: float = 0.0) -> None:
        self._idle_w = float(idle_w)
        self._total_j = 0.0
        self._marginal_j = 0.0
        self._samples_n = 0

    @property
    def total_j(self) -> float:
        return self._total_j

    @property
    def marginal_j(self) -> float:
        return self._marginal_j

    @property
    def samples_n(self) -> int:
        return self._samples_n

    @property
    def idle_baseline_w(self) -> float:
        return self._idle_w

    def set_idle_baseline(self, idle_w: float) -> None:
        """Re-baseline (nightly). Does not retroactively alter the counter."""
        self._idle_w = float(idle_w)

    def observe(self, watts: float, dt_s: float) -> None:
        contribution_j = max(0.0, watts * dt_s)
        self._total_j += contribution_j
        self._marginal_j += max(0.0, watts - self._idle_w) * dt_s
        self._samples_n += 1

    def snapshot(self) -> dict:
        return {
            "total_j": self._total_j,
            "marginal_j": self._marginal_j,
            "idle_baseline_w": self._idle_w,
            "samples_n": self._samples_n,
        }

    def restore(self, state: dict | None) -> None:
        """Rehydrate from a checkpoint. Unknown keys are ignored."""
        if not state:
            return
        self._total_j = float(state.get("total_j", 0.0) or 0.0)
        self._marginal_j = float(state.get("marginal_j", 0.0) or 0.0)
        self._samples_n = int(state.get("samples_n", 0) or 0)
        if state.get("idle_baseline_w") is not None:
            self._idle_w = float(state["idle_baseline_w"])


DEFAULT_PORT = 9420
DEFAULT_INTERVAL_MS = 200
NVIDIA_SMI_CMD = [
    "nvidia-smi",
    "--query-gpu=power.draw",
    "--format=csv,noheader,nounits",
]


def measure_idle_baseline(sample_fn: Callable[[], float | None], n: int = 50) -> float:
    """Average n samples to establish the idle floor.

    Returns 0.0 if nothing parseable arrives. A zero baseline charges absolute
    energy, which is wrong but safe; crashing the meter would be worse.
    """
    good = []
    for _ in range(n):
        try:
            value = sample_fn()
        except Exception:
            value = None
        if value is not None:
            good.append(float(value))
    if not good:
        return 0.0
    return sum(good) / len(good)


def build_energy_response(
    counter: EnergyCounter,
    watts_now: float,
    device: str,
    node: str,
    now_ms: int,
) -> dict:
    """The GET /energy payload. `counter_j` is what the gateway deltas."""
    snap = counter.snapshot()
    return {
        "counter_j": snap["marginal_j"],
        "total_j": snap["total_j"],
        "watts_now": watts_now,
        "idle_baseline_w": snap["idle_baseline_w"],
        "device": device,
        "node": node,
        "ts": now_ms,
        "samples_n": snap["samples_n"],
    }


CHECKPOINT_INTERVAL_S = 30
REBASELINE_INTERVAL_H = 24


def checkpoint_path(node: str) -> pathlib.Path:
    root = pathlib.Path.home() / ".skcapstone" / "skmeter"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{node}-state.json"


def save_checkpoint(counter: EnergyCounter, path) -> None:
    """Write the counter atomically.

    Non-atomic writes are how the joule wallet loses balances: a truncated
    file reads as zero on the next boot. Temp file plus os.replace, always.
    """
    path = pathlib.Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = dict(counter.snapshot())
    payload["saved_ms"] = int(time.time() * 1000)
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def load_checkpoint(path) -> dict | None:
    """Read a checkpoint. Returns None for missing or corrupt files."""
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def should_rebaseline(last_ms, now_ms: int, interval_h: int = REBASELINE_INTERVAL_H) -> bool:
    """True when the idle floor is stale. Never baselined counts as due."""
    if last_ms is None:
        return True
    return (now_ms - int(last_ms)) > interval_h * 3600 * 1000


class _State:
    """Shared between the sampler thread and the HTTP handler."""

    def __init__(self, counter: EnergyCounter, device: str, node: str) -> None:
        self.counter = counter
        self.device = device
        self.node = node
        self.watts_now = 0.0
        self.lock = threading.Lock()


def sample_loop(state: _State, interval_ms: int = DEFAULT_INTERVAL_MS) -> None:
    """Stream nvidia-smi output and feed the counter.

    Uses one long-lived `nvidia-smi -lms` process rather than spawning per
    sample, which would cost more than it measures.
    """
    dt_s = interval_ms / 1000.0
    while True:
        try:
            proc = subprocess.Popen(
                NVIDIA_SMI_CMD + ["-lms", str(interval_ms)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            for line in proc.stdout:
                watts = parse_power_line(line)
                if watts is None:
                    continue
                with state.lock:
                    state.counter.observe(watts, dt_s)
                    state.watts_now = watts
        except Exception:
            pass
        time.sleep(5.0)  # nvidia-smi died; back off and retry


def _handler_factory(state: _State):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/") != "/energy":
                self.send_response(404)
                self.end_headers()
                return
            with state.lock:
                payload = build_energy_response(
                    state.counter,
                    state.watts_now,
                    state.device,
                    state.node,
                    int(time.time() * 1000),
                )
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence per-request stderr noise
            return

    return Handler


def serve(
    port: int = DEFAULT_PORT,
    device: str = "gpu0",
    node: str = "",
    interval_ms: int = DEFAULT_INTERVAL_MS,
) -> None:
    """Run the meter: baseline, sampler thread, then serve GET /energy."""
    node = node or socket.gethostname()

    def one_sample() -> float | None:
        try:
            out = subprocess.run(NVIDIA_SMI_CMD, capture_output=True, text=True, timeout=5).stdout
        except Exception:
            return None
        return parse_power_line(out.splitlines()[0] if out.splitlines() else "")

    idle = measure_idle_baseline(one_sample, n=20)

    path = checkpoint_path(node)
    counter = EnergyCounter(idle_w=idle)
    counter.restore(load_checkpoint(path))
    state = _State(counter, device, node)
    last_baseline_ms = int(time.time() * 1000)

    def _maintenance() -> None:
        nonlocal last_baseline_ms
        while True:
            time.sleep(CHECKPOINT_INTERVAL_S)
            now_ms = int(time.time() * 1000)
            with state.lock:
                save_checkpoint(state.counter, path)
            if should_rebaseline(last_baseline_ms, now_ms):
                fresh = measure_idle_baseline(one_sample, n=20)
                if fresh > 0:
                    with state.lock:
                        state.counter.set_idle_baseline(fresh)
                last_baseline_ms = now_ms

    threading.Thread(target=sample_loop, args=(state, interval_ms), daemon=True).start()
    threading.Thread(target=_maintenance, daemon=True).start()

    HTTPServer(("127.0.0.1", port), _handler_factory(state)).serve_forever()


if __name__ == "__main__":
    serve()
