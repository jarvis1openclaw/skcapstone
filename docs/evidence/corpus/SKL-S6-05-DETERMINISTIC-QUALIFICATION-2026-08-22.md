# SKL-S6-05 deterministic qualification evidence

Date: 2026-08-22

SKCapstone card: `0ad49216`

Worker: `qwen-skl-s6-05`

## Outcome

The deterministic, read-only qualification work for the recorded candidate
release completed against the live HammerTime root. The candidate remains
addressable by immutable references and is now proven source-complete,
rights-cleared, conflict-preserving, and artifact-pinned at three separate
typed gates:

1. Bounded candidate inspection: qualified, 14 sources, 14 decompositions,
   28 verified artifact hashes, zero findings.
2. Batch-scoped lexical retrieval over the exact pinned chunks: 5 typed
   queries, zero findings, including two preserved-conflict co-retrievals and
   the historical-source currentness rejection.
3. Full fail-closed release qualification: blocked with exactly six typed
   findings, each naming a genuinely pending gate (snapshot hash, deep
   health, secondary review, pre-promotion alias state, promotion receipt,
   rollback receipt). No artifact, version, scope, rights, or supersession
   defect was found.

Nothing was promoted, rolled back, alias-changed, deployed, or sent
outbound. The HammerTime Inbox was not accessed by this worker. The
runtime alias snapshot hash is unchanged from the recorded preflight value.

## Files changed

- `packages/connectors/hammertime/src/sklegal_hammertime/scoped_retrieval.py`
  (new)
- `packages/connectors/hammertime/src/sklegal_hammertime/release_qualification.py`
  (new finding code, precise snapshot-seal finding detail)
- `packages/connectors/hammertime/src/sklegal_hammertime/__init__.py`
  (exports)
- `tests/test_official_drafting_scoped_retrieval.py` (new)
- `scripts/skl_s6_05_release_qualification_report.py` (new)
- `docs/evidence/corpus/SKL-S6-05-SCOPED-RETRIEVAL-EVIDENCE-2026-08-22.json`
  (new typed query and currentness evidence)
- `docs/evidence/corpus/SKL-S6-05-SECONDARY-REVIEW-CHALLENGE-2026-08-22.json`
  (new typed challenge prompt and output schema)
- `docs/evidence/corpus/SKL-S6-05-SECONDARY-REVIEW-VERDICT-2026-08-22.json`
  (new not-invoked verdict record)
- `docs/evidence/corpus/SKL-S6-05-QUALIFICATION-REPORT-2026-08-22.json`
  (new typed report)
- `docs/evidence/corpus/SKL-S6-05-DETERMINISTIC-QUALIFICATION-2026-08-22.md`

## Recorded pins re-verified live

All pins were recomputed against the live files and matched the values
recorded in the kickoff and S6-05A evidence:

- Candidate manifest `dev-20260822-official-drafting-standards-candidate-1`
  SHA256 `2ee914c26cf93e61138416c84b25d8e18dd5ee9ab7e1921b204d9b9176bbf752`
- Runtime alias snapshot SHA256
  `59381d410c53f9a33f86739b39661ef468a35e4cd15140369c533a4eb9123984`,
  still at current `dev-20260715-secured-transactions-practitioner-206` with
  previous `dev-20260706-coupon-bonds-oklahoma-public-finance`
- Source rights review SHA256
  `e83ac258276fb022502f8ad83bbffa90af5958de9a4a4ef6e1ba529574db155e`
  (all 14 source IDs in `cleared_for_internal_ingest`, empty
  `rights_quarantine`, WH-CORR-HIST human decision of 2026-08-22 recorded)
- S6-03 completion evidence SHA256
  `91b3a6958f8f0d7c6e80a3d9b80fbebef6c14f85c90691fb0701d51952571179`
  (14 normalized, 4,279 chunks, 4,279 verified vector points, 14
  source-provenance graph nodes)
- S6-04 style profile SHA256
  `9651ce6bdabf18cc27a39ff2bed87309b179b72fe37053c852f43cd288627959`
- Core principles SHA256
  `66db5cb1eac1cbd9b958f96d07e29ebf7711e2902c56ddee87dd2d92852c26ed`

## Typed scoped retrieval evidence

