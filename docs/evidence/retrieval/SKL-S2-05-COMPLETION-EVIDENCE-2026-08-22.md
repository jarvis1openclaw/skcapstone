# SKL-S2-05 completion evidence

Date: 2026-08-22

Board card: `f5ed9d24`

## Delivered

- `sklegal_retrieval.corpus_registry`: the materialized corpus registry model
  with the eight contract-pinned counts (source, normalized, decomposition,
  vector, graph, reject, orphan, release), core outbox watermark pins,
  `required_replay_lsn`, projection lag, and last-reconciled state per Tenant
  and release.
- An idempotent `CorpusRegistryProjector` that applies transactional outbox
  events with strict sequence, absolute counts, watermark compare-and-set, and
  a `CoreWatermarkLsnIndex` port that populates the watermark-to-LSN mapping
  the replica gate consumes.
- `sklegal_retrieval.corpus_health.BoundedCorpusHealthReader`: the only health
  read path, backed by one materialized snapshot read behind a hard 100 ms
  budget on a daemon worker thread, fail-closed to `unavailable` on budget
  breach, store outage, or invalid state, and structurally incapable of corpus
  tree or projection backend scans.
- `sklegal_retrieval.corpus_reconciliation.DeepCorpusReconciler`: the scheduled
  deep job that detects missing decomposition, orphan vector, stale graph,
  changed source, missing source, and count drift, writes corrected counts and
  last-reconciled state through one compare-and-set, and aborts closed on
  timeout, port outage, or write conflict.
- `config/retrieval/corpus-registry-contract.json`: the machine-readable
  contract, and `docs/development/CORPUS-REGISTRY.md`: the paired document.

## Verification

Command, from the worktree root:

```text
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-retrieval \
  pytest tests/test_corpus_registry.py tests/test_corpus_health.py \
  tests/test_corpus_reconciliation.py tests/test_corpus_registry_contract.py -q
```

Result: `68 passed, 19 subtests passed in 0.59s`.

Neighbor suites covering the consumed models, orchestrator, trace, and the
retrieval partition contract:

```text
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-retrieval \
  pytest tests/test_retrieval_models.py tests/test_retrieval_orchestrator.py \
  tests/test_retrieval_trace.py tests/test_retrieval_partition_contract.py -q
```

Result: `86 passed, 20 subtests passed in 0.24s`.

Lint and format:

```text
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-retrieval \
  ruff check <the seven new or changed corpus files>
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-retrieval \
  ruff format --check <the seven new or changed corpus files>
```

Result: `All checks passed!` and `7 files already formatted`.

## Acceptance criteria evidence

Health responds within the approved fixed budget:

- The contract pins `health.fixed_budget_ms = 100` with
  `budget_exceeded_status = unavailable`, and
  `tests/test_corpus_health.py` includes a 200-Tenant by 10-release registry
  tree answering inside the 100 ms budget, plus budget-breach, store-outage,
  and wedged-store cases that fail closed to `unavailable` with null counts
  and never report zero counts for a failed read.

Deep reconciliation detects missing and orphaned projections:

- `tests/test_corpus_reconciliation.py` covers missing decomposition,
  orphan vector rows (materialized into the orphan count), stale graph
  against relational watermarks, changed source pins, missing sources,
  count drift with registry and observed counts, timeout and outage aborts
  that write no state, write-conflict abort where the concurrent outbox
  event wins, watermark/LSN/lag pin preservation, and convergence of
  incremental outbox updates with deep reconciliation.

## Limitations and rollback

The registry store is the in-memory contract implementation; the PostgreSQL
store, outbox wiring, and scheduler registration for the deep job are
producer work on later cards. `CoreWatermarkLsnIndex` must be backed by the
core outbox so every live watermark resolves. No HammerTime path was read or
changed, no external action occurred, and no secret was touched. Rollback is
a Git revert of this card's commits; no data migration or external state
occurred.
