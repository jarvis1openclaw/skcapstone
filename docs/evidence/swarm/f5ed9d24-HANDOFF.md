# SKL-S2-05 handoff

Card: `f5ed9d24` (SKL-S2-05), branch `swarm/f5ed9d24`, worktree
`/tmp/swarm/f5ed9d24`.

## Files changed

Recovered in-flight implementation (already on this branch via `bc83c30`,
merged as `d29d7ce`) and completed by this handoff:

- `packages/retrieval/src/sklegal_retrieval/corpus_registry.py`: materialized
  registry model, projector, LSN index port, and in-memory store. Completed
  work: `apply` now raises `RetrievalIntegrityError` when the LSN index cannot
  pin a watermark's replay LSN (previously crashed inside model validation),
  the `CoreWatermarkLsnIndex` docstring states the fail-closed rule, and
  ruff formatting was applied.
- `packages/retrieval/src/sklegal_retrieval/corpus_health.py`: bounded health
  reader (unchanged this session; ruff clean).
- `packages/retrieval/src/sklegal_retrieval/corpus_reconciliation.py`: deep
  reconciler (formatting only this session).
- `config/retrieval/corpus-registry-contract.json`: machine-readable contract
  (recovered, unchanged).
- `docs/development/CORPUS-REGISTRY.md`: paired document; added the
  missing-LSN fail-closed rule and a paragraph naming the
  `CorpusRegistryStore` port contract.
- `tests/test_corpus_registry.py`: 20 tests (new).
- `tests/test_corpus_health.py`: 17 tests (new).
- `tests/test_corpus_reconciliation.py`: 22 tests (new).
- `tests/test_corpus_registry_contract.py`: 9 tests, 19 subtests (new).
- `docs/evidence/retrieval/SKL-S2-05-COMPLETION-EVIDENCE-2026-08-22.md`
  (new).
- `HANDOFF.md` (this file).

## Tests and exact results

From the worktree root, using the pinned uv toolchain (`.tools/bin/uv`,
`UV_CACHE_DIR=$PWD/.tools/uv-cache`):

```text
uv run --locked --package sklegal-retrieval pytest \
  tests/test_corpus_registry.py tests/test_corpus_health.py \
  tests/test_corpus_reconciliation.py tests/test_corpus_registry_contract.py -q
68 passed, 19 subtests passed in 0.59s
```

Per file: registry 20 passed in 0.12s; health 17 passed in 0.37s;
reconciliation 22 passed in 0.25s; contract 9 passed, 19 subtests in 0.09s.

Neighbor suites for consumed code:

```text
uv run --locked --package sklegal-retrieval pytest \
  tests/test_retrieval_models.py tests/test_retrieval_orchestrator.py \
  tests/test_retrieval_trace.py tests/test_retrieval_partition_contract.py -q
86 passed, 20 subtests passed in 0.24s
```

Lint and format on all seven new or changed corpus files:

```text
ruff check ...      -> All checks passed!
ruff format --check ... -> 7 files already formatted
```

## Acceptance criteria evidence

Health responds within the approved fixed budget:

- Contract pins `health.fixed_budget_ms = 100`,
  `budget_exceeded_status = unavailable`, and forbids full-tree scans,
  projection backend scans, per-Tenant breakdown, and zero-count failure
  reports.
- `tests/test_corpus_health.py` answers a 200-Tenant by 10-release materialized
  tree inside the 100 ms budget, and proves budget breach, store outage, a
  wedged store read, and invalid state each fail closed to `unavailable` with
  null counts.

Deep reconciliation detects missing and orphaned projections:

- `tests/test_corpus_reconciliation.py` detects missing decomposition,
  orphan vector rows (and materializes the orphan count), stale graph against
  relational watermarks, changed source pins, missing sources, and count
  drift with registry and observed counts; timeout, port outage, and write
  conflict abort with no state write; incremental outbox updates converge
  with deep reconciliation; coverage completeness is reported separately
  from health.

## Known limitations

- The registry store is the in-memory contract implementation. The
  PostgreSQL store, outbox wiring, and scheduler registration for the deep
  job are later producer cards.
- `CoreWatermarkLsnIndex` must be backed by the core outbox so every live
  watermark resolves to a replay LSN.
- Reconciliation drift findings are recorded in the report; alert routing and
  incident creation are out of scope for this card.
- No migration was needed: no existing schema or data changed. Rollback is a
  Git revert of this card's commits.

## Boundaries honored

No push or remote access, no files changed outside the worktree, no
HammerTime path access, no external action, no secrets read or written, no
skcapstone board commands, ASCII hyphens only in all deliverables.
