"""Protected-route composition must use durable CapAuth adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sklegal_api.capauth import build_postgres_capability_authorizer
from sklegal_capauth import (
    InMemoryAuditSink,
    StaticTrustedIssuerBackend,
)


def test_postgres_composition_injects_durable_state_adapters() -> None:
    executor_calls: list[tuple[str, tuple[object, ...]]] = []
    audit = InMemoryAuditSink()
    issuer = StaticTrustedIssuerBackend({"A" * 40})
    tenant_id = uuid4()

    def executor(sql: str, params: tuple[object, ...]) -> object:
        executor_calls.append((sql, params))
        raise RuntimeError("not invoked during composition")

    authorizer = build_postgres_capability_authorizer(
        executor=executor,
        trusted_issuers=issuer,
        audit=audit,
        tenant_id=tenant_id,
        clock=lambda: datetime.now(UTC),
    )

    assert authorizer is not None
    assert executor_calls == []
