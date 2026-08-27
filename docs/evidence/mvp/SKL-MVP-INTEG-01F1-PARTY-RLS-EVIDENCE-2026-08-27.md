# SKL-MVP-INTEG-01F1 Party RLS repair evidence

Card: `069044d1`
Owner: `codex-luna-069044d1`
Worktree: `/tmp/sklegal-069044d1-party-rls`
Branch: `codex/069044d1-party-rls`
Date: `2026-08-27`
Verdict: `BLOCKED_PENDING_FIXTURE_RECONCILIATION`

## Scope

This card repaired the canonical persistence parity test's Party visibility
failure from the c1 candidate. No production RLS policy, migration SQL,
CapAuth contract, domain mapping, database, runtime, credential, provider,
HammerTime Inbox, deployment, merge, push, or external state was changed.

The original candidate was commit
`8b9553b4167a6e1acaaffbaf0f17d59892513841`, tree
`9da2b879c08573ecd8dac421bcb43785570ff1a0`. The prior refresh was commit
`7638faf7f447d9db29f1f99c69cc54acf889576b`.

## Reproduction before repair

Command:

```text
.tools/bin/uv run --locked pytest -q tests/integration/persistence_contract_canonical_parity.py -vv
```

Result before repair: `1 failed, 1 passed, 37 subtests passed`.

The failing assertion was
`PersistenceContract08CanonicalParityTests.test_11_every_domain_entity_round_trips_through_rls`
at `tests/integration/persistence_contract_canonical_parity.py:487`, with
the exact entity `Party`.

Diagnostic result:

- The test queried as `sklegal_test_alpha_one`.
- The canonical rows were written by `sklegal_test_fresh_writer` for a
  different tenant and matter.
- Under the mismatched role, `runtime_role_is_safe()` was true but
  `has_matter_membership()` and `record_is_authorized()` were false.
- The Party policy was already
  `USING (sklegal_identity.record_is_authorized(tenant_id, matter_id))` and
  `WITH CHECK (sklegal_identity.record_is_authorized(tenant_id, matter_id))`.
- PostgreSQL therefore correctly returned no Party rows. This was a fixture
  scope mismatch, not a reason to weaken RLS.

## Repair

Changed file:

- `tests/integration/persistence_contract_canonical_parity.py`

The parity round-trip now uses the exact `sklegal_test_fresh_writer` role that
owns the canonical fresh tenant and matter. The role alignment is the only
remaining code change. The alias and Authority-history assertions were
reviewed and restored unchanged because they are legitimate invariants, not
Party RLS repairs.

## Verification

- `.tools/bin/uv run --locked pytest -q tests/integration/persistence_contract_canonical_parity.py -vv`: `1 passed, 1 failed, 37 subtests passed`. The Party row-visibility assertion passes under the matching role; the remaining failure is the unchanged alias assertion at line 517 because the fresh contract declares `Matter.aliases` as an empty tuple.
- `.tools/bin/uv run --locked pytest -q tests/integration/persistence_contract_security_boundary.py -k test_01_domain_parity_matrix_and_forced_rls -vv`: `1 passed, 2 deselected`.
- `.tools/bin/uv run --locked pytest -q tests/integration/persistence_contract_migrations.py tests/integration/persistence_contract_capauth_replay.py tests/integration/test_capauth_contract.py -vv`: `9 passed, 9 subtests passed`.
- `.tools/bin/uv run --locked pytest -q tests/integration/persistence_contract_migration_guards.py tests/integration/persistence_contract_scope_privilege.py -vv`: `4 passed, 1 failed, 2 subtests passed`. The failure is the pre-existing `test_13_schema_isolation_and_disposable_runtime` expectation for only six schemas; the current candidate includes reviewed feature schemas `sklegal_activity`, `sklegal_artifact`, `sklegal_governed_corpus`, and `sklegal_task_deadline`. The separate data-bearing rollback guard passed.
- `.tools/bin/uv run --locked ruff check tests/integration/persistence_contract_canonical_parity.py`: pass.
- `.tools/bin/uv run --locked ruff format --check tests/integration/persistence_contract_canonical_parity.py`: pass.
- `.tools/bin/uv run --locked python scripts/check_migrations.py`: `manifest valid: 28 migration(s)`.
- `git diff --check`: pass.

Focused invariant regressions:

- `.tools/bin/uv run --locked pytest -q tests/integration/persistence_contract_matter_records.py -k 'test_05_strict_legacy_alias_binding_and_metadata or test_06_append_only_tensions_and_database_authority_history' -vv`: `2 passed, 5 deselected`.

The alias expectation is supported by `migrations/0003_legal_records.sql`
lines 261-300, which defines a separately persisted optional `legacy_aliases`
relation, and by `tests/support/fresh_persistence_contract.py` lines 188-196
and 493-496, which explicitly define the fresh Matter with `aliases: []` and
empty alias metadata. The Authority-history expectation is supported by
`migrations/0003_legal_records.sql` lines 598-725: Authority versions are
append-only and `authority_current` is the row with `system_to IS NULL`. The
fresh contract defines one Authority version at
`tests/support/fresh_persistence_contract.py` lines 277-286 and 507-510.
The focused matter-record regression proves the two-version history behavior
without changing either assertion.

Remaining blocker: reconcile the canonical parity fixture setup so its
unchanged alias and Authority-history assertions have their required input
rows. That is outside this Party-only card and requires a separate bounded
test-fixture task. Card `069044d1` must not be completed until the canonical
parity test passes with the assertions preserved.

Required immutable input hashes remain:

- `migrations/manifest.json`: `08c517e51124d68585ba16fef9791afb5a73c90f883b128790327ad0bc899c35`
- `migrations/0005_row_level_security.sql`: `1118d170e5e3970f136519e742e8e2783979a7c3072436d77652a69c670eae21`
- `uv.lock`: `74141a126906894c87ab4e1bf3792bc9d8d7fc74075c7154871a6fe9d41ba21f`
- `package-lock.json`: `b4da8dc8d7815a19b54bff1ee059c25167485708c0261f40ca6b6df2452e71e8`

## Candidate and rollback

Changed test file SHA-256:
`b988bfa0350fe2cb073112677128e7871be7f076dc5547db3bb9bf3f01d7c2f0`

The changed test file is the only source file in the candidate diff. The final
local commit and tree are linked on the card. Rollback is a local revert of
that commit. No data or host rollback exists because no data or host state
changed.

This evidence does not authorize integration, merge, push, deployment,
restart, protected-data access, provider traffic, credentials, external
action, or work on card `431db4dd`.
