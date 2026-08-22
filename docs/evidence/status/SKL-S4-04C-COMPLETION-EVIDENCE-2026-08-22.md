# SKL-S4-04C completion evidence

Date: 2026-08-22
Card: `02c05a96` (`SKL-S4-04C`, slice of `2e5462d8`)
Agent: `skl-s4-04c`
Implementation commit: `2f8cadb`

## Outcome

Implemented deterministic genuine DOCX tracked-change export through a typed
Temporal activity, exact-version and exact-DOCX-hash Approval enforcement,
edit invalidation, idempotent derived-artifact recording, and rendered PDF
preview validation.

- Temporal history contains only Tenant, Matter, Work Product, version,
  artifact reference, digest, Approval, and idempotency fields. Protected
  document bodies are resolved inside the activity through an injected store.
- Exported WordprocessingML uses `w:ins`, `w:del`, paragraph-mark changes, and
  `w:trackRevisions`. Its ZIP member order, timestamps, revision metadata, XML,
  and bytes are deterministic.
- Accepting changes removes deleted paragraphs, unwraps insertions, clears
  revision tracking, and yields the exact current paragraph text and styles.
- Paragraph and character style IDs remain present in `document.xml` and are
  declared in `styles.xml` after tracked-change acceptance.
- Approval must bind the exact Approval ID, Tenant, Matter, Work Product,
  version ID, version number, canonical content SHA-256, and deterministic DOCX
  SHA-256. Any edit or mismatch fails before preview rendering or persistence.
- Preview rendering uses an injected renderer. The production adapter invokes
  headless LibreOffice with an isolated temporary profile. The activity binds
  the resulting PDF to the DOCX SHA-256, verifies PDF completion, and requires
  at least one rendered page before recording either derived artifact.
- Idempotent replay returns the original receipt. Reuse of a key with different
  derived bytes fails closed.

## Files changed

- `services/worker/src/sklegal_worker/document_export.py`
- `services/worker/src/sklegal_worker/models.py`
- `services/worker/src/sklegal_worker/activities.py`
- `services/worker/src/sklegal_worker/worker.py`
- `services/worker/src/sklegal_worker/__init__.py`
- `tests/test_work_product_docx_export.py`
- `docs/evidence/status/SKL-S4-04C-COMPLETION-EVIDENCE-2026-08-22.md`
- `HANDOFF.md`

## Tests and exact results

```text
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-worker pytest tests/test_work_product_docx_export.py -q
7 passed in 0.29s

UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-worker pytest tests/test_work_product_docx_export.py tests/test_worker_workflows.py -q
49 passed, 10 subtests passed in 0.34s

UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-domain pytest tests/test_claim_grounded_drafting.py tests/test_work_product_drafting.py -q
58 passed, 6 subtests passed in 0.18s

UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked mypy services/worker/src/sklegal_worker/document_export.py
Success: no issues found in 1 source file

UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked ruff check on changed Python files
All checks passed

UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked ruff format --check on changed Python files
6 files already formatted

DOCX no-copy provenance validation
doc-haus provenance valid: 10 candidate decisions, 3857 package records, 2889 CycloneDX components, 480 SKLegal implementation files and 2831140 token windows checked
CycloneDX 1.6 schema valid

git diff --check
passed

ASCII dash scan on changed code and tests
no findings
```

## Acceptance criteria evidence

- DOCX round-trip and tracked-change acceptance: test
  `test_docx_contains_genuine_tracked_changes_and_accepts_to_current` inspects
  the package XML, requires `w:ins`, `w:del`, and `w:trackRevisions`, accepts
  the changes, and verifies the exact current text and paragraph count.
- Style retention: test
  `test_paragraph_and_run_styles_survive_export_and_acceptance` verifies
  Heading, body, citation, and signature style IDs in accepted output and the
  style declarations.
- Exact-hash Approval and Temporal activity: test
  `test_temporal_activity_records_exact_hash_and_validated_pdf_preview` invokes
  the decorated worker activity, proves the approved DOCX hash, validates its
  one-page PDF preview, and proves idempotent replay.
- Supersession on edit and required re-Approval: test
  `test_edit_after_approval_requires_successor_validation_and_approval` replaces
  the approved artifact reference with an edited successor and proves the old
  request fails. It then requests version 3 under the old Approval and proves
  exact binding denies export. Existing domain suites additionally prove the
  approved version becomes superseded and the successor returns to review.
- Preview fail-closed behavior: test `test_pdf_without_page_fails_closed`
  proves an incomplete preview cannot be recorded.
- Independent implementation and rights boundary: the stored no-copy scanner
  passed. No doc.haus source, adapter, dependency, asset, or quarantined code
  was read or copied.

## Known limitations

- LibreOffice is not installed in this worktree environment, so the production
  headless renderer adapter could not be exercised locally. The hermetic test
  renderer emits a structurally valid one-page PDF and exercises exact binding,
  page validation, failure, and persistence behavior.
- The in-memory store and static Approval gate are test adapters. A durable
  artifact store and Approval repository must implement the same injected
  contracts before production use.
- This card registers the Temporal activity on worker queues. Orchestration of
  the two-phase flow that first computes the deterministic DOCX hash and then
  obtains human Approval remains a caller responsibility.

## Migration, rollback, and boundaries

No database migration, production data mutation, deployment, external action,
HammerTime access, secret access, or remote Git operation occurred. Roll back
by reverting the evidence commit and implementation commit `2f8cadb`. The
project-local `.tools` and `.venv` directories were created by the pinned
repository bootstrap and are ignored build tooling.

Jarvis owns the SKCapstone task update and completion. No SKCapstone board
command was run.
