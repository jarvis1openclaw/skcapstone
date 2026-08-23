# SKLegal V2 MVP contract freeze

Status: frozen for implementation review

Card: `cb60092e`

Base candidate: `8177c88c5fd371a487e2c206c53b24e3619c1855`

The authoritative executable contract is
`v2-surface-manifest.v1.json`. It owns the ordered identity of all 18 V2
surfaces, their current truth, their durable target, the HTTP operations they
consume, and the implementation lane that owns each boundary.

The manifest is authoritative over the older 17-row public-synthetic
acceptance matrix. `legacy_fixture_section_id` exists only to reconcile that
matrix. In particular, the canonical `agent-team` and `blind-challenge`
surfaces remain separate even though the old fixture called their combined
symbolic component `AgentChallengeTeam`.

## Frozen invariants

1. Every protected request is Tenant scoped. Matter resources are also Matter
   scoped. The server derives scope from authenticated authorization context
   and rejects conflicting client-supplied scope.
2. Every operation names one capability, one purpose, its mutation authority,
   its idempotency behavior, its success audit event, its provenance fields,
   and its closed error vocabulary.
3. Models return typed proposals. They cannot approve, mutate canonical legal
   state, or dispatch an external action.
4. Public-synthetic data is ephemeral demonstration truth. It cannot satisfy a
   durable persistence, audit, provenance, or deployment acceptance criterion.
5. Durable mutations commit the state change, immutable audit event,
   provenance link, and outbox record atomically.
6. Retrying a mutation with the same principal, Tenant, Matter, operation, and
   idempotency key returns the original result. Reuse with different bytes
   fails with `idempotency_conflict`.
7. `Claim.id` is the canonical Claim identity and lifecycle owner.
   `LedgerClaim` is a one-to-one evidence and review projection whose
   `claim_id` equals that canonical identity. It cannot be created without the
   canonical Claim, cannot own a competing lifecycle, and cannot silently
   reconcile different statements or scope.
8. An Approval is operative only when every condition in
   `v2-approval-validity.v1.schema.json` is satisfied. Hash equality alone is
   insufficient.
9. Corrections append superseding records. They never rewrite event, audit,
   proposal, review, Approval, provenance, or receipt history.
10. External actions remain simulation-only in this MVP. Recommendation
    acceptance may create a proposed Task or Work Product request, never a
    connector dispatch.

## Truth modes

- `static_reviewed`: reviewed explanatory copy with no runtime data claim.
- `public_synthetic_read`: executable only against the resettable public
  synthetic composition.
- `safely_unavailable`: visible and inert because its executable backend is
  absent.
- `durable_target`: frozen interface owed by a feature lane and not evidence
  that an implementation exists.

## Lane ownership

The manifest freezes seven feature-lane interfaces. Feature lanes own their
dedicated router, service, persistence adapter, tests, fixture fragment, and
evidence only. They do not register routers in the central application or edit
the live cockpit. Integration cards own central composition and frontend
wiring after independent review.

| Lane | Card | Frozen boundary |
| --- | --- | --- |
| joined analysis | `929c6ada` | Matter scope, reconciled Claims, Elements, Authority and recommendations |
| Agent Runs | `85967293` | analysis commands, runs, challenges and model evidence |
| artifact intake | `6894d326` | idempotent originals, custody and derived lineage |
| Work Products | `0d1d81ce` | version mutation, validation and complete Approval validity |
| Tasks and Deadlines | `519832c7` | deterministic deadlines, Tasks and simulation handoff |
| activity | `ec0763b9` | joined append-only activity, audit and export projections |
| corpus | `e0ad0f06` | durable scoped search, exact spans and retrieval traces |

## Error envelope

All non-success responses use the shared error schema. `detail.code` is closed,
stable and safe to display. The response includes a correlation ID but never a
token, secret, protected record detail, database message, provider payload, or
filesystem path. A denial and a missing cross-scope record are intentionally
indistinguishable to an unauthorized principal.

## Rollback

This freeze is additive. Rollback removes only this directory and its focused
contract test. It does not alter runtime state, databases, fixtures, services,
listeners, credentials, branches owned by other agents, or the live preview.
