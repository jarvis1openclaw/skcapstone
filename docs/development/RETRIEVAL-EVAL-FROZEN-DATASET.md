# Frozen retrieval evaluation dataset

Task: `SKL-S3-04A` (slice of `SKL-S3-04`)  
Card: `e1bc4552`  
Amendment: `AMENDMENT-SKL-S2-10`  
Dataset schema: `sklegal-retrieval-eval-frozen/v1`, version `1.0.0`

This document is the paired narrative for
`evals/retrieval/frozen-v1/` and the loader contract in
`packages/retrieval/src/sklegal_retrieval/evaluation_dataset.py`.

## Purpose

SKL-S3-04 must measure retrieval before the custom legal embedding is
accepted. This slice builds only the frozen query set, the synthetic
fixture-only corpus documents, and the graded relevance judgments. No model
comparison runs here. The later comparison slices (custom model versus base
BGE-M3 in shadow projection generations) consume this dataset exactly as
frozen; they must not regenerate, extend, or re-grade it.

The dataset covers the ten case classes required by the amendment and the
parent task:

1. exact citation
2. paraphrase
3. near-neighbor distinction
4. jurisdiction mismatch
5. superseded authority
6. evidence versus authority
7. OCR noise
8. no answer
9. privilege partition
10. prompt injection

## Layout

```text
evals/retrieval/frozen-v1/
  documents.json   fixture corpus documents, sorted by document_id
  queries.json     frozen queries, sorted by query_id
  judgments.json   graded relevance judgments, sorted by (query_id, document_id)
  manifest.json    hash-freeze manifest
```

`scripts/freeze_retrieval_eval.py` verifies the frozen state by default and
can re-emit `manifest.json` with `--emit` after a deliberate, reviewed
dataset revision.

## Fixture-only content rules

Every document is synthetic fixture content written for this dataset. The
two synthetic Tenants are pinned in the manifest:

- `11111111-1111-4111-8111-111111111111` (two Matters)
- `22222222-2222-4222-8222-222222222222` (two Matters)

No HammerTime path, release, artifact, or protected matter content appears
anywhere in the dataset. Invariant L22 rejects any record that references
`hammertime` or `inbox/`, any classification outside the `fixture-*` space,
and any Tenant outside the pinned synthetic set. Citations such as
`815 ILCS 5/2L` inside documents are synthetic fixture text shaped like a
citation for retrieval stress; they are not assertions about real law and
must never be cited as Authority outside this fixture corpus.

## Record shapes

`EvalDocument` pins Tenant, Matter, document kind (Authority or Evidence
Item), jurisdiction, classification, privilege state, OCR derivation,
authority kind and status, supersession target, and any embedded injection
payload. Superseded authority must pin its superseding document, which must
be current authority in the same jurisdiction.

`EvalQuery` pins Tenant, Matter, case class, expected answer state
(`answerable`, `no_answer`, or `blocked`), privilege authorization,
controlling jurisdiction for jurisdiction-mismatch cases, the exact locator
for exact-citation cases, and the injection origin
(`document_borne` or `query_borne`) for injection cases. The exact locator
must appear verbatim in the query text.

`RelevanceJudgment` grades one query and document pair from 0 to 3:

- `0` not relevant
- `1` background only
- `2` relevant support
- `3` directly responsive or controlling

Grades of 2 or higher count as relevant for Recall-style metrics in the
comparison slices. Every judgment carries at least one machine-checkable
basis from `JudgmentBasis` and a human-readable rationale.

Answer-state semantics: `answerable` queries must have at least one
relevant judgment. `no_answer` queries must carry only explicit
`negative_no_answer` judgments, at least two of them. `blocked` queries
(privilege denied or injection attempt) must carry no relevant judgment and
must grade any privileged document 0 with the `privilege_blocked` basis.

## Hash freeze

The manifest pins the SHA-256 of each record file's exact bytes plus a
`freeze_sha256` that binds the schema, dataset version, and the three file
hashes. The loader recomputes all four hashes and rejects any mismatch, any
missing file, and any schema violation before the invariants run. A changed
byte anywhere in the frozen records invalidates the dataset until a new
manifest is deliberately emitted and reviewed.

## Leakage invariants

`dataset_invariant_violations` returns every violation deterministically;
an empty result is the gate. The comparison slices and the test suite treat
any non-empty result as a hard failure.

| Code | Rule |
| ---- | ---- |
| L01 | every amendment case class has at least one query |
| L02 | every query has at least one judgment |
| L03 | no judgment crosses Tenant or Matter partitions |
| L04 | privileged documents never earn grade 1 or higher without authorization |
| L05 | every privileged document is relevant inside its authorized lane |
| L06 | unauthorized privilege judgments are grade 0 with the block basis |
| L07 | no-answer queries carry only explicit negatives, at least two |
| L08 | answerable queries have at least one relevant judgment |
| L09 | paraphrase queries share no word run longer than 3 with relevant documents |
| L10 | exact citation queries resolve to exactly one controlling locator hit |
| L11 | near-neighbor distractors overlap the query substantially but rank below the relevant document |
| L12 | superseded authority stays background and its current successor answers |
| L13 | jurisdiction mismatch distractors are foreign and stay background |
| L14 | evidence-versus-authority queries mark the non-source lane explicitly |
| L15 | the evidence-versus-authority class covers both fact-seeking and law-seeking directions |
| L16 | OCR-noise hits are OCR-derived, noise-tolerant, and not query copies |
| L17 | document-borne injection hits are graded as untrusted payload data |
| L18 | no corpus injection payload reaches any query text |
| L19 | query-borne injection never escalates privilege |
| L20 | distractor bases never carry grade 2 or higher |
| L21 | grade 3 judgments carry a directly responsive basis |
| L22 | fixture hygiene: synthetic Tenants, fixture classifications, no forbidden paths |
| L23 | the no-answer basis belongs only to no-answer queries |
| L24 | blocked queries carry no relevant judgment and block privileged documents |

## Provenance and lanes

The dataset keeps the amendment's separation of lanes visible per record:
Authority versus Evidence Item, current versus superseded Authority,
controlling versus foreign jurisdiction, public versus privileged, and
trusted versus injected content. Similarity or overlap never establishes
Authority applicability; the judgments state applicability explicitly and
the invariants keep the lanes distinct.

## Comparison-slice contract

Later slices must:

- load the dataset through `load_frozen_dataset` only, so the freeze and
  invariants are always enforced;
- evaluate per Tenant and Matter partition, never pooling partitions;
- report Recall@k, nDCG, MRR, citation accuracy, latency, and leakage
  results against this exact freeze hash;
- treat any drift in the freeze hash as a new dataset revision requiring a
  new manifest and fresh review.

Rollback for this slice is a Git revert; the dataset is files-only and no
external state exists.
