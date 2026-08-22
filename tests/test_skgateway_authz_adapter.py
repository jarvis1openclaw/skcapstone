from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sklegal_api.skgateway_authz import (
    SKGATEWAY_AUTHZ_SNAPSHOT_SQL,
    CanonicalSkGatewayFactsResolver,
    ConstantTimeBearerServiceAuthenticator,
    PostgresSkGatewayTrustedStateBackend,
    ServiceAuthenticationDenied,
    ServiceAuthenticationRequired,
    ServiceAuthenticationUnavailable,
    SkGatewayAuthzRequest,
    SkGatewayAuthzResponse,
    SkGatewayTrustedFacts,
    SkGatewayTrustedSnapshot,
    build_skgateway_authz_router,
)


class _ProtectedRoute:
    def __init__(self, error: HTTPException | None = None) -> None:
        self.calls: list[object] = []
        self.error = error

    async def __call__(self, request: object) -> object:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return object()


class _Evaluator:
    def __init__(self, *, allow: bool = True, fail: bool = False) -> None:
        self.allow = allow
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def decide(self, **kwargs: object) -> SkGatewayAuthzResponse:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("database outage")
        return SkGatewayAuthzResponse(
            allow=self.allow,
            reason="allow" if self.allow else "policy_denied",
            decision_id="decision-1",
            policy_revision="policy-1",
            correlation_id="corr-1",
        )


class _Resolver:
    def __init__(self, *, subject: str = "trusted-subject", fail: bool = False) -> None:
        self.subject = subject
        self.fail = fail
        self.calls: list[object] = []

    def resolve(self, request: object) -> SkGatewayTrustedFacts:
        self.calls.append(request)
        if self.fail:
            raise RuntimeError("trusted state unavailable")
        return SkGatewayTrustedFacts(
            subject=self.subject,
            capability="skgateway.infer",
            resource={"tenant_id": "trusted-tenant", "matter_id": "trusted-matter"},
            context={"classification": "public"},
        )


class _TrustedStateBackend:
    def __init__(self, snapshot: SkGatewayTrustedSnapshot | object) -> None:
        self.value = snapshot
        self.calls: list[dict[str, str]] = []

    def snapshot(self, **kwargs: object) -> SkGatewayTrustedSnapshot:
        self.calls.append(kwargs)
        return self.value  # type: ignore[return-value]


