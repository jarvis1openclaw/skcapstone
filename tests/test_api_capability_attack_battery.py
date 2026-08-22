"""Capability forgery, replay, and mid-flight revocation attacks on the composition.

SKL-S5-04A scope item 3 (SKCapstone card 1cb2aa72). Every attack drives the
production composition seam ``build_postgres_capability_authorizer`` from
``services/api/src/sklegal_api/capauth.py`` over fake durable executor state,
so the durable adapters in ``sklegal_capauth.postgres``, the authorization
core, and the ``ProtectedRouteDependency`` HTTP boundary behave exactly as a
deployed API would exercise them. Synthetic material only: no live
PostgreSQL, no gpg, no network, no persisted credentials.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from capauth import TokenPayload  # type: ignore[import-untyped]
from capauth.testing import (  # type: ignore[import-untyped]
    STUB_ISSUER_FPR,
    stub_signature_for,
)
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sklegal_api.capauth import (
    ProtectedRouteDependency,
    build_postgres_capability_authorizer,
)
from sklegal_capauth import (
    ApiCapabilityBoundary,
    Audience,
    AuthorizationDenied,
    AuthorizedContext,
    Capability,
    CapabilityClaims,
    CapabilityGrant,
    DecisionReason,
    FileTrustedIssuerBackend,
    PresentedCapability,
    PrincipalContext,
    PrincipalType,
    Purpose,
    parse_presented_token,
)
from sklegal_capauth import tokens as token_module
from sklegal_capauth.authorization import DelegatingCapabilityIssuer, DelegationDenied
from sklegal_capauth.postgres import (
    PRINCIPAL_SNAPSHOT_SQL,
    REPLAY_PRUNE_SQL,
    REPLAY_RESERVE_SQL,
    REVOCATION_SNAPSHOT_SQL,
)
from sklegal_capauth.tokens import METADATA_KEY

from tests.support.capauth_contract import (
    TENANT_ID,
    CapabilityTestRig,
    boundary_scope,
    raw_leaf,
)

FOREIGN_TENANT = UUID("10000000-0000-4000-8000-000000000002")
FOREIGN_MATTER = UUID("20000000-0000-4000-8000-000000000002")


def _write_issuer_policy(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "sklegal-trusted-issuers/v1",
                "policy_version": "sklegal-authz/v1",
                "issuers": [
                    {
                        "fingerprint": STUB_ISSUER_FPR,
                        "capabilities": [item.value for item in Capability],
                        "audiences": [item.value for item in Audience],
                        "principal_types": [item.value for item in PrincipalType],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


class _FakeDurableState:
    """Synthetic durable state behind the composed adapters, with a mid-flight hook.

    Implements exactly the four SECURITY DEFINER functions the durable
    adapters call. ``on_reserve`` fires inside the replay reservation, the
    last durable interaction of one authorize call, so a test can mutate
    revocation or principal state mid-flight and observe the next decision.
    """

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._principals: dict[tuple[UUID, UUID], tuple[PrincipalContext, bool]] = {}
        self._revoked: set[str] = set()
        self._reservations: dict[tuple[UUID, str], datetime] = {}
        self._revision_counter = 0
        self.on_reserve: Callable[[str], None] | None = None

    def set_principal(self, principal: PrincipalContext, *, active: bool) -> None:
        with self._lock:
            key = (principal.tenant_id, principal.principal_id)
            self._principals[key] = (principal, active)
            self._revision_counter += 1

    def revoke(self, credential_digest: str) -> None:
        with self._lock:
            self._revoked.add(credential_digest)
            self._revision_counter += 1

    def _revision_locked(self) -> str:
        raw = str(self._revision_counter).encode("ascii")
        return hashlib.sha256(raw).hexdigest()

    def executor(self, sql: str, params: tuple[object, ...]) -> object:
        if sql == PRINCIPAL_SNAPSHOT_SQL:
            return self._principal_snapshot(params)
        if sql == REVOCATION_SNAPSHOT_SQL:
            return self._revocation_snapshot(params)
        if sql == REPLAY_RESERVE_SQL:
            return self._reserve(params)
        if sql == REPLAY_PRUNE_SQL:
            return self._prune(params)
        raise AssertionError(f"synthetic executor saw unexpected SQL: {sql}")

    def _principal_snapshot(self, params: tuple[object, ...]) -> object:
        tenant_id, principal_id = params
        with self._lock:
            record = self._principals.get((tenant_id, principal_id))  # type: ignore[arg-type]
            if record is None:
                raise RuntimeError("synthetic principal lookup failed")
            principal, active = record
            revision = self._revision_locked()
        return {
            "revision": revision,
            "principal": {
                "principal_id": str(principal.principal_id),
                "principal_type": principal.principal_type.value,
                "subject": principal.subject,
                "tenant_id": str(principal.tenant_id),
            },
            "active": active,
        }

    def _revocation_snapshot(self, params: tuple[object, ...]) -> object:
        _tenant_id, digests = params
        requested = set(digests)  # type: ignore[arg-type]
        with self._lock:
            revoked = sorted(requested & self._revoked)
            revision = self._revision_locked()
        return {"revision": revision, "revoked_credential_digests": revoked}

    def _reserve(self, params: tuple[object, ...]) -> object:
        tenant_id, digest, _decision_id, expires_at = params
        hook = self.on_reserve
        if hook is not None:
            hook(digest)  # type: ignore[arg-type]
        with self._lock:
            now = self._clock()
            self._reservations = {
                key: expiry
                for key, expiry in self._reservations.items()
                if expiry >= now
            }
            key = (tenant_id, digest)  # type: ignore[arg-type]
            if key in self._reservations:
                return (False,)
            self._reservations[key] = expires_at  # type: ignore[assignment]
            return (True,)

    def _prune(self, params: tuple[object, ...]) -> object:
        (tenant_id,) = params
        with self._lock:
            now = self._clock()
            kept = {
                key: expiry
                for key, expiry in self._reservations.items()
                if key[0] == tenant_id and expiry >= now
            }
            pruned = len(self._reservations) - len(kept)
            self._reservations = kept
        return (pruned,)


class _RecordingAuditSink:
    """Thread-safe protocol sink standing in for the durable audit adapter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._decisions: list[object] = []

    def record(self, decision: object) -> None:
        with self._lock:
            self._decisions.append(decision)

    def decisions(self) -> tuple[object, ...]:
        with self._lock:
            return tuple(self._decisions)


