"""Backend contracts for trusted issuer, revocation, replay, cache, and audit."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Protocol, Self
from uuid import UUID

from pydantic import Field, model_validator

from .models import (
    VERIFIER_POLICY_VERSION,
    Audience,
    AuthorizationDecision,
    Capability,
    Fingerprint,
    PrincipalContext,
    PrincipalType,
    Sha256,
    StrictValue,
)


class BackendUnavailable(RuntimeError):
    """A required authorization backend has no trustworthy current answer."""


class PrincipalUnboundError(RuntimeError):
    """The authenticated principal has no current authorization binding."""


def _revision(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class TrustedIssuerGrant(StrictValue):
    fingerprint: Fingerprint
    capabilities: frozenset[Capability]
    audiences: frozenset[Audience]
    principal_types: frozenset[PrincipalType]

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        if not self.capabilities or not self.audiences or not self.principal_types:
            raise ValueError("trusted issuer authority sets cannot be empty")
        return self


class TrustedIssuerSnapshot(StrictValue):
    policy_version: str = Field(min_length=1, max_length=64)
    revision: Sha256
    issuers: tuple[TrustedIssuerGrant, ...]

    @model_validator(mode="after")
    def validate_issuers(self) -> Self:
        fingerprints = [issuer.fingerprint for issuer in self.issuers]
        if not fingerprints or len(fingerprints) != len(set(fingerprints)):
            raise ValueError("trusted issuer fingerprints must be nonempty and unique")
        return self


class PrincipalPolicySnapshot(StrictValue):
    revision: Sha256
    principal: PrincipalContext
    active: bool


class RevocationSnapshot(StrictValue):
    revision: Sha256
    revoked_credential_digests: frozenset[Sha256]


class TrustedIssuerBackend(Protocol):
    def snapshot(self) -> TrustedIssuerSnapshot:
        """Return a current, versioned issuer policy or raise."""


class RevocationBackend(Protocol):
    def snapshot(self, credential_digests: tuple[str, ...]) -> RevocationSnapshot:
        """Return current revocation state for every requested digest or raise."""


class PrincipalPolicyBackend(Protocol):
    def snapshot(self, principal: PrincipalContext) -> PrincipalPolicySnapshot:
        """Return current principal identity and status or raise when unknown."""


class ReplayBackend(Protocol):
    def reserve(
        self,
        *,
        credential_digest: str,
        decision_id: str,
        expires_at: datetime,
    ) -> bool:
        """Atomically reserve one credential use, returning false on replay."""


class AuditSink(Protocol):
    def record(self, decision: AuthorizationDecision) -> None:
        """Record a sanitized allow or deny decision or raise."""


class StaticTrustedIssuerBackend:
    """Immutable synthetic or development issuer policy.

    Production construction must use a separately protected, reloadable policy
    backend. This class remains useful for hermetic tests and isolated local
    development because every call still produces a versioned snapshot.
    """

    def __init__(
        self,
        issuer_fingerprints: set[str] | frozenset[str],
        *,
        policy_version: str = VERIFIER_POLICY_VERSION,
    ) -> None:
        normalized = frozenset(item.strip().upper() for item in issuer_fingerprints)
        grants = tuple(
            TrustedIssuerGrant(
                fingerprint=fingerprint,
                capabilities=frozenset(Capability),
                audiences=frozenset(Audience),
                principal_types=frozenset(PrincipalType),
            )
            for fingerprint in sorted(normalized)
        )
        payload = {
            "policy_version": policy_version,
            "issuers": [grant.model_dump(mode="json") for grant in grants],
        }
        self._snapshot = TrustedIssuerSnapshot(
            policy_version=policy_version,
            revision=_revision(payload),
            issuers=grants,
        )

    def snapshot(self) -> TrustedIssuerSnapshot:
        return self._snapshot


class TrustedIssuerPolicyFile(StrictValue):
    schema_version: str = Field(pattern=r"^sklegal-trusted-issuers/v1$")
    policy_version: str = Field(min_length=1, max_length=64)
    issuers: tuple[TrustedIssuerGrant, ...]

    @model_validator(mode="after")
    def validate_issuers(self) -> Self:
        fingerprints = [issuer.fingerprint for issuer in self.issuers]
        if not fingerprints or len(fingerprints) != len(set(fingerprints)):
            raise ValueError("trusted issuer fingerprints must be nonempty and unique")
        return self


class FileTrustedIssuerBackend:
    """Strict read-through trusted issuer policy with no stale fallback."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def snapshot(self) -> TrustedIssuerSnapshot:
        def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("trusted issuer policy contains duplicate keys")
                result[key] = value
            return result

        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(self._path, flags)
            try:
                metadata = os.fstat(fd)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise BackendUnavailable("trusted issuer policy is unavailable")
                with os.fdopen(fd, "rb", closefd=True) as stream:
                    fd = -1
                    raw = stream.read()
            finally:
                if fd >= 0:
                    os.close(fd)
            parsed = json.loads(
                raw.decode("utf-8"), object_pairs_hook=reject_duplicate_keys
            )
            policy = TrustedIssuerPolicyFile.model_validate_json(
                json.dumps(parsed, separators=(",", ":"), ensure_ascii=True)
            )
        except BackendUnavailable:
            raise
        except Exception as exc:
            raise BackendUnavailable("trusted issuer policy is unusable") from exc
        return TrustedIssuerSnapshot(
            policy_version=policy.policy_version,
            revision=hashlib.sha256(raw).hexdigest(),
            issuers=policy.issuers,
        )


