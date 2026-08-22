# SKL-S5-01C completion evidence

Date: 2026-08-22

Board card: `1181ba0f` (SKL-S5-01C)

Parent: SKL-S5-01 (`8d52de94`), pilot TDD
`docs/architecture/LIBERTY-AUTO-PILOT-TDD.md`. Slice of the S5-01B
approved import (card `59189e97`); depends on the S5-01A dry run
(`f5ed9d24` ancestry) and the S4-02 workspace (`e5ff5976` ancestry).

## Delivered

- `services/api/src/sklegal_api/pilot_verification.py`: the pilot
  verification suite. `run_pilot_verification` is read-only over the
  S5-01A dry-run report, the S5-01B approved import store and result,
  the rendered SKL-S4-02 workspace view, and the SKL-S2-05 registry
  state, and records one typed boolean per acceptance check across six
  frozen evidence sections (source preservation, terminology, tensions,
  states, provenance, corpus coverage) plus a replay section.
  `verification_fingerprint` hashes the replayable evidence content
  excluding the replay comparison, checks, pass flag, and wall-clock
  generation time, so two suites over identical pinned inputs must
  fingerprint identically. A model validator rejects any suite whose
  collected checks disagree with its sections or whose pass flag
  disagrees with its checks. The suite was recovered in flight from the
  worktree WIP commit and repaired: the replay comparison was stubbed
  and always-true, the fingerprint leaked `generated_at`, the tension
  not-harmonized check used the wrong semantics (it failed the healthy
  fixture because one tension group legitimately holds two predicates
  twice across two documents), the plan-coverage check demanded exact
  set equality with the inventory although the plan legitimately pins
  the legacy registry index outside the matter tree, and the suite
  constructor could not build (validator ran before
  `object.__setattr__`). The `json()` override of the deprecated
  pydantic v1-style method was renamed to `evidence_json()`.
- `tests/support/pilot_corpus.py`: builds the synthetic S2-05 corpus
  state for one synthetic Tenant and one synthetic release from the
  pilot plan's pinned source files by driving the real S2-05 components
  end to end: deterministic outbox events replayed through
  `CorpusRegistryProjector` into `InMemoryCorpusRegistryStore`, one
  `DeepCorpusReconciler` run over synthetic corpus, expectation, and
  projection ports (complete, one release, no discrepancies), and one
  `BoundedCorpusHealthReader` read at the contract budget of 100 ms.
- `scripts/pilot_verification_suite.py`: assembles the full stack over
  the shared synthetic HammerTime fixture tree in a temporary directory
  and runs the suite twice over identical pinned inputs; the second run
  is verified as a deterministic replay of the first. Writes
  `docs/evidence/migration/SKL-S5-01C-PILOT-VERIFICATION-SUITE-2026-08-22.json`
  and exits 2 if any check fails.
- `tests/test_pilot_verification.py`: 15 tests over the full synthetic
  stack.

## Verification

Commands and exact results:

```text
uv run --locked --package sklegal-api pytest tests/test_pilot_verification.py -q
15 passed in 0.67s

uv run --locked --package sklegal-api pytest tests/test_pilot_verification.py tests/test_workspace_pilot_projection.py tests/test_pilot_import.py tests/test_pilot_dry_run.py tests/test_api_workspace.py -q
64 passed in 1.81s

uv run --locked --package sklegal-api pytest tests/test_corpus_registry.py tests/test_corpus_health.py tests/test_corpus_reconciliation.py -q
59 passed in 0.57s

uv run --locked ruff format --check <new and changed files>
all formatted

uv run --locked ruff check <new and changed files>
All checks passed!

uv run --locked mypy services/api/src/sklegal_api/pilot_verification.py tests/test_pilot_verification.py tests/support/pilot_corpus.py scripts/pilot_verification_suite.py
16 errors: all import-untyped notes for workspace packages, the same
pre-existing category the S5-01A evidence recorded; no other finding.

uv run --locked python scripts/check_secrets.py
no finding in any file added or changed by this card

.venv/bin/python scripts/pilot_verification_suite.py --date 2026-08-22
run_id=fd023b87-902a-5437-afd5-e06a42c2877c
checks_total=46 checks_failed=0 (exit 0)
```

