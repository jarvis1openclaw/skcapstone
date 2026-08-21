# External action state machine: failure edges and receipt reconciliation

Status: proposed architecture amendment, pending human approval through the
approval trail (see `docs/approval/AMENDMENT-SKL-S4-08.md`).
Card: `48fde7c1` (`SKL-S4-08`).
Grounding: `packages/domain/src/sklegal_domain/states.py` and
`packages/domain/src/sklegal_domain/entities.py`
(`Communication.TRANSITIONS`, `Execution.TRANSITIONS`, and their validators).

This document amends section 14 of the approved
`docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md`. The approved text records only
the happy path:

```text
draft -> validated -> approved -> queued -> dispatched -> receipt_verified
```

The domain package defines two additional terminal or recovery states,
`failed` and `cancelled`, plus reset and retry edges. This document records
the full graph so connector work (`SKL-S4-06`) and deadline or calendar
delivery (`SKL-S4-05`) implement the same edges the domain enforces.

## 1. State vocabulary

Two domain enums share the external-action lifecycle:

- `CommunicationStatus` governs the Communication record, which carries the
  reviewed payload, participants, channel, and gate references.
- `ExecutionStatus` governs the Execution aggregate, which carries the
  immutable artifact binding, destination digest, idempotency key, validation
  and approval snapshots, the append-only Execution Event history, and the
  Execution Receipt.

Both enums define the same eight values:

| State | Meaning |
|---|---|
| `draft` | Payload is editable. No gate evidence may exist. |
| `validated` | Validation result exists for the exact artifact. |
| `approved` | A human Approval binds the exact artifact ID, version, and digest. |
| `queued` | Destination is verified and bound by digest; dispatch is staged. |
| `dispatched` | The connector handed the exact artifact to the provider. |
| `receipt_verified` | A bound, matching receipt proves delivery. Terminal. |
| `failed` | Dispatch or receipt verification failed with a recorded reason. |
| `cancelled` | A human withdrew the action before dispatch. Terminal. |

## 2. Full transition graph

The blocks below are the authoritative edge lists. The parity test
`tests/test_external_action_state_docs.py` asserts they equal the domain
`TRANSITIONS` maps exactly. `none` marks a terminal state with no outbound
edges.

```text
Communication transition edges:
approved -> cancelled
approved -> draft
approved -> queued
cancelled -> none
dispatched -> failed
dispatched -> receipt_verified
draft -> cancelled
draft -> validated
failed -> queued
queued -> cancelled
queued -> dispatched
queued -> failed
receipt_verified -> none
validated -> approved
validated -> cancelled
validated -> draft
```

```text
Execution transition edges:
approved -> cancelled
approved -> queued
cancelled -> none
dispatched -> failed
dispatched -> receipt_verified
draft -> cancelled
draft -> validated
failed -> queued
queued -> cancelled
queued -> dispatched
queued -> failed
receipt_verified -> none
validated -> approved
validated -> cancelled
```

The two graphs differ only in the reset edges:

- Communication permits `validated -> draft` and `approved -> draft`. These
  are declared resets: the transition must clear all gate evidence
  (`validation_result_id`, `approval_id`, `destination_verified`,
  `destination_sha256`, `execution_id`), and payload fields may change only
  on a reset to `draft`. A reviewed payload can never silently change while
  retaining its review evidence.
- Execution permits no reset. Its subject `ArtifactBinding`,
  `destination_sha256`, and `idempotency_key` are immutable for the life of
  the aggregate. Correcting an approved execution means cancelling it and
  creating a new draft with new artifact binding.

## 3. Failure, cancel, and retry semantics

Failure edges:

- `queued -> failed`: staging or pre-dispatch checks failed (for example
  capability revocation, provider rejection at handoff, or destination
  re-verification failure).
- `dispatched -> failed`: the provider reported failure, the receipt window
  expired, or reconciliation rejected the provider response (section 4).
- There is no `draft`, `validated`, or `approved` to `failed` edge. A
  pre-queue problem is expressed by staying in place or cancelling, never by
  recording a failure for an action that was never staged.

Cancel edges:

- `cancelled` is reachable from `draft`, `validated`, `approved`, and
  `queued`. Cancellation is a human decision and is terminal.
- `dispatched` cannot be cancelled. Once the provider has the artifact, the
  only honest outcomes are `receipt_verified` or `failed`. A cancellation
  request arriving after dispatch must be recorded as a follow-up action (for
  example a recall Communication), not as a state rewrite.
