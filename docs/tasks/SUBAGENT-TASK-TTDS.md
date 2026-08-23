# SKLegal subagent task technical designs

Date: 2026-08-19  
Status: Approved, retrieval architecture amended 2026-08-21
Rule: Subagents claim only eligible leaf tasks after architecture approval and dependency completion

## Task execution contract

Every task begins by loading SK context, reading `AGENTS.md`, checking the SKCapstone board, and claiming the matching card. Work stays inside the stated scope. Completion requires tests, acceptance evidence, changed-file inventory, limitations, and a linked card update.

## Sprint 0

### SKL-S0-01: Approve SKLegal architecture and pilot boundaries

- **Agent:** Human owner with architecture support
- **Size:** M
- **Dependencies:** None
- **Objective:** Review the high-level TDD, pilot TDD, sprint plan, CapAuth model, provider boundary, and external-action policy.
- **Outputs:** Recorded approval or requested changes, exact approved document hashes, and board gate decision.
- **Verification:** Confirm the approval page matches the Markdown sources and that all implementation cards depend on this gate by policy.
- **Acceptance:** Human explicitly approves the architecture, pilot, and simulation-first connector policy.
- **Prohibited:** Do not treat silence, page viewing, or partial approval as completion.

### SKL-S0-02: Qualify chiap01 capacity and deployment baseline

- **Agent:** Platform and deployment
- **Size:** M
- **Dependencies:** `SKL-S0-01`
- **Objective:** Establish a safe chiap01 deployment budget before adding persistent services.
- **Implementation:** Inventory filesystem use, Docker images and volumes, logs, databases, model caches, ports, backup targets, CPU, memory, and service ownership. Propose recoverable cleanup or approved storage expansion. Define capacity alarms and service resource limits.
- **Tests:** Disk-pressure simulation, Docker volume inventory reconciliation, port-conflict scan, and capacity alert test.
- **Acceptance:** Approved plan provides at least the required free-space and growth headroom, with no destructive cleanup performed without separate authorization.
- **Prohibited:** No broad deletion, Docker prune, or production deployment.

### SKL-S0-03: Define threat model, data classification, and source-rights controls

- **Agent:** Security and governance
- **Size:** L
- **Dependencies:** `SKL-S0-01`
- **Objective:** Define protected assets, trust boundaries, abuse paths, classification inheritance, model egress rules, and source-use rights.
- **Implementation:** Cover tenant isolation, conflicts, privilege, ethical walls, prompt injection, connector misuse, audit access, account takeover, source licensing, retention, legal hold, and backup exposure.
- **Tests:** Misuse cases and expected policy decisions for every boundary.
- **Acceptance:** Named owners accept the threat model and unknown rights are quarantined.
- **Prohibited:** Do not insert external-source conclusions into HammerTime corpus artifacts.

### SKL-S0-04: Scaffold the reproducible monorepo and CI baseline

- **Agent:** Engineering foundation
- **Size:** M
- **Dependencies:** `SKL-S0-01`
- **Objective:** Create the approved repository shape and one reproducible development workflow.
- **Implementation:** Add Python and frontend workspaces, lockfiles, local dependency stack, lint, format, type-check, unit-test, integration-test, migration, SBOM, secret-scan, and vulnerability-scan commands.
- **Tests:** Clean-checkout setup and full CI on fixture-only data.
- **Acceptance:** One command starts development dependencies and one command runs the required checks.
- **Prohibited:** No protected matter content or production secrets in fixtures.

### SKL-S0-05: Audit doc.haus licensing, provenance, and reusable components

- **Agent:** Licensing and frontend architecture
- **Size:** M
- **Dependencies:** `SKL-S0-01`
- **Objective:** Decide what may be reused from the local doc.haus and OpenCode lineage.
- **Implementation:** Pin commits, inspect all license files and notices, trace third-party components, generate an SBOM, and identify cleanly extractable citation, review-grid, DOCX, and workbench components.
- **Tests:** License scanner and provenance manifest completeness.
- **Acceptance:** Each candidate is approved, rejected, or requires permission. No `NOASSERTION` component is copied before resolution.
- **Prohibited:** Do not import code during the audit task.

## Sprint 1

### SKL-S1-01: Implement the legal-domain package and state machines

- **Agent:** Domain and backend
- **Size:** L
- **Dependencies:** `SKL-S0-03`, `SKL-S0-04`
- **Objective:** Implement typed entities and legal-domain transitions without ITIL types.
- **Implementation:** Add Pydantic models and value objects for tenant, client, engagement, matter, party role, proceeding, event, transaction, fact, tension, evidence, authority, claim, deadline, task, communication, work product, validation, approval, and execution.
- **Tests:** Valid and invalid state transitions, effective-time behavior, immutable identity, and legacy-alias validation.
- **Acceptance:** Domain tests pass and no new public API exposes Problem or Incident as a canonical type.
- **Prohibited:** No database or UI coupling inside domain objects.

### SKL-S1-02: Implement PostgreSQL schemas, migrations, and row-level security

- **Agent:** Data platform
- **Size:** L
- **Dependencies:** `SKL-S0-02`, `SKL-S0-04`, `SKL-S1-01`
- **Objective:** Persist the domain with tenant and matter isolation.
- **Implementation:** Create schemas for identity, legal records, integrations, workflow references, and audit. Add migration tooling, constraints, version columns, bitemporal authority fields, encrypted-field hooks, and row-level policies.
- **Tests:** Migration up and down in disposable databases, cross-tenant denial, concurrency, uniqueness, and transaction rollback.
- **Acceptance:** A principal cannot read or mutate another tenant or unassigned matter even if an API filter is omitted.
- **Prohibited:** Do not share the `skmem-pg` application schema.

### SKL-S1-03: Integrate CapAuth principals, capabilities, delegation, and revocation

- **Agent:** CapAuth and security
- **Size:** L
- **Dependencies:** `SKL-S0-03`, `SKL-S0-04`, `SKL-S1-01`
- **Objective:** Make signed capabilities the required authorization input for APIs, tools, models, and connectors.
- **Implementation:** Define human, agent, service, and connector principals; capability vocabulary; resource constraints; short TTL; delegation limits; token verification cache; revocation; and audit identifiers.
- **Tests:** Valid, expired, revoked, wrong-tenant, wrong-matter, wrong-purpose, over-delegated, and replayed tokens.
- **Acceptance:** Every protected route and tool fails closed without a valid scoped capability.
- **Prohibited:** No token in prompts, logs, browser local storage, or Temporal history.

### SKL-S1-04: Implement conflicts, privilege, ethical walls, retention, and holds

- **Agent:** Legal operations and policy
- **Size:** L
- **Dependencies:** `SKL-S1-01`, `SKL-S1-02`, `SKL-S1-03`
- **Objective:** Enforce legal information barriers before retrieval or model use.
- **Implementation:** Add party normalization, conflict checks and decisions, waiver references, conflict hold, matter membership, wall membership, privilege labels, work-product labels, retention policies, and legal holds.
- **Tests:** Adverse-party collision, conflict hold, wall exclusion, classification inheritance, retention pause, and policy-service outage.
- **Acceptance:** Denied material cannot enter retrieval, cache, model context, export, or audit detail.
- **Prohibited:** Profile ownership metadata cannot grant access automatically.

