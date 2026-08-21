# SKLegal legal information barriers

## Status and scope

`SKL-S1-04` implements deterministic party normalization, conflict checks and
human decisions, exact waiver references, conflict holds, ethical walls,
independent protected-access grants, privilege and work-product labels,
classification inheritance, retention eligibility, and legal holds. It binds
those decisions to the signed `SKL-S1-03` CapAuth context and the tenant and
matter isolation established by `SKL-S1-02`.

This implementation decides technical information handling. It does not make a
legal conclusion about privilege, conflicts, waiver validity, retention duties,
or whether a hold should be issued or released. Models and agents cannot create
authority, decide a conflict, waive a conflict, declassify material, or release
a hold.

## Authorization sequence

Every protected data flow follows this order:

1. A reviewed CapAuth boundary returns a nonserializable request-local
   `AuthorizedContext` with safe expiry and distinct principal-chain evidence.
2. Each `ProtectedDataFlow` has one immutable `PolicyBoundaryRequirement`.
   Before policy loading, `PolicyGateway` reconstructs and compares the exact
   capability, target, operation, resource type, audience, tenant, applicable
   matter, material ID, version, SHA256, purpose, model route, and workflow run.
   A management capability cannot satisfy a material read.
3. `CapAuthCurrentStateVerifier` rechecks expiry, the exact trusted-issuer
   policy revision, every principal binding and revision, and exact revocation
   revision and digests. It atomically reserves the CapAuth decision for one
   policy invocation. Reuse, stale evidence, and backend outage deny.
4. The policy backend loads one sanitized, request-local snapshot. It contains
   policy records and opaque identifiers, never the protected payload.
5. The gateway re-reads its trusted clock after the snapshot load and repeats
   the current CapAuth checks. Caller time cannot extend a credential, waiver,
   wall membership, grant, or policy interval.
6. `PolicyEngine` requires current tenant and matter membership, complete
   conflict, wall, and classification state, a current clear or exact waived
   conflict decision, no conflict hold, and every applicable wall grant.
7. The engine computes the most restrictive classification across every source
   and active protective label. Privileged work product and highly restricted
   material require an independent principal and purpose grant.
8. Boundary-specific classification rules run. The initial policy denies
   privileged work product and highly restricted content from model context.
   Confidential and more restrictive content is denied from the external
   OpenAI route until the later model-gateway approval contract exists, and is
   excluded from audit detail.
9. A sanitized policy decision is recorded. Audit failure converts the result
   to a denial.
10. After audit returns, the gateway re-reads its trusted clock and rechecks
    credential expiry plus current issuer, every principal, and revocation
    evidence. Expiry, revocation, suspension, policy change, or outage during a
    delayed audit denies.
11. `PolicyGateway` invokes the payload-producing handler immediately after the
    final current-state check. Only an allowed `PolicyAuthorizedContext`
    reaches it.

`ProtectedDataFlow` applies this contract to retrieval, cache population, model
context, export, and audit detail. Retrieval and cache require exact
`evidence.read` or `corpus.artifact.read`. Model context requires those read
capabilities on the model audience. Export requires the API audience. Audit
detail requires exact `audit.read`. A denial happens before its handler, so a
forbidden payload cannot enter any sink. The allowed context includes a SHA256
cache partition key bound to the full expected grant, principal, material hash
and version, classification, and policy revision. It contains no payload or
capability material.

Long-running workflows must invoke the gateway again before every protected
read or effect. An earlier decision is evidence, not continuing authority.

## Party normalization and conflicts

`normalize_party_name` performs a versioned, conservative exact normalization:

- Unicode compatibility decomposition and removal of combining marks
- case folding
- punctuation and whitespace normalization
- collapse of punctuated company initials
- removal of a narrow final company suffix list

The normalizer does not perform fuzzy matching and does not claim two parties
are legally identical. `ConflictService` compares only same-kind exact
normalized digests and emits a sanitized match when a client and adverse-party
relationship collide across matters in the same tenant. A match recommends a
hold. It never decides a waiver or exposes party names in its result.

A conflict decision is `clear`, `hold`, or `waived`. A waived decision requires
an exact waiver artifact ID, version, content hash, tenant, matter, and bounded
effective interval. PostgreSQL rejects a clear decision over a hold result and
rejects a hold or waived decision over a clear or incomplete check. A conflict
hold requires an exact hold decision.

Profile ownership and legacy ownership hints are migration metadata only.
`PartyCandidate` rejects profile-owner fields, `MaterialPolicyFacts` may retain
one owner hint for provenance, and the engine never reads it for authority.
Neither `matter_memberships` nor `matter_policy_states` has a profile-owner
authorization column.

## Walls, privilege, and classification

Matter membership is necessary and never sufficient. Each active ethical wall
must have complete membership state and an active explicit `allowed` membership
for the principal. An `excluded` membership wins. Missing or incomplete state
denies.

Privilege and work-product labels are technical protective labels. Active
labels raise the effective classification to `privileged_work_product` even if
an underlying record has a lower label. The final classification is the most
restrictive tenant, client, engagement, matter, source, record, field,
attachment, chunk, derived, privilege, or work-product input supplied by the
trusted snapshot. Missing or incomplete classification state is treated as
confidential but denied from ordinary use until classified.

