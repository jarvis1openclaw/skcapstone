# SKL-S1-03 independent review evidence

Card: `0ef8eb48`  
Implementer: `codex-capauth`  
Date: 2026-08-20  
Status: Independently accepted with zero findings; final qualification complete

The initial dual review identified seven issues. This evidence describes the
single merged correction pass over those findings, the fresh independent
acceptance receipts, and final qualification.

## Scope delivered

- Added `sklegal-capauth`, a strict adapter over the pinned CapAuth detached
  OpenPGP signing and exact signer-verification primitives.
- Pinned CapAuth `0.3.1` to exact upstream commit
  `183c04a7c623e8abcf37bd705bf8bca1deb4a364` in the workspace lock.
- Defined the four approved principals, four closed audiences, 26 approved
  capabilities, exact boundary targets, tenant and matter constraints, resource
  identity and version, operation, legal purpose, model route, and workflow run.
- Added strict extra-forbid wire and claim parsing, signed unique nonces,
  one-capability and one-use semantics, UTC validation, and a maximum one-hour
  lifetime.
- Added a credential digest that binds canonical signed payload bytes,
  normalized detached signature, full issuer fingerprint, and verifier policy.
  CapAuth `token_id` is not used as a SKLegal authorization identity.
- Added versioned trusted issuer authority with capability, audience, and
  principal-kind ceilings, plus current status enforcement for every distinct
  root, ancestor, leaf, and authenticated request principal.
- Added complete-chain bounded delegation with exact parent digests, monotonic
  attenuation, maximum depth two, ancestor expiry and revocation checks, and an
  initial nondelegable capability set.
- Added explicit fail-closed principal, trusted issuer, revocation, atomic
  replay, and audit backend interfaces. All in-memory implementations are
  marked synthetic or local-development only.
- Added a short positive-only cryptographic signature cache. No allow decision
  is cached. Current issuer policy, principal state, and revocation state are
  evaluated on every invocation.
- Added reusable API, tool, model, and connector boundaries plus a FastAPI route
  dependency. Handlers receive only sanitized `AuthorizedContext`.
- Added a strict, versioned, size-bounded Bearer wire envelope for a leaf and
  ordered ancestry. Direct credentials remain supported when unambiguous. The
  wire parser rejects recursive duplicate members, repeated credentials,
  missing or reordered links, excess depth, and extra fields.
- Split deliberate identity resolution denial from backend unavailability with
  sanitized 401, 403, and 503 behavior.
- Expanded safe decisions and audit records to carry the complete exact request
  scope, safe delegation depth and ancestor digests, and every checked policy
  revision without signed payload, signature, armor, or token material.
- Added clock revalidation after signature and policy work and again after
  replay reservation. Parser, delegator, authorizer, and HTTP boundaries now
  suppress unsafe exception chains.
- Added focused unit tests and one isolated real-OpenPGP integration test. The
  integration test uses a fresh temporary `GNUPGHOME` and a throwaway synthetic
  key only.
- Added the development, credential-handling, deployment prerequisite, test,
  and rollback contract in `docs/development/CAPAUTH.md`.

No migration was added. `SKL-S1-02` row-level security and current identity
state remain independent backstops. No production route, service, connector,
account, key, or deployment was enabled.

## Acceptance matrix

| Requirement | Evidence |
|---|---|
| Human, agent, service, connector | strict enum, issuer ceilings, current principal backend, positive and suspended or unbound tests for every distinct chain principal |
| Signed credentials | CapAuth manifest signer, exact `signature_verifies`, unsigned and invalid-signature denials, real OpenPGP test |
| Protected APIs, tools, models, connectors | four audience-specific boundary classes, direct and delegated FastAPI Bearer support, missing-credential handler suppression |
| Exact legal scope | capability-rule map plus tenant, matter, resource, operation, purpose, route, workflow, and target mismatch tests |
| Short TTL | mandatory aware UTC timestamps, 3,600-second ceiling, expiry, no-expiry, future issuance, over-TTL, slow signature, delegated ancestor, and slow replay reservation tests |
| Bounded delegation | complete chain, exact parent digest, maximum depth two, attenuation, nondelegable set, parent one-use, ancestor revocation and expiry tests |
| Verification cache | positive signature fact only, revision-bound key, later issuer, principal, revocation, and backend denials tested after cache population |
| Revocation | leaf and ancestor digest checks against a current versioned snapshot |
| Replay | atomic reservation before allow, eight-thread race with exactly one allow and seven replay denials |
| Safe audit IDs | opaque credential and ancestor digests, decision and correlation UUIDs, exact safe request scope, safe delegation depth, every principal policy revision, issuer and revocation revisions, closed reason code |
| Raw credential exclusion | request-local nonserializable wrapper, sanitized exception and traceback, repr, handler context and exact audit assertions for leaf and ancestor armor and wire material |
| Failure mode | missing, malformed, recursive duplicate, extra-field, unsigned, invalid, untrusted, unavailable, expired, future, revoked, wrong scope, over-delegated, reordered, and replayed all deny |

