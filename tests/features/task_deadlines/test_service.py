from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError
from sklegal_api.features.task_deadlines.contracts import (
    DeadlineReviewCommand,
    DeadlineTransitionCommand,
    SimulationHandoffCommand,
    TaskTransitionCommand,
    UpsertTaskCommand,
)
from sklegal_api.features.task_deadlines.service import TaskDeadlineServiceError
from sklegal_persistence.features.task_deadlines.models import (
    RuleAuthorityEvidence,
    TriggerEvidence,
)

from .helpers import (
    APPROVAL,
    HASH_A,
    HASH_C,
    MATTER,
    NOW,
    OTHER_MATTER,
    PRINCIPAL,
    TENANT,
    WORK_PRODUCT,
    WORK_PRODUCT_CONTENT_SHA256,
    WORK_PRODUCT_VERSION,
    WORK_PRODUCT_VERSION_NUMBER,
    access,
    composition,
    deadline_command,
)


def _create_task(*, assigned: bool = True):
    service, repository, policy, gate = composition()
    receipt = service.create_task(
        context=access(),
        matter_id=MATTER,
        idempotency_key="task-create-1",
        command=UpsertTaskCommand(
            title="Prepare response",
            description="Prepare the public-synthetic response draft.",
            assigned_principal_id=PRINCIPAL if assigned else None,
        ),
    )
    return service, repository, policy, gate, receipt


def _transition(service, task, key: str, transition: str, **values):
    return service.transition_task(
        context=access(),
        matter_id=MATTER,
        task_id=task.task_id,
        idempotency_key=key,
        command=TaskTransitionCommand.model_validate(
            {
                "expected_version": task.version,
                "transition": transition,
                **values,
            }
        ),
    ).task


def test_task_create_is_atomic_idempotent_and_audited() -> None:
    service, repository, _, _, first = _create_task()
    second = service.create_task(
        context=access(),
        matter_id=MATTER,
        idempotency_key="task-create-1",
        command=UpsertTaskCommand(
            title="Prepare response",
            description="Prepare the public-synthetic response draft.",
            assigned_principal_id=PRINCIPAL,
        ),
    )
    assert first == second
    assert first.task.status == "ready"
    assert first.task.version == 1
    assert len(repository.audit_events) == 1
    assert len(repository.outbox_records) == 1
    assert repository.outbox_records[0].dispatch_allowed is False
    assert first.task.audit_id == first.audit_id
    assert first.task.outbox_id == first.outbox_id


def test_idempotency_key_reuse_with_different_bytes_fails_closed() -> None:
    service, _, _, _, _ = _create_task()
    with pytest.raises(TaskDeadlineServiceError, match="idempotency_conflict"):
        service.create_task(
            context=access(),
            matter_id=MATTER,
            idempotency_key="task-create-1",
            command=UpsertTaskCommand(
                title="Changed title",
                description="Prepare the public-synthetic response draft.",
                assigned_principal_id=PRINCIPAL,
            ),
        )


def test_task_failure_retry_and_reconciliation_are_explicit_versions() -> None:
    service, repository, _, _, created = _create_task()
    started = _transition(service, created.task, "task-start", "start")
    failed = _transition(
        service,
        started,
        "task-fail",
        "fail",
        failure_code="synthetic_dependency_timeout",
    )
    retry = _transition(
        service,
        failed,
        "task-retry",
        "retry",
        failure_code="synthetic_dependency_timeout",
    )
    required = _transition(
        service,
        retry,
        "task-reconcile-required",
        "require_reconciliation",
        reason="Retry result requires human reconciliation.",
    )
    reconciled = _transition(
        service,
        required,
        "task-reconcile",
        "reconcile",
    )
    assert [
        started.status,
        failed.status,
        retry.status,
        required.status,
        reconciled.status,
    ] == [
        "in_progress",
        "failed",
        "retry_pending",
        "reconciliation_required",
        "reconciled",
    ]
    assert reconciled.version == 6
    assert reconciled.reconciliation_of_version == retry.version
    assert len(repository.audit_events) == 6
    assert len(reconciled.provenance) == 6
    assert [event.outcome for event in repository.audit_events] == [
        "succeeded",
        "succeeded",
        "failed",
        "retry_pending",
        "reconciliation_required",
        "reconciled",
    ]


