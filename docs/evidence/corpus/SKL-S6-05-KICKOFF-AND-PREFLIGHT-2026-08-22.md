# SKL-S6-05 kickoff and live preflight evidence

Date: 2026-08-22

SKCapstone card: `0ad49216`

## Outcome

SKL-S6-05 is in progress under Jarvis. A typed, fail-closed release
qualification boundary now verifies immutable HammerTime references without
exposing promotion, rollback, or other write methods to SKLegal.

The live official drafting standards batch is not ready for dev promotion.
The exact normalized and decomposition artifacts are present, and the archived
rights record clears all 14 acquired sources for private personal research and
internal provenance-preserving corpus analysis. S6-05A created one immutable
candidate release manifest for the exact batch. Scoped retrieval, completed
deep health, secondary Qwen3.8 review, guarded dev promotion, and rollback
proof remain required. Runtime aliases were not changed.

## Files changed

- `packages/connectors/hammertime/src/sklegal_hammertime/release_qualification.py`
- `packages/connectors/hammertime/src/sklegal_hammertime/candidate_inspection.py`
- `packages/connectors/hammertime/src/sklegal_hammertime/__init__.py`
- `tests/support/hammertime_fixture.py`
- `tests/test_official_drafting_candidate_inspection.py`
- `tests/test_official_drafting_release_qualification.py`
- `docs/evidence/corpus/SKL-S6-05-KICKOFF-AND-PREFLIGHT-2026-08-22.md`

## Implemented qualification gates

- Exact candidate release manifest hash and target
- Manifest document counts and deterministic verification
- Required vector and graph verification flags
- Normalized artifact hashes separated from original source hashes
- Original source hashes verified against normalized frontmatter
- Decomposition hashes and exact normalized parent paths
- Required decomposition snapshot count and snapshot hash
- Source-rights status
- Issuing body, version, scope, source hash, and locator retrieval evidence
- Preserved conflicts and rejection of superseded sources presented as current
- Lexical, vector, and graph release pins
- Deep health and complete reconciliation evidence
- Pinned local Qwen3.8 secondary review evidence
- Current and previous alias agreement with no drift
- Guarded promotion receipt and exact rollback round-trip receipt

The qualifier returns a typed blocked report for any missing or conflicting
evidence. It performs no HammerTime mutation.

## Tests and exact results

Command:

```text
.tools/bin/uv run --locked pytest -q tests/test_official_drafting_release_qualification.py tests/test_hammertime_adapter.py tests/test_official_drafting_style_profiles.py tests/test_corpus_reconciliation.py tests/test_corpus_health.py
```

Result:

```text
115 passed, 7 subtests passed in 1.60s
```

Commands:

```text
.tools/bin/uv run --locked ruff check packages/connectors/hammertime/src/sklegal_hammertime/release_qualification.py packages/connectors/hammertime/src/sklegal_hammertime/__init__.py tests/test_official_drafting_release_qualification.py
.tools/bin/uv run --locked ruff format --check packages/connectors/hammertime/src/sklegal_hammertime/release_qualification.py packages/connectors/hammertime/src/sklegal_hammertime/__init__.py tests/test_official_drafting_release_qualification.py
```

Results:

```text
All checks passed!
3 files already formatted
```

The focused test module covers missing artifacts, wrong source version,
normalized source metadata mismatch, missing required manifest verification,
stale projection, scope collapse, a superseded source presented as current,
source rights quarantine, failed secondary review, alias drift, promotion
failure, rollback failure, and the absence of a mutation method.

Command:

```text
.tools/bin/uv run --locked mypy packages/domain packages/connectors/hammertime
```

Result:

```text
Success: no issues found in 22 source files
```

## Live acceptance evidence

1. The S6-04 profile identifies 14 sources. All 14 normalized paths exist, all
   14 have one exact filename-matched decomposition, and their normalized byte
   hashes were recomputed separately from the original source hashes.
2. The S6-03 completion evidence reports 14 normalized sources, 4,279 chunks,
   4,279 verified vector points, and 14 source-provenance graph nodes. Its SHA256
   is `91b3a6958f8f0d7c6e80a3d9b80fbebef6c14f85c90691fb0701d51952571179`.
3. The archived source-rights review lists all 14 acquired source IDs in
   `cleared_for_internal_ingest`, with no rights quarantine. Its SHA256 is
   `e83ac258276fb022502f8ad83bbffa90af5958de9a4a4ef6e1ba529574db155e`.
