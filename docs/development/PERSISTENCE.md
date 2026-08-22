# SKLegal PostgreSQL persistence contract

Status: Implemented for `SKL-S1-02`, pending independent acceptance  
Database: Dedicated SKLegal PostgreSQL 17 database or cluster  
Boundary: Never the `skmem-pg` application schema

## Storage layout

SKLegal uses six prefixed schemas. Every application object, including schemas,
relations, functions, and types, must be owned by the exact non-superuser,
`NOBYPASSRLS` role `sklegal_migrator`.

| Schema | Responsibility |
|---|---|
| `sklegal_migrations` | Applied file and digest registry |
| `sklegal_identity` | Tenants, principals, database-role bindings, and tenant membership |
| `sklegal_legal` | Clients, engagements, matters, and typed legal records |
| `sklegal_integrations` | Connector and immutable external reference metadata |
| `sklegal_workflow` | Opaque workflow and policy decision references |
| `sklegal_audit` | Append-only audit chain, transactional outbox, delivery receipts, and projection watermarks |

The legal schema covers all 36 entities in the approved `SKL-S1-01` domain
contract plus the `SKL-S3-05A` claim ledger entities (LedgerClaim and
ClaimSupport) and the `SKL-S4-04A` work product drafting entities
(WorkProductTemplate, WorkProductTemplateVersion, and WorkProductUnknown).
`tests/fixtures/persistence/domain-table-parity.json` is the reviewed
entity-to-table and required-column matrix. The reusable, driver-neutral
`sklegal_persistence.mapping` adapter declares scalar, value-object, and
normalized-relation mappings for all 36 public `DomainEntity` types. It
decomposes canonical instances into closed scalar and relation rows, restores
those rows with strict Pydantic validation, and rejects missing joins, unknown
columns, undeclared persistence metadata, over-cardinality, and ambiguous
relation ownership. Each relation declares exact tenant, matter, owner,
discriminator, and scalar-reference bindings, which are enforced during both
decomposition and reconstruction. This includes same-UUID Claim and Defense
owners and nested Execution validation and approval references. Database-only
values are retained separately in the closed `PersistenceMetadata` envelope
rather than silently discarded.

Each `DecomposedEntity` also carries a closed, machine-verifiable
`DecompositionWriteContract`. It assigns every emitted leaf to either canonical
writer input or an explicit database-owned output, names the required write
authority and operation, and declares auxiliary physical rows such as Authority
identity. No canonical input may be normalized away as an implementation
detail.

No migration creates a Problem or Incident as a canonical legal type. Legacy
provenance is append-only and typed: `PRB-*` identifiers bind only `problem` to
Matter, and `INC-*` identifiers bind only `incident` to Matter Event. Each
alias also requires a source version, observation time, and import batch. New
application behavior uses Matter and Matter Event terminology.

## Database authorization boundary

Tenant context is never accepted from a caller-set PostgreSQL variable. Each
runtime login is bound to exactly one active tenant principal. Authorization
functions derive the original login from `session_user`, validate a safe role
profile, require an active Tenant and active Principal, require active tenant
membership, and require active matter membership for matter records. A
database-owned synchronization trigger copies only the two current identity
status decisions into the self-visible binding row, avoiding recursive RLS
lookups. Proposed and suspended tenants plus suspended principals are
unauthorized. Suspended tenants and principals may explicitly return to active.
Closed tenants and revoked principals are terminal and cannot be reactivated.

Every application table has row-level security enabled and forced. An
unfiltered query therefore returns only authorized rows. Composite scoped keys
and foreign keys use tenant and matter ownership so a UUID can be reused in a
different scope without becoming a global existence oracle. Cross-tenant and
same-tenant unassigned-matter reads, inserts, and updates are denied even when
the SQL omits API filters.

Runtime roles must be LOGIN, NOSUPERUSER, NOBYPASSRLS, NOINHERIT,
NOCREATEROLE, NOCREATEDB, and NOREPLICATION. They cannot own an SKLegal object
or participate in a PostgreSQL role graph. Provisioning first revokes all
existing SKLegal schema, table, and function privileges, then installs and
reads back an exact allowlist. Runtime roles receive no schema CREATE, DELETE,
TRUNCATE, REFERENCES, TRIGGER, direct audit write, direct execution event or
receipt write, or direct controlled-state update privileges.

