# SKL-MVP-INTEG-01 completion evidence

Date: 2026-08-27
Card: `c1ee25da`
Owner: `codex-luna-c1ee25da-refresh`
Worktree: `/tmp/sklegal-c1ee25da-integration-jarvis-20260824`
Branch: `codex/c1ee25da-integration-jarvis-20260824`
Verdict: PASS for the required c1 implementation gates; independent durable MVP review remains required

## Verification refresh

Refresh date: 2026-08-27
Refresh disposition: BLOCKED_PENDING_REPAIR

The recorded implementation candidate remains source-identical to commit
`8b9553b4167a6e1acaaffbaf0f17d59892513841`, tree
`9da2b879c08573ecd8dac421bcb43785570ff1a0`, because the three commits after
that candidate changed only this evidence document. The current target branch
HEAD is `ba0bb1feeb6f8516b1f65edb63837502ec4ee597`, tree
`b962e8beafe9b6246e7c17eebcbca915910043d7`.

The current shared main side differs from the candidate lineage at merge base
`523b3a7b7ca4dd513596d9d5ec73d344d922362d` by exactly commit
`30f7e8be92e2a0a3d6d46c6d438b3b4e79c1efda`. That commit adds only the
SKL-S5-05 acceptance package under `docs/acceptance/SKL-S5-05/` and does not
touch c1 source, migration, API, fixture, or evidence inputs.

Recomputed pins:

- `uv.lock`: `74141a126906894c87ab4e1bf3792bc9d8d7fc74075c7154871a6fe9d41ba21f`
- `package-lock.json`: `b4da8dc8d7815a19b54bff1ee059c25167485708c0261f40ca6b6df2452e71e8`
- migration manifest: `08c517e51124d68585ba16fef9791afb5a73c90f883b128790327ad0bc899c35`
- V2 surface manifest: `0857b0642c49531ae615362b7a40785e21aa8676a933ea6a27a5f1f057e11c66`
- V2 acceptance fixture: `4c65c56475532f43e84bc89d7c226046d0fa67996c21d1988522fa463b0f7775`
- V2 component map: `a632d9b2c9a1ac436c5cba165e543d2c1bc7ea8c509e6d8920261cbf88136d63`

Refresh commands and results:

- `.venv/bin/pytest -q tests/test_mvp_integration.py tests/test_api_app.py tests/contracts/test_v2_mvp_contract_freeze.py tests/mvp/test_public_synthetic_v2_cockpit.py tests/test_browser_sessions.py`: `81 passed, 12 warnings`.
- `.tools/bin/uv run --locked pytest -q tests/integration/persistence_contract_canonical_parity.py tests/integration/test_foundation_contract.py tests/features/agent_runs/test_migration.py tests/features/artifact_intake/test_migration.py tests/features/governed_corpus/test_migration.py tests/features/joined_analysis/test_disposable_postgres.py tests/features/matter_activity/test_migration.py tests/features/task_deadlines/test_migration.py tests/features/work_products/test_migration.py`: `34 passed, 1 failed, 53 subtests passed`.
- The failed test is `PersistenceContract08CanonicalParityTests.test_11_every_domain_entity_round_trips_through_rls`; the exact failing entity is `Party`, with no rows visible under the test role.
- `./scripts/run_checks.sh migration-check`: PASS, `28 migration(s)`.
- `./scripts/run_checks.sh fixture-check`: PASS, `17 file(s)`.
- `./scripts/run_checks.sh secret-scan`: FAIL, ten unreviewed high-entropy findings in `migrations/manifest.json` and `tests/fixtures/mvp/public-synthetic-mvp-v2-acceptance.json`.
- `./scripts/run_checks.sh vulnerability-scan`: PASS, no known Python or Node vulnerabilities and vendored CapAuth verified.
- `git diff --check`: PASS before this addendum.

No source, migration SQL, fixture, runtime, database, credential, provider,
HammerTime Inbox, deployment, merge, push, or external state changed during
this refresh. The card remains in progress and must not be completed until the
secret baseline findings are independently classified and the Party RLS parity
failure is repaired and independently rereviewed.

## Immutable candidate

- Candidate commit: `8b9553b4167a6e1acaaffbaf0f17d59892513841`
- Candidate tree: `9da2b879c08573ecd8dac421bcb43785570ff1a0`
- Source archive SHA-256: `5cca00cffc6f1547fdaae5b478efa4db36830c1c1e41735e664df4c224fd399a`
- `uv.lock` SHA-256: `74141a126906894c87ab4e1bf3792bc9d8d7fc74075c7154871a6fe9d41ba21f`
- `package-lock.json` SHA-256: `b4da8dc8d7815a19b54bff1ee059c25167485708c0261f40ca6b6df2452e71e8`
- Migration manifest SHA-256: `08c517e51124d68585ba16fef9791afb5a73c90f883b128790327ad0bc899c35`
- V2 surface manifest SHA-256: `0857b0642c49531ae615362b7a40785e21aa8676a933ea6a27a5f1f057e11c66`
- V2 acceptance fixture SHA-256: `4c65c56475532f43e84bc89d7c226046d0fa67996c21d1988522fa463b0f7775`
- V2 component map SHA-256: `a632d9b2c9a1ac436c5cba165e543d2c1bc7ea8c509e6d8920261cbf88136d63`

## Scope delivered

- Composed the reviewed JOIN, RUN, ART, WP, TASK, and ACT lanes through one
  explicit FastAPI compatibility adapter.
- Mounted the frozen 21-operation V2 inventory, including the two existing
  canonical base operations and 19 adapted feature operations.
