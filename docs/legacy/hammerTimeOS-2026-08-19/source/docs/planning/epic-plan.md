# hammerTimeOS epic plan

Date: 2026-08-19  
Planning horizon: Foundation through first controlled vertical slice

## Product outcome

Deliver a local-first legal workbench that can open an authorized matter, link to a pinned HammerTime corpus release, retrieve permitted sources, produce typed issue and claim proposals through Qwen3.8, validate sources and authority applicability, obtain human approval, and release an auditable work product.

The first outcome does not include autonomous filing or communication, full corpus migration, or broad multi-jurisdiction coverage.

## Delivery strategy

Use a walking skeleton from the beginning. Each wave should extend the same end-to-end path rather than completing isolated horizontal platforms that are never integrated.

```text
Matter intake
  -> conflict clearance
  -> source link
  -> authorized retrieval
  -> Qwen proposal
  -> claim and citation validation
  -> human review
  -> exact-version release
  -> audit replay
```

## Epic map

| Epic | Name | Primary dependency |
|---|---|---|
| E0 | Decisions, risk, and rights baseline | None |
| E1 | Reproducible engineering foundation | E0 |
| E2 | Legal domain and legacy adapter | E0, E1 |
| E3 | Identity, conflicts, privilege, and policy | E2 |
| E4 | HammerTime corpus registry and integration | E1, E2 |
| E5 | Retrieval and embedding qualification | E4 |
| E6 | Typed model gateway and Qwen worker | E1, E3, E4 |
| E7 | Durable matter workflow | E2, E3, E6 |
| E8 | Claim, authority, citation, and deadline assurance | E5, E7 |
| E9 | Legal workbench and document production | E2, E3, E7, E8 |
| E10 | First jurisdiction and matter-type vertical slice | E4 through E9 |
| E11 | Security, reliability, and operational qualification | E3 through E10 |
| E12 | Migration, rollout, and corpus expansion | E10, E11 |

## E0: Decisions, risk, and rights baseline

### Goal

Remove the decisions that could invalidate the architecture or corpus use.

### Deliverables

- architecture decision records for state ownership, workflow engine, model roles, graph licensing, and first vertical slice
- repository threat model and protected-data flow map
- source-rights and dependency-license schema
- Qwen derivative, base weight, and deployment-use license record
- FalkorDB deployment and distribution decision
- first jurisdiction, forum, and matter-type selection
- named owners for conflicts, privilege, ethical walls, retention, security, model assurance, corpus release, and product release
- classification policy and model-egress policy

### Acceptance

- every unknown license or rights item is quarantined or explicitly excluded
- there is one approved state-ownership table
- risk owners approve the first-slice boundaries

## E1: Reproducible engineering foundation

### Goal

Make the new codebase installable, testable, observable, and safe to change.

### Deliverables

- root Python package and locked dependencies
- lint, format, type-check, unit-test, integration-test, and migration commands
- local PostgreSQL and Temporal development stack
- application configuration and secret-reference conventions
- OpenTelemetry correlation and structured logging baseline
- CI with SBOM, dependency, secret, and vulnerability checks
- development fixtures containing no protected matter data

### Acceptance

- a clean checkout can run one command to start dependencies and one command to execute tests
- CI reproduces the local test suite
- logs contain correlation IDs and no fixture secrets or protected content

## E2: Legal domain and legacy adapter

### Goal

Establish typed legal operations records without breaking current HammerTime paths and scripts.

### Deliverables

- Pydantic and database models for tenant, client, engagement, matter, party role, proceeding, issue, claim, fact, evidence, authority, deadline, task, communication, work product, and approval
- state-machine validators
- stable UUIDs plus legacy `PRB-*`, `INC-*`, slug, and path aliases
- read-only `LegacyMatterSnapshot` adapter
- classification queue for ambiguous legacy incidents
- generated matter register and freshness metadata
- importer with content-hash idempotency and dry-run report

### Acceptance

- one fixture problem imports repeatedly without duplicate records
- no source files are rewritten
- every imported record links to legacy path, hash, and adapter version
- ambiguous incidents are reported rather than guessed
- existing HammerTime commands still resolve the fixture matter

## E3: Identity, conflicts, privilege, and policy

### Goal

Make access and retrieval enforceable before any private model workflow is enabled.

### Deliverables

- CapAuth identity and short-lived capability integration
- tenant and matter membership model
- conflict search, decision, waiver, and conflict-hold records
- ethical-wall membership and policy
- privilege and work-product classification model
- retention and legal-hold records
- policy gateway for API, tool, retrieval, model-egress, approval, and export decisions
- PostgreSQL row-level security as defense in depth
- deny-by-default test matrix

