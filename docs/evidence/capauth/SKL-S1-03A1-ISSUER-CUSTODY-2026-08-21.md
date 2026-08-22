# SKL-S1-03A1 dedicated issuer custody and trusted issuer policy

Card: `c01d6bd7`
Implementer: `kimi` (swarm2 worktree `swarm2/c01d6bd7`)
Date: 2026-08-21
Status: Complete; ready for review

First S1-03A slice: application issuer custody and trust policy. No route
activation, production mutation, or live key creation was performed. The
dependency `d9552c4c` (S1-03A production composition) is in the REVIEW
column; the swarm orchestrator `jarvis` holds the board claim on this
card, so the claim command refused reassignment and work proceeded under
the swarm assignment.

## Acceptance evidence

### Dedicated issuer fingerprint outside synced CapAuth homes

`IssuerCustodyPolicy` in `packages/capauth/src/sklegal_capauth/issuer.py`
declares the issuer identity. The Casey human identity anchor
`AD80D077A047BABF29EEC97AF454FDBC3B1C37D9` and the Jarvis agent identity
anchor `C8D406A46F2DF4894E4FB41580A638570C9D41C4` are module constants in
`FORBIDDEN_ISSUER_FINGERPRINTS` and are rejected as application issuers
and as rotation lineage by construction. `TrustedIssuerPolicyDocument`
also refuses to trust either anchor. Custody homes and sidecar sockets
must be absolute paths outside every declared synced CapAuth home;
containment is checked in both directions after resolution.

Tests: `IssuerCustodyDeclarationTest` in
`tests/test_capauth_issuer_custody.py` (6 tests) covers anchor rejection,
rotation lineage rejection, synced-home containment, and per-kind field
rules.

### Versioned issuer policy with ceilings, explicit rotation and rollback

`TrustedIssuerPolicyDocument` carries `policy_version`, integer
`revision`, `status`, `supersedes` lineage, and `TrustedIssuerGrant`
entries binding each fingerprint to exact capability, audience, and
principal kind ceilings. `IssuerPolicyStore` keeps one immutable document
per revision; rotation writes a new revision and rollback selects an
earlier active revision explicitly. `VersionedTrustedIssuerBackend`
implements the existing `TrustedIssuerBackend` contract with a fresh
strict read on every snapshot and no stale fallback.

Tests: `TrustedIssuerPolicyDocumentTest` (4 tests),
`IssuerPolicyStoreTest` (8 tests), and
`VersionedBackendAuthorizationTest` (3 tests) cover ceiling enforcement
through the real authorizer (`untrusted_issuer` on ceiling breach),
explicit rotation and rollback, and fail-closed behavior on missing,
malformed, symlinked, mismatched, inactive, and revoked policy states.

### Signing only through gpg-agent or a narrow sidecar

`IssuerSigningHandle` is the protected signing protocol.
`GpgAgentSigningHandle` invokes gpg in batch mode with `--homedir` bound
to the custody declaration and never passes a passphrase in arguments,
environment, logs, or rows; secret operations stay inside gpg-agent.
`readiness()` returns a sanitized `SigningReadiness` verdict. All signing
failures raise `SigningUnavailable` with static sanitized messages.

Tests: `GpgAgentSigningHandleTest` (5 tests) proves readiness verdicts,
sanitized failure, and the absence of passphrase flags in the spawned
command. The integration test
`tests/integration/test_issuer_custody_contract.py` uses two synthetic
throwaway ed25519 keys in an isolated temporary home and proves the full
lifecycle with real gpg: dedicated issuer authorizes, explicit rotation
and rollback authorize, unknown issuer denies `untrusted_issuer`, revoked
rotation denies `backend_unavailable` while the prior revision still
authorizes, and revoking the last revision closes the boundary.

## Files changed

- `packages/capauth/src/sklegal_capauth/issuer.py` (new)
- `packages/capauth/src/sklegal_capauth/__init__.py` (exports)
- `tests/test_capauth_issuer_custody.py` (new, 24 tests)
- `tests/integration/test_issuer_custody_contract.py` (new, 1 test)
- `docs/development/CAPAUTH.md` (issuer custody section)

## Tests and exact results

- `pytest -q tests/test_capauth_issuer_custody.py
  tests/integration/test_issuer_custody_contract.py`:
  25 passed, 2 subtests passed.
- Full python suite `pytest -q tests`: 557 passed, 15 skipped
  (pre-existing environment skips), 531 subtests passed.
- `ruff check` and `ruff format --check`: clean.
- `mypy packages/capauth`: no issues in 8 source files.

## Known limitations

- Revocation tombstones and policy documents rely on filesystem
  protection of the store directory; durable signed policy distribution is
  deployment work under the S1-03A composition.
- The verifier side still resolves public keys through `GNUPGHOME`;
  pinning verifier key custody is composition work, not part of this
  slice.
- The narrow sidecar handle protocol and readiness matrix are delivered
  by child card `d7574c4c`; the strict parsing and fail-closed matrix is
  deepened by child card `beadf98b`.
- Synthetic key generation in tests passes an empty passphrase to gpg in
  an isolated temporary home, matching the existing repository contract
  test pattern. The signing handle itself never accepts a passphrase.

## Rollback

Remove the new module, exports, tests, and documentation section. No
database migration, production configuration, route, or live key was
introduced, so rollback is a pure code revert.
