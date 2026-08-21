from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from sklegal_capauth import (
    ApiCapabilityBoundary,
    Audience,
    Capability,
    ModelCapabilityBoundary,
    ModelRoute,
    PrincipalType,
    Purpose,
)
from sklegal_domain import DataClassification
from sklegal_policies import (
    AccessState,
    CapAuthCurrentStateVerifier,
    ClassificationSource,
    ConflictDecision,
    ConflictDisposition,
    ConflictHold,
    DataFlowBoundary,
    EthicalWall,
    InMemoryAuthorizationUseBackend,
    InMemoryPolicyAuditSink,
    InMemoryPolicyBackend,
    LegalHold,
    LegalHoldStatus,
    MaterialPolicyFacts,
    PolicyAccessRequest,
    PolicyBoundaryRequirement,
    PolicyDenied,
    PolicyGateway,
    PolicyReason,
    PostgresPolicyBackend,
    ProtectedAccessGrant,
    ProtectedAccessLevel,
    ProtectedDataFlow,
    RetentionPolicy,
    UnavailablePolicyAuditSink,
    UnavailablePolicyBackend,
    WaiverReference,
    WallMembership,
    WallMembershipDisposition,
    WorkProductLabel,
    effective_classification,
)

from tests.support.capauth_contract import (
    RESOURCE_DIGEST,
    WORKFLOW_RUN_ID,
    CapabilityTestRig,
    boundary_scope,
)

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
TENANT_ID = UUID("a2000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("a2000000-0000-4000-8000-000000000002")
MATERIAL_ID = UUID("a2000000-0000-4000-8000-000000000003")
REVISION = "a" * 64


class PolicyEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = CapabilityTestRig()
        self.principal = self.rig.principal()
        self.grant = self.rig.grant(
            tenant_id=self.principal.tenant_id,
            capability=Capability.EVIDENCE_READ,
            purpose=Purpose.EVIDENCE_REVIEW,
            target="api:material.retrieve",
            resource_id=str(MATERIAL_ID),
            resource_version=1,
            resource_sha256=RESOURCE_DIGEST,
        )
        assert self.grant.matter_id is not None
        self.matter_id = self.grant.matter_id
        self.requirement = self.requirement_for(DataFlowBoundary.RETRIEVAL)
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=self.rig.authorizer,
            route_name="material.retrieve",
            capability=Capability.EVIDENCE_READ,
            purpose=Purpose.EVIDENCE_REVIEW,
        )
        self.authorized = boundary.authorize(
            principal=self.principal,
            scope=boundary_scope(self.grant),
            correlation_id=uuid4(),
            presented=self.rig.issue(self.principal, self.grant),
        )

    def tearDown(self) -> None:
        self.rig.close()

    def request(
        self,
        boundary: DataFlowBoundary = DataFlowBoundary.RETRIEVAL,
    ) -> PolicyAccessRequest:
        requirement = self.requirement_for(boundary)
        return PolicyAccessRequest(
            boundary=boundary,
            principal_id=self.principal.principal_id,
            tenant_id=self.principal.tenant_id,
            matter_id=self.matter_id,
            material_id=MATERIAL_ID,
            material_version=1,
            material_sha256=RESOURCE_DIGEST,
            purpose=requirement.purpose,
            model_route=requirement.model_route,
            workflow_run_id=requirement.workflow_run_id,
            evaluated_at=T0,
        )

    @staticmethod
    def requirement_for(
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

    def current_authorization(self) -> CapAuthCurrentStateVerifier:
        return CapAuthCurrentStateVerifier(
            trusted_issuers=self.rig.trusted,
            principals=self.rig.principals,
            revocations=self.rig.revocations,
            uses=InMemoryAuthorizationUseBackend(),
        )

    def facts(self, **changes: object) -> MaterialPolicyFacts:
        payload: dict[str, object] = {
            "policy_revision": REVISION,
            "tenant_id": self.principal.tenant_id,
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
                tenant_id=self.principal.tenant_id,
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
            "legal_hold_state_complete": True,
            "ownership_resolved": True,
            "pending_export": False,
            "preservation_required": False,
        }
        payload.update(changes)
        return MaterialPolicyFacts.model_validate(payload)

    def gateway(
        self,
        facts: MaterialPolicyFacts,
    ) -> tuple[PolicyGateway, InMemoryPolicyAuditSink]:
        audit = InMemoryPolicyAuditSink()
        backend = InMemoryPolicyBackend({MATERIAL_ID: facts})
        return (
            PolicyGateway(
                backend=backend,
                audit_sink=audit,
                current_authorization=self.current_authorization(),
                clock=lambda: T0,
            ),
            audit,
        )

    def test_happy_path_requires_capauth_and_every_policy_gate(self) -> None:
        gateway, audit = self.gateway(self.facts())
        context = gateway.authorize(
            self.authorized,
            self.requirement,
            self.request(),
        )
        self.assertTrue(context.decision.allow)
        self.assertEqual(PolicyReason.ALLOW, context.decision.reason)
        self.assertEqual(DataClassification.CONFIDENTIAL, context.classification)
        self.assertEqual(64, len(context.cache_partition_key))
        self.assertEqual((context.decision,), audit.decisions())

    def test_conflict_hold_and_invalid_waiver_fail_closed(self) -> None:
        hold = ConflictHold(
            hold_id=uuid4(),
            tenant_id=self.principal.tenant_id,
            matter_id=self.matter_id,
            conflict_decision_id=uuid4(),
            reason_code="adverse_party_collision",
            effective_from=T0 - timedelta(days=2),
        )
        for facts, reason in (
            (
                self.facts(conflict_holds=(hold,)),
                PolicyReason.CONFLICT_HOLD,
            ),
            (
                self.facts(
                    conflict_decision=ConflictDecision(
                        decision_id=uuid4(),
                        tenant_id=self.principal.tenant_id,
                        matter_id=self.matter_id,
                        conflict_check_id=uuid4(),
                        disposition=ConflictDisposition.WAIVED,
                        waiver_reference=WaiverReference(
                            waiver_id=uuid4(),
                            artifact_id=uuid4(),
                            artifact_version=2,
                            content_sha256="b" * 64,
                            tenant_id=self.principal.tenant_id,
                            matter_id=self.matter_id,
                            valid_from=T0 - timedelta(days=2),
                            valid_to=T0,
                        ),
                        decided_by_principal_id=uuid4(),
                        decided_at=T0 - timedelta(days=1),
                    )
                ),
                PolicyReason.WAIVER_INVALID,
            ),
        ):
            with self.subTest(reason=reason):
                gateway, _ = self.gateway(facts)
                with self.assertRaises(PolicyDenied) as raised:
                    gateway.authorize(
                        self.authorized,
                        self.requirement,
                        self.request(),
                    )
                self.assertEqual(reason, raised.exception.decision.reason)

    def test_wall_requires_an_active_explicit_membership_and_honors_exclusion(
        self,
    ) -> None:
        wall_id = uuid4()
        wall = EthicalWall(
            wall_id=wall_id,
            tenant_id=self.principal.tenant_id,
            matter_id=self.matter_id,
            name="synthetic-wall",
            active=True,
            membership_complete=True,
            effective_from=T0 - timedelta(days=1),
        )
        cases = (
            ((), PolicyReason.WALL_GRANT_REQUIRED),
            (
                (
                    WallMembership(
                        wall_id=wall_id,
                        tenant_id=self.principal.tenant_id,
                        matter_id=self.matter_id,
                        principal_id=self.principal.principal_id,
                        disposition=WallMembershipDisposition.EXCLUDED,
                        effective_from=T0 - timedelta(hours=1),
                    ),
                ),
                PolicyReason.WALL_EXCLUDED,
            ),
        )
        for memberships, reason in cases:
            with self.subTest(reason=reason):
                gateway, _ = self.gateway(
                    self.facts(walls=(wall,), wall_memberships=memberships)
                )
                with self.assertRaises(PolicyDenied) as raised:
                    gateway.authorize(
                        self.authorized,
                        self.requirement,
                        self.request(),
                    )
                self.assertEqual(reason, raised.exception.decision.reason)

        allowed_membership = WallMembership(
            wall_id=wall_id,
            tenant_id=self.principal.tenant_id,
            matter_id=self.matter_id,
            principal_id=self.principal.principal_id,
            disposition=WallMembershipDisposition.ALLOWED,
            effective_from=T0 - timedelta(hours=1),
        )
        gateway, _ = self.gateway(
            self.facts(walls=(wall,), wall_memberships=(allowed_membership,))
        )
        self.assertTrue(
            gateway.authorize(
                self.authorized,
                self.requirement,
                self.request(),
            ).decision.allow
        )

    def test_classification_inherits_most_restrictive_source_and_labels(self) -> None:
        label = WorkProductLabel(
            label_id=uuid4(),
            tenant_id=self.principal.tenant_id,
            matter_id=self.matter_id,
            material_id=MATERIAL_ID,
            label="attorney_work_product",
            active=True,
            labeled_by_principal_id=uuid4(),
            labeled_at=T0 - timedelta(days=1),
        )
        sources = (
            ClassificationSource(
                source_kind="tenant",
                source_id=self.principal.tenant_id,
                classification=DataClassification.INTERNAL,
            ),
            ClassificationSource(
                source_kind="source_artifact",
                source_id=uuid4(),
                classification=DataClassification.CONFIDENTIAL,
            ),
        )
        self.assertEqual(
            DataClassification.PRIVILEGED_WORK_PRODUCT,
            effective_classification(sources, work_product_labels=(label,)),
        )
        gateway, _ = self.gateway(
            self.facts(classification_sources=sources, work_product_labels=(label,))
        )
        with self.assertRaises(PolicyDenied) as raised:
            gateway.authorize(
                self.authorized,
                self.requirement,
                self.request(),
            )
        self.assertEqual(
            PolicyReason.PROTECTED_ACCESS_REQUIRED,
            raised.exception.decision.reason,
        )

        grant = ProtectedAccessGrant(
            grant_id=uuid4(),
            tenant_id=self.principal.tenant_id,
            matter_id=self.matter_id,
            principal_id=self.principal.principal_id,
            access_level=ProtectedAccessLevel.PRIVILEGED_WORK_PRODUCT,
            purpose=Purpose.EVIDENCE_REVIEW,
            granted_by_principal_id=uuid4(),
            effective_from=T0 - timedelta(hours=1),
        )
        gateway, _ = self.gateway(
            self.facts(
                classification_sources=sources,
                work_product_labels=(label,),
                protected_access_grants=(grant,),
            )
        )
        self.assertTrue(
            gateway.authorize(
                self.authorized,
                self.requirement,
                self.request(),
            ).decision.allow
        )

    def test_model_and_audit_detail_apply_boundary_specific_classification_rules(
        self,
    ) -> None:
        privileged = ClassificationSource(
            source_kind="record",
            source_id=MATERIAL_ID,
            classification=DataClassification.PRIVILEGED_WORK_PRODUCT,
        )

        agent = self.rig.principal(PrincipalType.AGENT)
        model_grant = self.rig.grant(
            audience=Audience.MODEL,
            capability=Capability.EVIDENCE_READ,
            purpose=Purpose.EVIDENCE_REVIEW,
            target="model:qwen.generate",
            tenant_id=agent.tenant_id,
            matter_id=self.matter_id,
            resource_id=str(MATERIAL_ID),
            resource_version=1,
            resource_sha256=RESOURCE_DIGEST,
        )
        model_boundary: ModelCapabilityBoundary[object] = ModelCapabilityBoundary(
            authorizer=self.rig.authorizer,
            model_target="qwen.generate",
            capability=Capability.EVIDENCE_READ,
            purpose=Purpose.EVIDENCE_REVIEW,
        )
        model_authorized = model_boundary.authorize(
            principal=agent,
            scope=boundary_scope(model_grant),
            correlation_id=uuid4(),
            presented=self.rig.issue(agent, model_grant),
        )
        model_access = ProtectedAccessGrant(
            grant_id=uuid4(),
            tenant_id=agent.tenant_id,
            matter_id=self.matter_id,
            principal_id=agent.principal_id,
            access_level=ProtectedAccessLevel.PRIVILEGED_WORK_PRODUCT,
            purpose=Purpose.EVIDENCE_REVIEW,
            granted_by_principal_id=uuid4(),
            effective_from=T0 - timedelta(hours=1),
        )
        model_facts = self.facts(
            classification_sources=(privileged,),
            protected_access_grants=(model_access,),
        )
        model_gateway, _ = self.gateway(model_facts)
        model_request = PolicyAccessRequest(
            boundary=DataFlowBoundary.MODEL_CONTEXT,
            principal_id=agent.principal_id,
            tenant_id=agent.tenant_id,
            matter_id=self.matter_id,
            material_id=MATERIAL_ID,
            material_version=1,
            material_sha256=RESOURCE_DIGEST,
            purpose=Purpose.EVIDENCE_REVIEW,
            model_route=ModelRoute.LOCAL_QWEN,
            workflow_run_id=WORKFLOW_RUN_ID,
            evaluated_at=T0,
        )
        model_requirement = PolicyBoundaryRequirement(
            boundary=DataFlowBoundary.MODEL_CONTEXT,
            audience=Audience.MODEL,
            target="model:qwen.generate",
            capability=Capability.EVIDENCE_READ,
            purpose=Purpose.EVIDENCE_REVIEW,
            model_route=ModelRoute.LOCAL_QWEN,
            workflow_run_id=WORKFLOW_RUN_ID,
        )
        invoked: list[DataFlowBoundary] = []
        model_flow: ProtectedDataFlow[str] = ProtectedDataFlow(
            gateway=model_gateway,
            requirement=model_requirement,
        )
        with self.assertRaises(PolicyDenied) as raised:
            model_flow.invoke(
                authorized=model_authorized,
                request=model_request,
                handler=lambda _: (
                    invoked.append(DataFlowBoundary.MODEL_CONTEXT) or "payload"
                ),
            )
        self.assertEqual(
            PolicyReason.MODEL_CONTEXT_DENIED,
            raised.exception.decision.reason,
        )

        external_grant = self.rig.grant(
            audience=Audience.MODEL,
            capability=Capability.EVIDENCE_READ,
            purpose=Purpose.EVIDENCE_REVIEW,
            target="model:openai.generate",
            tenant_id=agent.tenant_id,
            matter_id=self.matter_id,
            resource_id=str(MATERIAL_ID),
            resource_version=1,
            resource_sha256=RESOURCE_DIGEST,
            model_route=ModelRoute.OPENAI,
        )
        external_boundary: ModelCapabilityBoundary[object] = ModelCapabilityBoundary(
            authorizer=self.rig.authorizer,
            model_target="openai.generate",
            capability=Capability.EVIDENCE_READ,
            purpose=Purpose.EVIDENCE_REVIEW,
        )
        external_authorized = external_boundary.authorize(
            principal=agent,
            scope=boundary_scope(external_grant),
            correlation_id=uuid4(),
            presented=self.rig.issue(agent, external_grant),
        )
        external_gateway, _ = self.gateway(
            self.facts(
                classification_sources=(
                    ClassificationSource(
                        source_kind="record",
                        source_id=MATERIAL_ID,
                        classification=DataClassification.CONFIDENTIAL,
                    ),
                )
            )
        )
        external_request = PolicyAccessRequest(
            boundary=DataFlowBoundary.MODEL_CONTEXT,
            principal_id=agent.principal_id,
            tenant_id=agent.tenant_id,
            matter_id=self.matter_id,
            material_id=MATERIAL_ID,
            material_version=1,
            material_sha256=RESOURCE_DIGEST,
            purpose=Purpose.EVIDENCE_REVIEW,
            model_route=ModelRoute.OPENAI,
            workflow_run_id=WORKFLOW_RUN_ID,
            evaluated_at=T0,
        )
        external_requirement = PolicyBoundaryRequirement(
            boundary=DataFlowBoundary.MODEL_CONTEXT,
            audience=Audience.MODEL,
            target="model:openai.generate",
            capability=Capability.EVIDENCE_READ,
            purpose=Purpose.EVIDENCE_REVIEW,
            model_route=ModelRoute.OPENAI,
            workflow_run_id=WORKFLOW_RUN_ID,
        )
        external_flow: ProtectedDataFlow[str] = ProtectedDataFlow(
            gateway=external_gateway,
            requirement=external_requirement,
        )
        with self.assertRaises(PolicyDenied) as raised:
            external_flow.invoke(
                authorized=external_authorized,
                request=external_request,
                handler=lambda _: (
                    invoked.append(DataFlowBoundary.MODEL_CONTEXT) or "payload"
                ),
            )
        self.assertEqual(
            PolicyReason.EXTERNAL_MODEL_DENIED,
            raised.exception.decision.reason,
        )

        audit_grant = self.rig.grant(
            capability=Capability.AUDIT_READ,
            purpose=Purpose.AUDIT_REVIEW,
            target="api:audit.detail",
            tenant_id=self.principal.tenant_id,
            resource_id=str(MATERIAL_ID),
            resource_version=1,
            resource_sha256=RESOURCE_DIGEST,
        )
        audit_boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=self.rig.authorizer,
            route_name="audit.detail",
            capability=Capability.AUDIT_READ,
            purpose=Purpose.AUDIT_REVIEW,
        )
        audit_authorized = audit_boundary.authorize(
            principal=self.principal,
            scope=boundary_scope(audit_grant),
            correlation_id=uuid4(),
            presented=self.rig.issue(self.principal, audit_grant),
        )
        audit_access = ProtectedAccessGrant(
            grant_id=uuid4(),
            tenant_id=self.principal.tenant_id,
            matter_id=self.matter_id,
            principal_id=self.principal.principal_id,
            access_level=ProtectedAccessLevel.PRIVILEGED_WORK_PRODUCT,
            purpose=Purpose.AUDIT_REVIEW,
            granted_by_principal_id=uuid4(),
            effective_from=T0 - timedelta(hours=1),
        )
        audit_gateway, _ = self.gateway(
            self.facts(
                classification_sources=(privileged,),
                protected_access_grants=(audit_access,),
            )
        )
        audit_request = PolicyAccessRequest(
            boundary=DataFlowBoundary.AUDIT_DETAIL,
            principal_id=self.principal.principal_id,
            tenant_id=self.principal.tenant_id,
            matter_id=self.matter_id,
            material_id=MATERIAL_ID,
            material_version=1,
            material_sha256=RESOURCE_DIGEST,
            purpose=Purpose.AUDIT_REVIEW,
            evaluated_at=T0,
        )
        audit_requirement = PolicyBoundaryRequirement(
            boundary=DataFlowBoundary.AUDIT_DETAIL,
            audience=Audience.API,
            target="api:audit.detail",
            capability=Capability.AUDIT_READ,
            purpose=Purpose.AUDIT_REVIEW,
        )
        audit_flow: ProtectedDataFlow[str] = ProtectedDataFlow(
            gateway=audit_gateway,
            requirement=audit_requirement,
        )
        with self.assertRaises(PolicyDenied) as raised:
            audit_flow.invoke(
                authorized=audit_authorized,
                request=audit_request,
                handler=lambda _: (
                    invoked.append(DataFlowBoundary.AUDIT_DETAIL) or "payload"
                ),
            )
        self.assertEqual(
            PolicyReason.AUDIT_DETAIL_DENIED,
            raised.exception.decision.reason,
        )
        self.assertEqual([], invoked)

    def test_profile_ownership_is_never_membership_or_authority(self) -> None:
        facts = self.facts(
            matter_membership=AccessState.INACTIVE,
            profile_owner_principal_id=self.principal.principal_id,
        )
        gateway, _ = self.gateway(facts)
        with self.assertRaises(PolicyDenied) as raised:
            gateway.authorize(
                self.authorized,
                self.requirement,
                self.request(),
            )
        self.assertEqual(
            PolicyReason.MATTER_MEMBERSHIP_REQUIRED,
            raised.exception.decision.reason,
        )

    def test_policy_backend_outage_is_sanitized_and_fails_closed(self) -> None:
        audit = InMemoryPolicyAuditSink()
        gateway = PolicyGateway(
            backend=UnavailablePolicyBackend("synthetic secret detail"),
            audit_sink=audit,
            current_authorization=self.current_authorization(),
            clock=lambda: T0,
        )
        with self.assertRaises(PolicyDenied) as raised:
            gateway.authorize(
                self.authorized,
                self.requirement,
                self.request(),
            )
        rendered = raised.exception.decision.model_dump_json()
        self.assertEqual(
            PolicyReason.POLICY_UNAVAILABLE, raised.exception.decision.reason
        )
        self.assertNotIn("synthetic secret detail", rendered)
        self.assertNotIn("Synthetic", str(raised.exception))

    def test_audit_outage_denies_before_payload_use(self) -> None:
        gateway = PolicyGateway(
            backend=InMemoryPolicyBackend({MATERIAL_ID: self.facts()}),
            audit_sink=UnavailablePolicyAuditSink(),
            current_authorization=self.current_authorization(),
            clock=lambda: T0,
        )
        with self.assertRaises(PolicyDenied) as raised:
            gateway.authorize(
                self.authorized,
                self.requirement,
                self.request(),
            )
        self.assertEqual(
            PolicyReason.AUDIT_UNAVAILABLE,
            raised.exception.decision.reason,
        )

    def test_gateway_uses_trusted_clock_and_rechecks_after_snapshot_load(self) -> None:
        waiver = WaiverReference(
            waiver_id=uuid4(),
            artifact_id=uuid4(),
            artifact_version=1,
            content_sha256="f" * 64,
            tenant_id=self.principal.tenant_id,
            matter_id=self.matter_id,
            valid_from=T0 - timedelta(days=1),
            valid_to=T0,
        )
        facts = self.facts(
            conflict_decision=ConflictDecision(
                decision_id=uuid4(),
                tenant_id=self.principal.tenant_id,
                matter_id=self.matter_id,
                conflict_check_id=uuid4(),
                disposition=ConflictDisposition.WAIVED,
                waiver_reference=waiver,
                decided_by_principal_id=uuid4(),
                decided_at=T0 - timedelta(days=1),
            )
        )
        clock_values = iter((T0 - timedelta(microseconds=1), T0))
        gateway = PolicyGateway(
            backend=InMemoryPolicyBackend({MATERIAL_ID: facts}),
            audit_sink=InMemoryPolicyAuditSink(),
            current_authorization=self.current_authorization(),
            clock=lambda: next(clock_values),
        )
        stale_caller_time = PolicyAccessRequest.model_validate(
            {
                **self.request().model_dump(mode="python"),
                "evaluated_at": T0 - timedelta(hours=1),
            }
        )
        with self.assertRaises(PolicyDenied) as raised:
            gateway.authorize(
                self.authorized,
                self.requirement,
                stale_caller_time,
            )
        self.assertEqual(PolicyReason.WAIVER_INVALID, raised.exception.decision.reason)
        self.assertEqual(T0, raised.exception.decision.evaluated_at)

    def test_postgres_backend_uses_one_parameterized_sanitized_snapshot(self) -> None:
        expected = self.facts()
        calls: list[tuple[str, tuple[object, ...]]] = []

        def execute(statement: str, parameters: tuple[object, ...]) -> str:
            calls.append((statement, parameters))
            return expected.model_dump_json()

        backend = PostgresPolicyBackend(execute)  # type: ignore[arg-type]
        actual = backend.load(self.request())
        self.assertEqual(expected, actual)
        self.assertEqual(1, len(calls))
        statement, parameters = calls[0]
        self.assertNotIn(str(MATERIAL_ID), statement)
        self.assertNotIn(str(self.principal.principal_id), statement)
        self.assertEqual(MATERIAL_ID, parameters[2])
        self.assertEqual(self.principal.principal_id, parameters[4])

    def test_denial_stops_every_data_flow_before_payload_or_cache_access(self) -> None:
        calls: list[DataFlowBoundary] = []
        facts = self.facts(conflict_decision=None)
        gateway, audit = self.gateway(facts)
        for boundary in DataFlowBoundary:
            with self.subTest(boundary=boundary):
                requirement = self.requirement_for(boundary)
                flow: ProtectedDataFlow[str] = ProtectedDataFlow(
                    gateway=gateway,
                    requirement=requirement,
                )
                with self.assertRaises(PolicyDenied):
                    flow.invoke(
                        authorized=self.authorized,
                        request=self.request(boundary),
                        handler=lambda _: calls.append(boundary) or "payload",
                    )
        self.assertEqual([], calls)
        self.assertEqual(len(DataFlowBoundary), len(audit.decisions()))
        for decision in audit.decisions():
            serialized = decision.model_dump()
            self.assertNotIn("content", serialized)
            self.assertNotIn("display_name", serialized)
            self.assertNotIn("capability", serialized)

    def test_strict_scope_and_unknown_policy_state_are_denied(self) -> None:
        gateway, _ = self.gateway(self.facts(tenant_membership=AccessState.UNKNOWN))
        with self.assertRaises(PolicyDenied) as raised:
            gateway.authorize(
                self.authorized,
                self.requirement,
                self.request(),
            )
        self.assertEqual(
            PolicyReason.MEMBERSHIP_UNKNOWN, raised.exception.decision.reason
        )
        with self.assertRaises(ValidationError):
            PolicyAccessRequest.model_validate(
                {**self.request().model_dump(mode="python"), "extra": "forbidden"}
            )
        with self.assertRaisesRegex(ValidationError, "nil UUID"):
            PolicyAccessRequest.model_validate(
                {
                    **self.request().model_dump(mode="python"),
                    "material_id": UUID(int=0),
                }
            )
        with self.assertRaisesRegex(ValueError, "unvalidated policy"):
            self.facts().model_copy(update={"matter_membership": AccessState.ACTIVE})
        with self.assertRaisesRegex(ValueError, "unvalidated policy"):
            MaterialPolicyFacts.model_construct()


