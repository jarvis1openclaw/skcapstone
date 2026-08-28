# SKL-MVP-INTEG-01F1A fixture and schema guard evidence

Card: `0dd8dde0`
Owner: `codex-luna-0dd8dde0`
Worktree: `/tmp/sklegal-0dd8dde0-fixture-reconcile`
Base commit: `f187122389f9468ac31e40a67c4ad3613d76d3b8`
Base tree: `af39e07fd03d7a8c075a412b526a3a9f8b0a6b59`
Candidate commit: `b4a76dd652c092afb0ad09081e2eb1160c3b7b82`
Candidate tree: `fc1dd0248df1105fb80d5a7310d3674c92655335`
Parent of candidate: `f187122389f9468ac31e40a67c4ad3613d76d3b8`
Verdict: `PASS`

## Scope

This candidate changes only the canonical PostgreSQL test harness. It seeds
one valid synthetic Matter alias and one append-only Authority version 2 after
the write-first fixture exists. It also enumerates the exact ten schemas
created by the reviewed migration set. The Party role remains aligned with
the fresh tenant and Matter owner role `sklegal_test_fresh_writer`.

The original `069044d1` BLOCKED evidence remains preserved. No production
migration semantics, RLS policy, tenant or Matter isolation, CapAuth contract,
append-only rule, database outside the disposable test container, runtime,
credential, provider, HammerTime Inbox, deployment, merge, push, or external
state was changed.

## Changed files and hashes

- `tests/integration/persistence_contract_canonical_parity.py`
  SHA-256 `e863dfe4b3d5a9c09f5aa53585fb6ee1cb7a004d3a64259c0cac5f2ae9775661`
- `tests/integration/persistence_contract_migration_guards.py`
  SHA-256 `05b19971f2c59949484648b031cc553c00f4a9d484f33c2230628c41e8f5989f`

No file under `migrations/`, `packages/`, `services/`, or `apps/` changed.

## Reproduction before repair

Command:

```text
.tools/bin/uv run --locked --all-packages pytest -q tests/integration/persistence_contract_canonical_parity.py tests/integration/persistence_contract_migration_guards.py -vv
```

Result before repair: `2 passed, 2 failed, 37 subtests passed`.

The failures were the missing Matter alias metadata input at the unchanged
assertion and the six-schema expectation against the four reviewed feature
schemas already present in the candidate. The Party row visibility and audit
rollback checks were already passing.

## Verification

Canonical parity and schema isolation:

```text
.tools/bin/uv run --locked --all-packages pytest -q tests/integration/persistence_contract_canonical_parity.py tests/integration/persistence_contract_migration_guards.py -vv
```

Result: `4 passed, 37 subtests passed in 47.32s`.

RLS, CapAuth, migration up/down, replay, and scope privilege:

```text
.tools/bin/uv run --locked --all-packages pytest -q tests/integration/persistence_contract_security_boundary.py::PersistenceContract01SecurityBoundaryTests::test_01_domain_parity_matrix_and_forced_rls tests/integration/persistence_contract_migrations.py tests/integration/persistence_contract_capauth_replay.py tests/integration/test_capauth_contract.py tests/integration/persistence_contract_scope_privilege.py -vv
```

Result: `13 passed, 11 subtests passed in 44.00s`.

Alias and append-only Authority history regressions:

```text
.tools/bin/uv run --locked --all-packages pytest -q tests/integration/persistence_contract_matter_records.py -k 'test_05_strict_legacy_alias_binding_and_metadata or test_06_append_only_tensions_and_database_authority_history' -vv
```

Result: `2 passed, 20 deselected in 36.83s`.

Static, migration, diff, and Unicode dash checks:

```text
.tools/bin/uv run --locked --all-packages ruff check tests/integration/persistence_contract_canonical_parity.py tests/integration/persistence_contract_migration_guards.py
.tools/bin/uv run --locked --all-packages ruff format --check tests/integration/persistence_contract_canonical_parity.py tests/integration/persistence_contract_migration_guards.py
.tools/bin/uv run --locked --all-packages python scripts/check_migrations.py
git diff --check
```

Results: Ruff passed, both files already formatted, migration manifest valid
with `28 migration(s)`, diff check passed, and Unicode dash check found none.

## Limitations and safe state

The checks use synthetic data in disposable networkless PostgreSQL containers.
They do not authorize production deployment, migration, merge, push, restart,
protected data, credentials, provider traffic, HammerTime Inbox processing,
external action, or work on `431db4dd`.

## Rollback

Revert the local commit containing this evidence and candidate source commit
`b4a76dd652c092afb0ad09081e2eb1160c3b7b82` to restore exact base
`f187122389f9468ac31e40a67c4ad3613d76d3b8`. The disposable PostgreSQL
container is torn down by the test harness; no data or host rollback exists.
