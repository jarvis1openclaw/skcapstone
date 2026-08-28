# SKLegal AI-first Matter analysis component map

Date: 2026-08-24
Handoff card: `SKL-MVP-HANDOFF-03` (`433bb97c`)
Companion: `docs/planning/wireframes/index-v2.html`

This is a planning and integration map, not runtime evidence. It supersedes
the original immutable map with SHA-256
`8e139a1374f894c787aa758cb56ec3af3a711141ac73df223b5f04133f3dd1cd`.
The frozen contract is candidate
`eade7626fb6190924e80d73b79473f685e1c2531`, tree
`f003bd74f665465e8396255947dcd30e72d2dec5`. It fixes 18 ordered surfaces and
21 operations. An accepted lane is not mounted merely because its commit and
independent review exist.

## State vocabulary

| State | Meaning |
| --- | --- |
| Reviewed shell | Real Chrome qualified the exact public-synthetic V2 presentation. The result is not durable behavior. |
| Accepted durable lane, unmounted | An independently reviewed feature commit and tree exist. Central API integration has not registered it. |
| Blocked durable lane | No acceptable durable candidate exists. The UI remains safely unavailable. |
| Central adapter owed | `c1ee25da` must reconcile the lane to the frozen operation contract without rewriting reviewed lane internals. |
| Frontend wiring owed | `7334b7e5` must bind the joined API to the cockpit and prove browser behavior. |
| Post-integration qualification | `06a2686f`, `465e000f`, and `13473fb6` still owe durable composition, browser qualification, and independent review. |
| Post-MVP | The contract is deliberately outside the internal public-synthetic MVP. |

## Current foundation and browser truth

| Surface | Immutable custody | Current truth |
| --- | --- | --- |
| Repaired integration base | `0a293dd1afe9d5b4150468057e4ed8e552d7221b`, tree `5cccf34afa4653bf9bcfd59ebdad1b6eb3f61caa` | Independently reviewed base for central integration. It does not contain the accepted horizontal lanes as one composition. |
| V2 browser shell | `8177c88c5fd371a487e2c206c53b24e3619c1855`, tree `a23e08dc6edfde8397298b3d1ee0b354434c3bcd` | Reviewed shell. Real Chrome passed 18-section navigation, compact and expanded layout, keyboard, axe, CSP, reload, outage, denial, and leakage checks against public-synthetic fixture behavior. |
| Existing Client, Matter, workspace, policy, model gateway, agent registry, domain, and persistence foundations | Accepted repaired base and earlier reviewed packages | Reusable foundation only. They do not prove the frozen 21-operation durable composition. |
| Accepted horizontal lanes | JOIN, RUN, ART, WP, TASK, and ACT commits below | Durable lane behavior accepted but unmounted. Central adapters and registration remain owed. |
| Governed corpus lane | `38d61700` and repair `5ae69846` | Blocked pending exact offline PostgreSQL 17.7 and pgvector 0.8.0 input approval `71ecf523`, repair, and independent review. |

## Version 2 cockpit map

| # | UI region | Frozen operation binding | Current state | Next owner |
| --- | --- | --- | --- | --- |
| 1 | Decision first | Static presentation | Reviewed shell | FE preserves reviewed truth |
| 2 | AI operating model | `create_analysis_run`, `get_agent_run` | RUN accepted durable lane, unmounted; central adapter and frontend wiring owed | `c1ee25da`, then `7334b7e5` |
| 3 | Corpus and verification map | `search_corpus`, `get_corpus_span` | Durable corpus blocked; shell is presentation only | `38d61700`, then central and frontend integration |
| 4 | Matter cockpit | `get_workspace`, `get_joined_analysis`, `create_analysis_run` | Reviewed public-synthetic shell plus JOIN and RUN accepted durable lanes, unmounted | `c1ee25da`, then `7334b7e5` |
| 5 | AI intake | `create_analysis_run` | RUN accepted durable lane, unmounted; shell remains safely unavailable | `c1ee25da`, then `7334b7e5` |
| 6 | Matter artifact intake | `get_artifact`, `create_artifact_intake` | ART accepted durable lane, unmounted; central adapter and frontend wiring owed | `c1ee25da`, then `7334b7e5` |
| 7 | Essential Elements matrix | `get_claim_ledger`, `get_joined_analysis` | JOIN accepted durable lane, unmounted | `c1ee25da`, then `7334b7e5` |
| 8 | Ranked recommendation | `list_recommendations`, `decide_recommendation` | RUN accepted durable lane, unmounted; browser ranking remains inert | `c1ee25da`, then `7334b7e5` |
| 9 | Strategy and Authority | `get_joined_analysis`, `search_corpus`, `get_corpus_span` | JOIN accepted unmounted; corpus portion blocked | Corpus review, then central and frontend integration |
| 10 | Agent team | `create_analysis_run`, `get_agent_run` | RUN accepted durable lane, unmounted | `c1ee25da`, then `7334b7e5` |
| 11 | Blind challenge | `create_challenge`, `get_agent_run` | RUN accepted durable lane, unmounted; shell remains inert | `c1ee25da`, then `7334b7e5` |
| 12 | Model routing evidence | `get_agent_run` | RUN evidence accepted unmounted; no provider request is authorized | `c1ee25da`, then `7334b7e5`; live provider use remains separately gated |
| 13 | Work Product assembly | `get_work_product`, `create_work_product_version`, `validate_work_product`, `decide_approval` | WP accepted durable lane, unmounted | `c1ee25da`, then `7334b7e5` |
| 14 | Tasks, Deadlines and action handoff | `upsert_task`, `compute_deadline`, `create_simulation_handoff` | TASK accepted durable lane, unmounted; connector effect remains simulation-only | `c1ee25da`, then `7334b7e5` |
| 15 | Source and run evidence | `get_agent_run`, `get_corpus_span` | RUN accepted unmounted; corpus portion blocked | Corpus review, then central and frontend integration |
| 16 | Matter case log | `list_activity`, `create_activity_export` | ACT accepted durable lane, unmounted | `c1ee25da`, then `7334b7e5` |
| 17 | Failure states | Static presentation plus closed error envelopes | Reviewed shell; durable failure behavior remains post-integration qualification | Central, frontend, compose, qualify, review |
| 18 | Delivery map | Static presentation | Reviewed shell; it grants no deployment authority | FE preserves reviewed truth |

