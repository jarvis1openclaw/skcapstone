# SKL-S3-07 resilience smoke execution evidence

Date: 2026-08-22
Card: `cb6326ce`
Owner: `kimi-skl-s3-07`
Contract: `docs/development/RESILIENCE-SMOKE.md` (merged as `af5cc03`)
Status: all three smokes executed and passing; numeric RPO/RTO targets
proposed in `docs/approval/AMENDMENT-SKL-S3-07.md` and pending human approval

## Environment

- Host: chiap08 (WSL), Docker server 29.1.3.
- Stack: pinned development compose project `sklegal-dev`
  (`deploy/chiap01/compose.dev.yml`, digest-pinned `postgres:17.7-alpine` and
  `temporalio/auto-setup:1.29.1`), disposable volume and network.
- Repository: worktree `/tmp/sklegal-cb6326ce`, branch
  `feat/skl-s3-07-cb6326ce`, base `60eb3aa`.
- All workflows used synthetic tenant, matter, approval, and digest values.
  Simulation mode only. No live matter, HammerTime path, production
  credential, or production database was touched.

## Prerequisite repair: development stack health probe

The 2026-08-21 attempt recorded both live smokes as PENDING because the
Temporal container never reported healthy. Root cause found on 2026-08-22:
`temporalio/auto-setup` binds the frontend gRPC listener to the container IP
(observed `172.19.0.3:7233`), not loopback, so the pinned healthcheck
`tctl --address 127.0.0.1:7233 cluster health` could never connect. The
server itself was serving the whole time; the host port mapping
`127.0.0.1:17233` worked against it.

Fix (this card): the healthcheck now probes `$$(hostname -i):7233` in
`deploy/chiap01/compose.dev.yml`. After the fix:

```text
2026-08-22T16:17:57Z  ./scripts/dev_dependencies.sh up
sklegal-dev-postgres-1  Up (healthy)
sklegal-dev-temporal-1  Up 10 seconds (healthy)
```

## Contract step 1: unit-test gate and worker-kill replay

Command: `./scripts/run_checks.sh unit-test`

Result: exit 0. 64 test modules collected and run; 945 Python tests passed;
npm suite 9 files and 85 tests passed. The replay evidence row stands:

```text
tests/test_worker_workflows.py::WorkerKillReplayTests::test_resume_from_every_boundary_reproduces_identical_log PASSED
tests/test_worker_workflows.py::WorkerKillReplayTests::test_replay_is_pure_and_repeatable PASSED
```

Full log retained at `/tmp/sklegal-s307-unit-test.log` on the operator host
(transient; not committed).

## Smoke 1: worker kill mid-activity (PASS)

Driver: `scripts/resilience_smoke.py` against the live development stack.
The workflow is `MatterTaskWorkflow` on the `sklegal-interactive` queue with
one step, one human approval gate, and one connector dispatch. The dispatch
ledger is a JSON file outside the worker process so receipt counts survive a
kill.

Timeline (all UTC 2026-08-22):

| Event | Time | Source |
| --- | --- | --- |
| Worker F started (step delay 600 s) | 16:50:46 | worker-f.log |
| Workflow `skl-s3-07-smoke-c` started | 16:50:49.237 | start output |
| Step activity began on worker F (pid 1015701) | 16:50:49.287 | marker file |
| Worker F killed with `kill -9` mid-activity | 16:50:51 | operator log |
| Worker G started (step delay 0) | 16:51:50 | worker-g.log |
| Step redelivered to worker G (attempt 2) | 16:55:50.309 | marker file |
| Workflow reached `awaiting_approval` | 16:56:08 | phase query |
| Approval signaled | 16:56:25.459 | approve output |
| Workflow COMPLETED | 16:56:25.474 | verify output |

Observed server behavior:

- The killed attempt was detected only at the activity `StartToClose`
  timeout (5 minutes on the interactive queue). Temporal 1.29.1 records the
  redelivery as `ActivityTaskStarted` with `Attempt: 2` and
  `LastFailure: activity StartToClose timeout`; no separate attempt-1 start
  or timeout event is persisted (confirmed via `tctl workflow show`).
