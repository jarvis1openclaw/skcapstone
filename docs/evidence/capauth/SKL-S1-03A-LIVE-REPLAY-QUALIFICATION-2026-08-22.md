# SKL-S1-03A live replay qualification evidence

Date: 2026-08-22
Card: `d9552c4c` (SKL-S1-03A)
Owner: `jarvis`
Status: live PostgreSQL replay race verified; parent card remains gated

## Scope

This bounded qualification closes the documented absence of a live PostgreSQL
multi-worker test for `sklegal_identity.reserve_capability`. It uses a
networkless disposable PostgreSQL 17 container, synthetic tenant and service
principal records, and the production `PostgresReplayBackend` adapter. It does
not create an issuer key, mutate the chiap01 deployment, touch HammerTime, or
activate protected routes.

`docs/tasks/SUBAGENT-TASK-TTDS.md` does not contain a section for the later
SKL-S1-03A production-composition card. The card description and acceptance
criteria are therefore the execution contract for this slice.

## Files changed

- `tests/integration/persistence_contract_capauth_replay.py`
- `tests/integration/test_persistence_contract.py`
- `docs/evidence/capauth/SKL-S1-03A-LIVE-REPLAY-QUALIFICATION-2026-08-22.md`

## Test design and result

Eight worker threads cross a common barrier and invoke the same
`PostgresReplayBackend` instance. Each adapter invocation opens a separate
`psql` connection to the disposable PostgreSQL container. All workers present
the same tenant and credential digest with distinct decision IDs and one shared
future expiry. The test requires:

- exactly one adapter result to be `true`;
- exactly seven adapter results to be `false`; and
- the one durable reservation row to contain the winning decision ID.

Focused command:

```bash
.tools/bin/uv run --locked pytest -q \
  tests/integration/test_persistence_contract.py::PersistenceContract13CapAuthReplayTests
```

Result: `1 passed in 18.88s`.

Full changed boundary command:

```bash
.tools/bin/uv run --locked pytest -q \
  tests/integration/test_persistence_contract.py
```

Result: `45 passed, 74 subtests passed in 105.05s`.

Static checks:

```bash
.tools/bin/uv run --locked ruff check \
  tests/integration/persistence_contract_capauth_replay.py \
  tests/integration/test_persistence_contract.py
.tools/bin/uv run --locked ruff format --check \
  tests/integration/persistence_contract_capauth_replay.py \
  tests/integration/test_persistence_contract.py
```

Result: all checks passed; both files already formatted.

A direct mypy invocation against the integration test is not a clean project
gate. It reports the existing untyped editable-package imports for
`sklegal_persistence` and `sklegal_capauth`, plus the existing `Sequence`
construction finding in `persistence_contract_support.py`. No production
module changed in this slice.

## Acceptance evidence

- Durable, shared replay backend: strengthened. Eight independent database
  connections contend on the real security-definer reservation function and
  exactly one succeeds.
- Fail-closed ambiguity handling: strengthened. Every losing concurrent write
  returns a deterministic replay denial and the durable row identifies the
  sole winner.
- Multi-worker replay qualification: met for a disposable live PostgreSQL
  instance through the production adapter path.
- Signer rotation, issuer revocation, issuer custody, and protected-route
  activation: unchanged by this slice.

## Remaining gates and limitations

The parent card must remain incomplete because no approved dedicated issuer
ceremony record is linked to the card. Casey and Jarvis remain trust anchors
only and were not used as the application issuer.

The SKL-S5-04D evidence verifies full logical and point-in-time restoration of
all migrations, including the CapAuth schemas. It does not explicitly assert
restoration of a live replay reservation row. A card-specific replay-state
backup and restore receipt is still required before treating that acceptance
criterion as fully met.

## Migration and rollback

No migration or persistent deployment data changed. The disposable test
container is removed by the shared persistence harness. Rollback consists of
removing the new aggregate test module and its import from the historical
aggregate suite.
