# SKLegal epic and sprint plan

Date: 2026-08-19  
Status: Approved, retrieval architecture amended 2026-08-21
Execution policy: Sequential, approval-gated, test-evidenced

## Epic outcome

Deliver a multi-tenant SKLegal application on chiap01 that uses proper legal matter terminology, preserves HammerTime provenance, enforces CapAuth at every protected boundary, supports local Qwen and approved OpenAI agent roles, manages research and work products, and tracks email, filing, service, mailing, calendar, and client communications through verified action receipts.

## Global definition of done

Every implementation task must provide:

- exact files changed
- unit tests for new domain behavior
- integration tests for changed boundaries
- authorization-denial tests
- idempotency or replay evidence for mutations
- observability evidence using a correlation ID
- updated documentation
- no unresolved high-severity defect
- SKCapstone task evidence and completion update

## Sprint sequence

### Sprint 0: Architecture approval and readiness

Outcome: The design is approved, chiap01 can safely host the stack, protected-data boundaries are explicit, the repository is reproducible, and doc.haus reuse has a license decision.

Tasks:

- `SKL-S0-01` Approve SKLegal architecture and pilot boundaries
- `SKL-S0-02` Qualify chiap01 capacity and deployment baseline
- `SKL-S0-03` Define threat model, data classification, and source-rights controls
- `SKL-S0-04` Scaffold the reproducible monorepo and CI baseline
- `SKL-S0-05` Audit doc.haus licensing, provenance, and reusable components

### Sprint 1: Legal domain, persistence, and CapAuth

Outcome: SKLegal can represent legal matters and enforce tenant, matter, conflict, privilege, and ethical-wall policy before any corpus or model access.

Tasks:

- `SKL-S1-01` Implement the legal-domain package and state machines
- `SKL-S1-02` Implement PostgreSQL schemas, migrations, and row-level security
- `SKL-S1-03` Integrate CapAuth principals, capabilities, delegation, and revocation
- `SKL-S1-04` Implement conflicts, privilege, ethical walls, retention, and holds
- `SKL-S1-05` Implement append-only audit, outbox, and telemetry correlation

### Sprint 2: HammerTime integration and migration

Outcome: SKLegal can read a pinned HammerTime snapshot, import one legacy matter losslessly, submit approved new ingestion, build and query local governed PostgreSQL retrieval projections, and report bounded corpus health.

Tasks:

- `SKL-S2-01` Build the read-only HammerTime release and artifact adapter
- `SKL-S2-02` Build the legacy matter snapshot and pilot importer
- `SKL-S2-03` Build the governed SKLegal-to-HammerTime ingestion bridge
- `SKL-S2-04` Build PostgreSQL retrieval and graph adapters
- `SKL-S2-05` Build the materialized corpus registry and reconciliation jobs
- `SKL-S2-06` Build the official and free legal-source connector registry

Retrieval assurance gate:

- `SKL-S2-10` Define the tenant-native PostgreSQL retrieval partition contract

### Sprint 3: Durable agent harness and legal assurance

Outcome: A policy-gated Temporal workflow can retrieve permitted context, invoke Qwen or an allowed OpenAI route, reduce typed proposals, validate claims and citations, challenge output, and wait for human review.

Tasks:

- `SKL-S3-01` Implement Temporal workflow foundations and task queues
- `SKL-S3-02` Implement the Qwen and OpenAI provider-neutral model gateway
- `SKL-S3-03` Implement versioned agent specifications and the CapAuth tool gateway
- `SKL-S3-04` Build retrieval evaluation and qualify the custom embedding
- `SKL-S3-05` Implement claim, authority, citation, challenge, and release gates

### Sprint 4: Legal workbench and action workflows

Outcome: Users can manage matters, evidence, research, claims, documents, deadlines, communications, and all external action types through exact-version approval and receipt tracking.

Tasks:

- `SKL-S4-01` Build the SKLegal design system and application shell
- `SKL-S4-02` Build the client and matter workspace
- `SKL-S4-03` Build research, claim ledger, authority, and source viewer
- `SKL-S4-04` Build document drafting, versioning, and DOCX redlines
- `SKL-S4-05` Build tasks, deadlines, reminders, and calendar integration
- `SKL-S4-06` Build communication, email, service, mailing, and filing connectors

### Sprint 5: Pilot proof and rollout gate

Outcome: The Liberty Auto Plaza matter passes a lossless import, governed agent run, action simulation, security and recovery tests, and human acceptance. The result becomes the migration pattern for remaining matters.

Tasks:

- `SKL-S5-01` Execute and verify the pilot dry run and structured import
- `SKL-S5-02` Execute the end-to-end governed Qwen proposal workflow
- `SKL-S5-03` Qualify external-action simulation and approval receipts
- `SKL-S5-04` Run security, isolation, load, outage, backup, and restore qualification
- `SKL-S5-05` Complete human acceptance and publish the migration playbook

## Dependency spine

```text
SKL-S0-01 approval
  -> Sprint 0 readiness work
  -> Sprint 1 security and domain
  -> Sprint 2 HammerTime integration
  -> Sprint 3 agent harness
  -> Sprint 4 workbench and actions
  -> Sprint 5 pilot acceptance
```

Within each sprint, independent tasks may run concurrently only after their explicit dependencies pass. Corpus semantic tasks require Qwen3.8. External action tasks remain simulation-only until Sprint 5 acceptance and a separate connector activation approval.

## Release gates

### Architecture approved

- human approves the technical design and pilot scope
- all implementation cards remain blocked until this gate

### Foundation ready

- capacity, repository, security, domain, database, CapAuth, and audit tests pass

### Integration ready

- HammerTime is unchanged by read-only imports
- ingestion bridge dry run passes
- corpus coverage and projection lag are visible

### Harness ready

- malformed model output cannot mutate state
- Qwen and OpenAI routes obey policy and egress restrictions
- claim and citation gates fail closed

### Workbench ready

- user can trace every material output to sources and approvals
- action connectors produce simulation receipts

### Pilot accepted

- import is lossless and idempotent
- tenant isolation and recovery pass
- human accepts the exact replay package

## Board policy

SKCapstone cards use the stable task key in the title, the `sklegal` and sprint labels, the epic card label, and a link to the corresponding TDD section. Subagents claim only leaf task cards. Sprint tracking cards and the epic are never claimed as implementation work.
