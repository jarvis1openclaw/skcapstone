# SKL-S5-04B load and saturation qualification

Card: `3e3c32d6` (`SKL-S5-04B`)
Implementer: `skl-s5-04b`
Date: 2026-08-22
Status: complete, ready for review

This receipt publishes the pilot load envelope, measures the single-host
saturation ceilings for the API, Temporal workers, PostgreSQL, and the
CapAuth signing path, checks the S3-09 audit chain-head trigger gate, and
triages the bottlenecks into proposed follow-up cards. All identities,
tenants, matters, credentials, and key material are synthetic. No
HammerTime path, live keyring, or external service was touched.

## Method

Two new drivers, both committed with tests:

- `scripts/load_saturation.py`: API workspace-read HTTP load over loopback
  uvicorn with the real 12-gate CapAuth authorizer and a fresh one-use
  stub-signed credential per request; CapAuth signing-path load
  (issue plus authorize) with a real throwaway OpenPGP ed25519 key or the
  deterministic stub; and an in-process interactive-queue Temporal worker
  burst against the disposable development stack.
- `scripts/load_postgres_saturation.py`: extends the SKL-S3-09 harness
  (imported, not copied) with pilot-scale reads through the RLS-bound
  runtime roles, the three durable CapAuth SQL functions, and the
  per-tenant audit chain-head append ceiling measured idle and under
  concurrent read load.

Pilot scale is derived from the SKL-S5-01A pilot dry run (54 fact
assertions, 112 source files, 2 tension groups, 3 work-product versions)
and the stated growth projection of 25 matters per tenant, so the hot
tenant carries 1,350 fact assertions. The API store serves 25 matters.
Environment: Python 3.12.3, Linux x86_64, 24 logical CPUs, gpg 2.4.4,
PostgreSQL 17.7 pinned image on tmpfs, `--network none`. Numbers are
environment-specific ceilings for this host class, not portable constants.

Raw artifacts (gitignored; regenerate with the commands in section 8):

| Artifact | SHA256 |
|---|---|
| `build/benchmarks/load-api.json` | `8ceb39b7374a02495ef30f9596e3e5fc7195cb439fc080ddd9ddf57da130c236` |
| `build/benchmarks/load-signing.json` | `3814090e5ebaf5f555c24880a3d22999d2357c1bd5a92278799771080467f29b` |
| `build/benchmarks/load-temporal.json` | `9c6315f8450982c840a55a48ede59c1e726f41314858135563d54058078e64c0` |
| `build/benchmarks/postgres-saturation.json` | `08212c57abc23154d85480a601299b28ce1f0592cb2e69e73919b074f7f57599` |
| `build/benchmarks/postgres-saturation-audit-pilot.json` | `ba58733ce1353d50e50e3ae08e92862422d7ca7d22d64088169f4e3fee9f222c` |

## Pilot load envelope

No numeric Sprint 5 load target exists in `docs/planning/`; per
`docs/architecture/AUDIT-CHAIN-SCALING.md` section 5, deriving the
envelope is this card's job. The derivation below is an explicit
assumption set grounded in the pilot shape, not a measured user census.
It needs human confirmation before it is treated as a service-level
objective.

Assumptions: the pilot is one tenant, one active matter, with 5 concurrent
interactive principals (1 attorney, 1 paralegal, 1 reviewer, 2 agent
loops). Each principal browses at 6 workspace reads per minute. Governed
agent runs complete at 2 per minute and emit 10 audit events each.

| Envelope dimension | Sustained | Burst (10x) |
|---|---:|---:|
| Workspace read requests | 0.5 per second | 5 per second |
| Fresh one-use credentials issued and authorized | 0.5 per second | 5 per second |
| Audit appends per tenant | 0.34 per second | 10 per second (10 concurrent appenders) |
| Workflow starts | 0.033 per second | 1 per second |
| Hot-tenant fact assertions | 1,350 (25 matters x 54) | same |

## Results and pass/fail

### API workspace read (real CapAuth boundary, fresh one-use credential per request)

| Workers | Ops/sec | p50 ms | p95 ms | Errors |
|---:|---:|---:|---:|---:|
| 1 | 809.81 | 0.987 | 1.494 | 0 |
| 4 | 687.66 | 4.803 | 9.280 | 0 |
| 8 | 654.47 | 10.497 | 18.176 | 0 |
| 16 | 596.38 | 23.545 | 40.519 | 0 |
| 32 | 505.28 | 58.938 | 83.472 | 0 |

Envelope demand is 5 req/s at p95 under 200 ms. Measured 505 to 810 req/s
at p95 1.5 to 83 ms: **PASS with roughly 100x throughput headroom**.
Server process peaked at 168 MiB RSS against the 2 GiB `sklegal-api`
service limit and saturated about one core against the 2 CPU limit
(**PASS** against the S0-02 resource ceilings). Client-side stub
credential issuance added p95 0.220 ms, negligible next to the real
signing path measured separately.

### CapAuth signing path (real OpenPGP ed25519, issue plus full authorize)