4. The immutable dev candidate is addressable as release
   `dev-20260822-official-drafting-standards-candidate-1`. Its manifest SHA256
   is `2ee914c26cf93e61138416c84b25d8e18dd5ee9ab7e1921b204d9b9176bbf752`
   and it pins exactly 14 normalized artifacts and 14 decompositions.
5. The dev alias snapshot SHA256 is
   `59381d410c53f9a33f86739b39661ef468a35e4cd15140369c533a4eb9123984`.
   It remains at current release
   `dev-20260715-secured-transactions-practitioner-206` with previous release
   `dev-20260706-coupon-bonds-oklahoma-public-finance`.
6. A fresh pre-apply dry run reproduced the candidate manifest hash. Apply
   created only the candidate manifest. The alias, processing-state, and
   decomposed-state hashes remained unchanged. The HammerTime boundary suite
   passed 22 tests and the SKLegal qualifier suite passed 14 tests.
7. Shallow and deep HammerTime validators were started against the exact
   candidate. Both remained blocked in host filesystem waits for more than 75
   seconds and produced no validation result. Only those validator processes
   were terminated. This is recorded as incomplete infrastructure evidence,
   not a passed gate and not an assertion failure.
8. A later shallow-validation retry used a 50-second bounded command. It
   produced no validator output and ended with timeout exit 124. The candidate
   manifest and runtime alias remained unchanged. This retry is also incomplete
   infrastructure evidence and does not satisfy the validation gate.
9. The secondary-review dry run selected all 14 candidate sources. Formal
   review was not started because neither `HAMMERTIME_REVIEW_API_URLS` nor
   `HAMMERTIME_REVIEW_API_URL` is configured. The primary Qwen3.8 route passed
   its runtime probe, but the corpus SOP prohibits silently using that route as
   the formal secondary reviewer. No review report was represented as passing.
10. The bounded SKLegal candidate inspector followed only the immutable
    manifest paths and completed against the live HammerTime root in 0.25
    seconds. It reconciled 14 unique sources, 14 unique decompositions, and 28
    exact artifact hashes with no findings. It did not list the repository,
    inspect Inbox, read runtime aliases, or claim vector or graph health.

## Bounded candidate inspection verification

Command scope:

```text
tests/test_official_drafting_candidate_inspection.py
tests/test_official_drafting_release_qualification.py
tests/test_hammertime_adapter.py
```

Results:

```text
82 passed, 7 subtests passed in 0.71s
All Ruff checks passed
3 files already formatted
Success: no issues found in 25 source files
```

The expanded adapter, qualification, reconciliation, and health selection
passed `121 passed, 7 subtests passed in 0.96s`. The style-profile validator
also passed with its HammerTime root explicitly bound to the live sibling
repository because the temporary task worktree has no sibling corpus checkout.

Live content-free result:

```text
decomposition_count: 14
finding_codes: none
release_id: dev-20260822-official-drafting-standards-candidate-1
release_manifest_sha256: 2ee914c26cf93e61138416c84b25d8e18dd5ee9ab7e1921b204d9b9176bbf752
source_count: 14
status: qualified
verified_artifact_count: 28
```

## Known limitations and next required evidence

- The focused domain and HammerTime connector type check passes. The
  workspace-wide type check currently reports 11 unrelated errors in retrieval,
  worker, and model-gateway files that are being changed by other active cards.
- Run batch-scoped retrieval checks against that exact release, including a
  preserved-conflict query and a historical-source currentness rejection.
- Complete shallow and deep corpus validation when the OneDrive-backed corpus
  path is responsive. Run a secondary local Qwen3.8 challenge against the exact
  candidate, preserving prompt, output, schema, and model evidence.
- Configure and qualify a review-specific Qwen3.8 endpoint before the formal
  secondary-review command. Do not use the primary fallback without an explicit
  operator decision recorded in the task evidence.
- Use HammerTime's guarded promotion command for dev only after the candidate
  passes, then capture current and previous alias pins.
- Test rollback and re-promotion through HammerTime's own release tooling and
  retain both receipts. SKLegal remains read-only throughout.

## Rollback evidence

One unreferenced immutable candidate manifest was created. Because the dev
alias snapshot is unchanged, rejection rollback may remove only that exact
unreferenced manifest under S6-05 evidence. Code rollback is removal of the new
qualification module and test plus the package export entries. Once an alias
references the candidate, only HammerTime's guarded rollback command may change
runtime state.
