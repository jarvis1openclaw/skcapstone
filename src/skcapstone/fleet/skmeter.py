"""skmeter: the per-node energy counter.

The RTX 5060 Ti exposes instantaneous `power.draw` but has no cumulative
`total_energy_consumption` counter, and no RAPL exists anywhere on this fleet.
So we synthesize the counter: sample power continuously and integrate.

This module keeps the arithmetic pure and separate from the sampling loop and
the HTTP surface, following the pattern in sknoded.py, so the math is testable
without a GPU present.
"""

from __future__ import annotations

import re

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def parse_power_line(line: str) -> float | None:
    """Parse one `nvidia-smi --query-gpu=power.draw` output line into watts.

    Returns None for blanks, '[N/A]', and anything else unparseable. Tolerates
    NUL padding, which appears when the sampler's output file is read while
    nvidia-smi is still writing to it.
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
        return float(match.group(0))
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
        self._total_j += watts * dt_s
        self._marginal_j += max(0.0, watts - self._idle_w) * dt_s
        self._samples_n += 1

    def snapshot(self) -> dict:
        return {
            "total_j": self._total_j,
            "marginal_j": self._marginal_j,
            "idle_baseline_w": self._idle_w,
            "samples_n": self._samples_n,
        }
