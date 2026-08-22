# SKL-S3-04C completion evidence

Date: 2026-08-22

Board card: `cb452f95` (slice of SKL-S3-04, card `4c4ca6b0`; dependency
SKL-S3-04B `e4e3c5df` complete)

## Delivered

- `config/retrieval/embedding-qualification-thresholds.json`: the approved,
  versioned threshold set (schema
  `sklegal-embedding-qualification-thresholds/v1`, revision 1.0.0) pinned to
  the SKL-S3-04A dataset freeze
  `c4f829781badfc0139dda420c539ee30d4d43ac7c189aa55114af385ed02d189` and
  harness 1.0.0, with the approval basis recorded in the file and the exact
  file hash pinned at load time. Bound changes are new owner-approved
  versions, never in-place edits.
- `packages/retrieval/src/sklegal_retrieval/embedding_qualification.py`: the
  threshold models with full validator coverage (k-vector alignment, ranking
  bounds in [0, 1], latency ordering, waiver shape, enforced real-embedder
  floor), `load_qualification_thresholds` (exact-byte hashing),
  `apply_qualification_thresholds` (rejects freeze, harness, and k-value
  mismatches; cites the exact measured value and bound for every check on
  every route; records the fixture waiver with its reason), the
  `MetricCheck` / `RouteQualificationVerdict` /
  `EmbeddingQualificationVerdict` models with a derived three-way decision
  (`CUSTOM_QUALIFIED`, `ROLLBACK_TO_BASE`, `FAIL_CLOSED`).
- `packages/retrieval/src/sklegal_retrieval/embedding_alias.py`: the serving
  alias `embedding.serving.primary`. `EmbeddingAliasRegistry` registers
  freeze-checked candidates, binds only routes whose verdict qualified,
  rolls back to base BGE-M3 only on a verdict that failed the currently
  bound custom route while the base route qualified (the rollback reason
  cites every failed check with measured value and bound), fails closed when
  no route qualifies, and serves queries through the bound route's shadow
  plane with the served record citing the live revision sequence. State is
  append-only `AliasRevision` history with contiguous sequences and
  verdict-sha citations.
- `packages/retrieval/src/sklegal_retrieval/__init__.py`: package exports
  for the qualification and alias surface.
- `tests/test_embedding_qualification.py`: 37 tests covering the threshold
  file and every validator rejection, exact citation strings for both
  fixture routes under a deterministic clock, the waiver recording, the
  failing-custom rollback verdict, leakage and latency fail-closed paths,
  freeze/harness/k-value mismatch rejection, decision disagreement
  rejection, the full alias drill (bind, serve, rollback, serve through the
  base route, history), rollback gate rejections, and the fail-close drill.
- `scripts/embedding_qualification_eval.py`: the qualification runner.
  Reruns the SKL-S3-04B comparison over the frozen dataset with the real
  clock, prints every check citation per route and the decision, then
  exercises the alias drill end to end including a forced rollback through
  the cross-partition leakage seam; exits nonzero on any failed gate.
- `docs/development/EMBEDDING-QUALIFICATION.md`: the paired document
  (bounds table, waiver scope, rollback gates, fixture boundary, run
  commands).

## Verification

From the worktree root with
`UV_CACHE_DIR=$PWD/.tools/uv-cache /tmp/sklegal-uv/bin/uv`:

