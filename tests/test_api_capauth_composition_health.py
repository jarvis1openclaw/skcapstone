"""Composition health, revocation readiness, and lifecycle race coverage.

Covers the S1-03A production-composition family (SKCapstone cards 4ff4d96f,
abe7b9cd, and d0e59165) with synthetic and fake backends only: no live
PostgreSQL, no gpg, and no network. The factory seam under test is
``build_postgres_capability_authorizer`` in ``services/api/src/sklegal_api/
capauth.py`` driving the durable adapters in ``sklegal_capauth.postgres``
through a fake SQL executor.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from capauth.testing import STUB_ISSUER_FPR  # type: ignore[import-untyped]
from sklegal_api.capauth import build_postgres_capability_authorizer
from sklegal_capauth import (
    Audience,
    AuthorizationDecision,
    AuthorizationDenied,
    AuthorizedContext,
    Capability,
    CapabilityGrant,
    DecisionReason,
    FileTrustedIssuerBackend,
    InMemoryAuditSink,
    PrincipalContext,
    PrincipalType,
    StaticTrustedIssuerBackend,
    UnavailableAuditSink,
    UnavailableTrustedIssuerBackend,
    parse_presented_token,
)
from sklegal_capauth.postgres import (
    PRINCIPAL_SNAPSHOT_SQL,
    REPLAY_PRUNE_SQL,
    REPLAY_RESERVE_SQL,
    REVOCATION_SNAPSHOT_SQL,
    PostgresPrincipalPolicyBackend,
    PostgresReplayBackend,
    PostgresRevocationBackend,
)

from tests.support.capauth_contract import (
    TENANT_ID,
    CapabilityTestRig,
    raw_leaf,
)


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


def _unused_executor(_sql: str, _params: tuple[object, ...]) -> object:
    raise AssertionError("factory construction must not touch the executor")


def _fixed_clock() -> datetime:
    return datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


class _RecordingAuditSink:
    """Thread-safe protocol sink standing in for the durable audit adapter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._decisions: list[AuthorizationDecision] = []

    def record(self, decision: AuthorizationDecision) -> None:
        with self._lock:
            self._decisions.append(decision)

    def decisions(self) -> tuple[AuthorizationDecision, ...]:
        with self._lock:
            return tuple(self._decisions)


class _FakePostgresState:
    """Thread-safe synthetic durable state behind the factory executor seam.

    Implements exactly the four SECURITY DEFINER functions the durable
    adapters call, with the same fail-closed surface: any internal error
    raises, and the adapter converts it to ``BackendUnavailable``.
    """

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._principals: dict[tuple[UUID, UUID], tuple[PrincipalContext, bool]] = {}
        self._revoked: set[str] = set()
        self._reservations: dict[tuple[UUID, str], datetime] = {}
        self._revision_counter = 0
        self.poisoned_digests: set[str] = set()
        self.outage_sql: set[str] = set()
        self.calls: list[tuple[str, tuple[object, ...]]] = []

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
        with self._lock:
            self.calls.append((sql, params))
            outage = sql in self.outage_sql
        if outage:
            raise RuntimeError("synthetic database outage")
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
            if requested & self.poisoned_digests:
                raise RuntimeError("synthetic revocation read failure")
            revoked = sorted(requested & self._revoked)
            revision = self._revision_locked()
        return {"revision": revision, "revoked_credential_digests": revoked}

    def _reserve(self, params: tuple[object, ...]) -> object:
        tenant_id, digest, _decision_id, expires_at = params
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


