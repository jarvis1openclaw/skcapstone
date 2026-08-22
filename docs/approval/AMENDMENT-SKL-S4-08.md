# Amendment record: external action state machine failure edges and receipt reconciliation

Amendment ID: `AMENDMENT-SKL-S4-08`
Card: `48fde7c1` (`SKL-S4-08`)
Recorded by: `kimi-skl-s4-08`
Status: approved

## Human decision

2026-08-21: the human owner approved this amendment.
`docs/architecture/EXTERNAL-ACTION-STATE-MACHINE.md` is approved
architecture guidance, recorded at hash:

```text
8a67cf4adff11056939a8e16b454972fa6a1af69a44efd07946666d4255e7156  docs/architecture/EXTERNAL-ACTION-STATE-MACHINE.md
```

## Baseline

This amendment extends the approved architecture without modifying any
approved document. The baseline is pinned by
`docs/approval/DESIGN-HASHES.sha256`, including:

```text
63a6134dfedc2e1acd047d3b993e1ecfe6b2ac4262cd469caaacf2fc7c7cbc30  docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md
```

## Amendment

The approved section 14 of `SKLEGAL-HIGH-LEVEL-TDD.md` records only the
happy path `draft -> validated -> approved -> queued -> dispatched ->
receipt_verified`. The domain package
(`packages/domain/src/sklegal_domain/states.py`, `entities.py`) defines the
fuller graph with `failed`, `cancelled`, reset, and retry edges, and the
threat model (B9) requires receipt reconciliation for ambiguous, late, and
forged receipts.

This amendment adds one new document:

- `docs/architecture/EXTERNAL-ACTION-STATE-MACHINE.md`: the full
  architecture-level transition graph for `CommunicationStatus` and
  `ExecutionStatus`, the failure, cancel, and retry semantics, the receipt
  reconciliation states (`receipt_pending`, `receipt_matched`,
  `receipt_late`, `receipt_ambiguous`, `receipt_suspect`, `receipt_missing`),
  and the invariants the connector layer must not weaken.

The amendment is guarded by `tests/test_external_action_state_docs.py`,
which asserts the documented edge lists equal the domain `TRANSITIONS` maps
exactly. Any future divergence between the documented graph and the domain
graph fails the unit test gate.

## Approval trail effect

- No hash-pinned approved document was modified.
- The new document takes effect for implementation work only after the human
  owner approves this amendment. Until then it is proposed architecture
  guidance, and connectors remain simulation-only under the existing gates.
- Approval of this amendment should be recorded by appending the new
  document hash to the approval trail and updating this record's status.
