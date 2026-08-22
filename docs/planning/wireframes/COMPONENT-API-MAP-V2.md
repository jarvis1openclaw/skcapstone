# SKLegal AI-first Matter analysis component map

Date: 2026-08-22
Card: `SKL-UI-03` (`ecf6d536`)
Companion: `docs/planning/wireframes/index-v2.html`

This map separates current repository bindings from contracts that later
cards still owe. Missing surfaces are named by package and owning work family.
No literal endpoint is invented for an unimplemented contract.

## Current read surfaces

| Surface | Current binding | State |
| --- | --- | --- |
| Client and Matter lists | `GET /v1/clients`, `GET /v1/matters` | Implemented |
| Client and Matter detail | `GET /v1/clients/{id}`, `GET /v1/matters/{id}` | Implemented |
| Matter workspace aggregate | `GET /v1/matters/{id}/workspace` | Implemented |
| Parties, timeline, facts, tensions, evidence, communications, gaps, audit, provenance | `MatterWorkspaceRead` in `services/api/src/sklegal_api/workspace.py` | Implemented |
| Route capability | `matter.read` through `matterDetail` route requirement | Implemented UX gate plus server authorization |
| Qwen and OpenAI corpus summary proposal routes | `packages/model_gateway` and `config/model_gateway/route-registry.json` | Implemented narrow summary contract |
| Versioned bounded agent registry and tool gateway | `packages/agents` | Implemented foundation |
| Claim, Defense, Element, Authority, support, and counter-support entities | `packages/domain` and `packages/persistence` | Implemented domain and persistence foundation |

## Version 2 cockpit map

| UI region | Domain or service binding | Current state | Owning work family |
| --- | --- | --- | --- |
| Matter scope header | Current workspace aggregate plus Forum and Proceeding | Partial, Forum and Proceeding fields missing from response | S4-02 follow-up |
| Analysis request composer | Versioned agent input contract | Missing | S3-03 and S5-02 |
| Workflow ribbon | Temporal workflow state plus sanitized Agent Run state | Package foundation only | S3-01, S3-03, S5-02 |
| Ranked recommendation queue | New typed recommendation proposal and scoring policy | Missing | S3-05 and S5-02 |
| Essential Elements proof matrix | Issue, Claim, Defense, Element, Fact Assertion, Evidence Item, Authority | Domain and persistence exist, no analysis HTTP surface | S3-05 and S4-03 |
| Matter artifact intake | Source Artifact Reference, Evidence Item, Custody Event, import and extraction lineage | Domain and ingestion foundations are distributed, unified Matter intake UI and API are missing | S2-02, S2-03, S4-03, and a scoped UI contract |
| Missing-artifact request builder | Proof gap, proposed Task, proposed Communication, requested artifact specification | Missing joined contract | S3-05, S4-03, and S4-06 |
| Course strategy panel | HammerTime read-only adapter plus policy-filtered retrieval | Package surface only | S2-04 and S4-03A |
| Jurisdiction overlay | Authority applicability, effective time, official-source connectors, Deadline engine | Partial foundations, no joined Matter view | S2-06, S3-05B, S4-05 |
| Source-span viewer | Retrieval result, source hash, release, trace, exact locator | Package surface only | S4-03A |
| Support and counter-support panel | Claim ledger entities | Domain and persistence exist, no HTTP surface | S4-03B |
| Blind challenge panel | Challenge proposal, defects, human disposition | Missing | S3-05C and S5-02B |
| Work Product assembler | Work Product domain plus `workproduct.draft` tool contract | Domain and tool output exist, no drafting route or API | S4-04 |
| Model evidence drawer | Model gateway `Proposal` evidence plus Agent Run evidence | No HTTP surface | S3-03 and S5-02 |
| Deployment transport profile | Logical SKLegal route to direct Qwen, SKGateway Chat Completions, or direct OpenAI Responses binding | Missing | SKL-S3-10, `bbf206c3` |
| SKGateway route evidence | Requested model or bucket, gateway commit and config, backend, capacity domain, bucket member, exact served model, retry and failover | Upstream foundation exists, SKLegal evidence contract missing | SKL-S3-10, `bbf206c3` |
| Human decision panel | Approval domain plus exact artifact or proposal binding | General decision surface missing | S3-05 and S4-04 |
| Matter activity log | Matter Events, Agent Runs, tool calls, Tasks, Decisions, versions, Approvals, Execution Events, and receipts | Current workspace audit read slice plus durable audit foundation, joined projection and exports missing | S1-05, S4-02, and a scoped projection contract |
| Artifact lineage and production bundle | Source Artifact Reference, content hash, derived-artifact parent, Evidence Item, Work Product version, Approval, and receipt | Foundations are distributed, joined lineage and bundle API are missing | S2-03, S4-03, S4-04, and S4-06 |
| External-action handoff | Connector state machine and simulation harness | Implemented foundation, qualification gated | S4-06 and S5-03 |

## Recommendation proposal shape owed to the UI

The implementing task should define a strict schema containing:

- recommendation identity, version, Matter scope, and scoring-policy version
- targeted Issue, Claim or Defense, Element, and Proceeding phase
- proposed Task or Work Product type
- reason, urgency, prerequisites, and prohibited sequencing
- course spans, current Authority, Matter support, counter-support, and gaps
- source-role, release, exact locator, and retrieval-trace references
- candidate Deadline trigger and deterministic calculation state
- rank dimensions, confidence, challenge state, and review state
- route, model revision, prompt and schema hashes, and policy evidence
- required capability and downstream human gate

This proposal is inert. The UI may offer `Challenge`, `Accept as proposed
Task`, `Request Work Product proposal`, and `Reject`. It must not offer a
direct model-to-connector action.

## Matter artifact intake contract owed to the UI

The implementing task should define an idempotent intake contract containing:

- Tenant, Matter, submitting principal, purpose, classification, and source
  identity
- original filename, media type, byte count, content hash, observed time, and
  acquisition method
- quarantine, malware scan, duplicate, extraction, OCR, transcription, and
  validation states
- parent and child lineage for every derived artifact
- custody, privilege, ethical-wall, retention, and legal-hold state
- proposed links to Matter Events, Communications, Fact Assertions, Evidence
  Items, Issues, Claims, Elements, Tasks, and Work Products
- model and tool route evidence for every proposed classification or
  extraction
- human corrections, review state, and supersession reason

The single `Add to this Matter` interaction can hide most of this complexity,
but the record cannot. The original bytes are immutable. Reprocessing creates
a new derived artifact version and preserves lineage.

## Matter activity and provenance contract owed to the UI

The case log is a human-readable projection over append-only domain and audit
records. It must include:

- court and party activity, Matter Events, service, hearings,
  Communications, Evidence Items, Tasks, Deadlines, and outcomes
- every retrieval, Agent Run, tool call, recommendation, challenge, rerun,
  failure, and policy decision
- actor or agent identity, exact time, correlation ID, action, outcome, and
  reason
- before and after versions, hashes, source spans, Matter snapshot, corpus
  release, Authority snapshot, prompt and schema hashes, and served model
- every Work Product validation, Approval, cancellation, dispatch transition,
  provider response, receipt, and reconciliation state
- explicit links from the friendly timeline entry to the immutable underlying
  records

Exports such as a case chronology, artifact manifest, analysis dossier, and
action log are versioned Work Products with their own hashes and manifests.
They never replace the underlying records.

## Model transport seam owed to the UI

Matter code and Agent specs name only a logical SKLegal route ID. The route
resolves through a versioned deployment transport profile:

| Profile kind | Protocol | Intended use | Current state |
| --- | --- | --- | --- |
| Direct local Qwen | OpenAI-compatible local transport | Initial and rollback binding for private corpus and Matter analysis | Existing provider abstraction, concrete production transport still owed |
| SKGateway | OpenAI-compatible Chat Completions | Preferred router after live-path control qualification | Upstream reviewed, SKLegal adapter and protected qualification missing |
| Direct OpenAI Platform | Responses API | Approved frontier route when policy permits | Narrow adapter implemented |

The logical route continues to pin prompt, output schema, classification
ceiling, timeout, retry class, and workload class. The profile resolves the
deployment address and credential references. Neither domain state nor an
Agent spec contains a private host or secret.

SKGateway execution evidence must add gateway commit, configuration hash,
catalog and policy revisions, request ID, backend, capacity domain, requested
model or bucket, bucket member, exact served model, retry, failover,
saturation, usage, and latency. Missing or conflicting attribution fails the
provider contract.

Workload class and model parameter size are different fields. `S`, `M`, `L`,
and `XL` workload classes express a task capability floor. Model parameter
size is descriptive metadata and cannot qualify a model without measured
structured-output, citation, reasoning, context, and held-out task evidence.

Remote free routes start with public synthetic work only. Free pricing never
overrides Tenant and Matter scope, source rights, classification, egress,
trust-zone, or evaluation gates. See
`docs/research/SKGATEWAY-INTEGRATION-REVIEW-2026-08-22.md` and
`docs/tasks/SKL-S3-10-TDD.md`.

## Source classes shown on every material proposition

| Badge | Meaning |
| --- | --- |
| Course instruction | Private HowToWinInCourt source-derived method or pattern |
| Current Authority | Official rule, statute, opinion, or order with applicability state |
| Matter record | Fact Assertion or Evidence Item from the authorized Matter |
| Model inference | Typed model proposal with route evidence and confidence |
| Human decision | Attributable review, challenge disposition, Approval, or rejection |

## Cross-cutting controls

- Server authorization and policy run before retrieval or context assembly.
- SKLegal authorization, classification, rights, and egress gates run before
  selecting either a direct or SKGateway transport profile.
- Every protected result remains Tenant and Matter scoped.
- Source roles, releases, hashes, effective dates, and contradictory material
  remain visible.
- Similarity and adjacency are retrieval signals only.
- Exact quotation and Authority applicability use deterministic validators.
- Consequential Deadlines require rule, trigger, deterministic calculation,
  and review evidence.
- An approved Work Product version is immutable. Edits create a new version
  and invalidate prior Approval.
- Every original and derived artifact preserves hash, source identity,
  custody, extraction lineage, human corrections, and later uses.
- The Matter activity log is append-only at its source. Corrections create
  attributable superseding records rather than rewriting history.
- External actions remain separate from recommendation acceptance and follow
  `draft -> validated -> approved -> queued -> dispatched -> receipt_verified`.