- `failed` cannot be cancelled directly; it must first re-enter `queued`
  through the retry edge. This keeps the event history a faithful replay of
  every dispatch attempt.

Retry edges:

- `failed -> queued` is the only retry edge. Retry never jumps back to
  `dispatched` and never resets to `draft`.
- Retry reuses the same immutable `idempotency_key`. Providers that support
  idempotent submission deduplicate on that key, so a retry after an
  ambiguous provider outcome reconciles against the first attempt instead of
  sending twice (threat model B9: "reconciliation must use the idempotency
  key and provider receipt rather than issue another blind dispatch").
- Every retry appends a new `queued` Execution Event; the event history keeps
  the full attempt sequence.

## 4. Receipt reconciliation states

The domain deliberately has no reconciliation status enum. Reconciliation is
the connector workflow layer that decides which domain edge, if any, a
provider receipt justifies. This section defines the reconciliation states
required by threat model B9 (forged callbacks, ambiguous receipts, receipts
bound to another tenant or action) and by the High-impact case "a forged
connector receipt marking an external action complete and preventing
reconciliation".

An Execution Receipt is candidate evidence, never self-authorizing. The
domain already enforces these invariants before `receipt_verified` is
reachable:

- the receipt binds the same tenant, matter, and execution;
- `artifact_content_sha256` equals the approved artifact digest and
  `destination_sha256` equals the execution destination digest;
- `received_at` is not earlier than the last dispatch event;
- a receipt-verified Execution Event references the receipt ID, and a
  receipt ID exists only on that step.

Reconciliation states for a dispatched action:

| Reconciliation state | Definition | Required domain outcome |
|---|---|---|
| `receipt_pending` | Dispatched; within the provider receipt window. | Remain `dispatched`. No transition. |
| `receipt_matched` | Receipt authenticated, bound to the exact tenant, matter, execution, and idempotency key, and hashes match. | `dispatched -> receipt_verified` with the receipt attached. |
| `receipt_late` | Receipt arrives after the window expired and a failure was recorded, or after a retry was queued. | Reconcile by idempotency key before any new dispatch. If the receipt matches the execution, record the failure resolution evidence and transition through the declared graph (the retry path re-enters `queued`; a matching late receipt for the final dispatch satisfies `dispatched -> receipt_verified`). Never rewrite history. |
| `receipt_ambiguous` | Receipt cannot be bound to exactly one initiated action, or the provider response is internally inconsistent. | Leave the action unresolved in `dispatched`. Require human review. Never auto-verify. |
| `receipt_suspect` | Hash mismatch, wrong tenant, matter, or execution binding, unauthenticated callback, or a receipt claiming success for another action. | Reject the receipt. The domain invariants refuse the attachment. Keep the action unresolved, alert, and preserve the suspect receipt as evidence outside the Execution aggregate. |
| `receipt_missing` | The receipt window expired with no receipt. | `dispatched -> failed` with an explicit missing-receipt reason. Retry only through `failed -> queued`. |

Reconciliation rules:

1. Only `receipt_matched` may produce `receipt_verified`. Ambiguity, lateness
   without a binding match, and suspicion are never silently harmonized into
   success.
2. Suspect and ambiguous receipts are preserved as separate evidence records.
   The Execution Receipt attached to an Execution is immutable and set-once;
   a rejected candidate receipt is never edited into an acceptable one.
3. Receipt verification binds the callback to the initiated tenant, matter,
   action, and idempotency tuple before any state transition, and the
   callback is authenticated where the provider supports it.
4. Every reconciliation decision writes an audit event with the policy
   decision, the receipt identifiers, and the human reviewer where review was
   required. Fail closed: when binding or authenticity cannot be established,
   the action stays unresolved.

## 5. Invariants the connector layer must not weaken

- Every Execution transition appends exactly one Execution Event whose step
  matches the target state, preserving the exact prior event prefix. Event
  history is the replayable proof of the current state.
- Approval binds an exact artifact ID, version, and SHA-256 digest. An
  approval revoked after queueing blocks further progress; effect boundaries
  reauthorize against current policy and capability state.
- Queued and later Communication records bind an exact destination digest and
  cannot reset in place.
- Simulation mode remains the development default. Production dispatch is a
  separate per-connector approval and qualification event.
- No connector may skip validation, exact-version approval, destination
  verification, capability verification, and immutable audit, on the happy
  path or on any failure, cancel, or retry edge.
