"""Strict versioned trusted issuer policy contract: fail-closed matrix.

Card `beadf98b` deepens the S1-03A1 policy contract. This module proves
that malformed, missing, changed, revoked, and rollback policy states all
fail closed, that parsing is strict and versioned, that fingerprint
allowlists carry exact ceilings, and that loading never follows symlinks
or falls back to stale state. No signing key is created or stored here.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from capauth.testing import STUB_ISSUER_FPR  # type: ignore[import-untyped]
from sklegal_capauth import (
    Audience,
    BackendUnavailable,
    Capability,
    DecisionReason,
    IssuerPolicyStore,
    PrincipalType,
    TrustedIssuerGrant,
    TrustedIssuerPolicyDocument,
    VersionedTrustedIssuerBackend,
)

from tests.support.capauth_contract import CapabilityTestRig

DEDICATED_FPR = "3" * 40
SUCCESSOR_FPR = "4" * 40


def _grant(
    fingerprint: str,
    *,
    capabilities: frozenset[Capability] | None = None,
    audiences: frozenset[Audience] | None = None,
    principal_types: frozenset[PrincipalType] | None = None,
) -> TrustedIssuerGrant:
    return TrustedIssuerGrant(
        fingerprint=fingerprint,
        capabilities=capabilities or frozenset({Capability.MATTER_READ}),
        audiences=audiences or frozenset({Audience.API}),
        principal_types=principal_types or frozenset({PrincipalType.HUMAN}),
    )


def _document(
    revision: int,
    *,
    fingerprint: str = DEDICATED_FPR,
    policy_version: str = "sklegal-authz/v1",
    status: str = "active",
    supersedes: int | None = None,
) -> TrustedIssuerPolicyDocument:
    return TrustedIssuerPolicyDocument(
        policy_version=policy_version,
        revision=revision,
        status=status,  # type: ignore[arg-type]
        supersedes=supersedes,
        issuers=(_grant(fingerprint),),
    )


class StrictParsingContractTest(unittest.TestCase):
    """Malformed policy bytes and envelopes fail closed."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.store = IssuerPolicyStore(self.root)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _write_raw(self, revision: int, content: str | bytes) -> None:
        path = self.root / f"issuer-policy-{revision}.json"
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_bytes(content)

    def _load_fails(self, revision: int) -> None:
        with self.assertRaises(BackendUnavailable):
            self.store.load(revision)

    def test_malformed_states_fail_closed(self) -> None:
        cases = {
            1: "{not json at all",
            2: "",
            3: "[]",
            4: '"just a string"',
            5: "null",
            6: json.dumps({"schema_version": "sklegal-issuer-policy/v1"}),
            7: json.dumps(
                {
                    "schema_version": "sklegal-issuer-policy/v1",
                    "policy_version": "sklegal-authz/v1",
                    "revision": 7,
                    "status": "active",
                    "issuers": [],
                    "unexpected": "field",
                }
            ),
            8: '{"revision": 8, "revision": 9}',
            9: json.dumps(
                {
                    "schema_version": "sklegal-issuer-policy/v1",
                    "policy_version": "sklegal-authz/v1",
                    "revision": "9",
                    "status": "active",
                    "issuers": [
                        {
                            "fingerprint": DEDICATED_FPR,
                            "capabilities": ["matter.read"],
                            "audiences": ["sklegal.api"],
                            "principal_types": ["human"],
                        }
                    ],
                }
            ),
        }
        for revision, content in cases.items():
            with self.subTest(revision=revision):
                self._write_raw(revision, content)
                self._load_fails(revision)

    def test_nested_duplicate_members_fail_closed(self) -> None:
        raw = (
            '{"schema_version": "sklegal-issuer-policy/v1",'
            ' "policy_version": "sklegal-authz/v1",'
            ' "revision": 1, "status": "active",'
            ' "issuers": [{"fingerprint": "' + DEDICATED_FPR + '",'
            ' "fingerprint": "' + SUCCESSOR_FPR + '",'
            ' "capabilities": ["matter.read"],'
            ' "audiences": ["sklegal.api"],'
            ' "principal_types": ["human"]}]}'
        )
        self._write_raw(1, raw)
        self._load_fails(1)

    def test_non_utf8_and_oversized_policy_fail_closed(self) -> None:
        self._write_raw(1, b"\xff\xfe binary not utf8")
        self._load_fails(1)
        valid = _document(2).model_dump_json()
        padding = " " * (70 * 1024)
        oversized = valid[:-1] + ', "pad": "' + padding + '"}'
        self._write_raw(2, oversized)
        self._load_fails(2)

    def test_missing_states_fail_closed(self) -> None:
        self._load_fails(42)
        with self.assertRaises(BackendUnavailable):
            IssuerPolicyStore(self.root / "absent")
        (self.root / "issuer-policy-3.json").mkdir()
        self._load_fails(3)

    def test_no_symlink_loading_for_documents_and_tombstones(self) -> None:
        target_dir = self.root / "elsewhere"
        target_dir.mkdir()
        target = target_dir / "policy.json"
        target.write_text(_document(1).model_dump_json(), encoding="utf-8")
        os.symlink(target, self.root / "issuer-policy-1.json")
        self._load_fails(1)

        self.store.write(_document(2))
        os.symlink(
            target_dir / "tombstone.json", self.root / "issuer-policy-2.revoked.json"
        )
        self._load_fails(2)

        real_root = self.root / "real-store"
        real_root.mkdir()
        os.symlink(real_root, self.root / "linked-store")
        with self.assertRaises(BackendUnavailable):
            IssuerPolicyStore(self.root / "linked-store")

    def test_hardlinked_policy_fails_closed(self) -> None:
        path = self.root / "issuer-policy-1.json"
        path.write_text(_document(1).model_dump_json(), encoding="utf-8")
        os.link(path, self.root / "hardlink-copy.json")
        self._load_fails(1)


