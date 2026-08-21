"""Bounded complete-chain delegation tests for SKLegal capabilities."""

from __future__ import annotations

import unittest
from uuid import UUID, uuid4

from pydantic_core import PydanticSerializationError
from sklegal_capauth import (
    Audience,
    Capability,
    DecisionReason,
    DelegatingCapabilityIssuer,
    DelegationDenied,
    InMemoryPrincipalPolicyBackend,
    PresentedCapability,
    PrincipalContext,
    PrincipalPolicySnapshot,
    PrincipalType,
    Purpose,
    parse_presented_token,
)

from tests.support.capauth_contract import CapabilityTestRig, raw_leaf


class SelectiveUnavailablePrincipalBackend:
    def __init__(
        self,
        delegate: InMemoryPrincipalPolicyBackend,
        blocked_principal_id: UUID,
    ) -> None:
        self._delegate = delegate
        self._blocked_principal_id = blocked_principal_id

    def snapshot(self, principal: PrincipalContext) -> PrincipalPolicySnapshot:
        if principal.principal_id == self._blocked_principal_id:
            raise RuntimeError("synthetic principal backend outage")
        return self._delegate.snapshot(principal)


class CapabilityDelegationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = CapabilityTestRig()
        self.service = self.rig.principal(PrincipalType.SERVICE)
        self.agent = self.rig.principal(PrincipalType.AGENT)
        self.grant = self.rig.grant(
            audience=Audience.TOOL,
            capability=Capability.CORPUS_SEARCH,
            purpose=Purpose.LEGAL_RESEARCH,
            resource_id="corpus-index:synthetic",
        )

    def tearDown(self) -> None:
        self.rig.close()

    def test_valid_attenuated_child_requires_complete_chain(self) -> None:
        root = self.rig.issue(
            self.service,
            self.grant,
            max_delegation_depth=1,
        )
        delegator = DelegatingCapabilityIssuer(
            authorizer=self.rig.authorizer,
            issuer=self.rig.issuer,
        )
        child = delegator.delegate(
            parent=root,
            authenticated_parent=self.service,
            child_principal=self.agent,
            child_grant=self.grant,
            correlation_id=uuid4(),
        )
        raw_chain = child.credentials_for_verification()
        authorized = self.rig.authorizer.authorize(
            child,
            self.rig.request(self.agent, self.grant),
        )
        parsed_chain = tuple(parse_presented_token(item) for item in raw_chain)
        self.assertEqual(authorized.decision.delegation_depth, 1)
        self.assertEqual(
            authorized.decision.ancestor_credential_digests,
            (parsed_chain[0].credential_digest,),
        )
        self.assertEqual(
            tuple(
                reference.principal_id
                for reference in authorized.decision.principal_policy_revisions
            ),
            (self.service.principal_id, self.agent.principal_id),
        )
        with self.assertRaises(PydanticSerializationError):
            authorized.model_dump_json()
        visible = repr(authorized)
        for raw_credential in raw_chain:
            self.assertNotIn(raw_credential, visible)
        self.assertNotIn("BEGIN PGP SIGNATURE", visible)
        self.assertNotIn("skcapstone_token", visible)
        self.assertEqual(
            authorized.principal_chain,
            (self.service, self.agent),
        )
        audit_json = self.rig.audit.decisions()[-1].model_dump_json()
        for raw_credential in raw_chain:
            self.assertNotIn(raw_credential, audit_json)
        self.assertNotIn("BEGIN PGP SIGNATURE", audit_json)
        self.assertNotIn("skcapstone_token", audit_json)

        leaf_only = type(child).single(raw_leaf(child))
        self.assertEqual(
            self.rig.denied_reason(self.agent, self.grant, leaf_only),
            DecisionReason.DELEGATION_CHAIN_INVALID,
        )

    def test_parent_can_delegate_only_once(self) -> None:
        root = self.rig.issue(
            self.service,
            self.grant,
            max_delegation_depth=1,
        )
        delegator = DelegatingCapabilityIssuer(
            authorizer=self.rig.authorizer,
            issuer=self.rig.issuer,
        )
        delegator.delegate(
            parent=root,
            authenticated_parent=self.service,
            child_principal=self.agent,
            child_grant=self.grant,
            correlation_id=uuid4(),
        )
        with self.assertRaises(DelegationDenied):
            delegator.delegate(
                parent=root,
                authenticated_parent=self.service,
                child_principal=self.agent,
                child_grant=self.grant,
                correlation_id=uuid4(),
            )

    def test_scope_widening_and_nondelegable_capabilities_deny(self) -> None:
        exact_parent_grant = self.rig.grant(
            audience=Audience.TOOL,
            capability=Capability.CORPUS_SEARCH,
            purpose=Purpose.LEGAL_RESEARCH,
            resource_id="corpus-index:one",
        )
        changed_child_grant = self.rig.grant(
            audience=Audience.TOOL,
            capability=Capability.CORPUS_SEARCH,
            purpose=Purpose.LEGAL_RESEARCH,
            resource_id="corpus-index:two",
        )
        root = self.rig.issue(
            self.service,
            exact_parent_grant,
            max_delegation_depth=1,
        )
        delegator = DelegatingCapabilityIssuer(
            authorizer=self.rig.authorizer,
            issuer=self.rig.issuer,
        )
        with self.assertRaises(DelegationDenied):
            delegator.delegate(
                parent=root,
                authenticated_parent=self.service,
                child_principal=self.agent,
                child_grant=changed_child_grant,
                correlation_id=uuid4(),
            )

        human = self.rig.principal(PrincipalType.HUMAN)
        approval = self.rig.grant(
            audience=Audience.API,
            capability=Capability.WORK_PRODUCT_APPROVE,
            purpose=Purpose.HUMAN_APPROVAL,
            resource_id="work-product:synthetic",
            resource_version=1,
            resource_sha256="3" * 64,
        )
        with self.assertRaises(ValueError):
            self.rig.issue(human, approval, max_delegation_depth=1)

    def test_ancestor_revocation_and_expiry_deny_child(self) -> None:
        root = self.rig.issue(
            self.service,
            self.grant,
            max_delegation_depth=1,
        )
        parsed_root = parse_presented_token(raw_leaf(root))
        child_raw = self.rig.issuer._issue_child(
            parent=parsed_root,
            principal=self.agent,
            grant=self.grant,
            ttl_seconds=60,
            max_depth=1,
        )
        child = root.with_child(child_raw)
        self.rig.revocations.revoke(parsed_root.credential_digest)
        self.assertEqual(
            self.rig.denied_reason(self.agent, self.grant, child),
            DecisionReason.ANCESTOR_REVOKED,
        )

        service = self.rig.principal(PrincipalType.SERVICE)
        agent = self.rig.principal(PrincipalType.AGENT)
        grant = self.rig.grant(
            audience=Audience.TOOL,
            capability=Capability.CORPUS_SEARCH,
            purpose=Purpose.LEGAL_RESEARCH,
        )
        expiring_root = self.rig.issue(
            service,
            grant,
            ttl_seconds=1,
            max_delegation_depth=1,
        )
        parsed = parse_presented_token(raw_leaf(expiring_root))
        expiring_child = expiring_root.with_child(
            self.rig.issuer._issue_child(
                parent=parsed,
                principal=agent,
                grant=grant,
                ttl_seconds=1,
                max_depth=1,
            )
        )
        self.rig.clock.advance(seconds=2)
        self.assertEqual(
            self.rig.denied_reason(agent, grant, expiring_child),
            DecisionReason.ANCESTOR_EXPIRED,
        )

    def test_wrong_parent_digest_and_over_delegated_child_deny(self) -> None:
        root = self.rig.issue(
            self.service,
            self.grant,
            max_delegation_depth=1,
        )
        parsed = parse_presented_token(raw_leaf(root))
        changed_grant = self.rig.grant(
            audience=Audience.TOOL,
            capability=Capability.CORPUS_SEARCH,
            purpose=Purpose.LEGAL_RESEARCH,
            resource_id="corpus-index:narrowed-wrongly",
        )
        broadened_raw = self.rig.issuer._issue_child(
            parent=parsed,
            principal=self.agent,
            grant=changed_grant,
            ttl_seconds=60,
            max_depth=1,
        )
        broadened = root.with_child(broadened_raw)
        self.assertEqual(
            self.rig.denied_reason(self.agent, changed_grant, broadened),
            DecisionReason.OVER_DELEGATED,
        )

    def test_every_ancestor_principal_is_current_and_cache_cannot_bypass(self) -> None:
        def child_chain() -> PresentedCapability:
            root = self.rig.issue(
                self.service,
                self.grant,
                max_delegation_depth=1,
            )
            parsed = parse_presented_token(raw_leaf(root))
            return root.with_child(
                self.rig.issuer._issue_child(
                    parent=parsed,
                    principal=self.agent,
                    grant=self.grant,
                    ttl_seconds=60,
                    max_depth=1,
                )
            )

        inactive = child_chain()
        self.rig.principals.set(self.service, active=False)
        self.assertEqual(
            self.rig.denied_reason(self.agent, self.grant, inactive),
            DecisionReason.PRINCIPAL_INACTIVE,
        )

        self.rig.principals.set(self.service)
        unbound = child_chain()
        self.rig.principals.remove(self.service.principal_id)
        self.assertEqual(
            self.rig.denied_reason(self.agent, self.grant, unbound),
            DecisionReason.PRINCIPAL_UNBOUND,
        )

        self.rig.principals.set(self.service)
        rebound = child_chain()
        rebound_parent = PrincipalContext(
            principal_id=self.service.principal_id,
            principal_type=self.service.principal_type,
            subject=self.service.subject,
            tenant_id=UUID("10000000-0000-4000-8000-000000000009"),
        )
        self.rig.principals.set(rebound_parent)
        self.assertEqual(
            self.rig.denied_reason(self.agent, self.grant, rebound),
            DecisionReason.PRINCIPAL_REBOUND,
        )

        self.rig.principals.set(self.service)
        kind_rebound = child_chain()
        rebound_kind_parent = PrincipalContext(
            principal_id=self.service.principal_id,
            principal_type=PrincipalType.AGENT,
            subject=self.service.subject,
            tenant_id=self.service.tenant_id,
        )
        self.rig.principals.set(rebound_kind_parent)
        self.assertEqual(
            self.rig.denied_reason(self.agent, self.grant, kind_rebound),
            DecisionReason.PRINCIPAL_REBOUND,
        )

        self.rig.principals.set(self.service)
        unavailable = child_chain()
        unavailable_authorizer = self.rig.new_authorizer(
            principals=SelectiveUnavailablePrincipalBackend(
                self.rig.principals,
                self.service.principal_id,
            )
        )
        self.assertEqual(
            self.rig.denied_reason(
                self.agent,
                self.grant,
                unavailable,
                authorizer=unavailable_authorizer,
            ),
            DecisionReason.BACKEND_UNAVAILABLE,
        )

        cached = child_chain()
        context = self.rig.authorize(self.agent, self.grant, cached)
        self.assertEqual(
            tuple(
                reference.principal_id
                for reference in context.decision.principal_policy_revisions
            ),
            (self.service.principal_id, self.agent.principal_id),
        )
        self.rig.principals.set(self.service, active=False)
        self.assertEqual(
            self.rig.denied_reason(self.agent, self.grant, cached),
            DecisionReason.PRINCIPAL_INACTIVE,
        )


if __name__ == "__main__":
    unittest.main()
