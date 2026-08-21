"""Benchmark the SKLegal CapAuth signing and authorization hot path.

Every protected SKLegal call presents a fresh one-use signed credential and
runs the complete fail-closed authorize sequence with three clock re-reads.
This script measures issuance and authorization latency, attributes cost to
each backend phase, exercises representative in-process load, and writes a
JSON report under ``build/benchmarks/`` for the SKL-S5-04 load qualification
cards.

All principals, credentials, and key material are synthetic. The real
OpenPGP scenario uses a throwaway ed25519 key in a temporary ``GNUPGHOME``
and never touches a live keyring, tenant, or matter record.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from capauth import signature_verifies  # type: ignore[import-untyped]
from capauth.testing import (  # type: ignore[import-untyped]
    STUB_ISSUER_FPR,
    signing_stub,
    stub_signature_for,
)
from sklegal_capauth import (
    CAPABILITY_RULES,
    VERIFIER_POLICY_VERSION,
    Audience,
    AuthorizationDecision,
    AuthorizationDenied,
    AuthorizationRequest,
    Capability,
    CapabilityAuthorizer,
    CapabilityGrant,
    CapabilityIssuer,
    CapAuthManifestSigner,
    CredentialSigner,
    DecisionReason,
    FileTrustedIssuerBackend,
    InMemoryAuditSink,
    InMemoryPrincipalPolicyBackend,
    InMemoryReplayBackend,
    InMemoryRevocationBackend,
    ParsedCapability,
    PresentedCapability,
    PrincipalContext,
    PrincipalPolicySnapshot,
    PrincipalType,
    RevocationSnapshot,
    SignatureVerificationCache,
    TrustedIssuerSnapshot,
    parse_presented_token,
)

TENANT_ID = UUID("10000000-0000-4000-8000-0000000000b6")
MATTER_ID = UUID("20000000-0000-4000-8000-0000000000b6")

PHASE_TRUSTED = "trusted_issuers.snapshot"
PHASE_PRINCIPALS = "principals.snapshot"
PHASE_REVOCATIONS = "revocations.snapshot"
PHASE_REPLAY = "replay.reserve"
PHASE_AUDIT = "audit.record"
PHASE_CACHE_CONTAINS = "signature_cache.contains"
PHASE_CACHE_ADD = "signature_cache.add"

NS_PER_MS = 1_000_000


def _stats(samples_ns: list[int]) -> dict[str, float | int]:
    """Return nearest-rank percentile statistics in milliseconds."""

    if not samples_ns:
        return {"count": 0}
    ordered = sorted(samples_ns)
    count = len(ordered)

    def percentile(fraction: float) -> float:
        rank = max(0, math.ceil(fraction * count) - 1)
        return ordered[rank] / NS_PER_MS

    return {
        "count": count,
        "min_ms": ordered[0] / NS_PER_MS,
        "p50_ms": percentile(0.50),
        "mean_ms": (sum(ordered) / count) / NS_PER_MS,
        "p95_ms": percentile(0.95),
        "p99_ms": percentile(0.99),
        "max_ms": ordered[-1] / NS_PER_MS,
    }


def _time_call(call: Callable[[], object], iterations: int) -> list[int]:
    samples: list[int] = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        call()
        samples.append(time.perf_counter_ns() - start)
    return samples


class _TimingRecorder:
    """Thread-safe cumulative timing per backend phase."""

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self._lock = threading.Lock()
        self._totals_ns: dict[str, int] = {}
        self._counts: dict[str, int] = {}

    def record(self, phase: str, elapsed_ns: int) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._totals_ns[phase] = self._totals_ns.get(phase, 0) + elapsed_ns
            self._counts[phase] = self._counts.get(phase, 0) + 1

    def report(self) -> dict[str, dict[str, float | int]]:
        with self._lock:
            totals = dict(self._totals_ns)
            counts = dict(self._counts)
        return {
            phase: {
                "count": counts[phase],
                "total_ms": totals[phase] / NS_PER_MS,
                "mean_ms": (totals[phase] / counts[phase]) / NS_PER_MS,
            }
            for phase in sorted(totals)
        }


class _CountingClock:
    """Wall clock that counts reads so clock re-reads are observable."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reads = 0

    def __call__(self) -> datetime:
        with self._lock:
            self.reads += 1
        return datetime.now(UTC)