### SKL-S1-05: Implement append-only audit, outbox, and telemetry correlation

- **Agent:** Backend and observability
- **Size:** M
- **Dependencies:** `SKL-S1-02`, `SKL-S1-03`
- **Objective:** Correlate every mutation and external boundary without leaking protected content.
- **Implementation:** Add run IDs, append-only audit events, policy-decision references, transactional outbox, projection watermarks, OpenTelemetry propagation, redaction, and local retention.
- **Tests:** Transaction rollback, duplicate outbox delivery, correlation propagation, tamper evidence, and protected-field suppression.
- **Acceptance:** One run can be replayed across API, workflow, tool, model, human, and connector events.
- **Prohibited:** No raw prompts or source documents in external telemetry by default.

## Sprint 2

### SKL-S2-01: Build the read-only HammerTime release and artifact adapter

- **Agent:** HammerTime integration
- **Size:** L
- **Dependencies:** `SKL-S1-02`, `SKL-S1-03`, `SKL-S1-05`
- **Objective:** Provide stable typed access to releases, artifacts, provenance, and legacy matter structure.
- **Implementation:** Wrap release aliases, manifests, artifact reads, source hashes, decompositions, packet references, validation results, and legacy path resolution. Pin each response to a source snapshot.
- **Tests:** Missing path, changed hash, stale release, malformed frontmatter, unauthorized matter, and read-only filesystem enforcement.
- **Acceptance:** Frontend and domain code never construct HammerTime paths directly.
- **Prohibited:** No HammerTime write path in this adapter.

### SKL-S2-02: Build the legacy matter snapshot and pilot importer

- **Agent:** Migration and data
- **Size:** L
- **Dependencies:** `SKL-S1-01`, `SKL-S1-02`, `SKL-S1-04`, `SKL-S2-01`
- **Objective:** Implement the Liberty Auto lossless mapping from legacy records to legal-domain proposals.
- **Implementation:** Add dry run, source inventory, hash capture, atomic facts, tension groups, version lineage, import batches, idempotency, reconciliation, and human mapping review.
- **Tests:** Rerun idempotency, changed-source revision, tension preservation, contingent-date handling, negative execution state, and disposable rollback.
- **Acceptance:** Pilot dry run satisfies `LIBERTY-AUTO-PILOT-TDD.md` without changing HammerTime.
- **Prohibited:** Do not auto-resolve name or timing tensions.

### SKL-S2-03: Build the governed SKLegal-to-HammerTime ingestion bridge

- **Agent:** HammerTime corpus operations
- **Size:** L
- **Dependencies:** `SKL-S1-03`, `SKL-S1-05`, `SKL-S2-01`
- **Objective:** Submit new matter sources through HammerTime's existing ingestion and completion-evidence flow.
- **Implementation:** Add a HammerTime-owned submission command with dry run, batch manifest, exact source hash, authorized destination, status polling, finalizer evidence, release reference, and failure recovery.
- **Tests:** Duplicate source, unsupported source, Qwen outage, QC reject, release failure, missing completion evidence, and successful dry run.
- **Acceptance:** SKLegal receives a promoted artifact reference and cannot bypass finalization or promotion gates.
- **Prohibited:** Do not process live `Inbox/` in tests. Use an isolated fixture intake root.

### SKL-S2-04: Build PostgreSQL retrieval and graph adapters

- **Agent:** Retrieval and graph
- **Size:** L
- **Dependencies:** `SKL-S1-03`, `SKL-S1-04`, `SKL-S1-05`, `SKL-S2-01`, `SKL-S2-10`
- **Objective:** Query local derived PostgreSQL full-text, pgvector, and optional Apache AGE projections through pinned, policy-filtered interfaces.
- **Implementation:** Read the authorized projection registry, query exact pgvector and full-text partitions, expose only closed graph query templates after AGE qualification, preserve retrieval traces and watermarks, and degrade explicitly when a backend is unavailable.
- **Tests:** Every leak and qualification case in `config/retrieval/tenant-partition-contract.json`, including authorization before registry access, cross-Tenant and cross-Matter denial, stale generation, backend outage, empty result, release mismatch, mixed-scope rejection, replica lag, deterministic trace shape, idempotent rebuild, and atomic rollback.
- **Acceptance:** Every result identifies Tenant and Matter scope, release, source hashes, credential-binding event, projection schema and projector, retrieval adapter, query-template ID, version and hash, filters, rank path, watermark, and projection lag. Vector results also identify the embedding model revision, dimension, and metric. Replica results also identify replay LSN. The exact S2-10 contract is satisfied by fake and disposable PostgreSQL backends.
- **Prohibited:** Do not use the live `skmem-pg` instance or create new protected Qdrant or FalkorDB projections. Do not expose raw SQL, raw filters, raw Cypher, or graph names. Graph or vector similarity cannot establish Authority applicability.

### SKL-S2-05: Build the materialized corpus registry and reconciliation jobs

- **Agent:** Corpus operations and observability
- **Size:** M
- **Dependencies:** `SKL-S1-05`, `SKL-S2-01`, `SKL-S2-04`
- **Objective:** Replace unbounded health scans with a bounded metadata view and scheduled deep reconciliation.
- **Implementation:** Materialize source, normalized, decomposition, vector, graph, reject, orphan, and release counts. Add projection lag and last-reconciled state.
- **Tests:** Large fixture tree, changed source, orphan vector, missing decomposition, stale graph, timeout, and incremental update.
- **Acceptance:** Health responds inside the approved budget and deep reconciliation reports complete coverage separately.
- **Prohibited:** No full-tree scan on a user-facing health request.

### SKL-S2-06: Build the official and free legal-source connector registry

- **Agent:** Legal data connectors and source governance
- **Size:** L
- **Dependencies:** `SKL-S0-03`, `SKL-S1-03`, `SKL-S1-04`, `SKL-S1-05`, `SKL-S2-01`
- **Objective:** Inventory, review, and onboard relevant free legal research resources through governed connectors.
- **Implementation:** Begin with official Illinois, federal, court, legislature, agency, and regulator sources plus Free Law Project services. Record source role, jurisdiction, terms, license, attribution, account owner, rate limits, credential reference, freshness, allowed uses, and connector health. Create accounts sequentially when required. Use a human-driven local browser or CDP session for login, MFA, terms acceptance, and consequential confirmations.
- **Tests:** Terms and rights quarantine, rate-limit backoff, stale-source detection, account revocation, credential rotation, source hash, and connector outage.
- **Acceptance:** Every enabled connector has a reviewed rights record, accountable owner, secret reference, health check, and provenance contract.
- **Prohibited:** Free access alone cannot authorize corpus ingestion, redistribution, training, or protected-data upload.

