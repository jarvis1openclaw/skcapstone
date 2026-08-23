# SKLegal CapAuth integration

## Status and scope

`SKL-S1-03` establishes the signed authorization contract and protected
boundary adapters for APIs, tools, models, and connectors. It uses CapAuth for
detached OpenPGP signing and exact signer verification. SKLegal owns the legal
capability vocabulary, current principal binding, trusted issuer authority,
scope checks, delegation, revocation, one-use replay reservation, and sanitized
decision records.

This card did not enable a production service, create a production signing
key, or activate a connector. The durable principal, revocation, and replay
state described below has since landed as migrations 0008 through 0012, so the
integration now carries database schema with the rollback story documented at
the end of this contract. The remaining durable production backends listed
below are deployment prerequisites.

## Security invariants

- A protected invocation requires exactly one signed capability.
- The closed audiences are `sklegal.api`, `sklegal.tool`, `sklegal.model`, and
  `sklegal.connector`.
- The signed target identifies the exact route, tool, model operation, or
  connector operation. A credential cannot cross a boundary or target.
- The grant binds capability, tenant, optional matter, resource identity and
  version, operation, purpose, model route, and workflow run as applicable.
- Credential lifetime is positive and no more than 3,600 seconds. Issuance over
  that ceiling, no expiry, excess future issuance, and stale credentials deny.
- Each credential grants one capability for one invocation. Replay reservation
  is atomic and occurs before allow. Application idempotency is separate.
- Every request re-reads current issuer authority, current status for every
  distinct principal in the complete chain, and revocation state. A cache
  never stores an allow decision.
- A delegated request presents and verifies the complete signed chain from root
  through leaf. Missing, expired, revoked, or broadened ancestry denies.
- Backend absence, stale or malformed policy, and audit failure deny.
- Raw current and ancestor credentials remain request-local and are never
  passed to a handler.

## CapAuth provenance and reuse boundary

CapAuth `0.3.1` is vendored in `vendor/capauth`, copied from upstream commit
`183c04a7c623e8abcf37bd705bf8bca1deb4a364` of
`https://github.com/smilinTux/capauth.git`. The workspace resolves the
dependency from that vendored path through `tool.uv.sources`, so installs and
lock exports never fetch the personal remote. `scripts/check_vendor_capauth.py`
enforces the provenance manifest `vendor/capauth/VENDOR-MANIFEST.json`, which
records the upstream commit, the package version, the inclusion and exclusion
lists, the local build patch, and a SHA256 for every vendored file. The
vulnerability scan in `scripts/run_checks.sh` runs this verification and
refuses any exported requirement that references the personal remote. CapAuth
is GPL-3.0-or-later. SKLegal is GPL-3.0-only, so combined distribution remains
under GPLv3 with source and notice obligations preserved; the vendored copy
carries the upstream `LICENSE` and `README.md` verbatim.

### Updating the vendored copy

1. Fetch the upstream repository and check out the new reviewed commit.
2. Replace the `vendor/capauth` content with the upstream `LICENSE`,
   `MANIFEST.in`, `README.md`, `pyproject.toml`, and `src/` tree.
3. Reapply the local build patch recorded under `local_modifications` in the
   manifest: a static `version` in `pyproject.toml` replacing `setuptools_scm`
   dynamic versioning, because the vendored tree carries no git metadata.
4. Update `PINNED_COMMIT` and `PINNED_VERSION` in
   `scripts/check_vendor_capauth.py`, the version pin in
   `packages/capauth/pyproject.toml`, and the surrogate audit version in
   `scripts/run_checks.sh`.
5. Regenerate the manifest with
   `.tools/bin/uv run --locked python scripts/check_vendor_capauth.py --write-manifest`.
6. Run `./scripts/run_checks.sh all` and record the upstream review evidence
   on the governing SKCapstone card.

SKLegal reuses these narrow CapAuth public surfaces:

