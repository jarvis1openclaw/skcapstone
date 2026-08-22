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

## Original approved document hashes

```text
63a6134dfedc2e1acd047d3b993e1ecfe6b2ac4262cd469caaacf2fc7c7cbc30  docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md
0705159e43f6dd28beda4b6cbcb72444b4e23678408bbbbccc76f1903d76c7a4  docs/architecture/LIBERTY-AUTO-PILOT-TDD.md
836b1604564c863f3614f3ddf5b49aaa4d340ff70968a004dfa4a42dbb304496  docs/planning/EPIC-SPRINT-PLAN.md
4debdeb5b6db65840aa8ac11eae2d684bb619f7af1af9535cb1b22fe90776fd1  docs/tasks/SUBAGENT-TASK-TTDS.md
4402c7dc2bdb9ed245b6d581d325f9eaa887b191d9a209737b0a1dcf2e3b4005  docs/approval/index.html
```

The approved documents were unchanged when this original approval record was
created. The historical hashes above remain immutable evidence; later approved
revisions are recorded separately below.

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
  approved documents are hash-pinned, no silent edit was made. The scoped
  S2-10 retrieval amendment below did not include approval to change the model
  name. That correction still requires a separate explicit owner decision and
  a later re-pin through this approval process.
- 2026-08-21, card `f5b93935` (`SKL-S2-10`): The owner approved the scoped
  replacement of Qdrant and FalkorDB as primary protected retrieval stores.
  Current retrieval uses separate `sklegal-core-pg` and
  `sklegal-retrieval-pg` PostgreSQL 17 clusters, built-in full-text search,
  exact pgvector, and optional qualification-gated Apache AGE. Qdrant and
  FalkorDB remain HammerTime compatibility and shadow provenance only. The
  full owner statement, preserved boundaries, independent review, original
  hashes, and current hashes are recorded in
  `docs/approval/AMENDMENT-SKL-S2-10.md`.
- 2026-08-21, card `a4fcdd8e` (`SKL-S2-09`): The owner approved the
  provenance path correction in `docs/architecture/LIBERTY-AUTO-PILOT-TDD.md`.
  The pilot source path now reads the canonical
  `/mnt/cloud/onedrive/projects/DAVE-AI/hammerTime/...` root. The legacy
  space-spelled path survives only as a provenance alias in
  `docs/approval/AMENDMENT-SKL-S2-09.md`.
- 2026-08-21, card `3825cca4` (`SKL-S3-08`): The owner approved the
  database-login scaling decision.
  `docs/architecture/POSTGRES-PRINCIPAL-SCALING.md` (sha256
  `4d7a8b628a0db1f1dfda09e902a88e27e532ec105e9175c86a46ca8303e3bd88`) is the
  governing decision: a shared transaction-pooled `sklegal_runtime` login is
  permitted only when identity comes from a short-lived, one-use,
  database-owned authorization-context lease minted by a separate CapAuth and
  policy broker identity. Caller-set tenant and Principal variables remain
  rejected and all roles stay NOBYPASSRLS. Implementation, production
  deployment, credential creation, and role retirement require separate
  eligible cards.
- 2026-08-21, card `ea2c9790` (`SKL-S4-07`): The owner approved the connector
  dispatch ownership decision recorded in `docs/development/AUDIT.md`
  (section "Connector dispatch ownership (SKL-S4-07)") and in
  `docs/approval/AMENDMENT-SKL-S4-07.md`. Temporal owns connector dispatch
  orchestration; the polled PostgreSQL outbox owns only content-free evidence
  handoff and derived projections and never initiates a provider call.
- 2026-08-21, card `48fde7c1` (`SKL-S4-08`): The owner approved the external
  action state machine amendment.
  `docs/architecture/EXTERNAL-ACTION-STATE-MACHINE.md` (sha256
  `8a67cf4adff11056939a8e16b454972fa6a1af69a44efd07946666d4255e7156`) is
  approved architecture guidance, guarded by
  `tests/test_external_action_state_docs.py`.
- 2026-08-21, flagged by card `c976908e` (`SKL-S1-10`) and corrected under the
  owner decision recorded in `docs/approval/AMENDMENT-SKL-MODEL-NAMING.md`:
  the initial local corpus analyst is named `Qwen3` (the HammerTime-operated
  local route on chiap08). The four approved documents that carried the
  `Qwen3.8` token were corrected and re-pinned in this revision.
- 2026-08-21, card `2a41d17d` (`SKL-S3-09-FU`): The owner approved the audit
  chain-head scaling decision.
  `docs/architecture/AUDIT-CHAIN-SCALING.md` (sha256
  `da578eeb877aec9b82771a71164b9f079f9c3a619deda2599d6158a2daafd217`) is the
  governing decision: no schema change before or during the Sprint 5 pilot,
  per-Matter chain heads are the design direction if the numeric trigger
  gate fires, and batch append is rejected as the primary mitigation. No
  migration, code, or deployment change is authorized by this amendment.

## Current approved document hashes

These values match `docs/approval/DESIGN-HASHES.sha256` after the approved
S2-10 retrieval amendment, the approved S2-09 provenance path correction, and
the approved model-naming correction:

```text
033d09ffaf715a825fb64e15b7b69ba388ca6a0e6775aa82cc2465bec813910f  docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md
5c796030a888a7bebb197f55b2dde44d14711092856c752d03997907d05b85fd  docs/architecture/LIBERTY-AUTO-PILOT-TDD.md
a6c4e60759d10290fe4936072af4bf4a0ff850946dcf4bc4e70d0f90a2539ba4  docs/planning/EPIC-SPRINT-PLAN.md
7d7155e6cb23566e9cee3226de176a9e14999c5174e30fb24f75185dfc3de8cc  docs/tasks/SUBAGENT-TASK-TTDS.md
3155d2567280ca69b94040a4f5b57befac059b54fac8ac9167f703f23fd4f4a2  docs/approval/index.html
```
