# SKL-S1-04 correction review evidence

Card: `8137c2f5`  
Implementer: `codex-policy`  
Date: 2026-08-20  
Status: Independently accepted with zero findings; final qualification complete

The initial inventory with SHA256
`ea44dbffb26bb4863848ad534e84bcf3fb9492cfed474968e76fb6bf2a2fcb3a`
was rejected by independent review. The first corrected inventory with SHA256
`f6176d836afd1fd05009e8f4230df55e815296ec194627adfb175999001da5d0`
was also rejected, with exactly three remaining blockers. This receipt records
the original correction, one narrow test-first pass over those three blockers,
the accepted independent review, and final qualification. The accepted source
inventory has SHA256
`e641f17298c07354d74a20afc6b98895fdb2fd63c3411f124f2384c34c477254`.
Independent final review verified all 29 listed paths before and after review,
reported 29 of 29 stable, and accepted the correction with no findings. Card
`8137c2f5` now reads back as Review, owned by `codex-policy`. It was not marked
Done. No deployment or production action was performed.

## Second review correction

1. **Post-audit freshness:** After sanitized audit returns, `PolicyGateway`
   re-reads its trusted clock and rechecks exact current credential expiry,
   issuer policy, every principal, and revocation evidence immediately before
   `ProtectedDataFlow` invokes its handler. A one-second credential that expires
   during audit and a credential revoked concurrently while audit is blocked
   both deny without invoking the handler.
2. **Retention membership:** `PolicyEngine.decide_retention` and
   `RetentionGateway` require current active tenant and matter membership before
   any hold or retention eligibility evaluation can allow. Unknown membership
   denies as incomplete, inactive membership denies with its scoped reason,
   missing snapshot membership and policy outage deny as unavailable, and an
   active positive case remains eligible. The exact `matter.manage` capability
   is necessary but never substitutes for membership.
3. **Sanitized exception chains:** Public policy and retention denials are
   reconstructed outside dependency exception handlers. Current CapAuth,
   in-memory policy, unavailable policy, and PostgreSQL adapters discard nested
   backend exceptions at their boundary. Synthetic payload sentinels from the
   policy backend, clock, CapAuth freshness backend, audit sink, and nested
   database-driver cause are absent from rendered traceback, direct context,
   and direct cause.

## First correction disposition

1. **Exact CapAuth boundary binding:** Each protected data-flow instance now
   owns a strict `PolicyBoundaryRequirement`. Before policy-state loading, the
   gateway reconstructs and compares the exact capability, target, operation,
   resource type, audience, tenant, applicable matter, material ID, version,
   SHA256, purpose, model route, and workflow run. Retrieval, cache, model
   context, export, and audit detail each have a closed allowlist. A
   `matter.wall.manage`, `matter.manage`, or other management grant cannot
   satisfy a protected material read.
2. **Unambiguous current policy state:** Migration 0006 assigns every policy
   row a server-controlled value from one monotonic sequence. Conflict
   decisions, retention policies, and matter policy states form exact linear
   supersession chains with one genesis and one successor per head. Triggers
   reject missing or stale predecessors, branches, tied or backwards times,
   future heads, contradictory decision evidence, and overlapping retention
   intervals. Matter state binds exact conflict and retention heads and gets a
   server-computed revision. The snapshot selects exactly one leaf and denies
   if any later policy change makes it stale. It has no UUID or timestamp
   `LIMIT 1` tie-break.
3. **Retention uses the exact current conflict decision:** Missing or
   incomplete conflict state denies. `hold` pauses eligibility. `waived`
   requires the exact current waiver reference to be valid at trusted
   evaluation time. `clear` permits evaluation to continue only through the
   other hold, preservation, ownership, export, and retention gates.
4. **Exact legal-hold release graph:** Strict policy facts require unique hold
   IDs. Each release targets exactly one included active hold with the same
   tenant, matter, scope, material, issuing principal, and effective time. A
   missing target, duplicate ID, duplicate release, mismatched release, or
   release before the hold is rejected. The independent database trigger and
   one-release unique index enforce the same relationship.
5. **Trusted retention gateway:** `RetentionGateway` requires an exact fixed
   CapAuth management boundary, reloads current facts, replaces caller time
   with a trusted clock before and after loading, records a sanitized
   eligibility decision, and denies clock, CapAuth, policy, engine, or audit
   failure. It never deletes data. A caller-supplied future `evaluated_at`
   cannot make a record eligible.
6. **Request-local current CapAuth handoff:** `AuthorizedContext` now carries
   safe exact expiry and principal-chain evidence and refuses Pydantic
   serialization and pickling. The downstream current-state verifier checks
   expiry, trusted-issuer policy revision, every current principal binding and
   revision, and the exact revocation revision and leaf or ancestor state. It
   atomically consumes each CapAuth decision for one exact policy invocation,
   then repeats current checks after policy loading. Reuse, delayed expiry,
   principal suspension, direct or revision-changing revocation, issuer-policy
   change, and backend outage deny before payload handling. Raw credentials
   remain excluded downstream.

