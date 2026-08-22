# Shadow projection comparison harness

SKL-S3-04B slice of SKL-S3-04 (card `e4e3c5df`, dependency: SKL-S3-04A
`e1bc4552`). This document describes the deterministic comparison harness in
`packages/retrieval/src/sklegal_retrieval/shadow_comparison.py`, its runner
script, and what its measurements do and do not mean.

## What the harness does

The harness executes every frozen query from the SKL-S3-04A dataset through
the real PostgreSQL retrieval plane entry point
(`PostgresRetrievalAdapter` bound to the closed `vector.exact.v1` template
and the pinned `sklegal_retrieval.vector_exact_v1(%s, %s)` statement) against
per-Matter shadow vector projections, then computes a fixed metric set
deterministically:

- Recall@k for k in (1, 5, 10) over documents judged at grade 2 or higher;
- graded nDCG@k (exponential gain `2^grade - 1`, logarithmic discount
  `log2(rank + 1)`);
- MRR (reciprocal rank of the first grade-2-or-higher hit);
- citation accuracy (exact-citation queries whose rank-1 result is the unique
  grade-3 controlling document);
- latency (mean and max per partition, measured by an injectable clock, so
  tests are deterministic);
- leakage (any served document from another Tenant/Matter partition, or any
  privileged document served to an unauthorized query).

Metrics are computed per Tenant and Matter partition and then macro-averaged
over partitions; partitions are never pooled. Reports carry the exact dataset
freeze hash, harness version, route pins, projection set and generation, and
a leakage count, and the report model rejects a comparison whose routes cover
different partitions or freezes.

## Routes and the fixture boundary

The two compared routes are pinned as logical identifiers:
`embedding.custom-legal.shadow` (custom legal route) and
`embedding.bge-m3-base.shadow` (base BGE-M3 route), both cosine distance,
dimension 256 in the committed fixture configuration.

The committed embedders are deterministic seed-pinned hash fixtures
(`DeterministicHashEmbedder`). They exist so the harness itself is
deterministic, local, and repeatable in CI. Their numbers exercise and pin
the harness; they never measure, qualify, promote, or demote any real model.
Binding the real candidate models behind the provider-neutral model gateway
is a later qualification task and must not change any metric definition,
report shape, or test in this module. The script output restates this
boundary on every run.

## Guardrails proven by tests

- Deterministic metrics: hand-computed Recall@k, nDCG@k, and MRR values in
  `tests/test_shadow_comparison.py`; two full comparison runs with a step
  clock produce identical reports.
- Cross-partition leakage: the runner has an explicit `leak_partitions`
  simulation seam; a run with the seam enabled codes every foreign hit as
  `cross_partition:<query>:<document>` and a clean run reports zero leakage.
  The `leak_privilege` seam does the same for privileged documents served to
  unauthorized queries (`privilege_escalation:...`).
- Stale generation rejection: `stale_shadow_generation` serves rows stamped
  with a different projection generation while the plane selects the true
  pins; the adapter's row validation rejects the disagreement and the
  harness converts it to `ShadowComparisonError`. A freeze-hash mismatch
  between dataset and generation is rejected the same way.
- Fail-closed plane: the query runner refuses calls without an active query
  context and refuses any statement other than the pinned vector-exact call;
  both surface as `RetrievalUnavailableError` through the adapter, which
  wraps every runner failure.

## Running it

From the repo root:

```bash
python3 scripts/shadow_retrieval_eval.py
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked \
  --package sklegal-retrieval pytest tests/test_shadow_comparison.py -q
```

The script prints the freeze hash, harness version, per-route macro metrics,
per-partition metrics, and leakage counts, and exits nonzero when leakage is
detected. The dataset is loaded through `load_frozen_dataset` only, so the
freeze and all 24 dataset invariants are enforced on every run. Any drift in
the freeze hash is a new dataset revision requiring a new manifest and fresh
review, per the SKL-S3-04A comparison-slice contract.