class _RaisingAuditSink:
    """Passes factory validation but fails every durable audit write."""

    def record(self, decision: object) -> None:
        del decision
        raise RuntimeError("synthetic audit sink outage")


class _AttackEnvironment:
    """One factory-built authorizer over fake durable state, for attack tests."""

    def __init__(self, test: unittest.TestCase, *, audit: object | None = None) -> None:
        self.rig = CapabilityTestRig()
        test.addCleanup(self.rig.close)
        self.state = _FakeDurableState(self.rig.clock)
        tempdir = tempfile.TemporaryDirectory(prefix="sklegal-attacks-")
        test.addCleanup(tempdir.cleanup)
        policy_path = Path(tempdir.name) / "trusted-issuers.json"
        _write_issuer_policy(policy_path)
        self.audit = audit if audit is not None else _RecordingAuditSink()
        self.authorizer = build_postgres_capability_authorizer(
            executor=self.state.executor,
            trusted_issuers=FileTrustedIssuerBackend(policy_path),
            audit=self.audit,  # type: ignore[arg-type]
            tenant_id=TENANT_ID,
            clock=self.rig.clock,
        )

    def bind_principal(
        self, principal_type: PrincipalType = PrincipalType.HUMAN
    ) -> PrincipalContext:
        principal = self.rig.principal(principal_type)
        self.state.set_principal(principal, active=True)
        return principal

    def allow(
        self,
        principal: PrincipalContext,
        grant: CapabilityGrant,
        presented: PresentedCapability,
    ) -> AuthorizedContext:
        return self.authorizer.authorize(presented, self.rig.request(principal, grant))

    def denied(
        self,
        principal: PrincipalContext,
        grant: CapabilityGrant,
        presented: PresentedCapability,
    ) -> DecisionReason:
        try:
            self.authorizer.authorize(presented, self.rig.request(principal, grant))
        except AuthorizationDenied as exc:
            return exc.decision.reason_code
        raise AssertionError("attack unexpectedly succeeded")


