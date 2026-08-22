# SKL-S5-04C outage and restart qualification evidence

Date: 2026-08-22
Card: `2781e329` (SKL-S5-04C)
Owner: `skl-s5-04c`
Contract: `docs/development/OUTAGE-MATRIX.md`
Base: `e531d7d`, branch `swarm/2781e329`
Status: all seven scenarios executed and PASS; zero duplicate dispatches and
zero silent state loss observed; gaps recorded for S5-05 triage below

## Environment

- Host: chiap08 (WSL), Docker server 29.1.3.
- Stack: isolated compose project `sklegal-outage`
  (`./scripts/dev_dependencies.sh up --project sklegal-outage
  --postgres-port 25433 --temporal-port 27233`), own containers, volume
  `sklegal-outage-postgres-data`, and network; loopback-only host ports
  `127.0.0.1:25433` and `127.0.0.1:27233`. The shared `sklegal-dev`
  project, `skmem-pg`, and all containers belonging to other sessions were
  never stopped, restarted, or removed (verified after teardown: the shared
  stack stayed healthy and `Up` throughout).
- Driver: `scripts/outage_qualification.py run --address 127.0.0.1:27233
  --workdir /tmp/skl-s5-04c-outage`; the worker is a real OS subprocess so
  the kill is a real `kill -9` (recorded exit code `-9`).
- All tenants, matters, approvals, and digests are synthetic. Simulation
  mode only. No live matter content, HammerTime path, production
  credential, or production database was touched. The connector named
  `outage-matrix-connector` is a simulation-only name inside the dispatch
  ledger; no external delivery occurred.
- Model-level scenarios (OM-3 through OM-6) ran in-process against
  synthetic transports and a fake secret resolver; no network endpoint was
  contacted and no real secret was resolved. This host binds no live Qwen
  endpoint and no live OpenAI egress route, so those outages were imposed
  on synthetic transports under the same typed error surface the gateway
  exposes for real transports (a known limitation, see below).

## Test gate before the live run

- `uv run --locked --package sklegal-worker pytest
  tests/test_outage_qualification.py tests/test_worker_workflows.py
  tests/test_audit_reconciliation.py tests/test_audit.py -q`:
  102 passed, 47 subtests passed.
- Full unit suite (`uv run --locked pytest tests/ -q
  --ignore=tests/integration`): 1155 passed, 814 subtests, 5 failed.
  All 5 failures pre-exist this card (verified by `git stash` and rerun on
  the clean base): `tests/test_clean_room_check.py` (1),
  `tests/test_official_drafting_style_profiles.py` (1),
  `tests/test_retrieval_partition_leak_matrix.py` (3). None touch worker,
  audit, gateway, or outage-qualification code.

## Scenario OM-1: worker kill mid-activity (PASS)

Injection: `kill -9` of the worker pid after the activity start marker was
observed in the marker file. The step driver is the qualification
`MarkingStepDriver` sleeping 120 s, so the activity was genuinely
mid-execution, heartbeating every 10 s, with `heartbeat_timeout=60 s` on
the interactive queue.

Timeline (all UTC 2026-08-22):

| Event | Time | Source |
| --- | --- | --- |
| Scenario started | 20:35:58.244 | om-1.json `started_at` |
| First worker started, activity began (pid 1723194) | 20:36:00.319 | marker line 1 |
| Kill -9 issued | 20:36:00.454 | om-1.json observation |
| Killed worker exit observed (code -9) | immediate | om-1.json observation |
| Replacement worker started (pid 1723336) | before 20:37:01 | om1-worker-replacement.log |
| Step redelivered to replacement (attempt 2) | 20:37:01.321 | marker line 2 |
| Workflow COMPLETED, scenario closed | 20:37:02.186 | om-1.json `finished_at` |

Observed degraded and recovered state:

- Kill-to-redelivery 61.0 s, inside the 60 s heartbeat timeout bound (the
  S3-07 baseline detected the same kill only at the 5 minute StartToClose
  timeout; the heartbeat wiring added by this card closed that gap).
- Activity history from the server: `run_task_step` scheduled 1, started 1,
  completed 1; `dispatch_connector` scheduled 1, started 1, completed 1.
  The killed attempt appears only as the redelivery context of attempt 2,
  matching the S3-07 observation of how Temporal persists interrupted
  activities.
- Durable ledger held exactly one receipt, digest
  `ca7d41f236faf81f903de2ccf77e0dd1639c531746b1fbd24fa9358cf05d3810`;
  the redelivered step produced the identical deterministic digest; the
  workflow parked at `awaiting_approval`, was approved, and completed with
  phase `completed`.
- Zero duplicate dispatches and zero silent state loss.

All seven OM-1 checks PASS.

## Scenario OM-2: Temporal service restart (PASS)

Injection: `docker stop sklegal-outage-temporal-1` with the workflow parked
at `awaiting_approval`, degraded-state probes until a query failed, then
`docker start`.

Timeline (all UTC 2026-08-22):

| Event | Time |
| --- | --- |
| Scenario started | 20:37:02.194 |
| Step activity completed on the worker (pid 1731752) | 20:37:04.284 |
| Workflow parked at `awaiting_approval` | before stop |
| `docker stop` issued; stop to healthy | 13.7 s total window |
| Client query raised `TimeoutError` while frontend down | during stop window |
| Workflow COMPLETED after restart and approval | by 20:37:19.013 |