- Added duplicate operation, method drift, path collision, and canonical
  path-parameter checks before startup.
- Imported the reviewed contiguous migration prefix 0020 through 0028 and
  aligned lane migration tests to that prefix without changing SQL semantics.
- Preserved the governed corpus projection and retrieval migration as a
  separate reviewed lane.
- Kept all behavior public-synthetic, simulation-only, fail-closed, and
  provider-neutral. No protected Matter data, provider request, credential,
  deployment, runtime, or external action occurred.

## Verification

- Central composition and route adaptation: `37 passed, 37 subtests passed`.
- API, browser-session, and feature non-PostgreSQL suite: `306 passed, 13 warnings`.
- Disposable PostgreSQL feature qualification: `20 passed`.
- Migration preflight: `7 passed, 9 subtests passed`.
- Deterministic PostgreSQL adapters: `30 passed`.
- Foundation and development contracts: `24 passed, 60 subtests passed`.
- Load-saturation smoke qualification: `24 passed`.
- Web Vitest: `24 files, 257 tests passed`.
- Web ESLint, TypeScript build, Prettier check, and Vite production build: PASS.
- Ruff, Python compilation, and `git diff --check`: PASS.

The initial broad non-integration Python run recorded `2269 passed, 24
failed, 944 subtests passed`. Three c1 contract failures were repaired and
then passed in the focused `37 passed` rerun. Load-saturation was rerun after
locking the complete workspace environment and passed all 24 tests. The only
remaining rechecked failure is environment-only: the approved external
HammerTime style source locator is absent. That gate was not weakened or
skipped.

## Known limitations

- Full repository integration orchestration, real browser qualification, and
  independent end-to-end review remain open work.
- Card `13473fb6` remains the required independent review and is not claimed
  by this implementation.
- The c1 TDD prohibits merge, push, deployment, and broad cleanup. This branch
  therefore remains local and unpushed.

## Rollback

Revert the c1-owned commits after accepted base `0a293dd`, including the
integration implementation, compatibility repairs, and evidence-only commits,
to return to that base. This removes only the integration adapter, reviewed
lane copies, contiguous migration copies, compatibility tests, fixture pin
repair, and this evidence. No database, service, runtime, credential, provider,
protected-content, or external state was changed.

## Final composed candidate

Composition date: 2026-08-27
Composition disposition: PASS

The exact independently reviewed repair commits were applied in this order:

- Secret classification: `d970fe4a20386568d3ccd89d4ea76beac2f31df7`, independently PASS-reviewed by `66784c37`, bundle `20b99191e7345d4aa98baf2c30f52cc21a6074cb758e93c552b7a4a43e3bcbb8`.
- Party role and fixture repair: `f187122389f9468ac31e40a67c4ad3613d76d3b8`.
- Fixture and schema guard reconciliation: `b4a76dd652c092afb0ad09081e2eb1160c3b7b82`.
- Fixture reconciliation evidence: `36b84ef1021b9a012298767bf67e2de04799482b`.
- Hermetic order repair: `4b9fcad20899735a4a631ea3ab35d4d70a1a46b7`.
- Hermetic repair evidence: `751a85ec20ee2da607e7220a5d9c2205a5175113`.
- Hermetic chain independent PASS: `39979393`, bundle `308ec2e50b3973b815f0916ea0da4e0a1da34cde6d0a81330a08e403af1db3d4`.

The composed source candidate before this evidence update is commit
`59ec7c486699d15f7ae6d8460be68c7a82818f50`, tree
`10ea8249feb9175ef160c0ceb5cbfc7c2969800f`. The branch remains local and
unmerged.

## Final decisive gates

- API and public-synthetic suite: `81 passed, 12 warnings`.
- Combined canonical parity and migration guard suite: `4 passed, 37 subtests passed`.
- Explicit reversed canonical parity order: `2 passed, 37 subtests passed`.
- RLS, policy, CapAuth, migration, replay, scope, and privilege suite: `15 passed, 19 subtests passed`.
- Explicit isolated and order-sensitive parity, migration, rollback, and audit checks: `5 passed, 43 subtests passed`.
- Full c1 integration suite: `35 passed, 53 subtests passed`.
- Migration manifest: `PASS`, `28 migration(s)`.
- Fixture safety: `PASS`, `17 file(s)`.
- Secret scan: `PASS`, no findings outside the reviewed baseline.
- Vulnerability scan: `PASS`, no known vulnerabilities; vendored CapAuth verified.
- Ruff check: `PASS` on all touched Python files.
- Python compile check: `PASS`.
- `git diff --check`: `PASS`.

The broad Ruff format check was run. The three persistence files pass; the
existing `tests/test_secret_baseline.py` has pre-existing formatting
differences. No unreviewed formatting rewrite was added to the exact reviewed
secret-classification repair.

The original `95e3db49` BLOCKED review and its bundle
`c15b37eb95f89fb39cb820a788a6ca28620b99fd3ab5c9e66dc0e0bfbc075d95` remain
preserved. Its successor chain independently PASSed and does not rewrite the
historical verdict. No deployment, restart, protected data, provider traffic,
credentials, HammerTime Inbox access, external action, merge, push, unrelated
cleanup, or `431db4dd` interaction occurred.

Evidence payload SHA-256: `2b442bdbb0195d70bc9f35d7b5379717f6dd2e50125eb777e251da280c1b686b`

## Final rollback

Revert the six composed repair and evidence commits listed above to return this
branch to refresh commit `7638faf7f447d9db29f1f99c69cc54acf889576b`. This is a
local Git rollback only. It changes no database, service, runtime, credential,
provider, protected-content, HammerTime, or external state.