def _mutated_wire_payload(
    raw: str,
    mutate: Callable[[dict[str, object]], None],
) -> dict[str, object]:
    envelope = json.loads(raw)
    payload_data = envelope["payload"]  # type: ignore[index]
    mutate(payload_data)  # type: ignore[arg-type]
    return envelope  # type: ignore[return-value]


def _rebound_payload(payload_data: dict[str, object]) -> TokenPayload:
    """Rebuild a payload exactly the way ``parse_presented_token`` reconstructs it.

    The parser re-validates the closed claims contract and rebuilds metadata
    as ``claims.model_dump(mode="json")``, so signature verification bytes are
    model-ordered. A forged credential that means to survive verification must
    be signed over that same construction, not over wire-sorted bytes.
    """

    claims = CapabilityClaims.model_validate_json(
        json.dumps(
            payload_data["metadata"][METADATA_KEY],  # type: ignore[index,union-attr]
            separators=(",", ":"),
            ensure_ascii=True,
        )
    )
    source = TokenPayload.model_validate(payload_data)
    rebuilt = TokenPayload(
        token_id="0" * 64,
        token_type=source.token_type,
        issuer=source.issuer,
        subject=source.subject,
        capabilities=list(source.capabilities),
        issued_at=source.issued_at,
        expires_at=source.expires_at,
        not_before=source.not_before,
        metadata={METADATA_KEY: claims.model_dump(mode="json")},
        audience=source.audience,
    )
    rebuilt.token_id = token_module._payload_identity(rebuilt)
    return rebuilt


def _forged(
    raw: str,
    mutate: Callable[[dict[str, object]], None],
) -> PresentedCapability:
    """Tamper claims, recompute the unkeyed payload identity, keep the old signature.

    The payload identity is an unkeyed hash any holder can recompute, so this
    models the strongest tamper that stops short of holding signing material.
    """

    envelope = _mutated_wire_payload(raw, mutate)
    payload_data = envelope["payload"]  # type: ignore[index]
    rebuilt = _rebound_payload(payload_data)  # type: ignore[arg-type]
    envelope["payload"] = rebuilt.model_dump(mode="json")  # type: ignore[index]
    return PresentedCapability.single(
        json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    )


def _resigned(
    raw: str,
    mutate: Callable[[dict[str, object]], None],
) -> PresentedCapability:
    """Tamper claims and sign with obtained issuer material (the stub signer).

    Models a leaked or misused signing key: the signature is cryptographically
    valid, so only the request-binding and policy checks can stop the token.
    """

    envelope = _mutated_wire_payload(raw, mutate)
    payload_data = envelope["payload"]  # type: ignore[index]
    rebuilt = _rebound_payload(payload_data)  # type: ignore[arg-type]
    envelope["payload"] = rebuilt.model_dump(mode="json")  # type: ignore[index]
    envelope["signature"] = stub_signature_for(  # type: ignore[index]
        rebuilt.model_dump_json().encode("utf-8")
    )
    return PresentedCapability.single(
        json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    )


def _set_grant_field(field: str, value: object) -> Callable[[dict[str, object]], None]:
    def mutate(payload: dict[str, object]) -> None:
        claims = payload["metadata"][METADATA_KEY]  # type: ignore[index]
        claims["grant"][field] = value  # type: ignore[index]

    return mutate


def _rebind_both_tenants(tenant: UUID) -> Callable[[dict[str, object]], None]:
    """Re-point the whole credential at a foreign tenant.

    The claims contract requires principal and grant tenants to match, so a
    cross-tenant forgery must rewrite both. Anything weaker dies at claims
    validation; this is the strongest coherent tenant rebind.
    """

    def mutate(payload: dict[str, object]) -> None:
        claims = payload["metadata"][METADATA_KEY]  # type: ignore[index]
        claims["principal"]["tenant_id"] = str(tenant)  # type: ignore[index]
        claims["grant"]["tenant_id"] = str(tenant)  # type: ignore[index]

    return mutate


