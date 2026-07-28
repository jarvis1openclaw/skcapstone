"""Tests for skcapstone.fleet.mathutil.clamp."""

from __future__ import annotations

from skcapstone.fleet.mathutil import clamp


def test_clamp_below_range() -> None:
    """Returns low when value is below the range."""
    assert clamp(2.0, 5.0, 10.0) == 5.0


def test_clamp_above_range() -> None:
    """Returns high when value is above the range."""
    assert clamp(15.0, 5.0, 10.0) == 10.0


def test_clamp_in_range() -> None:
    """Returns value unchanged when it is within the range."""
    assert clamp(7.5, 5.0, 10.0) == 7.5


def test_clamp_at_lower_bound() -> None:
    """Returns value at the inclusive lower bound."""
    assert clamp(5.0, 5.0, 10.0) == 5.0


def test_clamp_at_upper_bound() -> None:
    """Returns value at the inclusive upper bound."""
    assert clamp(10.0, 5.0, 10.0) == 10.0


def test_clamp_negative_values() -> None:
    """Works correctly with negative numbers."""
    assert clamp(-5.0, -10.0, -1.0) == -5.0
    assert clamp(-20.0, -10.0, -1.0) == -10.0
    assert clamp(0.0, -10.0, -1.0) == -1.0
