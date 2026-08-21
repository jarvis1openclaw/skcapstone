"""Policy state and sanitized audit ports with test-only memory adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from threading import Lock
from typing import Protocol, cast
from uuid import UUID

from sklegal_capauth import (
    VERIFIER_POLICY_VERSION,
    AuthorizedContext,
    PrincipalPolicyBackend,
    PrincipalPolicySnapshot,
    RevocationBackend,
    RevocationSnapshot,
    TrustedIssuerBackend,
    TrustedIssuerSnapshot,
)

from .models import (
    MaterialPolicyFacts,
    PolicyAccessRequest,
    PolicyDecision,
    RetentionDecision,
    RetentionRequest,
    Sha256,
    require_utc,
)


class PolicyBackendUnavailable(RuntimeError):
    """No complete current policy answer is available."""


class PolicyAuditUnavailable(RuntimeError):
    """A sanitized policy decision could not be durably accepted."""


class CurrentAuthorizationExpired(PermissionError):
    """The request-local CapAuth authorization is no longer current."""


class CurrentAuthorizationReplayed(PermissionError):
    """The CapAuth decision was already bound to one policy invocation."""


class CurrentAuthorizationStale(PermissionError):
    """Current issuer, principal, or revocation evidence changed."""


class CurrentAuthorizationUnavailable(RuntimeError):
    """A current CapAuth dependency cannot provide an authoritative answer."""


def _load_current_snapshot[SnapshotT](loader: Callable[[], SnapshotT]) -> SnapshotT:
    failed = False
    value: SnapshotT | None = None
    try:
        value = loader()
    except Exception:
        failed = True
    if failed:
        raise CurrentAuthorizationUnavailable(
            "current CapAuth evidence unavailable"
        ) from None
    return cast(SnapshotT, value)


class PolicyBackend(Protocol):
    def load(
        self, request: PolicyAccessRequest | RetentionRequest
    ) -> MaterialPolicyFacts: ...


class PolicyAuditSink(Protocol):
    def record(self, decision: PolicyDecision | RetentionDecision) -> None: ...


class AuthorizationUseBackend(Protocol):
    def reserve(
        self,
        *,
        capauth_decision_id: UUID,
        invocation_digest: Sha256,
        expires_at: datetime,
        evaluated_at: datetime,
    ) -> bool: ...


class InMemoryAuthorizationUseBackend:
    """Synthetic one-use registry, never a production multi-worker backend."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._uses: dict[UUID, tuple[Sha256, datetime]] = {}

    def reserve(
        self,
        *,
        capauth_decision_id: UUID,
        invocation_digest: Sha256,
        expires_at: datetime,
        evaluated_at: datetime,
    ) -> bool:
        checked = require_utc(evaluated_at)
        expiry = require_utc(expires_at)
        with self._lock:
            self._uses = {
                decision_id: value
                for decision_id, value in self._uses.items()
                if value[1] > checked
            }
            if capauth_decision_id in self._uses:
                return False
            self._uses[capauth_decision_id] = (invocation_digest, expiry)
            return True


class UnavailableAuthorizationUseBackend:
    def reserve(
        self,
        *,
        capauth_decision_id: UUID,
        invocation_digest: Sha256,
        expires_at: datetime,
        evaluated_at: datetime,
    ) -> bool:
        del capauth_decision_id, invocation_digest, expires_at, evaluated_at
        raise CurrentAuthorizationUnavailable("authorization-use backend unavailable")


