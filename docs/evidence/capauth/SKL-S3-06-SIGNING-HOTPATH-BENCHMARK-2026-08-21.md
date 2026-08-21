# SKL-S3-06 CapAuth signing hot path benchmark and signer failover design

Card: `2841a019`
Implementer: `kimi-skl-s3-06`
Date: 2026-08-21
Status: Complete; ready for review

Every protected SKLegal call presents a fresh one-use signed credential and
runs the complete fail-closed authorize sequence. This evidence quantifies
that hot path, publishes a latency budget, defines signer failover and
degraded-mode behavior, and records the deny-cache decision.

## Method

New harness: `scripts/benchmark_capauth_hotpath.py`. It builds a synthetic
authorization environment per scenario and measures with
`time.perf_counter_ns`:

- `issue_only_stub` / `issue_only_openpgp`: credential construction and
  signing, isolated.
- `authorize_fresh_one_use_*`: a fresh one-use credential per iteration;
  only the authorize call is timed.
- `hot_path_total_*`: issuance plus authorize together, the true per-call
  cost a protected boundary pays.
- `parse_only_stub`: strict wire parse alone.
- `deny_replayed_stub`: second presentation of a spent credential.
- `deny_invalid_signature_openpgp`: tampered-signature denial with a real
  gpg verification per attempt.
- `load_stub`: threaded issue-plus-authorize at 1, 4, and 8 workers.

Backend composition: in-memory principal, revocation, replay, and audit
backends; `FileTrustedIssuerBackend` for the issuer policy; default
`SignatureVerificationCache` bounds. The OpenPGP scenarios use a throwaway
ed25519 key in a temporary `GNUPGHOME`. All principals, Matters, and
credentials are synthetic. No live keyring, tenant, Matter, or HammerTime
path was touched.

The measured authorize sequence is the 12-gate fail-closed pipeline:

1. credential presence check
2. wire chain extraction and depth bound
3. strict parse of every credential in the chain
4. trusted issuer policy snapshot and policy version match
5. current-state validation of every distinct principal
6. chain validation: clock re-read 1, issuer ceilings, delegation
   structure, monotonic attenuation
7. revocation snapshot: leaf and ancestor checks
8. per-credential signature verification behind the positive-only cache
9. exact request-to-grant match across the full legal scope
10. clock re-read 2 after signature and policy work
11. atomic one-use replay reservation
12. clock re-read 3 after reservation, then the audit record and allow

The harness asserts the triple clock re-read (`clock_reads_per_authorize`
is 3) and the one-use invariant on every run.

## Measured results

Environment: Python 3.12.3, Linux x86_64, 24 cores, gpg 2.4.4, CapAuth
0.3.1 pinned at commit `183c04a7c623e8abcf37bd705bf8bca1deb4a364`.
Report artifact: `build/benchmarks/capauth-hotpath-20260821T203612Z.json`
(gitignored; regenerate with `python scripts/benchmark_capauth_hotpath.py`).

Stub signer, 1,000 iterations per scenario:

| Scenario | p50 | p95 | mean | max |
|---|---|---|---|---|
| issue_only_stub | 0.045 ms | 0.048 ms | 0.045 ms | 0.246 ms |
| authorize_fresh_one_use_stub | 0.244 ms | 0.276 ms | 0.246 ms | 0.868 ms |
| hot_path_total_stub | 0.377 ms | 0.410 ms | 0.384 ms | 7.641 ms |
| parse_only_stub | 0.074 ms | 0.077 ms | 0.075 ms | 0.109 ms |
| deny_replayed_stub | 0.314 ms | 0.335 ms | 0.316 ms | 0.874 ms |

Real OpenPGP (gpg plus gpg-agent, ed25519), 30 iterations per scenario:

| Scenario | p50 | p95 | mean | max |
|---|---|---|---|---|
| issue_only_openpgp | 4.028 ms | 4.206 ms | 3.983 ms | 4.254 ms |
| verify_only_openpgp | 3.333 ms | 3.603 ms | 3.185 ms | 3.661 ms |
| authorize_fresh_one_use_openpgp | 4.027 ms | 4.293 ms | 3.856 ms | 4.499 ms |
| hot_path_total_openpgp | 8.123 ms | 8.860 ms | 8.088 ms | 8.954 ms |
| deny_invalid_signature_openpgp | 2.522 ms | 2.745 ms | 2.476 ms | 3.127 ms |

