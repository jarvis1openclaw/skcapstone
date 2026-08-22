"""Governed human policy-decision and revocation lifecycle tests (S1-04A)."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from uuid import UUID, uuid4

from pydantic import ValidationError
from sklegal_capauth import (
    ApiCapabilityBoundary,
    Audience,
    AuthorizationDecision,
    AuthorizedContext,
    BoundaryScope,
    Capability,
    DecisionReason,
    PrincipalContext,
    PrincipalPolicyReference,
    PrincipalType,
    Purpose,
)
from sklegal_domain import DataClassification
from sklegal_policies import (
    GOVERNANCE_REQUIREMENTS,
    AccessState,
    CapAuthCurrentStateVerifier,
    ClassificationSource,
    CloseEthicalWall,
    ConflictCheckEvidence,
    ConflictDecision,
    ConflictDisposition,
    DataFlowBoundary,
    DecideConflict,
    GovernanceAction,
    GovernanceCommand,
    GovernanceReason,
    GrantProtectedAccess,
    InMemoryAuthorizationUseBackend,
    InMemoryGovernanceAuditSink,
    InMemoryPolicyAuditSink,
    InMemoryPolicyGovernanceStore,
    IssueLegalHold,
    LegalHoldStatus,
    MaterialPolicyFacts,
    OpenEthicalWall,
    PolicyAccessRequest,
    PolicyBoundaryRequirement,
    PolicyDenied,
    PolicyGateway,
    PolicyGovernanceDenied,
    PolicyGovernanceService,
    PolicyGovernanceUnavailable,
    PolicyMutationReceipt,
    PolicyReason,
    ProtectedAccessLevel,
    RegisterWaiverReference,
    ReleaseLegalHold,
    RevokeProtectedAccess,
    SealEthicalWallRoster,
    SetProtectionLabel,
    SetRetentionPolicy,
    SetWallMembership,
    UnavailableGovernanceAuditSink,
    WallMembershipDisposition,
    canonical_command_digest,
    recompute_receipt_sha256,
)

from tests.support.capauth_contract import (
    MATTER_ID,
    RESOURCE_DIGEST,
    CapabilityTestRig,
)

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
REVISION = "a" * 64
MATERIAL_ID = UUID("b4000000-0000-4000-8000-000000000001")
REPO_ROOT = Path(__file__).resolve().parents[1]


class GovernanceRig:
    """One isolated human governance environment over synthetic CapAuth."""

    def __init__(
        self,
        *,
        audit: InMemoryGovernanceAuditSink | None = None,
        conflict_checks: dict[UUID, ConflictCheckEvidence] | None = None,
    ) -> None:
        self.rig = CapabilityTestRig()
        self.audit = audit or InMemoryGovernanceAuditSink()
        self.store = InMemoryPolicyGovernanceStore(
            audit=self.audit,
            conflict_checks=conflict_checks,
        )
        self.verifier = CapAuthCurrentStateVerifier(
            trusted_issuers=self.rig.trusted,
            principals=self.rig.principals,
            revocations=self.rig.revocations,
            uses=InMemoryAuthorizationUseBackend(),
        )
        self.service = PolicyGovernanceService(
            store=self.store,
            current_authorization=self.verifier,
            clock=self.rig.clock,
        )
        self.principal = self.rig.principal()
        self.other_principal = self.rig.principal()

    def close(self) -> None:
        self.rig.close()

    @property
    def tenant_id(self) -> UUID:
        return self.principal.tenant_id

    @property
    def matter_id(self) -> UUID:
        return MATTER_ID

    def authorize_for(
        self,
        action: GovernanceAction,
        *,
        principal: PrincipalContext | None = None,
        capability: Capability | None = None,
        purpose: Purpose | None = None,
        matter_id: UUID | None = None,
    ) -> AuthorizedContext:
        requirement = GOVERNANCE_REQUIREMENTS[action]
        actor = principal or self.principal
        matter = matter_id or self.matter_id
        grant = self.rig.grant(
            capability=capability or requirement.capability,
            purpose=purpose or requirement.purpose,
            target=requirement.target,
            tenant_id=actor.tenant_id,
            matter_id=matter,
            resource_id=str(matter),
        )
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=self.rig.authorizer,
            route_name=requirement.target.removeprefix("api:"),
            capability=grant.capability,
            purpose=grant.purpose,
        )
        return boundary.authorize(
            principal=actor,
            scope=BoundaryScope(
                tenant_id=actor.tenant_id,
                matter_id=matter,
                resource_id=str(matter),
            ),
            correlation_id=uuid4(),
            presented=self.rig.issue(actor, grant),
        )

    def command(self, action: GovernanceAction) -> GovernanceCommand:
        base = {
            "command_id": uuid4(),
            "tenant_id": self.tenant_id,
            "matter_id": self.matter_id,
        }
        if action == GovernanceAction.OPEN_ETHICAL_WALL:
            return OpenEthicalWall(
                **base,  # type: ignore[arg-type]
                wall_id=uuid4(),
                name=f"wall-{uuid4().hex[:8]}",
                effective_from=T0 - timedelta(hours=1),
            )
        raise AssertionError("unsupported synthetic command")

    def execute(
        self,
        command: GovernanceCommand,
        *,
        principal: PrincipalContext | None = None,
        capability: Capability | None = None,
        purpose: Purpose | None = None,
        authorized: AuthorizedContext | None = None,
    ) -> PolicyMutationReceipt:
        context = authorized or self.authorize_for(
            command.action,
            principal=principal,
            capability=capability,
            purpose=purpose,
            matter_id=command.matter_id,
        )
        return self.service.execute(context, command)

    def denial_reason(self, command: GovernanceCommand, **kwargs: object) -> str:
        try:
            self.execute(command, **kwargs)  # type: ignore[arg-type]
        except PolicyGovernanceDenied as exc:
            return exc.decision.reason.value
        raise AssertionError("governance command unexpectedly committed")


class GovernanceCommandModelTests(unittest.TestCase):
    def test_waiver_interval_and_label_family_validation(self) -> None:
        base = {
            "command_id": uuid4(),
            "tenant_id": uuid4(),
            "matter_id": uuid4(),
        }
        with self.assertRaises(ValidationError):
            RegisterWaiverReference(
                **base,  # type: ignore[arg-type]
                waiver_id=uuid4(),
                artifact_id=uuid4(),
                artifact_version=1,
                content_sha256="b" * 64,
                valid_from=T0,
                valid_to=T0,
            )
        with self.assertRaises(ValidationError):
            SetProtectionLabel(
                **base,  # type: ignore[arg-type]
                label_id=uuid4(),
                label_family="work_product",
                label_code="attorney_client",
                material_id=uuid4(),
                material_version=1,
                active=True,
            )
        with self.assertRaises(ValidationError):
            OpenEthicalWall(
                command_id=UUID(int=0),
                tenant_id=uuid4(),
                matter_id=uuid4(),
                wall_id=uuid4(),
                name="wall-one",
                effective_from=T0,
            )

    def test_hold_scope_and_release_times_validate(self) -> None:
        base = {
            "command_id": uuid4(),
            "tenant_id": uuid4(),
            "matter_id": uuid4(),
        }
        with self.assertRaises(ValidationError):
            IssueLegalHold(
                **base,  # type: ignore[arg-type]
                legal_hold_id=uuid4(),
                scope="material",
                effective_from=T0,
            )
        with self.assertRaises(ValidationError):
            ReleaseLegalHold(
                **base,  # type: ignore[arg-type]
                release_id=uuid4(),
                legal_hold_id=uuid4(),
                released_at=datetime(2026, 8, 20, 12, 0),
            )


class GovernanceLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gov = GovernanceRig()
        self.addCleanup(self.gov.close)

    def open_wall(self) -> PolicyMutationReceipt:
        return self.gov.execute(self.gov.command(GovernanceAction.OPEN_ETHICAL_WALL))

    def test_wall_open_seal_close_lifecycle_with_exact_versions(self) -> None:
        receipt = self.open_wall()
        self.assertEqual(1, receipt.policy_change_id)
        self.assertEqual(1, receipt.record_version)
        self.assertEqual(receipt.receipt_sha256, recompute_receipt_sha256(receipt))
        reconstructed = PolicyMutationReceipt.model_validate_json(
            receipt.model_dump_json()
        )
        self.assertEqual(receipt, reconstructed)
        self.assertEqual(
            reconstructed.receipt_sha256,
            recompute_receipt_sha256(reconstructed),
        )
        wall_id = receipt.record_id

        stale_close = CloseEthicalWall(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            wall_id=wall_id,
            expected_version=7,
            effective_to=T0,
        )
        self.assertEqual(
            GovernanceReason.VERSION_CONFLICT.value,
            self.gov.denial_reason(stale_close),
        )

        seal = SealEthicalWallRoster(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            wall_id=wall_id,
            expected_version=1,
        )
        sealed = self.gov.execute(seal)
        self.assertEqual(2, sealed.record_version)
        wall = self.gov.store.current_wall(
            self.gov.tenant_id, self.gov.matter_id, wall_id
        )
        assert wall is not None
        self.assertTrue(wall.membership_complete)

        close = CloseEthicalWall(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            wall_id=wall_id,
            expected_version=2,
            effective_to=T0,
        )
        closed = self.gov.execute(close)
        self.assertEqual(3, closed.record_version)

        again = CloseEthicalWall(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            wall_id=wall_id,
            expected_version=3,
            effective_to=T0,
        )
        self.assertEqual(
            GovernanceReason.STALE_AUTHORITY.value,
            self.gov.denial_reason(again),
        )

    def test_wall_membership_separation_of_duties(self) -> None:
        wall_id = self.open_wall().record_id
        self_membership = SetWallMembership(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            membership_id=uuid4(),
            wall_id=wall_id,
            subject_principal_id=self.gov.principal.principal_id,
            disposition=WallMembershipDisposition.ALLOWED,
            effective_from=T0 - timedelta(hours=1),
        )
        self.assertEqual(
            GovernanceReason.SEPARATION_OF_DUTIES.value,
            self.gov.denial_reason(self_membership),
        )
        membership = SetWallMembership.model_validate(
            {
                **self_membership.model_dump(mode="python"),
                "command_id": uuid4(),
                "subject_principal_id": self.gov.other_principal.principal_id,
            }
        )
        receipt = self.gov.execute(membership)
        self.assertEqual(membership.membership_id, receipt.record_id)

    def test_grant_revoke_lifecycle_and_immediate_effect(self) -> None:
        grant_command = GrantProtectedAccess(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            grant_id=uuid4(),
            grantee_principal_id=self.gov.other_principal.principal_id,
            access_level=ProtectedAccessLevel.PRIVILEGED_WORK_PRODUCT,
            purpose=Purpose.EVIDENCE_REVIEW,
            effective_from=T0 - timedelta(hours=2),
        )
        receipt = self.gov.execute(grant_command)
        grant_id = receipt.record_id

        wrong_version = RevokeProtectedAccess(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            grant_id=grant_id,
            expected_version=9,
            revoked_at=T0,
        )
        self.assertEqual(
            GovernanceReason.VERSION_CONFLICT.value,
            self.gov.denial_reason(wrong_version),
        )

        revoke = RevokeProtectedAccess(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            grant_id=grant_id,
            expected_version=1,
            revoked_at=T0,
        )
        revoked = self.gov.execute(revoke)
        self.assertEqual(2, revoked.record_version)
        current = self.gov.store.current_grant(
            self.gov.tenant_id, self.gov.matter_id, grant_id
        )
        assert current is not None
        self.assertEqual(T0, current.effective_to)
        self.assertFalse(current.is_active(T0))

        repeat = RevokeProtectedAccess(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            grant_id=grant_id,
            expected_version=2,
            revoked_at=T0,
        )
        self.assertEqual(
            GovernanceReason.STALE_AUTHORITY.value,
            self.gov.denial_reason(repeat),
        )

    def test_self_grant_separation_of_duties(self) -> None:
        command = GrantProtectedAccess(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            grant_id=uuid4(),
            grantee_principal_id=self.gov.principal.principal_id,
            access_level=ProtectedAccessLevel.HIGHLY_RESTRICTED,
            purpose=Purpose.EVIDENCE_REVIEW,
            effective_from=T0 - timedelta(hours=1),
        )
        self.assertEqual(
            GovernanceReason.SEPARATION_OF_DUTIES.value,
            self.gov.denial_reason(command),
        )

    def test_protection_label_apply_and_remove_lifecycle(self) -> None:
        apply_command = SetProtectionLabel(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            label_id=uuid4(),
            label_family="privilege",
            label_code="attorney_client",
            material_id=MATERIAL_ID,
            material_version=1,
            active=True,
        )
        receipt = self.gov.execute(apply_command)
        self.assertEqual(apply_command.label_id, receipt.record_id)
        remove_command = SetProtectionLabel(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            label_id=uuid4(),
            label_family="work_product",
            label_code="opinion_work_product",
            material_id=MATERIAL_ID,
            material_version=1,
            active=False,
        )
        removed = self.gov.execute(remove_command)
        self.assertEqual(remove_command.label_id, removed.record_id)
        actions = [
            record.action
            for record, _digest in self.gov.audit.records()
            if record.outcome.value == "committed"
        ]
        self.assertEqual(
            [GovernanceAction.SET_PROTECTION_LABEL] * 2,
            actions,
        )

    def test_retention_policy_genesis_supersede_and_seal(self) -> None:
        genesis = SetRetentionPolicy(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            retention_policy_id=uuid4(),
            retain_for_days=365,
            effective_from=T0 - timedelta(days=10),
        )
        first = self.gov.execute(genesis)
        second_genesis = SetRetentionPolicy(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            retention_policy_id=uuid4(),
            retain_for_days=30,
            effective_from=T0 - timedelta(days=1),
        )
        self.assertEqual(
            GovernanceReason.STALE_AUTHORITY.value,
            self.gov.denial_reason(second_genesis),
        )
        supersede = SetRetentionPolicy(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            retention_policy_id=uuid4(),
            retain_for_days=90,
            effective_from=T0 - timedelta(days=1),
            supersedes_retention_policy_id=first.record_id,
        )
        advanced = self.gov.execute(supersede)
        self.assertEqual(supersede.retention_policy_id, advanced.record_id)
        head = self.gov.store.retention_head(self.gov.tenant_id, self.gov.matter_id)
        assert head is not None
        self.assertEqual(
            supersede.retention_policy_id,
            getattr(head, "retention_policy_id"),
        )
        stale_supersede = SetRetentionPolicy(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            retention_policy_id=uuid4(),
            retain_for_days=10,
            effective_from=T0,
            supersedes_retention_policy_id=first.record_id,
        )
        self.assertEqual(
            GovernanceReason.STALE_AUTHORITY.value,
            self.gov.denial_reason(stale_supersede),
        )

    def test_legal_hold_issue_and_release_with_separation_of_duties(self) -> None:
        issue = IssueLegalHold(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            legal_hold_id=uuid4(),
            scope="matter",
            effective_from=T0 - timedelta(days=2),
        )
        issued = self.gov.execute(issue)
        self_release = ReleaseLegalHold(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            release_id=uuid4(),
            legal_hold_id=issued.record_id,
            released_at=T0,
        )
        self.assertEqual(
            GovernanceReason.SEPARATION_OF_DUTIES.value,
            self.gov.denial_reason(self_release),
        )
        release = ReleaseLegalHold(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            release_id=uuid4(),
            legal_hold_id=issued.record_id,
            released_at=T0,
        )
        receipt = self.gov.execute(release, principal=self.gov.other_principal)
        released = self.gov.store.legal_hold(
            self.gov.tenant_id, self.gov.matter_id, receipt.record_id
        )
        assert released is not None
        self.assertEqual(LegalHoldStatus.RELEASED, released.status)
        self.assertEqual(issued.record_id, released.supersedes_hold_id)
        again = ReleaseLegalHold(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            release_id=uuid4(),
            legal_hold_id=issued.record_id,
            released_at=T0,
        )
        self.assertEqual(
            GovernanceReason.STALE_AUTHORITY.value,
            self.gov.denial_reason(again, principal=self.gov.other_principal),
        )


class GovernanceConflictDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.check_id = uuid4()
        self.checked_by = uuid4()

    def make_rig(
        self,
        *,
        result: str = "hold",
        complete: bool = True,
        version: int = 1,
    ) -> GovernanceRig:
        rig = GovernanceRig()
        evidence = ConflictCheckEvidence(
            check_id=self.check_id,
            tenant_id=rig.tenant_id,
            matter_id=rig.matter_id,
            result=result,  # type: ignore[arg-type]
            complete=complete,
            version=version,
            checked_by_principal_id=self.checked_by,
        )
        rig.store = InMemoryPolicyGovernanceStore(
            audit=rig.audit,
            conflict_checks={self.check_id: evidence},
        )
        rig.service = PolicyGovernanceService(
            store=rig.store,
            current_authorization=rig.verifier,
            clock=rig.rig.clock,
        )
        return rig

    def decide(
        self,
        gov: GovernanceRig,
        disposition: ConflictDisposition,
        *,
        waiver_id: UUID | None = None,
        supersedes: UUID | None = None,
        check_version: int = 1,
        decided_at: datetime = T0 - timedelta(hours=1),
        check_id: UUID | None = None,
    ) -> DecideConflict:
        return DecideConflict(
            command_id=uuid4(),
            tenant_id=gov.tenant_id,
            matter_id=gov.matter_id,
            decision_id=uuid4(),
            conflict_check_id=check_id or self.check_id,
            conflict_check_version=check_version,
            disposition=disposition,
            waiver_id=waiver_id,
            supersedes_decision_id=supersedes,
            decided_at=decided_at,
        )

    def register_waiver(self, gov: GovernanceRig) -> UUID:
        command = RegisterWaiverReference(
            command_id=uuid4(),
            tenant_id=gov.tenant_id,
            matter_id=gov.matter_id,
            waiver_id=uuid4(),
            artifact_id=uuid4(),
            artifact_version=3,
            content_sha256="c" * 64,
            valid_from=T0 - timedelta(days=2),
            valid_to=T0 + timedelta(days=30),
        )
        return gov.execute(command).record_id

    def test_clear_decision_commits_with_exact_check_version(self) -> None:
        gov = self.make_rig(result="clear")
        self.addCleanup(gov.close)
        receipt = gov.execute(self.decide(gov, ConflictDisposition.CLEAR))
        self.assertEqual(1, receipt.policy_change_id)
        stale_version = self.decide(gov, ConflictDisposition.CLEAR, check_version=2)
        self.assertEqual(
            GovernanceReason.VERSION_CONFLICT.value,
            gov.denial_reason(stale_version),
        )

    def test_decision_requires_complete_matching_check_evidence(self) -> None:
        gov = self.make_rig(result="clear")
        self.addCleanup(gov.close)
        mismatched = self.decide(gov, ConflictDisposition.HOLD)
        self.assertEqual(
            GovernanceReason.EVIDENCE_INVALID.value,
            gov.denial_reason(mismatched),
        )
        unknown = self.decide(gov, ConflictDisposition.CLEAR, check_id=uuid4())
        self.assertEqual(
            GovernanceReason.EVIDENCE_INVALID.value,
            gov.denial_reason(unknown),
        )
        incomplete = self.make_rig(result="clear", complete=False)
        self.addCleanup(incomplete.close)
        self.assertEqual(
            GovernanceReason.EVIDENCE_INVALID.value,
            incomplete.denial_reason(
                self.decide(incomplete, ConflictDisposition.CLEAR)
            ),
        )

    def test_waived_decision_requires_effective_waiver_and_other_recorder(
        self,
    ) -> None:
        gov = self.make_rig(result="hold")
        self.addCleanup(gov.close)
        waiver_id = self.register_waiver(gov)
        own_waiver = self.decide(gov, ConflictDisposition.WAIVED, waiver_id=waiver_id)
        self.assertEqual(
            GovernanceReason.SEPARATION_OF_DUTIES.value,
            gov.denial_reason(own_waiver),
        )
        receipt = gov.execute(
            self.decide(
                gov,
                ConflictDisposition.WAIVED,
                waiver_id=waiver_id,
            ),
            principal=gov.other_principal,
        )
        self.assertEqual(GovernanceAction.DECIDE_CONFLICT, receipt.action)
        head = gov.store.decision_head(gov.tenant_id, gov.matter_id)
        assert head is not None
        self.assertEqual(ConflictDisposition.WAIVED, head.disposition)

    def test_supersession_requires_current_head_and_advancing_time(self) -> None:
        gov = self.make_rig(result="hold")
        self.addCleanup(gov.close)
        first = gov.execute(self.decide(gov, ConflictDisposition.HOLD))
        not_head = self.decide(gov, ConflictDisposition.HOLD, supersedes=uuid4())
        self.assertEqual(
            GovernanceReason.STALE_AUTHORITY.value,
            gov.denial_reason(not_head),
        )
        regressive = self.decide(
            gov,
            ConflictDisposition.HOLD,
            supersedes=first.record_id,
            decided_at=T0 - timedelta(hours=2),
        )
        self.assertEqual(
            GovernanceReason.EVIDENCE_INVALID.value,
            gov.denial_reason(regressive),
        )
        advance = self.decide(
            gov,
            ConflictDisposition.HOLD,
            supersedes=first.record_id,
            decided_at=T0 - timedelta(minutes=30),
        )
        receipt = gov.execute(advance)
        head = gov.store.decision_head(gov.tenant_id, gov.matter_id)
        assert head is not None
        self.assertEqual(receipt.record_id, head.decision_id)


class GovernanceAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gov = GovernanceRig()
        self.addCleanup(self.gov.close)

    def test_non_human_principal_never_commits(self) -> None:
        command = self.gov.command(GovernanceAction.OPEN_ETHICAL_WALL)
        requirement = GOVERNANCE_REQUIREMENTS[command.action]
        agent = PrincipalContext(
            principal_id=uuid4(),
            principal_type=PrincipalType.AGENT,
            subject="synthetic:agent:governance",
            tenant_id=self.gov.tenant_id,
        )
        grant = self.gov.rig.grant(
            capability=requirement.capability,
            purpose=requirement.purpose,
            target=requirement.target,
            tenant_id=agent.tenant_id,
            matter_id=command.matter_id,
            resource_id=str(command.matter_id),
        )
        decision = AuthorizationDecision(
            decision_id=uuid4(),
            correlation_id=uuid4(),
            allow=True,
            reason_code=DecisionReason.ALLOW,
            credential_digest="2" * 64,
            principal_id=agent.principal_id,
            tenant_id=agent.tenant_id,
            matter_id=command.matter_id,
            capability=grant.capability,
            audience=grant.audience,
            target=grant.target,
            resource_type=grant.resource_type,
            resource_id=grant.resource_id,
            operation=grant.operation,
            purpose=grant.purpose,
            principal_policy_revisions=(
                PrincipalPolicyReference(
                    principal_id=agent.principal_id, revision="3" * 64
                ),
            ),
            trusted_issuer_policy_revision="4" * 64,
            revocation_revision="5" * 64,
        )
        context = AuthorizedContext(
            decision=decision,
            principal=agent,
            grant=grant,
            credential_expires_at=T0 + timedelta(minutes=5),
            principal_chain=(agent,),
        )
        self.assertEqual(
            GovernanceReason.HUMAN_DECISION_REQUIRED.value,
            self.gov.denial_reason(command, authorized=context),
        )
        wall_id = getattr(command, "wall_id")
        self.assertIsNone(
            self.gov.store.current_wall(self.gov.tenant_id, self.gov.matter_id, wall_id)
        )

    def test_wrong_capability_and_tenant_fail_closed(self) -> None:
        command = self.gov.command(GovernanceAction.OPEN_ETHICAL_WALL)
        self.assertEqual(
            GovernanceReason.CAPAUTH_SCOPE_MISMATCH.value,
            self.gov.denial_reason(
                command,
                capability=Capability.MATTER_MANAGE,
                purpose=Purpose.MATTER_MANAGEMENT,
            ),
        )
        other_tenant = self.gov.rig.principal(tenant_id=uuid4())
        foreign = OpenEthicalWall(
            command_id=uuid4(),
            tenant_id=other_tenant.tenant_id,
            matter_id=self.gov.matter_id,
            wall_id=uuid4(),
            name="wall-foreign",
            effective_from=T0 - timedelta(hours=1),
        )
        self.assertEqual(
            GovernanceReason.CAPAUTH_SCOPE_MISMATCH.value,
            self.gov.denial_reason(foreign),
        )

    def test_replayed_and_expired_authorization_denied(self) -> None:
        command = self.gov.command(GovernanceAction.OPEN_ETHICAL_WALL)
        context = self.gov.authorize_for(command.action)
        self.gov.execute(command, authorized=context)
        replay = self.gov.command(GovernanceAction.OPEN_ETHICAL_WALL)
        self.assertEqual(
            GovernanceReason.CAPAUTH_REPLAYED.value,
            self.gov.denial_reason(replay, authorized=context),
        )

        expiring = self.gov.command(GovernanceAction.OPEN_ETHICAL_WALL)
        stale_context = self.gov.authorize_for(expiring.action)
        self.gov.rig.clock.advance(seconds=400)
        self.assertEqual(
            GovernanceReason.CAPAUTH_EXPIRED.value,
            self.gov.denial_reason(expiring, authorized=stale_context),
        )
        self.gov.rig.clock.value = T0

    def test_idempotent_replay_converges_and_conflict_denies(self) -> None:
        command = self.gov.command(GovernanceAction.OPEN_ETHICAL_WALL)
        first = self.gov.execute(command)
        replayed = self.gov.store.apply(
            command,
            self.gov.service._attribution(  # noqa: SLF001
                self.gov.authorize_for(command.action),
                GOVERNANCE_REQUIREMENTS[command.action],
                T0,
            ),
        )
        self.assertEqual(first, replayed)
        self.assertEqual(1, replayed.policy_change_id)
        conflicting = OpenEthicalWall(
            command_id=command.command_id,
            tenant_id=command.tenant_id,
            matter_id=command.matter_id,
            wall_id=uuid4(),
            name="wall-different",
            effective_from=T0 - timedelta(hours=3),
        )
        self.assertEqual(
            GovernanceReason.IDEMPOTENCY_CONFLICT.value,
            self.gov.denial_reason(conflicting),
        )
        digests = {
            canonical_command_digest(command),
            canonical_command_digest(conflicting),
        }
        self.assertEqual(2, len(digests))

    def test_audit_outage_fails_closed_without_state_change(self) -> None:
        outage = GovernanceRig(audit=None)
        self.addCleanup(outage.close)
        outage.store = InMemoryPolicyGovernanceStore(
            audit=UnavailableGovernanceAuditSink()
        )
        outage.service = PolicyGovernanceService(
            store=outage.store,
            current_authorization=outage.verifier,
            clock=outage.rig.clock,
        )
        command = outage.command(GovernanceAction.OPEN_ETHICAL_WALL)
        with self.assertRaises(PolicyGovernanceUnavailable):
            outage.execute(command)
        wall_id = getattr(command, "wall_id")
        self.assertIsNone(
            outage.store.current_wall(outage.tenant_id, outage.matter_id, wall_id)
        )
        fixed = GovernanceRig()
        self.addCleanup(fixed.close)
        recovered = fixed.execute(command)
        self.assertEqual(command.command_id, recovered.command_id)


class GovernanceRaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gov = GovernanceRig()
        self.addCleanup(self.gov.close)

    def test_concurrent_supersede_exactly_one_wins(self) -> None:
        first = self.gov.execute(self.gov.command(GovernanceAction.OPEN_ETHICAL_WALL))
        wall_id = first.record_id
        barrier = Event()
        outcomes: list[str] = []

        def close_once() -> None:
            command = CloseEthicalWall(
                command_id=uuid4(),
                tenant_id=self.gov.tenant_id,
                matter_id=self.gov.matter_id,
                wall_id=wall_id,
                expected_version=1,
                effective_to=T0,
            )
            barrier.wait(timeout=5)
            try:
                self.gov.execute(command)
                outcomes.append("committed")
            except PolicyGovernanceDenied as exc:
                outcomes.append(exc.decision.reason.value)

        threads = [Thread(target=close_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.set()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(2, len(outcomes))
        self.assertEqual(1, outcomes.count("committed"))
        loser = [item for item in outcomes if item != "committed"]
        self.assertEqual(1, len(loser))
        self.assertIn(
            loser[0],
            {
                GovernanceReason.VERSION_CONFLICT.value,
                GovernanceReason.STALE_AUTHORITY.value,
            },
        )
        wall = self.gov.store.current_wall(
            self.gov.tenant_id, self.gov.matter_id, wall_id
        )
        assert wall is not None
        self.assertFalse(wall.active)

    def test_revocation_racing_access_evaluation_wins(self) -> None:
        grant_command = GrantProtectedAccess(
            command_id=uuid4(),
            tenant_id=self.gov.tenant_id,
            matter_id=self.gov.matter_id,
            grant_id=uuid4(),
            grantee_principal_id=self.gov.other_principal.principal_id,
            access_level=ProtectedAccessLevel.PRIVILEGED_WORK_PRODUCT,
            purpose=Purpose.EVIDENCE_REVIEW,
            effective_from=T0 - timedelta(hours=2),
        )
        grant_id = self.gov.execute(grant_command).record_id

        entered = Event()
        release = Event()

        def facts() -> MaterialPolicyFacts:
            grant = self.gov.store.current_grant(
                self.gov.tenant_id, self.gov.matter_id, grant_id
            )
            assert grant is not None
            return MaterialPolicyFacts.model_validate(
                {
                    "policy_revision": REVISION,
                    "tenant_id": self.gov.tenant_id,
                    "matter_id": self.gov.matter_id,
                    "material_id": MATERIAL_ID,
                    "material_version": 1,
                    "tenant_membership": AccessState.ACTIVE,
                    "matter_membership": AccessState.ACTIVE,
                    "conflict_state_complete": True,
                    "wall_state_complete": True,
                    "classification_state_complete": True,
                    "conflict_decision": ConflictDecision(
                        decision_id=uuid4(),
                        tenant_id=self.gov.tenant_id,
                        matter_id=self.gov.matter_id,
                        conflict_check_id=uuid4(),
                        disposition=ConflictDisposition.CLEAR,
                        decided_by_principal_id=uuid4(),
                        decided_at=T0 - timedelta(days=1),
                    ),
                    "classification_sources": (
                        ClassificationSource(
                            source_kind="material",
                            source_id=MATERIAL_ID,
                            classification=(DataClassification.PRIVILEGED_WORK_PRODUCT),
                        ),
                    ),
                    "protected_access_grants": (grant,),
                    "legal_hold_state_complete": True,
                    "ownership_resolved": True,
                    "pending_export": False,
                    "preservation_required": False,
                }
            )

        class BlockingBackend:
            def load(self, request: object) -> MaterialPolicyFacts:
                del request
                entered.set()
                if not release.wait(timeout=5):
                    raise RuntimeError("synthetic backend wait timeout")
                return facts()

        rig = self.gov.rig
        requirement = PolicyBoundaryRequirement(
            boundary=DataFlowBoundary.RETRIEVAL,
            audience=Audience.API,
            target="api:material.retrieve",
            capability=Capability.EVIDENCE_READ,
            purpose=Purpose.EVIDENCE_REVIEW,
        )
        access_grant = rig.grant(
            capability=Capability.EVIDENCE_READ,
            purpose=Purpose.EVIDENCE_REVIEW,
            target="api:material.retrieve",
            tenant_id=self.gov.other_principal.tenant_id,
            matter_id=self.gov.matter_id,
            resource_id=str(MATERIAL_ID),
            resource_version=1,
            resource_sha256=RESOURCE_DIGEST,
        )
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=rig.authorizer,
            route_name="material.retrieve",
            capability=Capability.EVIDENCE_READ,
            purpose=Purpose.EVIDENCE_REVIEW,
        )
        authorized = boundary.authorize(
            principal=self.gov.other_principal,
            scope=BoundaryScope(
                tenant_id=self.gov.other_principal.tenant_id,
                matter_id=self.gov.matter_id,
                resource_id=str(MATERIAL_ID),
                resource_version=1,
                resource_sha256=RESOURCE_DIGEST,
            ),
            correlation_id=uuid4(),
            presented=rig.issue(self.gov.other_principal, access_grant),
        )
        gateway = PolicyGateway(
            backend=BlockingBackend(),
            audit_sink=InMemoryPolicyAuditSink(),
            current_authorization=CapAuthCurrentStateVerifier(
                trusted_issuers=rig.trusted,
                principals=rig.principals,
                revocations=rig.revocations,
                uses=InMemoryAuthorizationUseBackend(),
            ),
            clock=lambda: T0,
        )
        request = PolicyAccessRequest(
            boundary=DataFlowBoundary.RETRIEVAL,
            principal_id=self.gov.other_principal.principal_id,
            tenant_id=self.gov.other_principal.tenant_id,
            matter_id=self.gov.matter_id,
            material_id=MATERIAL_ID,
            material_version=1,
            material_sha256=RESOURCE_DIGEST,
            purpose=Purpose.EVIDENCE_REVIEW,
            evaluated_at=T0,
        )
        outcome: list[str] = []

        def access() -> None:
            try:
                gateway.authorize(authorized, requirement, request)
                outcome.append("allow")
            except PolicyDenied as exc:
                outcome.append(exc.decision.reason.value)

        thread = Thread(target=access)
        thread.start()
        if not entered.wait(timeout=5):
            raise AssertionError("access evaluation never reached policy load")
        self.gov.execute(
            RevokeProtectedAccess(
                command_id=uuid4(),
                tenant_id=self.gov.tenant_id,
                matter_id=self.gov.matter_id,
                grant_id=grant_id,
                expected_version=1,
                revoked_at=T0,
            )
        )
        release.set()
        thread.join(timeout=10)
        self.assertEqual([PolicyReason.PROTECTED_ACCESS_REQUIRED.value], outcome)


class GovernanceWriteGuardTests(unittest.TestCase):
    def test_barrier_tables_remain_write_denied_in_migration(self) -> None:
        text = (
            REPO_ROOT / "migrations" / "0006_legal_information_barriers.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("ENABLE ROW LEVEL SECURITY", text)
        self.assertIn("FORCE ROW LEVEL SECURITY", text)
        self.assertIn("BEFORE UPDATE OR DELETE", text)
        self.assertIn("reject_record_change", text)
        self.assertIn("FOR SELECT", text)
        self.assertNotIn("FOR INSERT", text)
        self.assertNotIn("POLICY policy_record_insert", text)
        self.assertNotIn("POLICY policy_record_update", text)
        self.assertNotIn("POLICY policy_record_delete", text)


if __name__ == "__main__":
    unittest.main()
