# Tenant-native PostgreSQL retrieval partition contract

Card: `f5b93935` (`SKL-S2-10`)
Applies to: `c4ef2a90` (`SKL-S2-04`)
Approval: `docs/approval/AMENDMENT-SKL-S2-10.md`
Status: approved implementation contract after owner approval and independent review

## Decision

Initial protected SKLegal retrieval runs locally on chiap01 in a dedicated
`sklegal-retrieval-pg` PostgreSQL 17 cluster. It contains PostgreSQL full-text
search and pgvector. It may contain Apache AGE only after the optional graph
backend passes its separate qualification gate. It is a derived, rebuildable
projection store and is not an authority for legal operational state.

Canonical Matters, policy state, audit records, workflow references, the
outbox, and the projection registry remain in a separate
`sklegal-core-pg` PostgreSQL cluster. The core and retrieval services have
separate databases, volumes, roles, credentials, resource limits, backup
lifecycles, restart lifecycles, and private network identities.

SKLegal does not use the live `skmem-pg` instance, its application schema,
its volume, its roles, or its lifecycle. The local SKMemory implementation is
useful design evidence, but its database is a rebuildable SKMemory cache and
cannot own canonical or derived SKLegal data.

Existing HammerTime Qdrant and FalkorDB projections remain upstream-owned
compatibility and shadow-comparison sources. SKLegal does not create a new
protected projection estate in them and does not query them directly in a
protected runtime path. The initial local projection is rebuilt from an exact
pinned HammerTime release, not copied from an unpartitioned backend database.

The machine-readable contract is
`config/retrieval/tenant-partition-contract.json`.

## Why two local PostgreSQL clusters

The core database contains canonical legal records and append-only audit
state. Retrieval vectors and graphs are derived, resource-intensive, and
rebuildable. Keeping them in separate clusters prevents an extension crash,
retrieval workload, vacuum cycle, image upgrade, or projection rebuild from
sharing the core database failure domain.

The retrieval cluster is still operationally simpler than running separate
Qdrant and FalkorDB services. PostgreSQL supplies full-text search, pgvector
supplies exact and approximate vector search, and AGE supplies the optional
graph projection. The provider interfaces remain replaceable.

PostgreSQL roles are cluster-wide rather than database-local, so separate
databases in the live `skmem-pg` instance would still share administrative
roles, memory, WAL, upgrades, and restarts. Official PostgreSQL role guidance:

- https://www.postgresql.org/docs/17/database-roles.html

## Reproducible retrieval image

The existing local SKMemory image is not an approved SKLegal artifact. The
retrieval implementation must build and qualify a separate image with:

- an immutable PostgreSQL 17 base-image digest;
- exact pgvector and AGE source revisions and SHA-256 source-artifact digests;
- an AGE revision containing Apache AGE pull request 2475 or an independently
  qualified equivalent fix;
- no mutable branch clone or unverified release-candidate dependency;
- an SBOM, vulnerability scan, license inventory, and extension-version
  readback;
- only approved non-core `vector` and `age` extensions, with no unapproved `pg_search`
  or other extra extension;
- no unresolved critical vulnerability, and explicit time-bounded human risk
  acceptance for any unresolved high finding;
- a documented image upgrade, backup, restore, and rollback procedure; and
- an adversarial proof that a retrieval backend failure does not interrupt
  `sklegal-core-pg`.

The projection registry pins the exact retrieval image digest, PostgreSQL
version and runtime readback, extension source revisions and SHA-256 digests,
extension runtime-version readbacks, and hashes of the SBOM, vulnerability
scan, and license inventory. AGE also requires an explicit qualified status
and qualification-evidence hash. Activation and later query use deny an image,
extension, version, or evidence mismatch.

Extension source checks use SHA-256, and an algorithm or digest mismatch
denies activation. Vulnerability evidence also pins its observation time and
advisory-database revision to the image digest. Any time-bounded human
acceptance for a high finding has a reference and expiry; expiration denies
activation and query use.

Relevant upstream evidence:

- pgvector multitenancy and filtering:
  https://github.com/pgvector/pgvector#multitenancy
- Apache AGE PG17 releases:
  https://github.com/apache/age/releases
- Apache AGE row-security crash fix:
  https://github.com/apache/age/pull/2475
- PostgreSQL row security:
  https://www.postgresql.org/docs/17/ddl-rowsecurity.html

## Projection registry and outbox

`sklegal-core-pg` owns the projection registry. A current registry record
contains at least:

- Tenant ID, scope kind, Matter ID when applicable, and opaque partition ID;
- backend kind and validated physical name;
- release ID, projection schema, embedding model identity, and source snapshot
  digest;