The migration owner remains subject to FORCE RLS. Narrow security-definer
transition functions run as `sklegal_migrator`, but authorize the original
`session_user` and its tenant and matter memberships. Direct table mutation is
still denied. The migration runner rejects any other owner name or an unsafe
migration role before bootstrap. The only accepted profile is exactly LOGIN,
NOINHERIT, NOSUPERUSER, NOBYPASSRLS, NOCREATEROLE, NOCREATEDB,
NOREPLICATION, with no `pg_auth_members` edge in either direction. The runner
rechecks that profile and all SKLegal object owners after every successful
operation.

CapAuth capability, delegation, and revocation enforcement is added above this
boundary by `SKL-S1-03`. Conflict, privilege, wall, retention, and hold policies
are added by `SKL-S1-04`. Database RLS remains the final tenant and matter
isolation backstop.

## Integrity and concurrency

Mutable records carry a positive optimistic `version`. Updates must provide the
next exact version and preserve scoped identity and creation time. The database
sets `updated_at` with `clock_timestamp()` and never permits it to move
backward, including in a long-running transaction. Repository updates should
also include `WHERE ... version = :expected` and require one changed row.

Scoped uniqueness and foreign keys, exact status enums, domain state checks,
immutable source references, append-only relationship evidence, and atomic SQL
transactions supply the other integrity backstops. In particular:

- tension membership is append-only and each tension must have at least two
  assertions at commit;
- authority identity is stable while authority versions are append-only;
  advisory locking assigns an exact next version, and database-generated
  assertion time produces `authority_current` and `authority_history` views;
- Work Product Version content, source artifact, and artifact digest are
  immutable through the adjacent `draft` to `frozen` to `superseded` path;
- an Approval moving from pending to approved atomically captures a complete,
  append-only approved-decision row in `approval_history`, keyed by its scoped
  approval ID, immutable approval version, and exact artifact triple. Runtime
  roles cannot insert, update, or delete this history. Execution binds both the
  approval ID and approval version and reconstructs its nested Approval from
  this durable snapshot, so a completed historical Execution remains
  strict-model-valid after the mutable current Approval is later revoked;
- Work Products can be edited while draft through an optimistic controlled
  writer. A validated or approved Work Product can reset only to `in_review`
  while atomically replacing its current version binding and clearing stale
  validation and approval gates. Validated and approved edges lock and recheck
  the exact current Work Product Version as frozen in the same transaction;
- Communications are created and edited only through optimistic controlled
  writers that atomically install a nonempty exact participant set. Runtime
  roles have no direct INSERT privilege on either the aggregate or participant
  relation. Validated or approved Communications can reset only to draft with a
  complete payload and participant replacement, clearing stale gate,
  destination, and execution evidence atomically. Queued and terminal records
  cannot be revised or reset;
- every artifact lifecycle operation locks the parent Work Product, then the
  exact Work Product Version, then the current Approval and its immutable
  approved snapshot when an approval is consumed, and finally the consumer.
  A version cannot be superseded while an exact-bound live Execution or
  Communication remains. This includes draft, validated, approved, queued,
  dispatched, and retryable failed consumers. Approval revocation is
  prospective: an approved Work Product or a live exact-bound Execution or
  Communication blocks revocation until it is reset or terminal. New
  progression and retry require the mutable current Approval to remain
  approved, while an already dispatched Execution may always finish as
  receipt-verified or failed from its captured immutable snapshot;
- accepted Claim and Defense cardinality derives the owner kind from the
  aggregate table and requires an Element with the matching immutable
  `theory_kind`; a same-scoped UUID in both tables cannot cross-satisfy either
  aggregate;
- validations bind an exact typed tenant and matter subject identity and version.
  The closed legal subject vocabulary is `party`, `party_role`, `matter_event`,
  `fact_assertion`, `evidence_item`, `authority`, `claim`, `defense`, and
  `deadline`; Work Product Version uses a separate digest-bearing artifact
  binding. Validation gates require a
  passed result with at least one check, while approvals remain bound to one exact
  frozen artifact ID, version, and digest; and
- executions begin as draft and can move only across declared adjacent edges.
  The controlled transition replays the immutable event prefix, binds exact
  gate evidence, and atomically writes matching receipt and event evidence for
  receipt verification. Connector-supplied `received_at` and `verified_at`
  remain canonical evidence inputs and are persisted exactly after the observed
  dispatch boundary; the database does not replace them with its clock. Direct
  terminal insert, skipped transitions, changed gates, wrong destinations or
  digests, missing events, and direct evidence writes fail closed.