class RetentionPolicyTests(unittest.TestCase):
    def facts(self, **changes: object) -> MaterialPolicyFacts:
        payload: dict[str, object] = {
            "policy_revision": REVISION,
            "tenant_id": TENANT_ID,
            "matter_id": MATTER_ID,
            "material_id": MATERIAL_ID,
            "material_version": 1,
            "tenant_membership": AccessState.ACTIVE,
            "matter_membership": AccessState.ACTIVE,
            "conflict_state_complete": True,
            "wall_state_complete": True,
            "classification_state_complete": True,
            "conflict_decision": ConflictDecision(
                decision_id=uuid4(),
                tenant_id=TENANT_ID,
                matter_id=MATTER_ID,
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
                tenant_id=TENANT_ID,
                matter_id=MATTER_ID,
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

    def test_retention_eligibility_is_not_deletion_and_every_hold_pauses_it(
        self,
    ) -> None:
        from sklegal_policies import PolicyEngine, RetentionRequest

        engine = PolicyEngine()
        request = RetentionRequest(
            principal_id=UUID("a2000000-0000-4000-8000-000000000004"),
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            material_id=MATERIAL_ID,
            material_version=1,
            material_sha256=RESOURCE_DIGEST,
            material_created_at=T0 - timedelta(days=31),
            evaluated_at=T0,
        )
        allowed = engine.decide_retention(request, self.facts())
        self.assertTrue(allowed.allow)
        self.assertTrue(allowed.eligible_only)

        hold = LegalHold(
            legal_hold_id=uuid4(),
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            status=LegalHoldStatus.ACTIVE,
            scope="matter",
            issued_by_principal_id=uuid4(),
            effective_from=T0 - timedelta(days=2),
        )
        cases = (
            (self.facts(legal_holds=(hold,)), PolicyReason.LEGAL_HOLD_ACTIVE),
            (
                self.facts(legal_hold_state_complete=False),
                PolicyReason.HOLD_STATE_UNKNOWN,
            ),
            (self.facts(pending_export=True), PolicyReason.PENDING_EXPORT),
            (self.facts(ownership_resolved=False), PolicyReason.OWNERSHIP_UNRESOLVED),
            (
                self.facts(preservation_required=True),
                PolicyReason.PRESERVATION_REQUIRED,
            ),
        )
        for facts, reason in cases:
            with self.subTest(reason=reason):
                decision = engine.decide_retention(request, facts)
                self.assertFalse(decision.allow)
                self.assertEqual(reason, decision.reason)

        release = LegalHold(
            legal_hold_id=uuid4(),
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            status=LegalHoldStatus.RELEASED,
            scope="matter",
            issued_by_principal_id=hold.issued_by_principal_id,
            effective_from=hold.effective_from,
            supersedes_hold_id=hold.legal_hold_id,
            released_by_principal_id=uuid4(),
            released_at=T0 - timedelta(days=1),
        )
        released = engine.decide_retention(
            request,
            self.facts(legal_holds=(hold, release)),
        )
        self.assertTrue(released.allow)
        self.assertTrue(released.eligible_only)


if __name__ == "__main__":
    unittest.main()
