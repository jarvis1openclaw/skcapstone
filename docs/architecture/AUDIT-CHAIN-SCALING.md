# Audit chain-head scaling decision

Card: `2a41d17d` (`SKL-S3-09-FU`)
Author: `kimi`
Date: 2026-08-21
Status: proposed, pending human approval through `docs/approval/AMENDMENT-SKL-S3-09-FU.md`

## 1. Question

Migration `0007_append_only_audit_outbox.sql` serializes every audit append
for one Tenant through a single locked `sklegal_audit.chain_heads` row.
`SKL-S3-09` (card `1b0a7b29`, evidence
`docs/evidence/audit/SKL-S3-09-CHAIN-HEAD-SERIALIZATION-2026-08-21.md`)
measured the result: a per-Tenant ceiling of roughly 80 to 96 appends per
second, contention from 2 concurrent writers, and p99 append latency above
600 ms at 32 writers. This document evaluates the two candidate mitigations
from the follow-up card and records the decision.

## 2. Constraints that any mitigation must preserve

From migration 0007 and the approved architecture:

- Append-only, hash-chained events with a controlled writer (SECURITY
  DEFINER `append_event`, controlled-writer triggers, FORCE RLS).
- One atomic database transaction carries the protected mutation, its audit
  event, and its outbox row. Temporal history is never the evidence of
  record (see `docs/development/AUDIT.md`, connector dispatch ownership).
- Per-Tenant chain verification must keep detecting evidence-bearing
  rollback (`rollback_guard` sentinel plus
  `verify_current_tenant_chain()`).
- Projection watermarks must stay exact, monotonic, and idempotent.

## 3. Option A: per-Matter chain heads

Design sketch: `chain_heads` gains a chain key `(tenant_id, matter_id)`,
with the Matter-less Tenant-scoped chain kept for events whose
`matter_id IS NULL`. Each new chain anchors its genesis event to the
current Tenant tip hash so Tenant-wide continuity survives the cutover.

- Concurrency: independent Matters of one Tenant append concurrently. The
  ceiling becomes roughly 90 appends per second per chain, and aggregate
  Tenant throughput scales with active Matter count.
- Verification: per-chain verification plus a Tenant-wide aggregate that
  checks every chain and the chain inventory itself. Rollback detection per
  Tenant is preserved.
- Cost 1, event identity: `event_sequence`, the predecessor foreign key, and
  the uniqueness constraint all move from `(tenant_id)` scope to
  `(tenant_id, chain)` scope. Every consumer that orders by
  `event_sequence` must learn the chain dimension.
- Cost 2, projection order: per-chain sequences yield only a partial order
  across a Tenant. `projection_watermarks` and
  `advance_projection_watermark` currently assume one dense total sequence
  per Tenant and would need a vector watermark (one entry per chain per
  projection). This is the largest contract change and touches the outbox
  polling, reconciliation, and cutover machinery built in S2-07.
- Cost 3, evidence semantics: a Tenant-wide cryptographic total order is
  lost. Cross-Matter ordering degrades to `recorded_at` comparison, which is
  weaker evidence for disputes that span Matters.

## 4. Option B: batch append

Design sketch: one chain-head lock acquisition appends N events
(`append_events` with an array payload), raising burst throughput by up to
N while leaving single-event latency unchanged.

- Collision with the atomicity constraint: batching across protected
  transactions would defer audit writes outside the committing transaction,
  which breaks the rule that the mutation, its audit event, and its outbox
  row commit atomically. That rule is load-bearing for the connector
  dispatch and evidence handoff design.
- In-transaction batching only: most protected transactions emit one or two
  audit events, so realistic batch factors are small. A micro-batching
  repository with a flush window adds crash-loss surface for buffered
  events and new exactly-once complexity for little measured gain.
- Verdict: rejected as the primary mitigation. In-transaction batching
  remains a permitted local optimization where one workflow step genuinely
  emits several events in one transaction.

## 5. Decision

1. **No schema change before or during the Sprint 5 pilot.** The pilot is a
   single-Matter, single-Tenant exercise whose steady-state audit rate is
   far below the measured ceiling; there is no numeric Sprint 5 audit-rate
   target in `docs/planning/` today, and S5-04B owns deriving the load
   envelope. The single-chain design stands.
2. **Per-Matter chain heads are the approved design direction** when a
   mitigation is required, because batch append conflicts with the atomic
   audit transaction guarantee. Implementation is deferred and gated.
3. **Trigger gate.** The mitigation becomes eligible work when any of these
   hold: a Tenant must sustain more than 50 appends per second (half the
   measured ceiling); measured p99 append latency exceeds 100 ms at pilot
   production concurrency; or any multi-tenant load claim beyond the pilot
   is made in Sprint 5 or later. S5-04B load qualification must measure
   against these thresholds and cite this document.
4. **Preconditions for implementation.** When the trigger fires, the
   per-Matter chain design must deliver: chain-scoped sequence and
   predecessor constraints, genesis anchoring to the Tenant tip, per-chain
   plus Tenant-wide aggregate verification, vector projection watermarks,
   and a migration rehearsal proving evidence-bearing rollback detection
   across the cutover. Each item requires its own eligible card, tests, and
   dry run.

## 6. Consequences

- `SKL-S1-05B` (durable audit production composition) may proceed against
  the current single-chain design without waiting on this mitigation.
- `SKL-S5-04B` derives the pilot load envelope and checks the trigger
  thresholds above.
- The benchmark harness from `SKL-S3-09`
  (`scripts/benchmark_audit_chain_head.py`) is the standing measurement
  tool; any future chain-design change re-runs it for before and after
  numbers on the same host class.