- retrieval image, PostgreSQL, extension, SBOM, vulnerability, license, and
  conditional AGE qualification evidence pins;
- policy and rights revisions used to construct the projection;
- monotonically increasing projection generation;
- building, ready, active, retiring, retired, rejected, or failed lifecycle state;
- backend watermark, replay LSN, reconciliation time, and visible lag; and
- component access-mapping reference, never a raw credential.

Relational physical identifiers match `rp_[0-9a-f]{32}` and graph physical
identifiers match `rg_[0-9a-f]{32}`. Registry records also pin the projection
adapter version, projection-schema version, and projector version. The
projection adapter identifies the code that constructed stored rows. It is
distinct from the retrieval adapter version pinned on each query trace and
cache key. Matter scope requires a non-null Matter ID. Tenant-shared scope
requires a null Matter ID and an independent affirmative policy decision.
Building, ready but not active, rejected, retiring, retired, failed, malformed,
or ambiguous records are never selected as active. The activation key is
Tenant ID, scope kind, and Matter ID or null for Tenant-shared scope. One
active projection set is allowed per exact activation key. Activation uses
compare-and-swap only after
the required lexical and vector components are ready in the same projection
set, release, and generation. Mixed sets, releases, or generations are denied.
Optional graph output must match the selected set when it is used.
The active projection-set manifest is immutable. Adding AGE or another later
optional component requires a new projection set and atomic cutover; it cannot
mutate the active set in place.

A caller, model, source document, query string, frontend, workflow payload, or
legacy path cannot supply or derive a table partition, graph name, database
name, or credential. The authorized adapter resolves one opaque registry
record after policy approval.

The canonical transaction commits first. Its transactional outbox then drives
an idempotent projector. No distributed transaction crosses core PostgreSQL,
retrieval PostgreSQL, HammerTime, a model, or a connector. A projection can be
returned only after its required outbox watermark is visible at the selected
primary or replica.

The retrieval cluster also holds a database-owned authorization binding
projection delivered from the core transactional outbox. Core and retrieval
credentials are distinct. Each retrieval login is separately provisioned for
one Principal, one exact Matter or explicitly Tenant-shared scope, and one
projection generation. Its binding includes the projection set, policy and
rights revisions, authorization event sequence and hash, and revocation state.
The database Principal name is opaque. CapAuth mediates credential resolution
through a broker only after policy approval, and no raw credential enters a
caller, model, prompt, or log. No shared runtime login or caller-set PostgreSQL
variable supplies scope. Before a query, the binding policy and revocation
watermark must equal the current core decision. A missing, stale, revoked,
wrong-Principal mapping, wrong-Matter, wrong-scope, or wrong-generation binding
fails closed. This contract does not claim that PostgreSQL can identify a
person who has obtained a raw password; preventing raw credential disclosure
and broker misuse is part of the boundary.

Connection pools are keyed by database Principal, Tenant, scope kind, Matter
or null, projection set, and generation. An authenticated connection cannot be
reused across any of those Principal, scope, or generation boundaries. Broker
and pool reuse are explicit denial cases.

## PostgreSQL full-text contract

Lexical chunks use built-in PostgreSQL full-text search in physical Tenant and
projection-generation partitions. Every row carries the required projection,
scope, immutable retrieval-record, source version and hash, document, chunk,
span, classification, rights, policy, adapter, projector, and watermark pins.
Matter access uses forced row security and an
exact server predicate. Runtime roles do not own objects, have `NOBYPASSRLS`,
and cannot access child partitions directly. Row security joins
`session_user` to the database-owned exact Principal, scope, projection-set,
and generation binding, so an omitted Matter predicate and a same-Tenant but
unassigned Matter are denied.

The adapter exposes fixed `websearch_to_tsquery` templates with a
registry-pinned text-search configuration. A caller cannot provide SQL, raw
`tsquery`, the text-search configuration, a filter, rank expression, or sort
expression. Search, count, aggregate, and existence operations share the same
mandatory scope builder and wrong-scope whole-response rejection.
Ranking ties are resolved by rank and immutable retrieval-record ID. Query
input is bounded to 16,384 UTF-8 bytes before parsing.

## pgvector contract

The initial vector path uses exact pgvector search. Exact search is the recall
baseline for the frozen evaluation set and avoids premature approximate-index
tuning.

Vector chunks live in `sklegal_retrieval.vector_chunks`, list-partitioned by
Tenant and projection generation. Every physical partition has independent
index statistics. Every row carries the fields in
`required_projection_fields`, plus chunk hash, embedding model identity, and
embedding dimension.

