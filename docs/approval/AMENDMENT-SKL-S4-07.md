# Amendment record: connector dispatch ownership

Amendment ID: `AMENDMENT-SKL-S4-07`
Card: `ea2c9790` (`SKL-S4-07`)
Decision recorded by: `kimi-skl-s4-07`
Record prepared by: `kimi` (validation session)
Status: approved

## Baseline

`AGENTS.md` and `docs/development/AUDIT.md` placed connector dispatch on the
polled PostgreSQL outbox while the Sprint 3 and 4 cards placed connector
workflows in Temporal, and no approved document assigned ownership. Section
14 of the approved `SKLEGAL-HIGH-LEVEL-TDD.md` is unchanged by this
amendment.

## Amendment

The decision is recorded in `docs/development/AUDIT.md`, section "Connector
dispatch ownership (SKL-S4-07)", which supplements TDD section 14 without
altering any approved hash-pinned document:

- Temporal owns connector dispatch orchestration: validation, exact-version
  Approval binding, destination verification, capability verification, the
  provider call, receipt capture, reconciliation, retries, and human waits.
  Only a Temporal activity may invoke a provider adapter, in simulation mode
  by default.
- The polled PostgreSQL outbox (migration 0007) owns only the content-free
  evidence handoff and derived projections. It never initiates a provider
  call.
- Double dispatch is prevented by one owner plus one dedup record: a
  deterministic SHA-256 over the tenant, the exact action identity, the
  destination, and the approved artifact hash, claimed before the provider
  call, with the recorded receipt returned on retry. If approval, capability,
  destination verification, or the audit append is unavailable, the activity
  fails closed and nothing dispatches.

## Human decision

2026-08-21: the human owner approved this amendment.

## Approval trail effect

- No hash-pinned approved document was modified.
- AUDIT.md is a development contract, not a pinned document. Its sha256 at
  approval time:
  `5eb69fbc6ad5d0e5c4e160ceec06b308b03b6f85f0331ac769aa5e1448bc6b0a`
  (`docs/development/AUDIT.md`).
- The S4-06 connector child cards build on this decision. Card `38798f6c`
  (S4-06A) carries the reference in its description, and a link to the
  AUDIT.md section is recorded on all six child cards.