## Verification before review freeze

```text
PASS: all 5 approved design hashes unchanged
PASS: uv.lock exact, 105 resolved packages
PASS: Ruff formatting across scripts, tests, services, and packages
PASS: Ruff lint across scripts, tests, services, and packages
PASS: mypy across 31 source files
PASS: frontend Prettier and ESLint
PASS: frontend TypeScript check and production build
PASS: focused CapAuth suite, 30 of 30 tests
PASS: broader Python unit suite, 241 of 241 tests
PASS: frontend unit suite, 1 of 1 test
PASS: broader integration suite, 34 of 34 tests
PASS: real OpenPGP signing and tamper denial in isolated temporary keyring
PASS: migration manifest, 5 migrations
PASS: synthetic fixture safety, 4 fixture files
PASS: secret scan, no findings outside the reviewed baseline
PASS: offline rights and license audit, 10 decisions, 3,857 package records,
      2,889 CycloneDX components, 67 implementation files, and 503,285 token
      windows
PASS: CycloneDX 1.6 evidence schema validation
PASS: no em dash or en dash in the SKL-S1-03 corrected paths
```

## Independent acceptance receipts

Both independent reviewers accepted the exact 20-path corrected inventory at
`docs/evidence/capauth/SKL-S1-03-CORRECTION-FROZEN-INVENTORY-2026-08-20.sha256`.
Its SHA256 is
`4ef9f7645a334e2dec81e164a76fb918f4c586f5c078ff18d3e5c0fc413396df`.
All 20 paths were byte-stable before and after both reviews.

The domain, API, and invariant review was performed by reviewer Lovelace under
task `/root/s1_03_invariant_review`. Its message receipt, with no persisted
reviewer artifact, is:

```text
Disposition: ACCEPT
Findings: zero
Focused CapAuth tests: 30 of 30 passed
Independent invariant probes: 55 of 55 passed
Independent resolver probes: 21 of 21 passed
Inventory before and after: 4ef9f7645a334e2dec81e164a76fb918f4c586f5c078ff18d3e5c0fc413396df
Paths stable: 20 of 20
Receipt form: message receipt, no persisted reviewer artifact
```

The contract and security review was performed by reviewer Einstein under task
`/root/s1_03_contract_review`. Its message receipt is:

```text
Disposition: ACCEPT
Findings: zero
Focused CapAuth tests: 30 of 30 passed in 0.151 seconds
Broad Python tests: 241 of 241 passed in 16.183 seconds
Integration tests: 34 of 34 passed in 95.046 seconds
Dependency lock: 105 packages
CapAuth: 0.3.1 at exact commit 183c04a7c623e8abcf37bd705bf8bca1deb4a364
CapAuth license: GPL-3.0-or-later
Approved design hashes: 5 of 5 passed
Inventory before and after: 4ef9f7645a334e2dec81e164a76fb918f4c586f5c078ff18d3e5c0fc413396df
Paths stable: 20 of 20
Receipt form: message receipt, no persisted reviewer artifact
```

Einstein also used the prompt-only sealed scan directory
`/tmp/codex-security-scans/sklegal/skl-s1-03-correction-closure-20260820`.
No artifact hash was reported. That directory is ephemeral auxiliary evidence,
not durable repository evidence, and is not needed to verify either inventory.

## Final qualification receipts

