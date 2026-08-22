# SKL-S4-03B handoff

Card: `4d98b588` (SKL-S4-03B, slice of `f31e9c1e` SKL-S4-03)
Branch: `swarm/4d98b588`

## Files changed

Backend:

- `services/api/src/sklegal_api/claims.py`
- `tests/test_api_claims.py`

Frontend:

- `apps/web/src/api/client.ts`
- `apps/web/src/api/types.ts`
- `apps/web/src/api/api.test.ts`
- `apps/web/src/design/tokens.ts`
- `apps/web/src/pages/CorpusPage.tsx`
- `apps/web/src/pages/corpusPage.test.tsx`
- `apps/web/src/testing/claims.ts`

Evidence:

- `docs/evidence/status/SKL-S4-03B-COMPLETION-EVIDENCE-2026-08-22.md`
- `HANDOFF.md`

## Tests and exact results

- Required package command plus neighboring API boundaries:
  `UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-api pytest tests/test_api_claims.py tests/test_api_corpus.py tests/test_api_workspace.py -q`
  - `44 passed in 0.48s`
- Focused frontend tests:
  `npm test -- --run src/pages/corpusPage.test.tsx src/api/api.test.ts`
  - 2 test files passed, 29 tests passed
- Full frontend tests: `npm test`
  - 11 test files passed, 163 tests passed
- `npm run typecheck`: passed
- `npm run lint`: passed
- `npm run format:check`: passed
- `npm run build`: passed, 176 modules transformed
- Ruff format check: 2 files already formatted
- Ruff lint: all checks passed
- `git diff --check`: passed

## Acceptance criteria evidence

- Every material claim exposes support: separate support and counter-support
  panels preserve exact locator, span, hashes, recorder, timestamp, and policy
  revision.
- Every material claim exposes qualification: support-verification status,
  authority applicability factors and reasons, and the deterministic claim
  gate with failed checks remain visible.
- Every material claim exposes challenge: blind-challenge independence,
  model identity, outcome, and defects are visible without reducing contrary
  findings.
- Every material claim exposes review history: human reviewer decisions are
  append-only records with principal, time, claim version, decision, note,
  and policy revision, separate from model challenge history.
- Claim-state transitions render in recorded order with from-state, to-state,
  time, and version.
- Required mismatch defect, contrary-support, and no-answer cases have API and
  component tests.
- The API requires exact Matter-scoped `claim.review`, enforces membership,
  and fails closed without exposing ledger data.

## Known limitations

- The read store is an interface plus in-memory test implementation. A live
  projection adapter remains parent-card integration work.
- This slice renders state transitions and reviewer decisions but does not
  mutate claim state.
- Support records cannot deep-link to the corpus source viewer until the
  ledger projection carries the viewer's source ID.
- No data migration exists. Rollback is a file-only revert of the two task
  commits.
- Jarvis owns the board evidence link and card completion. This worker ran no
  SKCapstone board command.
