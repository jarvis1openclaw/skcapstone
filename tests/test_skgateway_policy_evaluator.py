from __future__ import annotations

from unittest.mock import create_autospec
from uuid import UUID, uuid4

import pytest
from sklegal_api.skgateway_authz import (
    CanonicalSkGatewayPolicyEvaluator,
    SkGatewayAuthorizationDependencyUnavailable,
    SkGatewayScopeDenied,
)
from sklegal_capauth import (
    Audience,
    Capability,
    ModelCapabilityBoundary,
    ModelRoute,
    PrincipalType,
    Purpose,
)
from sklegal_domain import DataClassification
from sklegal_policies import (
    DataFlowBoundary,
    PolicyAuthorizedContext,
    PolicyDecision,
    PolicyDenied,
    PolicyGateway,
    PolicyReason,
)

from tests.support.capauth_contract import (
    RESOURCE_DIGEST,
    WORKFLOW_RUN_ID,
    CapabilityTestRig,
    boundary_scope,
)

MATERIAL_ID = UUID("a3000000-0000-4000-8000-000000000003")
POLICY_REVISION = "b" * 64


@pytest.fixture
def authorized_context():  # type: ignore[no-untyped-def]
    rig = CapabilityTestRig()
    principal = rig.principal(
        PrincipalType.AGENT,
        subject="agent:skgateway-worker",
    )
    grant = rig.grant(
        audience=Audience.MODEL,
        target="model:qwen.generate",
        capability=Capability.CORPUS_ARTIFACT_READ,
        purpose=Purpose.LEGAL_RESEARCH,
        tenant_id=principal.tenant_id,
        resource_id=str(MATERIAL_ID),
        resource_version=7,
        resource_sha256=RESOURCE_DIGEST,
        model_route=ModelRoute.LOCAL_QWEN,
        workflow_run_id=WORKFLOW_RUN_ID,
    )
    boundary: ModelCapabilityBoundary[object] = ModelCapabilityBoundary(
        authorizer=rig.authorizer,
        model_target="qwen.generate",
        capability=Capability.CORPUS_ARTIFACT_READ,
        purpose=Purpose.LEGAL_RESEARCH,
    )
    authorized = boundary.authorize(
        principal=principal,
        scope=boundary_scope(grant),
        correlation_id=uuid4(),
        presented=rig.issue(principal, grant),
    )
    yield authorized
    rig.close()


def _wire_scope(authorized_context):  # type: ignore[no-untyped-def]
    grant = authorized_context.grant
    return {
        "subject": authorized_context.principal.subject,
        "capability": "skgateway.infer",
        "resource": {
            "tenant_id": str(grant.tenant_id),
            "matter_id": str(grant.matter_id),
            "material_id": str(grant.resource_id),
            "material_version": str(grant.resource_version),
            "route_id": "qwen3-local-primary",
        },
        "context": {
            "purpose": grant.purpose.value,
            "classification": "internal",
            "privilege": "none",
            "ethical_wall": "clear",
        },
        "authorized": authorized_context,
    }


def _decision(authorized_context, *, reason: PolicyReason) -> PolicyDecision:  # type: ignore[no-untyped-def]
    grant = authorized_context.grant
    return PolicyDecision(
        capauth_decision_id=authorized_context.decision.decision_id,
        correlation_id=authorized_context.decision.correlation_id,
        principal_id=authorized_context.principal.principal_id,
        tenant_id=grant.tenant_id,
        matter_id=grant.matter_id,
        material_id=MATERIAL_ID,
        material_version=7,
        boundary=DataFlowBoundary.MODEL_CONTEXT,
        allow=reason == PolicyReason.ALLOW,
        reason=reason,
        effective_classification=(
            DataClassification.INTERNAL if reason == PolicyReason.ALLOW else None
        ),
        policy_revision=POLICY_REVISION,
        evaluated_at=authorized_context.credential_expires_at,
    )


def test_canonical_evaluator_binds_capauth_grant_to_policy_gateway(
    authorized_context,
) -> None:  # type: ignore[no-untyped-def]
    gateway = create_autospec(PolicyGateway, instance=True)
    decision = _decision(authorized_context, reason=PolicyReason.ALLOW)
    gateway.authorize.return_value = PolicyAuthorizedContext(
        decision=decision,
        classification=DataClassification.INTERNAL,
        cache_partition_key="c" * 64,
    )

    result = CanonicalSkGatewayPolicyEvaluator(gateway).decide(
        **_wire_scope(authorized_context)
    )

    assert result.allow is True
    assert result.decision_id == str(decision.decision_id)
    requirement = gateway.authorize.call_args.args[1]
    request = gateway.authorize.call_args.args[2]
    assert requirement.boundary == DataFlowBoundary.MODEL_CONTEXT
    assert requirement.target == authorized_context.grant.target
    assert request.material_sha256 == RESOURCE_DIGEST
    assert request.workflow_run_id == WORKFLOW_RUN_ID


@pytest.mark.parametrize(
    ("policy_reason", "wire_reason"),
    [
        (PolicyReason.WALL_EXCLUDED, "policy_denied"),
        (PolicyReason.CAPAUTH_REPLAYED, "capability_denied"),
    ],
)
def test_canonical_evaluator_returns_sanitized_denial(
    authorized_context,
    policy_reason: PolicyReason,
    wire_reason: str,
) -> None:  # type: ignore[no-untyped-def]
    gateway = create_autospec(PolicyGateway, instance=True)
    decision = _decision(authorized_context, reason=policy_reason)
    gateway.authorize.side_effect = PolicyDenied(decision)

    result = CanonicalSkGatewayPolicyEvaluator(gateway).decide(
        **_wire_scope(authorized_context)
    )

    assert result.allow is False
    assert result.reason == wire_reason
    assert result.policy_revision == POLICY_REVISION


@pytest.mark.parametrize(
    "policy_reason",
    [
        PolicyReason.AUDIT_UNAVAILABLE,
        PolicyReason.CAPAUTH_CURRENT_STATE_UNAVAILABLE,
        PolicyReason.POLICY_UNAVAILABLE,
    ],
)
def test_canonical_evaluator_raises_sanitized_dependency_outage(
    authorized_context,
    policy_reason: PolicyReason,
) -> None:  # type: ignore[no-untyped-def]
    gateway = create_autospec(PolicyGateway, instance=True)
    gateway.authorize.side_effect = PolicyDenied(
        _decision(authorized_context, reason=policy_reason)
    )

    with pytest.raises(SkGatewayAuthorizationDependencyUnavailable):
        CanonicalSkGatewayPolicyEvaluator(gateway).decide(
            **_wire_scope(authorized_context)
        )


def test_canonical_evaluator_rejects_scope_mismatch_before_policy(
    authorized_context,
) -> None:  # type: ignore[no-untyped-def]
    gateway = create_autospec(PolicyGateway, instance=True)
    wire = _wire_scope(authorized_context)
    wire["resource"] = {**wire["resource"], "matter_id": str(uuid4())}

    with pytest.raises(SkGatewayScopeDenied):
        CanonicalSkGatewayPolicyEvaluator(gateway).decide(**wire)

    gateway.authorize.assert_not_called()
