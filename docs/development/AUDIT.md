# SKLegal audit, outbox, and telemetry contract

Status: Implemented for `SKL-S1-05`, pending independent acceptance  
Boundary: Content-free evidence and local telemetry only

## Durable event contract

Every event carries one opaque run ID, correlation ID, W3C trace ID and span ID,
tenant and principal, optional matter, one of the six reviewed boundaries, an
action, an outcome, a bounded reason code, and optional exact CapAuth and policy
decision IDs. The closed boundary vocabulary is API, workflow, tool, model,
human, and connector. A complete run can therefore be replayed in event order
without storing the protected payload that crossed any boundary.

`AuditAttributes` is a closed strict model. It permits only reviewed operational
codes, exact resource version and SHA256 metadata, policy revision, status code,
bounded retry count, opaque sanitized resource identity, and exact policy data
flow boundary. Safe codes are lowercase and resource kinds have one shared
100-character limit in Python and PostgreSQL. It has no arbitrary attributes,
prompt, document, message, query, tool argument, model output, credential,
bearer, exception, or baggage field. PostgreSQL applies the same key, type,
pattern, and numeric range rules before accepting an event. Unknown or
malformed metadata is denied.

`DurableAuditSink` maps the safe output of the S1-03 CapAuth boundary and the
S1-04 access and retention policy boundaries into this event contract. It keeps
exact decision references and safe scope metadata, but excludes credential
digests, signed credentials, source content, waiver content, and backend error
payloads. Repository and validation failures become a generic
`AuditUnavailable` with no retained exception cause or context.

## Append-only PostgreSQL chain

Migration 0007 replaces the deliberately unavailable S1-02 writer. Runtime
roles still receive no direct INSERT, UPDATE, or DELETE grant on audit evidence.
They can call only a security-definer append function that rechecks the exact
active runtime tenant, principal, and applicable matter membership under FORCE
RLS. The function rejects caller scope drift, future occurrence time, nil IDs,
unknown boundary values, malformed trace context, timestamps outside AD 1
through 9999, and metadata outside the closed attribute schema. Non-finite, BC,
and later-year occurrence timestamps are rejected before any chain head,
event, outbox, or rollback-sentinel mutation.

The writer locks one tenant chain head, assigns the next exact sequence,
constructs canonical JSON, hashes it with SHA256 together with the predecessor,
and writes the immutable event. Both adapters use one language-neutral byte
contract: recursively render object keys in lexicographic order, preserve array
order, use compact comma and colon separators, encode JSON scalars without
presentation whitespace, and hash the UTF-8 bytes. Optional audit attributes
with null values are removed before canonicalization. Occurrence and recording
timestamps always use UTC, an explicitly numeric four-digit AD year, exactly
six fractional digits, and a terminal `Z`. The shared timestamp domain is AD 1
through 9999. Python does not delegate low-year rendering to platform
`strftime`, and PostgreSQL does not use an era-erasing `YYYY` template.
The Python `recompute_event_sha256` function applies the same rules to a
database-returned event. Canonical payloads and hashes therefore do not depend
on JSON object presentation, adapter language, timestamp precision shorthand,
or database session time zone.

A narrowly controlled verifier sees the complete authorized tenant chain even
when the caller has access to only some matters. It compares every row,
sequence, predecessor, count, tip, and exact chain head, then returns only one
boolean integrity result. The function-local full-tenant RLS path cannot be
enabled by a direct runtime query and does not return event details. Every
grantable audit security-definer function clears caller-supplied integrity mode
before any RLS read. Only the verifier enables full-tenant mode after current
runtime authorization, and it restores the prior mode on both success and
exception paths.
Event changes remain blocked by append-only triggers even for the migration
owner and ordinary database administrator paths exercised by the contract
tests.

Each successful append also writes one content-free rollback sentinel in the
same transaction. The sentinel is owner-readable so the exact NOBYPASSRLS
migration owner can detect evidence before destructive down DDL. Runtime roles
have no privilege on it, and a controlled-writer trigger prevents ordinary
insertion, update, or deletion. The sentinel contains no tenant, matter,
principal, resource, event, count, or payload detail.

