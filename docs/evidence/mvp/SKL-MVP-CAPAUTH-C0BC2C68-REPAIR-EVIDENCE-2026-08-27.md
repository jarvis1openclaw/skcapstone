# SKL-MVP CapAuth repair evidence

Card: `c0bc2c68`

Claim owner: `codex-sol-c0bc2c68`

Finding repaired: `a4ac0192`

Verdict: `PASS`

## Exact candidate

- Preserved BLOCKED base: `9b21c8878b9e62faf46b1145293218181c64a4a2`
- Implementation commit: `89c5e0e77bf2fcc362d66442b961439528583ace`
- Implementation tree: `dbf076fca56794efae6278c8c3cba5921f9b28f3`
- Qualification result: `docs/evidence/mvp/SKL-MVP-CAPAUTH-C0BC2C68-QUALIFICATION-2026-08-27.json`
- Qualification result SHA-256: `975f99b0a8e99dca666d0855f62055e8a2696e21e68585db27ee45bf5acb5d7d`
- Frozen V2 manifest SHA-256: `0857b0642c49531ae615362b7a40785e21aa8676a933ea6a27a5f1f057e11c66`
- Candidate OpenAPI SHA-256: `1555cec46c8143e0c4f69210b8ccf0f91ea90a9cea62166d645893462fb820b6`

## Security boundary and repair

The preserved browser session supplied exact capabilities for only four frozen
operations. The other 17 operations reached protected dependencies without a
matching signed request capability and returned 403 or 503.

The repair adds one reviewed registry for all 21 canonical operation IDs,
methods, and complete route templates. Each issued grant binds the canonical
`api:<operation_id>` target, exact method and route-template match, Tenant,
Matter, resource type, concrete resource identifier, capability, and purpose.
Routes must fully match and every UUID route parameter must use canonical UUID
text. Unknown methods, extra path components, invalid UUIDs, and unreviewed
routes receive no grant. No wildcard, route-prefix, ambient, arbitrary-path, or
in-memory production grant store was added.

`decide_approval` resolves the path Work Product and version IDs against the
current aggregate in PostgreSQL before issuance. The grant and protected scope
both bind the trusted current version number and content SHA-256. An absent,
stale, cross-scope, or mismatched Work Product or version returns no row and
fails closed. A durable-store failure also fails closed.

Canonical composition now rebinds legacy feature dependencies to the frozen
manifest operation ID, capability, and purpose. The reviewed registry is the
single source used by composition, avoiding the prior manifest dictionary
shape mismatch. `audit.read` now requires Matter scope. CapAuth revocation and
Work Product authorization use the same canonical durable PostgreSQL snapshot.

## Files changed

- `deploy/chiap01/mvp/core/001-core.sh`
- `packages/capauth/src/sklegal_capauth/models.py`
- `packages/persistence/src/sklegal_persistence/features/agent_runs/postgres.py`
- `scripts/qualify_durable_mvp.py`
- `services/api/src/sklegal_api/browser_sessions.py`
- `services/api/src/sklegal_api/capauth.py`
- `services/api/src/sklegal_api/durable_feature_routers.py`
- `services/api/src/sklegal_api/durable_public_synthetic.py`
- `services/api/src/sklegal_api/features/agent_runs/router.py`
- `services/api/src/sklegal_api/features/agent_runs/service.py`
- `services/api/src/sklegal_api/features/matter_activity/router.py`
- `services/api/src/sklegal_api/mvp_integration.py`
- `services/api/src/sklegal_api/v2_request_capabilities.py`
- `tests/features/agent_runs/test_router.py`
- `tests/features/agent_runs/test_service.py`
- `tests/test_durable_mvp_composition.py`
- `tests/test_mvp_integration.py`
- `tests/test_v2_request_capabilities.py`

The preserved `a4ac0192` evidence was not modified or erased.

## Live acceptance evidence

- All 21 frozen method, path, and canonical operation ID triples are exact and
  unique in OpenAPI.
- Every one of the 21 operations completed with 200 or 201 through the stock
  browser session and request capability path.
- Ten mutation lanes completed successful idempotent replay. Matter activity
  export created its durable receipt, then correctly returned a sanitized 409
  for a fresh decision reusing the decision-bound key.
- Durable readback proved authorization audit, Agent Run, artifact, Work
  Product, Task and Deadline, and activity idempotency, audit, and outbox rows.
- Cross-Tenant and cross-Matter requests denied.
- Stale policy, retrieval outage, core outage, and revoked browser session
  failed closed.
- Core restart recovered. Projection rebuild was deterministic and idempotent.
- Core and retrieval backup and restore both passed.
- Reset and reseed and fresh migration replay reproduced the pinned state.
- Chrome reload and CSP qualification passed using loopback only.
- Final cleanup proved task containers and task volumes absent.

## Exact commands and results