def _swap_principal(
    replacement: PrincipalContext,
) -> Callable[[dict[str, object]], None]:
    """Re-point the whole credential at a real, durably registered principal.

    A partial swap (principal_id only) is caught earlier as PRINCIPAL_REBOUND
    because the subject no longer matches the durable record. Swapping every
    principal field keeps the claims internally consistent so the probe
    reaches the layer under attack.
    """

    def mutate(payload: dict[str, object]) -> None:
        claims = payload["metadata"][METADATA_KEY]  # type: ignore[index]
        claims["principal"] = replacement.model_dump(mode="json")  # type: ignore[index]
        payload["subject"] = replacement.subject  # type: ignore[index]

    return mutate


def _wire_tampered(
    raw: str,
    mutate: Callable[[dict[str, object]], None],
) -> PresentedCapability:
    """Rewrite wire bytes only: no identity recompute, stale signature kept."""

    envelope = json.loads(raw)
    mutate(envelope["payload"])  # type: ignore[arg-type]
    return PresentedCapability.single(
        json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    )


def _signed_raw_mutation(
    raw: str,
    mutate: Callable[[dict[str, object]], None],
) -> PresentedCapability:
    """Tamper wire bytes and stub-sign the exact parser-reconstructed payload.

    Unlike ``_resigned`` this does not enforce the SKLegal claims contract
    while rebuilding, so it can produce mutations that only the parser's own
    claims validation rejects. The signature is valid over the bytes the
    verifier would reconstruct; whether it is ever consulted is exactly what
    the probe observes.
    """

    envelope = json.loads(raw)
    payload_data = envelope["payload"]  # type: ignore[index]
    mutate(payload_data)  # type: ignore[arg-type]
    payload = TokenPayload.model_validate(payload_data)
    payload.token_id = token_module._payload_identity(payload)
    envelope["payload"] = payload.model_dump(mode="json")  # type: ignore[index]
    envelope["signature"] = stub_signature_for(  # type: ignore[index]
        payload.model_dump_json().encode("utf-8")
    )
    return PresentedCapability.single(
        json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    )


