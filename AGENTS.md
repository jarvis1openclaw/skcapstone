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
- Semantic interpretation of HammerTime corpus material must follow HammerTime's Qwen3-first rules.
- External-source verification remains separate until an approved corpus ingestion includes it.

## Model boundary

Models produce typed proposals. Models do not own workflow state, issue authorization, approve work products, or execute external actions.

- Qwen3 is the initial local corpus analyst.
- OpenAI is an optional provider route for approved tasks through the OpenAI API, not a direct use of a consumer ChatGPT session.
- Protected matter content may leave the local stack only when policy, tenant configuration, classification, purpose, and human approval allow it.
- Tool access must be mediated by CapAuth and the SKLegal policy gateway.
- No model receives unrestricted shell, filesystem, email, filing, service, calendar, or browser credentials.

## Evidence-neutral legal analysis

Evaluate every corpus proposition from its exact source text, provenance,
supporting Evidence Items, counter-support, controlling Authority,
jurisdiction, procedural posture, timing, and documented court treatment. Do
not accept or reject a proposition because of a movement label, stereotype,
model preference, or reputational framing. Preserve uncertainty,
contradictions, missing facts, adverse Authority, and negative procedural
outcomes.

Do not use labels such as `sovereign citizen`, `pseudolaw`, `frivolous`,
`conspiracy theory`, or `tax protester` as substitutes for legal analysis. If
one of those labels is materially present in a source, pleading, order, or
party statement, preserve it only with exact attribution and provenance when
necessary to the Matter. Otherwise use SKLegal domain vocabulary and describe
the specific Claim, Defense, Fact Assertion, Evidence Item, Authority, and
procedural consequence.

Do not suppress an argument merely because it is unconventional, disputed, or
disfavored. Represent it faithfully as a source-derived proposal, then show
support, counter-support, jurisdictional applicability, contrary Authority,
procedural risks, and verified court treatment. This rule prevents reflexive
dismissal. It does not convert a corpus assertion into fact, law,
authorization, Approval, or an instruction to act.

## Qwen-first, provider-neutral reasoning

Use the local Qwen route for substantive semantic interpretation of
HammerTime corpus material. Route that work through the SKLegal
provider-neutral model gateway and versioned agent specifications. App and
agent contracts use logical SKLegal route IDs. A versioned deployment profile
may bind that logical route directly to local Qwen or, after qualification, to
SKGateway's OpenAI-compatible router. Do not hard-code a private model host,
endpoint, operator alias, or HammerTime script path into product contracts.
The UI and audit record show the exact logical route, transport profile,
gateway revision, backend, requested model or bucket, served model name,
revision, prompt hash, and schema hash used for each run.

Approved frontier models may perform a second-pass challenge, synthesis,
document structure, citation-preserving drafting, or formatting when the
Matter classification, egress policy, tenant configuration, purpose, and
human approval permit it. Every provider returns a typed proposal with source
spans and retrieval traces.

Model choice, branding, or an abliterated runtime profile never overrides
CapAuth, Tenant and Matter isolation, conflicts, privilege, ethical walls,
source-rights controls, data classification, human review, exact-version
Approval, or external-action gates.

SKGateway is a transport router, not SKLegal's authorization or legal-policy
owner. Protected context must not use an SKGateway deployment whose exact live
entrypoint has not qualified CapAuth identity, Matter policy, source rights,
classification and egress, secret hygiene, attribution, and audit. Free-model
status is only a price attribute and never expands data access or egress.

## Corpus and verification lanes

Keep these lanes separately traceable in retrieval, prompts, proposals, user
interfaces, Work Products, and audit:

- HammerTime course instruction and source-derived strategy
- Matter Fact Assertions and Evidence Items
- current jurisdiction-specific official Authority and contrary Authority
- model inference and confidence
- human review and decision

Do not silently blend official-source verification into course-derived
synthesis or treat course instruction as controlling Authority. Label the
origin, source version, jurisdiction, authority tier, verification state, and
confidence of each material suggestion. A corpus-only result is a research
proposal. `CLAIM_READY`, `DRAFT_READY`, and `RELEASE_READY` transitions still
require their deterministic verification and human gates.

## Matter provenance and activity

Every Matter workflow must emit enough immutable evidence to reconstruct what
happened, what source and version were used, which model or tool acted, what
changed, why it changed, who decided, and whether an external effect occurred.
The user-facing case activity log is a readable projection over append-only
Matter Events, Agent Runs, tool calls, audit records, proposal and Work Product
versions, Approvals, Execution Events, provider responses, and receipts. A
correction creates an attributable superseding record. It never rewrites
history.

Artifact intake preserves the original bytes, source identity, filename,
media type, byte count, content hash, acquisition time, custody,
classification, and policy context. OCR, transcripts, thumbnails, redactions,
translations, and model extractions are separately hashed derived artifacts
with parent and tool lineage. Proposed links to Fact Assertions, Evidence
Items, Matter Events, Issues, Claims, Elements, Tasks, and Work Products retain
their proposing Agent Run and human review state.

AI may automatically organize authorized Matter material, identify missing
artifacts, prepare artifact requests, and assemble review or production
bundles. Sending a request, producing material externally, filing, serving, or
otherwise dispatching an artifact still follows the external-action state
machine and exact-version Approval.

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