## Encrypted-field hooks

Principal profiles, integration configuration, and Work Product Version payloads
carry ciphertext plus key reference, algorithm, and encryption time. Communication
does not yet persist a ciphertext tuple; it stores only governed metadata and exact
artifact, destination, approval, and execution bindings. The encryption hook is
total and fail-closed across all 16 nullability combinations: either all four
fields are absent or all four are valid. It
rejects partial metadata, short ciphertext, unknown algorithms, malformed key
references, and SQL NULL results from validation.

These hooks do not perform encryption and never store key material. A later
secret-store integration supplies ciphertext and opaque key references. Disk,
backup, and transport encryption remain deployment responsibilities.

## Audit boundary

`sklegal_audit.events` has FORCE RLS and is append-only. S1-02 created a
deliberately unavailable scaffold. S1-05 replaces it with a narrow attributable
writer that validates current runtime scope, assigns a server sequence and
timestamp, hashes canonical event metadata with its predecessor, and creates an
outbox row in the same transaction. Runtime roles retain no direct write grant.
Delivery receipts are append-only and projection watermarks move only through
exact serialized compare-and-set functions. A content-free rollback sentinel is
the sole SKLegal application table intentionally outside RLS. It has no runtime
privileges and lets the NOBYPASSRLS migration owner refuse destructive down DDL
after any successful append. Complete-tenant verification uses a controlled
function-local RLS path and returns only a boolean integrity result. Every
grantable audit definer neutralizes caller-supplied integrity mode before RLS
reads. Audit digests use the same recursively sorted, compact UTF-8 canonical
JSON bytes and fixed six-digit UTC timestamps in PostgreSQL and Python. Both
adapters render the year numerically with exactly four digits and accept only
AD 1 through 9999. The controlled writer rejects non-finite, BC, and later-year
timestamps before any audit state change. See `AUDIT.md` for the complete
contract and its production limitations.

## Migration, provisioning, and rollback

Sixteen digest-pinned migrations create the foundation, identity, legal
records, integration and workflow references, audit target, RLS policies,
legal information-barrier records, the CapAuth state, snapshot, and grant
surface, the work product drafting surface, the claim ledger, and the
isolated pilot import staging surface. Each file
contains explicit up and down sections.
Migrations 0001 through 0004 create the six prefixed schemas, shared domains
and helper functions (0001), tenants, principals, database-role bindings, and
tenant membership (0002), the legal record tables (0003), and the
integration, workflow, and audit reference tables (0004).
Migration 0005 has an explicit down index inventory rather than broad catalog
discovery. Migration 0006 adds sealed policy records and the sanitized policy
snapshot function described in `POLICIES.md`.
Migration 0007 installs the append-only audit chain, transactional outbox,
delivery receipts, and exact projection watermarks described in `AUDIT.md`.
Migrations 0008 through 0013 install the durable CapAuth surface described in
`CAPAUTH.md` and grant it to the shared `sklegal_runtime` role:

- 0008 creates the tenant-scoped, forced-RLS `capability_revocations` and
  `capability_replay_reservations` tables in `sklegal_identity` with
  migrator-only controlled write policies and non-nil domain identifier
  triggers. It installs the SECURITY DEFINER `revoke_capability`,
  `reserve_capability`, and `capability_revocation_snapshot` functions,
  revokes PUBLIC access, and grants EXECUTE to `sklegal_runtime`.
  `reserve_capability` performs the atomic one-use insert with
  `ON CONFLICT DO NOTHING` and returns whether the caller won the reservation.
- 0009 installs the STABLE SECURITY DEFINER `capability_principal_snapshot`,
  which revalidates runtime role safety, exact tenant and principal binding,
  and tenant membership, then returns a revision-hashed sanitized principal
  snapshot. EXECUTE is granted to `sklegal_runtime`.
- 0010 adds the nullable `principals.authentication_subject` column with a
  shape check and a partial tenant-scoped index, then replaces
  `capability_principal_snapshot` so the snapshot requires the authentication
  subject and returns it instead of the principal UUID.