def test_terminal_task_cannot_transition_and_cancel_is_attributed() -> None:
    service, _, _, _, created = _create_task()
    cancelled = _transition(
        service,
        created.task,
        "task-cancel",
        "cancel",
        reason="Human reviewer cancelled the synthetic Task.",
    )
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at == NOW
    with pytest.raises(TaskDeadlineServiceError, match="precondition_failed"):
        _transition(service, cancelled, "task-restart", "start")


def test_task_due_time_must_bind_an_exact_operative_deadline() -> None:
    service, _, _, _ = composition()
    calculated = service.compute_deadline(
        context=access(),
        matter_id=MATTER,
        idempotency_key="task-linked-deadline",
        command=deadline_command(),
    ).deadline
    with pytest.raises(TaskDeadlineServiceError, match="precondition_failed"):
        service.create_task(
            context=access(),
            matter_id=MATTER,
            idempotency_key="task-unreviewed-link",
            command=UpsertTaskCommand(
                title="Unsafe due date",
                description="The candidate date is not operative.",
                deadline_id=calculated.deadline_id,
                due_at=calculated.candidate_due_at,
            ),
        )
    operative = service.review_deadline(
        context=access(),
        matter_id=MATTER,
        deadline_id=calculated.deadline_id,
        idempotency_key="task-linked-review",
        command=DeadlineReviewCommand(
            expected_version=calculated.version,
            decision="accepted",
            rationale="Exact synthetic calculation evidence reviewed.",
        ),
    ).deadline
    with pytest.raises(TaskDeadlineServiceError, match="precondition_failed"):
        service.create_task(
            context=access(),
            matter_id=MATTER,
            idempotency_key="task-wrong-due",
            command=UpsertTaskCommand(
                title="Mismatched due date",
                description="This date does not match the operative Deadline.",
                deadline_id=operative.deadline_id,
                due_at=operative.operative_due_at + timedelta(days=1),
            ),
        )
    linked = service.create_task(
        context=access(),
        matter_id=MATTER,
        idempotency_key="task-exact-due",
        command=UpsertTaskCommand(
            title="Linked Task",
            description="This Task binds the exact reviewed Deadline.",
            deadline_id=operative.deadline_id,
            due_at=operative.operative_due_at,
        ),
    ).task
    assert linked.deadline_id == operative.deadline_id
    assert linked.due_at == operative.operative_due_at


def test_business_day_deadline_is_deterministic_and_nonoperative_until_review() -> None:
    service, repository, _, _ = composition()
    first = service.compute_deadline(
        context=access(),
        matter_id=MATTER,
        idempotency_key="deadline-1",
        command=deadline_command(),
    )
    second = service.compute_deadline(
        context=access(),
        matter_id=MATTER,
        idempotency_key="deadline-1",
        command=deadline_command(),
    )
    assert first == second
    assert first.deadline.state == "calculated"
    assert first.deadline.review_state == "pending"
    assert first.deadline.operative_due_at is None
    assert first.deadline.candidate_due_at == datetime(2026, 8, 25, 15, 0, tzinfo=UTC)
    assert [item.offset_days for item in first.deadline.reminders] == [7, 1, 0]
    assert all(item.external_effect is False for item in first.deadline.reminders)
    assert len(repository.audit_events) == 1


def test_business_day_include_trigger_skips_weekend_trigger() -> None:
    service, _, _, _ = composition()
    command = deadline_command()
    trigger = TriggerEvidence.model_validate(
        {
            **command.trigger.model_dump(),
            "occurred_at": datetime(2026, 8, 22, 15, 0, tzinfo=UTC),
        }
    )
    rule = RuleAuthorityEvidence.model_validate(
        {
            **command.rule.model_dump(),
            "interval_days": 1,
            "include_trigger_day": True,
        }
    )
    calendar = command.calendar.model_copy(update={"holidays": ()})
    result = service.compute_deadline(
        context=access(),
        matter_id=MATTER,
        idempotency_key="weekend-trigger",
        command=command.model_copy(
            update={"trigger": trigger, "rule": rule, "calendar": calendar}
        ),
    ).deadline
    assert result.candidate_due_at == datetime(2026, 8, 24, 15, 0, tzinfo=UTC)