The chain provides tamper evidence and ordered replay. It is not an external
timestamp, signature, or independent archive. PostgreSQL superuser and host
root compromise remain deployment threats that require separate controls,
backup evidence, and external anchoring if the production risk decision
requires them.

## Transactional outbox and projections

The append function creates one `audit.local` outbox row in the same database
transaction as the audit event and chain-head update. If any statement in the
caller transaction fails, the event, head, and outbox row roll back together.
Mutation adapters must call the append function on the same connection and in
the same transaction as their protected state change. A later service card must
not acknowledge a mutation before this durable append succeeds.

Delivery uses at-least-once polling plus an immutable receipt keyed by tenant,
outbox ID, and destination. The receipt binds the exact event hash and a
deterministic idempotency key. A repeated delivery acknowledgement returns
false and cannot create a second receipt. The application must still make the
destination effect idempotent because a worker can fail after the effect and
before its receipt transaction commits.

Projection watermarks bind a named tenant projection to one exact existing
event sequence and event hash. Advancement is compare-and-set from the exact
current head, is monotonic, and returns false for an exact replay. Initialization
and later updates serialize on the tenant chain head, so two absent-row
initializers cannot both succeed or overwrite each other. Stale, backward,
different-event, or unknown targets fail closed. Watermarks report replay
position; they do not make a projection authoritative over PostgreSQL evidence.

## Policy-revision reconciliation (SKL-S2-07)

`PolicyRevisionReconciler` propagates policy revisions from the transactional
outbox to every registered derived store: request caches, model-context
bundles, exports, backups, workflow snapshots, and materialized lexical,
vector, and graph retrieval state. Every derived record carries a
`DerivedRecordPin` binding its tenant and optional matter scope, store kind,
opaque partition key, source material ID, version, and SHA256, exact policy
revision, optional rights revision, retrieval projection set and generation
pins where applicable, and the exact core audit event sequence and hash it
was derived from. Pins contain only opaque identifiers and digests, never
protected content or credentials.

The reconciler polls the tenant outbox in event order, records an idempotent
delivery receipt per message, computes the newest policy revision per tenant
and matter scope, and commits the read-gate view before applying denials, so
a partial store failure still fails closed at the gate. It then
synchronously denies every derived record pinned to a different revision or
to watermark evidence ahead of the reconciled head, and only then advances
its compare-and-set projection watermark. Purge is a separate asynchronous
step that can remove only records already denied. Denial and purge are
idempotent; repeated runs report zero new transitions.

`DerivedStore.require_current` rechecks the exact pin, state marker, tenant
and matter scope, policy revision, and watermark position before any use of
a derived record. Unknown, superseded, denied, cross-scope, revision-mismatch,
or rollback-evident records deny synchronously even while they await purge,
and a scope the reconciler has never reconciled has no view and fails closed.
Outbox outage, store outage, watermark conflict, lag, rollback, and
revocation races raise `ReconciliationUnavailable` or deny at the gate
without advancing the watermark.

`ReconciliationReport` carries complete per-store scanned, current, denied,
and purged counts plus the applied watermark. It never carries partition
keys, material identifiers, matter detail, or denied-record content. The
in-memory store and the reconciler instance state are isolated-development
adapters; the durable consumer path must apply its effect and receipt inside
one PostgreSQL transaction per the outbox contract above.

## Connector dispatch ownership (SKL-S4-07)

Decision record: SKCapstone card `ea2c9790`. This section supplements TDD
section 14 and does not alter any approved hash-pinned document. It takes
effect through the card review and approval trail.

External-action state (`draft` through `receipt_verified`) is owned by
PostgreSQL protected records. Every state transition commits in one database
transaction together with its audit event and outbox row through the
migration 0007 append path. Temporal history is never the evidence of record
for a connector transition.

Temporal owns connector dispatch orchestration. A connector workflow sequences
validation, exact-version Approval binding, destination verification,
capability verification, the provider call, receipt capture, and
reconciliation, and it owns human waits, retry policy, timers, and
resumability. Only a Temporal activity may invoke a provider adapter, in
simulation mode by default. No outbox poller, projection builder, or other
consumer may dispatch an external action.