- 0011 grants USAGE on schema `sklegal_identity` to `sklegal_runtime`.
- 0012 grants USAGE on schema `sklegal_legal` to `sklegal_runtime` so the
  runtime role can resolve the `sha256_digest` domain used by the CapAuth
  function signatures.
- 0013 replaces `reserve_capability` so the atomic reservation first removes
  an expired row for the same tenant and credential digest inside the same
  statement transaction, keeping exactly one winner under concurrent workers
  while bounding dead reservation state. It also installs the scoped SECURITY
  DEFINER `prune_expired_capability_replay_reservations` janitor, which
  deletes every expired reservation for the calling tenant and returns the
  pruned count. PUBLIC access is revoked and EXECUTE is granted to
  `sklegal_runtime`. A `capability_replay_controlled_delete` RLS policy
  mirrors the existing controlled write posture: deletes are possible only
  inside migrator-owned SECURITY DEFINER functions invoked by a different
  session user, never by direct data access.
- 0014 installs the work product drafting surface: tenant-scoped
  `work_product_templates` with hash-pinned, immutable
  `work_product_template_versions` (draft to frozen to archived), and the
  matter-scoped `work_product_unknowns` blocker table. Each unknown binds one
  exact Work Product Version triple and one bracketed placeholder key, is
  inserted open, and resolves only through the SECURITY DEFINER
  `resolve_work_product_unknown`, which records the bound principal and a
  non-future resolution time. The migration replaces
  `transition_work_product_version` so a version cannot freeze while any of
  its exact unknowns remain open. Templates activate only against a frozen
  exact current version, advance strictly forward through
  `advance_work_product_template`, and a current version cannot be archived.
  All three tables carry forced RLS tenant or matter boundary policies, and
  updates flow only through migrator-owned controlled writers.

Migration 0015 installs the claim ledger: versioned `ledger_claims` rows with
an immutable policy revision, append-only `ledger_claim_support` records that
link support and counter-support to exact source spans, and the
`ledger_claim_identities` anchor. A deferred constraint trigger rejects any
claim write that lacks at least one supporting source span, all three tables
are forced-RLS with matter-boundary select and insert policies, and the
`ledger_claim_history` and `ledger_claim_current` security-invoker views
expose the revision history.

Migration 0016 installs the isolated pilot import staging surface in the
`sklegal_migrations` schema for the Liberty Auto pilot (SKL-S5-01B).
`pilot_import_batches` records one human-approved import batch per source
snapshot with the reviewer, decision time, and review artifact reference, and
carries an `imported` to `withdrawn` lifecycle. `pilot_import_records` keys
every imported Matter or Matter Event proposal by its deterministic
idempotency key with a monotonic revision per target, so reruns insert
nothing and changed source content lands as a new revision.
`pilot_import_source_files`, `pilot_import_facts`, and
`pilot_import_tension_groups` pin the batch source hashes, atomic Fact
Assertion proposals, and unresolved Tension Group records.
`pilot_import_states` stores only negative states: a database CHECK allows
solely `pending_review` approvals and `not_started` executions, so no import
can advance approval or execution state. `pilot_import_withdrawn_targets`
pins withdrawn target UUIDs so a later import can never reuse them. Physical
deletion is possible only by direct data access against a withdrawn batch in
a disposable development database, matching the pilot rollback contract.

The down sections run in reverse order. The 0016 down drops the pilot import
staging tables in reverse dependency order. The 0015 down drops the claim
ledger
views, tables, and functions in reverse dependency order. The 0014 down
restores the 0003-era `transition_work_product_version` without the unknown
blocker, then drops the drafting functions and tables. The 0013 down
drops the prune function and restores the 0008-era `reserve_capability`, the
0010 down restores the 0009-era snapshot function after dropping the subject
column, and the 0008 down drops the three functions, their policies, and both
CapAuth state tables.

Three PostgreSQL role classes exist:

- `sklegal_migrator` owns application migrations and every application object.
  It is LOGIN, NOSUPERUSER, NOBYPASSRLS, NOINHERIT, NOCREATEROLE, NOCREATEDB,
  and NOREPLICATION, with no role-graph edges.
- Per-principal runtime logins are bound one-to-one to an active tenant
  principal by `provision_postgres_principal.py` and carry the RLS-bound
  grant profile.
