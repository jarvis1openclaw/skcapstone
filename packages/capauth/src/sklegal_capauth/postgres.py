"""Driver-neutral durable CapAuth backend adapters."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import cast
from uuid import UUID

from .backends import (
    BackendUnavailable,
    PrincipalPolicyBackend,
    PrincipalPolicySnapshot,
    ReplayBackend,
    RevocationBackend,
    RevocationSnapshot,
)
from .models import PrincipalContext

type SqlExecutor = Callable[[str, tuple[object, ...]], object]

PRINCIPAL_SNAPSHOT_SQL = """
SELECT sklegal_identity.capability_principal_snapshot(%s, %s)
""".strip()
REVOCATION_SNAPSHOT_SQL = """
SELECT sklegal_identity.capability_revocation_snapshot(%s, %s)
""".strip()
REPLAY_RESERVE_SQL = """
SELECT sklegal_identity.reserve_capability(%s, %s, %s, %s)
""".strip()
REPLAY_PRUNE_SQL = """
SELECT sklegal_identity.prune_expired_capability_replay_reservations(%s)
""".strip()


def _payload(value: object) -> Mapping[str, object]:
    if isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise ValueError("database adapter returned a non-object payload")
    return value


def _model_json(model_type: type[object], payload: Mapping[str, object]) -> object:
    return model_type.model_validate_json(  # type: ignore[attr-defined]
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    )


class PostgresPrincipalPolicyBackend(PrincipalPolicyBackend):
    """Read current principal state through the scoped SECURITY DEFINER function."""

    def __init__(self, executor: SqlExecutor) -> None:
        self._executor = executor

    def snapshot(self, principal: PrincipalContext) -> PrincipalPolicySnapshot:
        try:
            payload = _payload(
                self._executor(
                    PRINCIPAL_SNAPSHOT_SQL,
                    (principal.tenant_id, principal.principal_id),
                )
            )
            return cast(
                PrincipalPolicySnapshot,
                _model_json(PrincipalPolicySnapshot, payload),
            )
        except Exception:
            raise BackendUnavailable("principal policy backend unavailable") from None


class PostgresRevocationBackend(RevocationBackend):
    """Read current leaf and ancestor revocations through the scoped function."""

    def __init__(self, executor: SqlExecutor, *, tenant_id: UUID) -> None:
        self._executor = executor
        self._tenant_id = tenant_id

    def snapshot(self, credential_digests: tuple[str, ...]) -> RevocationSnapshot:
        try:
            payload = _payload(
                self._executor(
                    REVOCATION_SNAPSHOT_SQL,
                    (self._tenant_id, list(credential_digests)),
                )
            )
            normalized = dict(payload)
            raw_revoked = normalized.get("revoked_credential_digests", ())
            if not isinstance(raw_revoked, (list, tuple, set, frozenset)):
                raise ValueError("database revocation payload is malformed")
            normalized["revoked_credential_digests"] = frozenset(
                str(item) for item in raw_revoked
            )
            return RevocationSnapshot.model_validate(normalized)
        except Exception:
            raise BackendUnavailable("revocation backend unavailable") from None


class PostgresReplayBackend(ReplayBackend):
    """Reserve one credential use atomically through the database function."""

    def __init__(self, executor: SqlExecutor, *, tenant_id: UUID) -> None:
        self._executor = executor
        self._tenant_id = tenant_id

    def reserve(
        self,
        *,
        credential_digest: str,
        decision_id: str,
        expires_at: datetime,
    ) -> bool:
        try:
            value = self._executor(
                REPLAY_RESERVE_SQL,
                (self._tenant_id, credential_digest, UUID(decision_id), expires_at),
            )
            if isinstance(value, Mapping):
                value = value.get("reserve_capability")
            if isinstance(value, (list, tuple)) and len(value) == 1:
                value = value[0]
            if not isinstance(value, bool):
                raise ValueError("database replay adapter returned invalid state")
            return value
        except BackendUnavailable:
            raise
        except Exception:
            raise BackendUnavailable("replay backend unavailable") from None

    def prune_expired(self) -> int:
        """Delete expired reservations for the bound tenant and return the count."""
        try:
            value = self._executor(REPLAY_PRUNE_SQL, (self._tenant_id,))
            if isinstance(value, Mapping):
                value = value.get("prune_expired_capability_replay_reservations")
            if isinstance(value, (list, tuple)) and len(value) == 1:
                value = value[0]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("database replay prune returned invalid state")
            return value
        except BackendUnavailable:
            raise
        except Exception:
            raise BackendUnavailable("replay backend unavailable") from None