class _TimedTrustedIssuers:
    def __init__(
        self,
        inner: FileTrustedIssuerBackend,
        recorder: _TimingRecorder,
    ) -> None:
        self._inner = inner
        self._recorder = recorder

    def snapshot(self) -> TrustedIssuerSnapshot:
        start = time.perf_counter_ns()
        try:
            return self._inner.snapshot()
        finally:
            self._recorder.record(PHASE_TRUSTED, time.perf_counter_ns() - start)


class _TimedPrincipals:
    def __init__(
        self,
        inner: InMemoryPrincipalPolicyBackend,
        recorder: _TimingRecorder,
    ) -> None:
        self._inner = inner
        self._recorder = recorder

    def snapshot(self, principal: PrincipalContext) -> PrincipalPolicySnapshot:
        start = time.perf_counter_ns()
        try:
            return self._inner.snapshot(principal)
        finally:
            self._recorder.record(PHASE_PRINCIPALS, time.perf_counter_ns() - start)


class _TimedRevocations:
    def __init__(
        self,
        inner: InMemoryRevocationBackend,
        recorder: _TimingRecorder,
    ) -> None:
        self._inner = inner
        self._recorder = recorder

    def snapshot(self, credential_digests: tuple[str, ...]) -> RevocationSnapshot:
        start = time.perf_counter_ns()
        try:
            return self._inner.snapshot(credential_digests)
        finally:
            self._recorder.record(PHASE_REVOCATIONS, time.perf_counter_ns() - start)


class _TimedReplay:
    def __init__(
        self,
        inner: InMemoryReplayBackend,
        recorder: _TimingRecorder,
    ) -> None:
        self._inner = inner
        self._recorder = recorder

    def reserve(
        self,
        *,
        credential_digest: str,
        decision_id: str,
        expires_at: datetime,
    ) -> bool:
        start = time.perf_counter_ns()
        try:
            return self._inner.reserve(
                credential_digest=credential_digest,
                decision_id=decision_id,
                expires_at=expires_at,
            )
        finally:
            self._recorder.record(PHASE_REPLAY, time.perf_counter_ns() - start)


class _TimedAudit:
    def __init__(
        self,
        inner: InMemoryAuditSink,
        recorder: _TimingRecorder,
    ) -> None:
        self._inner = inner
        self._recorder = recorder

    def record(self, decision: AuthorizationDecision) -> None:
        start = time.perf_counter_ns()
        try:
            self._inner.record(decision)
        finally:
            self._recorder.record(PHASE_AUDIT, time.perf_counter_ns() - start)


class _TimedSignatureCache:
    def __init__(
        self,
        inner: SignatureVerificationCache,
        recorder: _TimingRecorder,
    ) -> None:
        self._inner = inner
        self._recorder = recorder
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def contains(self, key: tuple[str, ...]) -> bool:
        start = time.perf_counter_ns()
        try:
            result = self._inner.contains(key)
        finally:
            self._recorder.record(PHASE_CACHE_CONTAINS, time.perf_counter_ns() - start)
        with self._lock:
            if result:
                self._hits += 1
            else:
                self._misses += 1
        return result

    def add(self, key: tuple[str, ...], *, credential_expires_at: datetime) -> None:
        start = time.perf_counter_ns()
        try:
            self._inner.add(key, credential_expires_at=credential_expires_at)
        finally:
            self._recorder.record(PHASE_CACHE_ADD, time.perf_counter_ns() - start)

    def hits_and_misses(self) -> tuple[int, int]:
        with self._lock:
            return self._hits, self._misses


class _StubSigner:
    @property
    def issuer_fingerprint(self) -> str:
        return STUB_ISSUER_FPR

    def sign(self, payload_bytes: bytes) -> str:
        return stub_signature_for(payload_bytes)


def _benchmark_grant() -> CapabilityGrant:
    rule = CAPABILITY_RULES[Capability.MATTER_READ]
    return CapabilityGrant(
        audience=Audience.API,
        target="api:matter.get",
        capability=Capability.MATTER_READ,
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        resource_type=rule.resource_type,
        operation=rule.operation,
        purpose=next(iter(rule.purposes)),
    )


def _write_policy(root: Path, fingerprint: str) -> Path:
    path = root / "trusted-issuers.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "sklegal-trusted-issuers/v1",
                "policy_version": VERIFIER_POLICY_VERSION,
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
    return path