### SKL-S2-10: Define the tenant-native PostgreSQL retrieval partition contract

- **Agent:** Retrieval architecture and security
- **Size:** M
- **Dependencies:** None
- **Objective:** Define the structural Tenant, Matter, generation, provenance, and failure-domain backstops required before S2-04 implementation.
- **Implementation:** Specify separate core and retrieval PostgreSQL clusters, exact-first pgvector, governed full-text search, optional physical AGE graphs, closed query templates, registry and outbox ownership, supply-chain gates, trace fields, rebuild, replication, and rollback behavior.
- **Tests:** Machine-readable contract validation, ASCII-dash validation, approved-document linkage, and a complete adapter leak and qualification matrix for S2-04.
- **Acceptance:** `docs/development/RETRIEVAL-PARTITIONS.md` and `config/retrieval/tenant-partition-contract.json` are approved, internally consistent, test-covered, and referenced by S2-04.
- **Prohibited:** No retrieval adapter, database, container, live corpus, HammerTime `Inbox/`, production credential, or deployment change.

## Sprint 3

### SKL-S3-01: Implement Temporal workflow foundations and task queues

- **Agent:** Workflow backend
- **Size:** L
- **Dependencies:** `SKL-S1-02`, `SKL-S1-03`, `SKL-S1-05`
- **Objective:** Provide deterministic, resumable workflows for human and model work.
- **Implementation:** Add interactive, batch, long-context, and connector queues; typed workflow inputs; idempotent activities; human signals; retry classes; cancellation; compensation; and stale-run alerts.
- **Tests:** Worker kill at every boundary, duplicate signal, activity timeout, policy denial, model outage, and long human wait.
- **Acceptance:** Work resumes without duplicate state or external dispatch.
- **Prohibited:** No model or I/O call in deterministic workflow code.

### SKL-S3-02: Implement the Qwen and OpenAI provider-neutral model gateway

- **Agent:** Model integration
- **Size:** L
- **Dependencies:** `SKL-S1-03`, `SKL-S1-04`, `SKL-S2-01`, `SKL-S3-01`
- **Objective:** Route approved typed activities to local Qwen or OpenAI without provider-specific domain coupling.
- **Implementation:** Pin model, prompt, schema, context budget, timeout, egress classification, and response evidence. Add Qwen four-slot admission control and an OpenAI Responses API structured-output adapter. Include a human-driven OpenAI Platform onboarding checkpoint for organization or project selection, API billing, API-key creation, retention settings, spend limits, secret storage, and key rotation. Do not treat a consumer ChatGPT subscription as an application credential.
- **Tests:** Schema failure, saturation, timeout, cancellation, denied egress, redaction, provider outage, and provider-result parity fixtures.
- **Acceptance:** Provider output remains a proposal and cannot mutate state directly.
- **Prohibited:** Do not use a ChatGPT browser session as an application API.

### SKL-S3-03: Implement versioned agent specifications and the CapAuth tool gateway

- **Agent:** Agent harness and security
- **Size:** L
- **Dependencies:** `SKL-S1-03`, `SKL-S3-01`, `SKL-S3-02`
- **Objective:** Define bounded legal-agent roles as versioned task contracts.
- **Implementation:** Add task purpose, input schema, allowed context, tools, budgets, model routes, output schema, validators, retries, and escalation. Implement domain tools with argument and result validation.
- **Tests:** Prompt injection, unauthorized tool, malformed arguments, document-borne action request, budget exhaustion, and revoked capability.
- **Acceptance:** A source document cannot expand tool authority and no model sees raw credentials.
- **Prohibited:** No general shell or arbitrary network tool.

### SKL-S3-04: Build retrieval evaluation and qualify the custom embedding

- **Agent:** Retrieval evaluation
- **Size:** L
- **Dependencies:** `SKL-S2-04`, `SKL-S2-05`
- **Objective:** Measure retrieval before accepting the custom legal embedding.
- **Implementation:** Build frozen queries and relevance judgments across exact citation, paraphrase, near-neighbor distinction, jurisdiction mismatch, superseded authority, evidence versus authority, OCR noise, no answer, privilege partition, and prompt injection. Compare the custom model with base BGE-M3 in shadow projection generations.
- **Tests:** Dataset leakage, deterministic metric calculation, alias rollback, and cross-partition leakage.
- **Acceptance:** Approved Recall@k, nDCG, MRR, citation accuracy, latency, and leakage thresholds pass.
- **Prohibited:** Do not promote from pairwise cosine examples alone.

### SKL-S3-05: Implement claim, authority, citation, challenge, and release gates

- **Agent:** Legal assurance and backend
- **Size:** L
- **Dependencies:** `SKL-S1-04`, `SKL-S3-02`, `SKL-S3-03`, `SKL-S3-04`
- **Objective:** Enforce claim-level source support and authority applicability before drafting or release.
- **Implementation:** Add claim ledger, support and counter-support, authority status and scope, exact quotation verification, contrary-authority search, blind challenge, deterministic validators, and `CLAIM_READY`, `DRAFT_READY`, and `RELEASE_READY` gates.
- **Tests:** Unsupported claim, wrong jurisdiction, stale authority, quotation mismatch, missing remedy, same-model challenge labeling, and changed artifact after approval.
- **Acceptance:** No failed gate can be waived by model output.
- **Prohibited:** Similarity score alone cannot qualify authority.

## Sprint 4

### SKL-S4-01: Build the SKLegal design system and application shell

- **Agent:** Frontend and accessibility
- **Size:** L
- **Dependencies:** `SKL-S1-01`, `SKL-S1-03`, `SKL-S1-05`
- **Objective:** Create the branded, accessible React shell and protected navigation.
- **Implementation:** Add design tokens, typography, status semantics, responsive layout, keyboard navigation, tenant switcher, client and matter navigation, query boundaries, error states, and correlation display.
- **Tests:** Component, route authorization, accessibility, responsive layout, error recovery, and visual regression.
- **Acceptance:** WCAG AA checks pass and hidden UI never substitutes for server authorization.
- **Prohibited:** No protected data in browser persistence beyond approved session storage.

### SKL-S4-02: Build the client and matter workspace

- **Agent:** Full-stack product
- **Size:** L
- **Dependencies:** `SKL-S1-04`, `SKL-S2-02`, `SKL-S4-01`
- **Objective:** Present clients, engagements, matters, parties, events, facts, evidence, communications, gaps, and provenance.
- **Implementation:** Build overview, parties, timeline, facts and tensions, evidence, communication, and audit views with stable deep links.
- **Tests:** Tenant isolation, matter membership, tension rendering, version history, missing source, stale snapshot, and accessibility.
- **Acceptance:** Pilot matter is understandable without exposing Problem or Incident as canonical UI terms.
- **Prohibited:** Do not hide unresolved tensions or negative execution states.

### SKL-S4-03: Build research, claim ledger, authority, and source viewer

