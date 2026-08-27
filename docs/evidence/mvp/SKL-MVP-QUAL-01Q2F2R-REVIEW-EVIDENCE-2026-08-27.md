# SKL-MVP-QUAL-01Q2F2R independent rereview evidence

Card: `3f56814c`

Reviewer: `codex-sol-3f56814c`

Profile: Jarvis

Verdict: `PASS`

## Decision

The repaired candidate closes the exact `a4ac0192` authorization failure. All
21 frozen V2 operations execute through the production browser session and
request-local CapAuth path against separate real core and retrieval PostgreSQL
stores. Every observed allowed decision uses the canonical
`api:<operation_id>` target and binds the expected principal, Tenant, Matter,
concrete resource, capability, purpose, operation, principal-policy revision,
and revocation revision. The Approval decision additionally binds the trusted
current Work Product version and content SHA-256. No wildcard, prefix, ambient,
arbitrary-path, or in-memory production grant was found.

The review changed no product source, test, script, migration, configuration,
or existing evidence.

## Exact custody

- Candidate evidence commit: `76a293802259bf66239fe780c9f7a20f4a993fda`
- Candidate evidence tree: `6dc1d5f233a88fd18091d54b8225dc5fe30e3008`
- Candidate evidence archive SHA-256: `be7415419ad25d1f31adc43b98870679283ef79e40e2e3238c07666bf30f700f`
- Implementation commit: `89c5e0e77bf2fcc362d66442b961439528583ace`
- Implementation tree: `dbf076fca56794efae6278c8c3cba5921f9b28f3`
- Implementation archive SHA-256: `82d709c35551120facaae1abed2bb1c0beaa8c0b04eb8750d970a12e2fa2833b`
- Preserved failed review commit: `9b21c8878b9e62faf46b1145293218181c64a4a2`
- Preserved failed review tree: `b060621ab012fae9054961f16abdce3593390d20`

## Preserved failure reproduction

The exact prior candidate evidence commit `278d7af5277f95c2489c15ad7288009a7d4bc897`,
tree `68725be83f7a7429490d0d863004dbfab00761cb`, and implementation
`d2938d353ea77655a77d99107b04b2930c0f756e` were run in a detached temporary
worktree. A process-local hook at the prior qualifier's OpenAPI checkpoint made
no file change and preserved normal teardown.

The prior stock qualifier returned PASS, but the 17 operations named by
`a4ac0192` reproduced exactly as 16 HTTP 403 responses and one HTTP 503 for
`decide_approval`. Denial codes were `capability_denied` or `access_denied`,
and the Approval lane returned `application_unavailable`. This proves the old
stock PASS did not execute the missing operations.

- Prior stock result SHA-256: `3989f08a4886db837e9fa86f3084f818def2810c9c40f2b49aa3f6ec5261a2e6`
- Prior evidence archive SHA-256: `ce40aa8dea238b6247e0bce1b458efcb9ee7b6731e5a4283b036cf4f189dd7e2`
- Prior implementation archive SHA-256: `f9f39b5a59126870c2b71a94164b8268a2f1861e36f564536e3e9e48d1051ec1`

## Live repaired-candidate proof

The unmodified stock qualifier ran from the exact candidate with a fresh output
path and returned PASS. All 21 operations returned HTTP 200 or 201. Ten
mutation lanes replayed idempotently. Matter activity export created its first
receipt and then returned the expected sanitized decision-bound conflict.

A second full qualification used a review-only process-local hook. It checked
39 allowed CapAuth audit decisions and observed all 21 canonical targets. It
validated exact target, Tenant, Matter, resource, capability, purpose,
operation, principal-policy revision, and revocation revision fields. Approval
bound Work Product version 1 and its exact content SHA-256. The hook issued a
fresh exact workspace credential, inserted only its digest into the real core
PostgreSQL revocation table, received HTTP 403 `capability_denied` through the
protected browser-session request path, and removed exactly that row before the
stock qualifier continued.

Both repaired-candidate qualifications passed Chrome reload and CSP, separate
core and retrieval roles, networks and volumes, RLS, cross-Tenant and
cross-Matter denial, stale policy, retrieval outage, core outage, core restart,
projection rebuild, backup and restore, reset and reseed, fresh migration
replay, browser storage, and exact container and volume teardown.

- Frozen manifest SHA-256: `0857b0642c49531ae615362b7a40785e21aa8676a933ea6a27a5f1f057e11c66`
- OpenAPI SHA-256: `1555cec46c8143e0c4f69210b8ccf0f91ea90a9cea62166d645893462fb820b6`
- Independent stock result SHA-256: `103652e60315e0ac80815bf02b0267f2471dbb998929c90714c6a00a6f28a53d`
- Independent live authorization result SHA-256: `742756763c0b75882f6370c41fbf68c50edcd141e1e790d282f55a5abfc00e40`

## Exact commands and results

1. Required startup:
   `SKAGENT=jarvis "${CODEX_HOME:-$HOME/.codex}/bin/load-sk-agent-context.sh"`,
   `skcapstone coord status`, and `skcapstone coord briefing`.
   Result: Jarvis active; card `3f56814c` in progress and claimed by
   `codex-sol-3f56814c`; dependency `c0bc2c68` complete.
2. Prior candidate qualification:
   `PYTHONPATH=.:$(find packages services vendor -type d -name src -print | paste -sd:) /mnt/cloud/onedrive/projects/DAVE-AI/sklegal/.venv/bin/python -`
   from detached `278d7af`, with the review hook and output
   `SKL-MVP-QUAL-01Q2F2R-PRIOR-STOCK-2026-08-27.json`.
   Result: stock PASS, live failure reproduced as 16 x 403 and 1 x 503.
