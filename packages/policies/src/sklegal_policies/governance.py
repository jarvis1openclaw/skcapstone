"""CapAuth-gated human policy-decision mutations with revocation and receipts.

Every mutation in this module is a governed human decision: conflict
dispositions, waiver evidence, ethical walls and their rosters, protected
access grants, privilege and work-product labels, retention policies, and
legal holds. Models may propose, but only an authenticated human principal
holding an exact current CapAuth grant can commit one of these records.

The store protocol is the transactional boundary: state validation, the
append, and the sanitized audit or outbox record commit atomically. Any
outage fails closed with no state change.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Literal, Protocol, Self, cast
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from sklegal_capauth import (
    Audience,
    AuthorizedContext,
    BoundaryScope,
    Capability,
    CapabilityRequirement,
    PrincipalType,
    Purpose,
)

from .backends import (
    CapAuthCurrentStateVerifier,
    CurrentAuthorizationExpired,
    CurrentAuthorizationReplayed,
    CurrentAuthorizationStale,
    CurrentAuthorizationUnavailable,
)
from .models import (
    ConflictDecision,
    ConflictDisposition,
    EthicalWall,
    LegalHold,
    LegalHoldStatus,
    PolicyValue,
    PrivilegeLabel,
    ProtectedAccessGrant,
    ProtectedAccessLevel,
    RetentionPolicy,
    Sha256,
    ShortCode,
    WaiverReference,
    WallMembership,
    WallMembershipDisposition,
    WorkProductLabel,
    require_utc,
)

PrivilegeLabelCode = Literal[
    "attorney_client", "common_interest", "joint_defense", "other_privilege_review"
]
WorkProductLabelCode = Literal[
    "attorney_work_product", "opinion_work_product", "other_work_product"
]

PRIVILEGE_LABEL_CODES = frozenset(
    {"attorney_client", "common_interest", "joint_defense", "other_privilege_review"}
)
WORK_PRODUCT_LABEL_CODES = frozenset(
    {"attorney_work_product", "opinion_work_product", "other_work_product"}
)


class GovernanceAction(StrEnum):
    REGISTER_WAIVER = "register_waiver"
    DECIDE_CONFLICT = "decide_conflict"
    OPEN_ETHICAL_WALL = "open_ethical_wall"
    SEAL_ETHICAL_WALL_ROSTER = "seal_ethical_wall_roster"
    CLOSE_ETHICAL_WALL = "close_ethical_wall"
    SET_WALL_MEMBERSHIP = "set_wall_membership"
    GRANT_PROTECTED_ACCESS = "grant_protected_access"
    REVOKE_PROTECTED_ACCESS = "revoke_protected_access"
    SET_PROTECTION_LABEL = "set_protection_label"
    SET_RETENTION_POLICY = "set_retention_policy"
    ISSUE_LEGAL_HOLD = "issue_legal_hold"
    RELEASE_LEGAL_HOLD = "release_legal_hold"


class GovernanceReason(StrEnum):
    COMMITTED = "committed"
    HUMAN_DECISION_REQUIRED = "human_decision_required"
    CAPAUTH_SCOPE_MISMATCH = "capauth_scope_mismatch"
    CAPAUTH_EXPIRED = "capauth_expired"
    CAPAUTH_REPLAYED = "capauth_replayed"
    CAPAUTH_STALE = "capauth_stale"
    CAPAUTH_CURRENT_STATE_UNAVAILABLE = "capauth_current_state_unavailable"
    STALE_AUTHORITY = "stale_authority"
    VERSION_CONFLICT = "version_conflict"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    SEPARATION_OF_DUTIES = "separation_of_duties"
    EVIDENCE_INVALID = "evidence_invalid"


class GovernanceOutcome(StrEnum):
    COMMITTED = "committed"
    DENIED = "denied"


class PolicyGovernanceDenied(PermissionError):
    """Sanitized governance denial carrying only audit-safe evidence."""

    def __init__(self, decision: GovernanceDecision) -> None:
        self.decision = decision
        super().__init__("policy governance decision denied")


class PolicyGovernanceUnavailable(RuntimeError):
    """A governance store, audit, clock, or CapAuth dependency has no answer."""


class GovernanceCommand(PolicyValue):
    """Strict base for one human policy-decision request."""

    command_id: UUID
    tenant_id: UUID
    matter_id: UUID
    action: GovernanceAction


class RegisterWaiverReference(GovernanceCommand):
    action: Literal[GovernanceAction.REGISTER_WAIVER] = GovernanceAction.REGISTER_WAIVER
    waiver_id: UUID
    artifact_id: UUID
    artifact_version: int = Field(ge=1)
    content_sha256: Sha256
    valid_from: datetime
    valid_to: datetime

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        require_utc(self.valid_from)
        require_utc(self.valid_to)
        if self.valid_to <= self.valid_from:
            raise ValueError("waiver interval must be nonempty")
        return self


class DecideConflict(GovernanceCommand):
    action: Literal[GovernanceAction.DECIDE_CONFLICT] = GovernanceAction.DECIDE_CONFLICT
    decision_id: UUID
    conflict_check_id: UUID
    conflict_check_version: int = Field(ge=1)
    disposition: ConflictDisposition
    waiver_id: UUID | None = None
    supersedes_decision_id: UUID | None = None
    decided_at: datetime

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        require_utc(self.decided_at)
        if (self.disposition == ConflictDisposition.WAIVED) != (
            self.waiver_id is not None
        ):
            raise ValueError("only a waived disposition names waiver evidence")
        return self


class OpenEthicalWall(GovernanceCommand):
    action: Literal[GovernanceAction.OPEN_ETHICAL_WALL] = (
        GovernanceAction.OPEN_ETHICAL_WALL
    )
    wall_id: UUID
    name: ShortCode
    effective_from: datetime

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        require_utc(self.effective_from)
        return self


class SealEthicalWallRoster(GovernanceCommand):
    action: Literal[GovernanceAction.SEAL_ETHICAL_WALL_ROSTER] = (
        GovernanceAction.SEAL_ETHICAL_WALL_ROSTER
    )
    wall_id: UUID
    expected_version: int = Field(ge=1)


class CloseEthicalWall(GovernanceCommand):
    action: Literal[GovernanceAction.CLOSE_ETHICAL_WALL] = (
        GovernanceAction.CLOSE_ETHICAL_WALL
    )
    wall_id: UUID
    expected_version: int = Field(ge=1)
    effective_to: datetime

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        require_utc(self.effective_to)
        return self


class SetWallMembership(GovernanceCommand):
    action: Literal[GovernanceAction.SET_WALL_MEMBERSHIP] = (
        GovernanceAction.SET_WALL_MEMBERSHIP
    )
    membership_id: UUID
    wall_id: UUID
    subject_principal_id: UUID
    disposition: WallMembershipDisposition
    effective_from: datetime
    effective_to: datetime | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        require_utc(self.effective_from)
        if self.effective_to is not None:
            require_utc(self.effective_to)
            if self.effective_to <= self.effective_from:
                raise ValueError("wall membership interval must be nonempty")
        return self


class GrantProtectedAccess(GovernanceCommand):
    action: Literal[GovernanceAction.GRANT_PROTECTED_ACCESS] = (
        GovernanceAction.GRANT_PROTECTED_ACCESS
    )
    grant_id: UUID
    grantee_principal_id: UUID
    access_level: ProtectedAccessLevel
    purpose: Purpose
    effective_from: datetime
    effective_to: datetime | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        require_utc(self.effective_from)
        if self.effective_to is not None:
            require_utc(self.effective_to)
            if self.effective_to <= self.effective_from:
                raise ValueError("protected access interval must be nonempty")
        return self


class RevokeProtectedAccess(GovernanceCommand):
    action: Literal[GovernanceAction.REVOKE_PROTECTED_ACCESS] = (
        GovernanceAction.REVOKE_PROTECTED_ACCESS
    )
    grant_id: UUID
    expected_version: int = Field(ge=1)
    revoked_at: datetime

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        require_utc(self.revoked_at)
        return self


class SetProtectionLabel(GovernanceCommand):
    action: Literal[GovernanceAction.SET_PROTECTION_LABEL] = (
        GovernanceAction.SET_PROTECTION_LABEL
    )
    label_id: UUID
    label_family: Literal["privilege", "work_product"]
    label_code: ShortCode
    material_id: UUID
    material_version: int = Field(ge=1)
    active: bool

    @model_validator(mode="after")
    def validate_label(self) -> Self:
        allowed = (
            PRIVILEGE_LABEL_CODES
            if self.label_family == "privilege"
            else WORK_PRODUCT_LABEL_CODES
        )
        if self.label_code not in allowed:
            raise ValueError("label code does not belong to its label family")
        return self


class SetRetentionPolicy(GovernanceCommand):
    action: Literal[GovernanceAction.SET_RETENTION_POLICY] = (
        GovernanceAction.SET_RETENTION_POLICY
    )
    retention_policy_id: UUID
    retain_for_days: int = Field(ge=1, le=36500)
    effective_from: datetime
    supersedes_retention_policy_id: UUID | None = None

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        require_utc(self.effective_from)
        return self


class IssueLegalHold(GovernanceCommand):
    action: Literal[GovernanceAction.ISSUE_LEGAL_HOLD] = (
        GovernanceAction.ISSUE_LEGAL_HOLD
    )
    legal_hold_id: UUID
    scope: Literal["matter", "material"]
    material_id: UUID | None = None
    effective_from: datetime

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        require_utc(self.effective_from)
        if (self.scope == "material") != (self.material_id is not None):
            raise ValueError("material hold scope requires one material identifier")
        return self


class ReleaseLegalHold(GovernanceCommand):
    action: Literal[GovernanceAction.RELEASE_LEGAL_HOLD] = (
        GovernanceAction.RELEASE_LEGAL_HOLD
    )
    release_id: UUID
    legal_hold_id: UUID
    released_at: datetime

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        require_utc(self.released_at)
        return self


class ConflictCheckEvidence(PolicyValue):
    """Store-seeded evidence that one conflict check completed exactly."""

    check_id: UUID
    tenant_id: UUID
    matter_id: UUID
    result: Literal["clear", "hold"]
    complete: bool
    version: int = Field(ge=1)
    checked_by_principal_id: UUID


class GovernanceAttribution(PolicyValue):
    """Trusted attribution derived from authorization, never from the body."""

    principal_id: UUID
    principal_type: PrincipalType
    capauth_decision_id: UUID
    correlation_id: UUID
    run_id: UUID
    capability: Capability
    purpose: Purpose
    target: ShortCode
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        require_utc(self.occurred_at)
        return self


class GovernanceAuditRecord(PolicyValue):
    """Sanitized human-boundary audit record for one governance outcome."""

    event_id: UUID
    tenant_id: UUID
    matter_id: UUID
    principal_id: UUID
    action: GovernanceAction
    capability: ShortCode
    purpose: ShortCode
    target: ShortCode
    outcome: GovernanceOutcome
    reason: GovernanceReason
    command_id: UUID
    command_digest: Sha256
    capauth_decision_id: UUID
    correlation_id: UUID
    run_id: UUID
    record_id: UUID | None = None
    record_version: int | None = Field(default=None, ge=1)
    policy_change_id: int | None = Field(default=None, ge=1)
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        require_utc(self.occurred_at)
        committed = self.outcome == GovernanceOutcome.COMMITTED
        if committed != (
            self.record_id is not None
            and self.record_version is not None
            and self.policy_change_id is not None
        ):
            raise ValueError("committed audit requires exact record evidence")
        if committed != (self.reason == GovernanceReason.COMMITTED):
            raise ValueError("governance outcome and reason disagree")
        return self


class GovernanceDecision(PolicyValue):
    """Sanitized denial receipt for one rejected governance command."""

    decision_id: UUID
    command_id: UUID
    command_digest: Sha256
    action: GovernanceAction
    reason: GovernanceReason
    tenant_id: UUID
    matter_id: UUID
    principal_id: UUID
    capauth_decision_id: UUID
    correlation_id: UUID
    audit_event_id: UUID
    audit_event_sha256: Sha256
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        require_utc(self.occurred_at)
        return self


class PolicyMutationReceipt(PolicyValue):
    """Exact reconstructable receipt for one committed human decision."""

    receipt_id: UUID
    command_id: UUID
    command_digest: Sha256
    action: GovernanceAction
    tenant_id: UUID
    matter_id: UUID
    record_id: UUID
    record_version: int = Field(ge=1)
    policy_change_id: int = Field(ge=1)
    audit_event_id: UUID
    audit_event_sha256: Sha256
    occurred_at: datetime
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        require_utc(self.occurred_at)
        return self


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def canonical_command_digest(command: GovernanceCommand) -> Sha256:
    """Digest the exact validated command payload for idempotency."""

    encoded = _canonical_json_bytes(
        {
            "schema": "sklegal-governance-command/v1",
            "command": command.model_dump(mode="json"),
        }
    )
    return hashlib.sha256(encoded).hexdigest()


def recompute_receipt_sha256(receipt: PolicyMutationReceipt) -> Sha256:
    """Recompute one receipt digest without trusting the stored value."""

    payload = receipt.model_dump(mode="json")
    payload.pop("receipt_sha256", None)
    encoded = _canonical_json_bytes(
        {
            "schema": "sklegal-governance-receipt/v1",
            "receipt": payload,
        }
    )
    return hashlib.sha256(encoded).hexdigest()


def _receipt_digest(receipt: PolicyMutationReceipt) -> Sha256:
    return recompute_receipt_sha256(receipt)


class GovernanceAuditSink(Protocol):
    def record(self, record: GovernanceAuditRecord) -> Sha256: ...


class InMemoryGovernanceAuditSink:
    """Synthetic sink for tests and local development, never production."""

    def __init__(self) -> None:
        self._records: list[tuple[GovernanceAuditRecord, Sha256]] = []

    def record(self, record: GovernanceAuditRecord) -> Sha256:
        digest = hashlib.sha256(
            _canonical_json_bytes(
                {
                    "schema": "sklegal-governance-audit/v1",
                    "record": record.model_dump(mode="json"),
                }
            )
        ).hexdigest()
        self._records.append((record, digest))
        return digest

    def records(self) -> tuple[tuple[GovernanceAuditRecord, Sha256], ...]:
        return tuple(self._records)


class UnavailableGovernanceAuditSink:
    def record(self, record: GovernanceAuditRecord) -> Sha256:
        del record
        raise PolicyGovernanceUnavailable("governance audit sink unavailable")


@dataclass
class _StoredRow:
    value: PolicyValue
    version: int
    policy_change_id: int
    recorded_by: UUID
    recorded_at: datetime


@dataclass
class _MatterPolicyState:
    waivers: dict[UUID, _StoredRow] = field(default_factory=dict)
    decisions: list[_StoredRow] = field(default_factory=list)
    decision_links: dict[UUID, UUID] = field(default_factory=dict)
    walls: dict[UUID, list[_StoredRow]] = field(default_factory=dict)
    memberships: dict[UUID, _StoredRow] = field(default_factory=dict)
    grants: dict[UUID, list[_StoredRow]] = field(default_factory=dict)
    privilege_labels: dict[UUID, _StoredRow] = field(default_factory=dict)
    work_product_labels: dict[UUID, _StoredRow] = field(default_factory=dict)
    retention: list[_StoredRow] = field(default_factory=list)
    retention_links: dict[UUID, UUID] = field(default_factory=dict)
    legal_holds: dict[UUID, _StoredRow] = field(default_factory=dict)
    hold_releases: dict[UUID, UUID] = field(default_factory=dict)


@dataclass
class _Staged:
    record_id: UUID
    record_version: int
    commit: Callable[[int, UUID, datetime], None]


class _StateDenial(Exception):
    def __init__(self, reason: GovernanceReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def _current_row(history: list[_StoredRow]) -> _StoredRow:
    return history[-1]


class PolicyGovernanceStore(Protocol):
    """Transactional boundary for human policy-decision mutations."""

    def apply(
        self,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
    ) -> PolicyMutationReceipt: ...

    def record_denial(
        self,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
        *,
        reason: GovernanceReason,
    ) -> GovernanceDecision: ...


class InMemoryPolicyGovernanceStore:
    """Synthetic serialized store for tests, never production composition.

    One lock serializes every validation and commit, so a revocation racing
    another mutation or an access evaluation always lands in one exact order.
    """

    def __init__(
        self,
        *,
        audit: GovernanceAuditSink,
        conflict_checks: Mapping[UUID, ConflictCheckEvidence] | None = None,
    ) -> None:
        self._audit = audit
        self._conflict_checks = dict(conflict_checks or {})
        self._lock = Lock()
        self._states: dict[tuple[UUID, UUID], _MatterPolicyState] = {}
        self._commands: dict[UUID, tuple[Sha256, PolicyMutationReceipt]] = {}
        self._sequence = 0

    def _state(self, command: GovernanceCommand) -> _MatterPolicyState:
        return self._states.setdefault(
            (command.tenant_id, command.matter_id), _MatterPolicyState()
        )

    def _audit_locked(self, record: GovernanceAuditRecord) -> Sha256:
        try:
            digest = self._audit.record(record)
        except Exception:
            raise PolicyGovernanceUnavailable(
                "governance audit sink unavailable"
            ) from None
        if not isinstance(digest, str) or len(digest) != 64:
            raise PolicyGovernanceUnavailable(
                "governance audit sink returned invalid evidence"
            )
        return digest

    def _audit_record(
        self,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
        *,
        outcome: GovernanceOutcome,
        reason: GovernanceReason,
        record_id: UUID | None = None,
        record_version: int | None = None,
        policy_change_id: int | None = None,
    ) -> GovernanceAuditRecord:
        return GovernanceAuditRecord(
            event_id=uuid4(),
            tenant_id=command.tenant_id,
            matter_id=command.matter_id,
            principal_id=attribution.principal_id,
            action=command.action,
            capability=attribution.capability.value,
            purpose=attribution.purpose.value,
            target=attribution.target,
            outcome=outcome,
            reason=reason,
            command_id=command.command_id,
            command_digest=canonical_command_digest(command),
            capauth_decision_id=attribution.capauth_decision_id,
            correlation_id=attribution.correlation_id,
            run_id=attribution.run_id,
            record_id=record_id,
            record_version=record_version,
            policy_change_id=policy_change_id,
            occurred_at=attribution.occurred_at,
        )

    def _deny_locked(
        self,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
        reason: GovernanceReason,
    ) -> GovernanceDecision:
        record = self._audit_record(
            command,
            attribution,
            outcome=GovernanceOutcome.DENIED,
            reason=reason,
        )
        digest = self._audit_locked(record)
        return GovernanceDecision(
            decision_id=uuid4(),
            command_id=command.command_id,
            command_digest=record.command_digest,
            action=command.action,
            reason=reason,
            tenant_id=command.tenant_id,
            matter_id=command.matter_id,
            principal_id=attribution.principal_id,
            capauth_decision_id=attribution.capauth_decision_id,
            correlation_id=attribution.correlation_id,
            audit_event_id=record.event_id,
            audit_event_sha256=digest,
            occurred_at=attribution.occurred_at,
        )

    def record_denial(
        self,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
        *,
        reason: GovernanceReason,
    ) -> GovernanceDecision:
        if reason == GovernanceReason.COMMITTED:
            raise ValueError("record_denial requires a denial reason")
        with self._lock:
            return self._deny_locked(command, attribution, reason)

    def apply(
        self,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
    ) -> PolicyMutationReceipt:
        digest = canonical_command_digest(command)
        with self._lock:
            prior = self._commands.get(command.command_id)
            if prior is not None:
                if prior[0] == digest:
                    return prior[1]
                decision = self._deny_locked(
                    command, attribution, GovernanceReason.IDEMPOTENCY_CONFLICT
                )
                raise PolicyGovernanceDenied(decision) from None
            state = self._state(command)
            stagers: dict[GovernanceAction, Callable[..., _Staged]] = {
                GovernanceAction.REGISTER_WAIVER: self._stage_register_waiver,
                GovernanceAction.DECIDE_CONFLICT: self._stage_decide_conflict,
                GovernanceAction.OPEN_ETHICAL_WALL: self._stage_open_wall,
                GovernanceAction.SEAL_ETHICAL_WALL_ROSTER: self._stage_seal_roster,
                GovernanceAction.CLOSE_ETHICAL_WALL: self._stage_close_wall,
                GovernanceAction.SET_WALL_MEMBERSHIP: self._stage_membership,
                GovernanceAction.GRANT_PROTECTED_ACCESS: self._stage_grant,
                GovernanceAction.REVOKE_PROTECTED_ACCESS: self._stage_revoke_grant,
                GovernanceAction.SET_PROTECTION_LABEL: self._stage_label,
                GovernanceAction.SET_RETENTION_POLICY: self._stage_retention,
                GovernanceAction.ISSUE_LEGAL_HOLD: self._stage_issue_hold,
                GovernanceAction.RELEASE_LEGAL_HOLD: self._stage_release_hold,
            }
            try:
                staged = stagers[command.action](state, command, attribution)
            except _StateDenial as denial:
                decision = self._deny_locked(command, attribution, denial.reason)
                raise PolicyGovernanceDenied(decision) from None
            policy_change_id = self._sequence + 1
            record = self._audit_record(
                command,
                attribution,
                outcome=GovernanceOutcome.COMMITTED,
                reason=GovernanceReason.COMMITTED,
                record_id=staged.record_id,
                record_version=staged.record_version,
                policy_change_id=policy_change_id,
            )
            audit_digest = self._audit_locked(record)
            staged.commit(
                policy_change_id, attribution.principal_id, attribution.occurred_at
            )
            self._sequence = policy_change_id
            receipt = PolicyMutationReceipt(
                receipt_id=uuid4(),
                command_id=command.command_id,
                command_digest=digest,
                action=command.action,
                tenant_id=command.tenant_id,
                matter_id=command.matter_id,
                record_id=staged.record_id,
                record_version=staged.record_version,
                policy_change_id=policy_change_id,
                audit_event_id=record.event_id,
                audit_event_sha256=audit_digest,
                occurred_at=attribution.occurred_at,
                receipt_sha256="0" * 64,
            )
            receipt = PolicyMutationReceipt.model_validate(
                {
                    **receipt.model_dump(mode="python"),
                    "receipt_sha256": _receipt_digest(receipt),
                }
            )
            self._commands[command.command_id] = (digest, receipt)
            return receipt

    def _stage_register_waiver(
        self,
        state: _MatterPolicyState,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
    ) -> _Staged:
        request = cast(RegisterWaiverReference, command)
        if request.waiver_id in state.waivers:
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        waiver = WaiverReference(
            waiver_id=request.waiver_id,
            artifact_id=request.artifact_id,
            artifact_version=request.artifact_version,
            content_sha256=request.content_sha256,
            tenant_id=request.tenant_id,
            matter_id=request.matter_id,
            valid_from=request.valid_from,
            valid_to=request.valid_to,
        )

        def commit(change_id: int, principal: UUID, at: datetime) -> None:
            state.waivers[request.waiver_id] = _StoredRow(
                value=waiver,
                version=1,
                policy_change_id=change_id,
                recorded_by=principal,
                recorded_at=at,
            )

        return _Staged(record_id=request.waiver_id, record_version=1, commit=commit)

    def _decision_head(self, state: _MatterPolicyState) -> _StoredRow | None:
        superseded = set(state.decision_links.values())
        heads = [
            row for row in state.decisions if self._decision_id(row) not in superseded
        ]
        return heads[-1] if heads else None

    @staticmethod
    def _decision_id(row: _StoredRow) -> UUID:
        return cast(ConflictDecision, row.value).decision_id

    def _stage_decide_conflict(
        self,
        state: _MatterPolicyState,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
    ) -> _Staged:
        request = cast(DecideConflict, command)
        if any(
            self._decision_id(row) == request.decision_id for row in state.decisions
        ):
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        evidence = self._conflict_checks.get(request.conflict_check_id)
        if (
            evidence is None
            or evidence.tenant_id != request.tenant_id
            or evidence.matter_id != request.matter_id
            or not evidence.complete
        ):
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        if evidence.version != request.conflict_check_version:
            raise _StateDenial(GovernanceReason.VERSION_CONFLICT)
        if (request.disposition == ConflictDisposition.CLEAR) != (
            evidence.result == "clear"
        ):
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        if request.decided_at > attribution.occurred_at:
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        head = self._decision_head(state)
        if request.supersedes_decision_id is None:
            if head is not None:
                raise _StateDenial(GovernanceReason.STALE_AUTHORITY)
        else:
            if head is None or self._decision_id(head) != (
                request.supersedes_decision_id
            ):
                raise _StateDenial(GovernanceReason.STALE_AUTHORITY)
            prior = cast(ConflictDecision, head.value)
            if request.decided_at <= prior.decided_at:
                raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        waiver: WaiverReference | None = None
        if request.disposition == ConflictDisposition.WAIVED:
            waiver_row = state.waivers.get(cast(UUID, request.waiver_id))
            if waiver_row is None:
                raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
            waiver = cast(WaiverReference, waiver_row.value)
            if not waiver.is_effective(request.decided_at):
                raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
            if waiver_row.recorded_by == attribution.principal_id:
                raise _StateDenial(GovernanceReason.SEPARATION_OF_DUTIES)
        decision = ConflictDecision(
            decision_id=request.decision_id,
            tenant_id=request.tenant_id,
            matter_id=request.matter_id,
            conflict_check_id=request.conflict_check_id,
            disposition=request.disposition,
            waiver_reference=waiver,
            decided_by_principal_id=attribution.principal_id,
            decided_at=request.decided_at,
        )

        def commit(change_id: int, principal: UUID, at: datetime) -> None:
            state.decisions.append(
                _StoredRow(
                    value=decision,
                    version=1,
                    policy_change_id=change_id,
                    recorded_by=principal,
                    recorded_at=at,
                )
            )
            if request.supersedes_decision_id is not None:
                state.decision_links[request.decision_id] = (
                    request.supersedes_decision_id
                )

        return _Staged(record_id=request.decision_id, record_version=1, commit=commit)

    def _stage_open_wall(
        self,
        state: _MatterPolicyState,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
    ) -> _Staged:
        del attribution
        request = cast(OpenEthicalWall, command)
        if request.wall_id in state.walls:
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        for history in state.walls.values():
            current = cast(EthicalWall, _current_row(history).value)
            if current.active and current.name == request.name:
                raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        wall = EthicalWall(
            wall_id=request.wall_id,
            tenant_id=request.tenant_id,
            matter_id=request.matter_id,
            name=request.name,
            active=True,
            membership_complete=False,
            effective_from=request.effective_from,
        )

        def commit(change_id: int, principal: UUID, at: datetime) -> None:
            state.walls[request.wall_id] = [
                _StoredRow(
                    value=wall,
                    version=1,
                    policy_change_id=change_id,
                    recorded_by=principal,
                    recorded_at=at,
                )
            ]

        return _Staged(record_id=request.wall_id, record_version=1, commit=commit)

    def _wall_history(
        self, state: _MatterPolicyState, wall_id: UUID
    ) -> list[_StoredRow]:
        history = state.walls.get(wall_id)
        if history is None:
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        return history

    def _stage_seal_roster(
        self,
        state: _MatterPolicyState,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
    ) -> _Staged:
        del attribution
        request = cast(SealEthicalWallRoster, command)
        history = self._wall_history(state, request.wall_id)
        current = _current_row(history)
        wall = cast(EthicalWall, current.value)
        if not wall.active or wall.effective_to is not None:
            raise _StateDenial(GovernanceReason.STALE_AUTHORITY)
        if current.version != request.expected_version:
            raise _StateDenial(GovernanceReason.VERSION_CONFLICT)
        sealed = EthicalWall.model_validate(
            {**wall.model_dump(mode="python"), "membership_complete": True}
        )

        def commit(change_id: int, principal: UUID, at: datetime) -> None:
            history.append(
                _StoredRow(
                    value=sealed,
                    version=current.version + 1,
                    policy_change_id=change_id,
                    recorded_by=principal,
                    recorded_at=at,
                )
            )

        return _Staged(
            record_id=request.wall_id,
            record_version=current.version + 1,
            commit=commit,
        )

    def _stage_close_wall(
        self,
        state: _MatterPolicyState,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
    ) -> _Staged:
        request = cast(CloseEthicalWall, command)
        history = self._wall_history(state, request.wall_id)
        current = _current_row(history)
        wall = cast(EthicalWall, current.value)
        if not wall.active or wall.effective_to is not None:
            raise _StateDenial(GovernanceReason.STALE_AUTHORITY)
        if current.version != request.expected_version:
            raise _StateDenial(GovernanceReason.VERSION_CONFLICT)
        if (
            request.effective_to <= wall.effective_from
            or request.effective_to > attribution.occurred_at
        ):
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        closed = EthicalWall.model_validate(
            {
                **wall.model_dump(mode="python"),
                "active": False,
                "effective_to": request.effective_to,
            }
        )

        def commit(change_id: int, principal: UUID, at: datetime) -> None:
            history.append(
                _StoredRow(
                    value=closed,
                    version=current.version + 1,
                    policy_change_id=change_id,
                    recorded_by=principal,
                    recorded_at=at,
                )
            )

        return _Staged(
            record_id=request.wall_id,
            record_version=current.version + 1,
            commit=commit,
        )

    def _stage_membership(
        self,
        state: _MatterPolicyState,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
    ) -> _Staged:
        request = cast(SetWallMembership, command)
        history = self._wall_history(state, request.wall_id)
        wall = cast(EthicalWall, _current_row(history).value)
        if request.subject_principal_id == attribution.principal_id:
            raise _StateDenial(GovernanceReason.SEPARATION_OF_DUTIES)
        if request.membership_id in state.memberships:
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        if wall.effective_to is not None and (
            request.effective_from >= wall.effective_to
        ):
            raise _StateDenial(GovernanceReason.STALE_AUTHORITY)
        membership = WallMembership(
            wall_id=request.wall_id,
            tenant_id=request.tenant_id,
            matter_id=request.matter_id,
            principal_id=request.subject_principal_id,
            disposition=request.disposition,
            effective_from=request.effective_from,
            effective_to=request.effective_to,
        )

        def commit(change_id: int, principal: UUID, at: datetime) -> None:
            state.memberships[request.membership_id] = _StoredRow(
                value=membership,
                version=1,
                policy_change_id=change_id,
                recorded_by=principal,
                recorded_at=at,
            )

        return _Staged(record_id=request.membership_id, record_version=1, commit=commit)

    def _stage_grant(
        self,
        state: _MatterPolicyState,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
    ) -> _Staged:
        request = cast(GrantProtectedAccess, command)
        if request.grantee_principal_id == attribution.principal_id:
            raise _StateDenial(GovernanceReason.SEPARATION_OF_DUTIES)
        if request.grant_id in state.grants:
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        grant = ProtectedAccessGrant(
            grant_id=request.grant_id,
            tenant_id=request.tenant_id,
            matter_id=request.matter_id,
            principal_id=request.grantee_principal_id,
            access_level=request.access_level,
            purpose=request.purpose,
            granted_by_principal_id=attribution.principal_id,
            effective_from=request.effective_from,
            effective_to=request.effective_to,
        )

        def commit(change_id: int, principal: UUID, at: datetime) -> None:
            state.grants[request.grant_id] = [
                _StoredRow(
                    value=grant,
                    version=1,
                    policy_change_id=change_id,
                    recorded_by=principal,
                    recorded_at=at,
                )
            ]

        return _Staged(record_id=request.grant_id, record_version=1, commit=commit)

    def _stage_revoke_grant(
        self,
        state: _MatterPolicyState,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
    ) -> _Staged:
        request = cast(RevokeProtectedAccess, command)
        history = state.grants.get(request.grant_id)
        if history is None:
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        current = _current_row(history)
        grant = cast(ProtectedAccessGrant, current.value)
        if current.version != request.expected_version:
            raise _StateDenial(GovernanceReason.VERSION_CONFLICT)
        if grant.effective_to is not None and (
            grant.effective_to <= request.revoked_at
        ):
            raise _StateDenial(GovernanceReason.STALE_AUTHORITY)
        if (
            request.revoked_at < grant.effective_from
            or request.revoked_at > attribution.occurred_at
        ):
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        revoked = ProtectedAccessGrant.model_validate(
            {**grant.model_dump(mode="python"), "effective_to": request.revoked_at}
        )

        def commit(change_id: int, principal: UUID, at: datetime) -> None:
            history.append(
                _StoredRow(
                    value=revoked,
                    version=current.version + 1,
                    policy_change_id=change_id,
                    recorded_by=principal,
                    recorded_at=at,
                )
            )

        return _Staged(
            record_id=request.grant_id,
            record_version=current.version + 1,
            commit=commit,
        )

    def _stage_label(
        self,
        state: _MatterPolicyState,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
    ) -> _Staged:
        request = cast(SetProtectionLabel, command)
        registry = (
            state.privilege_labels
            if request.label_family == "privilege"
            else state.work_product_labels
        )
        if request.label_id in registry:
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        if request.label_family == "privilege":
            value: PolicyValue = PrivilegeLabel(
                label_id=request.label_id,
                tenant_id=request.tenant_id,
                matter_id=request.matter_id,
                material_id=request.material_id,
                label=cast(PrivilegeLabelCode, request.label_code),
                active=request.active,
                labeled_by_principal_id=attribution.principal_id,
                labeled_at=attribution.occurred_at,
            )
        else:
            value = WorkProductLabel(
                label_id=request.label_id,
                tenant_id=request.tenant_id,
                matter_id=request.matter_id,
                material_id=request.material_id,
                label=cast(WorkProductLabelCode, request.label_code),
                active=request.active,
                labeled_by_principal_id=attribution.principal_id,
                labeled_at=attribution.occurred_at,
            )

        def commit(change_id: int, principal: UUID, at: datetime) -> None:
            registry[request.label_id] = _StoredRow(
                value=value,
                version=1,
                policy_change_id=change_id,
                recorded_by=principal,
                recorded_at=at,
            )

        return _Staged(record_id=request.label_id, record_version=1, commit=commit)

    def _retention_head(self, state: _MatterPolicyState) -> _StoredRow | None:
        superseded = set(state.retention_links.values())
        heads = [
            row
            for row in state.retention
            if cast(RetentionPolicy, row.value).retention_policy_id not in superseded
        ]
        return heads[-1] if heads else None

    def _stage_retention(
        self,
        state: _MatterPolicyState,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
    ) -> _Staged:
        request = cast(SetRetentionPolicy, command)
        if any(
            cast(RetentionPolicy, row.value).retention_policy_id
            == request.retention_policy_id
            for row in state.retention
        ):
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        if request.effective_from > attribution.occurred_at:
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        head = self._retention_head(state)
        sealed: RetentionPolicy | None = None
        if request.supersedes_retention_policy_id is None:
            if head is not None:
                raise _StateDenial(GovernanceReason.STALE_AUTHORITY)
        else:
            if head is None or (
                cast(RetentionPolicy, head.value).retention_policy_id
                != request.supersedes_retention_policy_id
            ):
                raise _StateDenial(GovernanceReason.STALE_AUTHORITY)
            prior = cast(RetentionPolicy, head.value)
            if request.effective_from <= prior.effective_from:
                raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
            sealed = RetentionPolicy.model_validate(
                {
                    **prior.model_dump(mode="python"),
                    "effective_to": request.effective_from,
                }
            )
        policy = RetentionPolicy(
            retention_policy_id=request.retention_policy_id,
            tenant_id=request.tenant_id,
            matter_id=request.matter_id,
            retain_for_days=request.retain_for_days,
            effective_from=request.effective_from,
        )
        head_row = head
        sealed_row = sealed

        def commit(change_id: int, principal: UUID, at: datetime) -> None:
            if head_row is not None and sealed_row is not None:
                state.retention.append(
                    _StoredRow(
                        value=sealed_row,
                        version=head_row.version + 1,
                        policy_change_id=change_id,
                        recorded_by=principal,
                        recorded_at=at,
                    )
                )
            state.retention.append(
                _StoredRow(
                    value=policy,
                    version=1,
                    policy_change_id=change_id,
                    recorded_by=principal,
                    recorded_at=at,
                )
            )
            if request.supersedes_retention_policy_id is not None:
                state.retention_links[request.retention_policy_id] = (
                    request.supersedes_retention_policy_id
                )

        return _Staged(
            record_id=request.retention_policy_id, record_version=1, commit=commit
        )

    def _stage_issue_hold(
        self,
        state: _MatterPolicyState,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
    ) -> _Staged:
        request = cast(IssueLegalHold, command)
        if request.legal_hold_id in state.legal_holds:
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        if request.effective_from > attribution.occurred_at:
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        hold = LegalHold(
            legal_hold_id=request.legal_hold_id,
            tenant_id=request.tenant_id,
            matter_id=request.matter_id,
            status=LegalHoldStatus.ACTIVE,
            scope=request.scope,
            material_id=request.material_id,
            issued_by_principal_id=attribution.principal_id,
            effective_from=request.effective_from,
        )

        def commit(change_id: int, principal: UUID, at: datetime) -> None:
            state.legal_holds[request.legal_hold_id] = _StoredRow(
                value=hold,
                version=1,
                policy_change_id=change_id,
                recorded_by=principal,
                recorded_at=at,
            )

        return _Staged(record_id=request.legal_hold_id, record_version=1, commit=commit)

    def _stage_release_hold(
        self,
        state: _MatterPolicyState,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
    ) -> _Staged:
        request = cast(ReleaseLegalHold, command)
        if request.release_id in state.legal_holds:
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        target_row = state.legal_holds.get(request.legal_hold_id)
        if target_row is None:
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        target = cast(LegalHold, target_row.value)
        if target.status != LegalHoldStatus.ACTIVE or (
            request.legal_hold_id in state.hold_releases.values()
        ):
            raise _StateDenial(GovernanceReason.STALE_AUTHORITY)
        if target.issued_by_principal_id == attribution.principal_id:
            raise _StateDenial(GovernanceReason.SEPARATION_OF_DUTIES)
        if (
            request.released_at < target.effective_from
            or request.released_at > attribution.occurred_at
        ):
            raise _StateDenial(GovernanceReason.EVIDENCE_INVALID)
        release = LegalHold(
            legal_hold_id=request.release_id,
            tenant_id=request.tenant_id,
            matter_id=request.matter_id,
            status=LegalHoldStatus.RELEASED,
            scope=target.scope,
            material_id=target.material_id,
            issued_by_principal_id=target.issued_by_principal_id,
            effective_from=target.effective_from,
            supersedes_hold_id=target.legal_hold_id,
            released_by_principal_id=attribution.principal_id,
            released_at=request.released_at,
        )

        def commit(change_id: int, principal: UUID, at: datetime) -> None:
            state.legal_holds[request.release_id] = _StoredRow(
                value=release,
                version=1,
                policy_change_id=change_id,
                recorded_by=principal,
                recorded_at=at,
            )
            state.hold_releases[request.release_id] = request.legal_hold_id

        return _Staged(record_id=request.release_id, record_version=1, commit=commit)

    def current_grant(
        self, tenant_id: UUID, matter_id: UUID, grant_id: UUID
    ) -> ProtectedAccessGrant | None:
        with self._lock:
            state = self._states.get((tenant_id, matter_id))
            if state is None:
                return None
            history = state.grants.get(grant_id)
            if history is None:
                return None
            return cast(ProtectedAccessGrant, _current_row(history).value)

    def decision_head(
        self, tenant_id: UUID, matter_id: UUID
    ) -> ConflictDecision | None:
        with self._lock:
            state = self._states.get((tenant_id, matter_id))
            if state is None:
                return None
            head = self._decision_head(state)
            return None if head is None else cast(ConflictDecision, head.value)

    def retention_head(self, tenant_id: UUID, matter_id: UUID) -> PolicyValue | None:
        with self._lock:
            state = self._states.get((tenant_id, matter_id))
            if state is None:
                return None
            head = self._retention_head(state)
            return None if head is None else head.value

    def legal_hold(
        self, tenant_id: UUID, matter_id: UUID, hold_id: UUID
    ) -> LegalHold | None:
        with self._lock:
            state = self._states.get((tenant_id, matter_id))
            if state is None:
                return None
            row = state.legal_holds.get(hold_id)
            return None if row is None else cast(LegalHold, row.value)

    def current_wall(
        self, tenant_id: UUID, matter_id: UUID, wall_id: UUID
    ) -> EthicalWall | None:
        with self._lock:
            state = self._states.get((tenant_id, matter_id))
            if state is None:
                return None
            history = state.walls.get(wall_id)
            if history is None:
                return None
            return cast(EthicalWall, _current_row(history).value)


_CONFLICT_REQUIREMENT = CapabilityRequirement(
    audience=Audience.API,
    target="api:policy.conflict.decide",
    capability=Capability.MATTER_CONFLICT_REVIEW,
    purpose=Purpose.CONFLICT_REVIEW,
)
_WAIVER_REQUIREMENT = CapabilityRequirement(
    audience=Audience.API,
    target="api:policy.waiver.register",
    capability=Capability.MATTER_CONFLICT_REVIEW,
    purpose=Purpose.CONFLICT_REVIEW,
)
_WALL_REQUIREMENT = CapabilityRequirement(
    audience=Audience.API,
    target="api:policy.wall.manage",
    capability=Capability.MATTER_WALL_MANAGE,
    purpose=Purpose.INFORMATION_BARRIER_ADMIN,
)
_MATTER_REQUIREMENT = CapabilityRequirement(
    audience=Audience.API,
    target="api:policy.matter.manage",
    capability=Capability.MATTER_MANAGE,
    purpose=Purpose.MATTER_MANAGEMENT,
)

GOVERNANCE_REQUIREMENTS: dict[GovernanceAction, CapabilityRequirement] = {
    GovernanceAction.REGISTER_WAIVER: _WAIVER_REQUIREMENT,
    GovernanceAction.DECIDE_CONFLICT: _CONFLICT_REQUIREMENT,
    GovernanceAction.OPEN_ETHICAL_WALL: _WALL_REQUIREMENT,
    GovernanceAction.SEAL_ETHICAL_WALL_ROSTER: _WALL_REQUIREMENT,
    GovernanceAction.CLOSE_ETHICAL_WALL: _WALL_REQUIREMENT,
    GovernanceAction.SET_WALL_MEMBERSHIP: _WALL_REQUIREMENT,
    GovernanceAction.GRANT_PROTECTED_ACCESS: _WALL_REQUIREMENT,
    GovernanceAction.REVOKE_PROTECTED_ACCESS: _WALL_REQUIREMENT,
    GovernanceAction.SET_PROTECTION_LABEL: _MATTER_REQUIREMENT,
    GovernanceAction.SET_RETENTION_POLICY: _MATTER_REQUIREMENT,
    GovernanceAction.ISSUE_LEGAL_HOLD: _MATTER_REQUIREMENT,
    GovernanceAction.RELEASE_LEGAL_HOLD: _MATTER_REQUIREMENT,
}

_CURRENT_AUTHORIZATION_REASONS: dict[type[Exception], GovernanceReason] = {
    CurrentAuthorizationExpired: GovernanceReason.CAPAUTH_EXPIRED,
    CurrentAuthorizationReplayed: GovernanceReason.CAPAUTH_REPLAYED,
    CurrentAuthorizationStale: GovernanceReason.CAPAUTH_STALE,
    CurrentAuthorizationUnavailable: (
        GovernanceReason.CAPAUTH_CURRENT_STATE_UNAVAILABLE
    ),
}


def _invocation_digest(
    authorized: AuthorizedContext, command: GovernanceCommand
) -> Sha256:
    encoded = _canonical_json_bytes(
        {
            "schema": "sklegal-governance-invocation/v1",
            "capauth_decision_id": str(authorized.decision.decision_id),
            "command_digest": canonical_command_digest(command),
        }
    )
    return hashlib.sha256(encoded).hexdigest()


class PolicyGovernanceService:
    """Authorize and commit human policy decisions through current CapAuth."""

    def __init__(
        self,
        *,
        store: PolicyGovernanceStore,
        current_authorization: CapAuthCurrentStateVerifier,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._current_authorization = current_authorization
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def requirement_for(action: GovernanceAction) -> CapabilityRequirement:
        return GOVERNANCE_REQUIREMENTS[action]

    def _now(self) -> datetime:
        failed = False
        value: datetime | None = None
        try:
            value = require_utc(self._clock())
        except Exception:
            failed = True
        if failed or value is None:
            raise PolicyGovernanceUnavailable("governance clock unavailable") from None
        return value

    def _attribution(
        self,
        authorized: AuthorizedContext,
        requirement: CapabilityRequirement,
        occurred_at: datetime,
    ) -> GovernanceAttribution:
        return GovernanceAttribution(
            principal_id=authorized.principal.principal_id,
            principal_type=authorized.principal.principal_type,
            capauth_decision_id=authorized.decision.decision_id,
            correlation_id=authorized.decision.correlation_id,
            run_id=uuid4(),
            capability=requirement.capability,
            purpose=requirement.purpose,
            target=requirement.target,
            occurred_at=occurred_at,
        )

    def _deny(
        self,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
        reason: GovernanceReason,
    ) -> None:
        decision = self._store.record_denial(command, attribution, reason=reason)
        raise PolicyGovernanceDenied(decision) from None

    def _deny_current_authorization(
        self,
        command: GovernanceCommand,
        attribution: GovernanceAttribution,
        error: Exception,
    ) -> None:
        reason = _CURRENT_AUTHORIZATION_REASONS.get(
            type(error), GovernanceReason.CAPAUTH_CURRENT_STATE_UNAVAILABLE
        )
        self._deny(command, attribution, reason)

    def execute(
        self,
        authorized: AuthorizedContext,
        command: GovernanceCommand,
    ) -> PolicyMutationReceipt:
        requirement = GOVERNANCE_REQUIREMENTS[command.action]
        attribution = self._attribution(authorized, requirement, self._now())
        if authorized.principal.principal_type != PrincipalType.HUMAN:
            self._deny(command, attribution, GovernanceReason.HUMAN_DECISION_REQUIRED)
        expected_grant = requirement.bind(
            BoundaryScope(
                tenant_id=command.tenant_id,
                matter_id=command.matter_id,
                resource_id=str(command.matter_id),
            )
        )
        if (
            authorized.principal.tenant_id != command.tenant_id
            or authorized.grant != expected_grant
        ):
            self._deny(command, attribution, GovernanceReason.CAPAUTH_SCOPE_MISMATCH)
        try:
            self._current_authorization.reserve(
                authorized,
                invocation_digest=_invocation_digest(authorized, command),
                evaluated_at=attribution.occurred_at,
            )
        except (
            CurrentAuthorizationExpired,
            CurrentAuthorizationReplayed,
            CurrentAuthorizationStale,
            CurrentAuthorizationUnavailable,
        ) as exc:
            self._deny_current_authorization(command, attribution, exc)
        except Exception:
            raise PolicyGovernanceUnavailable(
                "current CapAuth evidence unavailable"
            ) from None
        effect_attribution = self._attribution(authorized, requirement, self._now())
        if effect_attribution.occurred_at < attribution.occurred_at:
            raise PolicyGovernanceUnavailable("trusted clock moved backwards")
        try:
            self._current_authorization.verify_current(
                authorized,
                evaluated_at=effect_attribution.occurred_at,
            )
        except (
            CurrentAuthorizationExpired,
            CurrentAuthorizationReplayed,
            CurrentAuthorizationStale,
            CurrentAuthorizationUnavailable,
        ) as exc:
            self._deny_current_authorization(command, effect_attribution, exc)
        except Exception:
            raise PolicyGovernanceUnavailable(
                "current CapAuth evidence unavailable"
            ) from None
        try:
            return self._store.apply(command, effect_attribution)
        except (PolicyGovernanceDenied, PolicyGovernanceUnavailable):
            raise
        except Exception:
            raise PolicyGovernanceUnavailable("governance store unavailable") from None