- **Agent:** Frontend, retrieval, and legal assurance
- **Size:** L
- **Dependencies:** `SKL-S3-05`, `SKL-S4-01`
- **Objective:** Let users trace every proposal to exact evidence and authority.
- **Implementation:** Add retrieval trace, source span viewer, claim support, counter-support, applicability factors, reviewer challenges, record gaps, and claim-state transitions.
- **Tests:** Citation navigation, inaccessible source, mismatch defect, contrary support, no-answer state, and keyboard review.
- **Acceptance:** Every material claim exposes support, qualification, challenge, and review history.
- **Prohibited:** No untraceable summary-only answer.

### SKL-S4-04: Build document drafting, versioning, and DOCX redlines

- **Agent:** Document engineering and frontend
- **Size:** L
- **Dependencies:** `SKL-S0-05`, `SKL-S3-05`, `SKL-S4-01`
- **Objective:** Produce versioned work products and genuine DOCX tracked changes.
- **Implementation:** Add templates, bracketed unknowns, claim-grounded drafting, version comparison, tracked-change export, exact hash approval, supersession, and rendered preview validation.
- **Tests:** Round-trip DOCX, tracked-change acceptance, style retention, unknown placeholder, changed-after-approval invalidation, and PDF preview.
- **Acceptance:** An approved version is immutable and edits require new validation and approval.
- **Prohibited:** Do not copy unaudited doc.haus code.

### SKL-S4-05: Build tasks, deadlines, reminders, and calendar integration

- **Agent:** Workflow and frontend
- **Size:** L
- **Dependencies:** `SKL-S1-04`, `SKL-S3-01`, `SKL-S4-01`
- **Objective:** Separate work tasks from rule-based deadlines and track calendar delivery.
- **Implementation:** Add triggers, governing rule references, deterministic calculation hooks, timezone, confidence, reviewer, reminders, supersession, ICS export, and calendar connector simulation.
- **Tests:** Business versus calendar days, missing trigger, timezone boundary, superseded order, duplicate event, and receipt reconciliation.
- **Acceptance:** Contingent printed dates never become operative deadlines without explicit review.
- **Prohibited:** No model-only consequential deadline calculation.

### SKL-S4-06: Build communication, email, service, mailing, and filing connectors

- **Agent:** Connector security and workflow
- **Size:** XL
- **Dependencies:** `SKL-S1-03`, `SKL-S1-04`, `SKL-S1-05`, `SKL-S3-01`, `SKL-S3-03`, `SKL-S4-01`
- **Objective:** Track every external action type from draft through verified receipt.
- **Implementation:** Define provider-neutral connectors, account registry, destination verification, exact-version approval, two-step dispatch, idempotency, simulation mode, provider responses, receipts, failure recovery, reconciliation, and human-driven browser authentication when necessary.
- **Tests:** Wrong destination, revoked capability, duplicate dispatch, expired approval, provider timeout, partial filing, missing receipt, MFA interruption, and simulation parity.
- **Acceptance:** Every connector remains simulation-only until individually qualified and activated.
- **Prohibited:** No unattended terms acceptance, MFA bypass, filing confirmation, or consequential click.

## Sprint 5

### SKL-S5-01: Execute and verify the pilot dry run and structured import

- **Agent:** Migration validation
- **Size:** L
- **Dependencies:** `SKL-S2-02`, `SKL-S2-05`, `SKL-S4-02`
- **Objective:** Import the Liberty Auto pilot into an isolated SKLegal environment and prove losslessness.
- **Implementation:** Capture source hashes, execute dry run, review mappings, import approved records, reconcile counts, render the workbench, rerun import, and generate replay evidence.
- **Tests:** Full pilot verification suite from the pilot TDD.
- **Acceptance:** Source hashes remain unchanged, rerun creates no duplicates, tensions remain visible, and no state advances.
- **Prohibited:** No production action, source modification, or migration of other matters.

### SKL-S5-02: Execute the end-to-end governed Qwen proposal workflow

- **Agent:** Workflow and model qualification
- **Size:** L
- **Dependencies:** `SKL-S3-05`, `SKL-S4-03`, `SKL-S5-01`
- **Objective:** Prove one authorized issue proposal from pinned context through human decision.
- **Implementation:** Issue a narrow capability, start Temporal run, retrieve pilot context, invoke Qwen, validate typed output and source links, challenge, present review, and record acceptance or rejection.
- **Tests:** Qwen outage, malformed output, source change, policy revocation, challenge defect, and human rejection.
- **Acceptance:** Complete replay exists and HammerTime remains unchanged.
- **Prohibited:** No direct model state mutation or automatic approval.

### SKL-S5-03: Qualify external-action simulation and approval receipts

- **Agent:** Connector validation
- **Size:** L
- **Dependencies:** `SKL-S4-04`, `SKL-S4-05`, `SKL-S4-06`, `SKL-S5-01`
- **Objective:** Exercise email, filing, service or mailing, calendar, and client-communication workflows without real dispatch.
- **Implementation:** Use test providers or simulation adapters, exact-version approvals, destination verification, duplicate suppression, and synthetic receipts.
- **Tests:** All connector failure and recovery cases plus audit replay.
- **Acceptance:** Each action reaches `receipt_verified` in simulation and cannot dispatch to a real destination.
- **Prohibited:** No live email, filing, mailing, service, or calendar mutation.

### SKL-S5-04: Run security, isolation, load, outage, backup, and restore qualification

- **Agent:** Security and reliability
- **Size:** XL
- **Dependencies:** `SKL-S3-05`, `SKL-S4-06`, `SKL-S5-01`
- **Objective:** Establish evidence for controlled pilot acceptance.
- **Implementation:** Test tenant escape, wall bypass, privilege leakage, prompt injection, token abuse, Qwen saturation, provider outage, database failure, Temporal restart, projection lag, backup, restore, and audit integrity.
- **Tests:** Automated attack and recovery matrix with exact results.
- **Acceptance:** No critical or high unmitigated finding, recovery targets pass, and protected context does not leak.
- **Prohibited:** No destructive production test.

### SKL-S5-05: Complete human acceptance and publish the migration playbook

- **Agent:** Human owner, product, and migration
- **Size:** M
- **Dependencies:** `SKL-S5-02`, `SKL-S5-03`, `SKL-S5-04`
- **Objective:** Decide whether the pilot becomes the pattern for remaining matters.
- **Implementation:** Assemble source, migration, workflow, security, connector, recovery, and UI evidence. Record defects and conditions. Produce an inventory-only plan for remaining HammerTime matters.
- **Tests:** Independent replay by a second operator and document-hash verification.
- **Acceptance:** Human approves, rejects, or conditionally approves the pattern with exact conditions.
- **Prohibited:** Do not bulk-migrate remaining matters as part of this task.

## Sprint 6

### SKL-S6-01: Inventory official government writing and style manuals

