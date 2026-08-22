# SKL-S4-04B completion evidence

Date: 2026-08-22
Card: `d5e43f63` (`SKL-S4-04B`, slice of `2e5462d8`)
Agent: `skl-s4-04b`

## Outcome

Built the claim-grounded drafting and version compare slice of the Matter
Documents surface from `docs/planning/wireframes/index.html` section `s-docs`.

- Each factual sentence has a deterministic key and an exact binding to a
  Work Product version id, version number, content hash, Tenant, Matter, and
  Claim ledger entry.
- Ungrounded, missing-Claim, and withdrawn-Claim sentences remain visible with
  explicit warnings and block `DRAFT_READY`.
- Bracketed unknowns render in place and remain explicit readiness blockers.
- The Documents editor shows sentence grounding, Claim links, source status,
  exact current hash, and a sentence-level previous-to-current compare table.
- An Approval is valid only when its version id, version number, and content
  hash all match the current version. A changed version displays an invalidated
  Approval alert with both hashes and requires re-validation and new Approval.
- The approved version is superseded rather than rewritten when an edited
  successor is created.

## Files changed

- `packages/domain/src/sklegal_domain/claim_grounded.py`
- `packages/domain/src/sklegal_domain/states.py`
- `packages/domain/src/sklegal_domain/__init__.py`
- `tests/test_claim_grounded_drafting.py`
- `services/api/src/sklegal_api/workspace.py`
- `tests/test_api_workspace.py`
- `apps/web/src/api/types.ts`
- `apps/web/src/components/WorkProductDrafting.tsx`
- `apps/web/src/components/workProductDrafting.test.tsx`
- `apps/web/src/pages/MatterWorkspace.tsx`
- `apps/web/src/pages/matterWorkspace.test.tsx`
- `apps/web/src/design/tokens.ts`
- `apps/web/src/styles.css`
- `apps/web/src/testing/workspace.ts`
- `apps/web/src/__snapshots__/visual-regression.test.tsx.snap`

## Tests and exact results

```text
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-domain pytest tests/test_claim_grounded_drafting.py -q
Result: 39 passed in 0.15s

UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-domain pytest tests/test_claim_grounded_drafting.py tests/test_work_product_drafting.py tests/test_claim_ledger.py -q
Result: 73 passed, 6 subtests passed in 0.21s

UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-api pytest tests/test_api_workspace.py -q
Result: 13 passed in 0.38s

npm test --workspace @sklegal/web
Result: 12 test files passed, 165 tests passed

npm run typecheck --workspace @sklegal/web
Result: passed

npm run lint --workspace @sklegal/web
Result: passed

npm run format:check --workspace @sklegal/web
Result: all matched files use Prettier code style

npm run build --workspace @sklegal/web
Result: passed, 177 modules transformed

ruff check on changed Python implementation and tests
Result: all checks passed

ruff format --check on changed Python implementation and tests
Result: all checked files formatted

rg -n '[en dash or em dash codepoints]' on changed files
Result: no findings

git diff --check
Result: passed
```

## Acceptance criteria evidence

- Ungrounded sentence warning: domain test
  `test_ungrounded_sentence_renders_its_warning` verifies the warning state and
  exact warning text. Web test
  `warns about an ungrounded factual sentence and blocks readiness` verifies
  the visible alert and `DRAFT_READY` blocker.
- Changed-after-approval invalidation: domain test
  `test_edit_after_approval_resets_gates_and_supersedes` verifies that validation
  and Approval evidence are cleared, the edited successor becomes current, and
  the approved version becomes superseded. Web tests verify exact-version
  mismatch, exact match, and absent Approval states.
- Bracketed unknown rendering: domain segment tests prove exact ordered content
  preservation. The web test verifies the unknown marker is rendered in place
  and its unresolved state remains visible.
- Version compare: domain tests verify stable sentence matching for added,
  removed, unchanged, replaced, inserted, and empty versions. The web test
  verifies all three compare row states and both version labels.

## Known limitations

- This slice defines the API read projection and its empty default, but it does
  not add a live persistence adapter that populates Work Products, sentence
  bindings, or compare rows. That integration remains parent-card work.
- The editor is the accessible saved-version UI projection. A write endpoint and
  persistence transaction for saving editor changes are outside this slice.
- Headings and list labels without terminal punctuation are treated as
  structural text, not factual sentences. The projection producer must classify
  unusual sentence forms before claiming `DRAFT_READY`.
- DOCX tracked-change export and rendered preview validation belong to
  `SKL-S4-04C` and are not implemented here.

## Migration and rollback

No database migration, data mutation, deployment, external action, or
HammerTime access occurred. Roll back by reverting commits `846720e` and
`c183201` in that order.

## Card linkage and boundaries

All work is linked to SKCapstone card `d5e43f63`. Per the delegated task,
Jarvis owns board updates and completion, so no SKCapstone board command was
run. No push, pull, remote access, secret access, external action, or file write
outside this worktree occurred.
