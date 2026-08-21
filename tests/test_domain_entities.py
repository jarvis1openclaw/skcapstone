from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import sklegal_domain
from pydantic import ValidationError
from sklegal_domain import (
    Approval,
    ApprovalStatus,
    ArtifactBinding,
    Authority,
    AuthorityStatus,
    Claim,
    ClaimStatus,
    Client,
    Communication,
    CommunicationStatus,
    CustodyEvent,
    Deadline,
    DeadlineCalculation,
    DeadlineStatus,
    DomainError,
    DomainTransitionError,
    EffectiveInterval,
    Engagement,
    EngagementStatus,
    EvidenceItem,
    EvidenceStatus,
    Execution,
    ExecutionEvent,
    ExecutionReceipt,
    ExecutionStatus,
    ExecutionStep,
    FactAssertion,
    FactReviewStatus,
    Forum,
    IdentityMutationError,
    LegacyAlias,
    LegacyRecordKind,
    Matter,
    MatterEvent,
    MatterEventStatus,
    MatterStatus,
    Party,
    PartyRole,
    PartyRoleStatus,
    Proceeding,
    ProceedingStatus,
    RecordCompleteness,
    SourceReference,
    Task,
    TaskStatus,
    Tenant,
    TenantStatus,
    TensionGroup,
    TensionStatus,
    Transaction,
    TypedValue,
    ValidationOutcome,
    ValidationResult,
    WorkProduct,
    WorkProductStatus,
    WorkProductVersion,
    WorkProductVersionStatus,
    effective_at,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def uid(value: int) -> UUID:
    return UUID(int=(1 << 127) | value)


def sha(value: str = "a") -> str:
    return value * 64


def audit(identifier: int) -> dict[str, object]:
    return {"id": uid(identifier), "created_at": T0, "updated_at": T0}


def protected(identifier: int) -> dict[str, object]:
    return {**audit(identifier), "tenant_id": uid(1)}


def scoped(identifier: int) -> dict[str, object]:
    return {**protected(identifier), "matter_id": uid(4)}


def source(identifier: int = 500) -> SourceReference:
    return SourceReference(
        source_reference_id=uid(identifier),
        source_system="synthetic-fixture",
        source_version="v1",
        content_sha256=sha("b"),
        locator="fixture://domain/source",
        observed_at=T0,
    )


def binding(identifier: int = 600, digest: str = "c") -> ArtifactBinding:
    return ArtifactBinding(
        artifact_id=uid(identifier),
        artifact_version=1,
        content_sha256=sha(digest),
    )


def validation(
    *,
    subject: ArtifactBinding | None = None,
    identifier: int = 610,
    at: datetime = T0,
    outcome: ValidationOutcome = ValidationOutcome.PASSED,
    tenant_id: UUID = uid(1),
    matter_id: UUID = uid(4),
) -> ValidationResult:
    return ValidationResult(
        id=uid(identifier),
        tenant_id=tenant_id,
        matter_id=matter_id,
        created_at=T0,
        updated_at=at,
        subject=subject or binding(),
        outcome=outcome,
        check_ids=("fixture.schema", "fixture.hash"),
        validator_principal_id=uid(700),
        validated_at=at,
        rationale="Synthetic checks reached the recorded outcome.",
    )


def approved_decision(
    *,
    subject: ArtifactBinding | None = None,
    identifier: int = 110,
    at: datetime = T0,
    tenant_id: UUID = uid(1),
    matter_id: UUID = uid(4),
) -> Approval:
    pending = Approval(
        id=uid(identifier),
        tenant_id=tenant_id,
        matter_id=matter_id,
        created_at=T0,
        updated_at=T0,
        subject=subject or binding(),
    )
    return pending.transition_to(
        ApprovalStatus.APPROVED,
        at=at,
        reviewer_principal_id=uid(700),
        decided_at=at,
        rationale="Approved synthetic version exactly as hashed.",
    )


def event(
    execution_id: UUID,
    identifier: int,
    step: ExecutionStep,
    occurred_at: datetime,
    *,
    receipt_id: UUID | None = None,
) -> ExecutionEvent:
    values = scoped(identifier)
    values["created_at"] = occurred_at
    values["updated_at"] = occurred_at
    return ExecutionEvent(
        **values,
        execution_id=execution_id,
        step=step,
        occurred_at=occurred_at,
        correlation_id=f"fixture-{identifier}",
        actor_principal_id=uid(700),
        receipt_id=receipt_id,
    )


class ValueObjectTests(unittest.TestCase):
    def test_strict_models_reject_string_uuid_in_python_mode(self) -> None:
        with self.assertRaises(ValidationError):
            Tenant(
                id=str(uid(1)),
                tenant_id=uid(1),
                name="Fixture Tenant",
                created_at=T0,
                updated_at=T0,
            )

    def test_nil_identifier_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "nil UUID"):
            Tenant(
                id=UUID(int=0),
                tenant_id=UUID(int=0),
                name="Fixture Tenant",
                created_at=T0,
                updated_at=T0,
            )

    def test_naive_and_nonzero_offset_timestamps_are_rejected(self) -> None:
        for invalid in (
            datetime(2026, 1, 1),
            datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=-6))),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValidationError, "UTC"):
                    Tenant(
                        id=uid(1),
                        tenant_id=uid(1),
                        name="Fixture Tenant",
                        created_at=invalid,
                        updated_at=invalid,
                    )

    def test_effective_interval_is_start_inclusive_and_end_exclusive(self) -> None:
        interval = EffectiveInterval(valid_from=T0, valid_to=T0 + timedelta(days=2))
        self.assertTrue(interval.contains(T0))
        self.assertTrue(interval.contains(T0 + timedelta(days=1)))
        self.assertFalse(interval.contains(T0 + timedelta(days=2)))

    def test_unknown_effective_interval_fails_closed(self) -> None:
        unknown = EffectiveInterval()
        known = EffectiveInterval(valid_from=T0)
        self.assertTrue(unknown.is_unknown)
        self.assertFalse(unknown.contains(T0))
        self.assertFalse(unknown.overlaps(known))

    def test_effective_interval_rejects_reversed_or_empty_range(self) -> None:
        for end in (T0, T0 - timedelta(seconds=1)):
            with self.subTest(end=end):
                with self.assertRaisesRegex(ValidationError, "later"):
                    EffectiveInterval(valid_from=T0, valid_to=end)

    def test_effective_interval_overlap(self) -> None:
        first = EffectiveInterval(valid_from=T0, valid_to=T0 + timedelta(days=2))
        overlapping = EffectiveInterval(
            valid_from=T0 + timedelta(days=1), valid_to=T0 + timedelta(days=3)
        )
        adjacent = EffectiveInterval(
            valid_from=T0 + timedelta(days=2), valid_to=T0 + timedelta(days=3)
        )
        self.assertTrue(first.overlaps(overlapping))
        self.assertFalse(first.overlaps(adjacent))

    def test_typed_fact_value_rejects_type_confusion(self) -> None:
        with self.assertRaisesRegex(ValidationError, "value_type"):
            TypedValue(value_type="integer", value=True)

    def test_typed_fact_value_rejects_non_finite_numbers(self) -> None:
        for invalid in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValidationError, "finite"):
                    TypedValue(value_type="number", value=invalid)

    def test_unvalidated_value_copy_and_construction_paths_are_closed(self) -> None:
        artifact = binding()
        with self.assertRaises(DomainError):
            artifact.model_copy(update={"artifact_version": 999})
        with self.assertRaises(DomainError):
            artifact.copy(update={"content_sha256": sha("9")})
        with self.assertRaises(DomainError):
            artifact.__replace__(artifact_id=uid(999))
        with self.assertRaises(DomainError):
            ArtifactBinding.model_construct(
                artifact_id=uid(600),
                artifact_version=0,
                content_sha256="invalid",
            )
        self.assertEqual(artifact, artifact.model_copy())