- **Agent:** Official-source research and source governance
- **Size:** M
- **Dependencies:** `SKL-S0-01`, `SKL-S0-03`
- **Objective:** Identify the current official manuals that govern United States government publications, legislation, regulations, correspondence, court documents, plain language, accessibility, technical reports, and digital content.
- **Implementation:** Start with the GPO Style Manual collection supplied by the human owner. Search primary government sources across the federal legislative, executive, and judicial branches and a bounded set of official state exemplars. Record issuing body, scope, version, official landing and download URLs, source format, supersession, rights, update status, conflicts, and an include, defer, or exclude decision.
- **Tests:** Official-domain validation, duplicate and supersession review, missing-rights quarantine, link reachability, and ASCII-dash validation.
- **Acceptance:** The inventory defines an exact, rights-cleared first-wave source set and preserves branch, agency, court, and jurisdiction-specific differences.
- **Prohibited:** Do not treat public access as permission, use unofficial mirrors as canonical sources, or insert external research conclusions into HammerTime artifacts.

### SKL-S6-02: Acquire and seal the cleared official manual source batch

- **Agent:** Source acquisition and HammerTime intake custody
- **Size:** M
- **Dependencies:** `SKL-S6-01`
- **Objective:** Acquire the exact cleared manuals from canonical government endpoints and place one owned batch in HammerTime intake.
- **Implementation:** Download into `Inbox/_imports/<batch>/source-root`, preserving landing-page metadata, redirects, filenames, MIME types, byte counts, SHA256 hashes, issuing bodies, version dates, access times, source rights, and acquisition status. Produce a deterministic manifest and explicit duplicate, changed-source, inaccessible-source, and quarantine records.
- **Tests:** Repeat acquisition, wrong MIME type, redirect change, changed hash, duplicate bytes, partial download, and non-official endpoint rejection.
- **Acceptance:** The batch is complete, idempotent, source-sealed, isolated from unrelated Inbox material, and ready for normalization.
- **Prohibited:** Do not acquire deferred sources, credentials, third-party mirrors, or unrelated HammerTime Inbox content.

### SKL-S6-03: Normalize and ingest the official manuals through HammerTime

- **Agent:** HammerTime corpus operations
- **Size:** L
- **Dependencies:** `SKL-S6-02`, HammerTime governed ingestion acceptance
- **Objective:** Move the exact cleared batch through HammerTime normalization, semantic processing, scoped retrieval registration, and completion-gated Inbox finalization.
- **Implementation:** Use direct extraction for dense text, routed OCR and OCR QC when required, lossless normalized files, structured source records, Qwen3.8-controlled semantic routing and summaries, scoped decomposition, runtime-alias-driven vector and graph registration, non-empty completion evidence, exact-source finalization, and archive provenance relocation.
- **Tests:** Duplicate source, extraction failure, OCR reject, Qwen3.8 outage, vector outage, graph outage, missing completion evidence, archive collision, and rerun idempotency.
- **Acceptance:** Every included source reconciles from exact source hash to normalized artifact, source record, decomposition, retrieval projection, finalization manifest, and archive path.
- **Prohibited:** Do not run an implicit repository-wide scan, archive incomplete material, process unrelated Inbox content, hardcode runtime collection names, or use a non-Qwen model for corpus meaning.

### SKL-S6-04: Build source-linked official drafting style profiles

- **Agent:** Qwen3.8 corpus analysis and legal document engineering
- **Size:** L
- **Dependencies:** `SKL-S6-03`
- **Objective:** Produce reusable, source-linked style profiles without turning model output into workflow authority.
- **Implementation:** Extract core principles, document settings, typography, capitalization, abbreviations, punctuation, numbers, lists, tables, citations, correspondence formats, plain-language practices, accessibility, web content, legislative drafting, regulatory drafting, and court-specific rules. Preserve exact locators, scope, modality, exceptions, uncertainty, contradictions, supersession, and review status.
- **Tests:** Source locator verification, schema validation, conflict preservation, obsolete-rule labeling, wrong-scope rejection, missing source hash, and Qwen3.8 outage.
- **Acceptance:** A machine-readable comparison matrix and human-readable principles guide trace every rule to the exact issuing body, source version, hash, and locator.
- **Prohibited:** Do not silently harmonize conflicts, call recommendations mandatory, authorize an external action, or let a model own approval or workflow state.

### SKL-S6-05: Validate and publish the official drafting standards release

- **Agent:** Corpus release, retrieval assurance, and SKLegal integration
- **Size:** M
- **Dependencies:** `SKL-S6-04`, `SKL-S2-01`
- **Objective:** Validate a bounded HammerTime release and expose immutable references through the read-only SKLegal adapter.
- **Implementation:** Reconcile source, normalized, structured, decomposed, vector, graph, profile, and release artifacts. Run scoped retrieval tests, source-rights checks, secondary Qwen3.8 review, deep corpus health, release manifest validation, alias-safe promotion, rollback verification, and read-only adapter checks.
- **Tests:** Missing artifact, wrong source version, stale projection, scope collapse, retrieval of a superseded rule as current, alias drift, promotion failure, and rollback.
- **Acceptance:** The candidate release is healthy, reversible, source-complete, conflict-preserving, and addressable by immutable HammerTime release and artifact references.
- **Prohibited:** Do not promote to UAT or production without the existing release gates, mutate HammerTime through the SKLegal read-only adapter, or use a release reference as external-action authorization.

### SKL-S6-05A: Add batch-scoped official drafting candidate release builder

- **Agent:** Deterministic HammerTime release engineering
- **Size:** M
- **Dependencies:** `SKL-S6-03`, `SKL-S6-04`, `SKL-S2-01`
- **Objective:** Build an immutable dev candidate manifest from only the sealed official drafting batch without repository-wide corpus discovery or alias mutation.
- **Implementation:** Follow `docs/tasks/SKL-S6-05A-TDD.md`. Validate the explicit finalized file list, rights evidence, completion evidence, normalized hashes, decompositions, profile artifacts, and existing dev projection bindings. Dry run emits a content-free plan and writes nothing. Apply mode may write only a new release manifest through the HammerTime release contract.
- **Tests:** Dry-run no-write proof, missing source, unrelated file, Inbox path rejection, quarantined rights, changed normalized hash, missing or stale decomposition, projection mismatch, existing release collision, and alias immutability.
- **Acceptance:** One exact 14-source candidate is reproducible, source-complete, profile-linked, and ready for the parent S6-05 qualification gates without changing a runtime alias.
- **Prohibited:** Do not scan or process HammerTime Inbox, discover the repository corpus implicitly, change processing state, rebuild shared stores, promote an alias, or include unrelated sources.

## Internal public-synthetic MVP

### SKL-MVP-01: Compose fail-closed internal application API