The polled PostgreSQL outbox owns the evidence handoff and derived
projections. Its rows are content-free: they bind the exact audit event hash,
tenant, matter, and destination, and they carry no protected payload,
credential, or dispatch command. Projection and reconciliation consumers poll
named destinations, record immutable receipts through
`record_outbox_delivery`, and advance watermarks through
`advance_projection_watermark`. The outbox never initiates a provider call.

Double dispatch is prevented by one owner plus one dedup record. The dispatch
activity derives its idempotency key the same way `record_outbox_delivery`
does: a deterministic SHA-256 over the tenant, the exact action identity, the
destination, and the approved artifact hash. The activity records or reclaims
that key before invoking the provider. A retried activity that finds the key
returns the recorded receipt instead of dispatching again. Because a worker
can fail after the provider effect and before the receipt commit, the
provider-facing effect must also be idempotent on the same key. If approval,
capability, destination verification, or the audit append is unavailable, the
activity fails closed and nothing dispatches.

## Trace propagation and local retention

`TraceContextPropagator` sends and accepts only a W3C `traceparent` value. Run
and correlation IDs remain explicit SKLegal fields. Baggage is not propagated,
which prevents a convenience carrier from becoming an unreviewed content or
credential channel.

`LocalTelemetryBuffer` accepts only strict `TelemetrySpan` values using the same
closed attributes. It uses trusted ingestion time, enforces positive retention
of at most seven days, bounds capacity at 100,000 spans, and drops expired and
oldest-over-capacity entries. It revalidates each value at record time, and the
shared immutable audit base rejects copy, replacement, and construction paths
that could otherwise bypass validation. Each export is a new deep, revalidated
snapshot, never the stored object identity. Mutation of an exported object
cannot alter later exports, and poisoned internal state fails closed with no
span emitted. It is process local and intended for tests and isolated
development. This card does not configure an OpenTelemetry collector, network
exporter, external telemetry account, or production retention job.

## Usage boundary

The in-memory ledger, outbox, watermark, and telemetry buffer are isolated
development adapters only. They do not coordinate across workers and are not a
production default. The PostgreSQL audit repository accepts one constant
parameterized statement and strictly validates the returned event. A real
database executor must preserve its transaction boundary and must not log SQL
parameters.

Production enablement still requires:

- service composition that records every mutation and every API, workflow,
  tool, model, human, and connector boundary;
- durable outbox polling with worker authorization, bounded retry, health and
  lag reporting, and idempotent destinations;
- an approved local OpenTelemetry collector configuration with producer-side
  filtering retained and external export disabled unless separately approved;
- retention, archival, backup, restore, and any external chain-anchor decisions
  that preserve legal holds and audit evidence; and
- authorized audit-read routes that apply CapAuth, tenant and matter RLS,
  information barriers, and sanitized response shaping.

No production service, collector, connector, model route, account, signing key,
protected corpus, HammerTime Inbox, or external action is enabled by this card.

## Verification and rollback

Unit tests use synthetic values to cover all six boundary correlations, strict
protected-field suppression, CapAuth and policy decision mapping, tamper
detection, duplicate delivery, exact watermark replay, trusted local retention,
parameterized PostgreSQL execution, and sanitized nested failures.

The disposable PostgreSQL contract uses a digest-pinned, network-isolated,
tmpfs-backed PostgreSQL 17.7 container. It verifies migration up, down, and up,
runtime grant readback, RLS isolation, controlled append, canonical hash-chain
verification, strict metadata parity, audit and outbox transaction rollback,
cross-adapter canonical byte and digest parity, timezone-independent
canonicalization, exact AD years 1, 9, 99, 999, 1000, the current year, and
helper year 9999, BC and out-of-domain zero-effect denial, idempotent delivery
receipt, serialized monotonic watermark advancement, integrity-mode laundering
denial, hidden-matter tamper detection, direct tamper denial, and automatic
container cleanup. All fixtures are synthetic.

Migration rollback is intentionally refused once any append has recorded the
content-free sentinel. The down guard remains effective when event and outbox
tables both contain data, when only events remain, and when only outbox data
remains after privileged fault simulation. An empty development database can
migrate down to the earlier fail-closed scaffold. A data-bearing system requires
a separately approved preservation and restore plan; audit evidence must never
be discarded merely to make a migration run.