Protected material needs an independent, current grant for the exact principal,
matter, purpose, and protection level. A highly restricted grant may satisfy a
privileged-work-product read, but a privileged grant never satisfies a highly
restricted read.

## Retention and legal holds

Retention produces an eligibility decision only. It never deletes data.
`RetentionGateway` requires exact `matter.manage` on the fixed retention
evaluation target, reserves and rechecks current CapAuth evidence, reloads the
policy snapshot, replaces caller evaluation time with its trusted clock, and
records a sanitized decision. Clock, CapAuth, policy, or audit failure denies.
A record becomes eligible only after its current retention interval has elapsed
and all of these facts are explicit:

- tenant and matter membership are both currently active; the
  `matter.manage` capability is necessary but is not membership proof
- legal-hold state is complete and no current hold applies
- conflict state is complete, its exact current decision is clear or has a
  currently effective exact waiver, and no current conflict hold applies
- no preservation requirement is active
- ownership is resolved
- no export is pending
- a current retention policy exists

An active legal hold pauses eligibility. Release is a separate attributable
record that names the exact hold it supersedes and its releasing principal and
time. Unknown hold state or a policy outage denies. A later deletion workflow
must still use an idempotency key, preserve a non-content tombstone and audit
receipt, and reconcile projections, caches, backups, and connector copies.

## PostgreSQL contract

Migration `0006_legal_information_barriers.sql` adds tenant and matter scoped,
append-only records for:

- normalized party identities and party associations
- conflict checks, matches, waiver references, decisions, and holds
- ethical walls and explicit wall memberships
- independent protected-access grants
- material classification sources, privilege labels, and work-product labels
- retention policies, legal holds, and complete matter policy state

Every table has FORCE RLS, non-nil UUID enforcement, server audit timestamps,
an append-only trigger, and a server-assigned value from one monotonic policy
change sequence. Runtime roles have no direct write grant. Direct runtime reads
return no policy rows. Conflict decisions, retention policies, and matter
policy states have one genesis and one exact successor per head. Their triggers
reject missing predecessors, branching, tied or backwards time, future heads,
stale bindings, and overlapping retention heads. Matter policy state binds the
exact conflict and retention heads and computes its revision server-side from
those IDs and complete state flags. Any later policy row makes that state stale
until an attributable successor state is appended.

The SECURITY DEFINER function
`sklegal_legal.material_policy_snapshot` first validates the exact safe runtime
role, current tenant, current principal, and matter membership. It then returns
one JSON policy snapshot at the current monotonic state cutoff containing only
policy metadata and opaque identifiers. Wrong-principal, wrong-matter,
missing, branched, stale, future, or contradictory head state and unsafe-role
calls deny.

`PostgresPolicyBackend` is driver-neutral. Its executor receives one constant
parameterized statement and a five-value parameter tuple. The adapter validates
the returned JSON through strict `MaterialPolicyFacts`. Missing, malformed,
cross-scope, or unavailable state becomes a generic policy-backend denial.
Backend exceptions and validation payloads are removed at the adapter boundary.
Public policy and retention denials carry no backend exception context or cause,
so clock, current-authorization, policy-store, database-driver, and audit-sink
details cannot enter rendered tracebacks.

## Development adapters and production prerequisites

`InMemoryPolicyBackend` and `InMemoryPolicyAuditSink` are synthetic test and
isolated-development adapters. They are not production defaults. Production
requires:

- a PostgreSQL executor using the provisioned least-privilege runtime identity
- the durable sanitized audit sink implemented by `SKL-S1-05`, composed on the
  same protected service boundary before production use
- a durable multi-worker atomic policy-invocation use backend; the in-memory
  registry is synthetic and cannot enforce one-use across processes
- policy-management API routes that require the matching CapAuth management or
  review capability and attributable human decision evidence
- transactionally current policy state and reauthorization at every later
  retrieval, model, export, connector, workflow, and deletion boundary

No production service, protected corpus, HammerTime Inbox, live CapAuth home,
signing key, connector, external model route, deletion workflow, or external
action is enabled by this card.

## Verification and rollback

Unit tests use synthetic values and cover adverse-party collision, exact waiver
expiry, conflict hold, wall exclusion and missing wall grant, classification
inheritance, protected-access grants, exact boundary and management-capability
denial matrices, one-use context, delayed expiry, principal suspension,
revocation and policy-revision changes, caller-time rejection, post-load time
recheck, post-audit one-second expiry and concurrent revocation, sanitized
exception chains, policy and audit outages, profile-owner non-authority, exact
legal-hold release graphs, retention conflict and active-membership decisions,
and negative handler invocation across all five data-flow boundaries.

The PostgreSQL integration uses the existing disposable, network-isolated,
tmpfs-backed PostgreSQL 17.7 contract. It verifies full migration up, down, and
up, each rollback depth, exact object ownership and runtime grants, sealed
policy rows, decision-to-check consistency, sanitized snapshot output, exact
linear heads, server-computed revisions, missing, stale, branched, tied, future,
overlapping, and contradictory-head denials, double-release denial,
wrong-matter and wrong-principal denial, profile-owner absence, and cleanup.

Code rollback removes the policies package implementation and its callers.
Schema rollback uses migration 0006 down only after an authorized backup and
data disposition decision. A production database containing policy or hold
records must not be rolled back merely to bypass enforcement.