class _ComposedEnvironment:
    """One factory-built authorizer over fake durable executor state."""

    def __init__(self, test: unittest.TestCase) -> None:
        self.rig = CapabilityTestRig()
        test.addCleanup(self.rig.close)
        self.state = _FakePostgresState(self.rig.clock)
        tempdir = tempfile.TemporaryDirectory(prefix="sklegal-composition-")
        test.addCleanup(tempdir.cleanup)
        self.policy_path = Path(tempdir.name) / "trusted-issuers.json"
        _write_issuer_policy(self.policy_path)
        self.audit = _RecordingAuditSink()
        self.authorizer = build_postgres_capability_authorizer(
            executor=self.state.executor,
            trusted_issuers=FileTrustedIssuerBackend(self.policy_path),
            audit=self.audit,
            tenant_id=TENANT_ID,
            clock=self.rig.clock,
        )

    def bind_principal(
        self, principal_type: PrincipalType = PrincipalType.HUMAN
    ) -> PrincipalContext:
        principal = self.rig.principal(principal_type)
        self.state.set_principal(principal, active=True)
        return principal

    def denied_reason(
        self,
        principal: PrincipalContext,
        grant: CapabilityGrant,
        presented: object,
    ) -> DecisionReason:
        return self.rig.denied_reason(
            principal,
            grant,
            presented,  # type: ignore[arg-type]
            authorizer=self.authorizer,
        )


class CompositionFactoryReadinessTest(unittest.TestCase):
    """Card abe7b9cd: the factory must fail closed on bad production wiring.

    The baseline uses only components a production-readiness validator should
    accept: a callable executor, a reloadable file-backed issuer policy, a
    protocol-conforming audit sink, a real tenant UUID, and a callable clock.
    Each case varies exactly one argument into a missing, synthetic, or
    misconfigured value and requires construction to refuse it.
    """

    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory(prefix="sklegal-factory-gate-")
        self.addCleanup(tempdir.cleanup)
        policy_path = Path(tempdir.name) / "trusted-issuers.json"
        _write_issuer_policy(policy_path)
        self._baseline: dict[str, object] = {
            "executor": _unused_executor,
            "trusted_issuers": FileTrustedIssuerBackend(policy_path),
            "audit": _RecordingAuditSink(),
            "tenant_id": TENANT_ID,
            "clock": _fixed_clock,
        }

    def _build(self, **overrides: object) -> None:
        kwargs = dict(self._baseline)
        kwargs.update(overrides)
        build_postgres_capability_authorizer(**kwargs)  # type: ignore[arg-type]

    def test_factory_rejects_missing_or_non_callable_executor(self) -> None:
        for bad in (None, object(), "not-an-executor"):
            with self.subTest(executor=type(bad).__name__):
                with self.assertRaises((TypeError, ValueError)):
                    self._build(executor=bad)

    def test_factory_rejects_synthetic_or_unavailable_adapters(self) -> None:
        cases: dict[str, tuple[dict[str, object], tuple[type[Exception], ...]]] = {
            "static_issuer": (
                {"trusted_issuers": StaticTrustedIssuerBackend({STUB_ISSUER_FPR})},
                (TypeError, ValueError),
            ),
            "unavailable_issuer": (
                {"trusted_issuers": UnavailableTrustedIssuerBackend()},
                (TypeError, ValueError, RuntimeError),
            ),
            "in_memory_audit": (
                {"audit": InMemoryAuditSink()},
                (TypeError, ValueError),
            ),
            "unavailable_audit": (
                {"audit": UnavailableAuditSink()},
                (TypeError, ValueError, RuntimeError),
            ),
        }
        for name, (override, expected) in cases.items():
            with self.subTest(adapter=name):
                with self.assertRaises(expected):
                    self._build(**override)

    def test_factory_rejects_misconfigured_tenant_or_clock(self) -> None:
        cases = {
            "tenant_id": {"tenant_id": "not-a-uuid"},
            "clock": {"clock": "not-a-clock"},
        }
        for name, override in cases.items():
            with self.subTest(misconfiguration=name):
                with self.assertRaises((TypeError, ValueError)):
                    self._build(**override)


