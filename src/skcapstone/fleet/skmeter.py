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
import sys
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
        """Rehydrate from a checkpoint. The checkpoint is untrusted input.

        A missing key leaves the existing value untouched. A value that will
        not coerce to the right type is ignored, also leaving the existing
        value untouched, and this method never raises: a corrupt checkpoint
        must degrade to "start from what we have", never to a crashed daemon.
        A value that does coerce but is negative is floored at zero, because
        nothing in this counter may ever go negative, checkpoint included.
        """
        if not state:
            return
        self._total_j = self._coerce_field(state, "total_j", self._total_j, float)
        self._marginal_j = self._coerce_field(state, "marginal_j", self._marginal_j, float)
        self._samples_n = self._coerce_field(state, "samples_n", self._samples_n, int)
        self._idle_w = self._coerce_field(state, "idle_baseline_w", self._idle_w, float)

    @staticmethod
    def _coerce_field(state: dict, key: str, current, cast):
        """Coerce one checkpoint field, defensively.

        Absent key or an uncoercible value: keep `current`. A coercible but
        negative value: floor at zero rather than install it or keep current.
        """
        if key not in state:
            return current
        try:
            value = cast(state[key])
        except (TypeError, ValueError):
            return current
        zero = cast(0)
        return value if value >= zero else zero


DEFAULT_PORT = 9420
DEFAULT_BIND = "127.0.0.1"
DEFAULT_INTERVAL_MS = 200
NVIDIA_SMI_CMD = [
    "nvidia-smi",
    "--query-gpu=power.draw",
    "--format=csv,noheader,nounits",
]


IDLE_QUANTILE = 0.10
IDLE_MAX_RATIO = 1.5
IDLE_MAX_DELTA_W = 15.0