The first post-acceptance `make check` ran all unit and integration tests
successfully but stopped at the Python vulnerability step after 125.63 seconds.
`pip-audit --require-hashes` cannot accept the exact locked CapAuth VCS
requirement because a VCS requirement does not carry an archive hash.
Clean-room qualification was not started until a subsequent full check passed.

The audit harness was corrected without changing any dependency, version, or
source pin. It now requires exactly one occurrence of the approved CapAuth URL
and commit in the locked export, removes only that VCS line from the otherwise
fully hashed registry audit, and audits the CapAuth `0.3.1` release identity
separately with dependency resolution disabled. CapAuth transitive dependencies
remain in the hashed registry audit. The exact commit remains enforced by
`uv.lock` and the checked VCS line. The focused vulnerability recheck passed in
1.21 seconds with zero Python or npm findings.

The complete accepted-source `make check` then passed in 135.33 seconds. It
included 241 Python unit tests in 16.255 seconds, one frontend test in 160 ms,
and 34 integration tests in 92.245 seconds. All five design hashes, the
105-package lock, provenance, formatting, lint, typing, frontend build,
migration manifest, fixture safety, SBOM, secret scan, hashed Python registry
audit, separate CapAuth release audit, and npm audit passed. Both Python audit
paths and npm reported zero known vulnerabilities.

The one authorized `make clean-room-check` copied 138 allowlisted source files
into a fresh temporary tree, bootstrapped the pinned toolchain, and passed its
full nested `make check`. It completed in 279.35 seconds. The clean-room run
included 241 Python unit tests in 16.412 seconds, one frontend test in 133 ms,
and 34 integration tests in 92.431 seconds, with zero Python or npm findings.
Its temporary source tree and disposable database were removed automatically.

The final evidence-state `make check` is recorded on SKCapstone card
`0ef8eb48` rather than written back into this self-referential source receipt.
It runs only after this evidence and the completion inventory are sealed, so no
post-check file edit can invalidate the checked state.

## Security and data assurance

- All principals, identifiers, credentials, key material, policy records, and
  authorization requests used in tests are synthetic.
- No raw credential is committed as a fixture or written to evidence.
- No live CapAuth home, production keyring, passphrase, secret, backup,
  protected corpus, HammerTime matter, or Inbox path was inspected.
- No browser storage, PostgreSQL business row, Temporal history, prompt, log,
  report, handler context, audit event, exception string, or traceback receives
  a raw leaf or ancestor credential.
- No external account, production service, persistent process, connector,
  corpus operation, deployment, commit, or push was performed.
- Approved architecture and planning files remain byte-identical to their
  recorded hashes.

## Production prerequisites

The authorization algorithm is complete and fails closed, but protected
production composition remains blocked until all of these are configured and
qualified:

1. a dedicated SKLegal issuer key outside synced CapAuth state, held by
   `gpg-agent` or a narrow signing sidecar;
2. a protected, versioned trusted issuer policy backend;
3. a live principal adapter bound to current S1-02 identity and tenant state;
4. a durable shared revocation backend with trustworthy revision semantics;
5. a durable multi-worker atomic replay backend;
6. the attributable append-only audit sink delivered by `SKL-S1-05`;
7. startup composition that uses explicit unavailable adapters until every
   required backend is healthy.

The process-local in-memory implementations are not multi-worker or production
safe and must never be presented as such. CapAuth's native token and revocation
stores are not used. The installed CapAuth service is not mounted as SKLegal's
policy decision point.

## Deferred integrations

- Authentication, session cookies, tenant and matter membership belong to
  their route and identity composition cards. The typed resolver contract in
  this card preserves deliberate 401 or 403 denials and maps backend failure to
  503 without exposing resolver details.
- Conflict, waiver, privilege, protected access, ethical wall, retention, and
  hold gates belong to `SKL-S1-04` and remain independent requirements.
- Durable audit and workflow replay belong to `SKL-S1-05`.
- The closed tool catalog and negative completeness test belong to
  `SKL-S3-03`; no production tool catalog exists yet.
- Model egress and connector state-machine policy remain independent gates in
  their assigned cards.

## Rollback

No database rollback is needed. Remove the exact source paths in the corrected
frozen inventory, remove the `sklegal-capauth` workspace and API dependencies,
restore the prior lock, and rerun the prior foundation checks. Do not replace
the authorizer with an allow fallback. Incomplete composition must remain
unavailable.
