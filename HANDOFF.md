# HANDOFF: SKL-S5-03A email and calendar connector simulation qualification

Card `9e4474ee` (SKL-S5-03A, slice of SKL-S5-03 `4aa07fd0`). Connector
cards qualified: email `f3c55ccd`, calendar `6de72db6`. Dependencies
recorded on the card (`b04de409`, `fa81f43e`, `07cca7a9`) were merged in
this branch and used as committed.

Branch `swarm/9e4474ee`. All work is inside this worktree. No push, pull,
remote operation, HammerTime access, external action, secret access, or
skcapstone board command was performed. jarvis owns board state and
completion.

## Files changed

- `packages/connectors/calendar/src/sklegal_calendar/connector.py`
  (modified): added the public `destination_digest(calendar_id)` helper
  and a validation gate requiring the action destination digest to match
  the exact calendar, so destination verification is enforced rather
  than vacuous.
- `packages/connectors/calendar/src/sklegal_calendar/__init__.py`
  (modified): export `destination_digest`, alphabetical `__all__`.
- `packages/connectors/calendar/README.md` (modified): replaced the
  reserved placeholder with the actual simulation-only boundary
  description.
- `tests/connectors/test_calendar_connector.py` (modified): fixture now
  uses the derived destination digest instead of a filler digest.
- `tests/connectors/test_email_simulation_qualification.py` (new):
  7-test end-to-end email simulation qualification suite.
- `tests/connectors/test_calendar_simulation_qualification.py` (new):
  6-test end-to-end calendar simulation qualification suite.
- `docs/evidence/connectors/SKL-S5-03A-COMPLETION-EVIDENCE-2026-08-22.md`
  (new): completion evidence following the sibling pattern.

Commits: implementation and tests first, then this handoff and the
evidence file.

## Tests and exact results

From the worktree root with `UV_CACHE_DIR=$PWD/.tools/uv-cache` and
`/tmp/sklegal-uv/bin/uv` (uv 0.12.5; this worktree has no `.tools/bin/uv`,
the shared tool is the same binary the repository scripts expect):

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

uv run --locked --package sklegal-filing-connector --group dev pytest \
  tests/test_filing_connector.py -q
Result: 4 passed in 0.02s

uv run --locked --package sklegal-client-communication-connector \
  --group dev pytest tests/test_client_communication_connector.py -q
Result: 3 passed in 0.02s

uv run --locked --package sklegal-worker --group dev pytest \
  tests/test_worker_workflows.py -q
Result: 42 passed, 10 subtests passed in 0.62s

uv run --locked --group dev ruff check packages/connectors tests/connectors
Result: All checks passed

uv run --locked --group dev ruff format --check \
  packages/connectors tests/connectors
Result: 30 files already formatted

uv run --locked --group dev mypy packages/connectors
Result: 3 pre-existing errors in packages/connectors/hammertime
(sklegal_domain missing py.typed), byte-identical on the baseline
commit before this change; zero errors in the calendar, email, and
base packages this card touches.
```

## Acceptance criteria evidence

Both connectors reach `receipt_verified` in simulation and cannot
dispatch to a real destination. Detail per element:

- Exact-version approvals: email rejects wrong version, wrong artifact
  digest, and wrong artifact id bindings; calendar rejects missing
  approval, wrong-version, wrong-digest, and changed-event bindings.
- Destination verification: email pins the destination digest to the
  normalized recipient set and rejects a changed digest at queue;
  calendar (newly enforced by this card) requires
  `destination_digest(calendar_id)` and rejects actions bound to another
  calendar.
- Duplicate suppression: one immutable receipt per idempotency key,
  proven across two email adapter instances sharing a registry and for
  repeated calendar simulation.
- Synthetic receipts: both paths mint receipts through the in-memory
  `SimulationRegistry` with `simulated is True`.
- Receipt reconciliation: foreign and mismatched receipts are rejected;
  bounce fails the email action, retry requeues, and recovery verifies
  the original receipt; the calendar path rejects foreign receipts and
  verifies the exact one.
- Cannot dispatch to a real destination: both suites include a structural
  AST guard rejecting network, subprocess, and file calls or imports in
  the email, calendar, and shared base source trees; neither connector
  package constructs a transport.

Full test-name mapping is in
`docs/evidence/connectors/SKL-S5-03A-COMPLETION-EVIDENCE-2026-08-22.md`.

## Known limitations

- No Temporal workflow is driven end to end by the new suites; the
  worker dispatch ledger boundary is covered by the existing
  `tests/test_worker_workflows.py` suite, which still passes.
- `sklegal-filing-connector` and
  `sklegal-client-communication-connector` are not importable from the
  root workspace environment (pre-existing packaging quirk, unchanged
  here); their suites were run under their own packages.
- Live provider qualification is out of scope by card boundary; every
  receipt in this card is synthetic and simulation-only.

## Migration or rollback evidence

No data changed and no migration is involved. Rollback is `git revert`
of the two commits; the calendar destination gate is additive validation
and the only caller-visible change is the new exported helper.