- **Card:** `bb5cb31d`
- **Agent:** Internal MVP API engineering
- **Size:** M
- **Dependencies:** `SKL-S1-02`, `SKL-S1-03`, `SKL-S1-04`, `SKL-S1-05`, `SKL-S1-04A`, `SKL-S2-04`, `SKL-S3-05`, `SKL-S4-05`
- **Objective:** Compose one explicit FastAPI application for the existing workspace, corpus, claim, governance, task, deadline, Work Product, Approval, and audit boundaries using only public synthetic data.
- **Implementation:** Add an application factory that mounts an explicit reviewed router set and accepts durable authentication, Tenant and Matter policy, audit, persistence, and provenance dependencies. Production-mode construction must fail closed when any dependency is absent or synthetic. Development mode may use deterministic public-synthetic adapters only when explicitly selected. Authenticate before resource lookup, authorize Tenant and Matter membership plus conflicts, privilege, ethical walls, retention, hold, capability, and operation policy, then emit correlation and append-only audit records with bounded redacted detail. Health may report only bounded component state and must not expose protected fields or trigger unbounded scans.
- **Tests:** Application construction, OpenAPI route inventory, health bounds, missing dependency startup denial, explicit development mode, missing authentication, expired or revoked capability, cross-Tenant and cross-Matter denial before lookup, conflict and wall denial, audit outage, persistence outage, provenance requirements, redaction, correlation, pagination, public-synthetic fixture validation, and deterministic rollback.
- **Acceptance:** The public-synthetic internal API starts only with the complete reviewed dependency composition, every protected route enforces policy before lookup, audit and provenance remain reconstructable, relevant focused and integration tests pass, and completion evidence records exact files, results, limitations, and rollback.
- **Prohibited:** No HammerTime `Inbox/` access, protected Matter content, provider request, production credential, external action, host deployment, merge, or push. Do not weaken authentication, Tenant or Matter isolation, custody, audit, provenance, provider-purity, rollback, or external-action gates.

### SKL-MVP-02: Wire bounded browser authentication and live API sessions

- **Card:** `431c4fbf`
- **Agent:** Internal browser session engineering
- **Size:** M
- **Dependencies:** `SKL-MVP-01` immutable commit `8291ffab4021c568850cd389c692783b8666c41a`, `SKL-S4-01`, `SKL-S1-03`, `SKL-S1-04A`
- **Objective:** Replace fixture-only browser startup with a bounded internal authentication and session flow wired to the exact reviewed MVP API while keeping server authorization authoritative.
- **Implementation:** Define an internal public-synthetic session bootstrap, current-session, expiry, refresh-failure, revocation, and sign-out contract. Use only approved credential references or bounded public-synthetic development credentials. Keep raw capabilities out of localStorage, URLs, browser history, logs, analytics, and committed files. Carry exact Tenant context and correlation identity on every API request. Invalidate incompatible session state and cached data on Tenant switching. Use a configurable same-origin API base with deny-by-default CORS, CSP, sanitized errors, CSRF protection where cookies are used, and secure cookie or memory-only credential posture. Client route guards are usability controls only.
- **Tests:** Sign-in, sign-out, expiry, revocation, refresh failure, Tenant switch, wrong Tenant, wrong Matter, insufficient capability, direct URL navigation, back button, multi-tab invalidation, API outage, `401`, `403`, correlation propagation, browser storage and telemetry leakage, CSP, CSRF, CORS, public-synthetic fixture bounds, and deterministic rollback to the fixture-only development shell.
- **Acceptance:** The reviewed React application establishes only a server-verifiable bounded session, every live request reaches the immutable MVP API contract, all denial and outage states fail closed without leaking credentials or protected fields, relevant browser and API integration tests pass, and completion evidence records exact files, results, limitations, and rollback.
- **Prohibited:** No protected Matter content, HammerTime `Inbox/` access, provider request, production credential issuance, external action, host deployment, merge, or push. Do not store raw capability material in browser persistence or weaken server-side Tenant, Matter, policy, audit, provenance, or revocation enforcement.

### SKL-MVP-03: Complete the internal Matter workbench vertical slice

- **Card:** `9dcda941`
- **Agent:** Internal Matter workbench engineering
- **Size:** L
- **Dependencies:** `SKL-MVP-02` immutable commit `398c414b670d2c60630e55891a4daa43a0dbe089`, `SKL-S4-02`, `SKL-S4-03`, `SKL-S4-04`, `SKL-S4-04A`, `SKL-S4-04B`, `SKL-S4-04C`, `SKL-S4-06`
- **Objective:** Deliver the smallest coherent internal public-synthetic Matter workbench across the existing React application and reviewed API without inventing legal, workflow, approval, execution, provider, or audit state.
- **Implementation:** Replace navigation placeholders with typed live read-only or simulation-first views for Client and Matter navigation, Matter activity, artifact provenance, Evidence Items, Fact Assertions and tensions, Issues and Claims, Authorities, corpus search, Tasks and Deadlines, Work Products, Approvals, simulated Execution Events and receipts, and audit replay. Show exact Tenant, Matter, source, content hash, classification, provenance, proposal, uncertainty, contradiction, contrary Authority, human decision, Work Product version, Approval, Execution Event, correlation, and audit state when available. Clearly label safely unavailable and post-MVP functions. Keep every mutation behind an idempotency key and its reviewed state machine. External actions stop at simulation or draft unless their separately approved exact validation and human gates exist.
- **Tests:** Public-synthetic end-to-end workbench flow, cross-Tenant denial, cross-Matter denial, missing Evidence Item, contradictory Fact Assertion, contrary Authority, stale Work Product Approval invalidation, audit outage denial, API outage recovery, direct navigation, keyboard navigation, WCAG AA checks, responsive layouts, visual regression, contract compatibility, secret and browser leakage checks, deterministic fixture reset, and rollback to the immutable SKL-MVP-02 handoff.
- **Acceptance:** One public-synthetic Client and Matter can be navigated end to end through the coherent workbench with source-grounded and reconstructable state, all unavailable functions are explicit, every denial and outage fails closed, no external action progresses beyond simulation or draft, the exact feature matrix separates complete, unavailable, and post-MVP behavior, relevant web, API, contract, integration, accessibility, visual, secret, and leakage checks pass, and completion evidence records exact files, results, limitations, and rollback.
- **Prohibited:** No protected Matter content, HammerTime `Inbox/` access, provider request, production credential issuance, host deployment, external dispatch, filing, service, email, calendar, client communication, merge, or push. Do not weaken authentication, Tenant or Matter isolation, conflicts, privilege, ethical walls, retention, legal hold, audit, provenance, exact-version Approval, revocation, rollback, provider-purity, or external-action gates.

### SKL-MVP-04R1: Repair MVP audit, persistence, and static blockers

