"""Issuer sidecar contract test with real gpg over synthetic temporary keys.

Every key is a throwaway synthetic identity in an isolated temporary
directory. The sidecar server is the synthetic test scaffolding in
`tests.support.sidecar_server`. No live keyring, synced CapAuth home, or
production path is touched, and no live key is created.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sklegal_capauth import (
    CAPABILITY_RULES,
    Audience,
    AuthorizationDenied,
    AuthorizationRequest,
    Capability,
    CapabilityAuthorizer,
    CapabilityGrant,
    CapabilityIssuer,
    DecisionReason,
    InMemoryAuditSink,
    InMemoryPrincipalPolicyBackend,
    InMemoryReplayBackend,
    InMemoryRevocationBackend,
    IssuerCustodyPolicy,
    IssuerPolicyStore,
    PrincipalContext,
    PrincipalType,
    SidecarSigningHandle,
    SignatureVerificationCache,
    SigningUnavailable,
    TrustedIssuerGrant,
    TrustedIssuerPolicyDocument,
    VersionedTrustedIssuerBackend,
)

from tests.support.capauth_contract import MATTER_ID, TENANT_ID, MutableClock
from tests.support.sidecar_server import SyntheticSidecarServer


def _generate_key(home: Path, name: str) -> str:
    environment = {**os.environ, "GNUPGHOME": str(home)}
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--passphrase",
            "",
            "--quick-generate-key",
            f"SKLegal Synthetic {name} <synthetic-{name.lower()}@example.invalid>",
            "ed25519",
            "sign",
            "1d",
        ],
        env=environment,
        check=True,
        capture_output=True,
    )
    listing = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--list-secret-keys"],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    fprs = [
        line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")
    ]
    return fprs[-1]


def _policy(revision: int, fingerprint: str, supersedes: int | None = None):
    return TrustedIssuerPolicyDocument(
        policy_version="sklegal-authz/v1",
        revision=revision,
        status="active",
        supersedes=supersedes,
        issuers=(
            TrustedIssuerGrant(
                fingerprint=fingerprint,
                capabilities=frozenset({Capability.MATTER_READ}),
                audiences=frozenset({Audience.API}),
                principal_types=frozenset({PrincipalType.HUMAN}),
            ),
        ),
    )


@unittest.skipUnless(shutil.which("gpg"), "gpg is required")
class IssuerSidecarContractTest(unittest.TestCase):
    def test_sidecar_lifecycle_rotation_outage_and_wrong_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sklegal-issuer-sidecar-") as temp:
            root = Path(temp)
            keyring = root / "sidecar-gpg"
            keyring.mkdir(mode=0o700)
            first_fpr = _generate_key(keyring, "First")
            second_fpr = _generate_key(keyring, "Second")
            # CapAuth verification reads GNUPGHOME; point it at the isolated
            # synthetic keyring for the duration of this test only.
            self.enterContext(patch.dict(os.environ, {"GNUPGHOME": str(keyring)}))

            socket_path = root / "issuer-sidecar.sock"
            synced_home = root / "synced-capauth-home"
            synced_home.mkdir()

            policy_root = root / "policy"
            policy_root.mkdir()
            store = IssuerPolicyStore(policy_root)
            store.write(_policy(1, first_fpr))
            store.write(_policy(2, second_fpr, supersedes=1))

            clock = MutableClock()
            principal = PrincipalContext(
                principal_id=uuid4(),
                principal_type=PrincipalType.HUMAN,
                subject="synthetic:human:issuer-sidecar",
                tenant_id=TENANT_ID,
            )
            rule = CAPABILITY_RULES[Capability.MATTER_READ]
            grant = CapabilityGrant(
                audience=Audience.API,
                target="api:matter.get",
                capability=Capability.MATTER_READ,
                tenant_id=TENANT_ID,
                matter_id=MATTER_ID,
                resource_type=rule.resource_type,
                operation=rule.operation,
                purpose=next(iter(rule.purposes)),
            )

            def new_authorizer(revision: int) -> CapabilityAuthorizer:
                return CapabilityAuthorizer(
                    trusted_issuers=VersionedTrustedIssuerBackend(store, revision),
                    principals=InMemoryPrincipalPolicyBackend((principal,)),
                    revocations=InMemoryRevocationBackend(),
                    replay=InMemoryReplayBackend(clock=clock),
                    audit=InMemoryAuditSink(),
                    signature_cache=SignatureVerificationCache(clock=clock),
                    clock=clock,
                )

            def request() -> AuthorizationRequest:
                return AuthorizationRequest(
                    principal=principal,
                    grant=grant,
                    correlation_id=uuid4(),
                )

            def custody(fingerprint: str, rotated_from: str | None = None):
                return IssuerCustodyPolicy(
                    issuer_fingerprint=fingerprint,
                    custody="sidecar",
                    socket_path=str(socket_path),
                    rotated_from=rotated_from,
                    synced_home_paths=(str(synced_home),),
                )

            def denied_reason(authorizer, presented) -> DecisionReason:
                try:
                    authorizer.authorize(presented, request())
                except AuthorizationDenied as exc:
                    return exc.decision.reason_code
                raise AssertionError("authorization unexpectedly succeeded")

            server = SyntheticSidecarServer(socket_path, keyring)
            server.start()
            try:
                # Readiness: the sidecar holds the dedicated key.
                first = SidecarSigningHandle(custody(first_fpr))
                report = first.readiness()
                self.assertTrue(report.ready, report.detail)
                self.assertEqual(report.backend, "sidecar")

                # The dedicated issuer signs and authorizes within ceilings.
                first_issuer = CapabilityIssuer(first, clock=clock)
                presented = first_issuer.issue_root(principal=principal, grant=grant)
                new_authorizer(1).authorize(presented, request())

                # Rotation: new custody lineage and new policy revision.
                second = SidecarSigningHandle(
                    custody(second_fpr, rotated_from=first_fpr)
                )
                self.assertTrue(second.readiness().ready)
                second_issuer = CapabilityIssuer(second, clock=clock)
                presented = second_issuer.issue_root(principal=principal, grant=grant)
                new_authorizer(2).authorize(presented, request())

                # The retired fingerprint no longer satisfies the new policy.
                presented = first_issuer.issue_root(principal=principal, grant=grant)
                reason = denied_reason(new_authorizer(2), presented)
                self.assertEqual(reason, DecisionReason.UNTRUSTED_ISSUER)

                # Rollback: explicit return to the prior policy revision.
                presented = first_issuer.issue_root(principal=principal, grant=grant)
                new_authorizer(1).authorize(presented, request())

                # Wrong fingerprint: the sidecar does not hold the key.
                stranger = SidecarSigningHandle(custody("F" * 40))
                report = stranger.readiness()
                self.assertFalse(report.ready)
                self.assertEqual(report.detail, "issuer key is not held by the signer")
                with self.assertRaises(SigningUnavailable) as raised:
                    stranger.sign(b"payload")
                self.assertEqual(
                    str(raised.exception), "issuer signing operation failed"
                )
            finally:
                server.stop()

            # Process and socket outage: every failure is sanitized.
            report = first.readiness()
            self.assertFalse(report.ready)
            self.assertEqual(report.detail, "issuer signer is unavailable")
            with self.assertRaises(SigningUnavailable) as raised:
                first.sign(b"payload")
            self.assertEqual(str(raised.exception), "issuer signer is unavailable")
            self.assertNotIn(str(socket_path), str(raised.exception))


if __name__ == "__main__":
    unittest.main()
