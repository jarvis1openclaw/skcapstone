"""Public-synthetic factories for the TASK-01 feature tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sklegal_api.features.task_deadlines.contracts import ComputeDeadlineCommand
from sklegal_api.features.task_deadlines.service import (
    StaticSimulationGateVerifier,
    StaticTaskDeadlinePolicy,
    TaskDeadlineAccessContext,
    TaskDeadlineService,
)
from sklegal_persistence.features.task_deadlines.models import (
    HolidayCalendarEvidence,
    RuleAuthorityEvidence,
    TriggerEvidence,
)
from sklegal_persistence.features.task_deadlines.repository import (
    InMemoryTaskDeadlineRepository,
)

NOW = datetime(2026, 8, 23, 18, 0, tzinfo=UTC)
TENANT = UUID("10000000-0000-4000-8000-000000000001")
MATTER = UUID("30000000-0000-4000-8000-000000000001")
OTHER_MATTER = UUID("30000000-0000-4000-8000-000000000002")
PRINCIPAL = UUID("20000000-0000-4000-8000-000000000001")
TRIGGER_EVENT = UUID("31000000-0000-4000-8000-000000000001")
FACT = UUID("32000000-0000-4000-8000-000000000001")
AUTHORITY = UUID("33000000-0000-4000-8000-000000000001")
WORK_PRODUCT = UUID("34000000-0000-4000-8000-000000000000")
WORK_PRODUCT_VERSION = UUID("34000000-0000-4000-8000-000000000001")
WORK_PRODUCT_VERSION_NUMBER = 2
APPROVAL = UUID("35000000-0000-4000-8000-000000000001")
HASH_A = "a1" * 32
HASH_B = "b2" * 32
HASH_C = "c3" * 32
HASH_D = "d4" * 32
WORK_PRODUCT_CONTENT_SHA256 = HASH_B


def access(
    capability: str = "matter.manage",
    purpose: str = "matter_management",
    *,
    matter_id: UUID = MATTER,
    revoked: bool = False,
) -> TaskDeadlineAccessContext:
    return TaskDeadlineAccessContext.model_validate(
        {
            "tenant_id": TENANT,
            "matter_id": matter_id,
            "principal_id": PRINCIPAL,
            "capability": capability,
            "purpose": purpose,
            "authorization_decision_id": UUID("36000000-0000-4000-8000-000000000001"),
            "credential_expires_at": NOW + timedelta(hours=1),
            "revoked": revoked,
        }
    )


def deadline_command(
    *,
    trigger_state: str = "confirmed",
    rule_state: str = "current",
    calendar_state: str = "current",
    time_zone: str = "America/Chicago",
) -> ComputeDeadlineCommand:
    trigger = TriggerEvidence.model_validate(
        {
            "state": trigger_state,
            "trigger_event_id": TRIGGER_EVENT if trigger_state != "missing" else None,
            "fact_assertion_id": FACT if trigger_state != "missing" else None,
            "occurred_at": (
                datetime(2026, 8, 21, 15, 0, tzinfo=UTC)
                if trigger_state != "missing"
                else None
            ),
            "evidence_sha256": HASH_A if trigger_state != "missing" else None,
        }
    )
    rule = RuleAuthorityEvidence.model_validate(
        {
            "state": rule_state,
            "rule_id": "illinois.response.v1",
            "rule_version": "2026.1",
            "rule_sha256": HASH_B,
            "authority_id": AUTHORITY,
            "authority_version": "2026-08-01",
            "authority_content_sha256": HASH_C,
            "authority_span_sha256": HASH_D,
            "verified_at": NOW - timedelta(days=1),
            "valid_until": NOW + timedelta(days=30),
            "interval_days": 1,
            "convention": "business_days",
            "include_trigger_day": False,
        }
    )
    calendar = HolidayCalendarEvidence.model_validate(
        {
            "state": calendar_state,
            "calendar_id": "illinois.state.2026",
            "revision": HASH_C,
            "time_zone": time_zone,
            "holidays": (date(2026, 8, 24),),
        }
    )
    return ComputeDeadlineCommand(
        title="Response deadline",
        trigger=trigger,
        rule=rule,
        calendar=calendar,
        reminder_offsets_days=(7, 1, 0),
    )


def composition() -> tuple[
    TaskDeadlineService,
    InMemoryTaskDeadlineRepository,
    StaticTaskDeadlinePolicy,
    StaticSimulationGateVerifier,
]:
    repository = InMemoryTaskDeadlineRepository()
    repository.set_matter_members(TENANT, MATTER, {PRINCIPAL})
    repository.set_matter_members(TENANT, OTHER_MATTER, {PRINCIPAL})
    grants = {
        (TENANT, matter_id, PRINCIPAL, capability, purpose)
        for matter_id in (MATTER, OTHER_MATTER)
        for capability, purpose in (
            ("matter.read", "matter_management"),
            ("matter.manage", "matter_management"),
            ("action.email.prepare", "external_action_preparation"),
        )
    }
    policy = StaticTaskDeadlinePolicy(
        grants=grants,
        revision=HASH_D,
        valid_until=NOW + timedelta(hours=2),
    )
    gate = StaticSimulationGateVerifier(set())
    service = TaskDeadlineService(
        repository=repository,
        policy=policy,
        simulation_gate=gate,
        clock=lambda: NOW,
    )
    return service, repository, policy, gate