Observed degraded and recovered state:

- While the container was fully stopped, the client query failed closed
  (`TimeoutError`); the workflow phase stayed `awaiting_approval` across
  the outage and after recovery.
- Worker reconnected automatically; post-restart completion recorded one
  step completion, one dispatch completion, and exactly one ledger receipt,
  digest `35f68e96aeece832d356427924976159a5874a299656ff01fe2caf31952d5b02`.
- Restart-to-healthy 13.7 s, inside the 15 minute RTO target; workflow
  state fully preserved (Postgres-backed persistence in the project
  volume).

All six OM-2 checks PASS. Calibration note: the first execution used
`docker restart`, which returns only after the frontend is accepting
again; the client's transparent RPC retry bridged the window and the
degraded-state check could not observe a failure. The contract and driver
were corrected to stop-then-start, which produces a deterministic outage
window. The failing first run is retained below in the gap list as honest
calibration history.

## Scenarios OM-3 and OM-4: provider route outages (PASS)

Executed in-process by `scripts/outage_qualification.py model-matrix
--workdir /tmp/skl-s504c-mm` (overall PASS at 2026-08-22T20:29:32Z).

- OM-3 (Qwen route): transport disconnected. The gateway surfaced a typed
  `ProviderUnavailableError` after 1 failed transport call, no proposal
  was persisted during the outage, and after transport recovery the
  identical request produced the identical payload sha256
  `ea5207278a9ff0d4c546a6ade704f41abc16d86d36b89e07402c408f00e8d3f8`.
- OM-4 (OpenAI egress): transport disconnected, then secret resolution
  failed. Typed `ProviderUnavailableError` and `SecretResolutionError`
  observed; the gateway failed closed with no fallback provider; after
  both recovered, the identical request produced exactly one proposal
  (payload sha256
  `390c9a342c0fb6a096b122ff4831e6291f42555caf6e735a96408747b0bd39dd`).
  The secret resolver is a fake; no credential was read.

## Scenario OM-5: reconciler restart (PASS)

In-process. Watermark sequence 2 advanced, a fresh reconciler constructed
over the same ledger without calling `recover()` failed closed
(`ReconciliationUnavailable`) while a message was pending, `recover()`
restored sequence 2 from the durable watermark, and the next `reconcile()`
succeeded. This run also proves the fix shipped in this card: before
`recover()` existed, a restarted reconciler could never advance again.

## Scenario OM-6: outbox backend outage (PASS)

In-process. The outbox port raised on every call: `reconcile()` surfaced a
typed `ReconciliationUnavailable`, the watermark did not advance during
the outage, and after backend recovery `reconcile()` completed with
exactly one delivery recorded (no outbox message delivered twice) and an
idempotent rerun.

## Scenario OM-7: dispatch replay suppression (PASS)

Live-stack run (part of the combined matrix): the durable file ledger
recorded the dispatch, a replay of the identical `DispatchRequest`
returned the original receipt digest
(`0318ccd178cc3714a191618db1fb678972eb96e3e981c7c644b2993097535a31`), and
a different payload under the same idempotency key was rejected. Combined
with OM-1's single receipt after a mid-activity kill, duplicate dispatch
suppression is proven on both the write path and the replay path.

## Cleanup evidence

- `./scripts/dev_dependencies.sh down --project sklegal-outage
  --postgres-port 25433 --temporal-port 27233` removed the project
  containers, volume `sklegal-outage-postgres-data`, and network.
- Post-teardown verification: zero `sklegal-outage` containers and zero
  volumes remain; the shared `sklegal-dev` stack (other session) remained
  healthy throughout the qualification and after teardown.

## Gap list for S5-05 triage

1. Live provider outage gaps: chiap08 binds no live Qwen endpoint or
   OpenAI egress route, so OM-3/OM-4 exercised synthetic transports under
   the same typed error surface. S5-05 should re-run these against a
   qualified live route when one is bound and approved.
2. OM-2 calibration history: the first OM-2 execution failed its
   degraded-state check because `docker restart` closes the outage window
   before it is observable. The stop-then-start injection is now the
   contract; future restart scenarios must keep an explicit stopped
   window with in-window probes.
3. Worker failure detection for non-heartbeating activities
   (`dispatch_connector`, `compensate_step`) still relies on
   StartToClose timeouts (2 minute connector); a killed worker during a
   dispatch is detected no faster than that. S5-05 should evaluate
   heartbeat coverage for connector activities.
4. OM-5 depends on a still-pending outbox message to expose the stale
   CAS expectation; an empty pending set hides the gap. The reconciler
   could log a warning when `recover()` finds no pending messages, which
   would make silent-restart gaps observable in operations.

## Known limitations

- Single-run qualification matrix on a disposable dev stack, not a soak or
  chaos test; results cover the injected failure modes only.
- OM-3/OM-4 provider outages are synthetic-transport simulations (host has
  no live provider endpoints); no claim is made about real provider
  behavior.
- The outages were imposed on the isolated `sklegal-outage` project only.
- Test-suite failures that pre-exist this card (clean-room, drafting
  styles, retrieval leak matrix) are unchanged by this work and remain
  open for their owning cards.
