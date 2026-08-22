# HANDOFF: SKL-S5-01C pilot workspace render and replay evidence

Card: `1181ba0f` (SKL-S5-01C), slice of SKL-S5-01 (`8d52de94`).
Branch: `swarm/1181ba0f`. Commit `3f58cbd` is the implementation and
evidence commit; this file is committed after it. The in-flight WIP
commit `dbadd1c` (module skeleton, `sklegal-retrieval` dependency
wiring for the API package, uv.lock) is part of this card's lineage and
was completed, not discarded. This file replaces the stale SKL-S2-05
handoff left in the worktree root by a sibling card.

## Files changed

- `services/api/src/sklegal_api/pilot_verification.py`: completed the
  recovered WIP module. Fixed the always-true stubbed replay
  comparison, excluded the wall-clock `generated_at` from the
  verification fingerprint, fixed the tension not-harmonized semantics
  (group membership equality, not unique predicate pairs; the healthy
  fixture legitimately repeats two predicates across two documents),
  fixed plan-vs-inventory coverage (the plan legitimately pins the
  legacy registry index outside the matter tree, so coverage is
  inventory-subset plus no dry-run `excluded_from_plan`), fixed the
  unbuildable suite construction (the consistency validator ran before
  the `object.__setattr__` patch; checks are now collected before the
  model is built), fixed `tension.key` to `tension_key`, and renamed
  the pydantic v1-style `json()` override to `evidence_json()`.
- `tests/support/pilot_corpus.py` (new): synthetic S2-05 corpus state
  built through the real `CorpusRegistryProjector`,
  `DeepCorpusReconciler`, and `BoundedCorpusHealthReader` from the pilot
  plan's pinned sources.
- `tests/test_pilot_verification.py` (new): 15 tests.
- `scripts/pilot_verification_suite.py` (new): assembles the full
  synthetic stack, runs the suite twice over identical pinned inputs,
  and writes the replay evidence artifact; exits 2 on any failed check.
- `docs/evidence/migration/SKL-S5-01C-PILOT-VERIFICATION-SUITE-2026-08-22.json`
  (new): machine-readable replay evidence, 46 checks, all passed.
- `docs/evidence/migration/SKL-S5-01C-COMPLETION-EVIDENCE-2026-08-22.md`
  (new): completion evidence.
- (From the WIP commit `dbadd1c`) `pyproject.toml`,
  `services/api/pyproject.toml`, `uv.lock`: `sklegal-retrieval`
  workspace dependency for the API package.
- `HANDOFF.md` (this file).

## Tests and exact results

From the worktree root with the pinned toolchain (`.tools/bin/uv`,
`UV_CACHE_DIR=$PWD/.tools/uv-cache`):

```text
uv run --locked --package sklegal-api pytest tests/test_pilot_verification.py -q
15 passed in 0.67s

uv run --locked --package sklegal-api pytest tests/test_pilot_verification.py \
  tests/test_workspace_pilot_projection.py tests/test_pilot_import.py \
  tests/test_pilot_dry_run.py tests/test_api_workspace.py -q
64 passed in 1.81s

uv run --locked --package sklegal-api pytest tests/test_corpus_registry.py \
  tests/test_corpus_health.py tests/test_corpus_reconciliation.py -q
59 passed in 0.57s

uv run --locked ruff format --check <new and changed files>
all formatted

uv run --locked ruff check <new and changed files>
All checks passed!

uv run --locked mypy <new and changed files>
16 findings, all the pre-existing import-untyped category for workspace
packages (same category the S5-01A evidence recorded); no other finding.

uv run --locked python scripts/check_secrets.py
no finding in any file added or changed by this card.

.venv/bin/python scripts/pilot_verification_suite.py --date 2026-08-22
run_id=fd023b87-902a-5437-afd5-e06a42c2877c; checks_total=46;
checks_failed=0; exit 0
```

## Acceptance criteria evidence

Card acceptance: render the imported pilot matter in the S4-02
workspace; verify corpus registry coverage via S2-05; produce the full
pilot verification suite replay evidence; the pilot matter is
understandable in the UI with tensions and provenance intact; no state
advances without approval.

- Render verification: the suite proves the S4-02 workspace aggregate
  matches the approved import plan: matter and event terminology
  (legacy `Problem`/`Incident` surface only as provenance aliases,
  legacy storage labels absent from the rendered UI dump, canonical
  types present), facts with predicate and value fidelity, timeline,
  communications, gaps, audit, and provenance pins.
- Tensions intact: every group unresolved, review-required, membership
  preserved, no harmonization; fail-closed test on a resolved
  (harmonized) tension plan.
- Provenance intact: pinned sources, version lineage with a single
  current review baseline and history retained, snapshot pin,
  freshness, missing-source gaps; fail-closed test on a stale render.
- No state advances: every rendered state is `pending_review` or
  `not_started`, every imported target carries both, rendered states
  equal the import store, the human approval is recorded on the batch,
  and the import is gated on the approved mapping decision; fail-closed
  test on a `dispatched` execution row.
- S2-05 coverage: registry entry present, source count equals the
  pinned corpus, reconciled-at recorded, deep reconciliation complete
  with one release and no discrepancies, bounded health available and
  consistent with the pinned count; fail-closed tests on count drift, a
  missing entry, and an incomplete reconciliation.
- Replay evidence: the artifact records the second suite run with
  `replays_deterministic: true` (run id, check set, import batch, and
  evidence fingerprint all match the first run while the generation
  times differ); fail-closed tests on changed pinned inputs and on a
  failing original.

## Known limitations

- Everything runs on the synthetic HammerTime fixture tree, the
  in-memory pilot import store, and the in-memory corpus registry. The
  live Liberty Auto pilot was not read; a live import stays gated on
  the pending S5-01A human mapping review. No HammerTime path was
  touched and no external action was taken.
- The corpus state proves the S2-05 wiring and coverage contract over
  the pinned sources, not a live reconciliation against the real
  HammerTime release.
- The suite verifies the workspace aggregate that the S4-02 routes and
  web shell render, including terminology checks over the rendered
  JSON; it does not itself produce HTML.
- No data migration: rollback is a Git revert of this card's commits.

## Boundaries honored

No push or remote access, no files changed outside the worktree, no
HammerTime path access, no external action, no secrets read or written,
no skcapstone board commands, ASCII hyphens only in all deliverables.
Board state stays with jarvis.