The five typed queries were evaluated only over the 14 hash-pinned
decompositions named by the candidate manifest:

- `typography-settings-conflict` (SPR-003): DOE-TECH-STYLE-2015 and
  CA7-TYPOGRAPHY both retrieved for the shared settings query. Preserved
  contradiction "DOE settings and CA7 settings are not interchangeable" is
  not collapsed.
- `capitalization-subject-line-conflict` (SPR-004): USCG-CORR-2024
  retrieved with its USCG-scoped subject-line capital letters rule.
- `punctuation-baseline-conflict` (SPR-006): DOI-CORR-2014 and GPO-2016
  co-retrieved. The preserved GPO-baseline contradiction is not collapsed.
- `historical-currentness-rejection`: WH-CORR-HIST retrieved and presented
  as `presented_as_current=false` (currentness "historical; never current").
- `federal-register-drafting-pair`: both OFR sources retrieved for the
  Federal Register drafting scope.

Every hit carries its source ID, issuing body, version, scope, sealed
source SHA256, currentness decision, and a chunk locator inside the pinned
decomposition. The full hit set is in the typed report.

## Typed qualification findings (blocked, pending gates)

- `missing_artifact` / `dev-20260822-official-drafting-standards-candidate-1`:
  "release snapshot lacks the required snapshot hash". The recorded
  candidate was built before the deterministic builder fix on main
  (commits `5bc3971` and `3c3b25f`, merged 2026-08-22) that writes
  `decomposed_snapshot.snapshot_hash`. The candidate manifest itself is
  otherwise complete and was not rebuilt, per the instruction to use the
  exact recorded candidate.
- `stale_projection` / candidate: deep corpus health has not passed. The
  shallow and deep validators previously blocked in host filesystem waits on
  the OneDrive-backed corpus path. The projection bindings are pinned
  (`hammertime-v3-dev`, `hammertime-v4-dev`) and reconciliation is complete;
  the gate is the health run, not the binding.
- `secondary_review_failed` / candidate: the review-dedicated local Qwen3.8
  endpoint is not configured (`HAMMERTIME_REVIEW_API_URLS` and
  `HAMMERTIME_REVIEW_API_URL` are unset). The typed challenge prompt and
  output schema are recorded and hashed; the verdict record marks the
  review `not_invoked`, `passed=false`.
- `alias_drift` / `dev`: the current dev alias still points at
  `dev-20260715-secured-transactions-practitioner-206`. This is the
  expected pre-promotion state; promotion was prohibited for this work.
- `promotion_failure` / `dev`: guarded promotion receipt pending.
- `rollback_failure` / `dev`: rollback receipt pending.

Absent findings, deliberately preserved: no `wrong_source_version`, no
`scope_collapse`, no `superseded_as_current`, no `source_rights_blocked`.

## Tests and exact results

Command:

```text
python -m pytest -q tests/test_official_drafting_scoped_retrieval.py tests/test_official_drafting_release_qualification.py tests/test_official_drafting_candidate_inspection.py tests/test_hammertime_adapter.py tests/test_official_drafting_style_profiles.py tests/test_corpus_reconciliation.py tests/test_corpus_health.py
```

Result:

```text
132 passed, 7 subtests passed in 1.77s
```

The style-profile validator binds its HammerTime root to the sibling
checkout. In this isolated worktree that sibling was symlinked to the live
root (`/tmp/swarm/hammerTime` ->
`/mnt/cloud/onedrive/projects/DAVE-AI/hammerTime`), matching the preflight
method of explicitly binding the live sibling repository.

Commands:

```text
ruff check packages/connectors/hammertime/src/sklegal_hammertime/ tests/test_official_drafting_scoped_retrieval.py scripts/skl_s6_05_release_qualification_report.py
ruff format --check packages/connectors/hammertime/src/sklegal_hammertime/scoped_retrieval.py packages/connectors/hammertime/src/sklegal_hammertime/release_qualification.py packages/connectors/hammertime/src/sklegal_hammertime/__init__.py tests/test_official_drafting_scoped_retrieval.py scripts/skl_s6_05_release_qualification_report.py
```

Results:

```text
All checks passed!
5 files already formatted
```

Command:

