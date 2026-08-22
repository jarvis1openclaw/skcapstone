# SKL-S5-01A completion evidence

Date: 2026-08-22

Board card: `c5454b85`

Parent: SKL-S5-01 (`8d52de94`), pilot TDD
`docs/architecture/LIBERTY-AUTO-PILOT-TDD.md`.

## Delivered

- A gated, read-only matter-tree inventory API on the HammerTime adapter
  (`list_matter_artifacts`, `read_matter_artifact`) that pins every regular
  file under an authorized legacy matter with content SHA-256, byte count,
  and source modification time, and records skipped entries (symlinks,
  special files, oversized artifacts, any `Inbox` component) verbatim
  instead of dropping them.
- A pilot dry-run module (`sklegal_migration.dry_run`) that executes pilot
  TDD phase A: pre/post source hash capture, JSON key and Markdown
  frontmatter capture, the S2-02 importer dry run, count reconciliation,
  and a zero-source-change proof.
- A CLI (`scripts/pilot_dry_run.py`) that runs the dry run against the live
  Liberty Auto pilot source set with a narrow authorizer limited to
  `PRB-2026-009` and `INC-016`, writes the evidence artifacts, and fails
  closed with exit code 2 if any source change is detected.
- Evidence artifacts from the live run:

  - `SKL-S5-01A-PILOT-SOURCE-HASHES-2026-08-22.sha256` (112 files)
  - `SKL-S5-01A-PILOT-DRY-RUN-2026-08-22.json` (machine report)
  - `SKL-S5-01A-PILOT-MAPPING-REVIEW-2026-08-22.md` (human review artifact)

## Verification

Commands:

```text
.venv/bin/python -m pytest tests/test_pilot_dry_run.py tests/test_hammertime_adapter.py tests/test_pilot_importer.py tests/test_hammertime_bridge.py -q
```

Result: `78 passed, 7 subtests passed`.

Live dry run against the pilot source set:

```text
.venv/bin/python scripts/pilot_dry_run.py --root /mnt/cloud/onedrive/projects/DAVE-AI/hammerTime
```

Result: `files_hashed=112`, `zero_source_changes=True`, `import_writes=0`,
run id `2413c223-c54c-557d-866a-1e175f68b8b0`, import batch proposal
`528fad8e-aaea-5a56-9879-c32d314bec83`.

Independent hash verification against the live source:

```text
cd /mnt/cloud/onedrive/projects/DAVE-AI/hammerTime && sha256sum -c <worktree>/docs/evidence/migration/SKL-S5-01A-PILOT-SOURCE-HASHES-2026-08-22.sha256
```

Result: all 112 entries report `: OK`.

Additional checks passed:

```text
.venv/bin/ruff format --check scripts tests packages
.venv/bin/ruff check scripts tests packages
.venv/bin/mypy packages/migration packages/connectors/hammertime
git diff --check
```

The only mypy findings are pre-existing `import-untyped` notes for
`sklegal_domain`, identical on the base branch.

## Acceptance evidence

Acceptance: the dry run satisfies the pilot TDD mapping review with zero
source changes.

- Source hash capture: 112 files under the pilot matter root hashed and
  pinned before and after the run; manifest verified independently with
  `sha256sum -c` (112 of 112 OK).
- S2-02 importer dry run: the plan proposes `PRB-2026-009` as a Matter
  (`problem.matter@1`) and `INC-016` as a transaction-review Matter Event
  (`incident.transaction_review@1`), 54 atomic fact assertions, 2
  unresolved tension groups, and packet lineage v2/v3 historical with v4
  as the current review baseline. `write_operations` is empty and
  `hammer_time_mutation` is false.
- Human mapping review: the mapping-review artifact lists every proposed
  mapping with status `proposed` and decision `pending`; the human
  decision block is explicitly unfilled. No mapping is approved by this
  slice.
- Zero source changes: pre/post comparison shows 0 changed, 0 added,
  0 removed, and 0 modification-time changes across all 112 files. The
  adapter exposes no write surface (enforced by
  `test_adapter_exposes_no_write_surface` and
  `test_package_source_contains_no_write_calls`).

## Limitations and rollback

- The pilot source snapshot is a HammerTime working tree, not a corpus
  release manifest; the pilot tree is uncommitted in the HammerTime git
  repository and the incident registry had pre-existing uncommitted
  modifications (mtime 2026-08-21 22:11 UTC, before this run). Content
  pins still make any later drift detectable.
- Frontmatter-only fact extraction: the S2-02 importer derives atomic
  facts from Markdown frontmatter; JSON-body facts (for example
  `case-facts.json` trust-name and timing-rule variants) are hashed and
  key-listed but not yet split into assertions. Structured import is a
  later SKL-S5-01 slice.
- The human mapping review decision is pending by design; no import batch
  is activated and no target record exists.
- Rollback is a Git revert of this card's commit. No HammerTime file was
  written, moved, or archived; no database rows exist; no external state
  changed.