3. Focused request-capability composition:
   `pytest -q tests/test_v2_request_capabilities.py tests/test_mvp_integration.py tests/test_browser_sessions.py tests/test_durable_mvp_composition.py`.
   Result: `20 passed in 0.75s`.
4. CapAuth and owning API boundaries:
   `pytest -q tests/test_capauth_*.py tests/test_api_capauth_composition.py tests/test_api_capauth_composition_health.py tests/features/agent_runs tests/features/matter_activity`.
   Result: `174 passed, 97 subtests passed in 17.33s`.
5. Durable feature owners:
   `pytest -q tests/test_durable_mvp_composition.py tests/features/joined_analysis tests/features/agent_runs tests/features/artifact_intake tests/features/work_products tests/features/task_deadlines tests/features/matter_activity tests/features/governed_corpus`.
   Result after adding the removable locked-tool link: `348 passed, 1 warning in 71.05s`.
   The warning is the inherited Starlette 422 constant deprecation. The first
   attempt had `344 passed, 4 setup errors` because `.tools/bin/uv` was absent.
6. Web:
   `npm test --workspace @sklegal/web -- --run`,
   `npm run lint --workspace @sklegal/web`, and
   `npm run typecheck --workspace @sklegal/web`.
   Result after adding the removable dependency link: `24 passed files`,
   `258 passed tests`, lint PASS, typecheck PASS. The first test attempt stopped
   before collection because `vitest` was absent from the isolated worktree.
7. Migration and fixture safety:
   `.tools/bin/uv run --locked python scripts/check_migrations.py` and
   `.tools/bin/uv run --locked python scripts/check_fixture_safety.py`.
   Result: `28` migrations valid and `17` fixture files valid.
8. Stock repaired qualifier:
   `PYTHONPATH=.:$(find packages services vendor -type d -name src -print | paste -sd:) /mnt/cloud/onedrive/projects/DAVE-AI/sklegal/.venv/bin/python scripts/qualify_durable_mvp.py --output docs/evidence/mvp/SKL-MVP-QUAL-01Q2F2R-STOCK-2026-08-27.json`.
   Result: PASS on commit `76a2938`, tree `6dc1d5f`, all 21 operations, exact
   safe-state flags true.
9. Live authorization and revocation qualification:
   the same qualifier was invoked through `python -` with process-local wrappers
   around `_gpg_identity`, `_execute_v2_operations`, and `_feature_readback`.
   Result: PASS; 39 allowed decisions, 21 targets, exact fields, exact Approval
   binding, revoked live credential denied 403, test row removed.
10. Static checks:
    `ruff check`, `ruff format --check`, and `python -m py_compile` over all 17
    changed Python files; `git diff --check 9b21c88 89c5e0e`; changed-file
    Unicode dash and forbidden-card scans.
    Result: PASS.
11. Secret hygiene:
    `detect-secrets scan --all-files <18 changed implementation files>`.
    Result: zero findings. `pytest -q tests/test_secret_baseline.py` returned
    `4 passed, 6 subtests passed`. The official repository gate
    `python scripts/check_secrets.py` remains inherited red with 103 additions:
    102 immutable evidence hashes and one metadata keyword. No baseline was
    changed.

## Hash recomputation

- Core migration manifest: `08c517e51124d68585ba16fef9791afb5a73c90f883b128790327ad0bc899c35`
- Retrieval migration manifest: `9d5b0c8118db9d4b56b9fc3ec432800833bcc70f9693bc9cc7bf323215f4cdf6`
- Core bootstrap: `9099bbbe4a79954f49d4f9d8e0d85aa506debddbc848d2b9d915a2a8d7e3b90b`
- Retrieval bootstrap: `638143b357337cd2e9dc1ad1040b90a260da9102b64ec107c2000f4256b6974f`
- Ordered migration and bootstrap aggregate: `e269a2358a5c032996e64b5e070a25967639a530679a364fac64725b681248cd`
- Ordered changed implementation file aggregate: `b745b82cd68a0ab6e109a92ecfd2b67defe36eac13ad4a7684e793db54a7e81d`
- Producer qualification: `975f99b0a8e99dca666d0855f62055e8a2696e21e68585db27ee45bf5acb5d7d`
- Producer report: `7e2fb3821ee58c8c22a5aff9fc2b9dbb214bc9cc22eacac883b6ef56d1ad4106`

## Limitations

- Public-synthetic corpus only.
- Connectors remain simulation-only.
- Optional AGE remains activation-gated.
- The official repository secret baseline is inherited red as described above.
  Changed implementation files are clean and planted secret classes remain
  detected.
- The repository TDD predates this card-specific section. CardStore acceptance
  criteria supplement the durable integration and independent review TDD.

## Rollback and safe state

Rollback is limited to reverting this review evidence commit. No implementation
or data rollback exists because source and non-test data were not changed.

All exact qualifier containers and volumes are absent. The generated root
`node_modules` and `.tools` links were unlinked, the task-created `.venv` and
web `dist` were moved to recoverable trash, and the detached prior-candidate
worktree was removed. The one review revocation row was deleted by exact digest.
No product source, migration, existing evidence, protected content, provider,
credential, deployment, external action, merge, push, broad cleanup, or card
`431db4dd` interaction occurred.
