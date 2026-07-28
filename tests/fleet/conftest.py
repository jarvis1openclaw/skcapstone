"""Shared fixtures for fleet tests."""

from __future__ import annotations

import pytest

from skcapstone.fleet.paths import FleetPaths


@pytest.fixture
def paths(tmp_path) -> FleetPaths:
    """A throwaway fleet tree root."""
    return FleetPaths(root=tmp_path / "fleet")


@pytest.fixture
def operator():
    """The operator seat writer (spec owner)."""
    from skcapstone.fleet.store import Writer

    return Writer(role="operator", node="node-158", identity="capauth:chef@skworld.io")


@pytest.fixture
def noded41():
    """sknoded writer on node-41 (status owner for node-41 only)."""
    from skcapstone.fleet.store import Writer

    return Writer(role="sknoded", node="node-41", identity="")


@pytest.fixture
def scheduler_writer():
    """The scheduler seat (placement owner, runs on the control-plane node)."""
    from skcapstone.fleet.store import Writer

    return Writer(role="scheduler", node="node-158", identity="")