## Preserved implementation scope

- Conservative, versioned party-name normalization and same-kind exact
  adverse-party collision detection use only sanitized digests in results.
- Clear, hold, or waived conflict decisions remain attributable and bind exact
  check and waiver evidence. Conflict holds bind exact hold decisions.
- Tenant and matter membership, every active ethical wall, explicit allowed or
  excluded wall membership, independent protected-access grants, privilege and
  work-product labels, and most-restrictive classification inheritance remain
  required before use.
- Denied material cannot reach retrieval, cache population, model context,
  export, audit detail, or their payload handlers. Audit records contain only
  sanitized decision metadata.
- Cache partitions bind principal, tenant, matter, material ID, version and
  hash, purpose, boundary, effective classification, audience, target,
  capability, operation, resource type, model route, workflow, and policy
  revision.
- Profile ownership remains optional provenance only. It is not membership,
  authority, a policy-head input, or an engine allow input.
- No production route, service, connector, model route, deletion workflow,
  CapAuth home, signing key, account, or deployment was enabled.

## Corrected acceptance matrix

| Requirement | Evidence |
|---|---|
| Five exact protected boundaries | management-capability negative matrix covers retrieval, cache, model context, export, and audit detail with zero policy loads and zero handler calls |
| Complete grant equality | target, capability, operation, resource type, audience, tenant, applicable matter, resource ID, version, hash, purpose, model route, and workflow are reconstructed from trusted requirements and requests |
| Current one-use CapAuth | use-once success, reuse denial, delayed expiry, post-load expiry, principal suspension, revocation, revision change, issuer-policy change, and outage tests |
| Exact conflict retention | Python missing, incomplete, hold, invalid waiver, expired waiver, and valid waiver tests plus exact PostgreSQL snapshot head tests |
| Linear current heads | PostgreSQL rejects duplicate genesis, stale predecessor, branch, tied or backwards time, future head, overlap, stale state, missing exact binding, and caller-spoofed sequence values |
| Exact legal-hold release | strict Python duplicate and mismatch matrix plus PostgreSQL exact release and double-release denials |
| Trusted retention clock | caller future time is ignored; pre-load and post-load trusted time is used; backend, clock, current-auth, and audit failures deny |
| Post-audit freshness | one-second expiry during audit and concurrent revocation during blocked audit both produce denial before handler invocation |
| Retention membership | unknown, inactive, missing, and unavailable membership state denies despite exact `matter.manage`; active membership has a positive gateway case |
| Exception sanitation | policy backend, clock, current CapAuth, audit, retention, and nested PostgreSQL error payloads are absent from traceback, context, and cause chains |
| Policy outage and sanitized audit | malformed or unavailable policy state and failed audit prevent payload or eligibility use without protected content in the decision |
| Profile ownership | negative tests and schema assertions prove profile ownership grants no membership or authority |

## Verification before accepted review

```text
PASS: second-correction adversarial tests were written before implementation;
      all three groups initially failed on the reviewed gaps
PASS: focused correction and policy-adjacent suite, 49 of 49 tests
PASS: broader Python unit suite, 269 of 269 tests in 5.858 seconds
PASS: frontend unit suite, 1 of 1 test in 127 milliseconds
PASS: broader integration suite, 35 of 35 tests in 76.194 seconds
PASS: PostgreSQL migration up, down, and up in disposable isolated databases
PASS: Ruff formatting across 61 files and full Ruff lint
PASS: mypy across 36 source files
PASS: frontend Prettier, ESLint, TypeScript, and production build
PASS: all 5 approved design hashes unchanged
PASS: uv.lock exact, 105 resolved packages
PASS: migration manifest, 6 migrations
PASS: synthetic fixture safety, 4 fixture files
PASS: secret scan, no findings outside the reviewed baseline
PASS: offline rights and license audit, 10 decisions, 3,857 package records,
      2,889 CycloneDX components, 76 implementation files, and 597,413 token
      windows
PASS: Python and Node CycloneDX generation and evidence schema validation
PASS: Python package and npm vulnerability audits, zero known findings
PASS: no em dash or en dash in the corrected SKL-S1-04 paths
PASS: no temporary PostgreSQL or clean-room tree remains
```

## Final qualification receipts

The first post-acceptance `make check` passed completely. It included 269 of
269 Python unit tests in 6.163 seconds, 1 of 1 frontend test in 120
milliseconds, and 35 of 35 disposable PostgreSQL integration tests in 89.901
seconds. All design hashes, the 105-package lock, provenance, formatting,
lint, typing, frontend build, migration manifest, fixture safety, SBOM, secret
scan, license audit, Python vulnerability audits, and npm vulnerability audit
passed. The vulnerability audits reported zero known findings.