- `SignedToken`, `TokenPayload`, and `TokenType` as the signed wire carrier
- `sign_manifest` through `CapAuthManifestSigner`
- `signature_verifies` for exact payload and declared-signer verification

SKLegal does not use `capauth.authz.decide`, `verify_token`,
`verify_audience_token`, the broad CapAuth FastAPI service, CapAuth token
storage, or CapAuth revocation storage. Those surfaces do not enforce the
closed legal scope, full delegation chain, trusted issuer authority, durable
one-use replay, or fail-closed revocation required here. The CapAuth `token_id`
is an internal wire compatibility field and is never the SKLegal credential,
replay, revocation, or audit identity.

No SKLegal issuer key, credential, revocation state, replay state, or audit data
may be placed in a synced CapAuth home. Production signing uses a dedicated
issuer key behind `gpg-agent` or a narrow signing sidecar. Services refer to the
key through a protected handle and never carry a passphrase in source,
arguments, environment, prompts, logs, workflow history, or business rows.

## Credential contract

`CapabilityClaims` is a strict, versioned, extra-forbid model. It contains:

- schema and verifier policy versions
- a signed unique credential nonce
- principal ID, principal kind, authentication subject, and tenant
- one exact `CapabilityGrant`
- delegation depth, maximum depth, and exact parent credential digest
- a literal one-use limit

The parser rejects unknown envelope, payload, metadata, claim, vocabulary, and
constraint fields. It also rejects duplicate JSON object members recursively,
including duplicates nested inside signed metadata. UTC timestamps with offset
zero are mandatory. Audience, target prefix, operation, resource type, purpose,
matter presence, model route, workflow run, and exact approval or dispatch
artifact fields are checked as a closed contract.

A direct credential may be the Bearer value only when its exact top-level shape
is unambiguous. Delegated credentials use this versioned Bearer wire envelope:

```json
{
  "sklegal_presented_capability": "1.0",
  "chain": {
    "leaf": "<request-local signed credential>",
    "ancestors": ["<root>", "<ordered parent>"]
  }
}
```

The envelope is strict, size-bounded, duplicate-free, and limited to the
implementation hard depth. Ancestors are ordered from root to immediate parent.
Missing, repeated, reordered, or excess ancestry denies. The chain travels only
inside the standard `Authorization: Bearer` header. It must not use a custom
chain header, browser storage, workflow history, or persistent token store.

The opaque `credential_digest` is SHA256 over the canonical signed CapAuth
payload bytes, normalized detached signature, full declared issuer
fingerprint, and verifier policy version. It binds every signed constraint.
Only this digest and sanitized decision fields may enter revocation, replay,
audit, exception, or workflow reference data.

## Principal and issuer policy

The four principal kinds are `human`, `agent`, `service`, and `connector`.
Authentication supplies a trusted `PrincipalContext`; request content cannot
assert it. `PrincipalPolicyBackend` then rechecks the current principal record,
kind, subject, tenant binding, and active status for the authenticated caller
and every distinct root, ancestor, and leaf principal. Unknown, unbound,
rebound, kind-changed, tenant-changed, suspended, or unavailable principal state
denies. Sanitized decisions keep only each principal ID and current policy
revision as evidence of the checks.

`TrustedIssuerBackend` returns a versioned current policy. Each issuer entry
binds a full fingerprint to allowed capabilities, audiences, and principal
kinds. A valid signature from a known key still denies if the issuer lacks
authority for the signed grant. A production issuer policy must be separately
protected and must not use a stale fallback. The strict file adapter rejects
missing files, symlinks, malformed JSON, and extra fields.

The exact 26 capabilities remain those approved in
`docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md`. `CAPABILITY_RULES` is the single
closed runtime mapping from each capability to operation, resource type,
allowed legal purpose, and matter requirement.

## Delegation

Delegation attenuates authority and never creates it. Default maximum depth is
zero. The implementation hard ceiling is two. A child must:

- name the exact digest of the presented parent
- increment depth by exactly one without increasing maximum depth
- expire no later than its parent
- retain one-use semantics
- remain in the same audience, target, capability, tenant, operation, resource
  type, purpose, and model route