def test_deadline_evidence_rejects_naive_and_future_timestamps() -> None:
    command = deadline_command()
    trigger_data = command.trigger.model_dump()
    trigger_data["occurred_at"] = datetime(2026, 8, 21, 15, 0)
    with pytest.raises(ValidationError, match="timezone aware"):
        TriggerEvidence.model_validate(trigger_data)

    rule_data = command.rule.model_dump()
    rule_data["verified_at"] = datetime(2026, 8, 22, 18, 0)
    with pytest.raises(ValidationError, match="timezone aware"):
        RuleAuthorityEvidence.model_validate(rule_data)

    service, repository, _, _ = composition()
    future_rule = command.rule.model_copy(
        update={"verified_at": NOW + timedelta(minutes=1)}
    )
    with pytest.raises(TaskDeadlineServiceError, match="validation_failed"):
        service.compute_deadline(
            context=access(),
            matter_id=MATTER,
            idempotency_key="future-authority-verification",
            command=command.model_copy(update={"rule": future_rule}),
        )
    assert repository.list_deadlines(TENANT, MATTER) == ()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="host TZ control unavailable")
def test_aware_deadline_is_identical_across_host_time_zones() -> None:
    original = os.environ.get("TZ")
    outputs: list[datetime | None] = []
    try:
        for host_zone in ("UTC", "America/Los_Angeles"):
            os.environ["TZ"] = host_zone
            time.tzset()
            service, _, _, _ = composition()
            outputs.append(
                service.compute_deadline(
                    context=access(),
                    matter_id=MATTER,
                    idempotency_key=f"host-zone-{host_zone}",
                    command=deadline_command(),
                ).deadline.candidate_due_at
            )
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()
    assert outputs == [
        datetime(2026, 8, 25, 15, 0, tzinfo=UTC),
        datetime(2026, 8, 25, 15, 0, tzinfo=UTC),
    ]


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"trigger_state": "missing"}, "trigger_missing"),
        ({"trigger_state": "disputed"}, "trigger_disputed"),
        ({"rule_state": "stale"}, "rule_stale"),
        ({"rule_state": "uncertain"}, "rule_uncertain"),
        ({"calendar_state": "missing"}, "holiday_calendar_missing"),
        ({"calendar_state": "stale"}, "holiday_calendar_stale"),
        ({"time_zone": "Invalid/Synthetic"}, "time_zone_unknown"),
    ],
)
def test_uncertain_deadline_inputs_remain_nonoperative(
    overrides: dict[str, str], expected_code: str
) -> None:
    service, _, _, _ = composition()
    result = service.compute_deadline(
        context=access(),
        matter_id=MATTER,
        idempotency_key=f"deadline-{expected_code}",
        command=deadline_command(**overrides),
    ).deadline
    assert result.state in {"blocked", "uncertain"}
    assert result.candidate_due_at is None
    assert result.operative_due_at is None
    assert expected_code in result.uncertainty_codes
    with pytest.raises(TaskDeadlineServiceError, match="precondition_failed"):
        service.review_deadline(
            context=access(),
            matter_id=MATTER,
            deadline_id=result.deadline_id,
            idempotency_key=f"review-{expected_code}",
            command=DeadlineReviewCommand(
                expected_version=result.version,
                decision="accepted",
                rationale="Synthetic review cannot cure missing evidence.",
            ),
        )


