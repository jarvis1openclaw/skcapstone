"""Synthetic SKLegal capability test support with no persisted credentials."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from capauth import TokenPayload  # type: ignore[import-untyped]
from capauth.testing import (  # type: ignore[import-untyped]
    STUB_ISSUER_FPR,
    signing_stub,
    stub_signature_for,
)
from sklegal_capauth import (
    CAPABILITY_RULES,
    Audience,
    AuthorizationDenied,
    AuthorizationRequest,
    AuthorizedContext,
    BoundaryScope,
    Capability,
    CapabilityAuthorizer,
    CapabilityGrant,
    CapabilityIssuer,
    DecisionReason,
    InMemoryAuditSink,
    InMemoryPrincipalPolicyBackend,
    InMemoryReplayBackend,
    InMemoryRevocationBackend,
    ModelRoute,
    PresentedCapability,
    PrincipalContext,
    PrincipalType,
    Purpose,
    SignatureVerificationCache,
    StaticTrustedIssuerBackend,
)
from sklegal_capauth import tokens as token_module

TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("20000000-0000-4000-8000-000000000001")
WORKFLOW_RUN_ID = "workflow:synthetic-001"
RESOURCE_DIGEST = "1" * 64


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class SyntheticSigner:
    @property
    def issuer_fingerprint(self) -> str:
        return STUB_ISSUER_FPR

    def sign(self, payload_bytes: bytes) -> str:
        return stub_signature_for(payload_bytes)


class CapabilityTestRig:
    """One isolated process-local authorization environment."""

    def __init__(self) -> None:
        self.clock = MutableClock()
        self.stub: AbstractContextManager[Callable[[bytes], str]] = signing_stub()
        self.stub.__enter__()
        self.principals = InMemoryPrincipalPolicyBackend()
        self.revocations = InMemoryRevocationBackend()
        self.replay = InMemoryReplayBackend(clock=self.clock)
        self.audit = InMemoryAuditSink()
        self.cache = SignatureVerificationCache(clock=self.clock)
        self.trusted = StaticTrustedIssuerBackend({STUB_ISSUER_FPR})
        self.authorizer = self.new_authorizer()
        self.issuer = CapabilityIssuer(SyntheticSigner(), clock=self.clock)

    def close(self) -> None:
        self.stub.__exit__(None, None, None)

    def new_authorizer(self, **overrides: object) -> CapabilityAuthorizer:
        values: dict[str, object] = {
            "trusted_issuers": self.trusted,
            "principals": self.principals,
            "revocations": self.revocations,
            "replay": self.replay,
            "audit": self.audit,
            "signature_cache": self.cache,
            "clock": self.clock,
        }
        values.update(overrides)
        return CapabilityAuthorizer(**values)  # type: ignore[arg-type]

    def principal(
        self,
        principal_type: PrincipalType = PrincipalType.HUMAN,
        *,
        tenant_id: UUID = TENANT_ID,
        subject: str | None = None,
        active: bool = True,
    ) -> PrincipalContext:
        principal = PrincipalContext(
            principal_id=uuid4(),
            principal_type=principal_type,
            subject=subject or f"synthetic:{principal_type.value}:{uuid4().hex}",
            tenant_id=tenant_id,
        )
        self.principals.set(principal, active=active)
        return principal

    def grant(
        self,
        *,
        audience: Audience = Audience.API,
        capability: Capability = Capability.MATTER_READ,
        purpose: Purpose | None = None,
        target: str | None = None,
        tenant_id: UUID = TENANT_ID,
        matter_id: UUID | None = MATTER_ID,
        resource_id: str | None = None,
        resource_version: int | None = None,
        resource_sha256: str | None = None,
        model_route: ModelRoute | None = None,
        workflow_run_id: str | None = None,
    ) -> CapabilityGrant:
        rule = CAPABILITY_RULES[capability]
        if not rule.matter_required:
            matter_id = None
        if audience == Audience.MODEL:
            model_route = model_route or ModelRoute.LOCAL_QWEN
            workflow_run_id = workflow_run_id or WORKFLOW_RUN_ID
        if audience in {Audience.TOOL, Audience.CONNECTOR}:
            workflow_run_id = workflow_run_id or WORKFLOW_RUN_ID
        if capability.value.endswith(".dispatch"):
            resource_id = resource_id or "action:synthetic-001"
            resource_version = resource_version or 1
            resource_sha256 = resource_sha256 or RESOURCE_DIGEST
        return CapabilityGrant(
            audience=audience,
            target=target or f"{audience.value.removeprefix('sklegal.')}:synthetic",
            capability=capability,
            tenant_id=tenant_id,
            matter_id=matter_id,
            resource_type=rule.resource_type,
            resource_id=resource_id,
            resource_version=resource_version,
            resource_sha256=resource_sha256,
            operation=rule.operation,
            purpose=purpose or next(iter(rule.purposes)),
            model_route=model_route,
            workflow_run_id=workflow_run_id,
        )

    def issue(
        self,
        principal: PrincipalContext,
        grant: CapabilityGrant,
        *,
        ttl_seconds: int = 300,
        max_delegation_depth: int = 0,
    ) -> PresentedCapability:
        return self.issuer.issue_root(
            principal=principal,
            grant=grant,
            ttl_seconds=ttl_seconds,
            max_delegation_depth=max_delegation_depth,
        )

    def request(
        self,
        principal: PrincipalContext,
        grant: CapabilityGrant,
    ) -> AuthorizationRequest:
        return AuthorizationRequest(
            principal=principal,
            grant=grant,
            correlation_id=uuid4(),
        )

    def authorize(
        self,
        principal: PrincipalContext,
        grant: CapabilityGrant,
        presented: PresentedCapability | None,
        *,
        authorizer: CapabilityAuthorizer | None = None,
    ) -> AuthorizedContext:
        return (authorizer or self.authorizer).authorize(
            presented,
            self.request(principal, grant),
        )

    def denied_reason(
        self,
        principal: PrincipalContext,
        grant: CapabilityGrant,
        presented: PresentedCapability | None,
        *,
        authorizer: CapabilityAuthorizer | None = None,
    ) -> DecisionReason:
        try:
            self.authorize(principal, grant, presented, authorizer=authorizer)
        except AuthorizationDenied as exc:
            return exc.decision.reason_code
        raise AssertionError("authorization unexpectedly succeeded")


def raw_leaf(presented: PresentedCapability) -> str:
    return presented.credentials_for_verification()[-1]


def resign_raw(
    raw: str,
    mutate_payload: Callable[[dict[str, Any]], None],
) -> PresentedCapability:
    """Mutate, canonicalize, and stub-sign an in-memory negative test token."""

    envelope = json.loads(raw)
    payload_data = envelope["payload"]
    mutate_payload(payload_data)
    payload_data["token_id"] = "0" * 64
    payload = TokenPayload.model_validate(payload_data)
    payload.token_id = token_module._payload_identity(payload)
    envelope["payload"] = payload.model_dump(mode="json")
    envelope["signature"] = stub_signature_for(
        payload.model_dump_json().encode("utf-8")
    )
    return PresentedCapability.single(
        json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    )


def boundary_scope(grant: CapabilityGrant) -> BoundaryScope:
    return BoundaryScope(
        tenant_id=grant.tenant_id,
        matter_id=grant.matter_id,
        resource_id=grant.resource_id,
        resource_version=grant.resource_version,
        resource_sha256=grant.resource_sha256,
        model_route=grant.model_route,
        workflow_run_id=grant.workflow_run_id,
    )
