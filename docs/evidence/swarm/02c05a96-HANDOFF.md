# HANDOFF: SKL-S4-04C DOCX tracked-change export

Card: `02c05a96` (`SKL-S4-04C`, slice of `2e5462d8`)
Implementation commit: `2f8cadb`

## Files changed

- Worker export engine: `services/worker/src/sklegal_worker/document_export.py`
- Temporal payloads and activity registration:
  `services/worker/src/sklegal_worker/models.py`,
  `services/worker/src/sklegal_worker/activities.py`,
  `services/worker/src/sklegal_worker/worker.py`, and
  `services/worker/src/sklegal_worker/__init__.py`
- Tests: `tests/test_work_product_docx_export.py`
- Completion evidence:
  `docs/evidence/status/SKL-S4-04C-COMPLETION-EVIDENCE-2026-08-22.md`

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

Ruff check and format check on changed Python files
passed

Stored doc.haus no-copy provenance validation and CycloneDX validation
passed

git diff --check and ASCII dash scan
passed
```

## Acceptance criteria evidence

- Genuine tracked changes use `w:ins`, `w:del`, paragraph-mark revision
  records, and `w:trackRevisions` in a deterministic DOCX package.
- Round-trip acceptance yields the exact current document text and paragraph
  structure.
- Paragraph and run styles survive export and acceptance.
- The decorated Temporal activity receives references and exact hashes only.
- Approval binds the exact Approval ID, Tenant, Matter, Work Product, version,
  content hash, and deterministic DOCX hash.
- Editing the approved artifact invalidates the request. An edited successor
  cannot export under the superseded Approval and requires validation and a new
  exact-hash Approval.
- The PDF preview is bound to the DOCX hash, must be complete, and must contain
  at least one page before artifacts are recorded.
- Idempotent replay returns one receipt and does not duplicate artifacts.

## Known limitations

- LibreOffice is not installed locally, so its production rendering adapter was
  not executed. The hermetic valid-PDF renderer covers activity integration,
  exact binding, page validation, failure, and storage behavior.
- Durable artifact persistence and the live Approval repository are injected
  interfaces and remain deployment integration work.
- A caller must perform the two-phase prepare, human Approval, and export flow.

## Migration and rollback

No migration or data change occurred. Revert this evidence commit and then
`2f8cadb` to roll back. No HammerTime path, external action, secret, credential,
remote Git operation, SKCapstone board command, or path outside this worktree
was modified. Jarvis owns board update and card completion.
