# SKL-S3-04B completion evidence

Date: 2026-08-22

Board card: `e4e3c5df` (slice of SKL-S3-04, card `4c4ca6b0`; dependency
SKL-S3-04A `e1bc4552` complete)

## Delivered

- `packages/retrieval/src/sklegal_retrieval/shadow_comparison.py`: the shadow
  projection comparison harness. Route pins for the two candidate embedding
  routes (`CUSTOM_LEGAL_ROUTE`, `BASE_BGE_M3_ROUTE`), the seed-pinned
  `DeterministicHashEmbedder` fixture, `build_shadow_generation` (per-Matter
  partitions staged through the idempotent outbox projector),
  `ShadowQueryRunner` (executes only the pinned
  `sklegal_retrieval.vector_exact_v1(%s, %s)` statement, enforces Matter
  scope and privilege authorization, fails closed without an active query
  context, carries explicit `leak_partitions` / `leak_privilege` simulation
  seams), `ShadowEvaluationPlane` (executes through
  `PostgresRetrievalAdapter` bound to `vector.exact.v1` with an injectable
  clock), the deterministic metric set (Recall@k, nDCG@k, MRR, citation
  accuracy, latency, leakage codes), per-partition and macro report models
  that reject freeze, partition-set, and generation disagreements,
  `evaluate_shadow_route`, `compare_shadow_routes`,
  `build_fixture_candidates`, and `stale_shadow_generation`.
- `packages/retrieval/src/sklegal_retrieval/postgres.py`: the pinned
  statement strings lifted into named module constants
  (`VECTOR_EXACT_STATEMENT`, `LEXICAL_SEARCH_STATEMENT`) with the call
  registry rewired to them; no behavior change.
- `packages/retrieval/src/sklegal_retrieval/__init__.py`: package exports
  for the shadow comparison surface and the two statement constants.
- `tests/test_shadow_comparison.py`: 29 tests plus 6 subtests covering
  hand-computed metric values, generation determinism, the full comparison
  over the frozen dataset with a deterministic clock, citation and recall
  eligibility, outcome coverage, both leakage seams, stale generation and
  freeze mismatch rejection, and plane guard behavior.
- `scripts/shadow_retrieval_eval.py`: the comparison runner. Loads the
  frozen dataset through `load_frozen_dataset`, prints freeze hash, harness
  version, per-route and per-partition metrics and leakage counts, exits
  nonzero on leakage, and restates the fixture-embedder boundary.
- `docs/development/RETRIEVAL-SHADOW-COMPARISON.md`: the paired document
  (metric definitions, fixture boundary, guardrails, run commands).

## Verification

From the worktree root with
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

uv run --locked --package sklegal-retrieval ruff check \
  packages/retrieval/src/sklegal_retrieval/shadow_comparison.py \
  packages/retrieval/src/sklegal_retrieval/postgres.py \
  packages/retrieval/src/sklegal_retrieval/__init__.py \
  tests/test_shadow_comparison.py scripts/shadow_retrieval_eval.py
Result: All checks passed!

uv run --locked --package sklegal-retrieval ruff format --check \
  packages/retrieval/src/sklegal_retrieval/shadow_comparison.py \
  packages/retrieval/src/sklegal_retrieval/postgres.py \
  packages/retrieval/src/sklegal_retrieval/__init__.py \
  tests/test_shadow_comparison.py scripts/shadow_retrieval_eval.py
Result: 5 files already formatted

python3 scripts/shadow_retrieval_eval.py
Result: harness 1.0.0 over freeze c4f829781badfc01..., 14 documents,
16 queries, 33 judgments; both routes measured (custom legal and base
BGE-M3 fixture routes), leakage count 0 on both routes, exit 0.

rg -n '\x{2013}|\x{2014}' <new and changed files>
Result: no findings.

git diff --check -- <new and changed files>
Result: passed.
```

Fixture-route macro measurements from the script run (hash fixtures, not
model measurements): custom legal route Recall@k 0.7000/0.9500/1.0000,
nDCG@k 0.6868/0.7281/0.7416, MRR 0.7045; base BGE-M3 route Recall@k
0.7000/0.9500/1.0000, nDCG@k 0.6832/0.7264/0.7405, MRR 0.7041; both routes
citation accuracy 0.0000, leakage count 0.

## Acceptance criteria evidence

Shadow projection generations on the local PostgreSQL retrieval plane:

- `build_shadow_generation` stages every frozen document into per Tenant and
  Matter partitions through the idempotent outbox projector
  (`test_partitions_are_grouped_by_tenant_and_matter`,
  `test_every_document_is_embedded_with_the_route_pins`), and a rebuild of
  the same route reproduces identical rows, digest, and projection set id
  (`test_rebuilding_one_route_reproduces_identical_rows`).
- Every frozen query executes through the real adapter over the pinned
  vector-exact statement; the runner refuses any other statement and any
  call without an active query context
  (`test_the_runner_executes_only_the_pinned_statement`,
  `test_fetch_without_a_query_context_is_unavailable`).

Deterministic metric harness (Recall@k, nDCG, MRR, citation accuracy,
latency, leakage):

- Metric functions are pinned to hand-computed values
  (`MetricUnitTests`), and two full comparison runs with a step clock
  produce identical reports
  (`test_comparison_is_deterministic_across_runs`).
- Latency is measured through an injectable clock; with a 0.5 s step clock
  every partition reports exactly 0.5 s mean and max
  (`test_fixture_comparison_reports_both_routes`).
- Citation accuracy counts exactly the EXACT_CITATION queries per partition
  (`test_citation_eligibility_counts_exact_citation_queries`).

Custom route versus base BGE-M3 over the S3-04A frozen dataset:

- `compare_shadow_routes` runs both routes over the same freeze and the
  identical partition set, and the report model rejects freeze, k-value, or
  partition-set disagreement
  (`test_routes_cover_identical_partitions_with_shared_freeze`).

Tests: deterministic metric calculation, cross-partition leakage, stale
generation rejection:

- Deterministic metric calculation: `MetricUnitTests` and
  `test_comparison_is_deterministic_across_runs`.
- Cross-partition leakage: with the `leak_partitions` seam the harness codes
  every foreign hit as `cross_partition:<query>:<document>` and counts them
  (`test_cross_partition_leakage_is_coded_and_counted`); the
  `leak_privilege` seam is coded `privilege_escalation:...`
  (`test_privilege_escalation_leakage_is_coded_and_counted`); a clean plane
  reports zero leakage (`test_a_clean_plane_reports_no_leakage`).
- Stale generation rejection: serving rows stamped with another projection
  generation raises `ShadowComparisonError`
  (`test_serving_another_generation_is_rejected`), and a dataset freeze
  mismatch is rejected before any query runs
  (`test_a_freeze_mismatch_is_rejected`).

## Limitations and rollback

The committed embedders are deterministic hash fixtures standing in for the
candidate models; their numbers exercise and pin the harness and never
measure, qualify, promote, or demote any real model. Binding the real
custom legal embedding and base BGE-M3 behind the provider-neutral gateway
is a later qualification task and must not change metric definitions or
report shapes. Fixture-route citation accuracy is 0.0 on both routes because
hash fixtures carry no semantic ranking signal; the metric becomes
discriminating once a real embedder is bound. Latency here measures the
local in-process plane with a simulated runner, not PostgreSQL wire time;
wire latency belongs to the deployment qualification slices. Rollback is a
Git revert of this card's commits; the slice is files-only, with no
database state, HammerTime path, external action, secret, or board mutation
involved.