class _RouteVerifier:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def verify(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("route policy unavailable")
        return object()


def _client(evaluator: _Evaluator, resolver: _Resolver | None = None) -> TestClient:
    app = FastAPI()
    service_identity = "capauth:sklegal-model-gateway@chiap01.skworld"
    app.include_router(
        build_skgateway_authz_router(
            evaluator=evaluator,
            facts_resolver=resolver or _Resolver(),
            service_authenticator=ConstantTimeBearerServiceAuthenticator(
                service_identity=service_identity,
                service_token="test-secret",
            ),
            protected_route=_ProtectedRoute(),  # type: ignore[arg-type]
            service_identity=service_identity,
        )
    )
    return TestClient(app)


def test_valid_service_decision_is_sanitized_and_forwarded() -> None:
    evaluator = _Evaluator()
    with _client(evaluator) as client:
        response = client.post(
            "/v1/authz/decide",
            headers={"X-SKLegal-Service-Authorization": "Bearer test-secret"},
            json={
                "subject": "sklegal-model-gateway",
                "capability": "skgateway.infer",
                "resource": {"tenant_id": "tenant-1", "matter_id": "matter-1"},
                "context": {"classification": "public"},
            },
        )
    assert response.status_code == 200
    assert response.json()["allow"] is True
    assert evaluator.calls[0]["subject"] == "trusted-subject"
    assert evaluator.calls[0]["resource"] == {
        "tenant_id": "trusted-tenant",
        "matter_id": "trusted-matter",
    }
    assert "test-secret" not in response.text


def test_wrong_service_credential_denies_without_evaluator_call() -> None:
    evaluator = _Evaluator()
    with _client(evaluator) as client:
        response = client.post(
            "/v1/authz/decide",
            headers={"X-SKLegal-Service-Authorization": "Bearer wrong"},
            json={"subject": "s", "capability": "skgateway.infer"},
        )
    assert response.status_code == 403
    assert evaluator.calls == []


def test_service_authentication_precedes_capauth_reservation() -> None:
    evaluator = _Evaluator()
    protected = _ProtectedRoute()
    app = FastAPI()
    service_identity = "capauth:sklegal-model-gateway@chiap01.skworld"
    app.include_router(
        build_skgateway_authz_router(
            evaluator=evaluator,
            facts_resolver=_Resolver(),
            service_authenticator=ConstantTimeBearerServiceAuthenticator(
                service_identity=service_identity,
                service_token="test-secret",
            ),
            protected_route=protected,  # type: ignore[arg-type]
            service_identity=service_identity,
        )
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/authz/decide",
            headers={"X-SKLegal-Service-Authorization": "Bearer wrong"},
            json={"subject": "s", "capability": "skgateway.infer"},
        )
    assert response.status_code == 403
    assert protected.calls == []
    assert evaluator.calls == []


def test_capauth_denial_is_preserved_and_stops_policy_evaluation() -> None:
    evaluator = _Evaluator()
    protected = _ProtectedRoute(
        HTTPException(status_code=403, detail={"code": "capability_denied"})
    )
    app = FastAPI()
    service_identity = "capauth:sklegal-model-gateway@chiap01.skworld"
    app.include_router(
        build_skgateway_authz_router(
            evaluator=evaluator,
            facts_resolver=_Resolver(),
            service_authenticator=ConstantTimeBearerServiceAuthenticator(
                service_identity=service_identity,
                service_token="test-secret",
            ),
            protected_route=protected,  # type: ignore[arg-type]
            service_identity=service_identity,
        )
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/authz/decide",
            headers={
                "Authorization": "Bearer opaque-capauth-credential",
                "X-SKLegal-Service-Authorization": "Bearer test-secret",
            },
            json={"subject": "s", "capability": "skgateway.infer"},
        )
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "capability_denied"}}
    assert len(protected.calls) == 1
    assert evaluator.calls == []


def test_missing_service_credential_requires_authentication() -> None:
    evaluator = _Evaluator()
    with _client(evaluator) as client:
        response = client.post(
            "/v1/authz/decide",
            json={"subject": "s", "capability": "skgateway.infer"},
        )
    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "authentication_required"}}
    assert evaluator.calls == []


class _ServiceAuthenticator:
    def __init__(self, result: str | Exception) -> None:
        self.result = result

    def authenticate(self, authorization: str | None) -> str:
        del authorization
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _client_with_authenticator(authenticator: _ServiceAuthenticator) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_skgateway_authz_router(
            evaluator=_Evaluator(),
            facts_resolver=_Resolver(),
            service_authenticator=authenticator,
            protected_route=_ProtectedRoute(),  # type: ignore[arg-type]
            service_identity="capauth:sklegal-model-gateway@chiap01.skworld",
        )
    )
    return TestClient(app)


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (ServiceAuthenticationRequired(), 401, "authentication_required"),
        (ServiceAuthenticationDenied(), 403, "invalid_service_credential"),
        (
            ServiceAuthenticationUnavailable(),
            503,
            "authorization_backend_unavailable",
        ),
    ],
)
def test_service_authentication_failures_are_sanitized(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    with _client_with_authenticator(_ServiceAuthenticator(error)) as client:
        response = client.post(
            "/v1/authz/decide",
            headers={"X-SKLegal-Service-Authorization": "Bearer opaque"},
            json={"subject": "s", "capability": "skgateway.infer"},
        )
    assert response.status_code == status_code
    assert response.json() == {"detail": {"code": code}}


def test_authenticated_service_identity_must_match_deployment_pin() -> None:
    with _client_with_authenticator(_ServiceAuthenticator("wrong-service")) as client:
        response = client.post(
            "/v1/authz/decide",
            headers={"X-SKLegal-Service-Authorization": "Bearer opaque"},
            json={"subject": "s", "capability": "skgateway.infer"},
        )
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "invalid_service_identity"}}