def measure_idle_baseline(sample_fn: Callable[[], float | None], n: int = 50) -> float:
    """Take n samples and return a low quantile of them as the idle floor.

    A low quantile, not the mean. The mean of a window that happened to
    contain real work is a BUSY number, and a busy baseline is the worst
    possible failure here: observe() floors marginal energy at zero per
    sample, so the gateway would record `joules: 0, basis: measured_gpu` for
    work that really burned power. That is an invented number wearing a
    measured label, which is exactly what this whole component exists to
    prevent. The idle floor is a floor, so the low end of the distribution is
    the honest estimator of it; the high end is whatever else the card was
    doing.

    The 10th percentile rather than the strict minimum, so one anomalously low
    reading (a driver hiccup, a truncated line that still parsed) cannot drag
    the floor down on its own. Nearest-rank, so it always returns an actual
    observed sample rather than an interpolated value that was never measured.

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
    good.sort()
    idx = int(IDLE_QUANTILE * (len(good) - 1))
    return good[idx]


def plausible_baseline(
    candidate_w: float,
    known_good_w: float,
    max_ratio: float = IDLE_MAX_RATIO,
    max_delta_w: float = IDLE_MAX_DELTA_W,
) -> float:
    """Accept a candidate idle baseline, or fall back to the known-good one.

    Baselining has no way to ask the GPU "are you idle right now?", so a
    re-baseline tick (or a daemon restart) that lands under load measures the
    LOAD and calls it idle. Installing that number silently zeroes the
    marginal energy of every subsequent request while still labelling it
    `measured_gpu`. The guard exists so that failure is refused rather than
    recorded.

    The rule: reject only when the candidate exceeds the known-good baseline
    on BOTH axes at once, ratio and absolute watts. Either test alone is
    wrong here:

      * ratio alone rejects benign drift on a low-idle card, e.g. 3.0 W ->
        9.5 W, which is a real idle shift we deliberately want to adopt (see
        resolve_boot_idle_baseline: the fresh measurement is meant to win).
      * absolute delta alone would let a proportionally huge jump through on
        a card whose idle is already high.

    Together they catch the case that actually matters, the ~9 W idle floor
    being replaced by a ~99 W under-load reading, while leaving ordinary
    ambient and driver drift alone.

    With no prior known-good baseline (0.0) there is nothing to compare
    against, so the candidate is accepted: a first measurement is the best
    fact available, and refusing it would leave the meter charging absolute
    energy forever.
    """
    candidate = float(candidate_w)
    known_good = float(known_good_w)
    if candidate <= 0.0:
        return known_good if known_good > 0.0 else 0.0
    if known_good <= 0.0:
        return candidate
    too_high_by_ratio = candidate > known_good * max_ratio
    too_high_by_watts = candidate > known_good + max_delta_w
    if too_high_by_ratio and too_high_by_watts:
        return known_good
    return candidate


def build_energy_response(
    counter: EnergyCounter,
    watts_now: float,
    device: str,
    node: str,
    now_ms: int,
) -> dict:
    """The GET /energy payload. `counter_j` is what the gateway deltas.

    When the meter has never observed a sample there is no power source on this
    node, and `counter_j` and `total_j` are OMITTED rather than sent as 0.0.

    That distinction is the whole point. A meter with no data source reporting
    0.0 makes the gateway compute a delta of zero and record
    `joules: 0, basis: measured_gpu`, a confident measured-zero for work that
    really consumed power. Omitting the field makes the reading unknowable
    instead, which is the honest answer and the one the gateway already handles
    by returning null. Found by deploying to a node with no GPU.
    """
    snap = counter.snapshot()
    payload = {
        "watts_now": watts_now,
        "idle_baseline_w": snap["idle_baseline_w"],
        "device": device,
        "node": node,
        "ts": now_ms,
        "samples_n": snap["samples_n"],
        "metering": "active" if snap["samples_n"] > 0 else "unavailable",
    }
    if snap["samples_n"] > 0:
        payload["counter_j"] = snap["marginal_j"]
        payload["total_j"] = snap["total_j"]
    return payload


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


def checkpoint_idle_baseline(checkpoint: dict | None) -> float:
    """The last known-good idle baseline off a checkpoint, or 0.0.

    Untrusted input, same contract as EnergyCounter.restore: never raises,
    never returns a negative.
    """
    if not checkpoint:
        return 0.0
    try:
        saved = float(checkpoint.get("idle_baseline_w"))
    except (TypeError, ValueError):
        return 0.0
    return saved if saved >= 0.0 else 0.0


def resolve_boot_idle_baseline(fresh_w: float, checkpoint: dict | None) -> float:
    """Pick the idle baseline to use at boot: the fresh measurement wins.

    We remeasure idle at every boot because ambient and hardware conditions
    change between restarts, and the nightly re-baseline already keeps the
    floor current going forward. Continuity of the counter matters, since
    that is what the gateway deltas; continuity of the idle floor does not.
    The checkpoint's baseline is only a fallback for when the fresh
    measurement failed (returned 0.0). Do not "fix" this back to preferring
    the checkpoint.

    One exception, and only one: a restart that lands while the card is under
    load measures the load, not idle. plausible_baseline() refuses a candidate
    that towers over the checkpointed known-good floor and keeps the
    checkpointed value instead. Every plausible fresh measurement still wins,
    so the rule above is intact.
    """
    saved = checkpoint_idle_baseline(checkpoint)
    if fresh_w > 0:
        return plausible_baseline(fresh_w, saved)
    return saved


def resolve_bind_address(bind: str | None = None, env: dict | None = None) -> str:
    """Pick the address the meter listens on. Loopback unless told otherwise.

    Precedence: explicit argument, then SKMETER_BIND, then loopback.

    The default is deliberately 127.0.0.1 and must stay that way. The gateway
    that reads this meter does not necessarily run on the GPU node, so a real
    deployment often does need a wider bind, but exposing per-node power
    telemetry on the network is a deployment decision an operator makes on
    purpose, not something this daemon does for them by default. See the
    comment in systemd/skmeter.service for how to widen it.
    """
    if bind:
        return str(bind)
    source = os.environ if env is None else env
    from_env = source.get("SKMETER_BIND")
    if from_env:
        return str(from_env)
    return DEFAULT_BIND


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
    bind: str | None = None,
) -> None:
    """Run the meter: baseline, sampler thread, then serve GET /energy.

    `bind` defaults to loopback (see resolve_bind_address). The meter must be
    reachable from whichever host runs skgateway, which on this fleet is not
    the GPU node, so a real deployment sets SKMETER_BIND explicitly.
    """
    node = node or socket.gethostname()
    bind_addr = resolve_bind_address(bind)

    def one_sample() -> float | None:
        try:
            out = subprocess.run(NVIDIA_SMI_CMD, capture_output=True, text=True, timeout=5).stdout
        except Exception:
            return None
        return parse_power_line(out.splitlines()[0] if out.splitlines() else "")

    idle = measure_idle_baseline(one_sample, n=20)

    path = checkpoint_path(node)
    checkpoint = load_checkpoint(path)
    counter = EnergyCounter(idle_w=idle)
    counter.restore(checkpoint)
    # restore() just installed the checkpoint's idle_baseline_w, if any.
    # Overrule it: the fresh boot measurement wins, the checkpoint is only a
    # fallback. See resolve_boot_idle_baseline for why.
    counter.set_idle_baseline(resolve_boot_idle_baseline(idle, checkpoint))
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
                        # Nothing tells this tick whether the card is idle, so
                        # run the candidate past the plausibility guard rather
                        # than installing a possibly-under-load reading as the
                        # idle floor. A rejected candidate is announced, never
                        # swallowed: a meter quietly refusing to re-baseline
                        # for weeks is its own kind of invisible wrong number.
                        known_good = state.counter.idle_baseline_w
                        accepted = plausible_baseline(fresh, known_good)
                        state.counter.set_idle_baseline(accepted)
                    if accepted != fresh:
                        print(
                            f"[skmeter] rejected implausible idle baseline "
                            f"{fresh:.2f} W (known good {known_good:.2f} W); "
                            f"the GPU was probably busy. Keeping {accepted:.2f} W.",
                            file=sys.stderr,
                            flush=True,
                        )
                last_baseline_ms = now_ms

    threading.Thread(target=sample_loop, args=(state, interval_ms), daemon=True).start()
    threading.Thread(target=_maintenance, daemon=True).start()

    HTTPServer((bind_addr, port), _handler_factory(state)).serve_forever()


if __name__ == "__main__":
    serve()
