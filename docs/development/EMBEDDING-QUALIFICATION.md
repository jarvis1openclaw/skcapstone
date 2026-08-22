# Embedding qualification thresholds and serving alias

SKL-S3-04C slice of SKL-S3-04 (card `cb452f95`, dependency: SKL-S3-04B
`e4e3c5df`). This document describes the approved threshold set, the
deterministic qualification verdict, and the serving alias with its exercised
rollback path to base BGE-M3.

## What the slice does

The slice turns SKL-S3-04B shadow comparison measurements into a decision
through three pieces:

1. The approved threshold set
   (`config/retrieval/embedding-qualification-thresholds.json`, schema
   `sklegal-embedding-qualification-thresholds/v1`) pins one dataset freeze,
   one harness version, one k-value vector, and one bound per metric family.
   The file hash pins its exact bytes; any bound change is a new owner
   approved version, never an in-place edit.
2. `packages/retrieval/src/sklegal_retrieval/embedding_qualification.py`
   applies every bound to a `ShadowComparisonReport` and emits an
   `EmbeddingQualificationVerdict`. Every check cites the exact measured
   value and the exact bound (`recall@5: passed (0.950000 >= 0.900000)`), so
   the verdict is auditable without rerunning the harness. The verdict
   derives one of three decisions: `CUSTOM_QUALIFIED`,
   `ROLLBACK_TO_BASE`, or `FAIL_CLOSED`.
3. `packages/retrieval/src/sklegal_retrieval/embedding_alias.py` owns the
   serving alias `embedding.serving.primary`. The registry binds exactly one
   qualified route at a time, serves queries through that route's shadow
   plane, rolls back to base BGE-M3 when a verdict fails the bound custom
   route, and fails closed when no route qualifies. Every transition appends
   one immutable revision citing the sha of the justifying verdict.

## The approved bounds (revision 1.0.0)

| Metric family | Bound |
| --- | --- |
| Recall@1 / @5 / @10 | at least 0.60 / 0.90 / 0.95 |
| nDCG@1 / @5 / @10 | at least 0.60 / 0.65 / 0.70 |
| MRR | at least 0.65 |
| Citation accuracy | at least 0.90 |
| Macro latency mean | at most 0.05 s |
| Max latency | at most 0.25 s |
| Leakage count | exactly 0, never waivable |

Bounds bind macro-averaged measurements of the SKL-S3-04B harness over the
pinned dataset freeze. Latency bounds govern the local in-process shadow
plane measured by the harness clock; PostgreSQL wire latency belongs to the
deployment qualification slices.

One fixture embedder waiver exists: deterministic hash fixture embedders
measure citation accuracy 0.0000 because they carry no semantic ranking
signal, so exact-citation rank-one hits are chance. The waiver applies only
to the `deterministic_hash_fixture` embedder kind and only to
`citation_accuracy`; the 0.90 floor stays enforced for every other embedder
kind without any file change. Leakage tolerates exactly zero events and can
never be waived.

## Rollback is exercised, not documented

The rollback path is a real state transition with gates:

- `rollback` requires a verdict whose decision is `ROLLBACK_TO_BASE`, a
  registered base BGE-M3 target whose own route verdict qualified, and a
  current binding on exactly the custom route the verdict failed. The
  rollback reason cites every failed check with its measured value and bound.
- After the rollback, `serve_query` executes through the base route's shadow
  plane and the served record cites the rollback revision sequence.
- The runner script `scripts/embedding_qualification_eval.py` exercises the
  whole drill on every run: it binds the custom route on a qualifying
  verdict, serves a frozen query, forces a failing custom verdict through the
  cross-partition leakage simulation seam, rolls the alias back, and serves
  the same query through base BGE-M3. It exits nonzero when any gate fails.
- When no route qualifies, `fail_close` appends a fail-closed revision and
  both `resolve` and `serve_query` refuse every query.

## Fixture boundary

The committed routes are deterministic hash fixtures. Their numbers exercise
and pin the qualification machinery; they never measure, qualify, promote, or
demote any real model. Qualifying the real custom legal embedding and real
base BGE-M3 behind the provider-neutral model gateway is a later task and
must not change the threshold schema, verdict shape, or alias transitions
here. A real-embedder qualification run needs no file change: the citation
floor binds as-is and the fixture waiver simply does not apply.

## Run commands

```text
uv run --locked --package sklegal-retrieval pytest \
  tests/test_embedding_qualification.py -q

python3 scripts/embedding_qualification_eval.py
```

The script prints every check citation on both routes, the decision, the
served queries before and after the rollback, and the full revision history
with its verdict sha citations.