def test_non_gateway_capability_denies_without_evaluator_call() -> None:
    evaluator = _Evaluator()
    with _client(evaluator) as client:
        response = client.post(
            "/v1/authz/decide",
            headers={"X-SKLegal-Service-Authorization": "Bearer test-secret"},
            json={"subject": "s", "capability": "matter.manage"},
        )
    assert response.status_code == 403
    assert evaluator.calls == []


def test_evaluator_outage_fails_closed() -> None:
    with _client(_Evaluator(fail=True)) as client:
        response = client.post(
            "/v1/authz/decide",
            headers={"X-SKLegal-Service-Authorization": "Bearer test-secret"},
            json={"subject": "s", "capability": "skgateway.infer"},
        )
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "authorization_backend_unavailable"}}


def test_trusted_state_outage_fails_closed_before_evaluator() -> None:
    evaluator = _Evaluator()
    with _client(evaluator, _Resolver(fail=True)) as client:
        response = client.post(
            "/v1/authz/decide",
            headers={"X-SKLegal-Service-Authorization": "Bearer test-secret"},
            json={"subject": "attacker", "capability": "skgateway.infer"},
        )
    assert response.status_code == 503
    assert evaluator.calls == []


def test_unknown_request_fields_are_rejected() -> None:
    with _client(_Evaluator()) as client:
        response = client.post(
            "/v1/authz/decide",
            headers={"X-SKLegal-Service-Authorization": "Bearer test-secret"},
            json={"subject": "s", "capability": "skgateway.infer", "prompt": "secret"},
        )
    assert response.status_code == 422


def test_oversized_selector_map_is_rejected_before_authorization() -> None:
    evaluator = _Evaluator()
    with _client(evaluator) as client:
        response = client.post(
            "/v1/authz/decide",
            headers={"X-SKLegal-Service-Authorization": "Bearer test-secret"},
            json={
                "subject": "s",
                "capability": "skgateway.infer",
                "resource": {f"selector-{index}": "value" for index in range(17)},
            },
        )
    assert response.status_code == 422
    assert evaluator.calls == []


@pytest.mark.parametrize(
    ("allow", "reason"),
    [
        (True, "policy_denied"),
        (False, "allow"),
        (False, "database_connection_failed_with_private_details"),
    ],
)
def test_response_rejects_inconsistent_or_unsanitized_reasons(
    allow: bool,
    reason: str,
) -> None:
    with pytest.raises(ValidationError):
        SkGatewayAuthzResponse(allow=allow, reason=reason)  # type: ignore[arg-type]


def test_response_rejects_unbounded_obligations() -> None:
    with pytest.raises(ValidationError):
        SkGatewayAuthzResponse(
            allow=False,
            reason="policy_denied",
            obligations=[f"obligation-{index}" for index in range(17)],
        )


def test_canonical_resolver_returns_only_exact_current_snapshot() -> None:
    facts = SkGatewayTrustedFacts(
        subject="agent-1",
        capability="skgateway.infer",
        resource={"tenant_id": "tenant-1", "matter_id": "matter-1"},
        context={"classification": "public", "route_id": "route-1"},
    )
    backend = _TrustedStateBackend(
        SkGatewayTrustedSnapshot(
            revision="policy-7",
            service_identity="capauth:sklegal-model-gateway@chiap01.skworld",
            facts=facts,
        )
    )
    route_verifier = _RouteVerifier()
    resolver = CanonicalSkGatewayFactsResolver(
        backend=backend,
        route_verifier=route_verifier,
        service_identity="capauth:sklegal-model-gateway@chiap01.skworld",
    )
    request = {
        "subject": "agent-1",
        "capability": "skgateway.infer",
        "resource": {"tenant_id": "tenant-1", "matter_id": "matter-1"},
        "context": {"classification": "public", "route_id": "route-1"},
    }
    assert resolver.resolve(SkGatewayAuthzRequest.model_validate(request)) == facts
    assert backend.calls[0]["service_identity"] == (
        "capauth:sklegal-model-gateway@chiap01.skworld"
    )
    assert backend.calls[0]["request"] == SkGatewayAuthzRequest.model_validate(request)
    assert route_verifier.calls == [
        {
            "service_identity": "capauth:sklegal-model-gateway@chiap01.skworld",
            "capability": "skgateway.infer",
            "resource": {"tenant_id": "tenant-1", "matter_id": "matter-1"},
            "context": {"classification": "public", "route_id": "route-1"},
        }
    ]


