# SKL-S3-09 per-tenant audit chain-head write serialization measurement

Card: `1b0a7b29`
Implementer: `kimi-skl-s3-09`
Date: 2026-08-21
Status: Measurement published, card moved to Review

This receipt quantifies the single-tenant append throughput ceiling imposed by
the append-only audit design in migration `0007_append_only_audit_outbox.sql`.
Every call to `sklegal_audit.append_event` locks the tenant's single
`sklegal_audit.chain_heads` row with `SELECT ... FOR UPDATE`, so all appends
for one tenant serialize through that row. The measurement below is the
throughput ceiling and lock-wait profile of that design point.

## Method

Benchmark script: `scripts/benchmark_audit_chain_head.py` (new, self-contained
measurement tooling; no shared package code was modified).

1. Start a disposable PostgreSQL container from the pinned contract-test image
   (`postgres:17.7-alpine@sha256:a6d31f853205ce20d399df4e33a0b4c715672f232f4ee7440499747e6e02c126`),
   data directory on tmpfs, `--network none`.
2. Apply the full migration chain through `scripts/manage_migrations.py` as
   `sklegal_migrator`, provision `sklegal_runtime`, seed two synthetic tenants
   (Client, Engagement, Matter, principal, memberships), and bind one runtime
   login role per tenant through `scripts/provision_postgres_principal.py`.
   The benchmark therefore drives the exact production append path: RLS-bound
   role, `SECURITY DEFINER` function, controlled-writer triggers, hash-chain
   payload construction, chain-head update, and outbox insert.
3. For each concurrency level (1, 2, 4, 8, 16, 32 workers), spawn that many
   persistent `psql` sessions bound to the same tenant role. Each worker
   issues `SELECT bench.timed_append(...)` statements, one append per
   statement and one statement per transaction, matching the production
   one-statement-per-append shape used by `PostgresAuditRepository`. The
   helper brackets exactly the `append_event` call with `clock_timestamp()`
   and returns both timestamps, so per-append latency is measured server-side
   and includes the chain-head row-lock wait.
4. A monitor session samples `pg_stat_activity` every 10 ms into
   `bench.samples`, counting client backends of the benchmark roles waiting on
   `wait_event_type = 'Lock'`. Samples are windowed to the interval in which
   workers were actually appending.
5. Control run: 8 workers per tenant across two tenants (16 total) to verify
   that the serialization boundary is per tenant.
6. After all levels, `sklegal_audit.verify_current_tenant_chain()` is executed
   per tenant and `pg_stat_database.deadlocks` is diffed.

Reproduce:

```bash
.tools/bin/uv run --locked python scripts/benchmark_audit_chain_head.py \
  --label <label> --output build/audit/chain-head-benchmark.json
```

Raw result artifact: `build/audit/chain-head-benchmark.json`
SHA256: `2b4d5301bb29fd1a419248345bd9ff4fedba8c374f40283cf716e18d5cf98911`
(label `chiap01-workstation-2026-08-21-r2`, seed 309).

Environment: PostgreSQL 17.7, container data on tmpfs, host 24 logical CPUs,
`Linux-7.0.0-29-generic-x86_64-with-glibc2.39`. Numbers are
environment-specific; treat them as the ceiling for this class of host, not a
portable constant.

## Results: single tenant, 200 appends per worker per level

| Workers | Events | Ops/sec | p50 ms | p99 ms | Samples with lock waiters | Mean lock waiters |
| ------- | ------ | ------- | ------ | ------ | ------------------------- | ----------------- |
| 1       | 200    | 68.78   | 13.45  | 26.19  | 0.0%                      | 0.00              |
| 2       | 400    | 96.25   | 19.58  | 30.08  | 38.3%                     | 0.38              |
| 4       | 800    | 89.66   | 42.88  | 61.40  | 99.4%                     | 2.38              |
| 8       | 1600   | 86.02   | 92.65  | 123.99 | 99.9%                     | 6.39              |
| 16      | 3200   | 84.18   | 190.93 | 242.18 | 100.0%                    | 14.36             |
| 32      | 6400   | 80.07   | 395.00 | 654.96 | 100.0%                    | 30.26             |

Two-tenant control (8 workers per tenant, 16 workers total, 200 appends per
worker): 170.59 ops/sec aggregate, a 1.983x speedup over the single-tenant
8-worker level (86.02 ops/sec). Per-tenant chains verified: alpha true, beta
true.

