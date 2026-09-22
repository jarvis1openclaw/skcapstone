"""Recovery-fence tests for the serialized lifecycle-seat orchestrator."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from skcapstone.fleet import seat_cycle_orchestrator
from skcapstone.fleet.seat_cycle_orchestrator import run_generation


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    """Build the subprocess result shape consumed by the orchestrator."""

    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _state(*, active: str, job: str = "", main_pid: str = "0") -> str:
    """Render the exact systemd properties used by recovery proof."""

    return "LoadState=loaded\n" f"ActiveState={active}\n" f"Job={job}\n" f"MainPID={main_pid}\n"


def test_recovery_resets_stopped_failed_oneshot_before_exact_inactivity(tmp_path) -> None:
    """A failed, process-free oneshot is normalized before a new generation."""

    marker = tmp_path / "coordination/seat-cycles/recovery-required"
    marker.parent.mkdir(parents=True)
    marker.write_text("recovery-required\n", encoding="utf-8")
    active_states = {
        unit: "failed" if unit == "skfleet-seraph.service" else "inactive"
        for unit in seat_cycle_orchestrator._GOVERNED_SERVICES
    }
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> SimpleNamespace:
        """Model systemd retaining failed state until reset-failed."""

        calls.append(command)
        unit = command[3] if command[2] == "show" else command[-1]
        if command[2] == "show":
            return _result(stdout=_state(active=active_states[unit]))
        if command[2] == "reset-failed":
            active_states[unit] = "inactive"
        return _result()

    result = run_generation(tmp_path, runner=runner)

    reset = ["systemctl", "--user", "reset-failed", "skfleet-seraph.service"]
    assert result["aborted"] is False
    assert calls.count(reset) == 1
    assert calls.index(reset) < next(
        index for index, call in enumerate(calls) if call[2:4] == ["start", "--wait"]
    )
    assert not marker.exists()


def test_recovery_accepts_authoritatively_missing_governed_units() -> None:
    """Missing units are safe when their exact post-stop state proves absence."""

    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> SimpleNamespace:
        """Return systemd's benign errors and authoritative absent state."""

        calls.append(command)
        if command[2] == "show":
            return _result(
                returncode=4,
                stdout="LoadState=not-found\nActiveState=inactive\nJob=\nMainPID=0\n",
            )
        return _result(returncode=5, stderr="Unit not found")

    assert seat_cycle_orchestrator._prove_recovery_inactive(runner, cancel_jobs=True) is True
    assert not any(call[2] == "reset-failed" for call in calls)


@pytest.mark.parametrize(
    ("active", "job", "main_pid"),
    [
        ("active", "", "42"),
        ("inactive", "", "42"),
        ("activating", "", "0"),
        ("deactivating", "", "0"),
        ("unknown", "", "0"),
        ("failed", "99", "0"),
        ("failed", "", "42"),
        ("failed", "", "unknown"),
    ],
)
def test_recovery_never_resets_unsafe_unit_state(active: str, job: str, main_pid: str) -> None:
    """Active, transitional, queued, and process-bearing units remain fenced."""

    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> SimpleNamespace:
        """Expose one unsafe unit state after a successful stop."""

        calls.append(command)
        if command[2] == "show":
            return _result(stdout=_state(active=active, job=job, main_pid=main_pid))
        return _result()

    assert seat_cycle_orchestrator._prove_recovery_inactive(runner, cancel_jobs=True) is False
    assert not any(call[2] == "reset-failed" for call in calls)


def test_recovery_rejects_process_appearing_after_failed_state_reset() -> None:
    """Post-reset process appearance cannot satisfy exact inactivity proof."""

    calls: list[list[str]] = []
    show_count = 0

    def runner(command: list[str], **_kwargs: object) -> SimpleNamespace:
        """Expose a process-bearing state only after failed-state cleanup."""

        nonlocal show_count
        calls.append(command)
        if command[2] == "show":
            show_count += 1
            if show_count == 1:
                return _result(stdout=_state(active="failed"))
            return _result(stdout=_state(active="inactive", main_pid="42"))
        return _result()

    assert seat_cycle_orchestrator._prove_recovery_inactive(runner, cancel_jobs=True) is False
    assert any(call[2] == "reset-failed" for call in calls)


def test_recovery_never_resets_failed_unit_after_stop_failure() -> None:
    """A failed stop cannot be hidden by resetting the unit's failed state."""

    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> SimpleNamespace:
        """Expose a process-free failed unit after an unsuccessful stop."""

        calls.append(command)
        if command[2] == "show":
            return _result(stdout=_state(active="failed"))
        return _result(returncode=1, stderr="stop failed")

    assert seat_cycle_orchestrator._prove_recovery_inactive(runner, cancel_jobs=True) is False
    assert not any(call[2] == "reset-failed" for call in calls)


def test_reset_failure_keeps_fence_and_records_aborted_recovery(tmp_path) -> None:
    """Failed normalization is durable rollback evidence and starts no seat."""

    marker = tmp_path / "coordination/seat-cycles/recovery-required"
    marker.parent.mkdir(parents=True)
    marker.write_text("recovery-required\n", encoding="utf-8")
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> SimpleNamespace:
        """Fail the minimum reset-failed cleanup operation."""

        calls.append(command)
        if command[2] == "show":
            return _result(stdout=_state(active="failed"))
        if command[2] == "reset-failed":
            return _result(returncode=1, stderr="reset failed")
        return _result()

    result = run_generation(tmp_path, runner=runner)
    receipt = json.loads(
        (tmp_path / "coordination/seat-cycles/orchestrator.health.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )

    assert result["aborted"] is True
    assert receipt["recovery"] == "governed_service_inactivity_unproven"
    assert marker.is_file()
    assert any(call[2] == "reset-failed" for call in calls)
    assert not any(call[2:4] == ["start", "--wait"] for call in calls)