class LegacyAliasTests(unittest.TestCase):
    def alias(self, **changes: object) -> LegacyAlias:
        values: dict[str, object] = {
            "record_kind": LegacyRecordKind.CONTAINER,
            "legacy_id": "PRB-0000-001",
            "legacy_slug": "synthetic-matter",
            "legacy_path": "archive/synthetic/MATTER.md",
            "source_version": "fixture-v1",
            "content_sha256": sha("d"),
            "observed_at": T0,
        }
        values.update(changes)
        return LegacyAlias(**values)

    def test_both_historical_identifier_shapes_are_preserved(self) -> None:
        container = self.alias()
        activity = self.alias(
            record_kind=LegacyRecordKind.ACTIVITY,
            legacy_id="INC-000",
            legacy_path="archive/synthetic/EVENT.md",
        )
        self.assertEqual("problem", container.record_kind.value)
        self.assertEqual("incident", activity.record_kind.value)

    def test_kind_and_identifier_must_match(self) -> None:
        with self.assertRaisesRegex(ValidationError, "record kind"):
            self.alias(record_kind=LegacyRecordKind.ACTIVITY)

    def test_alias_requires_safe_relative_posix_path(self) -> None:
        for path in ("/absolute/MATTER.md", "../escape", "a/../b", "a\\b"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValidationError, "relative POSIX"):
                    self.alias(legacy_path=path)

    def test_alias_rejects_malformed_slug_and_digest(self) -> None:
        with self.assertRaises(ValidationError):
            self.alias(legacy_slug="folder/slug")
        with self.assertRaises(ValidationError):
            self.alias(content_sha256="not-a-digest")

    def test_alias_is_strict_and_forbids_extra_fields(self) -> None:
        with self.assertRaises(ValidationError):
            self.alias(observed_at="2026-01-01T00:00:00Z")
        with self.assertRaises(ValidationError):
            self.alias(unreviewed_field=True)

    def test_alias_kinds_cannot_attach_to_the_wrong_aggregate(self) -> None:
        container = self.alias()
        activity = self.alias(
            record_kind=LegacyRecordKind.ACTIVITY,
            legacy_id="INC-000",
            legacy_path="archive/synthetic/EVENT.md",
        )
        with self.assertRaisesRegex(ValidationError, "container aliases"):
            Matter(
                **protected(4),
                matter_id=uid(4),
                client_id=uid(2),
                engagement_id=uid(3),
                title="Fixture Matter",
                summary="Synthetic provenance only.",
                aliases=(activity,),
            )
        with self.assertRaisesRegex(ValidationError, "activity aliases"):
            MatterEvent(
                **scoped(12),
                event_type="synthetic_review",
                description="A fixture event.",
                observed_at=T0,
                source_reference=source(),
                aliases=(container,),
            )

    def test_duplicate_event_aliases_are_rejected(self) -> None:
        activity = self.alias(
            record_kind=LegacyRecordKind.ACTIVITY,
            legacy_id="INC-000",
            legacy_path="archive/synthetic/EVENT.md",
        )
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            MatterEvent(
                **scoped(12),
                event_type="synthetic_review",
                description="A fixture event.",
                observed_at=T0,
                source_reference=source(),
                aliases=(activity, activity),
            )


class AggregateIdentityAndTransitionTests(unittest.TestCase):
    def tenant(self) -> Tenant:
        return Tenant(
            **protected(1),
            name="Fixture Tenant",
            status=TenantStatus.PROPOSED,
        )

    def test_frozen_entity_rejects_assignment(self) -> None:
        entity = self.tenant()
        with self.assertRaises(ValidationError):
            entity.name = "Changed"  # type: ignore[misc]

    def test_valid_transition_returns_new_version_with_same_identity(self) -> None:
        entity = self.tenant()
        active = entity.transition_to(TenantStatus.ACTIVE, at=T0 + timedelta(seconds=1))
        self.assertIsNot(active, entity)
        self.assertEqual(entity.id, active.id)
        self.assertEqual(entity.tenant_id, active.tenant_id)
        self.assertEqual(2, active.version)
        self.assertEqual(TenantStatus.ACTIVE, active.status)
        self.assertEqual(TenantStatus.PROPOSED, entity.status)

    def test_invalid_transition_is_rejected(self) -> None:
        with self.assertRaisesRegex(DomainTransitionError, "invalid Tenant transition"):
            self.tenant().transition_to(
                TenantStatus.SUSPENDED, at=T0 + timedelta(seconds=1)
            )

    def test_wrong_machine_status_is_rejected(self) -> None:
        with self.assertRaisesRegex(DomainTransitionError, "another state machine"):
            self.tenant().transition_to(MatterStatus.OPEN, at=T0 + timedelta(seconds=1))

    def test_generic_evolution_cannot_bypass_state_graph(self) -> None:
        with self.assertRaisesRegex(DomainTransitionError, "transition_to"):
            self.tenant().evolve(
                at=T0 + timedelta(seconds=1), status=TenantStatus.ACTIVE
            )

    def test_transition_target_cannot_be_overridden_in_changes(self) -> None:
        with self.assertRaisesRegex(DomainTransitionError, "only as target"):
            self.tenant().transition_to(
                TenantStatus.ACTIVE,
                at=T0 + timedelta(seconds=1),
                status=TenantStatus.CLOSED,
            )

    def test_caller_cannot_override_audit_fields(self) -> None:
        with self.assertRaises(IdentityMutationError):
            self.tenant().evolve(
                at=T0 + timedelta(seconds=1),
                updated_at=T0 + timedelta(days=10),
            )

    def test_unvalidated_entity_copy_and_construction_paths_are_closed(self) -> None:
        tenant = self.tenant()
        with self.assertRaises(IdentityMutationError):
            tenant.model_copy(update={"tenant_id": uid(999)})
        with self.assertRaises(IdentityMutationError):
            tenant.copy(update={"status": TenantStatus.CLOSED})
        with self.assertRaises(IdentityMutationError):
            tenant.__replace__(version=999)
        with self.assertRaises(IdentityMutationError):
            Tenant.model_construct(
                id=uid(1),
                tenant_id=uid(999),
                name="Bypass",
                created_at=T0,
                updated_at=T0,
            )
        self.assertEqual(tenant, tenant.model_copy())

    def test_evolution_cannot_move_backward_in_time(self) -> None:
        with self.assertRaisesRegex(DomainTransitionError, "backward"):
            self.tenant().evolve(at=T0 - timedelta(seconds=1), name="New")

    def test_stable_relationship_identity_cannot_change(self) -> None:
        engagement = Engagement(
            **protected(3),
            client_id=uid(2),
            title="Fixture Engagement",
            scope="Synthetic scope.",
            status=EngagementStatus.PROPOSED,
        )
        with self.assertRaisesRegex(IdentityMutationError, "client_id"):
            engagement.evolve(at=T0 + timedelta(seconds=1), client_id=uid(999))

    def test_optional_relationship_can_bind_once_during_transition(self) -> None:
        proceeding = Proceeding(
            **scoped(30),
            title="Fixture Proceeding",
            status=ProceedingStatus.PROPOSED,
        )
        active = proceeding.transition_to(
            ProceedingStatus.ACTIVE,
            at=T0 + timedelta(seconds=1),
            forum_id=uid(31),
        )
        self.assertEqual(uid(31), active.forum_id)
        stayed = active.transition_to(
            ProceedingStatus.STAYED, at=T0 + timedelta(seconds=2)
        )
        with self.assertRaisesRegex(IdentityMutationError, "forum_id"):
            stayed.transition_to(
                ProceedingStatus.ACTIVE,
                at=T0 + timedelta(seconds=3),
                forum_id=uid(32),
            )

    def test_optional_relationship_cannot_bind_via_generic_evolution(self) -> None:
        proceeding = Proceeding(
            **scoped(30), title="Fixture", status=ProceedingStatus.PROPOSED
        )
        with self.assertRaisesRegex(DomainTransitionError, "forum_id"):
            proceeding.evolve(at=T0 + timedelta(seconds=1), forum_id=uid(31))