class ChangedPolicyContractTest(unittest.TestCase):
    """A changed policy takes effect immediately; nothing stale survives."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.store = IssuerPolicyStore(self.root)
        self.rig = CapabilityTestRig()

    def tearDown(self) -> None:
        self.rig.close()
        self._temp.cleanup()

    def _document_for_stub(self, revision: int) -> TrustedIssuerPolicyDocument:
        return TrustedIssuerPolicyDocument(
            policy_version="sklegal-authz/v1",
            revision=revision,
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

    def test_tampered_document_changes_snapshot_and_denies(self) -> None:
        self.store.write(self._document_for_stub(1))
        backend = VersionedTrustedIssuerBackend(self.store, 1)
        before = backend.snapshot()

        principal = self.rig.principal()
        grant = self.rig.grant()
        presented = self.rig.issue(principal, grant)
        authorizer = self.rig.new_authorizer(trusted_issuers=backend)
        self.rig.authorize(principal, grant, presented, authorizer=authorizer)

        path = self.root / "issuer-policy-1.json"
        path.write_text(
            TrustedIssuerPolicyDocument(
                policy_version="sklegal-authz/v1",
                revision=1,
                status="active",
                issuers=(_grant(DEDICATED_FPR),),
            ).model_dump_json(),
            encoding="utf-8",
        )
        after = backend.snapshot()
        self.assertNotEqual(before.revision, after.revision)

        presented = self.rig.issue(principal, grant)
        reason = self.rig.denied_reason(
            principal, grant, presented, authorizer=authorizer
        )
        self.assertEqual(reason, DecisionReason.UNTRUSTED_ISSUER)

    def test_revision_renumbering_tamper_fails_closed(self) -> None:
        self.store.write(self._document_for_stub(2))
        path = self.root / "issuer-policy-2.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["revision"] = 99
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(BackendUnavailable):
            self.store.load(2)

    def test_snapshot_policy_version_passthrough(self) -> None:
        self.store.write(_document(1, policy_version="sklegal-authz/v9"))
        snapshot = VersionedTrustedIssuerBackend(self.store, 1).snapshot()
        self.assertEqual(snapshot.policy_version, "sklegal-authz/v9")


class RevokedAndRollbackContractTest(unittest.TestCase):
    """Revoked and rollback policy states fail closed."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.store = IssuerPolicyStore(self.root)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_revocation_tombstone_states_fail_closed(self) -> None:
        self.store.write(_document(1))
        self.store.revoke(1, reason="issuer-key-compromised")
        with self.assertRaises(BackendUnavailable):
            self.store.load(1)

        # A corrupt tombstone still closes the revision.
        self.store.write(_document(2))
        (self.root / "issuer-policy-2.revoked.json").write_text(
            "{corrupt", encoding="utf-8"
        )
        with self.assertRaises(BackendUnavailable):
            self.store.load(2)

        # A tombstone naming a different revision still closes it.
        self.store.write(_document(3))
        (self.root / "issuer-policy-3.revoked.json").write_text(
            json.dumps(
                {
                    "schema_version": "sklegal-issuer-policy-revocation/v1",
                    "revision": 30,
                    "reason": "wrong-revision",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(BackendUnavailable):
            self.store.load(3)

        # An in-document revoked status closes the revision too.
        self.store.write(_document(4, status="revoked"))
        with self.assertRaises(BackendUnavailable):
            self.store.load(4)

    def test_rollback_is_explicit_and_fails_closed_when_unsafe(self) -> None:
        self.store.write(_document(1))
        self.store.write(_document(2, fingerprint=SUCCESSOR_FPR, supersedes=1))

        current = VersionedTrustedIssuerBackend(self.store, 2)
        self.assertEqual(current.snapshot().issuers[0].fingerprint, SUCCESSOR_FPR)

        # Explicit rollback to the still active prior revision succeeds.
        rolled_back = VersionedTrustedIssuerBackend(self.store, 1)
        self.assertEqual(rolled_back.snapshot().issuers[0].fingerprint, DEDICATED_FPR)

        # Rollback to a revoked revision fails closed.
        self.store.revoke(1, reason="rollback-unsafe")
        with self.assertRaises(BackendUnavailable):
            rolled_back.snapshot()

        # Rollback to a missing revision fails closed.
        missing = VersionedTrustedIssuerBackend(self.store, 77)
        with self.assertRaises(BackendUnavailable):
            missing.snapshot()

        # Rollback to a tampered revision fails closed.
        self.store.write(_document(5))
        path = self.root / "issuer-policy-5.json"
        path.write_text("{tampered", encoding="utf-8")
        tampered = VersionedTrustedIssuerBackend(self.store, 5)
        with self.assertRaises(BackendUnavailable):
            tampered.snapshot()

    def test_ceiling_allowlist_matrix_through_authorizer(self) -> None:
        rig = CapabilityTestRig()
        self.addCleanup(rig.close)
        ceilings = (
            # (granted capability, granted audience, granted principal kind,
            #  requested capability, requested audience, requested kind, allow)
            (
                {Capability.MATTER_READ},
                {Audience.API},
                {PrincipalType.HUMAN},
                Capability.MATTER_READ,
                Audience.API,
                PrincipalType.HUMAN,
                True,
            ),
            (
                {Capability.CLIENT_READ},
                {Audience.API},
                {PrincipalType.HUMAN},
                Capability.MATTER_READ,
                Audience.API,
                PrincipalType.HUMAN,
                False,
            ),
            (
                {Capability.MATTER_READ},
                {Audience.MODEL},
                {PrincipalType.AGENT},
                Capability.MATTER_READ,
                Audience.TOOL,
                PrincipalType.AGENT,
                False,
            ),
            (
                {Capability.MATTER_READ},
                {Audience.API},
                {PrincipalType.SERVICE},
                Capability.MATTER_READ,
                Audience.API,
                PrincipalType.HUMAN,
                False,
            ),
        )
        revision = 0
        for (
            granted_caps,
            granted_auds,
            granted_kinds,
            requested_cap,
            requested_audience,
            requested_kind,
            allowed,
        ) in ceilings:
            revision += 1
            with self.subTest(
                requested_cap=requested_cap,
                requested_audience=requested_audience,
                requested_kind=requested_kind,
            ):
                self.store.write(
                    TrustedIssuerPolicyDocument(
                        policy_version="sklegal-authz/v1",
                        revision=revision,
                        status="active",
                        issuers=(
                            TrustedIssuerGrant(
                                fingerprint=STUB_ISSUER_FPR,
                                capabilities=frozenset(granted_caps),
                                audiences=frozenset(granted_auds),
                                principal_types=frozenset(granted_kinds),
                            ),
                        ),
                    )
                )
                backend = VersionedTrustedIssuerBackend(self.store, revision)
                authorizer = rig.new_authorizer(trusted_issuers=backend)
                principal = rig.principal(requested_kind)
                grant = rig.grant(audience=requested_audience, capability=requested_cap)
                presented = rig.issue(principal, grant)
                if allowed:
                    rig.authorize(principal, grant, presented, authorizer=authorizer)
                else:
                    reason = rig.denied_reason(
                        principal, grant, presented, authorizer=authorizer
                    )
                    self.assertEqual(reason, DecisionReason.UNTRUSTED_ISSUER)


if __name__ == "__main__":
    unittest.main()
