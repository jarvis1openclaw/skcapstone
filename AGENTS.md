# SKLegal agent instructions

## Current gate

The human owner approved the SKLegal architecture on 2026-08-19. The approval is recorded in `docs/approval/ARCHITECTURE-APPROVAL.md` and SKCapstone card `b04de409`.

Implementation may proceed only through an eligible, explicitly claimed SKCapstone card after all of that card's dependencies are complete. Architecture approval does not authorize production deployment, external account creation, HammerTime `Inbox/` processing, additional matter migration, or outbound legal actions unless the assigned task expressly authorizes that work and its human gates are satisfied.

## Required startup

At the start of SKLegal work, run:

```bash
"${CODEX_HOME:-$HOME/.codex}/bin/load-sk-agent-context.sh"
skcapstone coord status
```

Read the assigned task TDD in `docs/tasks/SUBAGENT-TASK-TTDS.md`. Claim the exact SKCapstone card before modifying files. Do not broaden the assigned task.

## Product vocabulary

Use legal-domain terminology in new code and user interfaces:

- Client
- Engagement
- Matter
- Matter Event
- Proceeding
- Party and Party Role
- Issue
- Fact Assertion
- Evidence Item
- Authority
- Claim and Defense
- Deadline
- Task
- Communication
- Work Product
- Approval
- Execution Event

Do not use `Problem` or `Incident` as new canonical domain types. Preserve `PRB-*`, `INC-*`, legacy slugs, and legacy paths only through provenance aliases and migration records.

## HammerTime boundary

HammerTime remains the source and provenance owner for the initial corpus, original matter artifacts, decompositions, releases, and existing process outputs.

- Do not search, read, move, or process HammerTime `Inbox/` unless the assigned task explicitly authorizes an SKLegal ingestion test.
- Do not write directly to arbitrary HammerTime paths.
- New ingestion must use the existing HammerTime intake, completion-evidence, release, and archive contracts.
- Any HammerTime workflow modification requires its own task, tests, dry run, and rollback plan.
- Semantic interpretation of HammerTime corpus material must follow HammerTime's Qwen3.8-first rules.
- External-source verification remains separate until an approved corpus ingestion includes it.

## Model boundary

Models produce typed proposals. Models do not own workflow state, issue authorization, approve work products, or execute external actions.

- Qwen3.8 is the initial local corpus analyst.
- OpenAI is an optional provider route for approved tasks through the OpenAI API, not a direct use of a consumer ChatGPT session.
- Protected matter content may leave the local stack only when policy, tenant configuration, classification, purpose, and human approval allow it.
- Tool access must be mediated by CapAuth and the SKLegal policy gateway.
- No model receives unrestricted shell, filesystem, email, filing, service, calendar, or browser credentials.

## Multi-tenant security

- Every protected record carries `tenant_id` and `matter_id` where applicable.
- Client and matter membership, conflicts, privilege, ethical walls, retention, and legal hold are enforced before retrieval.
- Profile ownership metadata is migration input, not authorization.
- Fail closed when a policy decision is unavailable.
- Do not place secrets or raw capability tokens in prompts, logs, workflow history, or committed files.

## External actions

Email, filing, service, mailing, calendar, and client-communication connectors use this state machine:

```text
draft -> validated -> approved -> queued -> dispatched -> receipt_verified
```

No connector may skip validation, exact-version approval, destination verification, capability verification, and immutable audit. Development and tests use simulation mode by default.

## Engineering rules

- Write tests with each task and run the smallest relevant test set plus integration tests for changed boundaries.
- Use idempotency keys for mutations and external actions.
- Use an outbox for derived projections and connector dispatch.
- Preserve source hashes, version history, uncertainty, contradictions, and review decisions.
- Never silently harmonize conflicting facts or mixed timing rules.
- Never use an em dash or en dash in chat, code comments, documentation, or generated artifacts.
- Use `rg` for repository search.
- Use `apply_patch` for file edits.
- Do not commit or push unless the assigned task explicitly requests it.

## Completion evidence

A subagent completes a task only when it provides:

- files changed
- tests and exact results
- acceptance criteria evidence
- known limitations
- migration or rollback evidence when data changes
- linked SKCapstone task update
