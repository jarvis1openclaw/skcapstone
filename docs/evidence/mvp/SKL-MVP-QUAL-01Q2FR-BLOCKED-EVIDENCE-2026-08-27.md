# SKL-MVP-QUAL-01Q2FR independent review evidence

Card: `a4ac0192`

Reviewer: `codex-sol-a4ac0192`

Verdict: `BLOCKED`

## Decisive finding

The repaired durable factory exposes all 21 frozen OpenAPI operations, but 17
of those operations cannot receive a request-bound capability from the
production browser session. They therefore cannot execute against the durable
PostgreSQL stores through the reviewed production composition.

`DurableBrowserSessions._capabilities()` defines eight request pairs. Only four
belong to the frozen 21-operation contract: workspace, Claim ledger, governed
corpus search, and governed corpus span. `BrowserSessionAuthentication.presented_for()`
performs an exact method and path lookup and returns no capability when the pair
is absent.

A live review hook ran inside the real reversible Chrome and dual-PostgreSQL
qualification before teardown. All 17 newly mounted operations were
inaccessible: 16 returned HTTP 403 and `decide_approval` returned HTTP 503.
The stock qualification still returned PASS because it inventories the 21
OpenAPI triples but never sends requests to those 17 operations.

This fails these card acceptance requirements:

- all 21 frozen operations execute against real separate core and retrieval PostgreSQL
- CapAuth targets remain correctly bound and fail closed without making authorized operations unreachable
- focused browser, API, and PostgreSQL tests cover the mounted durable operation path

No implementation source was repaired.

## Pinned custody and hashes

- Reviewed evidence commit: `278d7af5277f95c2489c15ad7288009a7d4bc897`
- Reviewed evidence tree: `68725be83f7a7429490d0d863004dbfab00761cb`
- Implementation commit: `d2938d353ea77655a77d99107b04b2930c0f756e`
- Implementation tree: `b849b3d7c38d292e0749b097111dc3601a873fcc`
- Original Q2 evidence commit: `c9a4d6d73eee625af243c6b8fc964bf8bf77b81f`
- Original Q2 tree: `8868912e04b1dd3cd0e83b8268872e9b599a4702`
- Reviewed evidence source archive SHA-256: `ce40aa8dea238b6247e0bce1b458efcb9ee7b6731e5a4283b036cf4f189dd7e2`
- Implementation source archive SHA-256: `f9f39b5a59126870c2b71a94164b8268a2f1861e36f564536e3e9e48d1051ec1`
- Original Q2 source archive SHA-256: `9e610a7d8d2d30ce17bcc3d093bdd8a257e3c462ae06d74f3964009d0ba86c54`
- Frozen V2 manifest SHA-256: `0857b0642c49531ae615362b7a40785e21aa8676a933ea6a27a5f1f057e11c66`
- Current canonical OpenAPI SHA-256: `1555cec46c8143e0c4f69210b8ccf0f91ea90a9cea62166d645893462fb820b6`
- Producer qualification result SHA-256: `75315583e85ef472fca13d0ca6456267d61c5073b961e96517c6e9504b19fd27`
- Independent stock qualification result SHA-256: `00e279ac2dbc16858045821fcfa857330cff373850f806e4e3e6fb513f4041b3`
- Independent authorization-probe qualification result SHA-256: `7a823a9d85ebb090fbcaf9667f95ed86cfbf0ca8fbc5b80e2f10e57930b713bc`
- Core migration manifest SHA-256: `08c517e51124d68585ba16fef9791afb5a73c90f883b128790327ad0bc899c35`
- Retrieval migration manifest SHA-256: `9d5b0c8118db9d4b56b9fc3ec432800833bcc70f9693bc9cc7bf323215f4cdf6`
- Migration bootstrap and manifest aggregate SHA-256: `75f8aacc924039a5b99166e03726f98b865ec19c4042c6c4842ac8f6384776ed`
- Changed implementation file aggregate SHA-256: `3414457a37d8511add83721ece3f212d2c793ac599f11bec6ad85edc76387268`

The producer result still records card `06a2686f`. This is a provenance defect
in the qualification script and does not identify this repair or review card.

## Original Q2 gap reproduction

The original `c9a4d6d` bytes were extracted into a disposable directory and
qualified with real Chrome and separate core and retrieval PostgreSQL. The
independent OpenAPI comparison produced:

- required operations: `21`
- exact matches: `0`
- absent required method and path pairs: `17`
- existing pairs with legacy operation IDs: `4`
- legacy IDs: `claims_ledger`, `corpus_span`, `workspace_matters_workspace`, and `corpus_search`
- independently regenerated original OpenAPI SHA-256: `b318210749206ffcf6fa54744f9baacef47df3479e2d7bfb59068422cd7cc2f1`

The structural result exactly reproduces the preserved gap. The regenerated
original OpenAPI hash differs from the preserved report value
`21daadaf38e4f38c12b37aef6b2f21b26e97ad0bc9de8e3a435b41f7589fd8fb`.
The extracted source bytes were exact, but the historical Python environment
was not preserved. This hash difference is recorded as a limitation.

