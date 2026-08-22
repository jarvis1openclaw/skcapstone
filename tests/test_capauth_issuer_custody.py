"""Fail-closed tests for dedicated issuer custody and versioned issuer policy."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capauth.testing import STUB_ISSUER_FPR  # type: ignore[import-untyped]
from pydantic import ValidationError
from sklegal_capauth import (
    CASEY_IDENTITY_FINGERPRINT,
    JARVIS_IDENTITY_FINGERPRINT,
    Audience,
    BackendUnavailable,
    Capability,
    DecisionReason,
    GpgAgentSigningHandle,
    IssuerCustodyError,
    IssuerCustodyPolicy,
    IssuerPolicyStore,
    PrincipalType,
    SigningUnavailable,
    TrustedIssuerGrant,
    TrustedIssuerPolicyDocument,
    VersionedTrustedIssuerBackend,
)

from tests.support.capauth_contract import CapabilityTestRig

DEDICATED_FPR = "1" * 40
SECOND_FPR = "2" * 40


def _grant(
    fingerprint: str,
    *,
    capabilities: frozenset[Capability] | None = None,
) -> TrustedIssuerGrant:
    return TrustedIssuerGrant(
        fingerprint=fingerprint,
        capabilities=capabilities or frozenset({Capability.MATTER_READ}),
        audiences=frozenset({Audience.API}),
        principal_types=frozenset({PrincipalType.HUMAN}),
    )


def _document(
    revision: int = 1,
    *,
    fingerprint: str = DEDICATED_FPR,
    status: str = "active",
    supersedes: int | None = None,
) -> TrustedIssuerPolicyDocument:
    return TrustedIssuerPolicyDocument(
        policy_version="sklegal-authz/v1",
        revision=revision,
        status=status,  # type: ignore[arg-type]
        supersedes=supersedes,
        issuers=(_grant(fingerprint),),
    )


class IssuerCustodyDeclarationTest(unittest.TestCase):
    def test_dedicated_gpg_agent_custody_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "issuer-gpg"
            policy = IssuerCustodyPolicy(
                issuer_fingerprint=DEDICATED_FPR,
                custody="gpg-agent",
                key_home=str(home),
                synced_home_paths=(str(Path(temp) / "synced-capauth"),),
            )
            self.assertEqual(policy.issuer_fingerprint, DEDICATED_FPR)

    def test_identity_anchors_are_never_application_issuers(self) -> None:
        for anchor in (CASEY_IDENTITY_FINGERPRINT, JARVIS_IDENTITY_FINGERPRINT):
            with self.subTest(anchor=anchor), tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(ValidationError):
                    IssuerCustodyPolicy(
                        issuer_fingerprint=anchor,
                        custody="gpg-agent",
                        key_home=str(Path(temp) / "issuer-gpg"),
                    )

    def test_rotation_lineage_rejects_anchors_and_self_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = str(Path(temp) / "issuer-gpg")
            with self.assertRaises(ValidationError):
                IssuerCustodyPolicy(
                    issuer_fingerprint=DEDICATED_FPR,
                    custody="gpg-agent",
                    key_home=home,
                    rotated_from=JARVIS_IDENTITY_FINGERPRINT,
                )
            with self.assertRaises(ValidationError):
                IssuerCustodyPolicy(
                    issuer_fingerprint=DEDICATED_FPR,
                    custody="gpg-agent",
                    key_home=home,
                    rotated_from=DEDICATED_FPR,
                )
            rotated = IssuerCustodyPolicy(
                issuer_fingerprint=SECOND_FPR,
                custody="gpg-agent",
                key_home=home,
                rotated_from=DEDICATED_FPR,
            )
            self.assertEqual(rotated.rotated_from, DEDICATED_FPR)

    def test_custody_home_must_stay_outside_synced_capauth_homes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            synced = Path(temp) / "capauth-home"
            with self.assertRaises(ValidationError):
                IssuerCustodyPolicy(
                    issuer_fingerprint=DEDICATED_FPR,
                    custody="gpg-agent",
                    key_home=str(synced / "issuer"),
                    synced_home_paths=(str(synced),),
                )
            with self.assertRaises(ValidationError):
                IssuerCustodyPolicy(
                    issuer_fingerprint=DEDICATED_FPR,
                    custody="gpg-agent",
                    key_home=str(synced),
                    synced_home_paths=(str(synced / ".private"),),
                )

    def test_custody_kind_field_and_path_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = str(Path(temp) / "issuer-gpg")
            socket = str(Path(temp) / "issuer.sock")
            with self.assertRaises(ValidationError):
                IssuerCustodyPolicy(
                    issuer_fingerprint=DEDICATED_FPR,
                    custody="gpg-agent",
                    key_home="relative/path",
                )
            with self.assertRaises(ValidationError):
                IssuerCustodyPolicy(
                    issuer_fingerprint=DEDICATED_FPR,
                    custody="gpg-agent",
                    key_home=home,
                    socket_path=socket,
                )
            with self.assertRaises(ValidationError):
                IssuerCustodyPolicy(
                    issuer_fingerprint=DEDICATED_FPR,
                    custody="sidecar",
                    key_home=home,
                )
            sidecar = IssuerCustodyPolicy(
                issuer_fingerprint=DEDICATED_FPR,
                custody="sidecar",
                socket_path=socket,
            )
            self.assertEqual(sidecar.custody, "sidecar")


class TrustedIssuerPolicyDocumentTest(unittest.TestCase):
    def test_document_binds_fingerprint_to_exact_ceilings(self) -> None:
        document = _document()
        grant = document.issuers[0]
        self.assertEqual(grant.capabilities, frozenset({Capability.MATTER_READ}))
        self.assertEqual(grant.audiences, frozenset({Audience.API}))
        self.assertEqual(grant.principal_types, frozenset({PrincipalType.HUMAN}))

    def test_document_rejects_identity_anchor_issuers(self) -> None:
        with self.assertRaises(ValidationError):
            _document(fingerprint=CASEY_IDENTITY_FINGERPRINT)

    def test_document_rejects_empty_duplicate_and_bad_lineage(self) -> None:
        with self.assertRaises(ValidationError):
            TrustedIssuerPolicyDocument(
                policy_version="sklegal-authz/v1",
                revision=1,
                status="active",
                issuers=(),
            )
        with self.assertRaises(ValidationError):
            TrustedIssuerPolicyDocument(
                policy_version="sklegal-authz/v1",
                revision=1,
                status="active",
                issuers=(_grant(DEDICATED_FPR), _grant(DEDICATED_FPR)),
            )
        with self.assertRaises(ValidationError):
            _document(revision=2, supersedes=2)
        with self.assertRaises(ValidationError):
            _document(revision=2, supersedes=3)

    def test_document_rejects_extra_fields_and_wrong_schema(self) -> None:
        valid = json.loads(_document().model_dump_json())
        with self.assertRaises(ValidationError):
            TrustedIssuerPolicyDocument.model_validate({**valid, "extra": 1})
        with self.assertRaises(ValidationError):
            TrustedIssuerPolicyDocument.model_validate(
                {**valid, "schema_version": "sklegal-issuer-policy/v0"}
            )


class IssuerPolicyStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name) / "policy-store"
        self.root.mkdir()

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_write_load_roundtrip_and_backend_snapshot(self) -> None:
        store = IssuerPolicyStore(self.root)
        store.write(_document(revision=1))
        document = store.load(1)
        self.assertEqual(document.revision, 1)
        backend = VersionedTrustedIssuerBackend(store, 1)
        snapshot = backend.snapshot()
        self.assertEqual(snapshot.policy_version, "sklegal-authz/v1")
        self.assertEqual(snapshot.issuers[0].fingerprint, DEDICATED_FPR)
        self.assertEqual(len(snapshot.revision), 64)

    def test_revisions_are_immutable_and_rotation_is_explicit(self) -> None:
        store = IssuerPolicyStore(self.root)
        store.write(_document(revision=1))
        with self.assertRaises(IssuerCustodyError):
            store.write(_document(revision=1))
        store.write(_document(revision=2, fingerprint=SECOND_FPR, supersedes=1))
        self.assertEqual(store.revisions(), (1, 2))
        rotated = VersionedTrustedIssuerBackend(store, 2)
        self.assertEqual(rotated.snapshot().issuers[0].fingerprint, SECOND_FPR)
        rolled_back = VersionedTrustedIssuerBackend(store, 1)
        self.assertEqual(rolled_back.snapshot().issuers[0].fingerprint, DEDICATED_FPR)

    def test_missing_malformed_and_mismatched_revisions_fail_closed(self) -> None:
        store = IssuerPolicyStore(self.root)
        with self.assertRaises(BackendUnavailable):
            store.load(9)
        (self.root / "issuer-policy-3.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(BackendUnavailable):
            store.load(3)
        store.write(_document(revision=4))
        raw = json.loads((self.root / "issuer-policy-4.json").read_text())
        raw["revision"] = 5
        (self.root / "issuer-policy-4.json").write_text(json.dumps(raw))
        with self.assertRaises(BackendUnavailable):
            store.load(4)
        (self.root / "issuer-policy-6.json").write_text(
            '{"a": 1, "a": 2}', encoding="utf-8"
        )
        with self.assertRaises(BackendUnavailable):
            store.load(6)

    def test_symlinked_policy_fails_closed(self) -> None:
        store = IssuerPolicyStore(self.root)
        store.write(_document(revision=1))
        target = self.root / "elsewhere.json"
        target.write_text(_document().model_dump_json(), encoding="utf-8")
        os.symlink(target, self.root / "issuer-policy-7.json")
        with self.assertRaises(BackendUnavailable):
            store.load(7)

    def test_revoked_revision_fails_closed_even_after_tampering(self) -> None:
        store = IssuerPolicyStore(self.root)
        store.write(_document(revision=1))
        store.revoke(1, reason="issuer-key-compromised")
        with self.assertRaises(BackendUnavailable):
            store.load(1)
        with self.assertRaises(BackendUnavailable):
            VersionedTrustedIssuerBackend(store, 1).snapshot()
        path = self.root / "issuer-policy-1.json"
        path.write_text(_document(revision=1, fingerprint=SECOND_FPR).model_dump_json())
        with self.assertRaises(BackendUnavailable):
            store.load(1)
        with self.assertRaises(IssuerCustodyError):
            store.write(_document(revision=1))
        with self.assertRaises(IssuerCustodyError):
            store.revoke(1, reason="again")

    def test_inactive_status_fails_closed(self) -> None:
        store = IssuerPolicyStore(self.root)
        store.write(_document(revision=1, status="revoked"))
        with self.assertRaises(BackendUnavailable):
            store.load(1)

    def test_store_root_must_be_a_real_directory(self) -> None:
        with self.assertRaises(BackendUnavailable):
            IssuerPolicyStore(self.root / "missing")
        target = self.root / "real"
        target.mkdir()
        link = self.root / "link"
        os.symlink(target, link)
        with self.assertRaises(BackendUnavailable):
            IssuerPolicyStore(link)


class VersionedBackendAuthorizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.store = IssuerPolicyStore(self.root)
        self.rig = CapabilityTestRig()

    def tearDown(self) -> None:
        self.rig.close()
        self._temp.cleanup()

    def _backend(self, revision: int = 1) -> VersionedTrustedIssuerBackend:
        return VersionedTrustedIssuerBackend(self.store, revision)

    def test_authorizer_allows_within_versioned_policy_ceilings(self) -> None:
        self.store.write(
            TrustedIssuerPolicyDocument(
                policy_version="sklegal-authz/v1",
                revision=1,
                status="active",
                issuers=(
                    TrustedIssuerGrant(
                        fingerprint=STUB_ISSUER_FPR,
                        capabilities=frozenset(Capability),
                        audiences=frozenset(Audience),
                        principal_types=frozenset(PrincipalType),
                    ),
                ),
            )
        )
        principal = self.rig.principal()
        grant = self.rig.grant()
        presented = self.rig.issue(principal, grant)
        authorizer = self.rig.new_authorizer(trusted_issuers=self._backend())
        self.rig.authorize(principal, grant, presented, authorizer=authorizer)

    def test_unknown_issuer_and_ceiling_breach_deny(self) -> None:
        self.store.write(
            TrustedIssuerPolicyDocument(
                policy_version="sklegal-authz/v1",
                revision=1,
                status="active",
                issuers=(_grant(DEDICATED_FPR),),
            )
        )
        principal = self.rig.principal()
        grant = self.rig.grant()
        presented = self.rig.issue(principal, grant)
        authorizer = self.rig.new_authorizer(trusted_issuers=self._backend())
        reason = self.rig.denied_reason(
            principal, grant, presented, authorizer=authorizer
        )
        self.assertEqual(reason, DecisionReason.UNTRUSTED_ISSUER)

        self.store.write(
            TrustedIssuerPolicyDocument(
                policy_version="sklegal-authz/v1",
                revision=2,
                status="active",
                supersedes=1,
                issuers=(
                    TrustedIssuerGrant(
                        fingerprint=STUB_ISSUER_FPR,
                        capabilities=frozenset({Capability.CLIENT_READ}),
                        audiences=frozenset({Audience.API}),
                        principal_types=frozenset({PrincipalType.HUMAN}),
                    ),
                ),
            )
        )
        authorizer = self.rig.new_authorizer(trusted_issuers=self._backend(2))
        reason = self.rig.denied_reason(
            principal, grant, presented, authorizer=authorizer
        )
        self.assertEqual(reason, DecisionReason.UNTRUSTED_ISSUER)

    def test_missing_and_revoked_policy_fail_closed_through_authorizer(self) -> None:
        principal = self.rig.principal()
        grant = self.rig.grant()
        presented = self.rig.issue(principal, grant)
        authorizer = self.rig.new_authorizer(trusted_issuers=self._backend(7))
        reason = self.rig.denied_reason(
            principal, grant, presented, authorizer=authorizer
        )
        self.assertEqual(reason, DecisionReason.BACKEND_UNAVAILABLE)

        self.store.write(
            TrustedIssuerPolicyDocument(
                policy_version="sklegal-authz/v1",
                revision=7,
                status="active",
                issuers=(
                    TrustedIssuerGrant(
                        fingerprint=STUB_ISSUER_FPR,
                        capabilities=frozenset(Capability),
                        audiences=frozenset(Audience),
                        principal_types=frozenset(PrincipalType),
                    ),
                ),
            )
        )
        self.store.revoke(7, reason="policy-superseded")
        reason = self.rig.denied_reason(
            principal, grant, presented, authorizer=authorizer
        )
        self.assertEqual(reason, DecisionReason.BACKEND_UNAVAILABLE)


class GpgAgentSigningHandleTest(unittest.TestCase):
    def _custody(self, home: Path) -> IssuerCustodyPolicy:
        return IssuerCustodyPolicy(
            issuer_fingerprint=DEDICATED_FPR,
            custody="gpg-agent",
            key_home=str(home),
        )

    def test_handle_requires_gpg_agent_custody(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            custody = IssuerCustodyPolicy(
                issuer_fingerprint=DEDICATED_FPR,
                custody="sidecar",
                socket_path=str(Path(temp) / "issuer.sock"),
            )
            with self.assertRaises(IssuerCustodyError):
                GpgAgentSigningHandle(custody)

    def test_readiness_reports_missing_home_and_missing_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "issuer-gpg"
            handle = GpgAgentSigningHandle(self._custody(home))
            report = handle.readiness()
            self.assertFalse(report.ready)
            self.assertEqual(report.fingerprint, DEDICATED_FPR)

            home.mkdir(mode=0o700)
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=b"", stderr=b""
            )
            with patch("subprocess.run", return_value=completed):
                report = handle.readiness()
            self.assertFalse(report.ready)
            self.assertEqual(report.detail, "issuer key is not held by the signer")

    def test_readiness_ready_when_agent_holds_the_dedicated_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "issuer-gpg"
            home.mkdir(mode=0o700)
            handle = GpgAgentSigningHandle(self._custody(home))
            listing = (
                "sec:u:255:22:0123456789ABCDEF:1780000000:::u:::sc:::::ed25519:::23:\n"
                f"fpr:::::::::{DEDICATED_FPR}:\n"
            ).encode()
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=listing, stderr=b""
            )
            with patch("subprocess.run", return_value=completed) as run:
                report = handle.readiness()
            self.assertTrue(report.ready)
            command = run.call_args.args[0]
            self.assertNotIn("--passphrase", command)
            self.assertNotIn("--pinentry-mode", command)

    def test_sign_returns_signature_and_never_carries_a_passphrase(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "issuer-gpg"
            home.mkdir(mode=0o700)
            handle = GpgAgentSigningHandle(self._custody(home))
            armor = b"-----BEGIN PGP SIGNATURE-----\nsynthetic\n-----END PGP SIGNATURE-----\n"
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=armor, stderr=b""
            )
            with patch("subprocess.run", return_value=completed) as run:
                signature = handle.sign(b"payload")
            self.assertEqual(signature, armor.decode())
            command = run.call_args.args[0]
            joined = " ".join(command)
            self.assertNotIn("--passphrase", joined)
            self.assertNotIn("--pinentry-mode", joined)
            self.assertIn("--batch", command)
            self.assertIn("--local-user", command)
            self.assertIn(DEDICATED_FPR, command)
            environment = run.call_args.kwargs.get("env")
            if environment is not None:
                self.assertNotIn("PASSPHRASE", json.dumps(environment))

    def test_sign_failures_are_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "issuer-gpg"
            home.mkdir(mode=0o700)
            handle = GpgAgentSigningHandle(self._custody(home))
            failures = (
                subprocess.CompletedProcess(
                    args=[], returncode=2, stdout=b"", stderr=b"secret key missing"
                ),
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=b"   \n", stderr=b""
                ),
            )
            for completed in failures:
                with patch("subprocess.run", return_value=completed):
                    with self.assertRaises(SigningUnavailable) as raised:
                        handle.sign(b"payload")
                self.assertNotIn("secret", str(raised.exception))
            with patch("subprocess.run", side_effect=OSError("gpg missing")):
                with self.assertRaises(SigningUnavailable) as raised:
                    handle.sign(b"payload")
            self.assertEqual(str(raised.exception), "issuer signer is unavailable")
            with patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="gpg", timeout=30),
            ):
                with self.assertRaises(SigningUnavailable):
                    handle.sign(b"payload")


if __name__ == "__main__":
    unittest.main()
