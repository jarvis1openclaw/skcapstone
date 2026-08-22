# HANDOFF: SKL-S4-04B claim-grounded drafting UI and version compare

Card: `d5e43f63` (`SKL-S4-04B`, slice of `2e5462d8`)
Branch commits: `c183201` and `846720e`, plus this handoff commit.

## Files changed

- Domain: `packages/domain/src/sklegal_domain/claim_grounded.py`,
  `packages/domain/src/sklegal_domain/states.py`,
  `packages/domain/src/sklegal_domain/__init__.py`
- Domain tests: `tests/test_claim_grounded_drafting.py`
- API read projection: `services/api/src/sklegal_api/workspace.py`,
  `tests/test_api_workspace.py`
- Web Documents UI: `apps/web/src/components/WorkProductDrafting.tsx`,
  `apps/web/src/components/workProductDrafting.test.tsx`,
  `apps/web/src/pages/MatterWorkspace.tsx`,
  `apps/web/src/pages/matterWorkspace.test.tsx`
- Web contracts and presentation: `apps/web/src/api/types.ts`,
  `apps/web/src/design/tokens.ts`, `apps/web/src/styles.css`,
  `apps/web/src/testing/workspace.ts`, and the visual regression snapshot
- Completion evidence:
  `docs/evidence/status/SKL-S4-04B-COMPLETION-EVIDENCE-2026-08-22.md`

## Tests and exact results

```text
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-domain pytest tests/test_claim_grounded_drafting.py tests/test_work_product_drafting.py tests/test_claim_ledger.py -q
73 passed, 6 subtests passed in 0.21s

UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-api pytest tests/test_api_workspace.py -q
13 passed in 0.38s

npm test --workspace @sklegal/web
12 test files passed, 165 tests passed

npm run typecheck --workspace @sklegal/web
passed

npm run lint --workspace @sklegal/web
passed

npm run format:check --workspace @sklegal/web
passed

npm run build --workspace @sklegal/web
passed, 177 modules transformed

ruff check and ruff format --check on changed Python files
passed

ASCII dash scan and git diff --check
passed
```

## Acceptance criteria evidence

- Ungrounded factual sentences render a critical warning, remain visible, and
  block `DRAFT_READY` in both domain and UI tests.
- Bracketed unknowns render in place and remain explicit readiness blockers.
- Grounded factual sentences carry Claim ledger ids, statements, state, and a
  Claim deep link.
- Version compare renders stable unchanged, removed, and added sentence rows.
- Editing after Approval creates a changed successor, clears validation and
  Approval evidence, supersedes the approved version, and shows an exact-hash
  invalidation alert requiring new validation and Approval.

## Known limitations

- The API model exposes the read projection with an empty default; a live
  persistence adapter that populates Work Products is parent-card work.
- Saving edits through a mutation endpoint is outside this read-side UI slice.
- Structural headings without terminal punctuation are not treated as factual
  sentences by the domain evaluator.
- DOCX tracked changes and PDF preview belong to `SKL-S4-04C`.

## Migration, rollback, and boundaries

No migration or data change was made. Revert this handoff commit, `846720e`,
and `c183201` to roll back. No HammerTime path, external action, secret,
credential, remote git operation, SKCapstone board command, or path outside the
assigned worktree was accessed or modified. Jarvis owns the card update and
completion.