class _Rig:
    """One synthetic authorization environment with timed backends."""

    def __init__(
        self,
        root: Path,
        fingerprint: str,
        signer: CredentialSigner,
        *,
        record: bool = True,
    ) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.recorder = _TimingRecorder(enabled=record)
        self.authorizer_clock = _CountingClock()
        policy_path = _write_policy(root, fingerprint)
        principals_inner = InMemoryPrincipalPolicyBackend()
        self.principal = PrincipalContext(
            principal_id=uuid4(),
            principal_type=PrincipalType.HUMAN,
            subject="synthetic:human:hotpath-benchmark",
            tenant_id=TENANT_ID,
        )
        principals_inner.set(self.principal, active=True)
        self.audit_inner = InMemoryAuditSink()
        self.cache = _TimedSignatureCache(SignatureVerificationCache(), self.recorder)
        self.authorizer = CapabilityAuthorizer(
            trusted_issuers=_TimedTrustedIssuers(
                FileTrustedIssuerBackend(policy_path), self.recorder
            ),
            principals=_TimedPrincipals(principals_inner, self.recorder),
            revocations=_TimedRevocations(InMemoryRevocationBackend(), self.recorder),
            replay=_TimedReplay(InMemoryReplayBackend(), self.recorder),
            audit=_TimedAudit(self.audit_inner, self.recorder),
            signature_cache=self.cache,
            clock=self.authorizer_clock,
        )
        self.issuer = CapabilityIssuer(signer)
        self.grant = _benchmark_grant()

    def issue(self) -> PresentedCapability:
        return self.issuer.issue_root(principal=self.principal, grant=self.grant)

    def authorize(self, presented: PresentedCapability) -> None:
        request = AuthorizationRequest(
            principal=self.principal,
            grant=self.grant,
            correlation_id=uuid4(),
        )
        self.authorizer.authorize(presented, request)