Tenant and generation partitioning is a structural, rollback, and performance
backstop. Matter authorization is also enforced through `FORCE ROW LEVEL
SECURITY`, an exact Matter predicate, and response validation. Runtime roles
are not table owners, have `NOBYPASSRLS`, and cannot query child partitions
directly. The adapter uses fixed security-invoker templates and an exact-scope
retrieval credential reference. The credential is bound to the exact Principal,
scope, projection set, and generation. Callers cannot provide SQL, an ordering
expression, or a raw filter.

The currently approved PostgreSQL identity remains `session_user`. Retrieval
login roles are separately provisioned from core credentials and bind one
Principal plus one exact Matter or Tenant-shared scope and generation. Runtime
roles have `NOSUPERUSER`, `NOBYPASSRLS`, `NOINHERIT`,
`NOCREATEROLE`, `NOCREATEDB`, and `NOREPLICATION`; own no protected objects;
participate in no PostgreSQL role graph; and have no schema `CREATE` grant.
Caller-set PostgreSQL variables are not authorization facts. Any future
authorization-context lease remains subject to the separate pending S3-08
human gate.

Every runtime connection uses a locked `search_path` containing only trusted,
non-writable schemas. Every gateway and template database reference is
schema-qualified. `pg_temp` cannot shadow a referenced relation or function.
`CREATE` on the `public` schema is revoked. Runtime roles cannot execute AGE
graph DDL, bulk-load, mutation, copy, or deletion functions directly.

HNSW may be added only to individual Tenant and generation partitions after
the held-out evaluation approves recall, latency, memory, and cross-partition
leakage thresholds. Each approximate configuration is compared with the exact
per-Matter baseline. A global cross-Tenant approximate index is prohibited.

## Apache AGE contract

AGE is an optional derived traversal projection. AGE row-level security is not
an authorization boundary.

AGE starts unavailable and unqualified. It is activated only after a pinned,
non-release-candidate build containing the required security fix passes ACL,
crash, isolation, extension-version, dump, restore, graph-registration, and
adapter leak qualification. Failure leaves graph retrieval explicitly
unavailable and does not weaken full-text or vector scope.

Each protected Matter and projection generation receives one opaque physical
AGE graph. Explicitly Tenant-shared material receives a separate physical
Tenant-shared graph and requires an affirmative policy decision. Cross-graph
edges are prohibited. A relational graph manifest under forced row security
records every graph, entity, edge, release, source hash, rights revision,
policy revision, projector version, optional model-run identity, watermark,
and generation. Entity and relationship manifests include immutable entity and
relationship identifiers, source versions, relationship endpoint identifiers,
and both endpoint scope digests.

The registry selects the exact graph before execution. A runtime role has no
direct graph-schema access and cannot enumerate the graph catalog. It receives
`EXECUTE` only on the gateway bound to that Principal, Matter or Tenant-shared
scope, projection set, and graph generation. The `SECURITY DEFINER` gateway
accepts no graph name, SQL, raw Cypher, or query-text parameter and rechecks
`session_user` against the current database-owned binding inside the function.
Its owner is a `NOLOGIN`, `NOSUPERUSER`, `NOBYPASSRLS` role with no role graph
and exact read-only ACL only on that graph generation. Access to every other
graph is denied. Its `search_path` is fixed to trusted non-writable schemas;
all referenced relations and functions are schema-qualified; `pg_temp` cannot
shadow them; `PUBLIC` execute is revoked; and its exact function-definition
hash is registry-pinned. Dynamic SQL, DDL, mutation, copy, bulk import, and
arbitrary query text are prohibited. Exceptions are normalized so no
graph-existence oracle is exposed. Runtime and projector roles are separate.

The adapter exposes only frozen parameterized Cypher templates. The gateway
credential for one Principal and graph generation is denied against every
other gateway and graph.

Every returned node, relationship, path, aggregate, and count is validated
against the authorized Tenant, Matter or Tenant-shared scope, release,
generation, and source hashes. Relationship endpoints and every path member
are validated against the forced-row-security relational manifest. One
wrong-scope object, endpoint, aggregate, count, or existence result rejects the entire
response. It is never silently removed while the rest of a mixed response is
returned.

If graph ACL, extension, crash, restore, or query-template qualification is
not healthy, graph retrieval is explicitly unavailable. Authorized lexical
and vector retrieval may continue only when their independent gates pass.

## Authorization and result use

The required sequence is:

1. Authenticate the Principal and validate the CapAuth capability.
2. Evaluate current Tenant, Matter, conflict, privilege, ethical-wall,
   classification, rights, retention, and legal-hold policy.
3. Verify the separate retrieval credential's database-owned Principal,
   exact scope, projection generation, policy, rights, authorization-event,
   and revocation binding against the current core decision.
4. Resolve the current registry record and component access reference.
5. Build a closed query from an approved query-template identifier and typed
   parameters.