### Acceptance

- unauthorized cross-matter retrieval fails before a query reaches Qdrant or a model
- conflict hold blocks substantive matter work
- ethical-wall membership changes are audited and cannot be self-approved
- privileged data cannot use a disallowed model route
- policy-service failure causes a safe denial

## E4: HammerTime corpus registry and integration

### Goal

Expose HammerTime through a stable, observable contract.

### Deliverables

- read-only artifact, provenance, release, alias, and legacy-matter APIs
- materialized corpus metadata registry
- release event or polling adapter
- source, normalized artifact, decomposition, vector, and graph coverage report
- projection watermarks and orphan reconciliation
- bounded health endpoint plus scheduled deep reconciliation
- controlled command path for an approved corpus release operation

### Acceptance

- health returns within a fixed budget without scanning the full tree
- every served artifact belongs to a named release or is explicitly marked unreleased
- Qdrant and FalkorDB lag is visible
- an orphaned vector or missing source is detected by reconciliation
- hammerTimeOS cannot write arbitrary HammerTime paths

## E5: Retrieval and embedding qualification

### Goal

Establish measurable, partition-safe retrieval before expanding agent behavior.

### Deliverables

- frozen gold query and relevance set
- lexical, metadata, Qdrant semantic, and optional graph retrieval adapters
- rank fusion and reranking baseline
- authority, jurisdiction, time, corpus-role, and classification filters
- custom BGE legal model versus base BGE-M3 comparison
- shadow collections and reversible alias promotion
- exact citation and quotation lookup tests
- no-answer and prompt-injection retrieval tests

### Acceptance

- minimum thresholds are approved for Recall@k, nDCG, MRR, citation-span accuracy, and cross-partition leakage
- no protected result appears in an unauthorized query set
- custom embedding is promoted only if it beats or intentionally trades off against the base on the frozen suite
- every result records corpus release, filter set, rank path, and source location

## E6: Typed model gateway and Qwen worker

### Goal

Provide one safe, observable route for bounded model analysis.

### Deliverables

- versioned task and agent specifications
- PydanticAI model adapter for the local OpenAI-compatible Qwen endpoint
- typed schemas for issue, claim, authority-applicability, challenge, and draft proposals
- capability-gated domain tool gateway
- four-slot admission control with interactive reservation and long-context limits
- timeout, cancellation, retry, and circuit-breaker policy
- model and prompt version evidence
- prompt-injection and malicious-tool-intent suite
- same-model variance labeling and independent-review roadmap

### Acceptance

- Qwen cannot access shell, arbitrary network, release, filing, or communication tools
- malformed output cannot commit canonical state
- a document instruction cannot expand tool capability
- saturation queues work without losing interactive requests
- a model outage preserves deterministic work and yields an explicit blocked state

## E7: Durable matter workflow

### Goal

Turn the walking skeleton into a resumable, idempotent human workflow.

### Deliverables

- Temporal workflow for request, authorization, snapshot, retrieval, proposal, validation, challenge, review, and release
- task queues separated by interactive, batch, and long-context work
- idempotent activities and outbox-based projections
- human review and revision loop
- compensation and reconciliation rules
- workflow status API and stale-run alarms

### Acceptance

- killing a worker at each activity boundary resumes without duplicate proposals or artifacts
- policy denial is not retried as a transient error
- human review may pause and resume without holding a worker
- every state transition has actor, reason, prior version, and correlation ID

## E8: Claim, authority, citation, and deadline assurance

### Goal

Create the legal-specific gates that distinguish the product from generic RAG.

### Deliverables

- claim ledger and support graph
- authority records with status, hierarchy, applicability, and effective time
- exact quote and citation verifier using eyecite and reporters metadata where suitable
- official-source connector adapter, beginning with CourtListener where within scope
- adverse and contrary-support workflow
- forum and jurisdiction pack contract
- deterministic deadline engine with trigger and rule provenance
- `CLAIM_READY`, `DRAFT_READY`, and `RELEASE_READY` validators

### Acceptance

- an unsupported material claim cannot reach `draft_ready`
- a similar but inapplicable authority is rejected with a recorded reason
- a quotation mismatch blocks release
- superseded authority is visible and cannot silently rank as current support
- a deadline records trigger, rule version, timezone, calculation, reviewer, and supersession

## E9: Legal workbench and document production

### Goal

Provide a usable matter-centered interface for evidence-grounded work and review.

### Deliverables