class CapAuthCurrentStateVerifier:
    """Recheck safe CapAuth evidence and reserve one exact policy invocation."""

    def __init__(
        self,
        *,
        trusted_issuers: TrustedIssuerBackend,
        principals: PrincipalPolicyBackend,
        revocations: RevocationBackend,
        uses: AuthorizationUseBackend,
    ) -> None:
        self._trusted_issuers = trusted_issuers
        self._principals = principals
        self._revocations = revocations
        self._uses = uses

    def reserve(
        self,
        authorized: AuthorizedContext,
        *,
        invocation_digest: Sha256,
        evaluated_at: datetime,
    ) -> None:
        self.verify_current(authorized, evaluated_at=evaluated_at)
        failed = False
        reserved = False
        try:
            reserved = self._uses.reserve(
                capauth_decision_id=authorized.decision.decision_id,
                invocation_digest=invocation_digest,
                expires_at=authorized.credential_expires_at,
                evaluated_at=evaluated_at,
            )
        except Exception:
            failed = True
        if failed:
            raise CurrentAuthorizationUnavailable(
                "authorization-use backend unavailable"
            ) from None
        if not isinstance(reserved, bool):
            raise CurrentAuthorizationUnavailable(
                "authorization-use backend returned invalid state"
            )
        if not reserved:
            raise CurrentAuthorizationReplayed("CapAuth decision already consumed")

    def verify_current(
        self,
        authorized: AuthorizedContext,
        *,
        evaluated_at: datetime,
    ) -> None:
        checked = require_utc(evaluated_at)
        if checked >= authorized.credential_expires_at:
            raise CurrentAuthorizationExpired("CapAuth decision expired")
        decision = authorized.decision
        if (
            decision.trusted_issuer_policy_revision is None
            or decision.revocation_revision is None
        ):
            raise CurrentAuthorizationStale("CapAuth current evidence is incomplete")
        issuer = _load_current_snapshot(self._trusted_issuers.snapshot)
        if not isinstance(issuer, TrustedIssuerSnapshot):
            raise CurrentAuthorizationUnavailable(
                "current CapAuth evidence unavailable"
            )
        if (
            issuer.policy_version != VERIFIER_POLICY_VERSION
            or issuer.revision != decision.trusted_issuer_policy_revision
        ):
            raise CurrentAuthorizationStale("trusted issuer policy changed")

        references = {
            item.principal_id: item.revision
            for item in decision.principal_policy_revisions
        }
        if len(references) != len(authorized.principal_chain):
            raise CurrentAuthorizationStale("principal evidence changed")
        for principal in authorized.principal_chain:
            current = _load_current_snapshot(
                lambda: self._principals.snapshot(principal)
            )
            if not isinstance(current, PrincipalPolicySnapshot):
                raise CurrentAuthorizationUnavailable(
                    "current CapAuth evidence unavailable"
                )
            if (
                not current.active
                or current.principal != principal
                or current.revision != references.get(principal.principal_id)
            ):
                raise CurrentAuthorizationStale("principal evidence changed")

        credential_digests = (
            decision.credential_digest,
            *decision.ancestor_credential_digests,
        )
        if credential_digests[0] is None:
            raise CurrentAuthorizationStale("credential evidence is incomplete")
        checked_digests = tuple(item for item in credential_digests if item is not None)
        revocation = _load_current_snapshot(
            lambda: self._revocations.snapshot(checked_digests)
        )
        if not isinstance(revocation, RevocationSnapshot):
            raise CurrentAuthorizationUnavailable(
                "current CapAuth evidence unavailable"
            )
        if (
            revocation.revision != decision.revocation_revision
            or revocation.revoked_credential_digests
        ):
            raise CurrentAuthorizationStale("revocation policy changed")


class InMemoryPolicyBackend:
    """Synthetic test and local-development backend, never production composition."""

    def __init__(self, facts: Mapping[UUID, MaterialPolicyFacts]) -> None:
        self._facts = dict(facts)

    def load(
        self, request: PolicyAccessRequest | RetentionRequest
    ) -> MaterialPolicyFacts:
        missing = False
        facts: MaterialPolicyFacts | None = None
        try:
            facts = self._facts[request.material_id]
        except KeyError:
            missing = True
        if missing:
            raise PolicyBackendUnavailable("policy facts unavailable") from None
        return cast(MaterialPolicyFacts, facts)


class UnavailablePolicyBackend:
    def __init__(self, detail: str = "unavailable") -> None:
        del detail

    def load(
        self, request: PolicyAccessRequest | RetentionRequest
    ) -> MaterialPolicyFacts:
        del request
        raise PolicyBackendUnavailable("policy backend unavailable")


MATERIAL_POLICY_SNAPSHOT_SQL = """
SELECT sklegal_legal.material_policy_snapshot(%s, %s, %s, %s, %s)
""".strip()


type PolicySnapshotExecutor = Callable[
    [str, tuple[UUID, UUID, UUID, int, UUID]], object
]


class PostgresPolicyBackend:
    """Driver-neutral adapter for the one sanitized database snapshot function."""

    def __init__(self, executor: PolicySnapshotExecutor) -> None:
        self._executor = executor

    def load(
        self, request: PolicyAccessRequest | RetentionRequest
    ) -> MaterialPolicyFacts:
        parameters = (
            request.tenant_id,
            request.matter_id,
            request.material_id,
            request.material_version,
            request.principal_id,
        )
        failed = False
        facts: MaterialPolicyFacts | None = None
        try:
            payload = self._executor(MATERIAL_POLICY_SNAPSHOT_SQL, parameters)
            if isinstance(payload, str):
                facts = MaterialPolicyFacts.model_validate_json(payload)
            elif isinstance(payload, Mapping):
                facts = MaterialPolicyFacts.model_validate(dict(payload))
        except Exception:
            failed = True
        if failed or facts is None:
            raise PolicyBackendUnavailable("policy snapshot unavailable") from None
        return facts


class InMemoryPolicyAuditSink:
    """Synthetic sink retaining only sanitized PolicyDecision values."""

    def __init__(self) -> None:
        self._decisions: list[PolicyDecision | RetentionDecision] = []

    def record(self, decision: PolicyDecision | RetentionDecision) -> None:
        self._decisions.append(decision)

    def decisions(self) -> tuple[PolicyDecision | RetentionDecision, ...]:
        return tuple(self._decisions)


class UnavailablePolicyAuditSink:
    def record(self, decision: PolicyDecision | RetentionDecision) -> None:
        del decision
        raise PolicyAuditUnavailable("policy audit unavailable")
