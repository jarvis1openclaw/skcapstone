# SKL-S5-03A completion evidence: email and calendar simulation qualification

Date: 2026-08-22

Card: `9e4474ee` (SKL-S5-03A, slice of SKL-S5-03 `4aa07fd0`)
Connector cards qualified: email `f3c55ccd`, calendar `6de72db6`

## Scope

Qualify the email and calendar connectors end to end in simulation only.
No live email, filing, service, mailing, or calendar dispatch was performed
and no transport credential was read or written.

## Delivered

- End-to-end qualification suites for both connectors:
  `tests/connectors/test_email_simulation_qualification.py` and
  `tests/connectors/test_calendar_simulation_qualification.py`.
- A real invariant fix found by the qualification run: the calendar
  connector previously accepted any `destination_sha256`. Validation now
  requires `destination_digest(calendar_id)` (new public helper), so a
  calendar action is bound to one exact destination calendar before
  queue, dispatch, and receipt verification.
- A structural no-live-transport guard: both suites parse the connector
  source trees with `ast` and reject network, subprocess, and file calls
  or imports (socket, smtp, ssl, http, requests, urllib, subprocess, and
  peers, plus `open` and `os.system`).

## Acceptance criteria evidence

1. Both connectors reach `receipt_verified` in simulation.
   - Email: `test_end_to_end_simulation_reaches_receipt_verified`.
   - Calendar: `test_end_to_end_simulation_reaches_receipt_verified`.
2. Exact-version approvals are enforced.
   - Email: `test_exact_version_approval_binds_artifact_version_and_digest`
     rejects wrong version, wrong artifact digest, and wrong artifact id
     against the `ApprovalBinding`.
   - Calendar: `test_exact_version_approval_is_enforced` rejects a
     missing approval, a wrong-version binding, a wrong-digest binding,
     and a changed event version after approval.
3. Destination verification is enforced.
   - Email: `test_destination_verification_binds_exact_recipients`
     pins the destination digest to the normalized recipient set and
     rejects a changed destination digest at queue time.
   - Calendar: `test_destination_verification_binds_exact_calendar`
     rejects an action bound to another calendar, requires the derived
     destination digest, and rejects a changed destination digest at
     queue time.
4. Duplicate dispatch suppression holds.
   - Email: `test_duplicate_dispatch_suppression_across_registries`
     proves one immutable receipt across two adapter instances sharing a
     registry.
   - Calendar: `test_duplicate_dispatch_suppression` proves repeated
     simulation of the same approved action returns the identical action
     and receipt with one registry entry.
5. Synthetic receipts and receipt reconciliation.
   - Email: `test_receipt_reconciliation_rejects_foreign_and_mismatched_receipts`
     covers foreign-receipt rejection, recipient-count mismatch, bounce
     to `failed`, retry to `queued`, and recovery to `receipt_verified`
     with the original receipt.
   - Calendar: `test_receipt_reconciliation_binds_exact_action` rejects
     a foreign receipt and verifies the exact one.
6. Cannot dispatch to a real destination.
   - Both suites: `test_connector_code_has_no_live_transport` walks the
     email, calendar, and shared base source trees and fails on any
     banned import or call. The email adapter also has no transport
     constructor or send API; the calendar connector's only effect path
     is `simulate` through the in-memory `SimulationRegistry`.

## Verification

Commands (from the worktree root, with
`UV_CACHE_DIR=$PWD/.tools/uv-cache` and `/tmp/sklegal-uv/bin/uv`, the
same uv 0.12.5 tool the shared setup uses):

```text
uv run --locked --package sklegal-email --group dev pytest \
  tests/connectors/test_email_simulation_qualification.py -q
Result: 7 passed in 0.03s

uv run --locked --package sklegal-calendar --group dev pytest \
  tests/connectors/test_calendar_simulation_qualification.py \
  tests/connectors/test_calendar_connector.py -q
Result: 10 passed in 0.03s

uv run --locked --group dev pytest tests/connectors/ \
  tests/test_connector_base.py tests/test_email_connector.py -q
Result: 26 passed in 0.05s

uv run --locked --package sklegal-worker --group dev pytest \
  tests/test_worker_workflows.py -q
Result: 42 passed, 10 subtests passed in 0.62s

uv run --locked --group dev ruff check packages/connectors tests/connectors
Result: All checks passed

uv run --locked --group dev ruff format --check packages/connectors tests/connectors
Result: 30 files already formatted

uv run --locked --group dev mypy packages/connectors
Result: 3 pre-existing errors in packages/connectors/hammertime
(sklegal_domain missing py.typed), identical on the baseline commit
before this change; zero errors in the calendar, email, and base
packages this card touches.
```

## Known limitations

- The suites qualify the connector packages and the shared simulation
  registry. They do not drive a Temporal workflow end to end; the worker
  dispatch ledger interaction is covered by the existing
  `tests/test_worker_workflows.py` suite, which still passes.
- `sklegal-filing-connector` and
  `sklegal-client-communication-connector` are not importable from the
  root workspace environment (they are workspace members but the root
  env does not depend on them); their suites were run under their own
  packages and pass. This is a pre-existing packaging quirk, unchanged
  by this card.
- Receipt verification is simulation-only by construction. A live
  provider qualification remains out of scope and requires a separate
  approved follow-up card.