- **Card:** `6f3cd09d`
- **Agent:** `jarvis`
- **Base:** Exact blocked MVP head `51cb9c3301c69737bf727c8d46360ab95a9d9e6f` in a fresh isolated worktree.
- **Objective:** Repair only the blockers proven by independent review `78277c6b`: append-only fail-closed browser-session audit and provenance, missing `SentenceGrounding` persistence parity, and exact Ruff and format failures.
- **Implementation:** Inject a canonical bounded session audit sink into the MVP composition and record bootstrap, refresh, Tenant switch, revoke, and denial outcomes with correlation and non-secret identity metadata. State-changing operations fail before mutation when audit is unavailable. Add the exact `SentenceGrounding` mapping, decomposition, reconstruction, and write-contract coverage without changing unrelated domain semantics. Apply only the proven static formatting repairs.
- **Tests:** Cover success, denial, audit outage, mutation ordering, rotation, replay, and value leakage for every session operation. Run browser/API authorization matrices, domain and persistence mapping tests, security, audit, workflow, Ruff, format, typecheck, build, dependency audit, and available secret scans.
- **Acceptance:** Produce an immutable commit, tree, source, test, lock, evidence, and rollback hash set ready for browser qualification `1e39b105` and independent rereview `bdb1bd1b`, leaving both unclaimed.
- **Rollback:** Revert only this candidate to `51cb9c3301c69737bf727c8d46360ab95a9d9e6f`; no data or host rollback exists.
- **Prohibited:** No deployment, service, database, credential, protected data, provider traffic, external action, merge, push, or cleanup.

### SKL-MVP-03A: Implement the V2 AI-first Matter cockpit in React

- **Card:** `b18fed11`
- **Agent:** `codex-skl-mvp-v2-react`
- **Base:** Exact repaired MVP evidence commit `cc20a130fc1f61ad34ca0f12ceb62f1fea08b35a`, implementation `9d6fad3afd0893a62d7bd0b1138c84a48bdc03c7`, and tree `b150cb207af2be09687daf30271b18a16ab8ab63` in isolated worktree `/tmp/sklegal-swarm-20260823/b18fed11`.
- **Objective:** Port the reviewed `docs/planning/wireframes/index-v2.html` information architecture and visual hierarchy into the existing public-synthetic React MVP without inventing backend state or weakening the reviewed API and browser-session boundaries.
- **Implementation:** Keep `/matters/$matterId` as the authorized center and implement compact and expanded Matter cockpit navigation. Render the decision and AI operating model, corpus map, AI intake, artifacts and lineage, essential Elements matrix, ranked recommendation, strategy and Authority lanes, bounded agent team, provider-neutral model routing, Work Product assembly, Tasks and Deadlines, source and run evidence, Matter activity log, failure states, and delivery map. Bind only existing workspace, Claim, corpus, Work Product, Approval, audit, and provenance fields. Render every unimplemented proposal, agent, Authority, artifact intake, model, Deadline, connector, and external-action capability as explicit inert or safely unavailable state.
- **Tests:** Extend public-synthetic component and route tests for the full cockpit hierarchy, source-role separation, existing API-bound Matter records, exact unavailable labels, no direct model or connector action, Approval invalidation, compact and expanded layouts, keyboard and landmark semantics, sanitized failures, and browser-storage leakage. Run complete web tests, typecheck, lint, build, API contract checks, Ruff for touched Python if any, format, diff, ASCII dash, dependency audit, and available secret scans.
- **Acceptance:** One public-synthetic Client and Matter navigate through a coherent working V2 React cockpit with source-grounded Claims, Evidence Items, Authority, recommendation, Work Product, audit, and explicit unavailable states. The implementation reproduces the reviewed hierarchy at compact and expanded sizes, preserves every server-side authorization boundary, and produces immutable commit, tree, source, test, evidence, and rollback hashes for API-backed browser qualification `9212ef10`.
- **Rollback:** Revert only the V2 cockpit candidate to `cc20a130fc1f61ad34ca0f12ceb62f1fea08b35a`. No data or host rollback exists.
- **Prohibited:** No protected content, HammerTime `Inbox/` access, provider request, credential, external action, deployment, service, merge, push, or cleanup. Do not invent an endpoint, treat a model proposal as workflow state, expose private route or host details, or weaken Tenant, Matter, policy, audit, provenance, exact-version Approval, revocation, or simulation-only external-action gates.

### SKL-MVP-SUPPORT-06: Build a reversible local public-synthetic MVP preview launcher

- **Card:** `b53c02bd`
- **Agent:** `codex-skl-mvp-preview-launcher`
- **Base:** Exact sealed 6f3cd09d evidence handoff `cc20a130fc1f61ad34ca0f12ceb62f1fea08b35a` in a fresh isolated worktree.
- **Objective:** Add one bounded launcher and teardown contract for the reviewed public-synthetic FastAPI composition and React production build without editing V2 UI implementation files.
- **Implementation:** Accept an exact candidate root, validate immutable source and lock hashes, require explicit public-synthetic mode, seed only deterministic public data, bind API and web only to configurable loopback ports, record process identities and launch hashes in an instance-scoped runtime directory, refuse collisions, stale or foreign PID ownership, protected or production dependencies, and serve direct browser routes through the production build fallback. Stop only processes whose recorded identity still matches and reset only the named public-synthetic instance.
- **Tests:** Exercise health and readiness, session bootstrap, Client and Matter reads, browser route fallback, API outage, restart, deterministic fixture reset, occupied ports, stale PID records, process ownership, loopback enforcement, hash drift, bounded logs, teardown, and secret or protected-data leakage. Run relevant Python tests, web typecheck, lint, unit tests, production build, dependency audit, static checks, and repository diff checks.
- **Acceptance:** One command starts the exact public-synthetic API and web build on loopback, one command stops only its recorded processes, all required probes pass, exact files and hashes are sealed, and the stopped rollback state is proven for downstream V2 browser work.
- **Rollback:** Stop the named instance through the launcher, verify its recorded PIDs and ports are gone, then revert only this candidate to `cc20a130fc1f61ad34ca0f12ceb62f1fea08b35a`. No persistent host or data rollback exists.
- **Prohibited:** No V2 UI file edit owned by `b18fed11`, protected data, credential, provider request, external action, persistent deployment, merge, push, or unrelated cleanup.

### SKL-MVP-03B: Integrate enriched V2 API fixture into the working cockpit

- **Card:** `b69f0c97`
- **Agent:** `codex-skl-mvp-v2-api-integration`
- **Base:** Exact V2 React candidate `48502082fe3be7c115b88937ab222844a4f6f5ff` with exact enriched API fixture implementation `ead05bcc44c589a82a6e1556b68a9726d31b16c7`, synthesized in a fresh isolated worktree.
- **Objective:** Produce one immutable candidate whose explicitly flagged loopback preview bootstraps in real Chrome and renders coherent non-empty reviewed public-synthetic Client, Matter, Claim, Evidence Item, Authority, corpus, Work Product, audit, provenance, and simulated execution data.
- **Implementation:** Preserve the exact V2 React UI and launcher boundaries. Integrate the reviewed fixture, exact routed capabilities, fixture hash guard, and API composition without weakening browser session, CSRF, Tenant, Matter, audit, provenance, persistence, simulation, or external-action boundaries. Keep absent contracts explicit and safely unavailable.
- **Tests:** Run all b18 and 0457 focused tests, web unit tests, typecheck, lint, production build, API boundary tests, Ruff, format, dependency audits, diff, ASCII dash, leakage, reset, teardown, and a separate loopback real Chrome bootstrap and navigation proof. Confirm the existing b18 instance and card `9212ef10` remain untouched.
- **Acceptance:** Real Chrome reaches the exact Client and Matter through reviewed APIs and displays non-empty enriched data for every available contract, while missing contracts remain explicit. Seal commit, tree, fixture, build, browser, test, evidence, and rollback hashes.
- **Rollback:** Stop only the synthesis card's named launcher instance, prove its loopback ports closed, and revert only this candidate to `48502082fe3be7c115b88937ab222844a4f6f5ff`. No persistent data migration exists.
- **Prohibited:** No disturbance of the live b18 instance or `9212ef10`, protected data, credential, provider request, external action, persistent deployment, merge, push, or unrelated cleanup.

