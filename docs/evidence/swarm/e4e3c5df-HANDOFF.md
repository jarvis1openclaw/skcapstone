# HANDOFF: SKL-S3-04B shadow projection comparison harness

Card: `e4e3c5df` (slice of SKL-S3-04, card `4c4ca6b0`). Dependency SKL-S3-04A
(`e1bc4552`, frozen dataset) is complete and was used as committed.

## Files changed

- `packages/retrieval/src/sklegal_retrieval/shadow_comparison.py` (new):
  shadow projection generations and the deterministic comparison harness.
  Route pins for the custom legal and base BGE-M3 routes, seed-pinned
  deterministic hash fixture embedders, per-Matter generation builds through
  the idempotent outbox projector, the pinned-statement `ShadowQueryRunner`
  behind `PostgresRetrievalAdapter` (`vector.exact.v1` over
  `sklegal_retrieval.vector_exact_v1(%s, %s)`), an injectable-clock
  evaluation plane, the metric set (Recall@k, nDCG@k, MRR, citation
  accuracy, latency, leakage), per-partition and macro report models with
  freeze/partition/generation agreement gates, and the
  `leak_partitions` / `leak_privilege` / `stale_shadow_generation`
  simulation seams.
- `packages/retrieval/src/sklegal_retrieval/postgres.py` (modified): pinned
  statement strings lifted into `VECTOR_EXACT_STATEMENT` and
  `LEXICAL_SEARCH_STATEMENT` module constants; call registry rewired to
  them. No behavior change.
- `packages/retrieval/src/sklegal_retrieval/__init__.py` (modified):
  package exports for the shadow comparison surface and the two constants.
- `tests/test_shadow_comparison.py` (new): 29 tests, 6 subtests.
- `scripts/shadow_retrieval_eval.py` (new): comparison runner; exits nonzero
  on leakage.
- `docs/development/RETRIEVAL-SHADOW-COMPARISON.md` (new): paired document.
- `docs/evidence/retrieval/SKL-S3-04B-COMPLETION-EVIDENCE-2026-08-22.md`
  (new): completion evidence with exact commands and results.

Commits on `swarm/e4e3c5df`: d9b8e5d (implementation), be76024 (tests),
508e2c2 (script and doc), 92365c2 (evidence), plus this handoff commit.

## Tests and exact results

Run from the worktree root with
`UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv`:

```text
uv run --locked --package sklegal-retrieval pytest tests/test_shadow_comparison.py -q
Result: 29 passed, 6 subtests passed in 0.67s

uv run --locked --package sklegal-retrieval pytest tests/test_shadow_comparison.py \
  tests/test_retrieval_postgres.py tests/test_retrieval_eval_dataset.py \
  tests/test_retrieval_partition_leak_matrix.py \
  tests/test_retrieval_partition_contract.py \
  tests/test_workspace_pilot_projection.py \
  tests/test_retrieval_adapter_coverage.py -q
Result: 156 passed, 213 subtests passed in 1.32s

uv run --locked --package sklegal-retrieval ruff check <all changed py files>
Result: All checks passed!

uv run --locked --package sklegal-retrieval ruff format --check <all changed py files>
Result: 5 files already formatted

python3 scripts/shadow_retrieval_eval.py
Result: harness 1.0.0, freeze c4f829781badfc01..., 14 documents, 16 queries,
33 judgments, both routes measured, leakage count 0 on both routes, exit 0.

rg -n '\x{2013}|\x{2014}' <changed files>  -> no findings
git diff --check                              -> passed
```

## Acceptance criteria evidence

- Shadow generations on the local PostgreSQL retrieval plane: every frozen
  query runs through the real `PostgresRetrievalAdapter` over the pinned
  vector-exact statement; the runner accepts no other statement and fails
  closed without an active query context; documents stage per Tenant/Matter
  partition through the idempotent outbox projector and rebuild identically.
- Deterministic metric harness: metric functions pinned to hand-computed
  values; two full runs with a step clock produce identical reports; latency
  measured via injectable clock; citation accuracy counts exactly the
  EXACT_CITATION queries; partitions are never pooled.
- Custom versus base BGE-M3 over the frozen dataset: both routes run over
  the same freeze and partition set; the report model rejects freeze,
  k-value, or partition-set disagreement.
- Required tests: deterministic metric calculation (`MetricUnitTests`,
  `test_comparison_is_deterministic_across_runs`), cross-partition leakage
  (`LeakageDetectionTests`, coded `cross_partition:`/`privilege_escalation:`
  with counts; clean plane reports zero), stale generation rejection
  (`test_serving_another_generation_is_rejected`,
  `test_a_freeze_mismatch_is_rejected`).

## Known limitations

- The committed embedders are deterministic hash fixtures standing in for
  the candidate models. Their numbers exercise and pin the harness; they
  never measure, qualify, promote, or demote any real model. Binding the
  real models behind the provider-neutral gateway is a later qualification
  task and must not change metric definitions or report shapes.
- Fixture-route citation accuracy is 0.0 on both routes: hash fixtures carry
  no semantic ranking signal, so the metric only becomes discriminating
  with a real embedder.
- Latency measures the local in-process plane with a simulated runner, not
  PostgreSQL wire time; wire latency belongs to deployment qualification.
- Both routes share one `rows_digest` by design: the digest covers source
  and content pins only, so it proves corpus parity across routes; routes
  differ in embeddings, projection set id, and per-partition access refs.

## Boundaries respected

No push, pull, or remote git access. No file outside this worktree was
modified. No HammerTime path, Inbox or otherwise, was touched. No external
action (email, filing, service) was attempted. No secrets or credentials
were read or written. No skcapstone board command was run; jarvis owns the
board and card completion. ASCII hyphens only; the em/en dash scan is clean.

## Rollback

Git revert of this card's commits (d9b8e5d, be76024, 508e2c2, 92365c2, and
this handoff commit). The slice is files-only: no database state, no
external system, no generated artifacts outside the repo.