## Frozen operation inventory

| Lane | Operations | Count | Current state |
| --- | --- | --- | --- |
| JOIN | `get_workspace`, `get_claim_ledger`, `get_joined_analysis` | 3 | Candidate `a9f87e1e1010ab75d446833268282ad531e3f173`, tree `2a975487b7c855a250864a70f88adcc660df63db`, accepted and unmounted |
| RUN | `create_analysis_run`, `get_agent_run`, `create_challenge`, `list_recommendations`, `decide_recommendation` | 5 | Candidate `4bf27dbcd9b038d21b9458728a9d3ae1e81f0b7c`, tree `dc186c2f615207ff45755ac51618fcb69225c163`, accepted and unmounted |
| ART | `create_artifact_intake`, `get_artifact` | 2 | Candidate `701e42d1ba1016c3ba5d75661eaa4ca9c8f66bfc`, tree `1846438ad92a7654c23deddacdc861a794e0b946`, accepted and unmounted |
| WP | `get_work_product`, `create_work_product_version`, `validate_work_product`, `decide_approval` | 4 | Candidate `3935618e42bba9dee044893b61e08a24df8a7f6e`, tree `e1fbdf095a95d09b76b79e541e16792c45f31a61`, accepted and unmounted |
| TASK | `upsert_task`, `compute_deadline`, `create_simulation_handoff` | 3 | Candidate `c2c61d2536572fe2d7f0908dfb43a77c29da5103`, tree `3e93f7fc206572c6c656d3933e430088da306adb`, accepted and unmounted |
| ACT | `list_activity`, `create_activity_export` | 2 | Candidate `d0b0cf16a13cbcbfe431f042fe050f7d955e94`, tree `19a6d8a5ae27dedff85602117107727cffcb6664`, accepted and unmounted |
| CORPUS | `search_corpus`, `get_corpus_span` | 2 | Blocked. No accepted durable candidate. |
| Total | Frozen manifest version `1.0.1` | 21 | No joined, durable, browser-qualified composition exists yet. |

The current accepted lane commits are separate immutable lineages. Their
route registration, shared envelopes, OpenAPI, fixture registration, migration
sequence, and any compatibility adapters belong only to `c1ee25da`. The map
does not claim that any frozen route is mounted until that integration passes.

## Dual PostgreSQL failure-domain boundary

| Cluster identity | Canonical responsibility | Required separation |
| --- | --- | --- |
| `sklegal-core-pg` | Legal domain state, policy state, audit, outbox, workflow references, and projection registry | Dedicated database, volume, roles, credentials, resource controls, private network identity, restart lifecycle, backup lifecycle, and no shared superuser or cross-cluster transaction |
| `sklegal-retrieval-pg` | Governed full-text, native pgvector, and optional AGE projections | Separate database, volume, roles, credentials, resource controls, private network identity, restart lifecycle, backup lifecycle, deterministic rebuild, and no canonical state ownership |

Core must continue safely or fail closed when retrieval is unavailable.
Retrieval failure never makes unknown or stale data appear healthy. This
planning boundary does not create either cluster or authorize deployment.

## Reviewed recommendation proposal shape awaiting central integration

The accepted RUN lane and central integration must preserve a strict schema containing:

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

## Reviewed Matter artifact intake contract awaiting central integration

The accepted ART lane, central integration, and frontend wiring must preserve
an idempotent intake contract containing:

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

## Reviewed Matter activity and provenance contract awaiting central integration

The accepted ACT lane defines the durable projection. Central integration and
frontend wiring must preserve a human-readable case log over append-only
domain and audit records containing:

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

## Separately gated model transport seam

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