| Workers | Ops/sec | p50 ms | p95 ms | Errors |
|---:|---:|---:|---:|---:|
| 1 | 70.04 | 11.071 | 29.253 | 0 |
| 2 | 153.03 | 11.132 | 22.458 | 0 |
| 4 | 399.19 | 9.939 | 12.218 | 0 |
| 8 | 538.16 | 14.299 | 19.873 | 0 |

Component summaries across all 560 measured operations: issuance p50
5.655 ms / p95 9.997 ms; authorize p50 6.466 ms / p95 11.706 ms.

Against the SKL-S3-06 published budget (p95 10 ms per component, 20 ms
total): the total holds at 4 workers (12.218 ms) and 8 workers
(19.873 ms, inside by 0.127 ms). The 1 and 2 worker runs show p95 tails
of 29.253 and 22.458 ms: a single-flight gpg-agent occasionally stalls
while deeper concurrency smooths the tail. **Split result: PASS at and
above 4 workers, FAIL the p95 total at 1 to 2 workers.** Issuance alone
p95 9.997 ms sits exactly at its 10 ms budget; authorize p95 11.706 ms
exceeds its 10 ms component budget by 1.7 ms. The signing-path ceiling
under load is roughly 500 to 560 issue-plus-authorize operations per
second at 8 workers on this host, which is 100x the envelope burst.

### Temporal interactive workers

40 of 40 synthetic gated workflows completed in 1.909 s (20.952 per
second, max concurrency 10) with zero failures, 40 dispatch receipts, and
completion latency p50 452.0 ms / p95 703.7 ms including the approval
signal round trip. Envelope demand is 0.033 workflow starts per second
sustained. **PASS with roughly 600x headroom.**

### PostgreSQL: RLS-bound pilot-scale reads

One read is the timed three-count workspace probe (fact assertions,
matters, matter events) through the tenant runtime role with row-level
security enforced.

| Workers | Ops/sec | p50 ms | p99 ms | Lock waiters |
|---:|---:|---:|---:|---:|
| 1 | 0.73 | 1,368.9 | 1,454.9 | 0 |
| 4 | 2.86 | 805.8 | 1,422.1 | 0 |
| 8 | 5.62 | 934.8 | 1,539.4 | 0 |
| 16 | 9.77 | 1,051.9 | 2,945.1 | 0 |

The superuser plan for the same probe is a 0.286 ms sequential scan over
1,566 rows, but through the RLS role one read costs 1.38 s: the
`matter_boundary` policy evaluates
`sklegal_identity.record_is_authorized(tenant_id, matter_id)` per row, and
each evaluation walks membership. Zero lock waiters at every level
confirms this is per-row CPU, not contention. **FAIL: p50 0.8 to 1.4 s
against a 200 ms interactive target; throughput ceiling about 10 reads
per second per role.** This is the top bottleneck (section 6).

### PostgreSQL: durable CapAuth SQL path

| Function | 1 worker | 4 workers | 8 workers | p99 at 8 (ms) |
|---|---:|---:|---:|---:|
| `capability_principal_snapshot` | 413.2/s | 1,700.8/s | 2,635.1/s | 6.29 |
| `capability_revocation_snapshot` | 738.6/s | 2,633.1/s | 4,462.2/s | 2.87 |
| `reserve_capability` (replay) | 615.8/s | 2,086.2/s | 3,433.8/s | 3.72 |

All three hot-path functions sustain thousands of operations per second
with single-digit-millisecond p99 at 8 workers. Replay reservations
matched the executed operation count exactly. **PASS with three orders of
magnitude of headroom over the envelope burst.**

### Audit chain-head ceiling and the S3-09 trigger gate

Full-load run (8 append workers, 16 read workers, 200 appends per worker):

| Condition | Idle | Under concurrent read load |
|---|---:|---:|
| Appends per second | 91.15 | 55.27 (0.606 retained) |
| Append p50 / p99 ms | 86.3 / 107.8 | 132.7 / 311.9 |
| Read p99 ms | - | 636.8 |

Pilot-concurrency run (2 append workers, 8 read workers, 100 appends per
worker): 99.04 appends/s idle and 69.55 under read load (0.702 retained),
append p99 27.8 ms idle and 43.7 ms under load, read p99 414.6 ms. Both
chains verified true after every run; deadlock delta 0 in both runs.

Trigger gate from `docs/architecture/AUDIT-CHAIN-SCALING.md` section 5.3,
checked in order:

1. Tenant sustaining more than 50 appends per second: the pilot envelope
   demands 0.34 sustained, so **not triggered** (the ceiling itself is 91
   to 99 per second per tenant, matching the S3-09 80 to 96 range).
2. Measured p99 append latency above 100 ms at pilot production
   concurrency: measured 27.8 ms idle and 43.7 ms under load at 2
   concurrent appenders, so **not triggered at pilot concurrency**. The
   100 ms threshold is crossed only at burst concurrency (8 appenders:
   107.8 ms idle, 311.9 ms under read load), which the gate does not
   count as pilot production concurrency. **Watch item**, recorded as
   bottleneck 2.
3. Multi-tenant load claims beyond the pilot: none are made by this card.

## Bottleneck triage

Proposed follow-up cards for the board owner to file (this card does not
create board state). Ordered by pilot impact.

