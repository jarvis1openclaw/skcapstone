from __future__ import annotations

import json
from pathlib import Path

from sklegal_persistence.features.task_deadlines.models import (
    DeadlineRecord,
    SimulationReceipt,
    TaskRecord,
)

FIXTURE = (
    Path(__file__).parents[3]
    / "tests"
    / "fixtures"
    / "mvp"
    / "fragments"
    / "task_deadlines"
    / "public-synthetic-task-deadlines-v1.json"
)


def test_public_synthetic_fixture_is_exact_and_internally_linked() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    tasks = tuple(TaskRecord.model_validate(item) for item in raw["tasks"])
    deadlines = tuple(DeadlineRecord.model_validate(item) for item in raw["deadlines"])
    simulations = tuple(
        SimulationReceipt.model_validate(item) for item in raw["simulations"]
    )
    assert raw["schemaVersion"] == "sklegal.task-deadline-fragment/v1"
    assert len(tasks) == len(deadlines) == len(simulations) == 1
    assert tasks[0].tenant_id == deadlines[0].tenant_id == simulations[0].tenant_id
    assert tasks[0].matter_id == deadlines[0].matter_id == simulations[0].matter_id
    assert simulations[0].task_id == tasks[0].task_id
    assert simulations[0].deadline_id == deadlines[0].deadline_id
    assert simulations[0].work_product_version_number == 2
    assert len(simulations[0].work_product_content_sha256) == 64
    assert deadlines[0].state == "operative"
    assert deadlines[0].review_state == "accepted"
    assert simulations[0].external_effect is False
    assert simulations[0].connector_invoked is False
    assert simulations[0].dispatch_attempted is False
