"""Pure math utilities for fleet calculations."""

from __future__ import annotations


def clamp(value: float, low: float, high: float) -> float:
    """Return *value* bounded to the inclusive range [low, high].

    Args:
        value: The numeric value to bound.
        low: The lower bound (inclusive).
        high: The upper bound (inclusive).

    Returns:
        *low* if *value* is below it, *high* if above it, otherwise *value*.
    """
    if value < low:
        return low
    if value > high:
        return high
    return value