class ComposedAuthorizerHealthTest(unittest.TestCase):
    """Cards 4ff4d96f and d0e59165: the composed authorizer stays fail closed."""

    def setUp(self) -> None:
        self.env = _ComposedEnvironment(self)

    def test_composition_defers_io_and_wires_durable_tenant_scoped_adapters(
        self,
    ) -> None:
        env = self.env
        self.assertEqual(env.state.calls, [])
        self.assertIsInstance(
            env.authorizer._principals, PostgresPrincipalPolicyBackend
        )
        self.assertIsInstance(env.authorizer._revocations, PostgresRevocationBackend)
        self.assertIsInstance(env.authorizer._replay, PostgresReplayBackend)

        principal = env.bind_principal()
        grant = env.rig.grant()
        token = env.rig.issue(principal, grant)
        context = env.authorizer.authorize(token, env.rig.request(principal, grant))
        self.assertTrue(context.decision.allow)

        sql_seen = {sql for sql, _params in env.state.calls}
        self.assertIn(PRINCIPAL_SNAPSHOT_SQL, sql_seen)
        self.assertIn(REVOCATION_SNAPSHOT_SQL, sql_seen)
        self.assertIn(REPLAY_RESERVE_SQL, sql_seen)
        for _sql, params in env.state.calls:
            self.assertEqual(params[0], TENANT_ID)

        decisions = env.audit.decisions()
        self.assertEqual(len(decisions), 1)
        self.assertTrue(decisions[0].allow)

    def test_revocation_lands_without_stale_signature_cache_allow(self) -> None:
        env = self.env
        principal = env.bind_principal()
        grant = env.rig.grant()
        token = env.rig.issue(principal, grant)
        digest = parse_presented_token(raw_leaf(token)).credential_digest

        first = env.authorizer.authorize(token, env.rig.request(principal, grant))
        self.assertTrue(first.decision.allow)

        env.state.revoke(digest)
        # The allow above cached a positive signature fact for this digest.
        # Revocation state is re-read on every call and must still deny.
        self.assertEqual(
            env.denied_reason(principal, grant, token),
            DecisionReason.REVOKED,
        )
        self.assertEqual(
            env.denied_reason(principal, grant, token),
            DecisionReason.REVOKED,
        )

        decisions = env.audit.decisions()
        self.assertEqual([item.allow for item in decisions], [True, False, False])
        self.assertEqual(
            [item.reason_code for item in decisions[1:]],
            [DecisionReason.REVOKED, DecisionReason.REVOKED],
        )

    def test_principal_suspension_lands_without_stale_allow(self) -> None:
        env = self.env
        principal = env.bind_principal()
        grant = env.rig.grant()
        token = env.rig.issue(principal, grant)
        context = env.authorizer.authorize(token, env.rig.request(principal, grant))
        self.assertTrue(context.decision.allow)

        env.state.set_principal(principal, active=False)
        self.assertEqual(
            env.denied_reason(principal, grant, token),
            DecisionReason.PRINCIPAL_INACTIVE,
        )

    def test_backend_outage_maps_to_fail_closed_denial_and_recovers(self) -> None:
        env = self.env
        principal = env.bind_principal()
        grant = env.rig.grant()
        for name, sql in (
            ("principal", PRINCIPAL_SNAPSHOT_SQL),
            ("revocation", REVOCATION_SNAPSHOT_SQL),
            ("replay", REPLAY_RESERVE_SQL),
        ):
            with self.subTest(outage=name):
                token = env.rig.issue(principal, grant)
                env.state.outage_sql.add(sql)
                try:
                    reason = env.denied_reason(principal, grant, token)
                finally:
                    env.state.outage_sql.discard(sql)
                self.assertEqual(reason, DecisionReason.BACKEND_UNAVAILABLE)

        recovered = env.rig.issue(principal, grant)
        context = env.authorizer.authorize(recovered, env.rig.request(principal, grant))
        self.assertTrue(context.decision.allow)