- intake and conflict-hold views
- matter, party, proceeding, issue, fact, evidence, authority, deadline, and task views
- claim ledger with support and counter-support
- source viewer with exact citation navigation
- run trace and defect review
- work-product version comparison
- human approval and exact-version release screen
- audited doc.haus component extraction for citation, grid review, and DOCX tracked changes

### Acceptance

- users can trace every drafted material claim to exact supporting sources
- editing an approved artifact invalidates its approval
- a reviewer can see unresolved defects without opening raw logs
- UI filtering cannot bypass server-side policy
- DOCX output preserves tracked changes and source references in the agreed format

## E10: First jurisdiction and matter-type vertical slice

### Goal

Prove the system on a narrow, realistic workflow with a controlled corpus.

### Candidate scope

Illinois state and applicable federal authority with one bounded UCC-related research and drafting task is a reasonable candidate because HammerTime has relevant corpus and workflows. Final scope requires the E0 decision and a non-sensitive fixture or expressly approved test matter.

### Deliverables

- versioned jurisdiction pack
- official source connectors and authority hierarchy
- frozen fixture matter and expected outcomes
- gold retrieval, claim, citation, authority, and deadline evaluations
- end-to-end work product with human approval
- replay package proving inputs, versions, decisions, and output

### Acceptance

- no critical security, rights, provenance, or citation defect
- all hard gates pass on the exact released version
- independent human review records acceptable factual, authority, and procedural quality
- a second team member can reproduce the run from pinned inputs

## E11: Security, reliability, and operational qualification

### Goal

Establish evidence for a controlled pilot.

### Deliverables

- security diff and scoped repository review
- threat-model validation and abuse tests
- backup, restore, key rotation, disaster recovery, and rollback exercises
- queue and long-context load tests
- model outage and retrieval outage drills
- audit-integrity and export tests
- privacy and retention checks
- operating runbooks and service objectives
- pilot go or no-go checklist

### Acceptance

- restore meets the approved recovery objectives
- no critical or high unmitigated security finding remains
- policy, model, database, vector, graph, and HammerTime outages have tested behavior
- audit replay identifies who saw, proposed, approved, changed, and released each artifact

## E12: Migration, rollout, and corpus expansion

### Goal

Move from controlled slice to managed adoption without breaking HammerTime.

### Deliverables

- legacy matter inventory and classification report
- phased import with dry run, approval, reconciliation, and rollback
- user and reviewer training
- support and escalation workflow through SK ITIL
- additional jurisdiction-pack intake process
- corpus and skill rights-review queue
- deprecation plan for manual registries and direct legacy dashboards

### Acceptance

- every migrated record reconciles to source path and hash
- no manual registry is retired before generated-view parity and rollback exist
- platform incidents flow through SK ITIL while legal matters remain in the legal domain
- each new pack passes rights, security, content, retrieval, and workflow evaluation

## Suggested first 30 days

1. Lock E0 decisions and owners.
2. Establish E1 packaging, tests, PostgreSQL, and local Temporal.
3. Implement the first Pydantic domain types and migrations.
4. Build a read-only adapter for one synthetic or non-sensitive legacy matter.
5. Build a materialized corpus release and coverage view.
6. Create the first 50 gold retrieval queries before tuning embeddings.
7. Route one typed `IssueProposal` through Qwen with no tools beyond authorized source read and search.
8. Demonstrate human approval of an exact proposal version with a complete audit trace.

## Suggested 60 to 90 day target

Complete one walking-skeleton vertical slice through conflict clearance, source linking, hybrid retrieval, Qwen issue and claim proposals, deterministic citation checks, human review, and exact-version release. Use fixture data and one jurisdiction pack. Do not widen corpus or agent scope until the slice passes security, provenance, retrieval, and release gates.

## Definition of MVP

MVP means all of the following, not merely a working chat screen:

- typed legal matter and party records
- enforced matter access, conflict state, privilege, and ethical walls
- pinned HammerTime corpus release and source provenance
- measured hybrid retrieval
- bounded Qwen proposal generation
- claim-level source support and authority applicability
- exact citation checks
- durable workflow and human approval
- immutable release and replay evidence
- tested failure behavior

## Work explicitly deferred

- autonomous filing, service, email, or client communication
- general-purpose agent marketplace
- broad public web research without connector and rights controls
- replacing HammerTime ingestion
- all-matter legacy migration
- OpenSearch cluster
- LangGraph runtime
- formal legal rule execution without a concrete use case
- multi-jurisdiction rollout
- model fine-tuning before the evaluation platform is trustworthy