- The redelivered step produced the identical deterministic result digest.
- Final state: `run_task_step` completed exactly once,
  `dispatch_connector` completed exactly once, the file ledger holds exactly
  one receipt, no compensation ran, result phase `completed`, dispatch
  receipt digest `afa769651518b8ba5a32a2f793248bb6d465591ccfac69ba51a5c2d2b94271d7`.

No duplicate mutation and no duplicate dispatch: PASS. Kill-to-completion
was 5 minutes 34 seconds (5 minute timeout floor plus 17 seconds of operator
approval latency), inside the 15 minute RTO target.

### Fail-closed behavior observed during driver calibration

Two earlier runs (`skl-s3-07-smoke-a`, `skl-s3-07-smoke-b`) reached dispatch
with a misconfigured smoke-driver approval gate. The activity raised
`PolicyDeniedError`, which is non-retryable by policy: the workflow ran
`compensate_step` exactly once and terminated `WORKFLOW_EXECUTION_FAILED`
with zero recorded receipts. The fail-closed denial and single-compensation
path is verified live, not only in the model tests.

## Smoke 2: Temporal restart (PASS)

Workflow `skl-s3-07-restart` was started at 16:51:55 and parked in
`awaiting_approval` at 16:52:01. The Temporal container was then restarted.

Timeline (all UTC 2026-08-22):

| Event | Time |
| --- | --- |
| `docker restart sklegal-dev-temporal-1` issued | 16:52:30.053 |
| Container process returned | 16:52:31.570 |
| Container healthy again | 16:52:41.855 |
| Workflow R query succeeded after restart | 16:52:56.279 |
| Approval signaled after restart | 16:57:44.149 |
| Workflow COMPLETED | 16:57:44.166 |

Observed behavior:

- Restart to healthy took 11.8 seconds; a client query probe succeeded
  within 26 seconds of the restart.
- The running workflow kept its phase (`awaiting_approval`) across the
  restart; the terminal history of the earlier failed run also remained
  queryable. No workflow history was lost.
- Worker G reconnected automatically without intervention and completed the
  workflow after the restart: one step completion, one dispatch completion,
  receipt digest `bdd79ce76f4ef03df21892fbce3df94980bc75aa56eadfc7ff5308e1fe64d275`.

PASS, inside the 15 minute RTO target.

## Smoke 3: PostgreSQL backup and restore (PASS)

Scratch setup on the development postgres container: database
`sklegal_s307_src`, exact-profile `sklegal_migrator` role, shared
`sklegal_runtime` role via `scripts/provision_postgres_runtime.py`, all 16
migrations applied via `scripts/manage_migrations.py up`, and one marker row
in `smoke_s307.marker`.

Timeline (all UTC 2026-08-22):

| Event | Time |
| --- | --- |
| Marker inserted | 16:26:33 |
| `pg_dump` backup complete (615913 bytes) | 16:26:34 |
| Failure simulated: `docker stop sklegal-dev-postgres-1` | 16:26:49 |
| Container started, accepting connections | 16:26:50 |
| Dump restored into `sklegal_s307_restore` | 16:26:51 |
| Probe verified marker and migration readback | 16:27:15 |

Probe results: marker row `1 | skl-s3-07-restore-marker` present in both
databases with the identical `recorded_at`; `sklegal_migrations
.schema_migrations` holds 16 rows in both, latest `0016_pilot_import.sql`;
the `file, sha256` lists are byte-identical (`diff` reported IDENTICAL).

RPO: backup age at failure was 15 seconds, inside the 5 minute target.
RTO: failure observation to a restored service accepting a probe was 26
seconds, inside the 15 minute target. PASS.

## Target evaluation summary

