# SKL-S1-08 real PostgreSQL CapAuth integration evidence

Card: `ed6b37e3`
Implementer: `codex-skl-s1-08`
Date: 2026-08-21
Status: Complete

This record attaches the durable PostgreSQL verification requested by the
S1-03A CapAuth review family. It closes the real database test gap identified
in `SKL-S1-05A-SOURCE-ACCEPTANCE-2026-08-21.md`. All inputs are synthetic and
the database is a disposable PostgreSQL 17.7 container with no network, host
port, or persistent volume.

## Acceptance evidence

### Migrations 0008 through 0012 execute on real PostgreSQL

`PersistenceContractTests.setUpClass` applies all digest-pinned migrations,
rolls all migrations down, reapplies them, and exercises every rollback suffix
from one migration through the migration 0007 audit boundary. The rollback
loop is now manifest-derived rather than fixed at `range(1, 8)`, so migrations
0008 through 0012 remain covered when later migrations are added.

The focused CapAuth migration test reads the real migration ledger and
requires the exact 0008 through 0012 file sequence. It also proves that the
principal snapshot, revocation snapshot, revocation writer, and replay writer
are SECURITY DEFINER functions executable by `sklegal_runtime`, and that
migrations 0011 and 0012 grant schema usage for both required schemas.

### Concurrent replay reservation is atomic

Eight independent `psql` sessions wait behind one Python barrier and call the
production `PostgresReplayBackend` for the same tenant and credential digest
with distinct decision IDs. Exactly one call must return true, seven must
return false, and the real replay table must contain exactly the winning row.

### Principal rebinding and revocation persist

The production `PostgresPrincipalPolicyBackend` reads a real principal
snapshot through the scoped function. A synthetic authentication subject is
then rebound in a separate administrator session. The next backend snapshot
must return the new subject and a different revision, making a stale principal
context detectable.

The revocation test reads an empty snapshot, revokes a synthetic credential
through `revoke_capability`, and constructs a fresh
`PostgresRevocationBackend` read in another session. The revocation and changed
revision must persist, and the durable table must contain exactly one row.

### Existing rollback and probe gates remain green

The full persistence module retains the manifest-derived synthetic failing
migration probe and the data-bearing audit rollback refusal. No migration SQL
or production data was changed by this card.

## Files changed for SKL-S1-08

- `tests/integration/test_persistence_contract.py`
- `docs/evidence/capauth/SKL-S1-08-REAL-POSTGRES-INTEGRATION-2026-08-21.md`

## Tests and exact results

- `pytest -q tests/integration/test_persistence_contract.py -k 'capauth_'`:
  4 passed, 34 deselected.
- Full persistence module within `./scripts/run_checks.sh all`:
  38 passed, 69 subtests passed.
- `pytest -q tests/test_capauth_postgres.py tests/test_foundation.py`:
  23 passed, 10 subtests passed.
- Migration manifest validation: 13 migrations valid.
- Repository secret scan: no findings outside the reviewed baseline.
- Repository vulnerability scan: no known Python or npm vulnerabilities.
- `./scripts/run_checks.sh all`: passed after integration hygiene repairs to
  unrelated newly merged cards.

## Known limitations

- This is disposable integration qualification, not production deployment.
- The test binds only a synthetic service principal to the shared runtime role.
- No production service, connector, account, database, signing key, or external
  action was started or changed.
- No HammerTime Inbox or corpus path was searched, read, or modified.

## Migration and rollback

No new migration or data migration is required. Revert the test and evidence
file to remove this card. Every test container is removed by class cleanup,
and the disposable database uses tmpfs with no persistent volume.
