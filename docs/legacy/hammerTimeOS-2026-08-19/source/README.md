# hammerTimeOS

hammerTimeOS is the proposed governed application and agent runtime around the existing HammerTime legal corpus framework.

The current design uses a strangler approach:

- HammerTime remains the provenance-owning corpus and artifact subsystem.
- hammerTimeOS owns operational matter state, workflow state, access policy, approvals, and audit events.
- Qdrant and FalkorDB remain rebuildable retrieval projections.
- The local Qwen3.8 route is a bounded analyst. It may submit typed proposals, but it cannot approve, publish, or mutate canonical records directly.
- Existing `Problem` and `Incident` identifiers remain valid aliases while the application adopts legal-domain terminology.

## Design set

- [Target architecture](docs/architecture/target-architecture.md)
- [Legal domain model](docs/design/legal-domain-model.md)
- [Agentic harness](docs/design/agentic-harness.md)
- [Framework and third-party evaluation](docs/discovery/framework-evaluation.md)
- [Epic plan](docs/planning/epic-plan.md)

## Current status

This repository contains an initial architecture and implementation plan, not a production application. The first build target is a narrow, non-production vertical slice that imports one test matter through a read-only HammerTime adapter, performs source-grounded retrieval, creates claim proposals, and requires explicit human approval before release.

