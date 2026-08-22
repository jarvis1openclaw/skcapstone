# SKL-S5-03B handoff

Card: `9298ac29` (SKL-S5-03B, slice of `4aa07fd0` SKL-S5-03)
Connectors qualified: court filing (`5b3ab1fe`), service and mailing (`1331e2fb`)
Branch: `swarm/9298ac29` (commits `824b27a`, plus the evidence and handoff commits)

## What this slice does

Qualifies the court filing and service or mailing connectors in
simulation with the same approval, destination, duplicate, and receipt
matrix as S5-03A, so that `receipt_verified` is reachable in simulation
only and partial filing plus missing receipt cases fail closed.

- Both connectors gained the step-wise surface
  `begin -> validate -> approve -> queue -> dispatch -> reconcile`
  matching `EmailSimulation`, with immutable `FilingAction` and
  `ServiceAction` wrappers binding the plan or address to the action. The
  one-shot `simulate` helper now requires an explicit `ApprovalBinding`.
- `FilingProviderResponse` (`accepted` / `partial` / `missing_receipt`)
  drives filing reconciliation: `partial` and `missing_receipt` both
  reconcile to `FAILED` with a recorded reason; response shape is
  validated at construction and against the plan at dispatch.
- `ServiceProviderResponse` (`delivered` / `missing_receipt`) drives
  service reconciliation: a missing carrier receipt fails closed without
  any simulation dispatch; delivery must re-verify the exact address.
- Receipts carry nullable simulation evidence; `receipt_verified` is only
  reachable through the base `Action.verify_receipt` binding to the exact
  idempotency key, which the destination and package digests feed.

## Files changed

- `packages/connectors/filing/src/sklegal_filing/connector.py`
- `packages/connectors/filing/src/sklegal_filing/__init__.py`
- `packages/connectors/service/src/sklegal_service/connector.py`
- `packages/connectors/service/src/sklegal_service/__init__.py`
- `tests/test_filing_connector.py` (17 tests)
- `tests/test_service_connector.py` (15 tests)
- `docs/evidence/status/SKL-S5-03B-COMPLETION-EVIDENCE-2026-08-22.md`
- `HANDOFF.md`

## Tests and exact results

Note on tooling: the repository bootstrap target `.tools/bin/uv` is
absent in this delegated worktree. The pinned uv 0.12.5 installed by the
session bootstrap at `/tmp/sklegal-uv/bin/uv` was used, matching
`requirements/bootstrap.lock`, with the shared default uv cache. All
commands ran from the worktree root.

- `/tmp/sklegal-uv/bin/uv run --locked --package sklegal-filing-connector
  pytest tests/test_filing_connector.py -q` -> `17 passed`
- `/tmp/sklegal-uv/bin/uv run --locked --package sklegal-service-connector
  pytest tests/test_service_connector.py -q` -> `15 passed`
- `/tmp/sklegal-uv/bin/uv run --locked pytest tests/test_filing_connector.py
  tests/test_service_connector.py -q` -> `32 passed`
- Neighbors unchanged and green:
  - `uv run --locked --package sklegal-worker pytest
    tests/test_email_connector.py -q` -> `4 passed`
  - `uv run --locked --package sklegal-calendar pytest
    tests/connectors/test_calendar_connector.py -q` -> `4 passed`
  - `uv run --locked --package sklegal-client-communication-connector pytest
    tests/test_client_communication_connector.py -q` -> `3 passed`
- `uv run --locked ruff check` and `ruff format --check` over changed
  files: passed
- `uv run --locked mypy services packages` -> 26 errors in 12 files,
  identical to the base tree (verified via `git stash`); zero errors in
  files this card touched
- ASCII dash scan: no en or em dashes in changed files

## Acceptance criteria evidence

Card acceptance: "receipt_verified in simulation only; partial filing and
missing receipt cases fail closed."

- Simulation only: neither package imports a provider, HTTP, or socket
  client; every receipt originates in `SimulationRegistry` and is marked
  `simulated`; clerk ids are `SIM-CLERK-` prefixed and tracking numbers
  `SIM-` prefixed. Evidence:
  `test_full_acceptance_reaches_receipt_verified_in_simulation_only`,
  `test_delivery_reaches_receipt_verified_in_simulation_only`.
- Partial filing fails closed:
  `test_partial_filing_fails_closed` (status `FAILED`, receipt absent
  from the action, rejected documents listed in the reason) and
  `test_corrected_package_after_partial_filing_is_a_new_action`.
- Missing receipt fails closed:
  `test_missing_receipt_fails_closed_without_simulation_dispatch` for the
  filing connector (zero registry dispatches) and the identical service
  case, which also asserts no tracking number, affidavit, or address
  verification is fabricated.
- Approval, destination, duplicate, and receipt matrix rows are listed
  per case in
  `docs/evidence/status/SKL-S5-03B-COMPLETION-EVIDENCE-2026-08-22.md`.

## Known limitations

- Simulation only; real provider activation stays behind its own
  human-gated task.
- Recovery (retry after failure, corrected package resubmission) is
  qualified at the connector state machine only; durable failure
  recovery, outbox replay, and Temporal wiring are parent-card work.
- One address and one delivery attempt per service action; no
  multi-recipient party-list service model.
- Integration suites needing disposable PostgreSQL or Temporal were not
  run; no persistence or workflow code changed.
- `simulate` helper callers must pass an explicit `ApprovalBinding` now;
  no other in-repo caller exists (verified with `rg`).

## Migration or rollback

Pure code change in two connector packages plus their tests. No schema,
data, or configuration change, so rollback is `git revert` of the
commits. The connectors remain simulation-only, so no external effect can
occur during or after rollback.

## Card linkage

Board state for `9298ac29` is owned by `jarvis`. This handoff and the
evidence file under `docs/evidence/status/` provide the completion
evidence for review, merge, and card completion.
