"""Protected-route composition must use durable CapAuth adapters."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from capauth.testing import STUB_ISSUER_FPR  # type: ignore[import-untyped]
from sklegal_api.capauth import build_postgres_capability_authorizer
from sklegal_capauth import (
    Audience,
    Capability,
    FileTrustedIssuerBackend,
    PrincipalType,
)


class _RecordingAuditSink:
    """Protocol-conforming sink standing in for the durable audit adapter."""

    def __init__(self) -> None:
        self.decisions: list[object] = []

    def record(self, decision: object) -> None:
        self.decisions.append(decision)


def test_postgres_composition_injects_durable_state_adapters() -> None:
    executor_calls: list[tuple[str, tuple[object, ...]]] = []
    audit = _RecordingAuditSink()
    tenant_id = uuid4()

    def executor(sql: str, params: tuple[object, ...]) -> object:
        executor_calls.append((sql, params))
        raise RuntimeError("not invoked during composition")

    with tempfile.TemporaryDirectory(prefix="sklegal-composition-") as temp:
        policy_path = Path(temp) / "trusted-issuers.json"
        policy_path.write_text(
            json.dumps(
                {
                    "schema_version": "sklegal-trusted-issuers/v1",
                    "policy_version": "sklegal-authz/v1",
                    "issuers": [
                        {
                            "fingerprint": STUB_ISSUER_FPR,
                            "capabilities": [item.value for item in Capability],
                            "audiences": [item.value for item in Audience],
                            "principal_types": [item.value for item in PrincipalType],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        authorizer = build_postgres_capability_authorizer(
            executor=executor,
            trusted_issuers=FileTrustedIssuerBackend(policy_path),
            audit=audit,
            tenant_id=tenant_id,
            clock=lambda: datetime.now(UTC),
        )

    assert authorizer is not None
    assert executor_calls == []
