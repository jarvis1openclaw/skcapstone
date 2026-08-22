# SKL-S5-04B handoff

Card: `3e3c32d6` (`SKL-S5-04B`), load and saturation qualification
Worktree: `/tmp/swarm/3e3c32d6`, branch `swarm/3e3c32d6`

## Summary

Recovered the two in-flight saturation drivers from the earlier merge,
repaired them to a passing state, added full test coverage, executed the
qualification benchmarks, and published the evidence receipt with the
pilot load envelope, pass/fail results, ceilings, and a bottleneck
triage.

## Files changed

| File | Change |
|---|---|
| `scripts/load_saturation.py` | Fixed: dispatch digests now sha256 hex (were invalid `key:artifact` strings), completion check uses `dispatch_receipt_digest`, signer warmup phase added so cold gpg-agent start does not skew p95; ruff formatted |
| `scripts/load_postgres_saturation.py` | Ruff formatted only; logic unchanged from recovery |
| `tests/test_load_saturation.py` | New: 24 tests (statistics, arg parsing, pilot-scale app through the real CapAuth boundary, workflow input shape, driver smoke runs for api, signing stub and real OpenPGP, and Temporal when the dev stack is reachable) |
| `tests/test_load_postgres_saturation.py` | New: 12 tests (SQL input builders, seed shape, output parser, defaults, dockerized tiny full-scenario smoke) |
| `docs/evidence/platform/SKL-S5-04B-LOAD-SATURATION-2026-08-22.md` | New: the qualification receipt |
| `HANDOFF.md` | This file |

Benchmark artifacts under `build/benchmarks/` are gitignored by design;
their SHA256 hashes are recorded in the receipt.

## Tests and exact results

```text
uv run --locked pytest tests/test_load_saturation.py -q
  24 passed in 2.45s
uv run --locked pytest tests/test_load_postgres_saturation.py -q
  12 passed in 68.87s (dockerized smoke included)
uv run --locked pytest tests/test_load_saturation.py \
    tests/test_load_postgres_saturation.py \
    tests/test_audit_chain_head_benchmark.py \
    tests/test_capauth_hotpath_benchmark.py \
    tests/test_api_workspace.py tests/test_worker_workflows.py -q
  107 passed, 22 subtests passed in 96.51s
ruff check scripts/load_saturation.py scripts/load_postgres_saturation.py \
    tests/test_load_saturation.py tests/test_load_postgres_saturation.py
  All checks passed
ruff format --check (same four files)
  4 files already formatted
```

Run with `UV_CACHE_DIR=$PWD/.tools/uv-cache` and the pinned
`.tools/bin/uv` from `./scripts/bootstrap.sh`.

## Acceptance criteria evidence

- Published load envelope with pass/fail against capacity plan: receipt
  sections 2 and 3. The envelope is an explicitly labeled assumption set
  (no numeric target existed in `docs/planning/`). Results: API PASS
  (505 to 810 req/s vs 5 req/s burst demand, p95 83 ms at 32 workers,
  168 MiB RSS and one core against the 2 CPU / 2 GiB S0-02 limits);
  signing PASS at 4+ workers, FAIL the 20 ms p95 total at 1 to 2 workers;
  Temporal PASS (20.95 wf/s vs 0.033/s demand); CapAuth durable SQL PASS
  (thousands of ops/s, single-digit ms p99).
- Signing-path and audit-chain ceilings measured under load: signing
  ceiling roughly 500 to 560 issue-plus-authorize ops/s at 8 workers;
  audit chain-head 91 to 99 appends/s per tenant idle, 55 to 70 under
  concurrent read load; both chains verified after every run, deadlock
  delta 0.
- Bottleneck list triaged into cards: receipt section 6 proposes four
  follow-up cards (per-row RLS authorization HIGH; audit p99 watch item
  MEDIUM; signing p95 tail MEDIUM; single-process API ceiling LOW). This
  agent may not run board commands, so filing them is left to jarvis.

## Known limitations

Recorded in receipt section 9; the load-bearing ones:

- The envelope assumptions (5 concurrent principals, 6 reads per minute,
  2 governed runs per minute) need human confirmation before becoming
  service-level objectives.
- All numbers are single-run, single-host, 24-CPU workstation class.
- The PostgreSQL container was not pinned to the capacity plan's 3 CPU
  service limit, so its ceilings are host-class, not service-limited.
- The recovered drivers arrived with two defects (invalid dispatch digest
  construction, wrong result attribute); both are now regression-tested,
  but any other recovered in-flight script from the same batch deserves
  the same skepticism.

## Reproduction

```bash
./scripts/bootstrap.sh
export UV_CACHE_DIR=$PWD/.tools/uv-cache
.tools/bin/uv run --locked python scripts/load_saturation.py --output build/benchmarks/load-api.json api
.tools/bin/uv run --locked python scripts/load_saturation.py --output build/benchmarks/load-signing.json signing
./scripts/dev_dependencies.sh up
.tools/bin/uv run --locked python scripts/load_saturation.py --output build/benchmarks/load-temporal.json temporal
.tools/bin/uv run --locked python scripts/load_postgres_saturation.py --label <label>
```

## Migration or rollback evidence

No data, migration, or service state changed. Rollback is deleting the
six files listed above and tearing down the disposable dev stack
(`./scripts/dev_dependencies.sh down`) if desired. The two benchmark
containers were removed on exit by the harness.

## Linked card update

Not performed by this agent (board commands are reserved to jarvis).
Everything jarvis needs to complete card `3e3c32d6` is in this file and
in `docs/evidence/platform/SKL-S5-04B-LOAD-SATURATION-2026-08-22.md`,
including the four proposed follow-up cards for the bottleneck triage.
