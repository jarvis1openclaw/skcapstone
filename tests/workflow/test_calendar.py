from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sklegal_workflow import (
    CalendarEvent,
    CalendarSimulation,
    DeadlineTrigger,
    DeterministicDeadlineCalculator,
)
from sklegal_workflow.calendar import WorkflowError


def trigger(**changes):
    value = {
        "trigger_id": uuid4(),
        "triggered_at": datetime(2026, 8, 21, 23, 30, tzinfo=UTC),
        "period_days": 1,
        "day_type": "calendar",
        "timezone": "America/Chicago",
        "governing_rule_reference": "rule-1",
        "governing_rule_version": "v1",
        "source_reference": "source-1",
    }
    value.update(changes)
    return DeadlineTrigger(**value)


def test_business_days_skip_weekend_and_remain_candidate():
    candidate = DeterministicDeadlineCalculator.calculate(
        trigger(triggered_at=datetime(2026, 8, 21, 15, tzinfo=UTC), period_days=1, day_type="business")
    )
    assert candidate.candidate_due_at.date().isoformat() == "2026-08-24"
    assert candidate.status == "candidate"
    with pytest.raises(WorkflowError):
        candidate.review(reviewer="", reviewed_at=datetime.now(UTC))


def test_timezone_boundary_uses_local_trigger_date():
    candidate = DeterministicDeadlineCalculator.calculate(
        trigger(triggered_at=datetime(2026, 8, 22, 4, 30, tzinfo=UTC))
    )
    assert candidate.candidate_due_at.isoformat() == "2026-08-23T04:30:00+00:00"


def test_superseded_rule_and_missing_trigger_are_rejected():
    with pytest.raises(ValueError):
        trigger(superseded_by="rule-2")
    with pytest.raises(ValueError):
        trigger(triggered_at=datetime(2026, 8, 21, 15))


def test_calendar_simulation_is_approval_gated_idempotent_and_reconciles():
    event = CalendarEvent(
        idempotency_key="deadline-1",
        summary="Review deadline",
        starts_at=datetime(2026, 8, 24, 15, tzinfo=UTC),
        ends_at=datetime(2026, 8, 24, 16, tzinfo=UTC),
        timezone="America/Chicago",
    )
    simulation = CalendarSimulation()
    with pytest.raises(WorkflowError):
        simulation.dispatch(event)
    approved = event.validate_for_simulation().approve_for_simulation()
    receipt = simulation.dispatch(approved)
    assert simulation.dispatch(approved) == receipt
    assert simulation.reconcile(receipt, approved)
    assert "BEGIN:VCALENDAR" in approved.to_ics()
