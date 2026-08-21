# SKLegal domain package contract

Status: Implemented for `SKL-S1-01`  
Date: 2026-08-20  
Package: `packages/domain/src/sklegal_domain`

## Purpose

The domain package represents SKLegal legal work without database, API, workflow,
model, or user-interface dependencies. It is the canonical application vocabulary
for tenants, clients, engagements, matters, legal records, review decisions, and
execution evidence.

Historical HammerTime storage labels are accepted only inside `LegacyAlias`
provenance. They do not define canonical Python entity types.

## Global invariants

- Pydantic models use strict input validation, reject extra fields, and are frozen.
- Unvalidated `model_copy(update=...)`, `copy(update=...)`, `__replace__`, and
  `model_construct()` paths are disabled. Plain validated copies remain available.
- Every protected entity carries `tenant_id`.
- Every matter record carries both `tenant_id` and `matter_id`.
- UUID identity values cannot be nil.
- Entity identity, stable relationship identity, exact artifact bindings, source
  evidence, and append-only result payloads cannot be rewritten through evolution.
- A directly embedded source reference or legacy alias cannot have an observation
  time later than the owning entity audit time. This applies generically to direct
  fields and tuple fields on every domain entity.
- Ordinary `evolve()` calls cannot change status or status evidence.
- `transition_to()` checks an explicit graph, revalidates the complete new snapshot,
  increments the version, preserves identity, and rejects backward audit time.
- Callers cannot override `status`, `version`, `created_at`, or `updated_at` through
  transition changes.
- Records default to `confidential` and matter records default to `incomplete`.
- Missing effective-time bounds are explicit unknown time, not an unbounded match.

## Entity catalog

Business and scope aggregates:

- Tenant
- Client
- Engagement
- Matter

Matter aggregates:

- Party and PartyRole
- Forum and Proceeding
- MatterEvent and Transaction
- FactAssertion and TensionGroup
- EvidenceItem and CustodyEvent
- Authority
- Issue, Claim, Defense, Element, and Remedy
- DeadlineCalculation and Deadline
- Task
- Communication
- WorkProduct and WorkProductVersion
- ValidationResult
- Approval
- Execution, ExecutionEvent, and ExecutionReceipt

Value objects:

- EffectiveInterval
- ArtifactBinding
- ValidationSubject
- SourceReference
- LegacyAlias
- TypedValue

## State-machine rules

Each transition graph is declared beside its aggregate and has no implied edges.
Representative terminal and gate behavior includes:

- Matters need an opening timestamp before becoming open. A proposed Matter may
  close without ever opening when it supplies a closing timestamp. Closed
  Matters may reopen with a new opening timestamp or become archived; archived
  Matters are terminal.
- Verified parties, party roles, matter events, facts, evidence items, and
  authorities require explicit review provenance.
- Matter events with an initially unknown occurrence time may bind that time once
  during a transition. `occurred_at` represents an actual historical occurrence,
  cannot be later than its observation or owning audit time, and cannot later be
  rewritten.
- Tension groups begin unresolved and cannot acquire a selected assertion,
  rationale, reviewer, or resolution time without a valid review transition.
- Deadline candidates cannot become reviewed or operative without a trigger fact,
  calculation, and validation record. An operative deadline also needs its exact
  operative due time.
- Completed tasks and satisfied deadlines require completion timestamps. Those
  timestamps cannot appear in earlier states.
- Work product validation and approval are separate gates. A reviewed or approved
  payload cannot change while retaining those gates. A declared reset to
  `in_review` clears both gates before a new version can be reviewed.
- An approved communication payload cannot change while retaining validation,
  approval, destination, or execution evidence. A pre-queue reset to `draft`
  clears review evidence. Queued and later communication records bind an exact
  destination digest and cannot reset in place.
- Approval binds one artifact ID, version, and SHA-256 digest. Its original reviewer,
  decision time, and rationale are immutable after the decision. Revocation records
  a separate revoker, rationale, and exact revocation time.
