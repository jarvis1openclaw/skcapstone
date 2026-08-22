# SKL-S3-07 resilience smoke contract

Status: approved for development qualification; production rollout remains
human-gated.

This contract pulls a small recovery smoke into Sprint 3. It is a confirmation
of workflow and data-recovery behavior, not a full load or chaos test. All
service tests use the pinned development compose stack and disposable data.
No live matter, HammerTime `Inbox/`, production credential, or production
database is in scope.

## Recovery targets

| Boundary | Target | Measurement |
| --- | ---: | --- |
| Recovery point objective | 5 minutes or less | Latest durable PostgreSQL backup timestamp before the failure |
| Recovery time objective | 15 minutes or less | Failure observation to a healthy restored service accepting a probe |
| Worker interruption | No duplicate mutation or dispatch | Replayed workflow event log and receipt count |
| Temporal restart | No lost workflow history | Workflow query and completion after restart |
| PostgreSQL restore | Source marker and schema survive | Scratch restore probe and migration readback |

The RPO target applies to the canonical SKLegal PostgreSQL database. Temporal
history is durable workflow state, but it is not the legal audit record. The
audit ledger and its outbox remain the source of record after recovery.

## Qualification procedure

1. Run `./scripts/run_checks.sh unit-test` and retain the worker-kill replay
   result. The test resumes from every event boundary and requires exactly one
   dispatch and one applied signal.
2. Start the pinned development dependencies with
   `./scripts/dev_dependencies.sh up`.
3. Create a synthetic workflow and a scratch PostgreSQL marker. Record the
   backup timestamp, then stop the worker during an activity and restart it.
   Confirm the workflow completes without a second mutation or receipt.
4. Restart the Temporal development service. Confirm the synthetic workflow
   remains queryable and completes.
5. Dump the scratch PostgreSQL database, stop the database, restore the dump
   into a separate scratch database, and verify the marker and migration
   version. Record elapsed recovery time and the backup age.
6. Remove only the named development containers, scratch database, and dump.

The operator must attach command output and timestamps to the evidence record.
An unavailable Docker daemon, missing backup tool, or failed health check is a
qualification failure, not a pass by omission.

## Evidence record

The current repository evidence is the automated worker-kill replay test in
`tests/test_worker_workflows.py`. It proves deterministic resume behavior in
the workflow model. The Temporal restart and PostgreSQL backup-restore rows
remain pending until the disposable development stack is available and an
operator records their timestamps and results.

| Smoke | Result | Evidence |
| --- | --- | --- |
| Worker kill and replay | PASS | `WorkerKillReplayTests.test_resume_from_every_boundary_reproduces_identical_log` |
| Temporal restart | PENDING | The 2026-08-21 disposable stack did not become healthy within the bounded probe window; retry required |
| PostgreSQL backup and restore | PENDING | Not run after the Temporal prerequisite failed; retry required |

The failed attempt was cleaned up with the named development compose project,
including its disposable volume and network. It did not touch a production
service or protected data.

No production deployment, external action, or protected-data migration is
authorized by this contract.
