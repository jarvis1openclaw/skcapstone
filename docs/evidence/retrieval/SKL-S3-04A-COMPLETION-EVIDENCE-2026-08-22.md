# SKL-S3-04A completion evidence

Date: 2026-08-22

Board card: `e1bc4552` (slice of `SKL-S3-04`, card `4c4ca6b0`)  
Amendment: `AMENDMENT-SKL-S2-10` (owner approved 2026-08-21)

## Delivered

- `packages/retrieval/src/sklegal_retrieval/evaluation_dataset.py`: typed
  dataset values (`EvalDocument`, `EvalQuery`, `RelevanceJudgment`,
  `DatasetManifest`, `FrozenEvaluationDataset`), the deterministic freeze
  hash, the 24 leakage invariants (L01 through L24), and
  `load_frozen_dataset`, which verifies schema, file hashes, freeze hash,
  and invariants before returning data.
- `evals/retrieval/frozen-v1/`: the frozen fixture-only dataset. 14
  documents, 16 queries, 33 graded judgments covering all ten required
  case classes across two synthetic Tenants and four Matters, plus the
  hash-freeze `manifest.json`.
- `scripts/freeze_retrieval_eval.py`: verify-by-default CLI over the frozen
  dataset with an explicit reviewed `--emit` mode for manifest revisions.
- `docs/development/RETRIEVAL-EVAL-FROZEN-DATASET.md`: the paired document
  with grade semantics, invariant table, and the comparison-slice
  contract.

Frozen state:

```text
freeze_sha256: c4f829781badfc0139dda420c539ee30d4d43ac7c189aa55114af385ed02d189
documents_sha256: 86c206720cadfd6c7420b0c60695cc3e9b4de6e41d39aa8b6de355a0e6cf9880
queries_sha256: dbcd47fbd4f4dc7b94d7a33af6794255dda7fe9b7688c86b13b4e56dba2a1dcb
judgments_sha256: 3eef5449e9dba9ce87663d8745f1f81b59b5c15f390a29f7ec212055c406fb47
```

## Verification

From the worktree root with
`UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv`:

```text
uv run --locked --package sklegal-retrieval pytest tests/test_retrieval_eval_dataset.py -q
Result: 37 passed, 8 subtests passed in 0.17s

uv run --locked --package sklegal-retrieval pytest tests/test_retrieval_eval_dataset.py \
  tests/test_retrieval_models.py tests/test_retrieval_orchestrator.py \
  tests/test_retrieval_trace.py tests/test_retrieval_partition_contract.py \
  tests/test_retrieval_registry.py tests/test_corpus_registry.py -q
Result: 179 passed, 28 subtests passed in 0.35s

uv run --locked --package sklegal-retrieval ruff check \
  packages/retrieval/src/sklegal_retrieval/evaluation_dataset.py \
  tests/test_retrieval_eval_dataset.py scripts/freeze_retrieval_eval.py
Result: All checks passed!

uv run --locked --package sklegal-retrieval ruff format --check \
  packages/retrieval/src/sklegal_retrieval/evaluation_dataset.py \
  tests/test_retrieval_eval_dataset.py scripts/freeze_retrieval_eval.py
Result: 3 files already formatted

python3 scripts/freeze_retrieval_eval.py
Result: frozen dataset verified: 14 documents, 16 queries, 33 judgments,
freeze c4f829781badfc01...

rg -n '\x{2013}|\x{2014}' <new files>
Result: no findings.

git diff --check -- <new files>
Result: passed.
```

## Acceptance criteria evidence

Frozen query set and relevance judgments per the amendment case classes:

- All ten case classes are present and asserted by
  `test_every_amendment_case_class_has_a_query`; the invariant engine
  independently rejects a missing class (L01).
- Exact citation, paraphrase, near-neighbor distinction, jurisdiction
  mismatch, superseded authority, evidence versus authority, OCR noise, no
  answer, privilege partition, and prompt injection each have fixture
  queries with graded judgments and dedicated invariants (L07 through
  L19).

Hash-frozen with leakage tests:

- The manifest pins per-file SHA-256 hashes plus a freeze hash binding
  schema, version, and file hashes.
  `test_every_pinned_file_hash_matches_its_committed_bytes`,
  `test_freeze_hash_binds_schema_version_and_file_hashes`, and
  `test_freeze_hash_moves_when_any_input_moves` verify the freeze, and
  `test_loader_rejects_a_mutated_judgment_byte` and
  `test_loader_rejects_a_manifest_hash_forgery` prove the loader rejects
  tampering.
- Cross-partition leakage, privilege escalation, payload escape into query
  text, superseded and jurisdiction distractor promotion, and unsupported
  answerable queries are each proven detectable by a dedicated test.

No model comparison in this slice:

- No embedding model, gateway, provider, or projection backend is
  referenced by the module, script, dataset, or tests. Metric
  computation (Recall@k, nDCG, MRR) is deferred to the comparison slices
  per the parent task.

## Limitations and rollback

The dataset is intentionally small and synthetic; it is a leakage and
case-class harness, not a statistical power study, and the comparison
slices may propose a larger second freeze through their own review. The
near-neighbor overlap thresholds (absolute floor 4, ratio 6/10) are
dataset-tuned constants documented in the module. Rollback is a Git revert
of this card's commits; the dataset is files-only, with no database,
HammerTime path, external action, secret, or board mutation involved.