def test_canonical_resolver_denies_wire_scope_mismatch() -> None:
    facts = SkGatewayTrustedFacts(
        subject="agent-1",
        capability="skgateway.infer",
        resource={"tenant_id": "tenant-1", "matter_id": "matter-1"},
        context={"classification": "public"},
    )
    backend = _TrustedStateBackend(
        SkGatewayTrustedSnapshot(
            revision="policy-7",
            service_identity="capauth:sklegal-model-gateway@chiap01.skworld",
            facts=facts,
        )
    )
    resolver = CanonicalSkGatewayFactsResolver(
        backend=backend,
        route_verifier=_RouteVerifier(),
        service_identity="capauth:sklegal-model-gateway@chiap01.skworld",
    )
    request = SkGatewayAuthzRequest(
        subject="agent-1",
        capability="skgateway.infer",
        resource={"tenant_id": "tenant-1", "matter_id": "matter-2"},
        context={"classification": "public"},
    )
    with pytest.raises(ValueError, match="resource scope"):
        resolver.resolve(request)


def test_canonical_resolver_fails_closed_when_route_policy_denies() -> None:
    facts = SkGatewayTrustedFacts(
        subject="agent-1",
        capability="skgateway.infer",
        resource={"route_id": "route-1"},
        context={"classification": "public"},
    )
    resolver = CanonicalSkGatewayFactsResolver(
        backend=_TrustedStateBackend(
            SkGatewayTrustedSnapshot(
                revision="policy-8",
                service_identity="capauth:sklegal-model-gateway@chiap01.skworld",
                facts=facts,
            )
        ),
        route_verifier=_RouteVerifier(fail=True),
        service_identity="capauth:sklegal-model-gateway@chiap01.skworld",
    )
    with pytest.raises(RuntimeError, match="route policy unavailable"):
        resolver.resolve(
            SkGatewayAuthzRequest(
                subject="agent-1",
                capability="skgateway.infer",
                resource={"route_id": "route-1"},
                context={"classification": "public"},
            )
        )


def test_postgres_backend_parses_one_atomic_snapshot() -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    def executor(sql: str, params: tuple[object, ...]) -> object:
        calls.append((sql, params))
        return {
            "revision": "policy-8",
            "service_identity": "capauth:sklegal-model-gateway@chiap01.skworld",
            "facts": {
                "subject": "agent-1",
                "capability": "skgateway.infer",
                "resource": {"tenant_id": "tenant-1", "matter_id": "matter-1"},
                "context": {"classification": "public"},
            },
        }

    request = SkGatewayAuthzRequest(
        subject="agent-1",
        capability="skgateway.infer",
        resource={"tenant_id": "tenant-1", "matter_id": "matter-1"},
        context={"classification": "public"},
    )
    snapshot = PostgresSkGatewayTrustedStateBackend(executor).snapshot(
        service_identity="capauth:sklegal-model-gateway@chiap01.skworld",
        request=request,
    )
    assert snapshot.revision == "policy-8"
    assert calls[0][0] == SKGATEWAY_AUTHZ_SNAPSHOT_SQL
    assert calls[0][1][0:2] == (
        "capauth:sklegal-model-gateway@chiap01.skworld",
        "agent-1",
    )


@pytest.mark.parametrize("bad_row", [None, (), "not-json", {"revision": "only"}])
def test_postgres_backend_fails_closed_on_bad_snapshot(bad_row: object) -> None:
    backend = PostgresSkGatewayTrustedStateBackend(lambda _sql, _params: bad_row)
    request = SkGatewayAuthzRequest(subject="agent-1", capability="skgateway.infer")
    with pytest.raises(RuntimeError, match="snapshot unavailable"):
        backend.snapshot(
            service_identity="capauth:sklegal-model-gateway@chiap01.skworld",
            request=request,
        )