def test_human_review_makes_deadline_operative_and_enables_simulation_only() -> None:
    service, repository, _, gate = composition()
    calculated = service.compute_deadline(
        context=access(),
        matter_id=MATTER,
        idempotency_key="deadline-operative",
        command=deadline_command(),
    ).deadline
    reviewed = service.review_deadline(
        context=access(),
        matter_id=MATTER,
        deadline_id=calculated.deadline_id,
        idempotency_key="deadline-review",
        command=DeadlineReviewCommand(
            expected_version=1,
            decision="accepted",
            rationale="Trigger, rule, Authority, and calendar evidence reviewed.",
        ),
    ).deadline
    assert reviewed.state == "operative"
    assert reviewed.operative_due_at == reviewed.candidate_due_at
    created = service.create_task(
        context=access(),
        matter_id=MATTER,
        idempotency_key="simulation-linked-task",
        command=UpsertTaskCommand(
            title="Prepare exact approved email",
            description="Bind the simulation to the operative Deadline.",
            assigned_principal_id=PRINCIPAL,
            deadline_id=reviewed.deadline_id,
            due_at=reviewed.operative_due_at,
        ),
    )
    gate.allowed.add(
        (
            TENANT,
            MATTER,
            created.task.task_id,
            reviewed.deadline_id,
            WORK_PRODUCT,
            WORK_PRODUCT_VERSION,
            WORK_PRODUCT_VERSION_NUMBER,
            WORK_PRODUCT_CONTENT_SHA256,
            APPROVAL,
            HASH_C,
            "email",
            HASH_A,
        )
    )
    command = SimulationHandoffCommand(
        task_id=created.task.task_id,
        deadline_id=reviewed.deadline_id,
        work_product_id=WORK_PRODUCT,
        work_product_version_id=WORK_PRODUCT_VERSION,
        work_product_version_number=WORK_PRODUCT_VERSION_NUMBER,
        work_product_content_sha256=WORK_PRODUCT_CONTENT_SHA256,
        approval_id=APPROVAL,
        approval_snapshot_sha256=HASH_C,
        approval_current=True,
        destination_sha256=HASH_A,
    )
    first = service.create_simulation_handoff(
        context=access("action.email.prepare", "external_action_preparation"),
        matter_id=MATTER,
        idempotency_key="simulate-1",
        command=command,
    )
    second = service.create_simulation_handoff(
        context=access("action.email.prepare", "external_action_preparation"),
        matter_id=MATTER,
        idempotency_key="simulate-1",
        command=command,
    )
    assert first == second
    assert first.simulation.state == "simulated"
    assert first.simulation.external_effect is False
    assert first.simulation.connector_invoked is False
    assert first.simulation.dispatch_attempted is False
    assert repository.outbox_records[-1].topic == "external_action.simulation"
    assert repository.outbox_records[-1].dispatch_allowed is False


def test_simulation_requires_exact_task_deadline_and_approval_scope() -> None:
    service, repository, _, gate = composition()
    calculated = service.compute_deadline(
        context=access(),
        matter_id=MATTER,
        idempotency_key="binding-deadline",
        command=deadline_command(),
    ).deadline
    operative = service.review_deadline(
        context=access(),
        matter_id=MATTER,
        deadline_id=calculated.deadline_id,
        idempotency_key="binding-review",
        command=DeadlineReviewCommand(
            expected_version=calculated.version,
            decision="accepted",
            rationale="Exact binding Deadline reviewed.",
        ),
    ).deadline
    task = service.create_task(
        context=access(),
        matter_id=MATTER,
        idempotency_key="binding-task",
        command=UpsertTaskCommand(
            title="Bound simulation Task",
            description="Exact Task and Deadline binding proof.",
            assigned_principal_id=PRINCIPAL,
            deadline_id=operative.deadline_id,
            due_at=operative.operative_due_at,
        ),
    ).task
    gate.allowed.add(
        (
            TENANT,
            MATTER,
            task.task_id,
            operative.deadline_id,
            WORK_PRODUCT,
            WORK_PRODUCT_VERSION,
            WORK_PRODUCT_VERSION_NUMBER,
            WORK_PRODUCT_CONTENT_SHA256,
            APPROVAL,
            HASH_C,
            "email",
            HASH_A,
        )
    )
    exact = SimulationHandoffCommand(
        task_id=task.task_id,
        deadline_id=operative.deadline_id,
        work_product_id=WORK_PRODUCT,
        work_product_version_id=WORK_PRODUCT_VERSION,
        work_product_version_number=WORK_PRODUCT_VERSION_NUMBER,
        work_product_content_sha256=WORK_PRODUCT_CONTENT_SHA256,
        approval_id=APPROVAL,
        approval_snapshot_sha256=HASH_C,
        approval_current=True,
        destination_sha256=HASH_A,
    )
    before = len(repository.audit_events)
    with pytest.raises(TaskDeadlineServiceError, match="precondition_failed"):
        service.create_simulation_handoff(
            context=access("action.email.prepare", "external_action_preparation"),
            matter_id=MATTER,
            idempotency_key="binding-omitted-deadline",
            command=exact.model_copy(update={"deadline_id": None}),
        )
    with pytest.raises(TaskDeadlineServiceError, match="precondition_failed"):
        service.create_simulation_handoff(
            context=access("action.email.prepare", "external_action_preparation"),
            matter_id=MATTER,
            idempotency_key="binding-wrong-destination",
            command=exact.model_copy(update={"destination_sha256": "e5" * 32}),
        )
    with pytest.raises(TaskDeadlineServiceError, match="precondition_failed"):
        service.create_simulation_handoff(
            context=access("action.email.prepare", "external_action_preparation"),
            matter_id=MATTER,
            idempotency_key="binding-wrong-work-product-content",
            command=exact.model_copy(update={"work_product_content_sha256": "f6" * 32}),
        )
    assert len(repository.audit_events) == before


