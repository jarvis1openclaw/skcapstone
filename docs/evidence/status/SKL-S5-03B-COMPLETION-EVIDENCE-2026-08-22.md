# SKL-S5-03B completion evidence

Date: 2026-08-22
Card: `9298ac29` (SKL-S5-03B, slice of `4aa07fd0` SKL-S5-03)
Connector cards in scope: court filing `5b3ab1fe`, service or mailing `1331e2fb`
Agent: `skl-s5-03b`

## Outcome

The court filing connector and the service or mailing connector are
qualified in simulation with the same approval, destination, duplicate,
and receipt matrix used for the S5-03A connectors.

- Both connectors now expose the step-wise path
  `begin -> validate -> approve -> queue -> dispatch -> reconcile`
  (matching the `EmailSimulation` surface), each step returning an
  immutable bound wrapper (`FilingAction`, `ServiceAction`). The one-shot
  `simulate` helper remains as a convenience wrapper and now requires an
  explicit `ApprovalBinding` argument, so no path constructs an approval
  implicitly.
- `CourtFilingConnector` accepts a `FilingProviderResponse`
  (`accepted`, `partial`, or `missing_receipt`). A partial acceptance
  (some documents rejected) and a missing clerk receipt both reconcile to
  `FAILED` with a reason string, never to `receipt_verified`. Response
  shape is validated in the constructor and against the plan at dispatch:
  documents outside the plan, an accepted-with-rejections contradiction,
  a subset acceptance under `accepted`, and an unaccounted split under
  `partial` all fail closed.
- `ServiceMailingConnector` accepts a `ServiceProviderResponse`
  (`delivered`, `missing_receipt`). A missing carrier receipt reconciles
  to `FAILED` with no simulation dispatch at all, and a delivered receipt
  must re-verify the exact service address binding.
- `CourtFilingReceipt` now carries the simulation receipt as nullable
  evidence plus status, accepted and rejected document sets, and clerk
  detail. `ServiceReceipt` keeps the legacy `simulation_receipt_id`
  property and carries the tracking number, affidavit digest, and address
  verification as nullable fields on the missing-receipt path.
- `reconcile_receipt` (filing) and the reconcile paths only produce
  `receipt_verified` from a non-null simulated receipt that the base
  `Action.verify_receipt` binds to the exact idempotency key.

Acceptance: `receipt_verified` is reachable in simulation only. The
connectors expose no provider, court, clerk, carrier, or network client;
every receipt comes from the shared `SimulationRegistry` and is marked
`simulated`, with `SIM-` prefixed clerk and tracking identifiers. Partial
filing and missing receipt cases fail closed with recorded failure
reasons and preserve the full append-only event history.

## Files changed

- `packages/connectors/filing/src/sklegal_filing/connector.py`
- `packages/connectors/filing/src/sklegal_filing/__init__.py`
- `packages/connectors/service/src/sklegal_service/connector.py`
- `packages/connectors/service/src/sklegal_service/__init__.py`
- `tests/test_filing_connector.py`
- `tests/test_service_connector.py`
- `docs/evidence/status/SKL-S5-03B-COMPLETION-EVIDENCE-2026-08-22.md`
- `HANDOFF.md`

## Tests and exact results

Pinned uv: the repository bootstrap target `.tools/bin/uv` is absent in
this delegated worktree; the pinned uv 0.12.5 binary installed by the
session bootstrap at `/tmp/sklegal-uv/bin/uv` was used with the shared
default uv cache. Commands were run from the worktree root.

- `uv run --locked --package sklegal-filing-connector pytest
  tests/test_filing_connector.py -q` -> 17 passed.
- `uv run --locked --package sklegal-service-connector pytest
  tests/test_service_connector.py -q` -> 15 passed.
- `uv run --locked pytest tests/test_filing_connector.py
  tests/test_service_connector.py -q` -> 32 passed.
- Neighbor suites unchanged and green:
  `uv run --locked --package sklegal-worker pytest
  tests/test_email_connector.py -q` -> 4 passed;
  `uv run --locked --package sklegal-calendar pytest
  tests/connectors/test_calendar_connector.py -q` -> 4 passed;
  `uv run --locked --package sklegal-client-communication-connector pytest
  tests/test_client_communication_connector.py -q` -> 3 passed.
- `uv run --locked ruff check` and `ruff format --check` over all changed
  files: passed.
- `uv run --locked mypy services packages`: 26 errors in 12 files,
  identical count and files to the base tree (verified with `git stash`);
  zero errors in the files this card touched.
- ASCII dash scan of all changed files: no en or em dashes.

Required matrix coverage, all present and passing:

- Approval: wrong artifact id, wrong version, wrong digest, missing prior
  approval, and implicit approval all fail closed; changed package or
  artifact text after approval fails revalidation; revoked capability
  blocks at queue before any dispatch.
- Destination: a changed court or address rekeys the idempotency key and
  does not deduplicate; a drifted destination digest fails queue; address
  normalization collapses whitespace variants of one address onto one
  receipt; malformed addresses fail before any dispatch.
- Duplicate: dispatching the same queued action twice returns the equal
  immutable receipt and one registry entry; the same action id with
  changed content produces a distinct receipt.
- Receipt: full acceptance or delivery reaches `receipt_verified` with
  the receipt bound to the exact idempotency key; partial filing fails
  closed listing rejected documents; missing clerk or carrier receipt
  fails closed with zero simulation dispatches; reconciliation rejects a
  receipt bound to another action, another plan, or without address
  verification; a failed action retries to completion with the full event
  history preserved.

## Known limitations

- Simulation only. The matrix runs entirely against the shared
  `SimulationRegistry`; no clerk portal, ECF endpoint, carrier, or postal
  provider exists in either package, and activation of a real provider
  remains a separate human-gated task per the parent SKL-S5-03 card.
- Recovery paths (`retry` after `FAILED`, corrected package after partial
  filing) are qualified at the connector state-machine level only. Durable
  failure recovery, outbox replay, and Temporal worker integration are
  parent-card work.
- The service connector models one address and one delivery attempt per
  action; multi-recipient service matrices (for example a party list) are
  not modeled.
- The `FilingPlan` destination digest treats the same case number in a
  different court as a different destination, which is correct, but does
  not model forum-specific clerk metadata beyond forum, court, and case
  number.
- `tests/integration` suites requiring disposable PostgreSQL or Temporal
  were not run; this slice changes no persistence or workflow code.

## Card linkage

Card `9298ac29` board state is owned by `jarvis`; this file plus
`HANDOFF.md` provide the completion evidence for review.