1. **HIGH, RLS read authorization evaluates per row.**
   `sklegal_identity.record_is_authorized` runs once per row under the
   `matter_boundary` policy, making a 0.3 ms superuser read cost 0.8 to
   1.4 s at pilot scale and capping a role near 10 reads per second.
   Proposed card: restructure the policy or read path so membership is
   evaluated once per statement (for example an initplan-shaped policy
   predicate or a security-barrier view over a pre-checked membership),
   with an acceptance target of p95 at or under 50 ms for the same
   1,350-fact probe through the same runtime role, and no change to
   cross-tenant denial tests.
2. **MEDIUM, audit chain-head p99 crosses the 100 ms watch level at
   burst concurrency.** At 8 concurrent appenders, append p99 is 107.8 ms
   idle and 311.9 ms while reads saturate the instance (throughput
   retained ratio 0.606). The S3-09 trigger is not met at pilot
   concurrency, and the approved per-Matter chain-head design from
   `AUDIT-CHAIN-SCALING.md` remains correctly deferred. Proposed card:
   re-measure after bottleneck 1 lands, since the mixed-load degradation
   is CPU theft from the per-row read path (zero lock-wait samples on the
   read side), then re-check the trigger gate on the same host class.
3. **MEDIUM, signing-path p95 tail at low concurrency.** One and two
   worker runs show p95 29.3 and 22.5 ms against the 20 ms SKL-S3-06
   total budget, and the authorize component p95 exceeds its 10 ms share.
   Proposed card: qualify the S3-06 signer sidecar (primary plus standby)
   under load and either hold the 20 ms budget through sidecar
   parallelism or publish a revised budget with human approval. The
   ceiling itself, roughly 500 to 560 operations per second, is far above
   the envelope.
4. **LOW, single-process API throughput ceiling.** The API process
   saturates one core at roughly 600 to 810 requests per second. The S0-02
   plan allots 2 CPUs, so a multi-process worker count needs tuning only
   when real user counts exist. No card needed until then; recorded so
   the number is published.

## Capacity-plan reconciliation

Against `docs/evidence/platform/CHIAP01-CAPACITY-QUALIFICATION-2026-08-19.md`
section 7 service ceilings: `sklegal-api` peaked at 168 MiB RSS and one
core against its 2 CPU / 2 GiB limit; the Temporal worker scenario ran
in-process against the interactive-queue profile. The PostgreSQL
saturation container was not pinned to the plan's 3 CPU / 6 GiB
`sklegal-postgres` limit, so its numbers are a host-class ceiling, not a
service-limited one (limitation 3 below). No storage conclusion is
affected: all benchmark data lived in disposable tmpfs containers removed
on exit.

## Verification

```text
PASS: .tools/bin/uv run --locked pytest tests/test_load_saturation.py -q
      (24 passed, including the Temporal smoke against the disposable
      dev stack; that test self-skips when the stack is unreachable)
PASS: .tools/bin/uv run --locked pytest tests/test_load_postgres_saturation.py -q
      (12 passed, including the dockerized tiny full-scenario smoke)
PASS: ruff check and ruff format --check on both drivers and both test files
```

The recovered in-flight drivers required two functional repairs before
they would run: `DispatchRequest` digests were being built as
`run_key:artifact` strings, which violates the
`^[0-9a-f]{64}$` digest constraint and made every Temporal workflow input
invalid; and the completion check read a nonexistent
`dispatch_receipt` attribute instead of `dispatch_receipt_digest`. Both
are regression-tested (`test_dispatch_digests_are_valid_sha256` and the
Temporal smoke).

## Known limitations

1. The envelope's user count, read rate, and audit rate are derived
   assumptions, not a measured census. They need human confirmation
   before becoming service-level objectives.
2. All numbers are single-host, single-run, and from a 24-CPU
   workstation-class host. Treat them as ceilings for this class, per the
   S3-09 precedent; the serialization and per-row shapes are design
   properties and will hold on chiap01, the absolute values may not.
3. The PostgreSQL benchmark container ran without the capacity plan's
   3 CPU service limit, so those ceilings are host-class, not
   service-limited. A follow-up run under the plan's cpuset is bundled
   into bottleneck 2's re-measure.
4. The signing OpenPGP scenario uses 40 operations per worker per level;
   per-level p95 tails at that sample size are indicative, consistent with
   the S3-06 caveat at 30 iterations.
5. The API scenario uses the in-memory read store with the real CapAuth
   boundary; the durable read repository behind it was not exercised over
   HTTP in this slice (the RLS-bound database shape is measured directly
   in the PostgreSQL scenario).
6. The stub-signer scenario variant is a sanity mode only; all published
   signing numbers are real OpenPGP.

## Rollback

No data, migration, or service state changed. Rollback is: delete
`scripts/load_saturation.py`, `scripts/load_postgres_saturation.py`,
`tests/test_load_saturation.py`, `tests/test_load_postgres_saturation.py`,
this document, and `HANDOFF.md`; remove `build/benchmarks/` artifacts;
tear down the disposable dev stack with `./scripts/dev_dependencies.sh
down` if it is no longer needed.