class LegalStateInvariantTests(unittest.TestCase):
    def test_matter_requires_open_and_close_times_and_clears_on_reopen(self) -> None:
        matter = Matter(
            **protected(4),
            matter_id=uid(4),
            client_id=uid(2),
            engagement_id=uid(3),
            title="Fixture Matter",
            summary="Synthetic facts only.",
            status=MatterStatus.PROPOSED,
        )
        opened = matter.transition_to(
            MatterStatus.OPEN,
            at=T0 + timedelta(seconds=1),
            opened_at=T0 + timedelta(seconds=1),
        )
        closed = opened.transition_to(
            MatterStatus.CLOSED,
            at=T0 + timedelta(seconds=2),
            closed_at=T0 + timedelta(seconds=2),
        )
        with self.assertRaises(ValidationError):
            closed.transition_to(MatterStatus.OPEN, at=T0 + timedelta(seconds=3))
        reopened = closed.transition_to(
            MatterStatus.OPEN,
            at=T0 + timedelta(seconds=3),
            closed_at=None,
        )
        self.assertIsNone(reopened.closed_at)

    def test_engagement_activation_requires_effective_start(self) -> None:
        engagement = Engagement(
            **protected(3),
            client_id=uid(2),
            title="Fixture Engagement",
            scope="Synthetic scope.",
        )
        with self.assertRaises(ValidationError):
            engagement.transition_to(
                EngagementStatus.ACTIVE, at=T0 + timedelta(seconds=1)
            )
        active = engagement.transition_to(
            EngagementStatus.ACTIVE,
            at=T0 + timedelta(seconds=1),
            effective_interval=EffectiveInterval(valid_from=T0),
        )
        self.assertEqual(EngagementStatus.ACTIVE, active.status)

    def test_verified_party_role_requires_provenance(self) -> None:
        role = PartyRole(
            **scoped(20),
            party_id=uid(19),
            role="counterparty",
            source_reference=source(),
        )
        with self.assertRaises(ValidationError):
            role.transition_to(PartyRoleStatus.VERIFIED, at=T0 + timedelta(seconds=1))
        verified = role.transition_to(
            PartyRoleStatus.VERIFIED,
            at=T0 + timedelta(seconds=1),
            verification_reference_id=uid(900),
        )
        self.assertEqual(PartyRoleStatus.VERIFIED, verified.status)

    def test_matter_event_can_bind_late_time_once_and_requires_review(self) -> None:
        matter_event = MatterEvent(
            **scoped(35),
            event_type="synthetic_review",
            description="A fixture event with initially unknown effective time.",
            observed_at=T0,
            source_reference=source(),
        )
        recorded = matter_event.transition_to(
            MatterEventStatus.RECORDED, at=T0 + timedelta(seconds=1)
        )
        with self.assertRaises(ValidationError):
            recorded.transition_to(
                MatterEventStatus.VERIFIED,
                at=T0 + timedelta(seconds=2),
                occurred_at=T0 - timedelta(days=1),
            )
        with self.assertRaisesRegex(ValidationError, "occurred_at"):
            recorded.transition_to(
                MatterEventStatus.VERIFIED,
                at=T0 + timedelta(seconds=2),
                occurred_at=T0 + timedelta(days=30),
                verification_reference_id=uid(900),
            )
        verified = recorded.transition_to(
            MatterEventStatus.VERIFIED,
            at=T0 + timedelta(seconds=2),
            occurred_at=T0 - timedelta(days=1),
            verification_reference_id=uid(900),
        )
        self.assertEqual(T0 - timedelta(days=1), verified.occurred_at)
        with self.assertRaisesRegex(DomainTransitionError, "occurred_at"):
            verified.evolve(
                at=T0 + timedelta(seconds=3),
                occurred_at=T0 - timedelta(days=2),
            )

    def test_verified_evidence_requires_review_provenance(self) -> None:
        evidence = EvidenceItem(
            **scoped(36),
            title="Synthetic evidence",
            media_type="text/plain",
            content_sha256=sha("1"),
            source_reference=source(),
        )
        collected = evidence.transition_to(
            EvidenceStatus.COLLECTED, at=T0 + timedelta(seconds=1)
        )
        with self.assertRaises(ValidationError):
            collected.transition_to(
                EvidenceStatus.VERIFIED, at=T0 + timedelta(seconds=2)
            )
        verified = collected.transition_to(
            EvidenceStatus.VERIFIED,
            at=T0 + timedelta(seconds=2),
            verification_reference_id=uid(900),
        )
        self.assertEqual(uid(900), verified.verification_reference_id)

    def test_fact_effective_time_and_verification_provenance(self) -> None:
        fact = FactAssertion(
            **scoped(40),
            subject_ref=uid(19),
            predicate="fixture.has_status",
            asserted_value=TypedValue(value_type="string", value="synthetic"),
            source_reference=source(),
            source_locator="/fixture/status",
            effective_interval=EffectiveInterval(valid_from=T0),
            observed_at=T0,
        )
        self.assertTrue(effective_at(fact, T0 + timedelta(days=1)))
        with self.assertRaises(ValidationError):
            fact.transition_to(FactReviewStatus.VERIFIED, at=T0 + timedelta(seconds=1))

    def test_ambiguous_fact_requires_tension_group(self) -> None:
        fact = FactAssertion(
            **scoped(40),
            subject_ref=uid(19),
            predicate="fixture.has_status",
            asserted_value=TypedValue(value_type="string", value="synthetic"),
            source_reference=source(),
            source_locator="/fixture/status",
            observed_at=T0,
        )
        with self.assertRaises(ValidationError):
            fact.transition_to(FactReviewStatus.AMBIGUOUS, at=T0 + timedelta(seconds=1))

    def test_tension_group_never_auto_resolves(self) -> None:
        tension = TensionGroup(
            **scoped(50),
            title="Synthetic disagreement",
            assertion_ids=(uid(40), uid(41)),
        )
        review = tension.transition_to(
            TensionStatus.UNDER_REVIEW, at=T0 + timedelta(seconds=1)
        )
        with self.assertRaises(ValidationError):
            review.transition_to(TensionStatus.RESOLVED, at=T0 + timedelta(seconds=2))
        resolved = review.transition_to(
            TensionStatus.RESOLVED,
            at=T0 + timedelta(seconds=2),
            selected_assertion_id=uid(40),
            resolution_rationale="A synthetic reviewer selected the supported assertion.",
            resolved_by=uid(700),
            resolved_at=T0 + timedelta(seconds=2),
        )
        self.assertEqual(TensionStatus.RESOLVED, resolved.status)
        with self.assertRaisesRegex(DomainTransitionError, "resolution_rationale"):
            resolved.evolve(
                at=T0 + timedelta(seconds=3),
                resolution_rationale="Changed outside review.",
            )

    def test_resolution_fields_cannot_be_preloaded(self) -> None:
        with self.assertRaises(ValidationError):
            TensionGroup(
                **scoped(50),
                title="Synthetic disagreement",
                assertion_ids=(uid(40), uid(41)),
                selected_assertion_id=uid(40),
            )

    def test_deadline_never_becomes_operative_without_all_provenance(self) -> None:
        deadline = Deadline(
            **scoped(60),
            title="Synthetic response candidate",
            candidate_due_at=T0 + timedelta(days=30),
        )
        with self.assertRaises(ValidationError):
            deadline.transition_to(
                DeadlineStatus.REVIEWED,
                at=T0 + timedelta(seconds=1),
                trigger_fact_id=uid(40),
                calculation_id=uid(61),
            )
        reviewed = deadline.transition_to(
            DeadlineStatus.REVIEWED,
            at=T0 + timedelta(seconds=1),
            trigger_fact_id=uid(40),
            calculation_id=uid(61),
            review_validation_id=uid(62),
        )
        operative = reviewed.transition_to(
            DeadlineStatus.OPERATIVE,
            at=T0 + timedelta(seconds=2),
            operative_due_at=T0 + timedelta(days=30),
        )
        with self.assertRaisesRegex(DomainTransitionError, "operative_due_at"):
            operative.evolve(
                at=T0 + timedelta(seconds=3),
                operative_due_at=T0 + timedelta(days=31),
            )

    def test_deadline_completion_time_is_terminal_state_evidence(self) -> None:
        with self.assertRaises(ValidationError):
            Deadline(
                **scoped(60),
                title="Synthetic candidate",
                completed_at=T0,
            )

    def test_blocked_task_must_clear_reason_before_resuming(self) -> None:
        task = Task(
            **scoped(70),
            title="Synthetic task",
            description="Perform a fixture-only review.",
            assigned_principal_id=uid(700),
        )
        ready = task.transition_to(TaskStatus.READY, at=T0 + timedelta(seconds=1))
        blocked = ready.transition_to(
            TaskStatus.BLOCKED,
            at=T0 + timedelta(seconds=2),
            blocked_reason="Waiting for synthetic input.",
        )
        with self.assertRaises(ValidationError):
            blocked.transition_to(TaskStatus.IN_PROGRESS, at=T0 + timedelta(seconds=3))
        resumed = blocked.transition_to(
            TaskStatus.IN_PROGRESS,
            at=T0 + timedelta(seconds=3),
            blocked_reason=None,
        )
        self.assertIsNone(resumed.blocked_reason)

    def test_task_terminal_fields_cannot_be_preloaded(self) -> None:
        for changes in (
            {"blocked_reason": "Too early."},
            {"completed_at": T0},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError):
                    Task(
                        **scoped(70),
                        title="Synthetic task",
                        description="Fixture-only.",
                        **changes,
                    )

    def test_resolution_and_completion_times_match_transition_boundary(self) -> None:
        tension = TensionGroup(
            **scoped(50),
            title="Synthetic disagreement",
            assertion_ids=(uid(40), uid(41)),
        ).transition_to(TensionStatus.UNDER_REVIEW, at=T0 + timedelta(seconds=1))
        with self.assertRaisesRegex(ValidationError, "resolution transition time"):
            tension.transition_to(
                TensionStatus.RESOLVED,
                at=T0 + timedelta(seconds=2),
                selected_assertion_id=uid(40),
                resolution_rationale="Synthetic resolution.",
                resolved_by=uid(700),
                resolved_at=T0 + timedelta(seconds=3),
            )

        task = Task(
            **scoped(70),
            title="Synthetic task",
            description="Fixture-only.",
            assigned_principal_id=uid(700),
        )
        task = task.transition_to(TaskStatus.READY, at=T0 + timedelta(seconds=1))
        task = task.transition_to(TaskStatus.IN_PROGRESS, at=T0 + timedelta(seconds=2))
        with self.assertRaisesRegex(ValidationError, "completion transition time"):
            task.transition_to(
                TaskStatus.COMPLETED,
                at=T0 + timedelta(seconds=3),
                completed_at=T0 + timedelta(seconds=4),
            )

    def test_matter_lifecycle_times_cannot_be_future_dated(self) -> None:
        matter = Matter(
            **protected(4),
            matter_id=uid(4),
            client_id=uid(2),
            engagement_id=uid(3),
            title="Fixture Matter",
            summary="Synthetic facts only.",
        )
        with self.assertRaisesRegex(ValidationError, "opened_at"):
            matter.transition_to(
                MatterStatus.OPEN,
                at=T0 + timedelta(seconds=1),
                opened_at=T0 + timedelta(seconds=2),
            )

    def test_authority_verification_requires_applicability_review(self) -> None:
        authority = Authority(
            **scoped(80),
            title="Synthetic Authority",
            citation="Synthetic Reporter 1",
            jurisdiction="Fixture Jurisdiction",
            authority_kind="case",
            source_reference=source(),
        )
        with self.assertRaises(ValidationError):
            authority.transition_to(
                AuthorityStatus.VERIFIED, at=T0 + timedelta(seconds=1)
            )

    def test_claim_acceptance_requires_elements_and_validation(self) -> None:
        claim = Claim(
            **scoped(90),
            issue_id=uid(89),
            label="Synthetic claim",
            statement="The fixture states a synthetic legal theory.",
        )
        review = claim.transition_to(
            ClaimStatus.UNDER_REVIEW, at=T0 + timedelta(seconds=1)
        )
        with self.assertRaises(ValidationError):
            review.transition_to(ClaimStatus.ACCEPTED, at=T0 + timedelta(seconds=2))

    def test_work_product_approval_requires_validation_and_approval(self) -> None:
        product = WorkProduct(
            **scoped(100),
            title="Synthetic memo",
            work_product_kind="memo",
            current_version_id=uid(101),
        )
        review = product.transition_to(
            WorkProductStatus.IN_REVIEW, at=T0 + timedelta(seconds=1)
        )
        validated = review.transition_to(
            WorkProductStatus.VALIDATED,
            at=T0 + timedelta(seconds=2),
            validation_result_id=uid(102),
        )
        with self.assertRaises(ValidationError):
            validated.transition_to(
                WorkProductStatus.APPROVED, at=T0 + timedelta(seconds=3)
            )

    def test_work_product_payload_change_resets_review_evidence(self) -> None:
        product = WorkProduct(
            **scoped(100),
            title="Synthetic memo",
            work_product_kind="memo",
            current_version_id=uid(101),
        )
        reviewed = product.transition_to(
            WorkProductStatus.IN_REVIEW, at=T0 + timedelta(seconds=1)
        )
        validated = reviewed.transition_to(
            WorkProductStatus.VALIDATED,
            at=T0 + timedelta(seconds=2),
            validation_result_id=uid(102),
        )
        approved = validated.transition_to(
            WorkProductStatus.APPROVED,
            at=T0 + timedelta(seconds=3),
            approval_id=uid(103),
        )
        with self.assertRaisesRegex(DomainTransitionError, "reset"):
            approved.evolve(at=T0 + timedelta(seconds=4), current_version_id=uid(104))
        with self.assertRaisesRegex(DomainTransitionError, "clear gate"):
            approved.transition_to(
                WorkProductStatus.IN_REVIEW,
                at=T0 + timedelta(seconds=4),
                current_version_id=uid(104),
            )
        reset = approved.transition_to(
            WorkProductStatus.IN_REVIEW,
            at=T0 + timedelta(seconds=4),
            current_version_id=uid(104),
            validation_result_id=None,
            approval_id=None,
        )
        self.assertEqual(uid(104), reset.current_version_id)
        self.assertIsNone(reset.validation_result_id)
        self.assertIsNone(reset.approval_id)

    def test_communication_payload_cannot_retain_stale_gate_evidence(self) -> None:
        communication = Communication(
            **scoped(71),
            direction="outbound",
            channel="email",
            subject="Synthetic communication",
            participant_ids=(uid(19),),
            work_product_version_id=uid(101),
        )
        validated = communication.transition_to(
            CommunicationStatus.VALIDATED,
            at=T0 + timedelta(seconds=1),
            validation_result_id=uid(72),
        )
        approved = validated.transition_to(
            CommunicationStatus.APPROVED,
            at=T0 + timedelta(seconds=2),
            approval_id=uid(73),
        )
        reset = approved.transition_to(
            CommunicationStatus.DRAFT,
            at=T0 + timedelta(seconds=3),
            subject="Revised synthetic communication",
            validation_result_id=None,
            approval_id=None,
            destination_verified=False,
        )
        self.assertEqual("Revised synthetic communication", reset.subject)
        self.assertIsNone(reset.validation_result_id)

        revalidated = reset.transition_to(
            CommunicationStatus.VALIDATED,
            at=T0 + timedelta(seconds=4),
            validation_result_id=uid(74),
        )
        reapproved = revalidated.transition_to(
            CommunicationStatus.APPROVED,
            at=T0 + timedelta(seconds=5),
            approval_id=uid(75),
        )
        queued = reapproved.transition_to(
            CommunicationStatus.QUEUED,
            at=T0 + timedelta(seconds=6),
            destination_verified=True,
            destination_sha256=sha("8"),
            execution_id=uid(120),
        )
        dispatched = queued.transition_to(
            CommunicationStatus.DISPATCHED, at=T0 + timedelta(seconds=7)
        )
        completed = dispatched.transition_to(
            CommunicationStatus.RECEIPT_VERIFIED,
            at=T0 + timedelta(seconds=8),
        )
        with self.assertRaisesRegex(DomainTransitionError, "reset"):
            completed.evolve(
                at=T0 + timedelta(seconds=9),
                participant_ids=(uid(999),),
            )

    def test_future_gate_evidence_cannot_be_preloaded(self) -> None:
        constructors = (
            lambda: Matter(
                **protected(4),
                matter_id=uid(4),
                client_id=uid(2),
                engagement_id=uid(3),
                title="Fixture Matter",
                summary="Synthetic facts only.",
                opened_at=T0,
            ),
            lambda: Deadline(
                **scoped(60),
                title="Synthetic deadline candidate",
                review_validation_id=uid(62),
            ),
            lambda: Communication(
                **scoped(71),
                direction="internal",
                channel="other",
                subject="Synthetic communication",
                participant_ids=(uid(19),),
                validation_result_id=uid(72),
            ),
            lambda: WorkProduct(
                **scoped(100),
                title="Synthetic memo",
                work_product_kind="memo",
                current_version_id=uid(101),
                validation_result_id=uid(102),
            ),
        )
        for constructor in constructors:
            with self.subTest(constructor=constructor):
                with self.assertRaises(ValidationError):
                    constructor()


