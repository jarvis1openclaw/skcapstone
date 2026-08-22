# SKL-S3-04A handoff: frozen retrieval evaluation dataset

Card: `e1bc4552` (SKL-S3-04A, slice of SKL-S3-04 / card `4c4ca6b0`)  
Branch: `swarm/e1bc4552`  
Amendment: `AMENDMENT-SKL-S2-10` (owner approved 2026-08-21)  
Date: 2026-08-22

## What this slice delivers

The frozen query set, synthetic fixture-only corpus documents, graded
relevance judgments, and hash-freeze manifest for retrieval evaluation.
Leakage invariants are machine-checked. No model comparison runs in this
slice, per the card scope.

## Files changed

- `packages/retrieval/src/sklegal_retrieval/evaluation_dataset.py` (new):
  typed dataset values (`EvalDocument`, `EvalQuery`, `RelevanceJudgment`,
  `DatasetManifest`, `FrozenEvaluationDataset`), case-class and basis
  enums, deterministic freeze hash, 24 leakage invariants (L01 to L24),
  and `load_frozen_dataset` with schema, hash, freeze, and invariant
  verification.
- `packages/retrieval/src/sklegal_retrieval/__init__.py`: export the new
  public dataset API.
- `evals/retrieval/frozen-v1/documents.json` (new): 14 fixture documents
  across two synthetic Tenants and four Matters.
- `evals/retrieval/frozen-v1/queries.json` (new): 16 frozen queries
  covering all ten amendment case classes.
- `evals/retrieval/frozen-v1/judgments.json` (new): 33 graded judgments
  with machine-checkable bases.
- `evals/retrieval/frozen-v1/manifest.json` (new): the hash freeze.
- `scripts/freeze_retrieval_eval.py` (new): verify-by-default CLI;
  `--emit` writes a new manifest for a reviewed dataset revision.
- `tests/test_retrieval_eval_dataset.py` (new): 37 tests.
- `docs/development/RETRIEVAL-EVAL-FROZEN-DATASET.md` (new): paired
  document with grade semantics, the invariant table, and the
  comparison-slice contract.
- `docs/evidence/retrieval/SKL-S3-04A-COMPLETION-EVIDENCE-2026-08-22.md`
  (new): completion evidence.
- `evals/README.md`: point at the frozen dataset.

Frozen state:

```text
freeze_sha256:    c4f829781badfc0139dda420c539ee30d4d43ac7c189aa55114af385ed02d189
documents_sha256: 86c206720cadfd6c7420b0c60695cc3e9b4de6e41d39aa8b6de355a0e6cf9880
queries_sha256:   dbcd47fbd4f4dc7b94d7a33af6794255dda7fe9b7688c86b13b4e56dba2a1dcb
judgments_sha256: 3eef5449e9dba9ce87663d8745f1f81b59b5c15f390a29f7ec212055c406fb47
```

## Tests and exact results

From the worktree root with
`UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv`:

```text
uv run --locked --package sklegal-retrieval pytest tests/test_retrieval_eval_dataset.py -q
37 passed, 8 subtests passed in 0.17s

uv run --locked --package sklegal-retrieval pytest tests/test_retrieval_eval_dataset.py \
  tests/test_retrieval_models.py tests/test_retrieval_orchestrator.py \
  tests/test_retrieval_trace.py tests/test_retrieval_partition_contract.py \
  tests/test_retrieval_registry.py tests/test_corpus_registry.py -q
179 passed, 28 subtests passed in 0.35s

uv run --locked --package sklegal-retrieval ruff check \
  packages/retrieval/src/sklegal_retrieval/evaluation_dataset.py \
  packages/retrieval/src/sklegal_retrieval/__init__.py \
  tests/test_retrieval_eval_dataset.py scripts/freeze_retrieval_eval.py
All checks passed!

uv run --locked --package sklegal-retrieval ruff format --check \
  packages/retrieval/src/sklegal_retrieval/evaluation_dataset.py \
  packages/retrieval/src/sklegal_retrieval/__init__.py \
  tests/test_retrieval_eval_dataset.py scripts/freeze_retrieval_eval.py
3 files already formatted (init reported already formatted)

python3 scripts/freeze_retrieval_eval.py
frozen dataset verified: 14 documents, 16 queries, 33 judgments,
freeze c4f829781badfc01...

rg '\x{2013}|\x{2014}' over all new files
no findings

git diff --check over all new files
passed
```

Note on tooling: the worktree did not carry `.tools/bin/uv`, so the pinned
uv 0.12.5 archive was provisioned from `requirements/bootstrap.lock` with
its checksum verified, exactly as `scripts/bootstrap.sh` specifies. The
`.tools/` directory is not committed (repository policy keeps it local).

## Acceptance criteria evidence

The card records no free-text acceptance criteria, so the slice is
verified against its description and the parent task SKL-S3-04 dataset
requirements:

- All ten amendment case classes are present with dedicated queries,
  judgments, and invariants; proven by
  `test_every_amendment_case_class_has_a_query` plus per-class invariants
  (L07 to L19) and their mutation tests.
- Dataset leakage tests exist and pass: cross-partition leakage (L03),
  privilege escalation (L04, L06, L19, L24), payload escape into query
  text (L18), distractor promotion (L12, L13, L20), missing support
  (L08), and fixture hygiene (L22) each have a dedicated test proving the
  violation is detected.
- The dataset is hash-frozen: per-file SHA-256 plus a freeze hash binding
  schema, version, and file hashes; tampering with any record byte or the
  manifest is rejected by the loader.
- No model comparison ran: no embedding, gateway, or provider is
  referenced anywhere in the slice; metric computation stays in the
  comparison slices.

## Known limitations

- The dataset is intentionally small (16 queries). It is a leakage and
  case-class harness, not a statistical power study; the comparison
  slices may propose a larger second freeze through their own review.
- Near-neighbor thresholds (absolute floor 4 shared terms, 6/10 ratio
  versus the relevant document) are dataset-tuned constants documented in
  the module; a larger dataset may need recalibration with a new freeze.
- Documents are synthetic. Citation-shaped strings such as
  `815 ILCS 5/2L` are fixture text, not statements about real law, and
  must never be treated as Authority outside this corpus.
- Loader invariants run over materialized records in memory; no database
  or projection backend is involved in this slice.

## Boundaries respected

No push, pull, or remote access. No file outside this worktree was
modified. No HammerTime path was read or touched. No email, filing,
service, or external action. No secret or credential file was read. No
skcapstone board command was run. ASCII hyphens only. Contradictions and
provenance are preserved in the dataset lanes (current versus superseded,
controlling versus foreign, evidence versus authority, privileged versus
public, trusted versus injected).

## Rollback

Git revert of this slice's commits. The dataset is files-only; no
database, migration, external state, or board mutation occurred.