## Acceptance evidence

Acceptance: the pilot matter is understandable in the UI with tensions
and provenance intact; no state advances without approval; corpus
registry coverage verified via S2-05; full pilot verification suite
replay evidence produced.

- Understandable in the UI: the suite verifies the rendered SKL-S4-02
  workspace aggregate (matter, timeline, facts, tension groups,
  communications, version lineage, execution states, gaps, audit,
  provenance) against the approved import plan. Terminology checks prove
  the legacy `Problem` renders only as a Matter provenance alias, the
  legacy `Incident` renders only as a Matter Event provenance alias,
  legacy storage labels never surface in the UI dump, canonical types
  are present, and aliases stay unique in scope.
- Tensions intact: every tension group renders unresolved and
  review-required with its full assertion-id membership preserved from
  the plan (`assertions_not_harmonized` compares group membership
  sets, per-group minimum size two, and that every grouped assertion
  still renders as an individual fact; `assertion_values_preserved`
  compares each fact's predicate and asserted value against the plan).
  The suite fails closed when a tampered plan resolves a tension
  (`test_harmonized_tension_fails_closed`).
- Provenance intact: source pins, version lineage with one current
  review baseline and history retained, snapshot pin, freshness, facts
  carrying source references, missing-source gaps, explicit gaps, and
  the workspace read-back fingerprint are each separate checks. The
  suite fails closed on a stale render
  (`test_stale_render_fails_provenance`).
- No state advances without approval: every rendered state must equal
  `approval/pending_review` or `execution/not_started`, every imported
  target must carry both negative states, rendered states must match
  the import store exactly, the human approval must be recorded on the
  batch, and the import must be gated on the approved mapping decision.
  The suite fails closed when a tampered store row carries a
  `dispatched` execution state (`test_advanced_execution_state_fails_closed`).
- S2-05 corpus registry coverage: the registry entry for the pinned
  Tenant and release must be present, its source count must equal the
  pinned corpus size, its last-reconciled time must be recorded, the
  deep reconciliation must be complete, cover at least one release, and
  report no discrepancies, and the bounded health read must be
  available and consistent with the pinned count. The suite fails
  closed on registry count drift, a missing entry, and an incomplete
  reconciliation
  (`test_registry_count_mismatch_fails_coverage`,
  `test_missing_registry_entry_fails_coverage`,
  `test_unreconciled_corpus_fails_coverage`).
- Replay evidence: the artifact records the full second-run suite with
  its replay section proving the run id, check set, import batch, and
  evidence fingerprint all match the first run
  (`replays_deterministic: true`) while the wall-clock generation
  times differ. Replay fails closed against changed pinned inputs and
  against a failing original (`test_replay_detects_changed_pinned_inputs`,
  `test_replay_rejects_failing_original`,
  `test_fingerprint_excludes_generation_time`).

## Limitations

- All evidence is synthetic: the shared HammerTime fixture tree, the
  in-memory pilot store, and the in-memory corpus registry. A live
  import remains gated on the pending S5-01A human mapping review, so
  the real Liberty Auto pilot tree was not read, and no HammerTime path
  was touched.
- The corpus state is built from the pilot plan's pinned sources; it
  proves the S2-05 wiring and coverage contract, not a live corpus
  reconciliation against the real HammerTime release.
- The suite does not itself render HTML; it verifies the workspace
  aggregate the S4-02 routes and web shell render, including the exact
  JSON dump terminology checks.
- Rollback is a Git revert; no migration, no canonical-record write, no
  external action, and no HammerTime file was changed by this card.
