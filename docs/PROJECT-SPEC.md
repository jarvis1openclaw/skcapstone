# SKLegal project specification index

This index distinguishes approved current specifications from historical design
material. When documents disagree, approved architecture and current executable
contracts take precedence over planning notes and legacy archives.

## Product boundary

SKLegal owns governed legal operational state, authorization and policy enforcement,
workflow state, human approval gates, audit evidence, and controlled adapters.
HammerTime owns initial source corpus originals and promoted releases. SKCapstone owns
engineering coordination. CapAuth supplies signature and credential primitives while
SKLegal makes the resource-specific authorization decision.

## Normative specifications

- [Architecture approval](approval/ARCHITECTURE-APPROVAL.md)
- [Approved design hashes](approval/DESIGN-HASHES.sha256)
- [High-level technical design](architecture/SKLEGAL-HIGH-LEVEL-TDD.md)
- [External action state machine](architecture/EXTERNAL-ACTION-STATE-MACHINE.md)
- [Liberty Auto pilot design](architecture/LIBERTY-AUTO-PILOT-TDD.md)
- [Foundation contract](development/FOUNDATION.md)
- [Domain contract](development/DOMAIN.md)
- [Persistence contract](development/PERSISTENCE.md)
- [CapAuth contract](development/CAPAUTH.md)
- [Policy contract](development/POLICIES.md)
- [Audit contract](development/AUDIT.md)
- [Threat model](security/THREAT-MODEL.md)
- [Cryptography inventory](crypto-architecture.md)

## Delivery specifications

- [Epic and sprint plan](planning/EPIC-SPRINT-PLAN.md)
- [SKCapstone board map](planning/SKCAPSTONE-BOARD-MAP.md)
- [Subagent task TDDs](tasks/SUBAGENT-TASK-TTDS.md)
- Evidence receipts under `docs/evidence/`

## Historical provenance

- [Archived hammerTimeOS proposal](legacy/hammerTimeOS-2026-08-19/README.md),
  superseded on migration to the SKLegal strategic name.

No document in `docs/legacy/` is a current implementation requirement.
