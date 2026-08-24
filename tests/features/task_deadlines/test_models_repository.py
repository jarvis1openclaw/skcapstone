from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sklegal_api.features.task_deadlines.contracts import UpsertTaskCommand
from sklegal_persistence.features.task_deadlines.models import (
    HolidayCalendarEvidence,
    TriggerEvidence,
)
from sklegal_persistence.features.task_deadlines.repository import (
    TaskDeadlineVersionConflict,
    validate_write_evidence,
)

from .helpers import HASH_A, MATTER, NOW, access, composition


def test_strict_evidence_rejects_partial_trigger_and_unordered_calendar() -> None:
    with pytest.raises(ValidationError, match="complete evidence"):
        TriggerEvidence.model_validate(
            {
                "state": "confirmed",
                "occurredAt": datetime(2026, 8, 21, tzinfo=UTC),
                "evidenceSha256": HASH_A,
            }
        )
    with pytest.raises(ValidationError, match="ordered"):
        HolidayCalendarEvidence.model_validate(
            {
                "state": "current",
                "calendarId": "synthetic.calendar",
                "revision": HASH_A,
                "timeZone": "UTC",
                "holidays": ["2026-08-24", "2026-01-01"],
            }
        )


def test_repository_rejects_audit_or_outbox_not_bound_to_exact_record() -> None:
    service, repository, _, _ = composition()
    receipt = service.create_task(
        context=access(),
        matter_id=MATTER,
        idempotency_key="bound-task",
        command=UpsertTaskCommand(title="Bound Task", description="Synthetic data."),
    )
    audit = repository.audit_events[0]
    outbox = repository.outbox_records[0]
    with pytest.raises(TaskDeadlineVersionConflict, match="does not bind"):
        validate_write_evidence(
            receipt.task,
            audit.model_copy(update={"resource_sha256": HASH_A}),
            outbox,
        )
    with pytest.raises(TaskDeadlineVersionConflict, match="does not bind"):
        validate_write_evidence(
            receipt.task,
            audit,
            outbox.model_copy(update={"payload_sha256": HASH_A}),
        )


def test_repository_requires_contiguous_versions_without_partial_evidence() -> None:
    service, repository, _, _ = composition()
    created = service.create_task(
        context=access(),
        matter_id=MATTER,
        idempotency_key="version-task",
        command=UpsertTaskCommand(title="Versioned Task", description="Synthetic."),
    )
    record = created.task.model_copy(
        update={
            "version": 3,
            "updated_at": NOW,
        }
    )
    audit = repository.audit_events[0].model_copy(
        update={"resource_version": 3, "resource_sha256": HASH_A}
    )
    outbox = repository.outbox_records[0].model_copy(update={"payload_sha256": HASH_A})
    with pytest.raises(TaskDeadlineVersionConflict):
        repository.commit_task(
            operation="task.transition",
            idempotency_key_sha256=HASH_A,
            request_sha256=HASH_A,
            expected_version=1,
            record=record,
            audit=audit,
            outbox=outbox,
        )