class ApprovalAndExecutionTests(unittest.TestCase):
    def approval(self) -> Approval:
        return Approval(**scoped(110), subject=binding())

    def test_approval_binds_exact_artifact_version_and_hash(self) -> None:
        approval = self.approval().transition_to(
            ApprovalStatus.APPROVED,
            at=T0 + timedelta(seconds=1),
            reviewer_principal_id=uid(700),
            decided_at=T0 + timedelta(seconds=1),
            rationale="Approved synthetic version exactly as hashed.",
        )
        self.assertEqual(1, approval.subject.artifact_version)
        self.assertEqual(sha("c"), approval.subject.content_sha256)
        with self.assertRaisesRegex(IdentityMutationError, "subject"):
            approval.evolve(at=T0 + timedelta(seconds=2), subject=binding(digest="e"))

    def test_approval_evidence_cannot_change_via_generic_evolution(self) -> None:
        approval = self.approval().transition_to(
            ApprovalStatus.APPROVED,
            at=T0 + timedelta(seconds=1),
            reviewer_principal_id=uid(700),
            decided_at=T0 + timedelta(seconds=1),
            rationale="Initial synthetic decision.",
        )
        with self.assertRaisesRegex(DomainTransitionError, "rationale"):
            approval.evolve(
                at=T0 + timedelta(seconds=2), rationale="Rewritten decision."
            )

    def test_revocation_preserves_original_decision_and_records_revoker(self) -> None:
        approved = approved_decision(at=T0 + timedelta(seconds=1))
        with self.assertRaisesRegex(IdentityMutationError, "reviewer_principal_id"):
            approved.transition_to(
                ApprovalStatus.REVOKED,
                at=T0 + timedelta(seconds=2),
                reviewer_principal_id=uid(999),
                decided_at=T0 + timedelta(seconds=2),
                rationale="Rewritten decision.",
                revoker_principal_id=uid(701),
                revocation_rationale="Synthetic revocation.",
                revoked_at=T0 + timedelta(seconds=2),
            )
        revoked = approved.transition_to(
            ApprovalStatus.REVOKED,
            at=T0 + timedelta(seconds=2),
            revoker_principal_id=uid(701),
            revocation_rationale="Synthetic revocation.",
            revoked_at=T0 + timedelta(seconds=2),
        )
        self.assertEqual(uid(700), revoked.reviewer_principal_id)
        self.assertEqual(uid(701), revoked.revoker_principal_id)
        self.assertEqual(approved.decided_at, revoked.decided_at)

    def test_decision_and_revocation_times_cannot_be_future_dated(self) -> None:
        with self.assertRaisesRegex(ValueError, "decision transition time"):
            self.approval().transition_to(
                ApprovalStatus.APPROVED,
                at=T0 + timedelta(seconds=1),
                reviewer_principal_id=uid(700),
                decided_at=T0 + timedelta(seconds=2),
                rationale="Future decision.",
            )
        approved = approved_decision(at=T0 + timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError, "revocation transition time"):
            approved.transition_to(
                ApprovalStatus.REVOKED,
                at=T0 + timedelta(seconds=2),
                revoker_principal_id=uid(701),
                revocation_rationale="Future revocation.",
                revoked_at=T0 + timedelta(seconds=3),
            )

    def test_pending_and_approved_states_reject_future_revocation_data(self) -> None:
        with self.assertRaises(ValidationError):
            Approval(
                **scoped(110),
                subject=binding(),
                revoked_at=T0,
            )
        with self.assertRaises(ValidationError):
            Approval(
                **scoped(110),
                subject=binding(),
                status=ApprovalStatus.APPROVED,
                reviewer_principal_id=uid(700),
                decided_at=T0,
                rationale="Synthetic decision.",
                revoked_at=T0,
            )

    def execution(self) -> Execution:
        return Execution(
            **scoped(120),
            subject=binding(),
            destination_sha256=sha("f"),
            idempotency_key="fixture-execution-1",
        )

    def advance_to_dispatched(self) -> Execution:
        execution = self.execution()
        first = event(
            execution.id,
            121,
            ExecutionStep.VALIDATED,
            T0 + timedelta(seconds=1),
        )
        execution = execution.transition_to(
            ExecutionStatus.VALIDATED,
            at=T0 + timedelta(seconds=1),
            validation_result=validation(at=T0 + timedelta(seconds=1)),
            events=(first,),
        )
        second = event(
            execution.id,
            122,
            ExecutionStep.APPROVED,
            T0 + timedelta(seconds=2),
        )
        execution = execution.transition_to(
            ExecutionStatus.APPROVED,
            at=T0 + timedelta(seconds=2),
            approval=approved_decision(at=T0 + timedelta(seconds=2)),
            events=(*execution.events, second),
        )
        third = event(
            execution.id,
            123,
            ExecutionStep.QUEUED,
            T0 + timedelta(seconds=3),
        )
        execution = execution.transition_to(
            ExecutionStatus.QUEUED,
            at=T0 + timedelta(seconds=3),
            events=(*execution.events, third),
        )
        fourth = event(
            execution.id,
            124,
            ExecutionStep.DISPATCHED,
            T0 + timedelta(seconds=4),
        )
        return execution.transition_to(
            ExecutionStatus.DISPATCHED,
            at=T0 + timedelta(seconds=4),
            events=(*execution.events, fourth),
        )

    def test_full_execution_requires_matching_receipt_and_event(self) -> None:
        dispatched = self.advance_to_dispatched()
        receipt = ExecutionReceipt(
            **{
                **scoped(125),
                "created_at": T0 + timedelta(seconds=5),
                "updated_at": T0 + timedelta(seconds=6),
            },
            execution_id=dispatched.id,
            connector="fixture-connector",
            external_receipt_id="fixture-receipt",
            artifact_content_sha256=dispatched.subject.content_sha256,
            destination_sha256=dispatched.destination_sha256,
            received_at=T0 + timedelta(seconds=5),
            verified_at=T0 + timedelta(seconds=6),
        )
        receipt_event = event(
            dispatched.id,
            126,
            ExecutionStep.RECEIPT_VERIFIED,
            T0 + timedelta(seconds=6),
            receipt_id=receipt.id,
        )
        completed = dispatched.transition_to(
            ExecutionStatus.RECEIPT_VERIFIED,
            at=T0 + timedelta(seconds=6),
            events=(*dispatched.events, receipt_event),
            receipt=receipt,
        )
        self.assertEqual(ExecutionStatus.RECEIPT_VERIFIED, completed.status)
        self.assertEqual(
            receipt.id, completed.receipt.id if completed.receipt else None
        )

    def test_draft_execution_rejects_future_events_and_receipt(self) -> None:
        execution = self.execution()
        future = event(
            execution.id,
            121,
            ExecutionStep.VALIDATED,
            T0 + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(ValidationError, "prove the current state"):
            Execution(
                **{
                    **scoped(120),
                    "updated_at": T0 + timedelta(seconds=1),
                },
                subject=binding(),
                destination_sha256=sha("f"),
                idempotency_key="fixture-execution-1",
                events=(future,),
            )

    def test_execution_event_receipt_id_matches_receipt_step_only(self) -> None:
        with self.assertRaisesRegex(ValidationError, "requires receipt_id"):
            event(
                uid(120),
                259,
                ExecutionStep.RECEIPT_VERIFIED,
                T0,
            )
        for step in ExecutionStep:
            with self.subTest(step=step):
                if step == ExecutionStep.RECEIPT_VERIFIED:
                    receipt_event = event(
                        uid(120),
                        260,
                        step,
                        T0,
                        receipt_id=uid(125),
                    )
                    self.assertEqual(uid(125), receipt_event.receipt_id)
                else:
                    with self.assertRaisesRegex(
                        ValidationError, "only a receipt-verified event"
                    ):
                        event(
                            uid(120),
                            260,
                            step,
                            T0,
                            receipt_id=uid(125),
                        )

    def test_execution_rejects_skipped_and_out_of_order_events(self) -> None:
        execution = self.execution()
        queued = event(
            execution.id,
            123,
            ExecutionStep.QUEUED,
            T0 + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(ValidationError, "state graph"):
            Execution(
                **{
                    **scoped(120),
                    "updated_at": T0 + timedelta(seconds=1),
                },
                subject=binding(),
                destination_sha256=sha("f"),
                idempotency_key="fixture-execution-1",
                validation_result=validation(),
                approval=approved_decision(),
                events=(queued,),
                status=ExecutionStatus.QUEUED,
            )
        validated = event(
            execution.id,
            121,
            ExecutionStep.VALIDATED,
            T0 + timedelta(seconds=2),
        )
        approved = event(
            execution.id,
            122,
            ExecutionStep.APPROVED,
            T0 + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(ValidationError, "ordered"):
            Execution(
                **{
                    **scoped(120),
                    "updated_at": T0 + timedelta(seconds=2),
                },
                subject=binding(),
                destination_sha256=sha("f"),
                idempotency_key="fixture-execution-1",
                validation_result=validation(),
                approval=approved_decision(),
                events=(validated, approved),
                status=ExecutionStatus.APPROVED,
            )

    def test_execution_cannot_claim_receipt_without_receipt_event(self) -> None:
        dispatched = self.advance_to_dispatched()
        with self.assertRaisesRegex(DomainTransitionError, "append exactly one"):
            dispatched.transition_to(
                ExecutionStatus.RECEIPT_VERIFIED,
                at=T0 + timedelta(seconds=5),
                events=dispatched.events,
            )

    def test_execution_evidence_cannot_change_via_generic_evolution(self) -> None:
        dispatched = self.advance_to_dispatched()
        with self.assertRaisesRegex(DomainTransitionError, "events"):
            dispatched.evolve(at=T0 + timedelta(seconds=5), events=())

    def test_execution_rejects_cross_boundary_event(self) -> None:
        execution = self.execution()
        invalid = ExecutionEvent(
            **{
                **scoped(121),
                "tenant_id": uid(999),
                "created_at": T0 + timedelta(seconds=1),
                "updated_at": T0 + timedelta(seconds=1),
            },
            execution_id=execution.id,
            step=ExecutionStep.VALIDATED,
            occurred_at=T0 + timedelta(seconds=1),
            correlation_id="fixture-cross-boundary",
            actor_principal_id=uid(700),
        )
        with self.assertRaisesRegex(ValidationError, "crosses tenant"):
            execution.transition_to(
                ExecutionStatus.VALIDATED,
                at=T0 + timedelta(seconds=1),
                validation_result=validation(at=T0 + timedelta(seconds=1)),
                events=(invalid,),
            )

    def test_execution_requires_typed_matching_passed_gate_evidence(self) -> None:
        invalid_results = (
            validation(subject=binding(digest="e"), at=T0 + timedelta(seconds=1)),
            validation(
                at=T0 + timedelta(seconds=1),
                outcome=ValidationOutcome.FAILED,
            ),
            validation(
                at=T0 + timedelta(seconds=1),
                tenant_id=uid(999),
            ),
        )
        for identifier, result in enumerate(invalid_results, start=221):
            with self.subTest(result=result):
                execution = self.execution()
                proof = event(
                    execution.id,
                    identifier,
                    ExecutionStep.VALIDATED,
                    T0 + timedelta(seconds=1),
                )
                with self.assertRaises(ValidationError):
                    execution.transition_to(
                        ExecutionStatus.VALIDATED,
                        at=T0 + timedelta(seconds=1),
                        validation_result=result,
                        events=(proof,),
                    )

        execution = self.execution()
        validation_event = event(
            execution.id,
            224,
            ExecutionStep.VALIDATED,
            T0 + timedelta(seconds=1),
        )
        validated = execution.transition_to(
            ExecutionStatus.VALIDATED,
            at=T0 + timedelta(seconds=1),
            validation_result=validation(at=T0 + timedelta(seconds=1)),
            events=(validation_event,),
        )
        approved = approved_decision(at=T0)
        revoked = approved.transition_to(
            ApprovalStatus.REVOKED,
            at=T0 + timedelta(seconds=1),
            revoker_principal_id=uid(701),
            revocation_rationale="No longer authorized.",
            revoked_at=T0 + timedelta(seconds=1),
        )
        approval_event = event(
            execution.id,
            225,
            ExecutionStep.APPROVED,
            T0 + timedelta(seconds=2),
        )
        with self.assertRaisesRegex(ValidationError, "currently approved"):
            validated.transition_to(
                ExecutionStatus.APPROVED,
                at=T0 + timedelta(seconds=2),
                approval=revoked,
                events=(*validated.events, approval_event),
            )

    def test_execution_gate_snapshots_cannot_be_replaced(self) -> None:
        execution = self.execution()
        first = event(
            execution.id,
            231,
            ExecutionStep.VALIDATED,
            T0 + timedelta(seconds=1),
        )
        original_validation = validation(at=T0 + timedelta(seconds=1))
        validated = execution.transition_to(
            ExecutionStatus.VALIDATED,
            at=T0 + timedelta(seconds=1),
            validation_result=original_validation,
            events=(first,),
        )
        second = event(
            execution.id,
            232,
            ExecutionStep.APPROVED,
            T0 + timedelta(seconds=2),
        )
        with self.assertRaisesRegex(IdentityMutationError, "validation_result"):
            validated.transition_to(
                ExecutionStatus.APPROVED,
                at=T0 + timedelta(seconds=2),
                validation_result=validation(
                    identifier=611, at=T0 + timedelta(seconds=1)
                ),
                approval=approved_decision(at=T0 + timedelta(seconds=2)),
                events=(*validated.events, second),
            )

    def test_execution_transition_preserves_exact_event_prefix(self) -> None:
        dispatched = self.advance_to_dispatched()
        replacement_history = (
            event(
                dispatched.id,
                241,
                ExecutionStep.VALIDATED,
                T0 + timedelta(seconds=1),
            ),
            event(
                dispatched.id,
                242,
                ExecutionStep.APPROVED,
                T0 + timedelta(seconds=2),
            ),
            event(
                dispatched.id,
                243,
                ExecutionStep.QUEUED,
                T0 + timedelta(seconds=3),
            ),
            event(
                dispatched.id,
                244,
                ExecutionStep.DISPATCHED,
                T0 + timedelta(seconds=4),
            ),
            event(
                dispatched.id,
                245,
                ExecutionStep.FAILED,
                T0 + timedelta(seconds=5),
            ),
        )
        with self.assertRaisesRegex(DomainTransitionError, "exact prior prefix"):
            dispatched.transition_to(
                ExecutionStatus.FAILED,
                at=T0 + timedelta(seconds=5),
                events=replacement_history,
            )

    def test_execution_event_must_match_transition_time(self) -> None:
        execution = self.execution()
        future_event = event(
            execution.id,
            251,
            ExecutionStep.VALIDATED,
            T0 + timedelta(seconds=2),
        )
        with self.assertRaisesRegex(DomainTransitionError, "transition boundary"):
            execution.transition_to(
                ExecutionStatus.VALIDATED,
                at=T0 + timedelta(seconds=1),
                validation_result=validation(at=T0 + timedelta(seconds=1)),
                events=(future_event,),
            )

    def test_embedded_execution_evidence_cannot_postdate_owner_audit(self) -> None:
        execution = self.execution()
        transition_at = T0 + timedelta(seconds=1)
        proof = event(
            execution.id,
            271,
            ExecutionStep.VALIDATED,
            transition_at,
        )
        future_updated_validation = ValidationResult(
            **{
                **scoped(610),
                "updated_at": T0 + timedelta(seconds=2),
            },
            subject=binding(),
            outcome=ValidationOutcome.PASSED,
            check_ids=("fixture.schema",),
            validator_principal_id=uid(700),
            validated_at=transition_at,
            rationale="The result was updated after the execution transition.",
        )
        with self.assertRaisesRegex(ValidationError, "validation audit time"):
            execution.transition_to(
                ExecutionStatus.VALIDATED,
                at=transition_at,
                validation_result=future_updated_validation,
                events=(proof,),
            )

        future_updated_event = ExecutionEvent(
            **{
                **scoped(272),
                "created_at": transition_at,
                "updated_at": T0 + timedelta(seconds=2),
            },
            execution_id=execution.id,
            step=ExecutionStep.VALIDATED,
            occurred_at=transition_at,
            correlation_id="future-updated-event",
            actor_principal_id=uid(700),
        )
        with self.assertRaisesRegex(ValidationError, "event audit time"):
            execution.transition_to(
                ExecutionStatus.VALIDATED,
                at=transition_at,
                validation_result=validation(at=transition_at),
                events=(future_updated_event,),
            )

        dispatched = self.advance_to_dispatched()
        receipt = ExecutionReceipt(
            **{
                **scoped(273),
                "created_at": T0 + timedelta(seconds=5),
                "updated_at": T0 + timedelta(seconds=7),
            },
            execution_id=dispatched.id,
            connector="fixture-connector",
            external_receipt_id="future-updated-receipt",
            artifact_content_sha256=dispatched.subject.content_sha256,
            destination_sha256=dispatched.destination_sha256,
            received_at=T0 + timedelta(seconds=5),
            verified_at=T0 + timedelta(seconds=6),
        )
        receipt_proof = event(
            dispatched.id,
            274,
            ExecutionStep.RECEIPT_VERIFIED,
            T0 + timedelta(seconds=6),
            receipt_id=receipt.id,
        )
        with self.assertRaisesRegex(ValidationError, "receipt audit time"):
            dispatched.transition_to(
                ExecutionStatus.RECEIPT_VERIFIED,
                at=T0 + timedelta(seconds=6),
                events=(*dispatched.events, receipt_proof),
                receipt=receipt,
            )


class PublicContractTests(unittest.TestCase):
    def test_public_exports_use_legal_canonical_names(self) -> None:
        forbidden = {"Problem", "Incident"}
        self.assertTrue(forbidden.isdisjoint(sklegal_domain.__all__))
        for name in sklegal_domain.__all__:
            exported = getattr(sklegal_domain, name)
            if isinstance(exported, type):
                self.assertNotIn(exported.__name__, forbidden)

    def test_protected_records_always_carry_tenant_and_matter_boundaries(self) -> None:
        records = (
            Party(
                **scoped(10),
                display_name="Synthetic Party",
                party_kind="person",
                source_reference=source(),
            ),
            EvidenceItem(
                **scoped(11),
                title="Synthetic evidence",
                media_type="text/plain",
                content_sha256=sha("1"),
                source_reference=source(),
            ),
            MatterEvent(
                **scoped(12),
                event_type="synthetic_review",
                description="A fixture event.",
                observed_at=T0,
                source_reference=source(),
            ),
            Transaction(
                **scoped(13),
                title="Synthetic transaction",
                description="A fixture transaction.",
                party_role_ids=(uid(20),),
                source_references=(source(),),
            ),
            Communication(
                **scoped(14),
                direction="internal",
                channel="other",
                subject="Synthetic communication",
                participant_ids=(uid(19),),
            ),
            WorkProductVersion(
                **scoped(15),
                work_product_id=uid(100),
                version_number=1,
                content_sha256=sha("2"),
                source_artifact_id=uid(601),
                status=WorkProductVersionStatus.DRAFT,
            ),
        )
        for record in records:
            with self.subTest(record=type(record).__name__):
                self.assertEqual(uid(1), record.tenant_id)
                self.assertEqual(uid(4), record.matter_id)
                self.assertEqual(RecordCompleteness.INCOMPLETE, record.completeness)

    def test_validation_result_binds_exact_subject(self) -> None:
        result = ValidationResult(
            **scoped(130),
            subject=binding(),
            outcome=ValidationOutcome.PASSED,
            check_ids=("fixture.schema", "fixture.hash"),
            validator_principal_id=uid(700),
            validated_at=T0,
            rationale="Synthetic checks passed.",
        )
        with self.assertRaisesRegex(IdentityMutationError, "subject"):
            result.evolve(at=T0 + timedelta(seconds=1), subject=binding(digest="e"))

    def test_legacy_alias_collections_cannot_be_removed_or_rewritten(self) -> None:
        matter_alias = LegacyAlias(
            record_kind=LegacyRecordKind.CONTAINER,
            legacy_id="PRB-0000-001",
            legacy_slug="synthetic-matter",
            legacy_path="archive/synthetic/MATTER.md",
            source_version="fixture-v1",
            content_sha256=sha("3"),
            observed_at=T0,
        )
        event_alias = LegacyAlias(
            record_kind=LegacyRecordKind.ACTIVITY,
            legacy_id="INC-000",
            legacy_slug="synthetic-event",
            legacy_path="archive/synthetic/EVENT.md",
            source_version="fixture-v1",
            content_sha256=sha("4"),
            observed_at=T0,
        )
        matter = Matter(
            **protected(4),
            matter_id=uid(4),
            client_id=uid(2),
            engagement_id=uid(3),
            title="Fixture Matter",
            summary="Synthetic provenance only.",
            aliases=(matter_alias,),
        )
        matter_event = MatterEvent(
            **scoped(12),
            event_type="synthetic_review",
            description="A fixture event.",
            observed_at=T0,
            source_reference=source(),
            aliases=(event_alias,),
        )
        for record in (matter, matter_event):
            with self.subTest(record=type(record).__name__):
                with self.assertRaisesRegex(IdentityMutationError, "aliases"):
                    record.evolve(at=T0 + timedelta(seconds=1), aliases=())

    def test_evidence_hash_and_source_are_immutable(self) -> None:
        evidence = EvidenceItem(
            **scoped(11),
            title="Synthetic evidence",
            media_type="text/plain",
            content_sha256=sha("1"),
            source_reference=source(),
        )
        with self.assertRaisesRegex(IdentityMutationError, "content_sha256"):
            evidence.evolve(at=T0 + timedelta(seconds=1), content_sha256=sha("2"))
        with self.assertRaisesRegex(IdentityMutationError, "source_reference"):
            evidence.evolve(at=T0 + timedelta(seconds=1), source_reference=source(501))

    def test_party_role_and_forum_source_provenance_is_immutable(self) -> None:
        role = PartyRole(
            **scoped(20),
            party_id=uid(19),
            role="counterparty",
            source_reference=source(),
        )
        forum = Forum(
            **scoped(21),
            name="Synthetic Forum",
            jurisdiction="Fixture Jurisdiction",
            forum_kind="court",
            source_reference=source(),
        )
        for record in (role, forum):
            with self.subTest(record=type(record).__name__):
                with self.assertRaisesRegex(IdentityMutationError, "source_reference"):
                    record.evolve(
                        at=T0 + timedelta(seconds=1),
                        source_reference=source(501),
                    )

    def test_every_source_bearing_aggregate_rejects_future_provenance(self) -> None:
        future_source = SourceReference(
            source_reference_id=uid(550),
            source_system="synthetic-fixture",
            source_version="v1",
            content_sha256=sha("5"),
            locator="fixture://domain/future-source",
            observed_at=T0 + timedelta(seconds=1),
        )
        future_container_alias = LegacyAlias(
            record_kind=LegacyRecordKind.CONTAINER,
            legacy_id="PRB-0000-001",
            legacy_slug="future-matter",
            legacy_path="archive/future/MATTER.md",
            source_version="fixture-v1",
            content_sha256=sha("6"),
            observed_at=T0 + timedelta(seconds=1),
        )
        future_activity_alias = LegacyAlias(
            record_kind=LegacyRecordKind.ACTIVITY,
            legacy_id="INC-000",
            legacy_slug="future-event",
            legacy_path="archive/future/EVENT.md",
            source_version="fixture-v1",
            content_sha256=sha("7"),
            observed_at=T0 + timedelta(seconds=1),
        )
        constructors = {
            "Matter.aliases": lambda: Matter(
                **protected(4),
                matter_id=uid(4),
                client_id=uid(2),
                engagement_id=uid(3),
                title="Fixture Matter",
                summary="Synthetic provenance only.",
                aliases=(future_container_alias,),
            ),
            "Party.source_reference": lambda: Party(
                **scoped(10),
                display_name="Synthetic Party",
                party_kind="person",
                source_reference=future_source,
            ),
            "PartyRole.source_reference": lambda: PartyRole(
                **scoped(20),
                party_id=uid(19),
                role="counterparty",
                source_reference=future_source,
            ),
            "Forum.source_reference": lambda: Forum(
                **scoped(21),
                name="Synthetic Forum",
                jurisdiction="Fixture Jurisdiction",
                forum_kind="court",
                source_reference=future_source,
            ),
            "MatterEvent.source_reference": lambda: MatterEvent(
                **scoped(35),
                event_type="synthetic_review",
                description="A fixture event.",
                observed_at=T0,
                source_reference=future_source,
            ),
            "MatterEvent.aliases": lambda: MatterEvent(
                **scoped(36),
                event_type="synthetic_review",
                description="A fixture event.",
                observed_at=T0,
                source_reference=source(),
                aliases=(future_activity_alias,),
            ),
            "Transaction.source_references": lambda: Transaction(
                **scoped(37),
                title="Synthetic transaction",
                description="A fixture transaction.",
                party_role_ids=(uid(20),),
                source_references=(future_source,),
            ),
            "FactAssertion.source_reference": lambda: FactAssertion(
                **scoped(40),
                subject_ref=uid(19),
                predicate="fixture.has_status",
                asserted_value=TypedValue(value_type="string", value="synthetic"),
                source_reference=future_source,
                source_locator="/fixture/status",
                observed_at=T0,
            ),
            "EvidenceItem.source_reference": lambda: EvidenceItem(
                **scoped(41),
                title="Synthetic evidence",
                media_type="text/plain",
                content_sha256=sha("1"),
                source_reference=future_source,
            ),
            "CustodyEvent.source_reference": lambda: CustodyEvent(
                **scoped(42),
                evidence_item_id=uid(41),
                action="acquired",
                custodian_id=uid(700),
                occurred_at=T0,
                source_reference=future_source,
            ),
            "Authority.source_reference": lambda: Authority(
                **scoped(43),
                title="Synthetic Authority",
                citation="Fixture Reporter 1",
                jurisdiction="Fixture Jurisdiction",
                authority_kind="case",
                source_reference=future_source,
            ),
            "DeadlineCalculation.source_references": lambda: DeadlineCalculation(
                **scoped(44),
                trigger_fact_id=uid(40),
                calculation_rule="Add thirty fixture calendar days.",
                candidate_due_at=T0 + timedelta(days=30),
                calculated_at=T0,
                source_references=(future_source,),
                calculation_version="fixture-v1",
            ),
        }
        self.assertEqual(12, len(constructors))
        for field_name, constructor in constructors.items():
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValidationError, "source provenance"):
                    constructor()

    def test_execution_event_and_receipt_payloads_are_append_only(self) -> None:
        execution_id = uid(120)
        immutable_event = event(
            execution_id,
            121,
            ExecutionStep.VALIDATED,
            T0 + timedelta(seconds=1),
        )
        receipt = ExecutionReceipt(
            **scoped(125),
            execution_id=execution_id,
            connector="fixture-connector",
            external_receipt_id="fixture-receipt",
            artifact_content_sha256=sha("c"),
            destination_sha256=sha("f"),
            received_at=T0,
            verified_at=T0,
        )
        with self.assertRaisesRegex(IdentityMutationError, "correlation_id"):
            immutable_event.evolve(
                at=T0 + timedelta(seconds=2), correlation_id="rewritten"
            )
        with self.assertRaisesRegex(IdentityMutationError, "external_receipt_id"):
            receipt.evolve(
                at=T0 + timedelta(seconds=1),
                external_receipt_id="rewritten-receipt",
            )

    def test_fixture_json_is_valid_strict_input(self) -> None:
        path = (
            sklegal_domain.__file__
            and __import__("pathlib").Path(sklegal_domain.__file__).resolve().parents[4]
            / "tests"
            / "fixtures"
            / "domain"
            / "synthetic-foundation.json"
        )
        assert path is not None
        data = json.loads(path.read_text(encoding="utf-8"))
        tenant = Tenant.model_validate_json(json.dumps(data["tenant"]))
        client = Client.model_validate_json(json.dumps(data["client"]))
        engagement = Engagement.model_validate_json(json.dumps(data["engagement"]))
        matter = Matter.model_validate_json(json.dumps(data["matter"]))
        self.assertEqual(tenant.id, client.tenant_id)
        self.assertEqual(client.id, engagement.client_id)
        self.assertEqual(engagement.id, matter.engagement_id)


if __name__ == "__main__":
    unittest.main()