class InMemoryPrincipalPolicyBackend:
    """Synthetic process-local principal policy for tests and development."""

    def __init__(self, principals: tuple[PrincipalContext, ...] = ()) -> None:
        self._lock = Lock()
        self._principals = {
            principal.principal_id: (principal, True) for principal in principals
        }
        self._counter = 0

    def set(self, principal: PrincipalContext, *, active: bool = True) -> None:
        with self._lock:
            self._principals[principal.principal_id] = (principal, active)
            self._counter += 1

    def remove(self, principal_id: UUID) -> None:
        with self._lock:
            self._principals.pop(principal_id, None)
            self._counter += 1

    def snapshot(self, principal: PrincipalContext) -> PrincipalPolicySnapshot:
        with self._lock:
            record = self._principals.get(principal.principal_id)
            if record is None:
                raise PrincipalUnboundError("principal is not bound")
            current, active = record
            revision = _revision(
                {
                    "counter": self._counter,
                    "principal": current.model_dump(mode="json"),
                    "active": active,
                }
            )
        return PrincipalPolicySnapshot(
            revision=revision,
            principal=current,
            active=active,
        )


class InMemoryRevocationBackend:
    """Atomic process-local revocation backend for synthetic tests and development."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._revoked: set[str] = set()
        self._counter = 0

    def revoke(self, credential_digest: str) -> None:
        with self._lock:
            self._revoked.add(credential_digest)
            self._counter += 1

    def snapshot(self, credential_digests: tuple[str, ...]) -> RevocationSnapshot:
        with self._lock:
            requested = set(credential_digests)
            revoked = frozenset(requested.intersection(self._revoked))
            payload = {
                "counter": self._counter,
                "revoked": sorted(self._revoked),
            }
            revision = _revision(payload)
        return RevocationSnapshot(
            revision=revision,
            revoked_credential_digests=revoked,
        )


class InMemoryReplayBackend:
    """Atomic process-local replay backend for synthetic tests and development.

    It is not safe across processes or hosts and is never a production default.
    """

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = Lock()
        self._reservations: dict[str, datetime] = {}

    def reserve(
        self,
        *,
        credential_digest: str,
        decision_id: str,
        expires_at: datetime,
    ) -> bool:
        del decision_id
        with self._lock:
            now = self._clock()
            self._reservations = {
                digest: expiry
                for digest, expiry in self._reservations.items()
                if expiry >= now
            }
            if credential_digest in self._reservations:
                return False
            self._reservations[credential_digest] = expires_at
            return True


class InMemoryAuditSink:
    """Synthetic process-local sink that retains only sanitized decisions."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._decisions: list[AuthorizationDecision] = []

    def record(self, decision: AuthorizationDecision) -> None:
        with self._lock:
            self._decisions.append(decision)

    def decisions(self) -> tuple[AuthorizationDecision, ...]:
        with self._lock:
            return tuple(self._decisions)


class UnavailableTrustedIssuerBackend:
    def snapshot(self) -> TrustedIssuerSnapshot:
        raise BackendUnavailable("trusted issuer backend unavailable")


class UnavailableRevocationBackend:
    def snapshot(self, credential_digests: tuple[str, ...]) -> RevocationSnapshot:
        del credential_digests
        raise BackendUnavailable("revocation backend unavailable")


class UnavailablePrincipalPolicyBackend:
    def snapshot(self, principal: PrincipalContext) -> PrincipalPolicySnapshot:
        del principal
        raise BackendUnavailable("principal policy backend unavailable")


class UnavailableReplayBackend:
    def reserve(
        self,
        *,
        credential_digest: str,
        decision_id: str,
        expires_at: datetime,
    ) -> bool:
        del credential_digest, decision_id, expires_at
        raise BackendUnavailable("replay backend unavailable")


class UnavailableAuditSink:
    def record(self, decision: AuthorizationDecision) -> None:
        del decision
        raise BackendUnavailable("audit backend unavailable")


class SignatureVerificationCache:
    """Bounded positive-only cache for the cryptographic signature fact."""

    def __init__(
        self,
        *,
        max_entries: int = 2048,
        ttl_seconds: int = 5,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_entries < 1 or not 1 <= ttl_seconds <= 30:
            raise ValueError("invalid signature cache bounds")
        self._max_entries = max_entries
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = Lock()
        self._entries: OrderedDict[tuple[str, ...], datetime] = OrderedDict()

    def contains(self, key: tuple[str, ...]) -> bool:
        now = self._clock()
        with self._lock:
            expiry = self._entries.get(key)
            if expiry is None:
                return False
            if expiry < now:
                del self._entries[key]
                return False
            self._entries.move_to_end(key)
            return True

    def add(self, key: tuple[str, ...], *, credential_expires_at: datetime) -> None:
        expires_at = min(self._clock() + self._ttl, credential_expires_at)
        with self._lock:
            self._entries[key] = expires_at
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