```text
uv run --locked --package sklegal-retrieval --group dev pytest \
  tests/test_embedding_qualification.py -q
Result: 37 passed in 0.41s

uv run --locked --package sklegal-retrieval --group dev pytest \
  tests/test_embedding_qualification.py tests/test_shadow_comparison.py \
  tests/test_retrieval_postgres.py tests/test_retrieval_eval_dataset.py \
  tests/test_retrieval_partition_leak_matrix.py \
  tests/test_retrieval_partition_contract.py \
  tests/test_retrieval_adapter_coverage.py -q
Result: 180 passed, 213 subtests passed in 1.14s

uv run --locked --package sklegal-api --group dev pytest \
  tests/test_workspace_pilot_projection.py -q
Result: 13 passed in 0.55s

uv run --locked --package sklegal-retrieval --group dev ruff check \
  packages/retrieval/src/sklegal_retrieval/ tests/test_embedding_qualification.py \
  scripts/embedding_qualification_eval.py
Result: All checks passed!

uv run --locked --package sklegal-retrieval --group dev ruff format --check \
  packages/retrieval/src/sklegal_retrieval/ tests/test_embedding_qualification.py \
  scripts/embedding_qualification_eval.py
Result: 20 files already formatted

python3 scripts/embedding_qualification_eval.py
Result: exit 0. Clean verdict decision custom_qualified on both fixture
routes with 11 checks each; alias bound custom_legal at revision 1 and
served query QA-Q-0001; the leakage seam forced decision rollback_to_base
(custom leakage_count failed (72 == 0), base qualified); the alias rolled
back to base_bge_m3 at revision 2 and served the same query through
embedding.bge-m3-base.shadow; rollback reason cites
"leakage_count measured 72 bound == 0".

rg -n '\x{2013}|\x{2014}' <new and changed files>
Result: no findings.

git diff --check -- <new and changed files>
Result: passed.
```

## Acceptance criteria evidence

Verdict cites exact metrics:

- Every check on every route records its exact measured value and bound as
  strings: custom route recall@1 `0.700000 >= 0.600000` through
  leakage_count `0 == 0`, and the base route likewise
  (`test_the_clean_fixture_verdict_cites_every_custom_check`,
  `test_the_clean_fixture_verdict_cites_every_base_check`). The clean
  fixture decision is `CUSTOM_QUALIFIED`
  (`test_the_clean_verdict_qualifies_the_custom_route`), and the failing
  custom verdict yields `ROLLBACK_TO_BASE` with the failed mrr check citing
  `0.100000` against `0.650000`
  (`test_a_failed_custom_route_rolls_back_to_base`).
- The fixture waiver is explicit and scoped: only `citation_accuracy`, only
  the `deterministic_hash_fixture` kind, with the 0.90 real-embedder floor
  recorded in the file and enforced by validators
  (`test_the_waiver_is_recorded_with_its_reason_on_both_routes`,
  `test_a_waiver_may_only_cover_the_fixture_embedder_kind`,
  `test_the_enforced_real_embedder_floor_must_match_the_bound`). Leakage is
  exactly 0 and never waivable
  (`test_the_leakage_bound_is_exactly_zero`).

Rollback path is exercised, not documented:

- `test_rollback_serves_the_base_route_and_cites_the_failed_check` binds the
  custom route, serves a query through it, rolls back on the failing
  verdict, then serves the same query through base BGE-M3 with revision
  sequence 2, and verifies the append-only history
  `[(1, bind), (2, rollback)]` and the citation
  `mrr measured 0.100000 bound >= 0.650000` inside the rollback reason.
- The runner script executes the same drill live on every run, including
  serving through the rolled-back route, and exits nonzero when any gate
  fails (see the Verification section).
- Every rollback gate is proven to reject: an unqualified route cannot bind,
  a passing verdict cannot roll back, a rollback needs an existing binding
  on exactly the failed custom route, and only registered candidates
  participate (the `AliasRollbackTests` group).
- When nothing qualifies, the alias fails closed and serving refuses every
  query (`AliasFailCloseTests`).

## Limitations and rollback

The committed routes are deterministic hash fixtures standing in for the
candidate models; the qualification numbers exercise and pin the machinery
and never measure, qualify, promote, or demote any real model. The
thresholds revision 1.0.0 was set by this card under the owner-approved
architecture gate and the owner-approved retrieval amendment; any future
bound change is a new owner-approved revision with fresh qualification
evidence. Fixture citation accuracy is 0.0 by construction and waived only
for the fixture kind; the 0.90 floor binds the first real embedder
qualification without any file change. Latency bounds govern the local
in-process shadow plane; PostgreSQL wire latency belongs to the deployment
qualification slices. The alias is an in-registry state machine over the
shadow planes; wiring it to the live serving path is a later slice.
Rollback of this card is a Git revert of its commits; the slice is
files-only, with no database state, HammerTime path, external action,
secret, or board mutation involved.