class ComposedAuthorizerRaceTest(unittest.TestCase):
    """Concurrent authorize calls stay deterministic and cross-talk free."""

    def setUp(self) -> None:
        self.env = _ComposedEnvironment(self)

    def _run_workers(
        self,
        count: int,
        invoke: Callable[[int], AuthorizedContext],
    ) -> dict[int, tuple[str, object]]:
        barrier = threading.Barrier(count)
        outcomes: dict[int, tuple[str, object]] = {}
        lock = threading.Lock()

        def run(index: int) -> None:
            outcome: tuple[str, object]
            try:
                barrier.wait()
                outcome = ("allow", invoke(index))
            except AuthorizationDenied as exc:
                outcome = ("denied", exc.decision.reason_code)
            except Exception as exc:
                outcome = ("error", exc)
            with lock:
                outcomes[index] = outcome

        threads = [
            threading.Thread(target=run, args=(index,)) for index in range(count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(len(outcomes), count)
        return outcomes

    def test_concurrent_distinct_credentials_allow_without_cross_talk(self) -> None:
        env = self.env
        count = 6
        principals = [env.bind_principal() for _ in range(count)]
        grant = env.rig.grant()
        tokens = [env.rig.issue(item, grant) for item in principals]

        def invoke(index: int) -> AuthorizedContext:
            return env.authorizer.authorize(
                tokens[index], env.rig.request(principals[index], grant)
            )

        outcomes = self._run_workers(count, invoke)
        for index, (kind, payload) in outcomes.items():
            with self.subTest(worker=index):
                self.assertEqual(kind, "allow")
                assert isinstance(payload, AuthorizedContext)
                self.assertTrue(payload.decision.allow)
                self.assertEqual(payload.principal, principals[index])
                self.assertEqual(payload.grant, grant)
                self.assertEqual(
                    payload.decision.principal_id,
                    principals[index].principal_id,
                )
        decision_ids = {
            payload.decision.decision_id
            for kind, payload in outcomes.values()
            if kind == "allow" and isinstance(payload, AuthorizedContext)
        }
        self.assertEqual(len(decision_ids), count)

    def test_concurrent_same_credential_allows_exactly_once(self) -> None:
        env = self.env
        principal = env.bind_principal()
        grant = env.rig.grant()
        token = env.rig.issue(principal, grant)

        def invoke(_index: int) -> AuthorizedContext:
            return env.authorizer.authorize(token, env.rig.request(principal, grant))

        outcomes = self._run_workers(8, invoke)
        kinds = [kind for kind, _payload in outcomes.values()]
        self.assertEqual(kinds.count("allow"), 1)
        self.assertEqual(kinds.count("denied"), 7)
        reasons = [payload for kind, payload in outcomes.values() if kind == "denied"]
        self.assertEqual(reasons, [DecisionReason.REPLAYED] * 7)

    def test_concurrent_mid_call_outage_denies_closed_without_cross_talk(self) -> None:
        env = self.env
        count = 8
        principals = [env.bind_principal() for _ in range(count)]
        grant = env.rig.grant()
        tokens = [env.rig.issue(item, grant) for item in principals]
        digests = [
            parse_presented_token(raw_leaf(item)).credential_digest for item in tokens
        ]
        poisoned = set(range(4, count))
        for index in poisoned:
            env.state.poisoned_digests.add(digests[index])

        def invoke(index: int) -> AuthorizedContext:
            return env.authorizer.authorize(
                tokens[index], env.rig.request(principals[index], grant)
            )

        outcomes = self._run_workers(count, invoke)
        for index, (kind, payload) in outcomes.items():
            with self.subTest(worker=index):
                if index in poisoned:
                    self.assertEqual(kind, "denied")
                    self.assertEqual(payload, DecisionReason.BACKEND_UNAVAILABLE)
                else:
                    self.assertEqual(kind, "allow")
                    assert isinstance(payload, AuthorizedContext)
                    self.assertEqual(
                        payload.decision.principal_id,
                        principals[index].principal_id,
                    )


if __name__ == "__main__":
    unittest.main()
