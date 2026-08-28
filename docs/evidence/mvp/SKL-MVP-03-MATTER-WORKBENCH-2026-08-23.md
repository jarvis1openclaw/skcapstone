# SKL-MVP-03 Matter workbench evidence

Card: `9dcda941`

## Immutable inputs

- Browser session and API handoff: `398c414b670d2c60630e55891a4daa43a0dbe089`
- Exact task TDD: `ccafcbecf1a59d4dc7b99864b114cd4e681a0348`
- Isolated worktree: `/tmp/sklegal-mvp-9dcda941`

## Delivered vertical slice

- Matter detail now composes the authorized workspace and Claim ledger requests. Either request failing leaves the protected page in the existing bounded error state.
- The Matter workbench shows Client and Matter navigation, activity, provenance, Evidence Items, Fact Assertions, tensions, Claims, support and counter-support, applicability counts, corpus research navigation, Work Products, exact-version Approval invalidation, negative execution state, audit replay, and source hashes.
- The feature matrix labels delivered behavior `mvp-complete`, missing Task and Deadline mutation `safely-unavailable`, and external dispatch `post-mvp`.
- Calendar, Work Queue, Agent Runs, Approvals, and Administration no longer imply unfinished fixture content. Each route renders an explicit bounded availability state and, where useful, links to the available Matter or corpus surface.
- No external action is available. The UI states that no record, Approval, Execution Event, receipt, or external effect was created.

## Verification

Commands run from `/tmp/sklegal-mvp-9dcda941`:

```text
npm --workspace apps/web run typecheck
PASS

npm --workspace apps/web run lint
PASS

npm --workspace apps/web test -- --run
13 test files passed, 181 tests passed

npm --workspace apps/web run build
PASS, 176 modules transformed

PYTHONPATH=services/api/src:packages/capauth/src:packages/policies/src:packages/audit/src:packages/domain/src:packages/migration/src:packages/model_gateway/src:packages/retrieval/src /mnt/cloud/onedrive/projects/DAVE-AI/sklegal/.venv/bin/python -m pytest tests/test_browser_sessions.py tests/test_api_app.py tests/test_api_workspace.py tests/test_api_claims.py tests/test_api_corpus.py tests/test_api_governance.py tests/test_api_capability_attack_battery.py tests/test_api_capauth_composition_health.py -q
92 passed, 12 known Pydantic OpenAPI warnings, 50 subtests passed

git diff --check
PASS
```

Web coverage includes the integrated public-synthetic Matter workbench, missing Claim ledger state, contradictory Fact Assertions, missing Evidence Item source, support and counter-support, stale exact-version Approval, negative execution state, audit and provenance, feature availability, route guards, keyboard navigation, contrast tokens, responsive shell modes, error states, storage leakage, and visual snapshots.

The repository secret checker completed with only the existing baseline drift list in unchanged files. A changed-diff keyword review found no credential value, bearer material, provider key, password, or private key in this candidate.

## Known limitations

- Task and Deadline mutation is not present in the immutable API and remains explicitly unavailable.
- External action dispatch remains post-MVP. This card does not create a simulated connector receipt because no reviewed connector route is mounted.
- Accessibility evidence is the existing semantic, keyboard, contrast-token, compact-layout, expanded-layout, and visual component suite. No full browser Playwright or axe run is configured in this repository.
- The public-synthetic development backend is in memory. Its deterministic reset is composition restart. No protected data or migration is involved.
- The 12 API warnings are the known governance command defaults excluded from generated Pydantic schemas.

## Rollback

Revert the SKL-MVP-03 candidate commit to return exactly to TDD handoff `ccafcbecf1a59d4dc7b99864b114cd4e681a0348` and browser-session implementation `398c414b670d2c60630e55891a4daa43a0dbe089`. No database, credential, service, provider, host, external action, or deployment state changed.