Post-run integrity: `verify_current_tenant_chain()` returned true for both
tenants; event count 12,600 single-tenant plus 3,200 control equals the
expected append count exactly; deadlock delta 0.

## Interpretation

- **Throughput ceiling: roughly 80 to 96 appends per second per tenant on
  this host.** Throughput peaks at 2 workers (96.25 ops/sec) and degrades
  gently as concurrency rises (80.07 ops/sec at 32 workers). The ceiling is
  set by the serialized chain-head transaction: implied in-database service
  time is about 10 to 12 ms per append (canonical JSON construction, SHA256,
  event insert, chain-head update, outbox insert, RLS policy evaluation,
  SECURITY DEFINER context switches).
- **Contention starts immediately.** At 2 concurrent writers, 38.3% of
  monitor samples already show a lock waiter; from 4 workers up, virtually
  100% of samples show waiters and the estimated lock-wait fraction of mean
  latency rises from 0.30 (2 workers) to 0.96 (32 workers).
- **Latency grows linearly with concurrency**, as expected for a FIFO-ish
  queue behind one row lock: p50 13.5 ms at 1 worker, 395 ms at 32 workers.
  Little's law holds (32 workers / 0.397 s mean latency = 80.6 ops/sec,
  matching the measured 80.07), which cross-validates the measurement.
- **The serialization boundary is per tenant, not global.** Two tenants at 8
  workers each delivered 1.983x the single-tenant 8-worker throughput, so
  tenants do not contend with each other. The lock-wait profile of the
  control (mean 12.7 waiters across 16 workers, two chains) matches two
  independent saturated chains.

## Assessment against Sprint 5 load

No numeric Sprint 5 audit-rate target exists in `docs/planning/` today.
Sprint 5 is the single-matter Liberty Auto Plaza pilot; a governed agent run
emits a handful of audit events per gated action, so steady-state pilot load
is far below this ceiling. However:

- A single hot tenant can never exceed about 100 appends/sec regardless of
  hardware, because the design is one locked row per tenant.
- Contention and queueing latency appear at just 2 concurrent writers; under
  a burst of parallel agent loops, every gated action pays chain-head queue
  latency (p99 over 600 ms observed at 32 writers).

The ceiling does not block the Sprint 5 pilot, but it is low in absolute
terms and it caps per-tenant burst absorption. Per the card scope, a
mitigation follow-up card has been filed proposing per-matter (or
per-partition) chain heads and/or batch append, so the design decision is
explicit before multi-tenant load claims are made in later sprints.

## Files changed

- `scripts/benchmark_audit_chain_head.py` (new): the benchmark driver.
- `tests/test_audit_chain_head_benchmark.py` (new): unit tests for the
  statistics and payload helpers plus a docker-based smoke test that runs the
  real benchmark at tiny scale and asserts chain verification, event counts,
  zero deadlocks, and result schema.
- `docs/evidence/audit/SKL-S3-09-CHAIN-HEAD-SERIALIZATION-2026-08-21.md`
  (new): this receipt.
- `build/audit/chain-head-benchmark.json` (generated artifact, not committed):
  raw measurements.

## Tests

- `.tools/bin/uv run --locked python -m unittest tests.test_audit_chain_head_benchmark -v`:
  11 tests, all pass (10 helper unit tests plus 1 containerized smoke test,
  25.1 s).
- `.tools/bin/uv run --locked ruff check scripts/benchmark_audit_chain_head.py tests/test_audit_chain_head_benchmark.py`:
  pass.
- `.tools/bin/uv run --locked ruff format --check scripts/benchmark_audit_chain_head.py tests/test_audit_chain_head_benchmark.py`:
  pass.

## Known limitations

- Numbers are specific to this host, the pinned PostgreSQL 17.7 image, and
  tmpfs storage. A production chiap01 deployment with persistent storage and
  different CPU will have a different absolute ceiling; the serialization
  shape (flat throughput past 2 writers, linear latency growth) is a property
  of the design, not the host.
- Worker startup is staggered by tens of milliseconds per `docker exec`
  spawn; levels run long enough (200 appends per worker) that steady state
  dominates.
- The benchmark drives `append_event` directly and does not include workflow
  or API boundary overhead; end-to-end audited action rates will be lower.
- The two-tenant control uses 2 tenants; larger tenant counts were not
  measured.
- The new test module is not added to `scripts/run_checks.sh` because that
  shared file is being edited by other agents in flight; run it explicitly as
  shown above.