In-process load, stub signer, 250 operations per worker:

| Workers | Throughput | p95 per op | Errors |
|---|---|---|---|
| 1 | 3,352 ops/s | 0.314 ms | 0 |
| 4 | 2,280 ops/s | 3.892 ms | 0 |
| 8 | 1,884 ops/s | 9.602 ms | 0 |

Phase attribution per authorize call (stub, mean over 3,002 calls):

| Phase | Mean per call |
|---|---|
| replay.reserve | 66.3 us |
| trusted_issuers.snapshot (file read plus hash) | 38.4 us |
| principals.snapshot | 10.2 us |
| revocations.snapshot | 7.0 us |
| signature_cache.add | 1.7 us |
| signature_cache.contains | 1.3 us |
| audit.record | 0.4 us |

## Findings

1. Cryptography dominates the real hot path. Signing costs about 4.0 ms and
   verification about 3.3 ms against a local gpg-agent; the entire
   in-process authorize logic, parse, and backend composition costs about
   0.25 ms. The end-to-end fresh-credential call is about 8.1 ms p50.
2. The signature cache never helps the true hot path. Every protected call
   presents a fresh one-use credential with a fresh digest, so the measured
   hit rate on the fresh path is exactly 0 of 1,000 lookups. Its value is
   confined to repeat presentations such as replay attempts.
3. `replay.reserve` is the largest in-process phase because the in-memory
   backend purges expired reservations with a full dict scan per call. This
   is a synthetic-backend artifact; the Postgres replay backend uses an
   indexed insert and will be measured under SKL-S5-04.
4. An invalid-signature denial costs a full gpg verification, about
   2.5 ms per attempt. An attacker replaying tampered credentials forces
   that cost repeatedly; see the deny-cache decision below.
5. Threaded throughput degrades above one worker because of the GIL and
   backend lock contention. Horizontal scaling must come from worker
   processes, not threads.

## Published latency budget

Budget for one protected call at a boundary, local gpg-agent signer,
in-process policy composition, p95 targets from this measurement base:

| Component | p95 budget |
|---|---|
| Credential issuance (sign) | 10 ms |
| Authorize sequence (verify plus 12 gates) | 10 ms |
| Total issuance plus authorization | 20 ms |
| In-process logic allowance within authorize | 2 ms |

The 20 ms total is measured at 8.9 ms p95 today, leaving headroom for
moderate host contention. Durable backend IO (Postgres principal,
revocation, replay, and audit round trips) is deliberately excluded here
and receives its own budget under the SKL-S5-04 load qualification cards;
this report is their input baseline.

## Signer failover and degraded-mode design

The signer is an availability single point of failure. Verification never
needs the signer: already-issued credentials verify offline against public
key material, so a signer outage blocks only new credential issuance.

Failover design:

- Run the signer as a narrow sidecar holding the private key through
  gpg-agent; the service process never sees key material. Deploy one
  primary and one standby sidecar.
- The standby either holds the same key under sealed, separately audited
  custody (active/passive) or a distinct subkey fingerprint pre-registered
  in the trusted issuer policy (active/active). No unregistered fingerprint
  may ever sign.
- Liveness is a timed canary: sign a fixed synthetic payload on a probe
  interval and record latency. Two consecutive probe failures or a probe
  latency above the 10 ms issuance budget by an order of magnitude opens
  the circuit.
- On primary failure, issuance fails over to the standby once, with a
  bounded per-attempt timeout of 2 seconds. Failover is always observable:
  an audit and metrics event records the signer identity, the issuer policy
  revision, and the failure reason. There is no silent fallback.
- If every signer is unavailable, issuance fails closed. Protected calls
  then deny with `BACKEND_UNAVAILABLE`; no unsigned credential, cached
  allow, or stale grant may be minted or reused to bypass the outage.

Degraded-mode policy:

- Signer down: verification of existing credentials continues; new
  issuance stops; new protected calls fail closed and observably.
- Any authorization backend (trusted issuer, principal, revocation,
  replay, audit) unavailable: existing behavior stands, deny with
  `BACKEND_UNAVAILABLE`. No degraded mode may weaken a gate.
- Every degraded interval emits structured reason codes and metrics and
  raises an operator alert after a sustained threshold, so degraded
  operation is never silent.