- keep every parent-constrained matter, resource, version, digest, and workflow
  value exact
- use an allowed child principal kind

Every ancestor is reparsed, time-checked, authority-checked, signature-checked,
current-principal-checked, and revocation-checked. A cycle cannot fit the exact
depth and parent-digest contract. `tenant.admin`, `work_product.approve`, all
dispatch capabilities, and `audit.read` are initially nondelegable. Connector
principals cannot delegate.

Delegating consumes the parent credential as its one invocation before minting
the child. A concurrent second delegation attempt therefore denies as replay.

## Authorization sequence

`CapabilityAuthorizer.authorize` follows this order:

1. Require a request-local `PresentedCapability` and parse the complete chain.
2. Load the current trusted issuer policy and require the exact verifier policy
   version.
3. Load current status for every distinct request and chain principal and
   require exact active bindings.
4. Validate time, issuer authority, lineage, depth, and monotonic attenuation.
5. Load current revocation state for every digest and deny a revoked leaf or
   ancestor.
6. Verify each detached signature. A short positive-only cache may avoid the
   repeated cryptographic calculation.
7. Intersect the leaf with the exact trusted boundary request.
8. Re-read the clock and revalidate the leaf and every ancestor immediately
   before replay reservation.
9. Reserve the leaf digest atomically for one invocation.
10. Re-read the clock again before allow so a slow reservation cannot authorize
    a credential that expired during the operation.
11. Record a sanitized allow decision. Audit failure converts the result to a
    denial.
12. Return only a request-local `AuthorizedContext` to the handler. It carries
    the safe credential expiry and the distinct trusted principal chain needed
    for an immediately adjacent current-state policy recheck.

The signature-cache key includes credential digest, issuer fingerprint,
verifier policy version, issuer policy revision, and revocation revision. Cache
entries expire within 30 seconds and never beyond credential expiry. Current
issuer, principal, and revocation policy is read before every decision, and
allow decisions are never cached.

## Boundary adapters

`ApiCapabilityBoundary`, `ToolCapabilityBoundary`,
`ModelCapabilityBoundary`, and `ConnectorCapabilityBoundary` construct exact
trusted requirements. Their handler contract receives only
`AuthorizedContext`. A missing or rejected credential prevents handler
invocation. The context refuses Pydantic serialization and pickling. It is a
volatile handoff for the same request, not a workflow input, cache value, or
later authorization receipt.

`ProtectedRouteDependency` is the FastAPI adapter. Trusted authentication and
scope resolvers produce the principal and resource scope. A deliberate
authentication denial remains a sanitized 401 response, a deliberate scope or
identity denial remains a sanitized 403 response, and backend or unexpected
resolution failure becomes a sanitized 503 response. Capability denial returns
a generic 403 response with only a decision ID. Raw Bearer material is not
placed on `request.state`, returned, or included in an exception or chained
traceback.

Later route and tool cards must instantiate one of these boundaries for every
protected operation. `SKL-S3-03` will add the closed tool catalog and its
negative completeness test. After authorization, database work must still set
the `SKL-S1-02` runtime tenant and principal context so row-level security is an
independent backstop. CapAuth cannot replace authentication, membership,
conflict, privilege, ethical-wall, classification, source-rights, retention,
hold, or exact-version approval gates.

## Credential handling

Raw credentials may exist only in the volatile incoming transport value and
the request-local `PresentedCapability` chain. The wrapper has redacted string
representations and refuses serialization. Parser, delegation, authorization,
and FastAPI boundaries suppress underlying exception chains that could contain
input bytes. Decisions, exceptions, and audit events contain only safe
identifiers and reason codes.

