# SKL-MVP-01 internal API composition evidence

Date: 2026-08-23
Card: `bb5cb31d`
Owner: `codex-skl-mvp-api`
Worktree: `/tmp/sklegal-mvp-bb5cb31d`
Branch: `swarm/bb5cb31d-internal-api`
Verdict: PASS for isolated implementation review

## Scope

This candidate composes the reviewed SKLegal workspace, claim ledger, corpus research, and human governance routers into one internal public-synthetic FastAPI application. It performs no deployment, provider request, protected Matter access, HammerTime `Inbox/` access, production credential operation, or external action.

## Files changed

- `docs/tasks/SUBAGENT-TASK-TTDS.md`
- `services/api/src/sklegal_api/app.py`
- `services/api/src/sklegal_api/workspace.py`
- `tests/test_api_app.py`
- `docs/evidence/mvp/SKL-MVP-01-INTERNAL-API-COMPOSITION-2026-08-23.md`

## Acceptance evidence

- The explicit `create_mvp_app` factory mounts only the reviewed workspace, claim ledger, corpus research, and governance routers.
- Authentication, policy, audit, persistence, and provenance probes are mandatory, unique, and ready at construction and application startup.
- Production mode rejects synthetic dependency probes and the known in-memory workspace, claim, and corpus stores.
- Development mode must be selected explicitly and is limited to the deterministic public-synthetic adapters supplied by the caller.
- `/healthz` uses a fixed 100 millisecond total budget, returns only named component state, and reports `503 unavailable` on outage or timeout.
- Middleware accepts only UUID correlation identifiers, generates a correlation identifier when absent or invalid, returns it on every response, and converts unhandled failures to a sanitized unavailable response.
- Client and Matter lists accept bounded `offset` and `limit` parameters with a maximum page size of 100.
- Missing authentication is denied before workspace lookup. Existing API boundary tests continue to cover expired, revoked, wrong-Tenant, wrong-Matter, membership, audit, policy, and backend outage behavior.

## Verification

Focused API boundary suite:

```text
97 passed, 12 warnings, 64 subtests passed in 2.84s
```

Command:

```text
PYTHONPATH=services/api/src:packages/capauth/src:packages/policies/src:packages/audit/src:packages/retrieval/src:packages/migration/src:packages/model_gateway/src /mnt/cloud/onedrive/projects/DAVE-AI/sklegal/.venv/bin/python -m pytest -q tests/test_api_app.py tests/test_api_workspace.py tests/test_api_claims.py tests/test_api_corpus.py tests/test_api_governance.py tests/test_capauth_boundaries.py tests/test_api_capability_attack_battery.py tests/test_api_capauth_composition_health.py
```

Static checks:

```text
ruff check: PASS
ruff format --check: PASS
mypy --ignore-missing-imports services/api/src/sklegal_api/app.py: PASS
git diff --check: PASS
ASCII dash scan: PASS
changed-file detect-secrets scan: 0 findings
```

The 12 warnings are the pre-existing governance router's internal command-class defaults being excluded from generated Pydantic schemas. They do not change the tested operation inventory or runtime request contracts. Repairing that router implementation was outside the clean-isolation file list authorized after the collision and is deferred to review or a separately owned patch.

The attempted full repository test run was not a valid candidate failure signal. Host-backed integration tests require a worktree-local `.tools/bin/uv`, which this isolated worktree does not contain, and `test_foundation_contract` detected the pre-existing `sklegal-dev-postgres-1` and `sklegal-dev-temporal-1` containers. The run was stopped after `1 failed, 28 passed, 15 skipped, 61 errors, 51 subtests passed`. The one exact temporary backup-test container and its three volumes created by this run were removed. The older container and development stack were not changed.

## Known limitations

- This card composes existing reviewed routers. Dedicated Task, Deadline, Work Product mutation, Approval, and audit replay routers do not yet exist in this source revision and were not invented here. Existing workspace aggregates expose Work Products, execution states, audit entries, and provenance read-only. The successor Matter workbench card must mark unavailable capabilities explicitly until reviewed endpoints exist.
- Dependency probes are adapter-owned bounded checks. The HTTP health boundary enforces its total response budget, but a timed-out synchronous probe may finish in its worker thread after the unavailable response.
- No durable production adapter composition is included. Production startup therefore remains fail closed until later deployment work supplies and qualifies those adapters.
- The candidate is sealed in the immutable card commit linked from `bb5cb31d`. It is not merged, pushed, or deployed by this card execution.

## Rollback

Rollback is byte-local and data-free: revert the immutable card commit, which removes `app.py`, `test_api_app.py`, this evidence file, and the exact TDD section and restores the prior unpaginated workspace list handlers. No database, credential, service, provider, consumer, host, or external state changed.
