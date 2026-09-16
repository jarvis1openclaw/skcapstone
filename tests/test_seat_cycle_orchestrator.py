"""Contract tests for the single lifecycle-seat generation orchestrator."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

from skcapstone.fleet.seat_cycle_orchestrator import run_generation, select_niobe_service


def test_generation_runs_exact_order_and_continues_after_failure(tmp_path, monkeypatch) -> None:
    """One failed seat cannot suppress later seats in the same generation."""

    monkeypatch.setattr(
        "skcapstone.fleet.seat_cycle_orchestrator.select_niobe_service",
        lambda _home: "skfleet-niobe-live.service",
    )
    calls: list[list[str]] = []

    def runner(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1 if "skfleet-tank.service" in command else 0)

    result = run_generation(tmp_path, runner=runner)

    assert [command[-1] for command in calls] == [
        "skfleet-tank.service",
        "skfleet-seraph.service",
        "skfleet-niobe-live.service",
    ]
    assert result["failures"] == 1
    receipt = json.loads(
        (tmp_path / "coordination/seat-cycles/orchestrator.health.jsonl")
        .read_text()
        .splitlines()[-1]
    )
    assert [seat["unit"] for seat in receipt["seats"]] == [command[-1] for command in calls]
    assert [seat["returncode"] for seat in receipt["seats"]] == [1, 0, 0]


def test_niobe_activation_selects_live_or_shadow(tmp_path, monkeypatch) -> None:
    """Only a valid existing activation selects the live Niobe service."""

    activation = tmp_path / "coordination/niobe-activation.json"
    activation.parent.mkdir(parents=True)
    activation.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "skcapstone.fleet.seat_cycle_orchestrator.parse_activation",
        lambda *_args, **_kwargs: object(),
    )
    assert select_niobe_service(tmp_path) == "skfleet-niobe-live.service"

    monkeypatch.setattr(
        "skcapstone.fleet.seat_cycle_orchestrator.parse_activation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("expired")),
    )
    assert select_niobe_service(tmp_path) == "skfleet-niobe.service"


def test_non_object_niobe_activation_roots_fail_closed_to_shadow(tmp_path, monkeypatch) -> None:
    """Valid JSON with the wrong root type cannot abort the whole generation."""

    activation = tmp_path / "coordination/niobe-activation.json"
    activation.parent.mkdir(parents=True)

    def parser(value, **_kwargs):
        assert isinstance(value, Mapping)

    monkeypatch.setattr("skcapstone.fleet.seat_cycle_orchestrator.parse_activation", parser)
    for value in ([], None, 1, "active"):
        activation.write_text(json.dumps(value), encoding="utf-8")
        assert select_niobe_service(tmp_path) == "skfleet-niobe.service"


def test_orchestrator_units_are_packaged_and_prevent_overlapping_generations() -> None:
    """Only the orchestrator recurs, five minutes after a generation ends."""

    root = Path(__file__).parents[1]
    service = (root / "systemd/skfleet-seat-cycle.service").read_text()
    timer = (root / "systemd/skfleet-seat-cycle.timer").read_text()
    assert "TimeoutStartSec=960" in service
    assert "OnUnitInactiveSec=5min" in timer
    assert "OnUnitActiveSec" not in timer and "OnCalendar" not in timer
    for suffix in ("service", "timer"):
        assert (root / f"systemd/skfleet-seat-cycle.{suffix}").read_bytes() == (
            root / f"src/skcapstone/data/systemd/skfleet-seat-cycle.{suffix}"
        ).read_bytes()


def test_control_profile_requires_only_orchestrator_for_serialized_seats() -> None:
    """Competing Tank, Seraph, and Niobe timers cannot be enabled by profile convergence."""

    source = (Path(__file__).parents[1] / "scripts/fleet/gen-profile-manifests.py").read_text()
    assert '"skfleet-seat-cycle.timer"' in source
    for timer in (
        "skfleet-tank.timer",
        "skfleet-seraph.timer",
        "skfleet-niobe.timer",
        "skfleet-niobe-live.timer",
    ):
        assert f'"{timer}"' in source
    assert "SERIALIZED_SEAT_MUST_NOT" in source