## Passing controls

- Current OpenAPI has all 21 exact IDs, methods, and paths.
- Operation IDs and method/path pairs are unique.
- Compatibility composition rejects duplicate source operations and duplicate V2 routes.
- No legacy corpus router is mounted when feature routers are present.
- Production composition rejects in-memory workspace, Claim, corpus, CapAuth, policy, and session stores.
- All seven feature router groups bind PostgreSQL repositories.
- Core and retrieval use separate databases, admin roles, application roles, networks, volumes, ports, restart paths, backup paths, and restore paths.
- Canonical state remains in core; governed retrieval projections remain rebuildable in retrieval.
- Real qualification passed Tenant and Matter isolation, RLS, stale policy, revocation persistence, core restart, retrieval outage, core outage, projection lag, deterministic rebuild, backup/restore, reset/reseed, migration replay, Chrome CSP, browser storage, and exact teardown.
- Changed-source secret scan reported zero findings.
- Repository-wide secret scan remains red with 50 hash findings and one metadata-keyword finding in committed historical evidence. No credential or private-key detector fired.

## Exact commands and results

1. Required startup:
   `"${CODEX_HOME:-$HOME/.codex}/bin/load-sk-agent-context.sh"`,
   `skcapstone coord status`, and `skcapstone coord briefing`.
   Active profile was Jarvis. CardStore proved the claim owner was
   `codex-sol-a4ac0192` and dependency `36ce9b05` was complete.
2. Focused feature command:
   `PYTHONPATH=<all workspace src roots> python -m pytest -q tests/test_durable_mvp_composition.py tests/features/joined_analysis tests/features/agent_runs tests/features/artifact_intake tests/features/work_products tests/features/task_deadlines tests/features/matter_activity tests/features/governed_corpus`.
   First run: `343 passed, 4 setup errors` because `.tools/bin/uv` was absent.
   Full rerun with a removable symlink to the existing locked tool:
   `347 passed, 1 warning` in `75.95s`.
3. Contract and route composition:
   `python -m pytest -q tests/test_mvp_integration.py tests/contracts/test_v2_mvp_contract_freeze.py`.
   Result: `44 passed`.
4. Web tests:
   `npm test --workspace @sklegal/web -- --run`.
   Result: `24 passed files`, `258 passed tests`.
5. Web lint and typecheck:
   `npm run lint --workspace @sklegal/web` and
   `npm run typecheck --workspace @sklegal/web`.
   Result: PASS.
6. Real qualification:
   `PYTHONPATH=<all workspace src roots> python scripts/qualify_durable_mvp.py --output .review-a4ac0192-qualification.json`.
   Result: PASS in `18.321s`, followed by exact container and volume teardown.
7. Review-only live operation probe:
   the same qualifier was invoked through an inline wrapper that hooked
   `_v2_openapi_inventory`, called the 17 operations with the authenticated
   browser session, and then allowed the stock qualification to finish.
   Result: stock PASS in `20.537s`; operation access `16 x 403`, `1 x 503`.
8. Migration manifest:
   `.tools/bin/uv run --locked python scripts/check_migrations.py`.
   Result: `migration manifest valid: 28 migration(s)`.
9. Ruff:
   `.tools/bin/uv run --locked ruff check scripts/qualify_durable_mvp.py services/api/src/sklegal_api/durable_feature_routers.py services/api/src/sklegal_api/durable_public_synthetic.py`.
   Result: PASS.
10. MyPy:
    the raw external-package run reported 14 `import-untyped` errors only.
    The documented external-workspace mode used
    `--follow-imports=skip --ignore-missing-imports` and passed all three files.
11. Secret hygiene:
    changed implementation source scan result: `0` findings.
    Exact candidate repository scan result: FAIL with `50` hex-hash findings
    and `1` metadata keyword in committed evidence.
12. Diff checks:
    `git diff --check c9a4d6d d2938d3` and `git diff --check` both passed.

The producer evidence states `400 passed`, but it does not record the exact
test command. That exact selection could not be independently reconstructed.
The complete reproducible selection above passed and is preserved without
claiming equivalence to an undocumented command.

## Rollback and safe state

No implementation source, migration, production data, credential, protected
content, provider, deployment, external action, or card `431db4dd` was touched.

The two stock qualifications and the original-gap qualification each stopped
their API, web, Chrome, core PostgreSQL, and retrieval PostgreSQL processes and
removed only their exact named containers and volumes. The extracted original
source, qualification JSON files, locked-tool symlinks, Python environment
symlink, and Node dependency symlink were removed. Final inspection found no
task-named container, volume, process, or untracked file before this evidence
was added.

Rollback is limited to reverting the independent evidence commit. Card
`a4ac0192` must remain open until a separately authorized repair supplies exact
request-bound CapAuth credentials for all frozen operations and a successor
qualification executes all 21 operations against the durable stores.