```text
mypy packages/domain packages/connectors/hammertime
```

Result:

```text
packages/domain/src/sklegal_domain/claim_grounded.py:85: error: Unused "type: ignore" comment  [unused-ignore]
packages/domain/src/sklegal_domain/claim_grounded.py:300: error: Incompatible types in assignment (expression has type "SentenceGrounding | None", variable has type "SentenceGrounding")  [assignment]
Found 2 errors in 1 file (checked 27 source files)
```

Both errors are pre-existing on `origin/main` (file added by another active
card in commit `c183201`, merged 2026-08-22 before this worker started).
They reproduce identically on the untouched main checkout and in files this
worker did not touch. The new and changed files in this work are clean.

## Known limitations and blockers

- The recorded candidate manifest lacks
  `decomposed_snapshot.snapshot_hash`, so the full qualification gate
  reports `missing_artifact` until either the candidate is rebuilt by the
  updated deterministic builder or the gate is waived under card evidence.
  This worker did not rebuild, per the instruction to use the exact
  recorded candidate.
- Shallow and deep corpus validation remain incomplete infrastructure
  evidence (OneDrive-backed corpus path host waits), as recorded in the
  kickoff.
- The formal secondary Qwen3.8 review is recorded, typed, and hashed but
  not invoked. A review-dedicated endpoint must be configured and
  qualified before the formal run.
- Guarded dev promotion and the rollback/re-promotion receipts are still
  HammerTime-owned mutations and were explicitly out of scope for this
  work.
- Main moved ahead of this worktree after it was cut (commits `5bc3971`,
  `3c3b25f`, `10d9308`, `899b502`, `2c43b8e`): the SKLegal-side
  candidate release builder and the S3-10A SKGateway chiap01
  qualification. They do not change the recorded candidate or this
  worker's files; the only shared file is the package `__init__.py`
  exports, which both sides only extend.

## Rollback evidence

No HammerTime state changed in this work: the candidate manifest,
runtime aliases, processing state, and decomposed state were only read.
Code rollback is removal of `scoped_retrieval.py`, the new test module,
the new script, the two enum/detail changes in
`release_qualification.py`, and the package export additions. The evidence
JSON and report files are append-only records and may be kept or removed
with the card evidence.

## Report reproduction

```text
python scripts/skl_s6_05_release_qualification_report.py \
  --root /mnt/cloud/onedrive/projects/DAVE-AI/hammerTime \
  --evidence docs/evidence/corpus/SKL-S6-05-SCOPED-RETRIEVAL-EVIDENCE-2026-08-22.json \
  --profile /mnt/cloud/onedrive/projects/DAVE-AI/sklegal/config/drafting_styles/official-drafting-style-profiles.json \
  --rights /mnt/cloud/onedrive/hammertime-Ingested/processed-inbox-archive/2026-08-21/2026-08-21-official-government-style-manuals/completed-inbox-sources/_imports/2026-08-21-official-government-style-manuals/source-root/manifests/rights-review.json \
  --completion /mnt/cloud/onedrive/projects/DAVE-AI/hammerTime/json/intake-manifests/2026-08-21-official-government-style-manuals-completion-evidence.json \
  --core-principles /mnt/cloud/onedrive/projects/DAVE-AI/sklegal/docs/drafting-styles/OFFICIAL-DRAFTING-CORE-PRINCIPLES.md \
  --review-challenge docs/evidence/corpus/SKL-S6-05-SECONDARY-REVIEW-CHALLENGE-2026-08-22.json \
  --review-verdict docs/evidence/corpus/SKL-S6-05-SECONDARY-REVIEW-VERDICT-2026-08-22.json \
  --out docs/evidence/corpus/SKL-S6-05-QUALIFICATION-REPORT-2026-08-22.json \
  --now 2026-08-22T21:00:00+00:00
```

Output:

```text
candidate_inspection: qualified sources=14 decompositions=14 verified=28
scoped_retrieval: queries=5 findings=0
full_qualification: blocked findings=['missing_artifact', 'stale_projection', 'secondary_review_failed', 'alias_drift', 'promotion_failure', 'rollback_failure']
report: docs/evidence/corpus/SKL-S6-05-QUALIFICATION-REPORT-2026-08-22.json
```
