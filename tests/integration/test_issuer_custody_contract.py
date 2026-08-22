"""Issuer custody contract test with real gpg over synthetic temporary keys.

Every key is a throwaway synthetic identity in an isolated temporary
directory. No live keyring, synced CapAuth home, or production path is
touched.
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
    GpgAgentSigningHandle,
    InMemoryAuditSink,
    InMemoryPrincipalPolicyBackend,
    InMemoryReplayBackend,
    InMemoryRevocationBackend,
    IssuerCustodyPolicy,
    IssuerPolicyStore,
    PrincipalContext,
    PrincipalType,
    SignatureVerificationCache,
    TrustedIssuerGrant,
    TrustedIssuerPolicyDocument,
    VersionedTrustedIssuerBackend,
)

from tests.support.capauth_contract import MATTER_ID, TENANT_ID, MutableClock


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


def _policy_document(revision: int, fingerprint: str, supersedes: int | None = None):
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
class IssuerCustodyContractTest(unittest.TestCase):
    def test_dedicated_issuer_lifecycle_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sklegal-issuer-custody-") as temp:
            root = Path(temp)
            keyring = root / "issuer-gpg"
            keyring.mkdir(mode=0o700)
            issuer_fpr = _generate_key(keyring, "Issuer")
            stranger_fpr = _generate_key(keyring, "Stranger")
            # CapAuth verification reads GNUPGHOME; point it at the isolated
            # synthetic keyring for the duration of this test only.
            self.enterContext(patch.dict(os.environ, {"GNUPGHOME": str(keyring)}))

            synced_home = root / "synced-capauth-home"
            synced_home.mkdir()
            custody = IssuerCustodyPolicy(
                issuer_fingerprint=issuer_fpr,
                custody="gpg-agent",
                key_home=str(keyring),
                synced_home_paths=(str(synced_home),),
            )
            handle = GpgAgentSigningHandle(custody)
            readiness = handle.readiness()
            self.assertTrue(readiness.ready, readiness.detail)
            self.assertEqual(readiness.fingerprint, issuer_fpr)

            policy_root = root / "policy"
            policy_root.mkdir()
            store = IssuerPolicyStore(policy_root)
            store.write(_policy_document(1, issuer_fpr))

            clock = MutableClock()
            principal = PrincipalContext(
                principal_id=uuid4(),
                principal_type=PrincipalType.HUMAN,
                subject="synthetic:human:issuer-custody",
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

            issuer = CapabilityIssuer(handle, clock=clock)

            def denied_reason(
                authorizer: CapabilityAuthorizer, presented
            ) -> DecisionReason:
                try:
                    authorizer.authorize(presented, request())
                except AuthorizationDenied as exc:
                    return exc.decision.reason_code
                raise AssertionError("authorization unexpectedly succeeded")

            # The dedicated issuer signs and authorizes within its ceilings.
            presented = issuer.issue_root(principal=principal, grant=grant)
            new_authorizer(1).authorize(presented, request())

            # Rotation is a new explicit revision; rollback stays explicit.
            store.write(_policy_document(2, issuer_fpr, supersedes=1))
            presented = issuer.issue_root(principal=principal, grant=grant)
            new_authorizer(2).authorize(presented, request())
            presented = issuer.issue_root(principal=principal, grant=grant)
            new_authorizer(1).authorize(presented, request())

            # An unknown issuer is denied even with a valid signature.
            stranger_custody = IssuerCustodyPolicy(
                issuer_fingerprint=stranger_fpr,
                custody="gpg-agent",
                key_home=str(keyring),
            )
            stranger = CapabilityIssuer(
                GpgAgentSigningHandle(stranger_custody), clock=clock
            )
            presented = stranger.issue_root(principal=principal, grant=grant)
            reason = denied_reason(new_authorizer(2), presented)
            self.assertEqual(reason, DecisionReason.UNTRUSTED_ISSUER)

            # A revoked rotation fails closed; the prior revision still works.
            store.revoke(2, reason="rotation-aborted")
            presented = issuer.issue_root(principal=principal, grant=grant)
            reason = denied_reason(new_authorizer(2), presented)
            self.assertEqual(reason, DecisionReason.BACKEND_UNAVAILABLE)
            presented = issuer.issue_root(principal=principal, grant=grant)
            new_authorizer(1).authorize(presented, request())

            # Revoking the last revision closes the boundary completely.
            store.revoke(1, reason="issuer-key-retired")
            presented = issuer.issue_root(principal=principal, grant=grant)
            reason = denied_reason(new_authorizer(1), presented)
            self.assertEqual(reason, DecisionReason.BACKEND_UNAVAILABLE)

            # The signing handle itself never read GNUPGHOME and never
            # carried a passphrase: every call used the protected
            # --homedir argument bound to the custody declaration.
            self.assertNotIn("GNUPGHOME_PASSPHRASE", os.environ)


if __name__ == "__main__":
    unittest.main()