def _stub_scenarios(
    root: Path,
    iterations: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    scenarios: dict[str, Any] = {}
    invariants: dict[str, Any] = {}
    with signing_stub():
        rig = _Rig(root, STUB_ISSUER_FPR, _StubSigner())

        scenarios["issue_only_stub"] = _stats(_time_call(rig.issue, iterations))

        hits_before, misses_before = rig.cache.hits_and_misses()
        authorize_samples: list[int] = []
        for _ in range(iterations):
            presented = rig.issue()
            start = time.perf_counter_ns()
            rig.authorize(presented)
            authorize_samples.append(time.perf_counter_ns() - start)
        scenarios["authorize_fresh_one_use_stub"] = _stats(authorize_samples)
        hits_after, misses_after = rig.cache.hits_and_misses()
        invariants["signature_cache_hits_on_fresh_path"] = hits_after - hits_before
        invariants["signature_cache_lookups_on_fresh_path"] = (
            misses_after - misses_before + hits_after - hits_before
        )

        def issue_and_authorize() -> None:
            rig.authorize(rig.issue())

        scenarios["hot_path_total_stub"] = _stats(
            _time_call(issue_and_authorize, iterations)
        )

        raw = rig.issue().credentials_for_verification()[-1]
        scenarios["parse_only_stub"] = _stats(
            _time_call(lambda: parse_presented_token(raw), iterations)
        )

        probe = rig.issue()
        reads_before = rig.authorizer_clock.reads
        rig.authorize(probe)
        invariants["clock_reads_per_authorize"] = (
            rig.authorizer_clock.reads - reads_before
        )

        replayed = rig.issue()
        rig.authorize(replayed)
        deny_samples: list[int] = []
        for _ in range(iterations):
            start = time.perf_counter_ns()
            try:
                rig.authorize(replayed)
            except AuthorizationDenied as exc:
                if exc.decision.reason_code is not DecisionReason.REPLAYED:
                    raise AssertionError(
                        f"expected REPLAYED, got {exc.decision.reason_code.value}"
                    ) from None
            else:
                raise AssertionError("replayed credential unexpectedly allowed")
            deny_samples.append(time.perf_counter_ns() - start)
        scenarios["deny_replayed_stub"] = _stats(deny_samples)
        invariants["one_use_replay_denied"] = True

        expected_allows = 2 * iterations + 2
        allows = sum(1 for decision in rig.audit_inner.decisions() if decision.allow)
        invariants["allow_count"] = allows
        invariants["expected_allow_count"] = expected_allows
        invariants["allow_count_matches"] = allows == expected_allows
        phases = rig.recorder.report()
    return scenarios, phases, invariants


def _generate_synthetic_key(keyring: Path) -> str:
    environment = {**os.environ, "GNUPGHOME": str(keyring)}
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--passphrase",
            "",
            "--quick-generate-key",
            "SKLegal Synthetic Benchmark <synthetic-benchmark@example.invalid>",
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
    return next(
        line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")
    )


def _verify_or_raise(parsed: ParsedCapability) -> bool:
    if not signature_verifies(parsed.token):
        raise AssertionError("synthetic OpenPGP signature failed to verify")
    return True


def _gpg_scenarios(root: Path, iterations: int) -> dict[str, Any]:
    scenarios: dict[str, Any] = {}
    keyring = root / "gnupg"
    keyring.mkdir(mode=0o700)
    fingerprint = _generate_synthetic_key(keyring)
    previous_home = os.environ.get("GNUPGHOME")
    os.environ["GNUPGHOME"] = str(keyring)
    try:
        rig = _Rig(root, fingerprint, CapAuthManifestSigner(fingerprint))

        scenarios["issue_only_openpgp"] = _stats(_time_call(rig.issue, iterations))

        parsed = parse_presented_token(rig.issue().credentials_for_verification()[-1])
        scenarios["verify_only_openpgp"] = _stats(
            _time_call(lambda: _verify_or_raise(parsed), iterations)
        )

        authorize_samples: list[int] = []
        for _ in range(iterations):
            presented = rig.issue()
            start = time.perf_counter_ns()
            rig.authorize(presented)
            authorize_samples.append(time.perf_counter_ns() - start)
        scenarios["authorize_fresh_one_use_openpgp"] = _stats(authorize_samples)

        def issue_and_authorize() -> None:
            rig.authorize(rig.issue())

        scenarios["hot_path_total_openpgp"] = _stats(
            _time_call(issue_and_authorize, iterations)
        )

        envelope = json.loads(rig.issue().credentials_for_verification()[-1])
        envelope["signature"] = envelope["signature"].replace("A", "B", 1)
        invalid = PresentedCapability.single(json.dumps(envelope))
        deny_samples: list[int] = []
        for _ in range(iterations):
            start = time.perf_counter_ns()
            try:
                rig.authorize(invalid)
            except AuthorizationDenied as exc:
                if exc.decision.reason_code is not DecisionReason.INVALID_SIGNATURE:
                    raise AssertionError(
                        "expected INVALID_SIGNATURE, got "
                        f"{exc.decision.reason_code.value}"
                    ) from None
            else:
                raise AssertionError("tampered credential unexpectedly allowed")
            deny_samples.append(time.perf_counter_ns() - start)
        scenarios["deny_invalid_signature_openpgp"] = _stats(deny_samples)
    finally:
        if previous_home is None:
            os.environ.pop("GNUPGHOME", None)
        else:
            os.environ["GNUPGHOME"] = previous_home
    return scenarios


def _load_scenarios(
    root: Path,
    workers: tuple[int, ...],
    per_worker: int,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    with signing_stub():
        for worker_count in workers:
            rig = _Rig(
                root / f"workers-{worker_count}",
                STUB_ISSUER_FPR,
                _StubSigner(),
                record=False,
            )
            barrier = threading.Barrier(worker_count)
            samples: list[int] = []
            samples_lock = threading.Lock()
            errors: list[str] = []

            def worker() -> None:
                local: list[int] = []
                barrier.wait()
                for _ in range(per_worker):
                    start = time.perf_counter_ns()
                    try:
                        rig.authorize(rig.issue())
                    except Exception as exc:
                        errors.append(type(exc).__name__)
                        continue
                    local.append(time.perf_counter_ns() - start)
                with samples_lock:
                    samples.extend(local)

            threads = [
                threading.Thread(target=worker, daemon=True)
                for _ in range(worker_count)
            ]
            wall_start = time.perf_counter()
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            wall_seconds = time.perf_counter() - wall_start
            stats = _stats(samples)
            stats["wall_seconds"] = wall_seconds
            stats["ops_per_second"] = (
                len(samples) / wall_seconds if wall_seconds > 0 else 0.0
            )
            stats["errors"] = len(errors)
            results[str(worker_count)] = stats
    return results


def _meta() -> dict[str, Any]:
    gpg_path = shutil.which("gpg")
    gpg_version = None
    if gpg_path:
        gpg_version = subprocess.run(
            [gpg_path, "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()[0]
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "capauth_version": importlib.metadata.version("capauth"),
        "sklegal_capauth_version": importlib.metadata.version("sklegal-capauth"),
        "gpg_available": gpg_path is not None,
        "gpg_version": gpg_version,
        "backend_composition": (
            "in-memory principal, revocation, replay, and audit backends; "
            "FileTrustedIssuerBackend issuer policy; default "
            "SignatureVerificationCache bounds"
        ),
    }


def run_benchmark(
    *,
    iterations: int = 1000,
    gpg_iterations: int = 30,
    load_workers: tuple[int, ...] = (1, 4, 8),
    load_per_worker: int = 250,
    include_gpg: bool = True,
) -> dict[str, Any]:
    """Run every benchmark scenario and return the structured report."""

    if iterations < 1 or gpg_iterations < 0 or load_per_worker < 1:
        raise ValueError("iteration counts must be positive")
    if not load_workers or any(count < 1 for count in load_workers):
        raise ValueError("load worker counts must be positive")
    with tempfile.TemporaryDirectory(prefix="sklegal-capauth-bench-") as temp:
        root = Path(temp)
        scenarios, phases, invariants = _stub_scenarios(root / "stub", iterations)
        openpgp: dict[str, Any] = {}
        if include_gpg and gpg_iterations and shutil.which("gpg"):
            openpgp = _gpg_scenarios(root / "gpg", gpg_iterations)
        load = _load_scenarios(root / "load", load_workers, load_per_worker)
    invariants["load_errors"] = sum(int(stats["errors"]) for stats in load.values())
    return {
        "meta": _meta(),
        "parameters": {
            "iterations": iterations,
            "gpg_iterations": gpg_iterations,
            "load_workers": list(load_workers),
            "load_per_worker": load_per_worker,
            "include_gpg": include_gpg,
        },
        "scenarios": scenarios,
        "openpgp_scenarios": openpgp,
        "load_stub": load,
        "phases_stub": phases,
        "invariants": invariants,
    }


def write_report(report: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    path = output_dir / f"capauth-hotpath-{stamp}.json"
    path.write_text(encoded, encoding="utf-8")
    (output_dir / "capauth-hotpath-latest.json").write_text(encoded, encoding="utf-8")
    return path


def _print_summary(report: dict[str, Any], path: Path) -> None:
    print(f"report: {path}")
    sections = (
        ("stub", report["scenarios"]),
        ("openpgp", report["openpgp_scenarios"]),
    )
    for label, scenarios in sections:
        for name, stats in scenarios.items():
            if not stats.get("count"):
                continue
            print(
                f"{label}/{name}: n={stats['count']} "
                f"p50={stats['p50_ms']:.3f}ms "
                f"p95={stats['p95_ms']:.3f}ms "
                f"mean={stats['mean_ms']:.3f}ms "
                f"max={stats['max_ms']:.3f}ms"
            )
    for workers, stats in report["load_stub"].items():
        print(
            f"load/{workers} workers: {stats['ops_per_second']:.1f} ops/s "
            f"p95={stats['p95_ms']:.3f}ms errors={stats['errors']}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark the CapAuth signing and authorization hot path.",
    )
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--gpg-iterations", type=int, default=30)
    parser.add_argument("--load-workers", default="1,4,8")
    parser.add_argument("--load-per-worker", type=int, default=250)
    parser.add_argument("--no-gpg", action="store_true")
    parser.add_argument("--output-dir", default="build/benchmarks")
    args = parser.parse_args(argv)
    workers = tuple(int(part) for part in args.load_workers.split(",") if part.strip())
    report = run_benchmark(
        iterations=args.iterations,
        gpg_iterations=args.gpg_iterations,
        load_workers=workers,
        load_per_worker=args.load_per_worker,
        include_gpg=not args.no_gpg,
    )
    path = write_report(report, Path(args.output_dir))
    _print_summary(report, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