## Deny-stable cache decision

Decision: adopt a bounded, digest-keyed deny cache in a follow-up card,
restricted to credential-intrinsic denial classes only:
`MALFORMED_CREDENTIAL`, `UNSIGNED_CREDENTIAL`, `INVALID_SIGNATURE`,
`TTL_EXCEEDED`, `DELEGATION_CHAIN_INVALID`, and `OVER_DELEGATED`. Key it by
credential digest, verifier policy version, and issuer policy revision;
bound it by size and by `min(30 seconds, credential expiry)`; fail closed
on any cache error.

Rationale:

- These denials depend only on the signed bytes and the pinned policy
  revision, so a cached deny can never mask a later legitimate allow.
- Caching a deny can never produce an allow; fail-closed semantics are
  preserved by construction.
- The measured benefit is real but narrow: it caps the 2.5 ms gpg
  verification an attacker can force per repeated tampered credential.
- State-dependent denials (`REVOKED`, `ANCESTOR_REVOKED`,
  `PRINCIPAL_INACTIVE`, `PRINCIPAL_UNBOUND`, `PRINCIPAL_REBOUND`,
  `REPLAYED`, `EXPIRED`, `NOT_YET_VALID`, `POLICY_MISMATCH`,
  `BACKEND_UNAVAILABLE`, `AUDIT_UNAVAILABLE`) are never cached, because
  they depend on mutable backend state and a cached entry could pin a
  stale denial after the state clears.
- Allows are never cached. This reaffirms the existing invariant: the
  current positive-only signature cache stores a cryptographic fact, not a
  decision, and every allow still evaluates current issuer, principal,
  revocation, and replay state.

Implementation is deferred because this card is measurement plus design
and must not edit the shared package. The follow-up card needs tests for
cache-boundedness, revision invalidation, expiry, and fail-closed cache
errors.

## Feed into SKL-S5-04

The load qualification cards should reuse this harness, swap the in-memory
backends for the Postgres compositions, and re-run under multi-process
worker counts. The baseline expectations from this card: about 8.9 ms p95
for the full signed call before durable backend IO, about 0.25 ms of
in-process authorize logic, and roughly 3,300 in-process operations per
second per worker process for the stub path.

## Verification

```text
PASS: python scripts/benchmark_capauth_hotpath.py
      (1,000-iteration stub scenarios, 30-iteration OpenPGP scenarios,
      load at 1, 4, and 8 workers, zero errors, all invariants true)
PASS: python -m unittest tests.test_capauth_hotpath_benchmark -v
      (5 of 5 tests)
PASS: ruff format --check and ruff check on both new files
PASS: python -m unittest tests.test_capauth_authorization
      tests.test_capauth_delegation tests.test_capauth_boundaries
      tests.test_capauth_hotpath_benchmark (35 of 35 tests)
PASS: python -m unittest tests.integration.test_capauth_contract
      (1 of 1 real OpenPGP boundary test)
PASS: ./scripts/run_checks.sh unit-test (349 of 349 Python tests,
      including this module through the gate list)
```

`./scripts/run_checks.sh format-check` currently reports two files owned
by other in-flight cards (`scripts/benchmark_audit_chain_head.py` and
`services/worker/src/sklegal_worker/queues.py`). Both files added by this
card pass ruff format and lint individually; no foreign file was
reformatted.

## Known limitations

- Numbers are single-host and single-run; the OpenPGP scenarios use 30
  iterations, so treat their p95 as indicative, not as a tight bound.
- Durable Postgres backends, network hop, and HTTP boundary overhead are
  out of scope and belong to SKL-S5-04.
- The in-memory replay backend's purge inflates the `replay.reserve`
  phase; production numbers will differ.
- `docs/tasks/SUBAGENT-TASK-TTDS.md` has no SKL-S3-06 section; this card
  was executed from `core.json` alone.
- No TDD entry, parent card `3457eb15` may want to reconcile the task
  document.

## Rollback

No data or migration changed. Rollback is: delete
`scripts/benchmark_capauth_hotpath.py`,
`tests/test_capauth_hotpath_benchmark.py`, and this document; remove the
single `tests.test_capauth_hotpath_benchmark` line added to
`scripts/run_checks.sh`; delete `build/benchmarks/` artifacts.
