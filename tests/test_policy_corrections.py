from __future__ import annotations

import traceback
import unittest
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from uuid import UUID, uuid4

from pydantic import ValidationError
from sklegal_capauth import (
    Audience,
    Capability,
    ModelRoute,
    PrincipalType,
    Purpose,
    StaticTrustedIssuerBackend,
    UnavailablePrincipalPolicyBackend,
)
from sklegal_domain import DataClassification
from sklegal_policies import (
    AccessState,
    CapAuthCurrentStateVerifier,
    ClassificationSource,
    ConflictDecision,
    ConflictDisposition,
    DataFlowBoundary,
    InMemoryAuthorizationUseBackend,
    InMemoryPolicyAuditSink,
    LegalHold,
    LegalHoldStatus,
    MaterialPolicyFacts,
    PolicyAccessRequest,
    PolicyBackendUnavailable,
    PolicyBoundaryRequirement,
    PolicyDenied,
    PolicyGateway,
    PolicyReason,
    PostgresPolicyBackend,
    ProtectedDataFlow,
    RetentionBoundaryRequirement,
    RetentionDenied,
    RetentionGateway,
    RetentionPolicy,
    RetentionRequest,
    UnavailablePolicyAuditSink,
    UnavailablePolicyBackend,
    WaiverReference,
)

from tests.support.capauth_contract import (
    RESOURCE_DIGEST,
    WORKFLOW_RUN_ID,
    CapabilityTestRig,
)

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
MATERIAL_ID = UUID("a3000000-0000-4000-8000-000000000001")
OTHER_MATERIAL_ID = UUID("a3000000-0000-4000-8000-000000000002")
REVISION = "a" * 64


class CountingPolicyBackend:
    def __init__(
        self,
        facts: MaterialPolicyFacts,
        *,
        before_return: object | None = None,
    ) -> None:
        self.facts = facts
        self.calls = 0
        self.before_return = before_return

    def load(self, request: object) -> MaterialPolicyFacts:
        del request
        self.calls += 1
        if callable(self.before_return):
            self.before_return()
        return self.facts


class FailingClock:
    def __call__(self) -> datetime:
        raise RuntimeError("synthetic clock detail")


class ActionAuditSink:
    def __init__(self, action: object) -> None:
        self.action = action
        self.recorded: list[object] = []

    def record(self, decision: object) -> None:
        self.recorded.append(decision)
        if len(self.recorded) == 1 and callable(self.action):
            self.action()


class BlockingAuditSink:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()
        self.calls = 0

    def record(self, decision: object) -> None:
        del decision
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise RuntimeError("synthetic audit wait timeout")


class SentinelBackend:
    def __init__(self, sentinel: str) -> None:
        self.sentinel = sentinel

    def load(self, request: object) -> MaterialPolicyFacts:
        del request
        raise RuntimeError(self.sentinel)


class SentinelAuditSink:
    def __init__(self, sentinel: str) -> None:
        self.sentinel = sentinel

    def record(self, decision: object) -> None:
        del decision
        raise RuntimeError(self.sentinel)


class SentinelClock:
    def __init__(self, sentinel: str) -> None:
        self.sentinel = sentinel

    def __call__(self) -> datetime:
        raise RuntimeError(self.sentinel)


class SentinelPrincipalBackend:
    def __init__(self, sentinel: str) -> None:
        self.sentinel = sentinel

    def snapshot(self, principal: object) -> object:
        del principal
        raise RuntimeError(self.sentinel)


def exception_chain_text(error: BaseException) -> str:
    rendered = ["".join(traceback.format_exception(error))]
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        rendered.append(repr(current))
        current = current.__cause__ or current.__context__
    return "\n".join(rendered)


class PolicyCorrectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = CapabilityTestRig()
        self.rig.clock.value = T0
        self.human = self.rig.principal()
        self.matter_id = UUID("a3000000-0000-4000-8000-000000000003")
        self.uses = InMemoryAuthorizationUseBackend()

    def tearDown(self) -> None:
        self.rig.close()

    def facts(self, **changes: object) -> MaterialPolicyFacts:
        payload: dict[str, object] = {
            "policy_revision": REVISION,
            "tenant_id": self.human.tenant_id,
            "matter_id": self.matter_id,
            "material_id": MATERIAL_ID,
            "material_version": 1,
            "tenant_membership": AccessState.ACTIVE,
            "matter_membership": AccessState.ACTIVE,
            "conflict_state_complete": True,
            "wall_state_complete": True,
            "classification_state_complete": True,
            "conflict_decision": ConflictDecision(
                decision_id=uuid4(),
                tenant_id=self.human.tenant_id,
                matter_id=self.matter_id,
                conflict_check_id=uuid4(),
                disposition=ConflictDisposition.CLEAR,
                decided_by_principal_id=uuid4(),
                decided_at=T0 - timedelta(days=1),
            ),
            "classification_sources": (
                ClassificationSource(
                    source_kind="material",
                    source_id=MATERIAL_ID,
                    classification=DataClassification.CONFIDENTIAL,
                ),
            ),
            "retention_policy": RetentionPolicy(
                retention_policy_id=uuid4(),
                tenant_id=self.human.tenant_id,
                matter_id=self.matter_id,
                retain_for_days=30,
                effective_from=T0 - timedelta(days=100),
            ),
            "legal_hold_state_complete": True,
            "ownership_resolved": True,
            "pending_export": False,
            "preservation_required": False,
        }
        payload.update(changes)
        return MaterialPolicyFacts.model_validate(payload)

    def request_for(
        self,
        requirement: PolicyBoundaryRequirement,
        *,
        principal_id: UUID | None = None,
    ) -> PolicyAccessRequest:
        return PolicyAccessRequest(
            boundary=requirement.boundary,
            principal_id=principal_id or self.human.principal_id,
            tenant_id=self.human.tenant_id,
            matter_id=self.matter_id,
            material_id=MATERIAL_ID,
            material_version=1,
            material_sha256=RESOURCE_DIGEST,
            purpose=requirement.purpose,
            model_route=requirement.model_route,
            workflow_run_id=requirement.workflow_run_id,
            evaluated_at=T0,
        )

    def requirement(
        self,
        boundary: DataFlowBoundary,
    ) -> PolicyBoundaryRequirement:
        values = {
            DataFlowBoundary.RETRIEVAL: dict(
                audience=Audience.API,
                target="api:material.retrieve",
                capability=Capability.EVIDENCE_READ,
                purpose=Purpose.EVIDENCE_REVIEW,
            ),
            DataFlowBoundary.CACHE: dict(
                audience=Audience.TOOL,
                target="tool:material.cache",
                capability=Capability.EVIDENCE_READ,
                purpose=Purpose.EVIDENCE_REVIEW,
                workflow_run_id=WORKFLOW_RUN_ID,
            ),
            DataFlowBoundary.MODEL_CONTEXT: dict(
                audience=Audience.MODEL,
                target="model:qwen.context",
                capability=Capability.EVIDENCE_READ,
                purpose=Purpose.EVIDENCE_REVIEW,
                model_route=ModelRoute.LOCAL_QWEN,
                workflow_run_id=WORKFLOW_RUN_ID,
            ),
            DataFlowBoundary.EXPORT: dict(
                audience=Audience.API,
                target="api:material.export",
                capability=Capability.EVIDENCE_READ,
                purpose=Purpose.EVIDENCE_REVIEW,
            ),
            DataFlowBoundary.AUDIT_DETAIL: dict(
                audience=Audience.API,
                target="api:audit.detail",
                capability=Capability.AUDIT_READ,
                purpose=Purpose.AUDIT_REVIEW,
            ),
        }[boundary]
        return PolicyBoundaryRequirement(boundary=boundary, **values)

    def current_verifier(
        self,
        *,
        principals: object | None = None,
        trusted: object | None = None,
    ) -> CapAuthCurrentStateVerifier:
        return CapAuthCurrentStateVerifier(
            trusted_issuers=trusted or self.rig.trusted,
            principals=principals or self.rig.principals,
            revocations=self.rig.revocations,
            uses=self.uses,
        )

    def gateway(
        self,
        backend: CountingPolicyBackend,
        *,
        verifier: CapAuthCurrentStateVerifier | None = None,
        audit: object | None = None,
        clock: object | None = None,
    ) -> PolicyGateway:
        return PolicyGateway(
            backend=backend,
            audit_sink=audit or InMemoryPolicyAuditSink(),
            current_authorization=verifier or self.current_verifier(),
            clock=clock or self.rig.clock,
        )

    def authorize_exact(
        self,
        requirement: PolicyBoundaryRequirement,
        *,
        principal_type: PrincipalType | None = None,
        ttl_seconds: int = 300,
    ) -> tuple[object, PolicyAccessRequest]:
        principal = self.human
        if principal_type is not None:
            principal = self.rig.principal(principal_type)
        request = self.request_for(requirement, principal_id=principal.principal_id)
        grant = requirement.bind(request)
        token = self.rig.issue(principal, grant, ttl_seconds=ttl_seconds)
        return self.rig.authorize(principal, grant, token), request

    def test_management_capability_is_rejected_by_every_data_flow_before_load(
        self,
    ) -> None:
        backend = CountingPolicyBackend(self.facts())
        gateway = self.gateway(backend)
        invoked: list[DataFlowBoundary] = []
        for boundary in DataFlowBoundary:
            with self.subTest(boundary=boundary):
                requirement = self.requirement(boundary)
                request = self.request_for(requirement)
                management = self.rig.grant(
                    capability=Capability.MATTER_WALL_MANAGE,
                    purpose=Purpose.INFORMATION_BARRIER_ADMIN,
                    target="api:wall.update",
                    tenant_id=self.human.tenant_id,
                    matter_id=self.matter_id,
                )
                authorized = self.rig.authorize(
                    self.human,
                    management,
                    self.rig.issue(self.human, management),
                )
                flow: ProtectedDataFlow[str] = ProtectedDataFlow(
                    gateway=gateway,
                    requirement=requirement,
                )
                with self.assertRaises(PolicyDenied) as raised:
                    flow.invoke(
                        authorized=authorized,
                        request=request,
                        handler=lambda _: invoked.append(boundary) or "payload",
                    )
                self.assertEqual(
                    PolicyReason.CAPAUTH_SCOPE_MISMATCH,
                    raised.exception.decision.reason,
                )
        self.assertEqual(0, backend.calls)
        self.assertEqual([], invoked)

    def test_exact_capauth_grant_fields_are_compared_before_policy_load(self) -> None:
        requirement = self.requirement(DataFlowBoundary.RETRIEVAL)
        request = self.request_for(requirement)
        exact = requirement.bind(request)
        variants = (
            self.rig.grant(
                capability=Capability.EVIDENCE_READ,
                purpose=Purpose.EVIDENCE_REVIEW,
                target="api:material.other",
                tenant_id=self.human.tenant_id,
                matter_id=self.matter_id,
                resource_id=str(MATERIAL_ID),
                resource_version=1,
                resource_sha256=RESOURCE_DIGEST,
            ),
            self.rig.grant(
                capability=Capability.EVIDENCE_MANAGE,
                purpose=Purpose.EVIDENCE_REVIEW,
                target=exact.target,
                tenant_id=self.human.tenant_id,
                matter_id=self.matter_id,
                resource_id=str(MATERIAL_ID),
                resource_version=1,
                resource_sha256=RESOURCE_DIGEST,
            ),
            self.rig.grant(
                capability=Capability.MATTER_READ,
                purpose=Purpose.MATTER_MANAGEMENT,
                target=exact.target,
                tenant_id=self.human.tenant_id,
                matter_id=self.matter_id,
                resource_id=str(MATERIAL_ID),
                resource_version=1,
                resource_sha256=RESOURCE_DIGEST,
            ),
            self.rig.grant(
                capability=Capability.EVIDENCE_READ,
                purpose=Purpose.EVIDENCE_REVIEW,
                target=exact.target,
                tenant_id=self.human.tenant_id,
                matter_id=self.matter_id,
                resource_id=str(OTHER_MATERIAL_ID),
                resource_version=1,
                resource_sha256=RESOURCE_DIGEST,
            ),
            self.rig.grant(
                capability=Capability.EVIDENCE_READ,
                purpose=Purpose.EVIDENCE_REVIEW,
                target=exact.target,
                tenant_id=self.human.tenant_id,
                matter_id=self.matter_id,
                resource_id=str(MATERIAL_ID),
                resource_version=2,
                resource_sha256=RESOURCE_DIGEST,
            ),
            self.rig.grant(
                capability=Capability.EVIDENCE_READ,
                purpose=Purpose.EVIDENCE_REVIEW,
                target=exact.target,
                tenant_id=self.human.tenant_id,
                matter_id=self.matter_id,
                resource_id=str(MATERIAL_ID),
                resource_version=1,
                resource_sha256="2" * 64,
            ),
        )
        backend = CountingPolicyBackend(self.facts())
        gateway = self.gateway(backend)
        for grant in variants:
            with self.subTest(grant=grant):
                authorized = self.rig.authorize(
                    self.human,
                    grant,
                    self.rig.issue(self.human, grant),
                )
                with self.assertRaises(PolicyDenied) as raised:
                    gateway.authorize(authorized, requirement, request)
                self.assertEqual(
                    PolicyReason.CAPAUTH_SCOPE_MISMATCH,
                    raised.exception.decision.reason,
                )
        self.assertEqual(0, backend.calls)

    def test_authorized_context_is_current_one_use_and_expiry_bounded(self) -> None:
        requirement = self.requirement(DataFlowBoundary.RETRIEVAL)
        authorized, request = self.authorize_exact(requirement, ttl_seconds=1)
        backend = CountingPolicyBackend(self.facts())
        flow: ProtectedDataFlow[str] = ProtectedDataFlow(
            gateway=self.gateway(backend),
            requirement=requirement,
        )
        self.assertEqual(
            "payload",
            flow.invoke(
                authorized=authorized,  # type: ignore[arg-type]
                request=request,
                handler=lambda _: "payload",
            ),
        )
        with self.assertRaises(PolicyDenied) as replayed:
            flow.invoke(
                authorized=authorized,  # type: ignore[arg-type]
                request=request,
                handler=lambda _: "second payload",
            )
        self.assertEqual(
            PolicyReason.CAPAUTH_REPLAYED,
            replayed.exception.decision.reason,
        )
        self.assertEqual(1, backend.calls)

        delayed, delayed_request = self.authorize_exact(requirement, ttl_seconds=1)
        self.rig.clock.advance(seconds=2)
        with self.assertRaises(PolicyDenied) as expired:
            flow.invoke(
                authorized=delayed,  # type: ignore[arg-type]
                request=delayed_request,
                handler=lambda _: "delayed payload",
            )
        self.assertEqual(
            PolicyReason.CAPAUTH_EXPIRED,
            expired.exception.decision.reason,
        )
        self.assertEqual(1, backend.calls)

    def test_expiry_and_suspension_during_policy_load_stop_payload_handler(
        self,
    ) -> None:
        requirement = self.requirement(DataFlowBoundary.RETRIEVAL)
        invoked: list[str] = []

        expiring, request = self.authorize_exact(requirement, ttl_seconds=1)
        expiry_backend = CountingPolicyBackend(
            self.facts(),
            before_return=lambda: self.rig.clock.advance(seconds=2),
        )
        expiry_flow: ProtectedDataFlow[str] = ProtectedDataFlow(
            gateway=self.gateway(expiry_backend),
            requirement=requirement,
        )
        with self.assertRaises(PolicyDenied) as expired:
            expiry_flow.invoke(
                authorized=expiring,  # type: ignore[arg-type]
                request=request,
                handler=lambda _: invoked.append("expired") or "payload",
            )
        self.assertEqual(
            PolicyReason.CAPAUTH_EXPIRED,
            expired.exception.decision.reason,
        )

        self.rig.clock.value = T0
        suspending, request = self.authorize_exact(requirement)
        suspension_backend = CountingPolicyBackend(
            self.facts(),
            before_return=lambda: self.rig.principals.set(
                self.human,
                active=False,
            ),
        )
        suspension_flow: ProtectedDataFlow[str] = ProtectedDataFlow(
            gateway=self.gateway(suspension_backend),
            requirement=requirement,
        )
        with self.assertRaises(PolicyDenied) as suspended:
            suspension_flow.invoke(
                authorized=suspending,  # type: ignore[arg-type]
                request=request,
                handler=lambda _: invoked.append("suspended") or "payload",
            )
        self.assertEqual(
            PolicyReason.CAPAUTH_STALE,
            suspended.exception.decision.reason,
        )
        self.assertEqual([], invoked)

    def test_expiry_and_concurrent_revocation_during_audit_stop_handler(
        self,
    ) -> None:
        requirement = self.requirement(DataFlowBoundary.RETRIEVAL)
        invoked: list[str] = []

        expiring, request = self.authorize_exact(requirement, ttl_seconds=1)
        expiry_audit = ActionAuditSink(lambda: self.rig.clock.advance(seconds=2))
        expiry_flow: ProtectedDataFlow[str] = ProtectedDataFlow(
            gateway=self.gateway(
                CountingPolicyBackend(self.facts()),
                audit=expiry_audit,
            ),
            requirement=requirement,
        )
        with self.assertRaises(PolicyDenied) as expired:
            expiry_flow.invoke(
                authorized=expiring,  # type: ignore[arg-type]
                request=request,
                handler=lambda _: invoked.append("expired") or "payload",
            )
        self.assertEqual(
            PolicyReason.CAPAUTH_EXPIRED,
            expired.exception.decision.reason,
        )

        self.rig.clock.value = T0
        revocable, request = self.authorize_exact(requirement)
        credential_digest = revocable.decision.credential_digest  # type: ignore[attr-defined]
        assert credential_digest is not None
        blocking_audit = BlockingAuditSink()
        revocation_flow: ProtectedDataFlow[str] = ProtectedDataFlow(
            gateway=self.gateway(
                CountingPolicyBackend(self.facts()),
                audit=blocking_audit,
            ),
            requirement=requirement,
        )
        failures: list[BaseException] = []

        def invoke() -> None:
            try:
                revocation_flow.invoke(
                    authorized=revocable,  # type: ignore[arg-type]
                    request=request,
                    handler=lambda _: invoked.append("revoked") or "payload",
                )
            except BaseException as exc:
                failures.append(exc)

        worker = Thread(target=invoke)
        worker.start()
        self.assertTrue(blocking_audit.entered.wait(timeout=5))
        self.rig.revocations.revoke(credential_digest)
        blocking_audit.release.set()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(failures))
        self.assertIsInstance(failures[0], PolicyDenied)
        assert isinstance(failures[0], PolicyDenied)
        self.assertEqual(
            PolicyReason.CAPAUTH_STALE,
            failures[0].decision.reason,
        )
        self.assertEqual([], invoked)

    def test_current_principal_revocation_revision_and_outage_deny_before_load(
        self,
    ) -> None:
        requirement = self.requirement(DataFlowBoundary.RETRIEVAL)
        cases: list[tuple[str, object, PolicyReason]] = []

        suspended, request = self.authorize_exact(requirement)
        self.rig.principals.set(self.human, active=False)
        cases.append(("suspended", suspended, PolicyReason.CAPAUTH_STALE))
        self.rig.principals.set(self.human, active=True)

        revoked, _ = self.authorize_exact(requirement)
        digest = revoked.decision.credential_digest  # type: ignore[attr-defined]
        assert digest is not None
        self.rig.revocations.revoke(digest)
        cases.append(("revoked", revoked, PolicyReason.CAPAUTH_STALE))

        revision_changed, _ = self.authorize_exact(requirement)
        self.rig.revocations.revoke("f" * 64)
        cases.append(("revision", revision_changed, PolicyReason.CAPAUTH_STALE))

        backend = CountingPolicyBackend(self.facts())
        gateway = self.gateway(backend)
        for name, authorized, expected in cases:
            with self.subTest(name=name):
                with self.assertRaises(PolicyDenied) as raised:
                    gateway.authorize(
                        authorized,  # type: ignore[arg-type]
                        requirement,
                        request,
                    )
                self.assertEqual(expected, raised.exception.decision.reason)

        unavailable, _ = self.authorize_exact(requirement)
        unavailable_gateway = self.gateway(
            backend,
            verifier=self.current_verifier(
                principals=UnavailablePrincipalPolicyBackend()
            ),
        )
        with self.assertRaises(PolicyDenied) as raised:
            unavailable_gateway.authorize(
                unavailable,  # type: ignore[arg-type]
                requirement,
                request,
            )
        self.assertEqual(
            PolicyReason.CAPAUTH_CURRENT_STATE_UNAVAILABLE,
            raised.exception.decision.reason,
        )

        issuer_changed, _ = self.authorize_exact(requirement)
        changed_trusted = StaticTrustedIssuerBackend(
            {"A" * 40},
        )
        changed_gateway = self.gateway(
            backend,
            verifier=self.current_verifier(trusted=changed_trusted),
        )
        with self.assertRaises(PolicyDenied) as raised:
            changed_gateway.authorize(
                issuer_changed,  # type: ignore[arg-type]
                requirement,
                request,
            )
        self.assertEqual(PolicyReason.CAPAUTH_STALE, raised.exception.decision.reason)
        self.assertEqual(0, backend.calls)

    def test_denial_and_backend_exception_chains_exclude_nested_payloads(
        self,
    ) -> None:
        requirement = self.requirement(DataFlowBoundary.RETRIEVAL)

        cases: tuple[tuple[str, object, object | None], ...] = (
            (
                "policy_backend",
                SentinelBackend("SYNTHETIC_POLICY_BACKEND_PAYLOAD"),
                None,
            ),
            (
                "clock",
                CountingPolicyBackend(self.facts()),
                SentinelClock("SYNTHETIC_CLOCK_PAYLOAD"),
            ),
            (
                "capauth_freshness",
                CountingPolicyBackend(self.facts()),
                SentinelPrincipalBackend("SYNTHETIC_CAPAUTH_PAYLOAD"),
            ),
            (
                "audit_sink",
                CountingPolicyBackend(self.facts()),
                SentinelAuditSink("SYNTHETIC_AUDIT_PAYLOAD"),
            ),
        )
        for name, backend, dependency in cases:
            with self.subTest(name=name):
                authorized, request = self.authorize_exact(requirement)
                values: dict[str, object] = {
                    "backend": backend,
                    "audit_sink": InMemoryPolicyAuditSink(),
                    "current_authorization": self.current_verifier(),
                    "clock": self.rig.clock,
                }
                sentinel = ""
                if name == "clock":
                    values["clock"] = dependency
                    sentinel = "SYNTHETIC_CLOCK_PAYLOAD"
                elif name == "capauth_freshness":
                    values["current_authorization"] = self.current_verifier(
                        principals=dependency
                    )
                    sentinel = "SYNTHETIC_CAPAUTH_PAYLOAD"
                elif name == "audit_sink":
                    values["audit_sink"] = dependency
                    sentinel = "SYNTHETIC_AUDIT_PAYLOAD"
                else:
                    sentinel = "SYNTHETIC_POLICY_BACKEND_PAYLOAD"
                gateway = PolicyGateway(**values)  # type: ignore[arg-type]
                with self.assertRaises(PolicyDenied) as raised:
                    gateway.authorize(
                        authorized,  # type: ignore[arg-type]
                        requirement,
                        request,
                    )
                self.assertNotIn(sentinel, exception_chain_text(raised.exception))
                self.assertIsNone(raised.exception.__context__)
                self.assertIsNone(raised.exception.__cause__)

        postgres_sentinel = "SYNTHETIC_POSTGRES_NESTED_PAYLOAD"

        def failing_executor(
            statement: str,
            parameters: tuple[object, ...],
        ) -> object:
            del statement, parameters
            try:
                raise ValueError(postgres_sentinel)
            except ValueError as exc:
                raise RuntimeError("synthetic driver wrapper") from exc

        postgres = PostgresPolicyBackend(failing_executor)  # type: ignore[arg-type]
        with self.assertRaises(PolicyBackendUnavailable) as raised:
            postgres.load(self.request_for(requirement))
        self.assertNotIn(postgres_sentinel, exception_chain_text(raised.exception))
        self.assertIsNone(raised.exception.__context__)
        self.assertIsNone(raised.exception.__cause__)

    def test_legal_hold_release_graph_is_exact_and_unambiguous(self) -> None:
        issuer = uuid4()
        active = LegalHold(
            legal_hold_id=uuid4(),
            tenant_id=self.human.tenant_id,
            matter_id=self.matter_id,
            status=LegalHoldStatus.ACTIVE,
            scope="material",
            material_id=MATERIAL_ID,
            issued_by_principal_id=issuer,
            effective_from=T0 - timedelta(days=10),
        )
        release = LegalHold(
            legal_hold_id=uuid4(),
            tenant_id=self.human.tenant_id,
            matter_id=self.matter_id,
            status=LegalHoldStatus.RELEASED,
            scope=active.scope,
            material_id=active.material_id,
            issued_by_principal_id=active.issued_by_principal_id,
            effective_from=active.effective_from,
            supersedes_hold_id=active.legal_hold_id,
            released_by_principal_id=uuid4(),
            released_at=T0 - timedelta(days=1),
        )
        self.facts(legal_holds=(active, release))
        invalid_sets = (
            (active, active),
            (release,),
            (active, release, release),
            (
                active,
                LegalHold.model_validate(
                    {
                        **release.__dict__,
                        "legal_hold_id": uuid4(),
                        "material_id": OTHER_MATERIAL_ID,
                    }
                ),
            ),
            (
                active,
                release,
                LegalHold.model_validate(
                    {**release.__dict__, "legal_hold_id": uuid4()}
                ),
            ),
        )
        for holds in invalid_sets:
            with self.subTest(holds=holds):
                with self.assertRaises(ValidationError):
                    self.facts(legal_holds=holds)

    def test_retention_requires_current_conflict_decision_and_valid_waiver(
        self,
    ) -> None:
        from sklegal_policies import PolicyEngine

        engine = PolicyEngine()
        request = RetentionRequest(
            principal_id=self.human.principal_id,
            tenant_id=self.human.tenant_id,
            matter_id=self.matter_id,
            material_id=MATERIAL_ID,
            material_version=1,
            material_sha256=RESOURCE_DIGEST,
            material_created_at=T0 - timedelta(days=31),
            evaluated_at=T0,
        )
        hold = ConflictDecision(
            decision_id=uuid4(),
            tenant_id=self.human.tenant_id,
            matter_id=self.matter_id,
            conflict_check_id=uuid4(),
            disposition=ConflictDisposition.HOLD,
            decided_by_principal_id=uuid4(),
            decided_at=T0 - timedelta(days=1),
        )
        expired_waiver = ConflictDecision(
            decision_id=uuid4(),
            tenant_id=self.human.tenant_id,
            matter_id=self.matter_id,
            conflict_check_id=uuid4(),
            disposition=ConflictDisposition.WAIVED,
            waiver_reference=WaiverReference(
                waiver_id=uuid4(),
                artifact_id=uuid4(),
                artifact_version=1,
                content_sha256="b" * 64,
                tenant_id=self.human.tenant_id,
                matter_id=self.matter_id,
                valid_from=T0 - timedelta(days=2),
                valid_to=T0,
            ),
            decided_by_principal_id=uuid4(),
            decided_at=T0 - timedelta(days=1),
        )
        for changes, expected in (
            ({"conflict_state_complete": False}, PolicyReason.CONFLICT_UNRESOLVED),
            ({"conflict_decision": None}, PolicyReason.CONFLICT_UNRESOLVED),
            ({"conflict_decision": hold}, PolicyReason.CONFLICT_HOLD),
            ({"conflict_decision": expired_waiver}, PolicyReason.WAIVER_INVALID),
        ):
            with self.subTest(expected=expected):
                decision = engine.decide_retention(request, self.facts(**changes))
                self.assertFalse(decision.allow)
                self.assertEqual(expected, decision.reason)

        valid_waiver = ConflictDecision(
            decision_id=uuid4(),
            tenant_id=self.human.tenant_id,
            matter_id=self.matter_id,
            conflict_check_id=uuid4(),
            disposition=ConflictDisposition.WAIVED,
            waiver_reference=WaiverReference(
                waiver_id=uuid4(),
                artifact_id=uuid4(),
                artifact_version=2,
                content_sha256="c" * 64,
                tenant_id=self.human.tenant_id,
                matter_id=self.matter_id,
                valid_from=T0 - timedelta(days=2),
                valid_to=T0 + timedelta(days=1),
            ),
            decided_by_principal_id=uuid4(),
            decided_at=T0 - timedelta(days=1),
        )
        self.assertTrue(
            engine.decide_retention(
                request,
                self.facts(conflict_decision=valid_waiver),
            ).allow
        )

    def test_retention_requires_current_active_tenant_and_matter_membership(
        self,
    ) -> None:
        from sklegal_policies import PolicyEngine

        request = RetentionRequest(
            principal_id=self.human.principal_id,
            tenant_id=self.human.tenant_id,
            matter_id=self.matter_id,
            material_id=MATERIAL_ID,
            material_version=1,
            material_sha256=RESOURCE_DIGEST,
            material_created_at=T0 - timedelta(days=31),
            evaluated_at=T0,
        )
        engine = PolicyEngine()
        for changes, expected in (
            (
                {"tenant_membership": AccessState.UNKNOWN},
                PolicyReason.MEMBERSHIP_UNKNOWN,
            ),
            (
                {"matter_membership": AccessState.UNKNOWN},
                PolicyReason.MEMBERSHIP_UNKNOWN,
            ),
            (
                {"tenant_membership": AccessState.INACTIVE},
                PolicyReason.TENANT_MEMBERSHIP_REQUIRED,
            ),
            (
                {"matter_membership": AccessState.INACTIVE},
                PolicyReason.MATTER_MEMBERSHIP_REQUIRED,
            ),
        ):
            with self.subTest(engine_reason=expected):
                decision = engine.decide_retention(request, self.facts(**changes))
                self.assertFalse(decision.allow)
                self.assertEqual(expected, decision.reason)
        self.assertTrue(engine.decide_retention(request, self.facts()).allow)

        requirement = RetentionBoundaryRequirement(target="api:retention.evaluate")

        def evaluate_with(backend: object) -> object:
            grant = requirement.bind(request)
            authorized = self.rig.authorize(
                self.human,
                grant,
                self.rig.issue(self.human, grant),
            )
            gateway = RetentionGateway(
                backend=backend,  # type: ignore[arg-type]
                audit_sink=InMemoryPolicyAuditSink(),
                current_authorization=self.current_verifier(),
                requirement=requirement,
                clock=self.rig.clock,
            )
            return gateway.evaluate(authorized, request)

        for changes, expected in (
            (
                {"tenant_membership": AccessState.UNKNOWN},
                PolicyReason.MEMBERSHIP_UNKNOWN,
            ),
            (
                {"matter_membership": AccessState.UNKNOWN},
                PolicyReason.MEMBERSHIP_UNKNOWN,
            ),
            (
                {"tenant_membership": AccessState.INACTIVE},
                PolicyReason.TENANT_MEMBERSHIP_REQUIRED,
            ),
            (
                {"matter_membership": AccessState.INACTIVE},
                PolicyReason.MATTER_MEMBERSHIP_REQUIRED,
            ),
        ):
            with self.subTest(gateway_reason=expected):
                with self.assertRaises(RetentionDenied) as raised:
                    evaluate_with(CountingPolicyBackend(self.facts(**changes)))
                self.assertEqual(expected, raised.exception.decision.reason)

        missing = self.facts().model_dump(mode="python")
        del missing["matter_membership"]
        with self.assertRaises(RetentionDenied) as raised:
            evaluate_with(
                PostgresPolicyBackend(lambda _statement, _parameters: missing)
            )
        self.assertEqual(
            PolicyReason.POLICY_UNAVAILABLE,
            raised.exception.decision.reason,
        )

        with self.assertRaises(RetentionDenied) as raised:
            evaluate_with(UnavailablePolicyBackend("synthetic membership outage"))
        self.assertEqual(
            PolicyReason.POLICY_UNAVAILABLE,
            raised.exception.decision.reason,
        )
        active = evaluate_with(CountingPolicyBackend(self.facts()))
        self.assertTrue(active.allow)  # type: ignore[attr-defined]

    def test_retention_gateway_reloads_uses_trusted_time_and_audits(self) -> None:
        requirement = RetentionBoundaryRequirement(target="api:retention.evaluate")
        caller_request = RetentionRequest(
            principal_id=self.human.principal_id,
            tenant_id=self.human.tenant_id,
            matter_id=self.matter_id,
            material_id=MATERIAL_ID,
            material_version=1,
            material_sha256=RESOURCE_DIGEST,
            material_created_at=T0 - timedelta(days=1),
            evaluated_at=T0 + timedelta(days=100),
        )
        grant = requirement.bind(caller_request)
        authorized = self.rig.authorize(
            self.human,
            grant,
            self.rig.issue(self.human, grant),
        )
        backend = CountingPolicyBackend(self.facts())
        audit = InMemoryPolicyAuditSink()
        gateway = RetentionGateway(
            backend=backend,
            audit_sink=audit,
            current_authorization=self.current_verifier(),
            requirement=requirement,
            clock=self.rig.clock,
        )
        with self.assertRaises(RetentionDenied) as raised:
            gateway.evaluate(authorized, caller_request)
        self.assertEqual(
            PolicyReason.RETENTION_NOT_DUE, raised.exception.decision.reason
        )
        self.assertEqual(T0, raised.exception.decision.evaluated_at)
        self.assertEqual(1, backend.calls)
        self.assertEqual((raised.exception.decision,), audit.decisions())

        failing_clock_gateway = RetentionGateway(
            backend=backend,
            audit_sink=audit,
            current_authorization=self.current_verifier(),
            requirement=requirement,
            clock=FailingClock(),
        )
        fresh = self.rig.authorize(
            self.human,
            grant,
            self.rig.issue(self.human, grant),
        )
        with self.assertRaises(RetentionDenied) as clock_denied:
            failing_clock_gateway.evaluate(fresh, caller_request)
        self.assertEqual(
            PolicyReason.POLICY_UNAVAILABLE,
            clock_denied.exception.decision.reason,
        )

        audit_outage_gateway = RetentionGateway(
            backend=backend,
            audit_sink=UnavailablePolicyAuditSink(),
            current_authorization=self.current_verifier(),
            requirement=requirement,
            clock=self.rig.clock,
        )
        fresh = self.rig.authorize(
            self.human,
            grant,
            self.rig.issue(self.human, grant),
        )
        with self.assertRaises(RetentionDenied) as audit_denied:
            audit_outage_gateway.evaluate(fresh, caller_request)
        self.assertEqual(
            PolicyReason.AUDIT_UNAVAILABLE,
            audit_denied.exception.decision.reason,
        )

        backend_outage_gateway = RetentionGateway(
            backend=UnavailablePolicyBackend("synthetic retention backend detail"),
            audit_sink=InMemoryPolicyAuditSink(),
            current_authorization=self.current_verifier(),
            requirement=requirement,
            clock=self.rig.clock,
        )
        fresh = self.rig.authorize(
            self.human,
            grant,
            self.rig.issue(self.human, grant),
        )
        with self.assertRaises(RetentionDenied) as backend_denied:
            backend_outage_gateway.evaluate(fresh, caller_request)
        self.assertEqual(
            PolicyReason.POLICY_UNAVAILABLE,
            backend_denied.exception.decision.reason,
        )
        self.assertNotIn(
            "synthetic retention backend detail",
            backend_denied.exception.decision.model_dump_json(),
        )


if __name__ == "__main__":
    unittest.main()
