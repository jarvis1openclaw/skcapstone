# SKL-S5-04C outage and restart qualification matrix

Status: approved for development qualification; production rollout remains
human-gated. This contract extends the SKL-S3-07 resilience smoke
(`docs/development/RESILIENCE-SMOKE.md`) with an outage matrix covering
process, service, and connectivity failure. It is a single-run qualification
matrix on the disposable development stack, not a chaos or soak test.

## Scope and boundaries

- All scenarios run against an isolated, disposable compose project with its
  own containers, volume, and network. The shared `sklegal-dev` project, the
  `skmem-pg` container, and any container belonging to another session are
  never stopped, restarted, or removed.
- Every workflow, tenant, matter, approval, and digest is synthetic. No live
  matter content, HammerTime path, production credential, or production
  database is in scope.
- Connectivity failures are imposed only against local synthetic endpoints
  inside the qualification harness. No remote endpoint, hosted API, or
  third-party service is contacted.
- The model providers are simulation adapters bound to synthetic transports
  that fail and recover on command. The OpenAI route never resolves a real
  secret: the outage matrix exercises the secret-resolution and transport
  failure paths with fakes, per the model boundary in `AGENTS.md`.
- An unavailable Docker daemon, a failed health check, or an operator error is
  a qualification failure, not a pass by omission.

## Outage matrix

Each scenario must produce a written result with observed timestamps, the
observable degraded state while the outage was active, and the post-recovery
verification. A scenario passes only when every check in its row passes.

| ID | Failure | Injected by | Observable degraded state | Recovery verification |
| --- | --- | --- | --- | --- |
| OM-1 | Worker process killed mid-activity (`kill -9`) | killing the worker pid after the activity start marker | worker absent; activity open; no receipt | step redelivered at the heartbeat timeout, identical result digest, exactly one dispatch receipt, workflow COMPLETED |
| OM-2 | Temporal service restart | `docker stop` then `docker start` of the temporal container | client queries fail while the frontend is down; workflow phase unchanged | workflow still queryable, phase preserved, completes after restart with one receipt |
| OM-3 | Local Qwen route unreachable | synthetic transport raising connection errors | typed `ProviderUnavailableError`; step retries within the MODEL budget | after transport recovery the next attempt produces the identical typed proposal; no partial proposal persisted |
| OM-4 | OpenAI egress route unreachable | synthetic transport and secret resolver raising errors | typed `ProviderUnavailableError` or `SecretResolutionError`; fail closed | after secret and transport recovery the identical request produces one proposal; no retry storm past the MODEL budget |
| OM-5 | Reconciler process restart after a watermark advance | constructing a fresh reconciler over the same ledger | in-memory expectation and views lost | `recover()` restores the watermark sequence, views rebuilt, next `reconcile()` advances without `WatermarkConflict` |
| OM-6 | Outbox backend outage during reconciliation | outbox port raising on every call | `ReconciliationUnavailable`; watermark not advanced | after backend recovery, `reconcile()` completes; no outbox message delivered twice; watermark monotonic |
| OM-7 | Dispatch replay after recovery (duplicate suppression) | replaying the identical `DispatchRequest` against the durable ledger | n/a (verification step) | second record returns the original receipt digest; a different payload under the same key is rejected |

## Recovery targets

The SKL-S3-07 numeric targets carry over unchanged: RPO 5 minutes or less,
RTO 15 minutes or less. This card adds the worker-kill redelivery bound: a
killed worker must be detected at the activity heartbeat timeout (60 seconds
interactive, 5 minutes batch and long-context, 30 seconds connector), not at
the `StartToClose` timeout, because `run_task_step` now heartbeats.

## Qualification procedure

1. Run `uv run --locked --package sklegal-worker pytest
   tests/test_outage_qualification.py -q` (model-level matrix) and confirm
   every scenario is green before touching the live stack.
2. Start the isolated stack with `./scripts/dev_dependencies.sh up --project
   sklegal-outage --postgres-port 25433 --temporal-port 27233`. The driver
   uses only this project.
3. Run `./scripts/outage_qualification.py run --address 127.0.0.1:27233
   --workdir <dir>`. The driver executes OM-1 and OM-2 end to end, writes one
   JSON result per scenario plus a combined matrix, and refuses to write PASS
   for any check that did not observe its expected evidence.
4. Inspect the written results, then run `./scripts/dev_dependencies.sh down
   --project sklegal-outage` to remove only the qualification containers.
5. Record per-scenario results, timestamps, and observed degraded state in
   the evidence file under `docs/evidence/platform/`.

## Evidence record

| Scenario | Result | Evidence |
| --- | --- | --- |
| OM-1 worker kill mid-activity | PASS (2026-08-22) | `docs/evidence/platform/SKL-S5-04C-OUTAGE-MATRIX-2026-08-22.md` |
| OM-2 Temporal restart | PASS (2026-08-22) | same |
| OM-3 Qwen route outage | PASS (2026-08-22, synthetic transport) | same |
| OM-4 OpenAI egress outage | PASS (2026-08-22, synthetic transport and fake resolver) | same |
| OM-5 reconciler restart | PASS (2026-08-22) | same |
| OM-6 outbox backend outage | PASS (2026-08-22) | same |
| OM-7 dispatch replay suppression | PASS (2026-08-22) | same |