| Boundary | Target | Observed | Result |
| --- | ---: | --- | --- |
| Recovery point objective | 5 minutes or less | 15 seconds backup age at failure | PASS |
| Recovery time objective | 15 minutes or less | 26 seconds (PG), 11.8 seconds (Temporal), 5 minutes 34 seconds (worker kill) | PASS |
| Worker interruption | No duplicate mutation or dispatch | One step completion, one dispatch, one receipt | PASS |
| Temporal restart | No lost workflow history | Phase and terminal history queryable after restart | PASS |
| PostgreSQL restore | Source marker and schema survive | Marker and 16-migration readback identical | PASS |

The numbers above are single-run smoke observations on a quiet development
stack, not qualification under load.

## Gap list for the S5-04 child cards

Recorded here per the card scope; board linkage is left to the coordinator.

- G1 (feeds `2781e329` S5-04C): the pinned development healthcheck probed
  loopback while auto-setup binds the frontend to the container IP. Fixed in
  this card; S5-04C should rerun outage qualification with the fixed probe.
- G2 (feeds `2781e329` S5-04C): a killed worker's in-flight activity is
  redelivered only at its `StartToClose` timeout (5 minutes interactive, 30
  minutes batch and long-context, 2 minutes connector) because activities do
  not heartbeat. Worst-case single-attempt recovery latency equals the step
  timeout; S5-04C should evaluate heartbeat timeouts or tighter bounds.
- G3 (feeds `2781e329` S5-04C and `9549c3be` S5-04D):
  `SimulatedDispatchLedger` keeps idempotency state in worker memory. A real
  crash between dispatch and receipt persistence can duplicate a downstream
  effect unless the durable outbox backs the ledger. This smoke proved
  exactly-once only with a file-backed ledger.
- G4 (feeds `2781e329` S5-04C): no runtime client or worker entrypoint in
  the repository pins `pydantic_data_converter`, which Temporal documents as
  required for the pydantic v2 workflow models; the default converter risks
  UUID and datetime fields degrading to plain strings. The smoke driver pins
  it; the production entrypoint should too.
- G5 (feeds `9549c3be` S5-04D): this smoke covered a logical `pg_dump`
  restore into a scratch database on the same instance. Full-instance and
  point-in-time restore with audit hash chain and outbox verification after
  restore remain open.
- G6 (feeds `9549c3be` S5-04D): a 5 minute RPO implies a durable backup
  cadence of 5 minutes or faster. No backup scheduler, retention policy, or
  restore runbook exists yet.
- G7 (feeds `3e3c32d6` S5-04B and `2781e329` S5-04C): Temporal persistence
  sits on a single development postgres volume; no replication, failover, or
  saturated-host behavior was qualified here.
- G8 (feeds `1cb2aa72` S5-04A): the smoke ran as the temporal superuser with
  trust authentication inside a disposable container; restore-time role and
  RLS re-verification under the real login model stays with S5-04A and
  S5-04D.

## RPO/RTO approval status

The numeric targets are recorded in
`docs/approval/AMENDMENT-SKL-S3-07.md` as a proposed amendment to the S0-02
capacity baseline, pinning the contract's recovery-target table at hash
`25aabb9e44bfa52d4c702331dcb750fe56248f3c27ae34b56d40a4c9a598fb80`. Human
approval is outstanding; acceptance criterion 1 stays pending until the
human owner records the decision in that amendment.

## Cleanup evidence

- 16:58:33: scratch databases `sklegal_s307_src` and `sklegal_s307_restore`
  dropped; scratch roles `sklegal_migrator` and `sklegal_runtime` dropped;
  dump file removed.
- 16:58:48: `./scripts/dev_dependencies.sh down` removed the `sklegal-dev`
  containers, network, and the `sklegal-dev-postgres-data` volume. No
  `sklegal-dev` container or volume remains.
- The concurrently running `sklegal-s102-*` integration containers of
  another session were not touched.

## Known limitations

- Single-run observations on an idle host; no load, chaos, or saturated-host
  qualification (reserved for S5-04B/C).
- The worker-kill RTO observation is bounded below by the 5 minute activity
  `StartToClose` timeout, not by detection speed.
- The PG restore smoke restored into a second database on the same instance;
  cross-instance and point-in-time recovery is unverified.
- Smoke workflow histories lived only in the disposable development stack
  and were removed with its volume.
