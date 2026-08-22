# HANDOFF: SKL-S3-04C embedding qualification verdict, alias rollback, eval report

Card `cb452f95` (slice of SKL-S3-04 `4c4ca6b0`; dependency SKL-S3-04B
`e4e3c5df` complete). Branch `swarm/cb452f95`, head `0b3f633`.

## Files changed

- `config/retrieval/embedding-qualification-thresholds.json` (new): approved
  threshold set revision 1.0.0, pinned to dataset freeze
  `c4f829781badfc0139dda420c539ee30d4d43ac7c189aa55114af385ed02d189`,
  harness 1.0.0, k values (1, 5, 10). Bounds: recall at least
  0.60/0.90/0.95, nDCG at least 0.60/0.65/0.70, MRR at least 0.65,
  citation accuracy at least 0.90 (fixture-kind waiver recorded with the
  enforced real-embedder floor), macro latency mean at most 0.05 s, max
  latency at most 0.25 s, leakage exactly 0 and never waivable. Approval
  basis recorded in the file.
- `packages/retrieval/src/sklegal_retrieval/embedding_qualification.py`
  (new): threshold models and validators, exact-byte-hashed loader, the
  deterministic verdict engine with per-check citations, and the derived
  three-way decision.
- `packages/retrieval/src/sklegal_retrieval/embedding_alias.py` (new): the
  `embedding.serving.primary` alias registry with bind, exercised rollback
  to base BGE-M3, fail-close, resolve, and serve through the bound route's
  shadow plane; append-only revision history citing verdict shas.
- `packages/retrieval/src/sklegal_retrieval/__init__.py` (modified): new
  exports for both modules.
- `tests/test_embedding_qualification.py` (new): 37 tests.
- `scripts/embedding_qualification_eval.py` (new): qualification runner and
  live rollback drill; exits nonzero on any failed gate.
- `docs/development/EMBEDDING-QUALIFICATION.md` (new): bounds table, waiver
  scope, rollback gates, fixture boundary, run commands.
- `docs/evidence/retrieval/SKL-S3-04C-COMPLETION-EVIDENCE-2026-08-22.md`
  (new): completion evidence following the sibling pattern.

Commits: `f59c06c` (implementation), `c1b7212` (tests), `0b3f633`
(runner, guide, evidence).

## Tests and exact results

From the worktree root, with
`UV_CACHE_DIR=$PWD/.tools/uv-cache /tmp/sklegal-uv/bin/uv` (the worktree
has no `.tools/bin/uv`; the shared `/tmp/sklegal-uv/bin/uv` is the same
tool):

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
```

Note: `tests/test_workspace_pilot_projection.py` imports `sklegal_api`, so
it runs under the `sklegal-api` package, not `sklegal-retrieval`.

```text
uv run --locked --package sklegal-retrieval --group dev ruff check \
  packages/retrieval/src/sklegal_retrieval/ tests/test_embedding_qualification.py \
  scripts/embedding_qualification_eval.py
Result: All checks passed!

uv run --locked --package sklegal-retrieval --group dev ruff format --check \
  packages/retrieval/src/sklegal_retrieval/ tests/test_embedding_qualification.py \
  scripts/embedding_qualification_eval.py
Result: 20 files already formatted

python3 scripts/embedding_qualification_eval.py
Result: exit 0 (full transcript summarized below)

rg -n '\x{2013}|\x{2014}' <new and changed files>
Result: no findings (exit 1 from rg, no matches)

git diff --check
Result: clean (exit 0)
```

The `--group dev` flag is required on this worktree; a plain
`uv run --locked --package sklegal-retrieval pytest ...` fails with
`ModuleNotFoundError: No module named 'pytest'`.

## Acceptance criteria evidence

Verdict cites exact metrics:

- Every check on both routes carries its exact measured value and bound.
  Clean fixture run: custom recall@1 `0.700000 >= 0.600000` ... mrr
  `0.704518 >= 0.650000`, base nDCG@10 `0.740503 >= 0.700000`, mrr
  `0.704060 >= 0.650000`, both routes leakage `0 == 0`, citation accuracy
  waived for the fixture kind with the 0.90 floor recorded for real
  embedders. Decision `CUSTOM_QUALIFIED`.
- Failing-custom run (mrr forced to 0.1): decision `ROLLBACK_TO_BASE`, the
  failed check cites `mrr measured 0.100000 bound >= 0.650000`
  (`test_a_failed_custom_route_rolls_back_to_base`).
- The verdict rejects freeze, harness-version, and k-value mismatches
  against the approved threshold pins, and a decision that disagrees with
  the route verdicts fails model validation.

Rollback path exercised, not documented:

- `test_rollback_serves_the_base_route_and_cites_the_failed_check`: bind
  custom, serve query (route `custom_legal`, revision 1), rollback on the
  failing verdict, serve the same query again (route `base_bge_m3`, model
  `embedding.bge-m3-base.shadow`, revision 2), history
  `[(1, bind), (2, rollback)]`, rollback reason contains the exact failed
  check citation.
- `scripts/embedding_qualification_eval.py` executes the drill live every
  run: binds custom on the clean verdict, serves `QA-Q-0001` through it,
  forces a failing custom verdict through the `leak_partitions` simulation
  seam (custom `leakage_count failed (72 == 0)` while base qualifies),
  rolls back to base, serves the same query through
  `embedding.bge-m3-base.shadow` at revision 2, prints the revision history
  with verdict shas, and exits 0 only when every gate holds.
- Rollback gates reject: no rollback without an existing binding on exactly
  the failed custom route, no rollback on a passing verdict, no rollback to
  an unqualified target, no bind of an unqualified route.
- Fail-closed path: when both routes fail (leakage seam on both), the
  verdict is `FAIL_CLOSED`, `fail_close` appends the closing revision, and
  `resolve`/`serve_query` refuse every query.

## Known limitations

- No numeric approved thresholds existed in the repo; the TTDS names only
  the metric families. Revision 1.0.0 was set by this card under the
  owner-approved architecture gate (board card `b04de409`) and the
  owner-approved retrieval amendment `AMENDMENT-SKL-S2-10`, with the
  approval basis recorded in the threshold file. Its values are calibrated
  so the S3-04B fixture measurements pass. Any bound change is a new
  owner-approved revision with fresh qualification evidence, never an
  in-place edit.
- The compared routes are deterministic hash fixtures. The numbers exercise
  and pin the qualification machinery; they never measure, qualify,
  promote, or demote a real model. A real-embedder qualification run needs
  no file change: the 0.90 citation floor binds as-is and the fixture
  waiver does not apply.
- Fixture citation accuracy is 0.0 by construction (hash fixtures carry no
  semantic ranking signal); the waiver covers only the
  `deterministic_hash_fixture` kind and only `citation_accuracy`.
- Latency bounds govern the local in-process shadow plane with the harness
  clock; PostgreSQL wire latency belongs to the deployment qualification
  slices.
- The alias is an in-registry state machine over the shadow planes; wiring
  it into the live serving path is a later slice.

## Rollback of this card

Git revert of commits `f59c06c`, `c1b7212`, `0b3f633` on
`swarm/cb452f95`. The slice is files-only: no database state, no
HammerTime path, no external action, no secret, no board mutation.