An `AuthorizationDecision` records the exact safe request scope needed to prove
the result: decision and correlation IDs; principal, tenant, and optional matter
IDs; capability, audience, and target; resource type, ID, version, and SHA256;
operation, purpose, model route, and workflow run; opaque credential and
ancestor digests; safe delegation depth; verifier policy version; and trusted
issuer, per-principal, and revocation revisions. It never contains raw signed
payloads, signatures, armor, or transport envelopes. The handler-visible
`AuthorizedContext` contains only this decision, the trusted principal and
exact grant, credential expiry, and safe distinct principal chain. It refuses
serialization. A downstream policy boundary must still reserve the decision
for one exact invocation and recheck current issuer, principal, revocation, and
expiry evidence immediately before protected state is loaded and again after
sanitized audit, immediately before its payload handler runs. The context is
evidence from the CapAuth boundary, not continuing authority.

Raw credentials are forbidden in:

- prompts, model context, tool arguments, or connector jobs
- logs, telemetry, traces, exceptions, reports, and committed fixtures
- browser local storage, session storage, IndexedDB, or client bundles
- PostgreSQL business rows or audit payloads
- Temporal workflow inputs, history, search attributes, memos, and activity
  payloads

Browser code will use a secure HttpOnly session. A trusted server component
obtains or delegates an invocation credential at the effect boundary and
discards it immediately after authorization.

## Issuer custody and versioned issuer policy

`SKL-S1-03A1` adds the dedicated issuer boundary in
`sklegal_capauth.issuer`:

- `IssuerCustodyPolicy` declares the dedicated application issuer. The
  Casey human identity key and the Jarvis agent identity key are trust
  anchors and are rejected as application issuers by construction, as is
  any rotation lineage that names them. The custody home or sidecar socket
  must be absolute and outside every declared synced CapAuth home.
- `TrustedIssuerPolicyDocument` is the versioned policy contract. Each
  revision binds a fingerprint allowlist to exact capability, audience,
  and principal kind ceilings, names the revision it supersedes, and
  carries an explicit `active` or `revoked` status.
- `IssuerPolicyStore` holds one immutable document per revision. Loads are
  strict: no symlink following, no duplicate JSON members, no stale
  fallback, and a separate revocation tombstone makes a revision fail
  closed even if the original document is later edited. Rotation writes a
  new revision. Rollback selects an earlier active revision explicitly.
- `VersionedTrustedIssuerBackend` adapts the store to the existing
  `TrustedIssuerBackend` contract and re-reads the store on every
  snapshot.
- `GpgAgentSigningHandle` is the protected signing handle. It invokes gpg
  in batch mode against the custody home with no passphrase argument, so
  secret operations stay inside gpg-agent. `SidecarSigningHandle`
  implements the same `IssuerSigningHandle` protocol against a narrow
  sidecar over a private unix socket using the strict
  `sklegal-issuer-sidecar/v1` protocol with exactly two operations,
  readiness and detached signing; only the schema, operation,
  fingerprint, and payload bytes cross the channel. `readiness()` reports
  a sanitized readiness verdict and every failure raises
  `SigningUnavailable` with a static sanitized message.

No production key is created by this boundary. Synthetic temporary keys in
isolated directories are the only test keys.

## Backend contracts and deployment prerequisites

The package intentionally provides interfaces without a production default:

- `TrustedIssuerBackend` for current signed or equivalently protected issuer
  authority and revision
- `PrincipalPolicyBackend` for current identity binding and status
- `RevocationBackend` for current leaf and ancestor revocation state
- `ReplayBackend` for atomic one-use reservation
- `AuditSink` for durable sanitized allow and deny decisions

`InMemoryPrincipalPolicyBackend`, `InMemoryRevocationBackend`,
`InMemoryReplayBackend`, `InMemoryAuditSink`, and
`StaticTrustedIssuerBackend` are synthetic or isolated-development components.
They are not safe across workers or hosts and are not production defaults.

