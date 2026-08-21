"""Driver-neutral durable CapAuth adapter tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sklegal_capauth import (
    BackendUnavailable,
    PostgresPrincipalPolicyBackend,
    PostgresReplayBackend,
    PostgresRevocationBackend,
    PrincipalContext,
    PrincipalType,
)

TENANT = uuid4()
PRINCIPAL = PrincipalContext(
    principal_id=uuid4(),
    principal_type=PrincipalType.HUMAN,
    subject="synthetic:human:postgres",
    tenant_id=TENANT,
)


def test_principal_snapshot_uses_scoped_executor_and_normalizes_json() -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(sql: str, params: tuple[object, ...]) -> object:
        calls.append((sql, params))
        return (
            {
                "revision": "a" * 64,
                "principal": {
                    "principal_id": str(PRINCIPAL.principal_id),
                    "principal_type": "human",
                    "subject": PRINCIPAL.subject,
                    "tenant_id": str(TENANT),
                },
                "active": True,
            },
        )

    snapshot = PostgresPrincipalPolicyBackend(execute).snapshot(PRINCIPAL)
    assert snapshot.active is True
    assert snapshot.principal == PRINCIPAL
    assert calls and calls[0][1] == (TENANT, PRINCIPAL.principal_id)


def test_revocation_snapshot_is_strict_and_fail_closed() -> None:
    digest = "b" * 64

    def execute(_sql: str, _params: tuple[object, ...]) -> object:
        return (
            '{{"revision":"{revision}","revoked_credential_digests":["{digest}"]}}'
        ).format(revision="c" * 64, digest=digest)

    snapshot = PostgresRevocationBackend(execute, tenant_id=TENANT).snapshot((digest,))
    assert snapshot.revoked_credential_digests == frozenset({digest})

    with pytest.raises(BackendUnavailable):
        PostgresRevocationBackend(lambda *_: object(), tenant_id=TENANT).snapshot(
            (digest,)
        )


def test_replay_reservation_accepts_only_boolean_and_binds_tenant() -> None:
    calls: list[tuple[object, ...]] = []

    def execute(_sql: str, params: tuple[object, ...]) -> object:
        calls.append(params)
        return (True,)

    expires = datetime.now(UTC) + timedelta(minutes=5)
    reserved = PostgresReplayBackend(execute, tenant_id=TENANT).reserve(
        credential_digest="d" * 64,
        decision_id=str(uuid4()),
        expires_at=expires,
    )
    assert reserved is True
    assert calls and calls[0][0] == TENANT

    with pytest.raises(BackendUnavailable):
        PostgresReplayBackend(
            lambda *_: {"unexpected": True}, tenant_id=TENANT
        ).reserve(
            credential_digest="d" * 64,
            decision_id=str(uuid4()),
            expires_at=expires,
        )
