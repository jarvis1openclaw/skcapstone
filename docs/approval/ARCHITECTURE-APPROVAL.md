# SKLegal architecture approval record

Decision timestamp: 2026-08-19T21:32:57-05:00  
Decision: Approved  
Human owner: `skuser01`  
Board gate: `SKL-S0-01`, card `b04de409`

## Human decision

The human owner explicitly approved the SKLegal architecture and authorized completion of the architecture approval gate.

The received statement ended with `SKL-S0-0`. It is recorded as approval of `SKL-S0-01` because:

- `SKL-S0-01` is the unique human-only SKLegal architecture approval card.
- The approval review page names `SKL-S0-01` in its approval phrase.
- No card named `SKL-S0-0` exists.
- The statement explicitly approves the SKLegal architecture.

This resolution does not complete any other Sprint 0 card.

## Approved scope

The approval covers:

- the governed multi-tenant SKLegal architecture
- the Liberty Auto Plaza single-matter pilot boundary
- HammerTime as initial corpus and original-artifact owner
- legal-domain terminology for new canonical records
- CapAuth enforcement from the foundation
- Qwen as the initial local corpus analyst
- OpenAI as an optional policy-controlled API provider
- Temporal-owned durable workflow state
- typed agent proposals with human approval for consequential transitions
- simulation-first external-action connectors
- sequential, evidence-gated sprints and task designs

## Approved document hashes

```text
63a6134dfedc2e1acd047d3b993e1ecfe6b2ac4262cd469caaacf2fc7c7cbc30  docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md
0705159e43f6dd28beda4b6cbcb72444b4e23678408bbbbccc76f1903d76c7a4  docs/architecture/LIBERTY-AUTO-PILOT-TDD.md
836b1604564c863f3614f3ddf5b49aaa4d340ff70968a004dfa4a42dbb304496  docs/planning/EPIC-SPRINT-PLAN.md
4debdeb5b6db65840aa8ac11eae2d684bb619f7af1af9535cb1b22fe90776fd1  docs/tasks/SUBAGENT-TASK-TTDS.md
4402c7dc2bdb9ed245b6d581d325f9eaa887b191d9a209737b0a1dcf2e3b4005  docs/approval/index.html
```

The approved documents remain unchanged by this approval record.

## Gate effect

- Complete `SKL-S0-01` as approved.
- Unblock dependent cards, subject to their own dependencies, claims, tests, and acceptance criteria.
- Keep `SKL-S0-02` through `SKL-S0-05` open until independently verified.
- Do not treat architecture approval as approval to dispatch external legal actions, migrate additional matters, process HammerTime `Inbox/`, create external accounts, or bypass task-specific human gates.

## Verification evidence

- All five approved hashes passed `sha256sum -c` immediately before recording approval.
- The review page and Markdown sources contain the approved HammerTime, CapAuth, model-provider, pilot, and simulation-first action boundaries.
- Sprint 0 implementation cards `SKL-S0-02` through `SKL-S0-05` directly depend on card `b04de409`.
- Before this approval was claimed, the board reported zero claimed and zero in-progress tasks.
- No SKLegal application implementation agent was dispatched before approval.

## Amendment trail

- 2026-08-21, card `c976908e` (`SKL-S1-10`): Repaired the development
  contracts `docs/development/PERSISTENCE.md` and
  `docs/development/CAPAUTH.md` to document the real migration surface,
  migrations 0008 through 0012 (durable CapAuth revocation, replay
  reservation, principal snapshot, authentication subject, and
  `sklegal_runtime` grants). Development contracts are not hash-pinned; the
  five approved documents and `docs/approval/DESIGN-HASHES.sha256` remain
  unchanged and verified clean.
- 2026-08-21, card `c976908e` (`SKL-S1-10`): Flagged a model naming error for
  correction at the next approved document revision. The approved documents
  name the initial local corpus analyst `Qwen3.8`
  (`docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md`,
  `docs/architecture/LIBERTY-AUTO-PILOT-TDD.md`,
  `docs/planning/EPIC-SPRINT-PLAN.md`, and `docs/approval/index.html`).
  `Qwen3.8` is not a real Qwen model identifier; the intended deployment is
  the local Qwen3 route operated by HammerTime on chiap08. Because the
  approved documents are hash-pinned, no silent edit was made. The next
  approved revision should correct the name and re-pin the inventory through
  this approval process.

