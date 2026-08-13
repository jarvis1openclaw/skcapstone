# ai-runner go-live checklist

The fleet suggestion engine's runner (`skcapstone.agent_run.run_ai_runner_job`,
scheduled by `jobs.yaml` on noroc2027) is **plan-only** today: with
`SKAI_RUNNER_LIVE` unset it records a plan and moves the card to review, never
dispatching a live agent. This runbook is the gate for turning real dispatch on.

## Safety model (what is already enforced)

- **`SKAI_RUNNER_LIVE`** (default unset) is the master switch. Unset ⇒ no live
  dispatch at all.
- **Execute is fail-closed (R1).** Even with `SKAI_RUNNER_LIVE=1`, an `execute`
  run is NEVER sent to the raw `claude -p` dispatcher. It requires an explicitly
  wired sandboxed/graded executor via `agent_run.set_execute_dispatcher(fn)`.
  With none wired, execute records a plan and moves to review (`gated=True`,
  reason "execute requires the sandboxed executor (R1)"). Propose/dry-run use
  the passed-in dispatcher (no real side effects).
- **Defense in depth.** `claude_dispatcher` itself refuses `mode == "execute"`,
  so even mis-wiring it as the execute dispatcher cannot run an execute run raw.
- **Kind gate.** `gate()` blocks execute on `change` kinds (CAB vote required)
  and clamps GTD execute to draft-only (never auto-send).
- **Queue authz.** Queuing an execute run needs the `agentrun.execute`
  capability (verified enrollment) once `SKAI_AUTHZ=pdp|both`
  (skdashboard.queue_authz); the assistant surface cannot queue execute at all.

## Before setting SKAI_RUNNER_LIVE=1

1. **Wire the sandboxed executor.** Implement and register the
   `skharness.autocode` bridge and call `agent_run.set_execute_dispatcher(bridge)`
   at runner startup. The bridge must construct a `skharness.autocode` WorkItem
   from the run context, run it through sandbox → grade → twin-gate, and return
   `{"summary", "activity", "links"}` with a DRAFT PR link, never an auto-merge.
   (Follow-up card: "wire skharness.autocode execute dispatcher".)
2. **Verify propose/dry-run first.** Enable live dispatch with execute still
   fail-closed; confirm propose/dry-run runs behave (plans/scratch diffs land in
   review) before trusting execute.
3. **Flip authz.** Set `SKAI_AUTHZ=both` (token + PDP) so execute requires
   `agentrun.execute` (verified). Confirm an unverified caller is denied.
4. **Confirm no off-loopback exposure** of the queue routes until PDP is on
   (R2). The dashboard queue gate is loopback-open only while neither
   `SKAI_AUTHZ` nor `SKAI_QUEUE_TOKEN` is set.
5. **Canary one card.** Queue a single low-risk execute run, watch it produce a
   draft PR, review it, then widen.

## Turning it on

```bash
# On the runner node (noroc2027), only after steps 1-5:
systemctl --user set-environment SKAI_RUNNER_LIVE=1   # or in the job unit env
# restart the scheduler / job runner
```

To turn off instantly: unset `SKAI_RUNNER_LIVE` and restart. Execute reverts to
fail-closed; queued runs record plans again.