- `sklegal_runtime` is the shared grantee for the CapAuth SECURITY DEFINER
  functions. It is provisioned by `scripts/provision_postgres_runtime.py`
  before migration, because the migrator role cannot create roles. It holds
  the same least-privilege attribute profile, owns no objects, and
  participates in no role graph. The proposed Sprint 3 scaling decision is
  recorded in `docs/architecture/POSTGRES-PRINCIPAL-SCALING.md` and its
  pending approval record. Until that amendment is approved and implemented,
  the per-principal `session_user` binding above remains authoritative.

Direct connections use libpq environment configuration, `PGSERVICE`, or a
`.pgpass` file. A database URL is never accepted on the command line, keeping
embedded credentials out of the process argument list.

```bash
make migration-check
python scripts/manage_migrations.py status --direct \
  --database sklegal --user sklegal_migrator
python scripts/provision_postgres_runtime.py --direct \
  --database sklegal
python scripts/provision_postgres_principal.py --direct \
  --database sklegal --runtime-role sklegal_app_tenant_one \
  --tenant-id 10000000-0000-4000-8000-000000000001 \
  --principal-id 20000000-0000-4000-8000-000000000001
```

The runner requires applied files to be an exact digest-matched manifest
prefix. It uses an advisory lock and one transaction for each requested up or
down batch. Qualification verifies full up/down/up, ordinary DML rollback, and
an intentionally failing synthetic migration whose DDL and manifest insertion
both roll back. Data-bearing production rollback still requires a separate
backup and restore decision.

## Qualification boundary

`tests.integration.test_persistence_contract` uses only synthetic identifiers
and content. It starts the digest-pinned PostgreSQL 17.7 image without a
published port or network, stores its data in tmpfs, creates no named volume,
and removes the container automatically.

The suite verifies migration owner preflight and ownership, up/down/up,
migration failure rollback, exact runtime grants, forced RLS, unfiltered tenant
and matter isolation, same-tenant unassigned and cross-tenant mutation denial,
role-bypass resistance, scoped UUID reuse, 31-entity table parity, source and
legacy provenance, encryption completeness, append-only and bitemporal
behavior, state validity, optimistic concurrency, monotonic update time,
controlled artifact and execution transitions, live identity status and
terminal identity semantics, the complete Matter lifecycle including a
closed-never-opened matter, Work Product and Communication revision and reset,
same-ID Claim and Defense theory isolation, controlled Communication creation
and participant replacement, both gate-first and supersede-first artifact race
orders for Work Product, Execution, and Communication, both approval-first and
revocation-first consumer races, prospective revocation, durable approval
snapshot reconstruction after revocation, exact connector receipt timestamps,
terminal Execution completion, audit chain and outbox rollback, duplicate
delivery, exact projection watermarks, protected audit-field suppression,
schema isolation,
ordinary rollback, and service cleanup.

Bidirectional qualification has two independent halves. The reverse path reads
all 31 normalized records, strictly reconstructs them, and compares canonical
snapshots. The write-first path starts with 31 canonical instances, calls the
production decomposer before any corresponding insert, writes in dependency
order, reads, strictly reconstructs, and compares canonical state plus retained
persistence metadata. Tenant, Client, Engagement, and Matter use the explicit
administrative bootstrap adapter. Allowlisted aggregates use ordinary runtime
RLS writes. Communication, Work Product Version, Approval, Execution, Execution
Event, and Execution Receipt use their narrow controlled writers. Authority
uses its declared auxiliary identity write before the append-only version row.
Database-generated event identity, sequence, timestamps, and optimistic metadata
are normalized only where the writer contract declares them. Approval capture
returns the database-owned `approval_history.captured_at` value, while every
domain-defined approval field remains an exact writer input. The write-first
receipt phase observes dispatch, constructs and decomposes the canonical
connector receipt, sends its exact `received_at` and `verified_at` values to the
controlled transition, and compares the identical values on readback. The test
adapter consumes production `DecomposedEntity` output and does not expand direct
runtime privileges.

Provisioning readback and every database authorization decision recheck the full
role profile and all membership-graph edges. Operators must also run the same
drift check at application startup and before accepting traffic. PostgreSQL RLS
cannot contain a login after an administrator grants that login `BYPASSRLS`; the
database therefore detects and rejects the profile, while administrative role
governance remains a deployment trust boundary.

The suite does not connect to `skmem-pg`, persistent development services, the
HammerTime corpus or Inbox, external accounts, or protected matter data.
