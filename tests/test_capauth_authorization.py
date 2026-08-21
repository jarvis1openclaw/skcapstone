"""Focused fail-closed tests for the SKLegal capability authorizer."""

from __future__ import annotations

import json
import pickle
import tempfile
import threading
import traceback
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from capauth import (  # type: ignore[import-untyped]
    signature_verifies as capauth_signature_verifies,
)
from capauth.testing import STUB_ISSUER_FPR  # type: ignore[import-untyped]
from pydantic_core import PydanticSerializationError
from sklegal_capauth import (
    Audience,
    AuthorizationDenied,
    BackendUnavailable,
    Capability,
    CredentialFormatError,
    DecisionReason,
    DelegatingCapabilityIssuer,
    DelegationDenied,
    FileTrustedIssuerBackend,
    ModelRoute,
    PresentedCapability,
    PrincipalContext,
    PrincipalType,
    Purpose,
    UnavailableAuditSink,
    UnavailablePrincipalPolicyBackend,
    UnavailableReplayBackend,
    UnavailableRevocationBackend,
    UnavailableTrustedIssuerBackend,
    parse_authorization_bearer,
    parse_presented_token,
)

from tests.support.capauth_contract import (
    MATTER_ID,
    RESOURCE_DIGEST,
    TENANT_ID,
    CapabilityTestRig,
    raw_leaf,
    resign_raw,
)


class CapabilityAuthorizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = CapabilityTestRig()

    def tearDown(self) -> None:
        self.rig.close()

    def test_valid_human_agent_service_and_connector_invocations(self) -> None:
        cases = (
            (
                PrincipalType.HUMAN,
                self.rig.grant(),
            ),
            (
                PrincipalType.AGENT,
                self.rig.grant(audience=Audience.TOOL),
            ),
            (
                PrincipalType.AGENT,
                self.rig.grant(
                    audience=Audience.MODEL,
                    capability=Capability.CORPUS_SEARCH,
                    purpose=Purpose.LEGAL_RESEARCH,
                ),
            ),
            (
                PrincipalType.SERVICE,
                self.rig.grant(audience=Audience.API),
            ),
            (
                PrincipalType.CONNECTOR,
                self.rig.grant(
                    audience=Audience.CONNECTOR,
                    capability=Capability.ACTION_EMAIL_DISPATCH,
                    purpose=Purpose.EXTERNAL_ACTION_DISPATCH,
                ),
            ),
        )
        for principal_type, grant in cases:
            with self.subTest(principal_type=principal_type, audience=grant.audience):
                principal = self.rig.principal(principal_type)
                presented = self.rig.issue(principal, grant)
                self.rig.authorize(principal, grant, presented)
        self.assertEqual(len(self.rig.audit.decisions()), len(cases))

    def test_missing_malformed_unsigned_invalid_and_extra_fields_deny(self) -> None:
        principal = self.rig.principal()
        grant = self.rig.grant()
        self.assertEqual(
            self.rig.denied_reason(principal, grant, None),
            DecisionReason.MISSING_CREDENTIAL,
        )
        self.assertEqual(
            self.rig.denied_reason(
                principal,
                grant,
                PresentedCapability.single("not-json"),
            ),
            DecisionReason.MALFORMED_CREDENTIAL,
        )

        raw = raw_leaf(self.rig.issue(principal, grant))
        unsigned = json.loads(raw)
        unsigned["signature"] = ""
        self.assertEqual(
            self.rig.denied_reason(
                principal,
                grant,
                PresentedCapability.single(json.dumps(unsigned)),
            ),
            DecisionReason.UNSIGNED_CREDENTIAL,
        )

        invalid = json.loads(raw)
        invalid["signature"] = "invalid"
        self.assertEqual(
            self.rig.denied_reason(
                principal,
                grant,
                PresentedCapability.single(json.dumps(invalid)),
            ),
            DecisionReason.INVALID_SIGNATURE,
        )

        extra = json.loads(raw)
        extra["unexpected"] = True
        self.assertEqual(
            self.rig.denied_reason(
                principal,
                grant,
                PresentedCapability.single(json.dumps(extra)),
            ),
            DecisionReason.MALFORMED_CREDENTIAL,
        )

        metadata_extra = json.loads(raw)
        metadata_extra["payload"]["metadata"]["unexpected"] = "value"
        self.assertEqual(
            self.rig.denied_reason(
                principal,
                grant,
                PresentedCapability.single(json.dumps(metadata_extra)),
            ),
            DecisionReason.MALFORMED_CREDENTIAL,
        )

    def test_time_and_issuer_policy_fail_closed(self) -> None:
        principal = self.rig.principal()
        grant = self.rig.grant()

        expiring = self.rig.issue(principal, grant, ttl_seconds=1)
        self.rig.clock.advance(seconds=2)
        self.assertEqual(
            self.rig.denied_reason(principal, grant, expiring),
            DecisionReason.EXPIRED,
        )

        current = raw_leaf(self.rig.issue(principal, grant))
        over_ttl = resign_raw(
            current,
            lambda payload: payload.__setitem__(
                "expires_at",
                (self.rig.clock.value + timedelta(seconds=3601)).isoformat(),
            ),
        )
        self.assertEqual(
            self.rig.denied_reason(principal, grant, over_ttl),
            DecisionReason.TTL_EXCEEDED,
        )

        future = resign_raw(
            current,
            lambda payload: payload.update(
                {
                    "issued_at": (
                        self.rig.clock.value + timedelta(seconds=31)
                    ).isoformat(),
                    "not_before": (
                        self.rig.clock.value + timedelta(seconds=31)
                    ).isoformat(),
                    "expires_at": (
                        self.rig.clock.value + timedelta(seconds=300)
                    ).isoformat(),
                }
            ),
        )
        self.assertEqual(
            self.rig.denied_reason(principal, grant, future),
            DecisionReason.NOT_YET_VALID,
        )

        no_expiry = json.loads(current)
        no_expiry["payload"]["expires_at"] = None
        self.assertEqual(
            self.rig.denied_reason(
                principal,
                grant,
                PresentedCapability.single(json.dumps(no_expiry)),
            ),
            DecisionReason.MALFORMED_CREDENTIAL,
        )

        other_fingerprint = "F" * 40
        untrusted = resign_raw(
            current,
            lambda payload: payload.__setitem__("issuer", other_fingerprint),
        )
        self.assertEqual(
            self.rig.denied_reason(principal, grant, untrusted),
            DecisionReason.UNTRUSTED_ISSUER,
        )

    def test_decision_time_recheck_catches_crypto_crossing_expiry(self) -> None:
        principal = self.rig.principal()
        grant = self.rig.grant()
        token = self.rig.issue(principal, grant, ttl_seconds=1)
        advanced = False

        def verify_then_advance(token_value: object) -> bool:
            nonlocal advanced
            verified = capauth_signature_verifies(token_value)
            if not advanced:
                advanced = True
                self.rig.clock.advance(seconds=2)
            return verified

        with patch(
            "sklegal_capauth.authorization.signature_verifies",
            side_effect=verify_then_advance,
        ):
            self.assertEqual(
                self.rig.denied_reason(principal, grant, token),
                DecisionReason.EXPIRED,
            )

    def test_decision_time_recheck_catches_ancestor_crossing_expiry(self) -> None:
        service = self.rig.principal(PrincipalType.SERVICE)
        agent = self.rig.principal(PrincipalType.AGENT)
        grant = self.rig.grant(audience=Audience.TOOL)
        root = self.rig.issue(
            service,
            grant,
            ttl_seconds=1,
            max_delegation_depth=1,
        )
        parsed_root = parse_presented_token(raw_leaf(root))
        child = root.with_child(
            self.rig.issuer._issue_child(
                parent=parsed_root,
                principal=agent,
                grant=grant,
                ttl_seconds=1,
                max_depth=1,
            )
        )
        advanced = False

        def verify_then_advance(token_value: object) -> bool:
            nonlocal advanced
            verified = capauth_signature_verifies(token_value)
            if not advanced:
                advanced = True
                self.rig.clock.advance(seconds=2)
            return verified

        with patch(
            "sklegal_capauth.authorization.signature_verifies",
            side_effect=verify_then_advance,
        ):
            self.assertEqual(
                self.rig.denied_reason(agent, grant, child),
                DecisionReason.ANCESTOR_EXPIRED,
            )

    def test_final_time_recheck_catches_replay_reservation_crossing_expiry(
        self,
    ) -> None:
        principal = self.rig.principal()
        grant = self.rig.grant()
        token = self.rig.issue(principal, grant, ttl_seconds=1)
        delegate = self.rig.replay
        clock = self.rig.clock

        class AdvancingReplayBackend:
            def reserve(
                self,
                *,
                credential_digest: str,
                decision_id: str,
                expires_at: datetime,
            ) -> bool:
                clock.advance(seconds=2)
                return delegate.reserve(
                    credential_digest=credential_digest,
                    decision_id=decision_id,
                    expires_at=expires_at,
                )

        self.assertEqual(
            self.rig.denied_reason(
                principal,
                grant,
                token,
                authorizer=self.rig.new_authorizer(replay=AdvancingReplayBackend()),
            ),
            DecisionReason.EXPIRED,
        )

    def test_duplicate_json_members_deny_at_every_token_depth(self) -> None:
        principal = self.rig.principal()
        grant = self.rig.grant()
        raw = raw_leaf(self.rig.issue(principal, grant))
        duplicate_top = '{"signature":"duplicate",' + raw[1:]
        duplicate_nested = raw.replace(
            '"use_limit":1',
            '"use_limit":2,"use_limit":1',
            1,
        )
        for malformed in (duplicate_top, duplicate_nested):
            with self.subTest(kind=malformed[:32]):
                self.assertEqual(
                    self.rig.denied_reason(
                        principal,
                        grant,
                        PresentedCapability.single(malformed),
                    ),
                    DecisionReason.MALFORMED_CREDENTIAL,
                )

    def test_exact_request_scope_and_cross_boundary_reuse_deny(self) -> None:
        principal = self.rig.principal(PrincipalType.AGENT)
        grant = self.rig.grant(audience=Audience.TOOL)

        cases = (
            (
                self.rig.grant(audience=Audience.TOOL, target="tool:different"),
                DecisionReason.WRONG_TARGET,
            ),
            (
                self.rig.grant(
                    audience=Audience.TOOL,
                    matter_id=UUID("20000000-0000-4000-8000-000000000002"),
                ),
                DecisionReason.WRONG_MATTER,
            ),
            (
                self.rig.grant(
                    audience=Audience.TOOL,
                    resource_id="matter:different",
                ),
                DecisionReason.WRONG_RESOURCE,
            ),
            (
                self.rig.grant(
                    audience=Audience.TOOL,
                    capability=Capability.CORPUS_SEARCH,
                    purpose=Purpose.LEGAL_RESEARCH,
                ),
                DecisionReason.WRONG_CAPABILITY,
            ),
            (
                self.rig.grant(
                    audience=Audience.MODEL,
                    capability=Capability.CORPUS_SEARCH,
                    purpose=Purpose.LEGAL_RESEARCH,
                    model_route=ModelRoute.OPENAI,
                ),
                DecisionReason.WRONG_AUDIENCE,
            ),
        )
        for requested, reason in cases:
            with self.subTest(reason=reason):
                token = self.rig.issue(principal, grant)
                self.assertEqual(
                    self.rig.denied_reason(principal, requested, token),
                    reason,
                )

        other_tenant = UUID("10000000-0000-4000-8000-000000000002")
        tenant_principal = PrincipalContext(
            principal_id=principal.principal_id,
            principal_type=principal.principal_type,
            subject=principal.subject,
            tenant_id=other_tenant,
        )
        self.rig.principals.set(tenant_principal)
        tenant_grant = self.rig.grant(
            audience=Audience.TOOL,
            tenant_id=other_tenant,
        )
        self.assertEqual(
            self.rig.denied_reason(
                tenant_principal,
                tenant_grant,
                self.rig.issue(principal, grant),
            ),
            DecisionReason.PRINCIPAL_REBOUND,
        )

    def test_model_route_workflow_and_purpose_are_exact(self) -> None:
        principal = self.rig.principal(PrincipalType.AGENT)
        grant = self.rig.grant(
            audience=Audience.MODEL,
            capability=Capability.CORPUS_SEARCH,
            purpose=Purpose.LEGAL_RESEARCH,
        )
        token = self.rig.issue(principal, grant)
        wrong_route = self.rig.grant(
            audience=Audience.MODEL,
            capability=Capability.CORPUS_SEARCH,
            purpose=Purpose.LEGAL_RESEARCH,
            model_route=ModelRoute.OPENAI,
        )
        self.assertEqual(
            self.rig.denied_reason(principal, wrong_route, token),
            DecisionReason.WRONG_MODEL_ROUTE,
        )

        token = self.rig.issue(principal, grant)
        wrong_workflow = self.rig.grant(
            audience=Audience.MODEL,
            capability=Capability.CORPUS_SEARCH,
            purpose=Purpose.LEGAL_RESEARCH,
            workflow_run_id="workflow:different",
        )
        self.assertEqual(
            self.rig.denied_reason(principal, wrong_workflow, token),
            DecisionReason.WRONG_WORKFLOW,
        )

        evidence_principal = self.rig.principal()
        evidence_grant = self.rig.grant(
            capability=Capability.CORPUS_ARTIFACT_READ,
            purpose=Purpose.LEGAL_RESEARCH,
        )
        quarantine_request = self.rig.grant(
            capability=Capability.CORPUS_ARTIFACT_READ,
            purpose=Purpose.ISOLATED_QUARANTINE_REVIEW,
        )
        self.assertEqual(
            self.rig.denied_reason(
                evidence_principal,
                quarantine_request,
                self.rig.issue(evidence_principal, evidence_grant),
            ),
            DecisionReason.WRONG_PURPOSE,
        )

    def test_revocation_replay_principal_status_and_backend_outages_deny(self) -> None:
        principal = self.rig.principal()
        grant = self.rig.grant()
        token = self.rig.issue(principal, grant)
        digest = parse_presented_token(raw_leaf(token)).credential_digest
        self.rig.revocations.revoke(digest)
        self.assertEqual(
            self.rig.denied_reason(principal, grant, token),
            DecisionReason.REVOKED,
        )

        replayed = self.rig.issue(principal, grant)
        self.rig.authorize(principal, grant, replayed)
        self.assertEqual(
            self.rig.denied_reason(principal, grant, replayed),
            DecisionReason.REPLAYED,
        )
        self.assertTrue(self.rig.audit.decisions()[-1].signature_cache_hit)

        inactive = self.rig.issue(principal, grant)
        self.rig.principals.set(principal, active=False)
        self.assertEqual(
            self.rig.denied_reason(principal, grant, inactive),
            DecisionReason.PRINCIPAL_INACTIVE,
        )

        unbound_principal = PrincipalContext(
            principal_id=UUID("30000000-0000-4000-8000-000000000001"),
            principal_type=PrincipalType.HUMAN,
            subject="synthetic:unbound",
            tenant_id=TENANT_ID,
        )
        unbound_token = self.rig.issue(unbound_principal, grant)
        self.assertEqual(
            self.rig.denied_reason(unbound_principal, grant, unbound_token),
            DecisionReason.PRINCIPAL_UNBOUND,
        )

        self.rig.principals.set(principal, active=True)
        outage_cases = (
            {"trusted_issuers": UnavailableTrustedIssuerBackend()},
            {"principals": UnavailablePrincipalPolicyBackend()},
            {"revocations": UnavailableRevocationBackend()},
            {"replay": UnavailableReplayBackend()},
        )
        for override in outage_cases:
            with self.subTest(override=tuple(override)):
                fresh = self.rig.issue(principal, grant)
                self.assertEqual(
                    self.rig.denied_reason(
                        principal,
                        grant,
                        fresh,
                        authorizer=self.rig.new_authorizer(**override),
                    ),
                    DecisionReason.BACKEND_UNAVAILABLE,
                )

        audit_outage = self.rig.issue(principal, grant)
        self.assertEqual(
            self.rig.denied_reason(
                principal,
                grant,
                audit_outage,
                authorizer=self.rig.new_authorizer(audit=UnavailableAuditSink()),
            ),
            DecisionReason.AUDIT_UNAVAILABLE,
        )

    def test_signature_cache_never_hides_revocation_or_principal_suspension(
        self,
    ) -> None:
        principal = self.rig.principal()
        grant = self.rig.grant()
        token = self.rig.issue(principal, grant)
        digest = parse_presented_token(raw_leaf(token)).credential_digest
        self.rig.authorize(principal, grant, token)

        self.rig.principals.set(principal, active=False)
        self.assertEqual(
            self.rig.denied_reason(principal, grant, token),
            DecisionReason.PRINCIPAL_INACTIVE,
        )
        self.rig.principals.set(principal, active=True)
        self.rig.revocations.revoke(digest)
        self.assertEqual(
            self.rig.denied_reason(principal, grant, token),
            DecisionReason.REVOKED,
        )

        backend_token = self.rig.issue(principal, grant)
        self.rig.authorize(principal, grant, backend_token)
        self.assertEqual(
            self.rig.denied_reason(
                principal,
                grant,
                backend_token,
                authorizer=self.rig.new_authorizer(
                    revocations=UnavailableRevocationBackend()
                ),
            ),
            DecisionReason.BACKEND_UNAVAILABLE,
        )

        with tempfile.TemporaryDirectory(prefix="sklegal-issuer-policy-") as temp:
            policy_path = Path(temp) / "trusted-issuers.json"

            def write_policy(fingerprint: str) -> None:
                policy_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "sklegal-trusted-issuers/v1",
                            "policy_version": "sklegal-authz/v1",
                            "issuers": [
                                {
                                    "fingerprint": fingerprint,
                                    "capabilities": [grant.capability.value],
                                    "audiences": [grant.audience.value],
                                    "principal_types": [principal.principal_type.value],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

            write_policy(STUB_ISSUER_FPR)
            file_authorizer = self.rig.new_authorizer(
                trusted_issuers=FileTrustedIssuerBackend(policy_path)
            )
            issuer_token = self.rig.issue(principal, grant)
            self.rig.authorize(
                principal,
                grant,
                issuer_token,
                authorizer=file_authorizer,
            )
            write_policy("F" * 40)
            self.assertEqual(
                self.rig.denied_reason(
                    principal,
                    grant,
                    issuer_token,
                    authorizer=file_authorizer,
                ),
                DecisionReason.UNTRUSTED_ISSUER,
            )

    def test_trusted_issuer_policy_rejects_duplicate_keys_and_hardlinks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sklegal-issuer-policy-guard-") as temp:
            root = Path(temp)
            policy_path = root / "trusted-issuers.json"
            policy_path.write_text(
                '{"schema_version":"sklegal-trusted-issuers/v1",'
                '"schema_version":"sklegal-trusted-issuers/v1",'
                '"policy_version":"sklegal-authz/v1","issuers":[]}',
                encoding="utf-8",
            )
            with self.assertRaises(BackendUnavailable):
                FileTrustedIssuerBackend(policy_path).snapshot()

            valid = root / "valid.json"
            valid.write_text(
                json.dumps(
                    {
                        "schema_version": "sklegal-trusted-issuers/v1",
                        "policy_version": "sklegal-authz/v1",
                        "issuers": [
                            {
                                "fingerprint": STUB_ISSUER_FPR,
                                "capabilities": [Capability.MATTER_READ.value],
                                "audiences": [Audience.API.value],
                                "principal_types": [PrincipalType.HUMAN.value],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            hardlink = root / "hardlink.json"
            hardlink.hardlink_to(valid)
            with self.assertRaises(BackendUnavailable):
                FileTrustedIssuerBackend(hardlink).snapshot()

    def test_concurrent_replay_reservation_allows_exactly_one_invocation(self) -> None:
        principal = self.rig.principal()
        grant = self.rig.grant()
        token = self.rig.issue(principal, grant)
        barrier = threading.Barrier(8)
        outcomes: list[DecisionReason] = []
        lock = threading.Lock()

        def invoke() -> None:
            barrier.wait()
            try:
                self.rig.authorize(principal, grant, token)
                reason = DecisionReason.ALLOW
            except AuthorizationDenied as exc:
                reason = exc.decision.reason_code
            with lock:
                outcomes.append(reason)

        threads = [threading.Thread(target=invoke) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes.count(DecisionReason.ALLOW), 1)
        self.assertEqual(outcomes.count(DecisionReason.REPLAYED), 7)

    def test_raw_credentials_are_absent_from_safe_outputs(self) -> None:
        principal = self.rig.principal()
        grant = self.rig.grant()
        token = self.rig.issue(principal, grant)
        raw = raw_leaf(token)
        self.assertNotIn(raw, repr(token))
        self.assertNotIn(raw, str(token))
        with self.assertRaises(TypeError):
            pickle.dumps(token)

        authorized = self.rig.authorizer.authorize(
            token,
            self.rig.request(principal, grant),
        )
        with self.assertRaises(PydanticSerializationError):
            authorized.model_dump_json()
        with self.assertRaises(PydanticSerializationError):
            authorized.model_dump()
        with self.assertRaises(TypeError):
            pickle.dumps(authorized)
        self.assertEqual((principal,), authorized.principal_chain)
        self.assertGreater(authorized.credential_expires_at, self.rig.clock())
        self.assertNotIn(raw, repr(authorized))
        decision = self.rig.audit.decisions()[-1]
        self.assertNotIn(raw, decision.model_dump_json())
        self.assertNotIn("BEGIN PGP SIGNATURE", decision.model_dump_json())
        self.assertNotIn("skcapstone_token", decision.model_dump_json())
        self.assertNotIn(raw, repr(decision))

        try:
            self.rig.authorize(principal, grant, token)
        except AuthorizationDenied as exc:
            self.assertNotIn(raw, str(exc))
            self.assertNotIn(raw, exc.decision.model_dump_json())
        else:
            self.fail("replay unexpectedly succeeded")

    def test_parser_authorizer_and_delegator_tracebacks_are_sanitized(self) -> None:
        sentinel = "SYNTHETIC_RAW_CREDENTIAL_SENTINEL"
        principal = self.rig.principal()
        grant = self.rig.grant()
        valid_leaf = raw_leaf(self.rig.issue(principal, grant))

        malformed_cases = (
            PresentedCapability.single(sentinel),
            PresentedCapability.delegated(
                leaf=valid_leaf,
                ancestors=(sentinel,),
            ),
        )
        for presented in malformed_cases:
            with self.subTest(presented=repr(presented)):
                try:
                    self.rig.authorizer.authorize(
                        presented,
                        self.rig.request(principal, grant),
                    )
                except AuthorizationDenied as exc:
                    rendered = "".join(traceback.format_exception(exc))
                    self.assertNotIn(sentinel, rendered)
                    self.assertNotIn(sentinel, repr(exc))
                    self.assertNotIn(sentinel, exc.decision.model_dump_json())
                else:
                    self.fail("malformed credential unexpectedly succeeded")

        malformed_wire = json.dumps(
            {
                "sklegal_presented_capability": "1.0",
                "chain": {"leaf": sentinel, "ancestors": []},
                "unexpected": sentinel,
            }
        )
        try:
            parse_authorization_bearer(malformed_wire)
        except CredentialFormatError as exc:
            self.assertNotIn(sentinel, "".join(traceback.format_exception(exc)))
            self.assertNotIn(sentinel, repr(exc))
        else:
            self.fail("malformed authorization wire unexpectedly succeeded")

        delegator = DelegatingCapabilityIssuer(
            authorizer=self.rig.authorizer,
            issuer=self.rig.issuer,
        )
        try:
            delegator.delegate(
                parent=PresentedCapability.single(sentinel),
                authenticated_parent=principal,
                child_principal=principal,
                child_grant=grant,
                correlation_id=self.rig.request(principal, grant).correlation_id,
            )
        except DelegationDenied as exc:
            self.assertNotIn(sentinel, "".join(traceback.format_exception(exc)))
            self.assertNotIn(sentinel, repr(exc))
        else:
            self.fail("malformed delegation unexpectedly succeeded")

    def test_exact_dispatch_artifact_is_bound(self) -> None:
        principal = self.rig.principal(PrincipalType.CONNECTOR)
        grant = self.rig.grant(
            audience=Audience.CONNECTOR,
            capability=Capability.ACTION_EMAIL_DISPATCH,
            purpose=Purpose.EXTERNAL_ACTION_DISPATCH,
        )
        wrong_digest = self.rig.grant(
            audience=Audience.CONNECTOR,
            capability=Capability.ACTION_EMAIL_DISPATCH,
            purpose=Purpose.EXTERNAL_ACTION_DISPATCH,
            resource_sha256="2" * 64,
        )
        self.assertEqual(grant.resource_sha256, RESOURCE_DIGEST)
        self.assertEqual(grant.matter_id, MATTER_ID)
        self.assertEqual(
            self.rig.denied_reason(
                principal,
                wrong_digest,
                self.rig.issue(principal, grant),
            ),
            DecisionReason.WRONG_RESOURCE,
        )


if __name__ == "__main__":
    unittest.main()