def test_deadline_supersession_never_creates_two_operative_deadlines() -> None:
    service, _, _, _ = composition()
    original = service.compute_deadline(
        context=access(),
        matter_id=MATTER,
        idempotency_key="supersession-original",
        command=deadline_command(),
    ).deadline
    original = service.review_deadline(
        context=access(),
        matter_id=MATTER,
        deadline_id=original.deadline_id,
        idempotency_key="supersession-original-review",
        command=DeadlineReviewCommand(
            expected_version=original.version,
            decision="accepted",
            rationale="Original synthetic Deadline reviewed.",
        ),
    ).deadline
    replacement_command = deadline_command().model_copy(
        update={"supersedes_deadline_id": original.deadline_id}
    )
    replacement = service.compute_deadline(
        context=access(),
        matter_id=MATTER,
        idempotency_key="supersession-replacement",
        command=replacement_command,
    ).deadline
    review = DeadlineReviewCommand(
        expected_version=replacement.version,
        decision="accepted",
        rationale="Replacement synthetic Deadline reviewed.",
    )
    with pytest.raises(TaskDeadlineServiceError, match="precondition_failed"):
        service.review_deadline(
            context=access(),
            matter_id=MATTER,
            deadline_id=replacement.deadline_id,
            idempotency_key="supersession-too-early",
            command=review,
        )
    original = service.transition_deadline(
        context=access(),
        matter_id=MATTER,
        deadline_id=original.deadline_id,
        idempotency_key="supersession-cancel-original",
        command=DeadlineTransitionCommand(
            expected_version=original.version,
            transition="cancel",
            reason="Exact replacement Deadline is ready for review.",
        ),
    ).deadline
    replacement = service.review_deadline(
        context=access(),
        matter_id=MATTER,
        deadline_id=replacement.deadline_id,
        idempotency_key="supersession-accepted",
        command=review,
    ).deadline
    assert original.state == "cancelled"
    assert replacement.state == "operative"
    assert replacement.supersedes_deadline_id == original.deadline_id


def test_simulation_rejects_stale_approval_and_unreviewed_deadline() -> None:
    service, _, _, _, created = _create_task()
    deadline = service.compute_deadline(
        context=access(),
        matter_id=MATTER,
        idempotency_key="deadline-unreviewed",
        command=deadline_command(),
    ).deadline
    command = SimulationHandoffCommand(
        task_id=created.task.task_id,
        deadline_id=deadline.deadline_id,
        work_product_id=WORK_PRODUCT,
        work_product_version_id=WORK_PRODUCT_VERSION,
        work_product_version_number=WORK_PRODUCT_VERSION_NUMBER,
        work_product_content_sha256=WORK_PRODUCT_CONTENT_SHA256,
        approval_id=APPROVAL,
        approval_snapshot_sha256=HASH_C,
        approval_current=True,
        destination_sha256=HASH_A,
    )
    with pytest.raises(TaskDeadlineServiceError, match="precondition_failed"):
        service.create_simulation_handoff(
            context=access("action.email.prepare", "external_action_preparation"),
            matter_id=MATTER,
            idempotency_key="simulate-unreviewed",
            command=command,
        )