### SKL-MVP-03D: Synthesize the final working V2 MVP candidate

- **Card:** `6417165c`
- **Agent:** `codex-skl-mvp-v2-final-synthesis`
- **Base:** Exact enriched API candidate `ca60896554fbe36a64e08d6b81a1aa92e717a0df`, exact session reload and CSP evidence candidate `498a22747944afaa5b7cea3c88fbde20f4a1117f`, and exact truthful UX candidate `334b0b1c156761d66776675a2a8f22f48364b422` in a fresh isolated worktree.
- **Objective:** Produce one clean immutable final candidate that preserves every reviewed API, session hydration, CSP, truthful UX, accessibility, launcher, and browser qualification boundary without adding product scope.
- **Implementation:** Integrate only the pinned candidate ranges and resolve only direct integration conflicts. Preserve exact public synthetic identities, process-local fixture state, fail-closed authorization, session reload, script-free CSP, truthful unavailable states, accessible responsive groups, and reversible loopback launcher ownership.
- **Tests:** Run the complete combined web, API, browser session, persistence, audit, fixture, launcher, type, lint, production build, dependency audit, diff, ASCII dash, leakage, reset, teardown, rollback, and committed Node Chrome reload and CSP harness gates. Prove the exact enriched Client, Matter, Claims, Evidence Items, Authority, corpus, Work Product, audit, provenance, simulated execution, reload, responsive, and accessibility states in real Chrome.
- **Acceptance:** Seal the exact commit, tree, archive, locks, fixture, distribution, browser, test, evidence, and rollback hashes. Leave one exact loopback-only final preview live with its recorded stop command and no unexpected browser, network, storage, accessibility, CSP, or truthfulness failure.
- **Rollback:** Stop only the final synthesis instance through its recorded launcher state, verify its exact PIDs and loopback ports close, reset only that instance, and revert to `ca60896554fbe36a64e08d6b81a1aa92e717a0df`. No persistent data migration exists.
- **Prohibited:** No merge, push, persistent deployment, protected data, credential, provider request, external action, unrelated service mutation, or cleanup.

### SKL-MVP-SUPPORT-07: Populate the live V2 public-synthetic API composition

- **Card:** `0457ea71`
- **Agent:** `codex-skl-mvp-v2-api-fixture`
- **Base:** Exact sealed launcher evidence handoff `4777de8b91914bac4ebe70cae260e7ee241ce98e` in a fresh isolated worktree, with only the required acceptance bindings from fixture candidate `196cb7e35098a89dcfec895a1b9d077023b6e878`.
- **Objective:** Populate only the loopback preview composition with coherent non-empty public-synthetic records for every existing reviewed V2 read contract and explicit safely-unavailable states for absent contracts.
- **Implementation:** Bind one deterministic Client and Matter across Matter Events, provenance, Evidence Items, Fact Assertions and tensions, Issues and Claims with support and counter-support, Authority, corpus results, Work Product versions and Approval state, simulated Execution Event and receipt state, audit replay, uncertainty, contradiction, and model or run attribution. Preserve exact cross-record identifiers and source hashes. Do not invent a Task or Deadline API when the reviewed router has none.
- **Tests:** Cover authenticated navigation and repeatable reads, cross-Tenant and cross-Matter denial, unauthenticated denial before lookup, audit and persistence outage, deterministic reset and replay, fixture-to-contract bindings, non-empty V2 sections, exact source and audit linkage, safe unavailable states, external-action ceiling, leakage, Ruff, format, diff, and Unicode dash checks.
- **Acceptance:** The immutable fixture candidate is ready for launcher and V2 UI synthesis, all existing read APIs return coherent public-synthetic data, absent APIs remain explicit and fail closed, no unbound or cross-Tenant record exists, and exact evidence and rollback are linked.
- **Rollback:** Revert only this candidate to `4777de8b91914bac4ebe70cae260e7ee241ce98e`. The preview fixture is process-local, so launcher teardown discards it without data migration.
- **Prohibited:** No file under `apps/web`, protected data, credential, provider request, external action, persistent deployment, merge, push, or unrelated cleanup.

### SKL-MVP-SUPPORT-05: Build the V2 cockpit fixture and acceptance matrix

- **Card:** `7e244214`
- **Agent:** `codex-skl-mvp-v2-fixtures`
- **Base:** Reviewed public-synthetic fixture handoff `f676aabe9ca52bebf154fc625f4f2c9a99d80d5d`; parallel React implementation card `b18fed11` owns its application files.
- **Objective:** Create an additive deterministic public-synthetic V2 cockpit fixture and executable acceptance matrix derived from `docs/planning/wireframes/index-v2.html` and `docs/planning/wireframes/COMPONENT-API-MAP-V2.md` without editing the parallel React implementation.
- **Implementation:** Extend the reviewed V1 fixture through a distinct V2 fixture and schema-aware acceptance contract. Map every V2 navigation section to an exact React component name, API contract, fixture identity, complete or safely-unavailable state, accessibility expectation, and evidence assertion. Preserve legal-domain vocabulary, typed-proposal ownership, source-lane separation, immutable provenance, Tenant and Matter scope, and simulation-only external actions.
- **Tests:** Execute deterministic fixture and matrix validation. Reject missing or extra navigation sections, invented or cross-Tenant records, contract drift, unbound source or audit state, non-simulated external-action progression, duplicate identities, broken references, missing accessibility expectations, and unsupported completion claims. Run the reviewed V1 fixture tests plus focused V2 tests, formatting, JSON, diff, ASCII dash, and scoped leakage scans.
- **Acceptance:** Every V2 navigation section has a public-synthetic fixture binding or an explicit safely-unavailable state and an executable assertion. Produce immutable candidate, tree, parent, fixture, matrix, validator, tests, evidence, and rollback hashes suitable for direct consumption by `b18fed11` without modifying its files.
- **Rollback:** Revert only the additive V2 fixture, matrix, validator, tests, evidence, and this TDD to base `f676aabe9ca52bebf154fc625f4f2c9a99d80d5d`. The reviewed V1 fixture and all application, service, database, and runtime state remain unchanged.
- **Prohibited:** Do not edit files owned by `b18fed11`; deploy or start a live service; use protected Matter data; make a provider request; advance an external action; read credentials; merge; push; or clean another worktree.
