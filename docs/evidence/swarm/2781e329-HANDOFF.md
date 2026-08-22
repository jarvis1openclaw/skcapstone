# SKL-S5-04C handoff: outage and restart qualification

Card: `2781e329` (SKL-S5-04C), dependency `cb6326ce` (SKL-S3-07).
Branch: `swarm/2781e329`, base `e531d7d`. All work is inside this
worktree. No push, pull, remote operation, HammerTime access, external
action, secret access, or skcapstone board command was performed.

## Files changed

Commits (oldest first):

1. `4f3db4f` `feat(worker)`:
   `services/worker/src/sklegal_worker/activities.py` (FileDispatchLedger,
   heartbeat executor loop), `workflows.py` (per-queue heartbeat timeouts),
   `worker.py` (WORKER_DATA_CONVERTER, connect_worker_client), `__init__.py`
   (exports).
2. `65fec3f` `feat(audit)`:
   `packages/audit/src/sklegal_audit/ledger.py` (read_watermark on
   InMemoryAuditLedger), `reconciliation.py` (read_watermark on the
   PolicyRevisionOutbox protocol, PolicyRevisionReconciler.recover()).
3. `43584b0` `fix(deploy)`:
   `deploy/chiap01/compose.dev.yml` (parameterized host ports and volume
   name; defaults unchanged), `scripts/dev_dependencies.sh` (flag parser
   now actually parses flags placed after the subcommand; previously they
   were silently ignored, which made an "isolated" run adopt the shared
   sklegal-dev project).
4. `75ad426` `test(resilience)`: `docs/development/OUTAGE-MATRIX.md`
   (contract), `scripts/outage_qualification.py` (driver),
   `tests/test_outage_qualification.py` (tests).
5. `395320a` `fix(resilience)`: OM-2 stop/start injection, per-run ledger
   cleanup, `--only` scenario filter, OM-1 marker-miss FAIL path, worker
   logs to files instead of unread pipes.
6. `9b5863d` `docs(evidence)`:
   `docs/evidence/platform/SKL-S5-04C-OUTAGE-MATRIX-2026-08-22.md` and the
   contract evidence table.

## Tests and exact results

- `uv run --locked --package sklegal-worker pytest
  tests/test_outage_qualification.py -q`: 20 passed, 18 subtests.
- `uv run --locked --package sklegal-worker pytest
  tests/test_outage_qualification.py tests/test_worker_workflows.py
  tests/test_audit_reconciliation.py tests/test_audit.py -q`: 102 passed,
  47 subtests.
- Full unit suite `uv run --locked pytest tests/ -q --ignore=tests/integration`
  (after `uv sync --locked --all-packages`): 1155 passed, 814 subtests,
  5 failed. All 5 failures pre-exist this card, verified by stashing my
  changes and rerunning on the clean base:
  `test_clean_room_check.py` (1), `test_official_drafting_style_profiles.py`
  (1), `test_retrieval_partition_leak_matrix.py` (3). They belong to other
  cards and are unchanged by this work.
- `tests/integration/test_foundation_contract.py`: 2 failures
  (`sklegal_api` import needs its package env; a running-container check
  sees other sessions' stacks). Both also pre-exist on the clean base.
- `ruff check scripts/outage_qualification.py
  tests/test_outage_qualification.py`: All checks passed.

## Acceptance criteria evidence

- Outage matrix executed with written results per scenario: overall PASS,
  all of OM-1..OM-7 PASS. Live OM-1/OM-2/OM-7 ran against the isolated
  `sklegal-outage` compose project (ports 25433/27233, loopback only) via
  `scripts/outage_qualification.py run`; OM-3..OM-6 ran in-process via
  `model-matrix`. Per-scenario timelines, check tables, and observations
  are in `docs/evidence/platform/SKL-S5-04C-OUTAGE-MATRIX-2026-08-22.md`
  and in the JSON results under `/tmp/skl-s5-04c-outage` (live) and
  `/tmp/skl-s504c-mm` (model; transient operator-host artifacts).
- Zero duplicate dispatches: OM-1 ended with exactly one ledger receipt
  after a real `kill -9` mid-activity and 61.0 s kill-to-redelivery
  (heartbeat timeout detection; S3-07 saw the same kill detected only at
  the 5 min StartToClose). OM-7 replay returned the original receipt
  digest and rejected a different payload under the same key. OM-6
  recorded exactly one outbox delivery with no duplicates.
- Zero silent state loss: OM-2 preserved the workflow phase across a
  frontend stop/start with a query failing closed in the stopped window;
  OM-5 `recover()` restored the durable watermark and the next
  `reconcile()` advanced.
- Gaps triaged before S5-05: four items recorded in the evidence file's
  gap list (live-provider re-qualification, OM-2 restart-window
  calibration history, connector-activity heartbeat coverage, pending-
  message observability in recover()).

## Known limitations

- OM-3/OM-4 provider outages are synthetic-transport simulations; this
  host binds no live Qwen endpoint and no OpenAI egress route. No claim is
  made about real provider behavior; S5-05 should re-qualify when a live
  approved route exists.
- Single-run qualification on a disposable dev stack, not a soak or chaos
  test.
- The `sklegal-outage` stack was torn down and verified removed; the
  shared `sklegal-dev` project (another session) was untouched throughout.
- Pre-existing unit/integration failures listed above remain open for
  their owning cards.

## Migration or rollback evidence

No production data or schema changed. The compose parameterization is
backward compatible (defaults unchanged). Rollback is reverting the
commits; the qualification stack is disposable and already removed.

## Linked task update

Card `2781e329` state is owned by jarvis per the brief; this handoff is
the completion evidence for review. No skcapstone board command was run.
