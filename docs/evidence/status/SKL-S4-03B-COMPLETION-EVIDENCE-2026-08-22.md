# SKL-S4-03B completion evidence

Date: 2026-08-22
Card: `4d98b588` (slice of `f31e9c1e` SKL-S4-03)
Agent: `skl-s4-03b`

## Outcome

Added the governed claim ledger to `/corpus`. Every material claim renders
separate support and counter-support panels, exact source locators and hashes,
authority applicability factors, support verification and `CLAIM_READY` gate
results, preserved blind-challenge defects, append-only human reviewer
decisions, and claim-state transition history. A recorded ledger with zero
claims renders an explicit no-answer state.

The new read route is scoped to the authenticated tenant and exact Matter. It
requires `claim.review` for `claim_review`, enforces Matter membership, and
fails closed for capability denial, membership denial, unknown Matters, and
store unavailability. The route has no mutation operation.

## Files changed

- `services/api/src/sklegal_api/claims.py`
- `tests/test_api_claims.py`
- `apps/web/src/api/client.ts`
- `apps/web/src/api/types.ts`
- `apps/web/src/api/api.test.ts`
- `apps/web/src/design/tokens.ts`
- `apps/web/src/pages/CorpusPage.tsx`
- `apps/web/src/pages/corpusPage.test.tsx`
- `apps/web/src/testing/claims.ts`

## Tests and exact results

- `UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-api pytest tests/test_api_claims.py tests/test_api_corpus.py tests/test_api_workspace.py -q`
  - `44 passed in 0.48s`
- `npm test`
  - `11 passed` test files, `163 passed` tests
- `npm run typecheck`
  - passed
- `npm run lint`
  - passed
- `npm run format:check`
  - passed
- `npm run build`
  - passed, 176 modules transformed
- `UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked ruff format --check services/api/src/sklegal_api/claims.py tests/test_api_claims.py`
  - `2 files already formatted`
- `UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked ruff check services/api/src/sklegal_api/claims.py tests/test_api_claims.py`
  - `All checks passed!`
- `git diff --check`
  - passed

## Acceptance evidence

| Requirement | Evidence |
| --- | --- |
| Every material claim exposes support | `ClaimLedgerView` renders exact support records with locator, span, source hash, excerpt hash, recorder, time, and policy revision. API and component tests assert the support panel. |
| Contrary support remains visible | Counter-support is a distinct typed array and panel. API models reject counter-support placed in the support panel. Required API and component tests assert the contrary source and unresolved contrary-review reason. |
| Qualification is reconstructible | Support verification, applicability checks with closed reasons, and the current gate outcome with failed checks are shown per claim. Passing and failed outcomes are validated for internal consistency. |
| Challenges and mismatch defects remain visible | Blindness and independence labels, challenger identity, outcome, and every defect are rendered. Required tests preserve the quotation mismatch text and `challenge_defect_unresolved` gate reason. |
| Human review is distinct and attributable | `reviewHistory` records reviewer principal, exact claim version, time, decision, note, and policy revision. UI and API tests keep these records separate from model challenges. |
| Claim-state transitions remain visible | The ordered history renders initial proposal and every recorded from-state, to-state, time, and version. Tests assert the challenged sequence in order. |
| No-answer state | A recorded empty ledger returns HTTP 200 with no claims and renders a `role="status"` message stating there is no claim answer to review. |
| Protected read boundary | Tests cover missing credential, wrong capability, cross-Matter capability, cross-tenant token, non-member, unknown Matter, and store outage. |

## Known limitations

- The store is a Protocol plus an in-memory implementation, matching the
  neighboring corpus and workspace slices. A live projection adapter belongs
  to parent integration work.
- This slice is read only. It displays recorded state transitions and review
  decisions but does not create them.
- Support records expose exact provenance metadata but are not interactive
  source-span links because the S3-05 ledger contract does not carry the
  corpus viewer's source ID.
- Jarvis owns the SKCapstone board update and completion. No board command was
  run by this worker.

## Migration and rollback

No schema, Matter data, corpus content, runtime configuration, or external
system changed. No migration is required. Rollback is a file-only revert of
the implementation commit and evidence commit.
