"""Real OpenPGP contract test using only an isolated synthetic keyring."""

from __future__ import annotations

import json
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
    CapAuthManifestSigner,
    DecisionReason,
    FileTrustedIssuerBackend,
    InMemoryAuditSink,
    InMemoryPrincipalPolicyBackend,
    InMemoryReplayBackend,
    InMemoryRevocationBackend,
    PrincipalContext,
    PrincipalType,
    SignatureVerificationCache,
)

from tests.support.capauth_contract import (
    MATTER_ID,
    TENANT_ID,
    MutableClock,
    raw_leaf,
)


@unittest.skipUnless(shutil.which("gpg"), "gpg is required")
class CapAuthRealOpenPgpContractTest(unittest.TestCase):
    def test_isolated_synthetic_key_signs_and_verifies_exact_payload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sklegal-capauth-gpg-") as temp:
            root = Path(temp)
            keyring = root / "gnupg"
            keyring.mkdir(mode=0o700)
            environment = {**os.environ, "GNUPGHOME": str(keyring)}
            subprocess.run(
                [
                    "gpg",
                    "--batch",
                    "--passphrase",
                    "",
                    "--quick-generate-key",
                    "SKLegal Synthetic Test <synthetic@example.invalid>",
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
            fingerprint = next(
                line.split(":")[9]
                for line in listing.splitlines()
                if line.startswith("fpr:")
            )

            policy_path = root / "trusted-issuers.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": "sklegal-trusted-issuers/v1",
                        "policy_version": "sklegal-authz/v1",
                        "issuers": [
                            {
                                "fingerprint": fingerprint,
                                "capabilities": [Capability.MATTER_READ.value],
                                "audiences": [Audience.API.value],
                                "principal_types": [PrincipalType.HUMAN.value],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            clock = MutableClock()
            principal = PrincipalContext(
                principal_id=uuid4(),
                principal_type=PrincipalType.HUMAN,
                subject="synthetic:human:real-openpgp",
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
            request = AuthorizationRequest(
                principal=principal,
                grant=grant,
                correlation_id=uuid4(),
            )
            principals = InMemoryPrincipalPolicyBackend((principal,))
            revocations = InMemoryRevocationBackend()
            replay = InMemoryReplayBackend(clock=clock)
            audit = InMemoryAuditSink()
            authorizer = CapabilityAuthorizer(
                trusted_issuers=FileTrustedIssuerBackend(policy_path),
                principals=principals,
                revocations=revocations,
                replay=replay,
                audit=audit,
                signature_cache=SignatureVerificationCache(clock=clock),
                clock=clock,
            )

            with patch.dict(os.environ, {"GNUPGHOME": str(keyring)}):
                issuer = CapabilityIssuer(
                    CapAuthManifestSigner(fingerprint),
                    clock=clock,
                )
                presented = issuer.issue_root(principal=principal, grant=grant)
                authorizer.authorize(presented, request)

                tampered = json.loads(
                    raw_leaf(issuer.issue_root(principal=principal, grant=grant))
                )
                tampered["signature"] = tampered["signature"].replace("A", "B", 1)
                with self.assertRaises(AuthorizationDenied) as raised:
                    authorizer.authorize(
                        type(presented).single(json.dumps(tampered)),
                        request,
                    )
                self.assertEqual(
                    raised.exception.decision.reason_code,
                    DecisionReason.INVALID_SIGNATURE,
                )


if __name__ == "__main__":
    unittest.main()