1. Focused request capability and composition suite:

   `PYTHONPATH=.:$(find packages services vendor -type d -name src -print | paste -sd:) /mnt/cloud/onedrive/projects/DAVE-AI/sklegal/.venv/bin/pytest -q tests/test_v2_request_capabilities.py tests/test_mvp_integration.py tests/test_browser_sessions.py tests/test_durable_mvp_composition.py`

   Result: `20 passed in 0.70s`.

2. Reviewed durable feature suite:

   `PYTHONPATH=.:$(find packages services vendor -type d -name src -print | paste -sd:) /mnt/cloud/onedrive/projects/DAVE-AI/sklegal/.venv/bin/pytest -q tests/test_durable_mvp_composition.py tests/features/joined_analysis tests/features/agent_runs tests/features/artifact_intake tests/features/work_products tests/features/task_deadlines tests/features/matter_activity tests/features/governed_corpus`

   Result: `348 passed, 1 warning in 70.06s`. The warning is the existing
   Starlette 422 constant deprecation in the governed corpus router.

3. CapAuth, API boundary, Agent Run, and Matter activity owning suites:

   `PYTHONPATH=$(find packages services vendor -type d -name src -print | paste -sd:) /mnt/cloud/onedrive/projects/DAVE-AI/sklegal/.venv/bin/pytest -q tests/test_capauth_*.py tests/test_api_capauth_composition.py tests/test_api_capauth_composition_health.py tests/features/agent_runs tests/features/matter_activity`

   Result: `174 passed, 97 subtests passed in 18.57s`.

4. Final reversible Chrome and dual PostgreSQL qualification against the exact
   implementation commit:

   `PYTHONPATH=.:$(find packages services vendor -type d -name src -print | paste -sd:) /mnt/cloud/onedrive/projects/DAVE-AI/sklegal/.venv/bin/python scripts/qualify_durable_mvp.py --output docs/evidence/mvp/SKL-MVP-CAPAUTH-C0BC2C68-QUALIFICATION-2026-08-27.json`

   Result: `PASS` in `23.608s`, source commit
   `89c5e0e77bf2fcc362d66442b961439528583ace`, with all 21 operations
   successful and exact cleanup true.

5. Migration and fixture checks:

   `/mnt/cloud/onedrive/projects/DAVE-AI/sklegal/.tools/bin/uv run --locked python scripts/check_migrations.py`

   Result: `migration manifest valid: 28 migration(s)`.

   `/mnt/cloud/onedrive/projects/DAVE-AI/sklegal/.tools/bin/uv run --locked python scripts/check_fixture_safety.py`

   Result: `fixture safety valid: 17 file(s)`.

6. Static checks ran over all changed Python files:

   `/mnt/cloud/onedrive/projects/DAVE-AI/sklegal/.venv/bin/ruff check <changed-python-files>`

   `/mnt/cloud/onedrive/projects/DAVE-AI/sklegal/.venv/bin/ruff format --check <changed-python-files>`

   `/mnt/cloud/onedrive/projects/DAVE-AI/sklegal/.venv/bin/python -m py_compile <changed-python-files>`

   `git diff --check`

   Results: PASS. The changed-file Unicode dash and forbidden-card scans also
   returned no matches.

7. Changed-file secret scan:

   `mapfile -d '' changed < <(git ls-files -z --modified --others --exclude-standard); /mnt/cloud/onedrive/projects/DAVE-AI/sklegal/.venv/bin/detect-secrets scan --all-files "${changed[@]}"`

   Result before evidence generation: zero findings in changed source. The
   qualification JSON scan reports nine `Hex High Entropy String` findings,
   all of which are documented commit, tree, content, manifest, OpenAPI,
   backup, or projection SHA-256 evidence. No credential, token, cookie,
   capability, private-key, or provider secret detector fired. The inherited
   repository-wide baseline remains red on previously documented historical
   evidence hashes and one metadata keyword; no baseline was changed.

The coordinator independently reported `40 focused Python tests PASS in
0.79s` and Ruff PASS on the current repair tree.

A repository-wide pytest attempt without `.` in `PYTHONPATH` produced 17
collection import errors. The corrected attempt was interrupted at the status
checkpoint and did not return a result. Its 21 PID-scoped disposable test
containers and three PID-scoped test volumes were removed by exact name. This
does not reduce the 348-test owning feature gate or the complete live
qualification above.

## Rollback and limits

Code rollback is `git revert 89c5e0e77bf2fcc362d66442b961439528583ace`.
Runtime rollback stops only the exact qualification Compose project and removes
only its two named volumes. The final qualifier performed this cleanup and
recorded both safe-state flags true.

Remaining limits are public synthetic corpus only, simulated connectors only,
and optional AGE activation remains gated. Matter activity export idempotency
remains intentionally bound to its first authorization decision, so a fresh
decision reusing that key receives a sanitized 409 rather than the prior
receipt. This card authorizes no protected content, provider traffic,
credential use, non-loopback action, deployment, merge, push, external action,
broad cleanup, or interaction with unrelated cards.