Before any protected production route is enabled, deployment must configure a
durable shared principal adapter, trusted issuer policy, revocation backend,
atomic replay backend, and the S1-05 durable audit sink. PostgreSQL is the
shared state for principal, revocation, replay, and audit adapters.
`PostgresPrincipalPolicyBackend`, `PostgresRevocationBackend`, and
`PostgresReplayBackend` in `sklegal_capauth.postgres` implement those three
contracts over the migration 0008 through 0013 SECURITY DEFINER functions.
Each adapter revalidates the current runtime scope inside the database
function, converts any database error into a fail-closed
`BackendUnavailable`, and never sees raw credential material. The audit sink
implementation is available. The S3-11 production factory composes only the
disabled, loopback-bound SKGateway authorization endpoint and rejects synthetic
or unavailable dependencies before startup. It does not activate protected
traffic. Replay reservation needs
a unique credential-digest insert or equivalent serializable atomic operation.
Migration 0013 makes the durable reservation match the reviewed in-memory
expiry semantics: `reserve_capability` removes an expired row for the same
tenant and credential digest inside the same statement transaction before the
atomic `ON CONFLICT DO NOTHING` insert, so exactly one concurrent worker wins
and expired state never blocks or accumulates. The scoped
`prune_expired_capability_replay_reservations` janitor, exposed as
`PostgresReplayBackend.prune_expired`, deletes every expired reservation for
the calling tenant and returns the pruned count for scheduled cleanup.
Migration 0018 separately reserves each canonical PolicyGateway invocation by
CapAuth decision ID and invocation digest through
`PostgresAuthorizationUseBackend`. This prevents a handler from reusing one
already-authorized request context after the credential-level replay check.
Read errors, unavailable revisions, corrupt state, and write ambiguity must
deny. The package provides explicit unavailable adapters so an incomplete
composition fails closed during development.

## Testing

Focused unit tests use only CapAuth's lexical signing stub with synthetic
in-memory claims. They cover complete-chain current principal checks, recursive
duplicate rejection, Bearer wire compatibility, typed resolver failure,
concurrent replay, decision-time expiry races, cache invalidation, exact safe
audit scope, and traceback redaction. The integration test creates a fresh
temporary `GNUPGHOME`, generates a throwaway synthetic OpenPGP signing key,
authorizes one credential, and rejects a tampered signature. It never reads a
live keyring or CapAuth home. No raw credential is committed as a fixture or
included in test output.

Run the focused checks with:

```bash
.tools/bin/uv run --locked python -m unittest -v \
  tests.test_capauth_authorization \
  tests.test_capauth_delegation \
  tests.test_capauth_boundaries \
  tests.integration.test_capauth_contract
```

## Rollback

The integration now carries durable PostgreSQL state, so rollback has two
halves. The code half is unchanged: disable route, tool, model, or connector
compositions that depend on `sklegal-capauth`, remove the package dependency
and workspace member, then remove the package, API adapter, tests, and
check-runner entries. Never replace the authorizer with an allow fallback. A
missing integration must leave the operation unavailable.

The database half runs the explicit down sections of migrations 0008 through
0018 in reverse order through the digest-pinned runner:

- 0018 drops the policy authorization-use function, its RLS policies, and the
  `policy_authorization_uses` table. This rollback is appropriate only after
  the disabled endpoint is restored to deterministic denial.

- 0013 drops the `capability_replay_controlled_delete` policy and
  `prune_expired_capability_replay_reservations`, then restores the 0008-era
  `reserve_capability` without the expired-row purge.
- 0012 and 0011 revoke schema USAGE from `sklegal_runtime`.
- 0010 drops the authentication subject index, constraint, and column, then
  restores the 0009-era `capability_principal_snapshot` that derives the
  subject from the principal UUID.
- 0009 drops `capability_principal_snapshot`.
- 0008 drops the revocation and replay functions, their RLS policies, and the
  `capability_revocations` and `capability_replay_reservations` tables.

Revocation history and consumed replay reservations live only in the 0008
tables, and principal authentication subject bindings live only in the 0010
column. A down migration destroys that evidence, so any data-bearing
production rollback requires the separate backup and restore decision recorded
in `PERSISTENCE.md`. Reapplying up after a down recreates empty state; it does
not resurrect revoked credential digests or consumed replay reservations.