class CapabilityForgeryAgainstCompositionTest(unittest.TestCase):
    """Forged and re-bound credentials must fail closed through the composition."""

    def setUp(self) -> None:
        self.env = _AttackEnvironment(self)
        self.principal = self.env.bind_principal()
        self.grant = self.env.rig.grant(target="api:matter.get")

    def test_stale_signature_over_tampered_payload_is_denied(self) -> None:
        env = self.env
        other = env.bind_principal()
        token = env.rig.issue(self.principal, self.grant)
        raw = raw_leaf(token)
        # Every mutation keeps the claims internally consistent so the probe
        # reaches the layer under attack instead of dying at claims parsing.
        mutations = {
            "matter": _set_grant_field("matter_id", str(FOREIGN_MATTER)),
            # Full swap onto a real second principal: the durable snapshot
            # succeeds and the stale signature is what fails.
            "principal": _swap_principal(other),
            "credential_nonce": (
                lambda payload: payload["metadata"][METADATA_KEY]  # type: ignore[index,return-value]
                .__setitem__("credential_nonce", "e" * 32)
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                forged = _forged(raw, mutate)
                self.assertEqual(
                    env.denied(self.principal, self.grant, forged),
                    DecisionReason.INVALID_SIGNATURE,
                )
        # A tenant re-point has no durable principal under the foreign tenant,
        # so the SECURITY DEFINER snapshot fails closed before any signature
        # check: the same fail-closed outcome the SQL scope guard produces.
        with self.subTest(mutation="tenant"):
            self.assertEqual(
                env.denied(
                    self.principal,
                    self.grant,
                    _forged(raw, _rebind_both_tenants(FOREIGN_TENANT)),
                ),
                DecisionReason.BACKEND_UNAVAILABLE,
            )
        # Tampering the expiry beyond the one-hour TTL cap is caught even
        # earlier: the time window is validated before signatures.
        with self.subTest(mutation="extended_expiry"):
            extended = _forged(
                raw,
                lambda payload: payload.__setitem__(
                    "expires_at", "2026-08-20T13:00:01Z"
                ),
            )
            self.assertEqual(
                env.denied(self.principal, self.grant, extended),
                DecisionReason.TTL_EXCEEDED,
            )
        decisions = env.audit.decisions()  # type: ignore[attr-defined]
        self.assertEqual(
            [item.allow for item in decisions],  # type: ignore[attr-defined]
            [False] * (len(mutations) + 2),
        )

    def test_absent_or_garbage_signature_is_denied(self) -> None:
        env = self.env
        token = env.rig.issue(self.principal, self.grant)
        envelope = json.loads(raw_leaf(token))
        cases: dict[str, str] = {}
        emptied = dict(envelope)
        emptied["signature"] = ""
        cases["empty"] = json.dumps(emptied, sort_keys=True, separators=(",", ":"))
        garbage = dict(envelope)
        garbage["signature"] = "attacker-forged-signature-value"
        cases["garbage"] = json.dumps(garbage, sort_keys=True, separators=(",", ":"))
        stripped = {key: value for key, value in envelope.items() if key != "signature"}
        cases["missing"] = json.dumps(stripped, sort_keys=True, separators=(",", ":"))
        fail_closed = {
            DecisionReason.UNSIGNED_CREDENTIAL,
            DecisionReason.INVALID_SIGNATURE,
            DecisionReason.MALFORMED_CREDENTIAL,
        }
        for name, forged_raw in cases.items():
            with self.subTest(signature=name):
                reason = env.denied(
                    self.principal,
                    self.grant,
                    PresentedCapability.single(forged_raw),
                )
                self.assertIn(reason, fail_closed)

    def test_self_declared_issuer_is_denied_even_with_a_valid_signature(self) -> None:
        env = self.env
        token = env.rig.issue(self.principal, self.grant)
        raw = raw_leaf(token)
        spoofs = {
            "valid_signature_over_foreign_issuer": _resigned(
                raw, lambda payload: payload.__setitem__("issuer", "AB" * 32)
            ),
            "stale_signature_over_foreign_issuer": _forged(
                raw, lambda payload: payload.__setitem__("issuer", "AB" * 32)
            ),
        }
        for name, forged in spoofs.items():
            with self.subTest(spoof=name):
                self.assertEqual(
                    env.denied(self.principal, self.grant, forged),
                    DecisionReason.UNTRUSTED_ISSUER,
                )

    def test_cross_principal_token_reuse_is_denied(self) -> None:
        env = self.env
        holder = self.principal
        attacker = env.bind_principal()
        token = env.rig.issue(holder, self.grant)
        # A stolen bearer presented under the attacker's session must fail
        # request binding; the durable snapshot of the token's own principal
        # still succeeds, so the denial is exactly WRONG_PRINCIPAL.
        self.assertEqual(
            env.denied(attacker, self.grant, token),
            DecisionReason.WRONG_PRINCIPAL,
        )

    def test_validly_resigned_credential_still_binds_to_the_request(self) -> None:
        env = self.env
        other = env.bind_principal()
        token = env.rig.issue(self.principal, self.grant)
        raw = raw_leaf(token)
        cases = {
            "matter": (
                _set_grant_field("matter_id", str(FOREIGN_MATTER)),
                DecisionReason.WRONG_MATTER,
            ),
            "target": (
                _set_grant_field("target", "api:evil.route"),
                DecisionReason.WRONG_TARGET,
            ),
            # Even a perfectly forged, validly re-signed credential naming a
            # real second principal cannot be consumed under this request.
            "principal": (_swap_principal(other), DecisionReason.WRONG_PRINCIPAL),
            # A tenant re-point has no durable principal under the foreign
            # tenant, so the snapshot fails closed rather than leaking which
            # layer would have caught a well-formed cross-tenant credential.
            "tenant": (
                _rebind_both_tenants(FOREIGN_TENANT),
                DecisionReason.BACKEND_UNAVAILABLE,
            ),
        }
        for name, (mutate, expected) in cases.items():
            with self.subTest(rebind=name):
                forged = _resigned(raw, mutate)
                self.assertEqual(env.denied(self.principal, self.grant, forged), expected)

        # The request side is also structurally guarded: AuthorizationRequest
        # itself refuses a grant whose tenant differs from the authenticated
        # principal, so a cross-tenant request cannot even be constructed.
        foreign_grant = env.rig.grant(
            target="api:matter.get", tenant_id=FOREIGN_TENANT
        )
        with self.assertRaises(ValidationError):
            env.rig.request(self.principal, foreign_grant)

    def test_purpose_forgery_is_structurally_impossible(self) -> None:
        """Every capability admits exactly one purpose, so none can be re-pointed.

        The claims validator ties each capability to its single purpose, so a
        purpose forgery cannot even form a valid credential: with either a
        stale or a freshly valid signature, parse-level claims validation
        fails closed first.
        """
        env = self.env
        token = env.rig.issue(self.principal, self.grant)
        raw = raw_leaf(token)
        foreign_purpose = _set_grant_field("purpose", Purpose.LEGAL_RESEARCH.value)
        for name, forged in (
            ("stale_signature", _wire_tampered(raw, foreign_purpose)),
            ("valid_signature", _signed_raw_mutation(raw, foreign_purpose)),
        ):
            with self.subTest(signature=name):
                self.assertEqual(
                    env.denied(self.principal, self.grant, forged),
                    DecisionReason.MALFORMED_CREDENTIAL,
                )

    def test_malformed_credential_shapes_are_denied(self) -> None:
        env = self.env
        for raw in ("{}", "not-json", "[]", '{"payload": {}}'):
            with self.subTest(raw=raw):
                self.assertEqual(
                    env.denied(
                        self.principal,
                        self.grant,
                        PresentedCapability.single(raw),
                    ),
                    DecisionReason.MALFORMED_CREDENTIAL,
                )


class ReplayAgainstCompositionTest(unittest.TestCase):
    """One-use credentials must never authorize twice through the composition."""

    def setUp(self) -> None:
        self.env = _AttackEnvironment(self)
        self.principal = self.env.bind_principal()
        self.grant = self.env.rig.grant(target="api:matter.get")

    def test_sequential_replay_denies_every_repeat(self) -> None:
        env = self.env
        token = env.rig.issue(self.principal, self.grant)
        self.assertTrue(env.allow(self.principal, self.grant, token).decision.allow)
        for _attempt in range(3):
            self.assertEqual(
                env.denied(self.principal, self.grant, token),
                DecisionReason.REPLAYED,
            )
        decisions = env.audit.decisions()  # type: ignore[attr-defined]
        self.assertEqual([item.allow for item in decisions], [True, False, False, False])  # type: ignore[attr-defined]

    def test_delegation_consumes_the_parent_reservation(self) -> None:
        env = self.env
        parent = env.bind_principal(PrincipalType.SERVICE)
        child_principal = env.bind_principal(PrincipalType.SERVICE)
        root = env.rig.issue(parent, self.grant, max_delegation_depth=1)
        delegator = DelegatingCapabilityIssuer(
            authorizer=env.authorizer,
            issuer=env.rig.issuer,
        )
        child = delegator.delegate(
            parent=root,
            authenticated_parent=parent,
            child_principal=child_principal,
            child_grant=self.grant,
            correlation_id=uuid4(),
            ttl_seconds=60,
        )

        # The delegation itself consumed the parent's single use.
        self.assertEqual(
            env.denied(parent, self.grant, root),
            DecisionReason.REPLAYED,
        )
        with self.assertRaises(DelegationDenied):
            delegator.delegate(
                parent=root,
                authenticated_parent=parent,
                child_principal=child_principal,
                child_grant=self.grant,
                correlation_id=uuid4(),
                ttl_seconds=60,
            )

        # The minted child allows exactly once and never replays.
        self.assertTrue(
            env.allow(child_principal, self.grant, child).decision.allow
        )
        self.assertEqual(
            env.denied(child_principal, self.grant, child),
            DecisionReason.REPLAYED,
        )

    def test_replayed_credential_cannot_be_laundered_as_a_fresh_bearer(self) -> None:
        env = self.env
        token = env.rig.issue(self.principal, self.grant)
        raw = raw_leaf(token)
        self.assertTrue(env.allow(self.principal, self.grant, token).decision.allow)
        # Re-presenting the exported bytes is the same one-use credential.
        relaunched = PresentedCapability.single(
            json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"))
        )
        self.assertEqual(
            env.denied(self.principal, self.grant, relaunched),
            DecisionReason.REPLAYED,
        )
        # Only a genuinely new issuance (later issued_at, new token identity)
        # is a different credential and allows again.
        env.rig.clock.advance(seconds=1)
        fresh = env.rig.issue(self.principal, self.grant)
        self.assertTrue(env.allow(self.principal, self.grant, fresh).decision.allow)


class RevocationDuringFlightTest(unittest.TestCase):
    """Mid-call durable writes land after the snapshot read; the next decision closes.

    Ordering is honest and deliberate: one authorize call reads the revocation
    and principal snapshots before it reserves replay, so a write that lands
    during that same call can still produce one allow. The property under
    attack is whether any window, cache, or single-decision state survives
    into the NEXT decision. None may.
    """

    def setUp(self) -> None:
        self.env = _AttackEnvironment(self)
        self.principal = self.env.bind_principal()
        self.grant = self.env.rig.grant(target="api:matter.get")
        self.token = self.env.rig.issue(self.principal, self.grant)
        self.digest = parse_presented_token(raw_leaf(self.token)).credential_digest

    def test_leaf_revoked_during_reservation_lands_then_denies(self) -> None:
        env = self.env
        token = self.token

        def revoke_mid_flight(reserved_digest: str) -> None:
            if reserved_digest == self.digest:
                env.state.revoke(self.digest)

        env.state.on_reserve = revoke_mid_flight
        context = env.allow(self.principal, self.grant, token)
        self.assertTrue(context.decision.allow)

        env.state.on_reserve = None
        self.assertEqual(
            env.denied(self.principal, self.grant, token),
            DecisionReason.REVOKED,
        )
        decisions = env.audit.decisions()  # type: ignore[attr-defined]
        self.assertEqual(
            [item.allow for item in decisions],  # type: ignore[attr-defined]
            [True, False],
        )

    def test_ancestor_revoked_during_child_reservation(self) -> None:
        env = self.env
        parent = env.bind_principal(PrincipalType.SERVICE)
        child_principal = env.bind_principal(PrincipalType.SERVICE)
        root = env.rig.issue(parent, self.grant, max_delegation_depth=1)
        root_digest = parse_presented_token(raw_leaf(root)).credential_digest
        delegator = DelegatingCapabilityIssuer(
            authorizer=env.authorizer,
            issuer=env.rig.issuer,
        )
        child = delegator.delegate(
            parent=root,
            authenticated_parent=parent,
            child_principal=child_principal,
            child_grant=self.grant,
            correlation_id=uuid4(),
            ttl_seconds=60,
        )
        child_digest = parse_presented_token(raw_leaf(child)).credential_digest

        def revoke_parent_mid_flight(reserved_digest: str) -> None:
            if reserved_digest == child_digest:
                env.state.revoke(root_digest)

        env.state.on_reserve = revoke_parent_mid_flight
        context = env.allow(child_principal, self.grant, child)
        self.assertTrue(context.decision.allow)

        env.state.on_reserve = None
        self.assertEqual(
            env.denied(child_principal, self.grant, child),
            DecisionReason.ANCESTOR_REVOKED,
        )

    def test_principal_suspended_during_reservation(self) -> None:
        env = self.env
        env.state.on_reserve = lambda _digest: env.state.set_principal(
            self.principal, active=False
        )
        context = env.allow(self.principal, self.grant, self.token)
        self.assertTrue(context.decision.allow)

        env.state.on_reserve = None
        self.assertEqual(
            env.denied(self.principal, self.grant, self.token),
            DecisionReason.PRINCIPAL_INACTIVE,
        )


class AuditWriteFailureTest(unittest.TestCase):
    """An allow whose audit write fails must be rescinded, never silently kept."""

    def test_allow_is_rescinded_when_the_audit_write_fails(self) -> None:
        env = _AttackEnvironment(self, audit=_RaisingAuditSink())
        principal = env.bind_principal()
        grant = env.rig.grant()
        token = env.rig.issue(principal, grant)
        with self.assertRaises(AuthorizationDenied) as raised:
            env.allow(principal, grant, token)
        decision = raised.exception.decision
        self.assertFalse(decision.allow)
        self.assertEqual(decision.reason_code, DecisionReason.AUDIT_UNAVAILABLE)


class HttpCompositionAttackSurfaceTest(unittest.TestCase):
    """Forged bearers against ProtectedRouteDependency over the composition."""

    def setUp(self) -> None:
        self.env = _AttackEnvironment(self)
        self.principal = self.env.bind_principal()
        self.grant = self.env.rig.grant(
            target="api:matter.get",
            purpose=Purpose.MATTER_MANAGEMENT,
        )
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=self.env.authorizer,
            route_name="matter.get",
            capability=Capability.MATTER_READ,
            purpose=Purpose.MATTER_MANAGEMENT,
        )
        dependency = ProtectedRouteDependency(
            boundary=boundary,
            principal_resolver=lambda _request: self.principal,
            scope_resolver=lambda _request: boundary_scope(self.grant),
        )
        app = FastAPI()

        @app.get("/matter")
        async def get_matter(
            authorized: object = Depends(dependency),
        ) -> dict[str, str]:
            del authorized
            return {"status": "ok"}

        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _get(self, bearer: str):
        return self.client.get("/matter", headers={"Authorization": f"Bearer {bearer}"})

    def test_forged_bearers_are_rejected_without_material_leakage(self) -> None:
        token = self.env.rig.issue(self.principal, self.grant)
        raw = raw_leaf(token)
        tampered = raw_leaf(
            _forged(raw, _rebind_both_tenants(FOREIGN_TENANT))
        )
        foreign_issuer = raw_leaf(
            _resigned(raw, lambda payload: payload.__setitem__("issuer", "AB" * 32))
        )
        for name, headers in (
            ("tampered_payload", {"Authorization": f"Bearer {tampered}"}),
            ("foreign_issuer", {"Authorization": f"Bearer {foreign_issuer}"}),
            ("malformed", {"Authorization": "Bearer {}"}),
            ("wrong_scheme", {"Authorization": f"Basic {raw}"}),
        ):
            with self.subTest(bearer=name):
                response = self.client.get("/matter", headers=headers)
                self.assertEqual(response.status_code, 403)
                detail = response.json()["detail"]
                self.assertEqual(detail["code"], "capability_denied")
                self.assertIn("decision_id", detail)
                body = response.text
                self.assertNotIn(raw, body)
                self.assertNotIn(str(FOREIGN_TENANT), body)
                self.assertNotIn(METADATA_KEY, body)

    def test_replayed_bearer_is_rejected_over_the_composition(self) -> None:
        token = self.env.rig.issue(self.principal, self.grant)
        bearer = raw_leaf(token)
        allowed = self._get(bearer)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json(), {"status": "ok"})

        replayed = self._get(bearer)
        self.assertEqual(replayed.status_code, 403)
        self.assertEqual(replayed.json()["detail"]["code"], "capability_denied")
        self.assertNotIn(bearer, replayed.text)

        decisions = self.env.audit.decisions()  # type: ignore[attr-defined]
        self.assertEqual([item.allow for item in decisions], [True, False])  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