The first `make clean-room-check` attempt copied the Git-eligible source into
an isolated temporary tree, but its unchanged bootstrap reached the built-in
300-second `npm ci` timeout while the OneDrive-backed tree was still actively
writing `node_modules`. `npm ci` received SIGTERM, returned 124 to the nested
check, and the wrapper returned 1. No source gate ran or failed. The temporary
tree was removed automatically, no disposable container remained, and the
accepted source and clean-room harness remained byte-identical.

After exact cleanup readback, one environmental retry was authorized. Only the
existing package cache had been warmed by the first attempt. The unchanged
`make clean-room-check` retry copied 153 allowlisted source files into a new
temporary tree and passed its full nested `make check`. It included 269 of 269
Python unit tests in 16.230 seconds, 1 of 1 frontend test in 151 milliseconds,
and 35 of 35 disposable PostgreSQL integration tests in 93.414 seconds. Static,
migration, fixture, license, secret, SBOM, and vulnerability gates all passed,
with zero known Python or npm vulnerabilities. The retry returned 0 and printed
`clean-room check valid: 153 source file(s)`. Its temporary tree and disposable
database were removed automatically.

The final evidence-state `make check` runs only after this evidence and the
completion inventory are sealed. Its result is recorded on card `8137c2f5`
and in the qualification handoff rather than written back into this
self-referential receipt, so no post-check file edit invalidates the verified
state.

The corrected migration SHA256 is
`430f87482e0793297096a58a5b8339a69848a078d7172e5187cfc6c11d284c27`.
The manifest pins that exact value and the migration checker accepts all six
migrations. The dependency lock SHA256 is
`23b61a02289b6aea781d7d42afc6346e884c2af4f4ef668e8454feda583772da`.
The reviewed secret baseline SHA256 is
`9c11bcbb1c683226ead13eb13726b3876e281906cc130d9a62725bcbe120db06`.

The independently accepted second corrected exact checkpoint is listed in
`docs/evidence/policies/SKL-S1-04-CORRECTION-2-FROZEN-INVENTORY-2026-08-20.sha256`.
Its SHA256 is
`e641f17298c07354d74a20afc6b98895fdb2fd63c3411f124f2384c34c477254`.
The reviewer verified all 29 listed paths before and after review, reported 29
of 29 stable, and accepted the checkpoint with no findings. Both rejected
inventories remain historical evidence and are not acceptance targets. The
completion inventory adds this immutable accepted inventory and the final
evidence bytes without changing any accepted source file.

## Publication disposition

The checkout contains a Git directory, but `main` has no commits, every source
path is untracked, and no remote is configured. Creating a branch or commit
would invent repository history, so no branch, commit, push, or draft pull
request was created. No Git repository was initialized.

## Security and data assurance

- All identities, parties, matters, policy records, capabilities, payload
  sentinels, waiver references, and hold records used in tests are synthetic.
- No HammerTime Inbox or corpus path was inspected, read, or changed.
- No production database, service, account, keyring, credential, prompt,
  document, model, connector, cache, vector store, graph store, or export was
  accessed.
- Disposable PostgreSQL tests were network isolated and tmpfs backed. Their
  temporary containers and data were removed automatically.
- Only the pre-existing healthy `skmem-pg` container remained after checks; it
  was not used or changed.
- Policy snapshots, decisions, cache keys, logs, and evidence contain only
  policy metadata and opaque identifiers, never protected content, party
  names, waiver content, capability material, or model context.

## Production prerequisites and limitations

The policy algorithm and isolated PostgreSQL contract are implemented, but no
production composition is claimed. Production remains blocked on:

1. CapAuth-gated human management APIs for conflict, waiver, wall, protected
   access, classification, retention, and legal-hold changes;
2. a durable multi-worker atomic policy-invocation use backend and current
   issuer, principal, and revocation adapters;
3. the durable attributable audit sink from SKL-S1-05;
4. retrieval, cache, Qdrant, FalkorDB, model, export, connector, workflow,
   backup, and deletion adapters that reauthorize immediately before each
   protected read or effect and reconcile changed policy revisions;
5. qualified party identity resolution beyond conservative normalization, with
   human review for aliases and ambiguous entities;
6. a separately authorized deletion workflow that preserves non-content
   tombstones and reconciles projections, caches, backups, and connector
   copies.

These are prerequisites, never allow fallbacks. Missing composition fails
closed.

## Rollback

Code rollback removes the exact corrected paths in the frozen inventory and
restores the prior workspace lock and check inventory. Schema rollback uses
migration 0006 down only after an authorized backup and data-disposition
decision. A database containing policy or legal-hold records must not be
rolled back to bypass enforcement. Incomplete composition must use unavailable
backends and remain denied.

## Independent review disposition

Independent final review accepted correction-2 inventory
`e641f17298c07354d74a20afc6b98895fdb2fd63c3411f124f2384c34c477254`
after verifying 29 of 29 paths stable. It reported no findings. Any later source
change requires a new scoped review and exact inventory. This completion seal
changes evidence only and preserves every accepted source byte.