- Execution follows `draft -> validated -> approved -> queued -> dispatched ->
  receipt_verified` for the successful path. Validation and approval gates are
  immutable typed snapshots that match the tenant, matter, and exact artifact
  binding. Validation must be passed and approval must be currently approved in the
  captured snapshot. Every transition preserves the exact prior event prefix and
  appends exactly one matching event at the transition time. Receipt verification
  additionally requires a matching immutable receipt whose artifact and destination
  hashes match the execution. A receipt ID exists if and only if the event is the
  receipt-verification step. Nested validation, event, and receipt audit times cannot
  be later than their owning execution snapshot.
- Failure, cancellation, retry, and reopening use only declared graph edges and
  must clear state evidence that is invalid in the target state.

`SKL-S1-02` discovered one backward-compatible persistence contract extension:
`ValidationResult.subject` also accepts an immutable `ValidationSubject` with a
closed legal subject-kind vocabulary, non-nil identity, positive exact version,
and no artifact digest. Work Product Version validation uses the existing
digest-required `ArtifactBinding` only. Approval and Execution subjects remain
`ArtifactBinding` only, so artifact gates cannot be weakened by the extension.
The closed legal vocabulary is `party`, `party_role`, `matter_event`,
`fact_assertion`, `evidence_item`, `authority`, `claim`, `defense`, and
`deadline`. The persistence boundary deliberately implements only these nine
kinds with exact subject resolvers. `work_product_version` remains the separate
digest-bearing `ArtifactBinding` case, and unsupported legal record kinds fail
closed rather than being accepted without a resolver.

`Element.theory_kind` explicitly distinguishes claim-owned and defense-owned
elements. It defaults to `claim` for backward-compatible construction, while
the persisted discriminator is mandatory and immutable. A Claim and Defense
may reuse the same scoped UUID without making Element ownership ambiguous.

## Effective time

`EffectiveInterval` accepts UTC-aware timestamps with offset zero only. Its start is
inclusive and its end is exclusive. An interval with no bounds is unknown and
therefore contains no instant and overlaps no interval. Reversed and empty bounded
intervals are invalid.

The helper `effective_at()` evaluates party roles, fact assertions, and authorities
using these rules. The observed timestamp remains separate from effective time.

## Legacy provenance

`LegacyAlias` preserves the exact historical identifier kind, identifier, slug,
relative POSIX path, source version, content hash, and observed time. Validation
requires:

- the historical container identifier form `PRB-YYYY-NNN...`
- the historical activity identifier form `INC-NNN...`
- a record kind that matches the identifier form
- a normalized relative path with no traversal or backslashes
- a single safe slug segment
- a lowercase SHA-256 digest
- a UTC observed timestamp

Alias collections remain immutable in domain snapshots. Persistence may attach a
new append-only alias through its authorized provenance boundary, which advances
the owning Matter or Matter Event version and audit time before reconstructing the
new immutable snapshot.

`Matter` accepts historical container aliases only. `MatterEvent` accepts historical
activity aliases only. Duplicate aliases within either aggregate are invalid.

## Integration boundary

The package imports only Python standard-library modules, Pydantic, and its own
relative modules. It contains no FastAPI, SQL, PostgreSQL, Temporal, model, connector,
filesystem, or frontend coupling. Persistence adapters must serialize and restore
the complete immutable snapshots without weakening validation. `model_construct()`
is intentionally unavailable; restoration must use validated parsing.

## Limitations

- This package does not decide authentication, authorization, conflict, privilege,
  ethical-wall, retention, hold, or source-rights policy. Later cards enforce those
  controls before domain use.
- It does not persist records, issue outbox events, or provide optimistic-locking
  storage. The entity `version` is the domain input for that later persistence work.
- It does not read HammerTime, run a migration, or process protected corpus content.
- It does not dispatch connectors. Execution records describe the required evidence
  contract for later simulation-first connectors. A later effect-boundary service
  must reauthorize the acting principal and confirm that the captured approval is
  still current immediately before any external side effect. A snapshot alone is
  not sufficient authorization to act.
- Status and validation describe application handling, not a legal conclusion.

## Rollback

No database, corpus, service, external account, or deployment state changes are made
by `SKL-S1-01`. Code rollback consists of removing the five added domain modules,
restoring `sklegal_domain/__init__.py` to its foundation stub, removing the domain
unit and integration tests plus the synthetic fixture, removing their two entries
from `scripts/run_checks.sh`, and deleting this document and the completion evidence.
The Python lockfile is unchanged because the foundation already pinned Pydantic.
