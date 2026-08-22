# SKL-S5-01B completion evidence

Date: 2026-08-22

Board card: `59189e97`

Parent: SKL-S5-01 (`8d52de94`), pilot TDD
`docs/architecture/LIBERTY-AUTO-PILOT-TDD.md`. Slice of the S5-01A dry run
(card `c5454b85`).

## Delivered

- Migration `0016_pilot_import.sql`: an isolated pilot import staging
  surface in the `sklegal_migrations` schema. `pilot_import_batches`
  records one human-approved batch per source snapshot (reviewer, decision
  time, review artifact, `imported`/`withdrawn` lifecycle).
  `pilot_import_records` keys every Matter or Matter Event proposal by its
  deterministic idempotency key with a monotonic revision per target.
  `pilot_import_source_files`, `pilot_import_facts`, and
  `pilot_import_tension_groups` pin source hashes, atomic Fact Assertion
  proposals, and unresolved Tension Group records. `pilot_import_states`
  admits only negative states by database CHECK (`pending_review`
  approvals, `not_started` executions).
  `pilot_import_withdrawn_targets` pins withdrawn target UUIDs against
  reuse. Manifest updated; `scripts/check_migrations.py` reports
  `migration manifest valid: 16 migration(s)`.
- `sklegal_migration.approved_import`: the approved-import path.
  `run_approved_import` fails closed without an explicit `MappingApproval`
  whose decision is `approved` and whose idempotency key set exactly covers
  the plan records (exact-version approval, since each key embeds the
  source content hash). It reconciles the plan against the S5-01A source
  inventory before the first write and fails closed on any missing or
  hash-mismatched file. Every write is insert-if-absent on deterministic
  idempotency keys. `withdraw_batch` marks a batch withdrawn and pins its
  targets; `reset_disposable_batch` physically deletes a withdrawn batch's
  child rows only on a store flagged disposable, preserving the batch
  tombstone and withdrawn-target pins. Stores: driver-neutral
  `PostgresPilotImportStore` (injected SQL executor, same pattern as the
  CapAuth Postgres backends) and `InMemoryPilotImportStore` for unit tests
  and replay evidence.
- `scripts/pilot_import_replay.py`: synthetic replay of the full lifecycle
  (import, rerun, changed-source revision, withdraw, disposable reset) that
  writes the machine-readable artifact
  `SKL-S5-01B-APPROVED-IMPORT-2026-08-22.json` and exits 2 if any invariant
  fails.
- `docs/development/PERSISTENCE.md` documents migration 0016 (required by
  the development contract tests).

## Verification

Commands and exact results:

```text
.venv/bin/python -m pytest tests/test_pilot_import.py -q
12 passed in 0.49s

.venv/bin/python -m pytest tests/integration/test_pilot_import.py -q
6 passed in 28.32s   (networkless disposable PostgreSQL 17.7 container)

.venv/bin/python -m pytest tests/integration/test_persistence_contract.py -q
44 passed, 74 subtests passed in 95.07s

.venv/bin/python -m pytest tests/test_pilot_dry_run.py tests/test_pilot_importer.py tests/test_hammertime_adapter.py tests/test_hammertime_bridge.py -q
78 passed, 7 subtests passed in 0.65s

uv run --locked ruff format --check scripts tests services packages
224 files already formatted

uv run --locked ruff check scripts tests services packages
All checks passed!

uv run --locked mypy <scripts> services packages
Success: no issues found in 124 source files

python3 scripts/check_migrations.py
migration manifest valid: 16 migration(s)

python3 scripts/check_fixture_safety.py
fixture safety valid: 6 file(s)

.venv/bin/python scripts/pilot_import_replay.py
all six invariants True (see artifact)
```

`scripts/check_secrets.py` reports the same two pre-existing
`migrations/manifest.json` hex-entropy findings as the base branch
(digest pins); no new finding category was introduced.

## Acceptance evidence

Acceptance: rerun creates no duplicates and tensions remain visible.

- Import gated on human-approved mapping decisions, failing closed without
  them: `test_import_fails_closed_without_any_approval`,
  `test_import_fails_closed_on_unapproved_or_mismatched_review`
  (rejected decision, wrong batch, partial key coverage, blank reviewer),
  and integration test 06 (no batch row written in the real store). The
  S5-01A review is pending by design; tests supply an approved-review
  fixture (`MappingApproval` with exact key coverage).
- Idempotency keys on every import write: batches, records, source files,
  facts, tensions, and states are all insert-if-absent on deterministic
  keys (`ON CONFLICT DO NOTHING` in Postgres; key-checked dicts in memory).
- Count reconciliation against the S5-01A source inventory:
  `_reconcile_inventory` proves every inventoried file is covered by the
  plan with a matching hash before any write; the result reconciliation
  records `inventoried_files`, plan coverage, and created/suppressed
  counts. Live-source reconciliation input is the 112-file S5-01A
  inventory shape; the synthetic fixture inventory (7 files) exercises the
  same code path.
- Rerun creates no duplicates: `test_rerun_creates_no_duplicates` (all
  suppressed, store counts identical) and integration test 02 (real
  Postgres counts unchanged after rerun).
- Changed-source revision: `test_changed_source_creates_revision_without_duplicates`
  and integration test 04 show the changed Matter Event landing as
  revision 2 while the unchanged Matter record is suppressed and prior
  facts are retained.
- Negative execution state: every imported target carries exactly
  `approval/pending_review` and `execution/not_started`; integration test
  03 proves the database CHECK rejects a `dispatched` execution write.
- Disposable rollback: `test_disposable_reset_requires_disposable_withdrawn_batch`
  and integration test 05 prove reset is refused on non-disposable stores
  and non-withdrawn batches, deletes only the withdrawn batch's child rows,
  preserves the tombstone and withdrawn-target pins, and blocks reimport of
  withdrawn targets.
- Tensions remain visible: tension rows import with status `unresolved`
  and `review_required`, reference existing fact assertions, survive rerun
  unsuppressed-by-merge (one row per batch), and are never harmonized;
  integration test 04 asserts every stored tension group is unresolved and
  review-required.

## Limitations and rollback

- No live import ran: the S5-01A human mapping review is pending by
  design, so importing the real pilot tree would violate the approval
  gate. All import evidence comes from the synthetic fixture tree plus the
  disposable Postgres container. A live approved import is a later slice
  after the human decision lands.
- The import writes to a staging surface in `sklegal_migrations`, not yet
  to the canonical `sklegal_legal` records (matters, matter_events, fact
  assertions). Promotion of approved staging rows into canonical records
  is a later SKL-S5-01 slice.
- The idempotency key formula (pilot TDD section 7) intentionally excludes
  the snapshot label, while target UUIDs derive from it. Identical source
  content imported under two different snapshot labels suppresses the
  second record row, binding it to the first batch's target. For the pilot
  the snapshot label is pinned per import and reruns reuse it; cross-label
  deduplication semantics are deferred to the canonical-record slice.
- Fact assertions cover Markdown frontmatter only (inherited from the
  S2-02 importer); JSON-body facts remain hashed and key-listed but
  unsplit, as recorded in the S5-01A limitations.
- Migration/rollback: migration 0016 has a working down section (proven by
  the persistence contract up/down/up cycle). Data rollback is the
  withdraw plus disposable-reset path above; no HammerTime file was
  written, moved, or archived; no external state changed. Rollback of this
  card is a Git revert of its commit.
