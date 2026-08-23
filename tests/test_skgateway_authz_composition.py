from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import create_autospec
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sklegal_api.skgateway_authz import (
    ServiceAuthenticationDenied,
    ServiceAuthenticationUnavailable,
    SkGatewayProductionCompositionUnavailable,
    VaultReferenceServiceAuthenticator,
    build_skgateway_authz_production_composition,
    build_skgateway_authz_rollback_router,
    load_skgateway_authz_deployment,
)
from sklegal_audit import PostgresAuditRepository
from sklegal_capauth import FileTrustedIssuerBackend, StaticTrustedIssuerBackend
from sklegal_model_gateway import SkGatewayRoutePolicyVerifier
from sklegal_policies import (
    POLICY_AUTHORIZATION_USE_RESERVE_SQL,
    CurrentAuthorizationUnavailable,
    PostgresAuthorizationUseBackend,
)

ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = (
    ROOT / "config" / "model_gateway" / "deployment" / "skgateway-authz-adapter.json"
)
TENANT_ID = UUID("a1000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 8, 23, 3, 0, tzinfo=UTC)


class _CredentialBackend:
    production_ready = True

    def __init__(self, *, available: bool = True, verified: bool = True) -> None:
        self.is_available = available
        self.verified = verified
        self.calls: list[tuple[str, str]] = []

    def available(self, *, reference: str, service_identity: str) -> bool:
        self.calls.append((reference, service_identity))
        return self.is_available

    def verify(
        self,
        *,
        reference: str,
        service_identity: str,
        authorization: str,
    ) -> bool:
        del authorization
        self.calls.append((reference, service_identity))
        return self.verified


class _Readiness:
    production_ready = True

    def __init__(self, unavailable: str | None = None) -> None:
        self.unavailable = unavailable
        self.checked: list[str] = []

    def check(self, dependency: str) -> bool:
        self.checked.append(dependency)
        return dependency != self.unavailable


def _issuer_backend(tmp_path: Path) -> FileTrustedIssuerBackend:
    policy = {
        "policy_version": "sklegal-authz/v1",
        "schema_version": "sklegal-trusted-issuers/v1",
        "issuers": [
            {
                "fingerprint": "A" * 40,
                "principal_types": ["agent"],
                "audiences": ["sklegal.model"],
                "capabilities": ["corpus.artifact.read"],
            }
        ],
    }
    path = tmp_path / "trusted-issuers.json"
    path.write_text(json.dumps(policy), encoding="ascii")
    return FileTrustedIssuerBackend(path)


def _audit_repository() -> PostgresAuditRepository:
    return PostgresAuditRepository(
        lambda _statement, _parameters: (_ for _ in ()).throw(
            AssertionError("composition must not append audit evidence")
        )
    )


def _composition(tmp_path: Path, **overrides: object):  # type: ignore[no-untyped-def]
    dependencies = {
        "deployment": load_skgateway_authz_deployment(DEPLOYMENT),
        "executor": lambda _sql, _params: None,
        "trusted_issuers": _issuer_backend(tmp_path),
        "audit_repository": _audit_repository(),
        "service_credentials": _CredentialBackend(),
        "route_verifier": create_autospec(SkGatewayRoutePolicyVerifier, instance=True),
        "readiness": _Readiness(),
        "tenant_id": TENANT_ID,
        "clock": lambda: NOW,
        "principal_resolver": lambda _request: None,
        "scope_resolver": lambda _request: None,
    }
    dependencies.update(overrides)
    return build_skgateway_authz_production_composition(**dependencies)  # type: ignore[arg-type]


def test_production_composition_is_ready_but_profile_remains_disabled(
    tmp_path: Path,
) -> None:
    readiness = _Readiness()
    composition = _composition(tmp_path, readiness=readiness)

    assert composition.revision == "sklegal-skgateway-authz-production-composition/v1"
    assert composition.bind_host == "127.0.0.1"
    assert composition.transport == "authenticated-local-http"
    assert composition.profile_enabled is False
    assert composition.protected_traffic is False
    assert composition.service_secret_reference.startswith("vault:")
    assert readiness.checked == [
        "trusted_state",
        "service_identity",
        "principal_state",
        "revocation",
        "replay",
        "route_policy",
        "material_policy",
        "append_only_audit",
    ]


def test_production_composition_rejects_synthetic_current_state(
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError, match="durable canonical backend"):
        _composition(
            tmp_path,
            trusted_issuers=StaticTrustedIssuerBackend({"A" * 40}),
        )


def test_production_composition_rejects_non_durable_audit(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="append-only PostgreSQL adapter"):
        _composition(tmp_path, audit_repository=object())


@pytest.mark.parametrize("dependency", ["revocation", "replay", "route_policy"])
def test_startup_rejects_unavailable_dependency(
    tmp_path: Path,
    dependency: str,
) -> None:
    with pytest.raises(
        SkGatewayProductionCompositionUnavailable,
        match="required authorization dependency is unavailable",
    ):
        _composition(tmp_path, readiness=_Readiness(unavailable=dependency))


def test_startup_rejects_audit_outage(tmp_path: Path) -> None:
    with pytest.raises(SkGatewayProductionCompositionUnavailable):
        _composition(
            tmp_path,
            readiness=_Readiness(unavailable="append_only_audit"),
        )


def test_startup_rejects_unavailable_vault_reference(tmp_path: Path) -> None:
    with pytest.raises(
        SkGatewayProductionCompositionUnavailable,
        match="service authentication dependency is unavailable",
    ):
        _composition(
            tmp_path,
            service_credentials=_CredentialBackend(available=False),
        )


def test_service_identity_and_vault_reference_are_pinned() -> None:
    backend = _CredentialBackend()
    authenticator = VaultReferenceServiceAuthenticator(
        backend=backend,
        secret_reference="vault:sklegal/skgateway/authz-service-token",  # pragma: allowlist secret
        service_identity="capauth:sklegal-model-gateway@chiap01.skworld",
    )
    assert authenticator.authenticate("Bearer synthetic") == (
        "capauth:sklegal-model-gateway@chiap01.skworld"
    )
    assert backend.calls[-1] == (
        "vault:sklegal/skgateway/authz-service-token",
        "capauth:sklegal-model-gateway@chiap01.skworld",
    )
    backend.verified = False
    with pytest.raises(ServiceAuthenticationDenied):
        authenticator.authenticate("Bearer synthetic")
    backend.is_available = False
    with pytest.raises(ServiceAuthenticationUnavailable):
        authenticator.require_ready()


def test_policy_authorization_use_is_durable_and_fails_closed() -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(statement: str, parameters: tuple[object, ...]) -> object:
        calls.append((statement, parameters))
        return {"reserve_policy_authorization_use": True}

    backend = PostgresAuthorizationUseBackend(execute, tenant_id=TENANT_ID)
    decision_id = UUID("a2000000-0000-4000-8000-000000000002")
    assert backend.reserve(
        capauth_decision_id=decision_id,
        invocation_digest="a" * 64,
        expires_at=datetime(2026, 8, 23, 3, 1, tzinfo=UTC),
        evaluated_at=NOW,
    )
    assert calls[0][0] == POLICY_AUTHORIZATION_USE_RESERVE_SQL
    assert calls[0][1][:3] == (TENANT_ID, decision_id, "a" * 64)

    unavailable = PostgresAuthorizationUseBackend(
        lambda _statement, _parameters: None,
        tenant_id=TENANT_ID,
    )
    with pytest.raises(CurrentAuthorizationUnavailable):
        unavailable.reserve(
            capauth_decision_id=decision_id,
            invocation_digest="a" * 64,
            expires_at=datetime(2026, 8, 23, 3, 1, tzinfo=UTC),
            evaluated_at=NOW,
        )


def test_rollback_router_returns_deterministic_sanitized_denial() -> None:
    app = FastAPI()
    app.include_router(build_skgateway_authz_rollback_router())
    with TestClient(app) as client:
        response = client.post(
            "/v1/authz/decide",
            headers={"Authorization": "Bearer synthetic-sensitive-value"},
            json={"matter_content": "synthetic protected-looking text"},
        )
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "authorization_backend_unavailable"}}
    assert "synthetic" not in response.text