def test_deadline_failure_retry_reconciliation_and_cancel_are_explicit() -> None:
    service, repository, _, _ = composition()
    current = service.compute_deadline(
        context=access(),
        matter_id=MATTER,
        idempotency_key="deadline-lifecycle",
        command=deadline_command(),
    ).deadline

    def transition(key: str, name: str, **values):
        nonlocal current
        current = service.transition_deadline(
            context=access(),
            matter_id=MATTER,
            deadline_id=current.deadline_id,
            idempotency_key=key,
            command=DeadlineTransitionCommand.model_validate(
                {"expected_version": current.version, "transition": name, **values}
            ),
        ).deadline
        return current

    assert (
        transition(
            "deadline-fail", "fail", failure_code="synthetic_calculator_failure"
        ).state
        == "failed"
    )
    assert (
        transition(
            "deadline-retry", "retry", failure_code="synthetic_calculator_failure"
        ).state
        == "retry_pending"
    )
    assert (
        transition(
            "deadline-reconcile-required",
            "require_reconciliation",
            reason="Retry result differs from the original calculation.",
        ).state
        == "reconciliation_required"
    )
    assert transition("deadline-reconcile", "reconcile").state == "reconciled"
    assert (
        transition(
            "deadline-cancel",
            "cancel",
            reason="Superseded by a separately reviewed Deadline.",
        ).state
        == "cancelled"
    )
    assert [event.outcome for event in repository.audit_events] == [
        "succeeded",
        "failed",
        "retry_pending",
        "reconciliation_required",
        "reconciled",
        "cancelled",
    ]


def test_cross_matter_reads_do_not_disclose_existing_resource() -> None:
    service, _, _, _, created = _create_task()
    with pytest.raises(TaskDeadlineServiceError, match="resource_unavailable"):
        service.get_task(
            context=access("matter.read", "matter_management", matter_id=OTHER_MATTER),
            matter_id=OTHER_MATTER,
            task_id=created.task.task_id,
        )


def test_cross_tenant_and_wrong_capability_fail_before_lookup() -> None:
    service, repository, _, _, _ = _create_task()
    before = len(repository.audit_events)
    foreign_tenant = UUID("10000000-0000-4000-8000-000000000002")
    with pytest.raises(TaskDeadlineServiceError, match="access_denied"):
        service.list_tasks(
            context=access("matter.read").model_copy(
                update={"tenant_id": foreign_tenant}
            ),
            matter_id=MATTER,
        )
    with pytest.raises(TaskDeadlineServiceError, match="access_denied"):
        service.list_tasks(context=access("matter.manage"), matter_id=MATTER)
    assert len(repository.audit_events) == before


def test_revocation_policy_outage_and_atomic_evidence_outage_fail_closed() -> None:
    service, repository, policy, _ = composition()
    with pytest.raises(TaskDeadlineServiceError, match="access_denied"):
        service.create_task(
            context=access(revoked=True),
            matter_id=MATTER,
            idempotency_key="revoked",
            command=UpsertTaskCommand(title="X", description="Y"),
        )
    policy.available = False
    with pytest.raises(TaskDeadlineServiceError, match="policy_unavailable"):
        service.create_task(
            context=access(),
            matter_id=MATTER,
            idempotency_key="policy-outage",
            command=UpsertTaskCommand(title="X", description="Y"),
        )
    policy.available = True
    repository.outbox_available = False
    with pytest.raises(TaskDeadlineServiceError, match="dependency_unavailable"):
        service.create_task(
            context=access(),
            matter_id=MATTER,
            idempotency_key="outbox-outage",
            command=UpsertTaskCommand(title="X", description="Y"),
        )
    assert repository.list_tasks(TENANT, MATTER) == ()