6. Verify that the required projection watermark or replay LSN is visible.
7. Execute against the one registry-selected partition.
8. Validate every result scope, release, generation, source hash, and
   watermark before content enters a cache, prompt, model context, export,
   log, or Work Product.
9. Recheck authorization, policy revision, and rights revision before return
   or use.
10. Emit a sanitized retrieval trace and lag observation.

Approved templates are versioned and digest-pinned. The initial set includes
`lexical.search.v1`, `lexical.count.v1`, `vector.exact.v1`, `hybrid.rrf.v1`,
and bounded graph entity, neighbor, path, claim-support, Authority-citation,
source-lineage, count, and existence templates. Parameters are typed and
bounded to at most 100 results, 100 entity identifiers, and graph depth 3.

Unavailable policy, registry ambiguity, missing partition, stale generation,
mixed-scope response, backend outage, credential failure, source mismatch,
revision race, or replica lag fails closed. A permitted query may return an
explicit incomplete state only when the authorized query plan declared that
component optional, but never content from an unverified requested component.
There is no automatic fallback to a cache, `skmem-pg`, Qdrant, or FalkorDB.
Denial and missing-partition responses use the same external shape.

Caches bind Tenant, Matter or explicit Tenant-shared scope, Principal policy
context, policy revision, rights revision, credential-binding event hash,
partition ID, projection generation, release, retrieval adapter version,
projection adapter, projection schema, projector, query-template identifier,
version, and hash,
retrieval mode, canonical query and structured-filter digests, required core
watermark, and Principal policy-context digest. Vector cache keys also bind
embedding model revision, dimension, and distance metric. A stale cache entry
is denied synchronously before asynchronous purge.

## Adapter and qualification tests

S2-04 cannot be accepted with evaluation-time retrieval tests alone. Its fake
backend and disposable-backend suites must prove every entry in
`required_adapter_leak_tests` and `required_qualification_tests` from the
machine-readable contract.

Fixtures use duplicate chunk, node, relationship, source, and Matter
identifiers across two synthetic Tenants. The tests prove denial before a
registry or backend call, filter omission safety, child-partition denial,
graph enumeration denial, immutable query scope, stale-policy denial, mixed
response rejection, stale-rights and credential-revocation denial, gateway
hardening, supply-chain evidence mismatch, replica watermark enforcement,
idempotent rebuild, and atomic registry rollback.

No test uses HammerTime `Inbox/`, a live protected Matter, a production
credential, the live `skmem-pg` instance, or a live Qdrant or FalkorDB service.

## Provenance and legal limits

Every result identifies its Tenant and Matter scope, partition ID, projection
set and generation, projection schema and projector versions, release, source
IDs, source hashes, retrieval adapter version, credential-binding event
sequence and hash, query-template ID, version and hash, rank path, backend
watermark, and projection lag. Lexical traces also identify the
text-search configuration. Vector traces also identify the
embedding model revision, dimension, and distance metric. Replica traces also
identify replay LSN. Physical backend names and raw credentials remain
internal.

Vector similarity and graph adjacency are retrieval signals only. They do not
establish Authority applicability, validate a quotation, resolve a Fact
Assertion tension, approve a Claim or Defense, or authorize a Work Product.
Exact source verification and later legal-assurance gates remain mandatory.

## Migration, rollback, and future scale

The initial projection is rebuilt from a pinned HammerTime release without
modifying HammerTime. Existing Qdrant and FalkorDB projections may be read by
an isolated compatibility verifier for shadow parity only. Counts, hashes,
scope fields, rights state, watermarks, retrieval quality, and leak tests must
pass before the core registry atomically selects the new generation.

Rollback restores the previous registry generation. It never rewrites a
source hash or selects an unpartitioned legacy backend. Retired projection
deletion requires clear retention and legal-hold decisions, backup and restore
evidence, and later human approval. Legacy Qdrant and FalkorDB aliases are
provenance fields only and are never routing eligible.

A physical Tenant and generation partition can contain rows referenced by
multiple Matter and Tenant-shared registry entries. It cannot be deleted until
every referencing scope is retired and separately passes retention,
legal-hold, backup-and-restore-evidence, and human-approval gates.

Physical PostgreSQL streaming replication is a future availability and read
scale option. It is not a backup and does not provide write scaling. A replica
must expose the required replay LSN before it can answer a pinned query.

Qdrant or another vector engine and FalkorDB or another graph engine remain
possible future adapters. They are introduced only after measured pgvector or
AGE latency, recall, memory, traversal, availability, or scale thresholds fail.
They are populated from the same pinned release and outbox, qualified in
shadow mode, and activated through the same registry cutover and rollback
contract.
