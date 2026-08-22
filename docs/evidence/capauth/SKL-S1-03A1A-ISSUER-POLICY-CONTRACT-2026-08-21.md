# SKL-S1-03A1a versioned trusted issuer policy contract

Card: `beadf98b`
Implementer: `kimi` (swarm2 worktree `swarm2/c01d6bd7`)
Date: 2026-08-21
Status: Complete; ready for review

Small issuer-custody slice: the versioned trusted issuer policy contract
and its read-through trust boundary. This card creates and stores no
signing key. The contract types landed with parent card `c01d6bd7`; this
card hardens the semantics and proves the full fail-closed matrix. The
board claim is held by the swarm orchestrator `jarvis`, so the claim
command refused reassignment and work proceeded under the swarm
assignment.

## Acceptance evidence

### Strict versioned parsing, allowlists, ceilings, no-symlink, no stale fallback

`TrustedIssuerPolicyDocument` is a strict, extra-forbid, frozen model:
exact `sklegal-issuer-policy/v1` schema, integer `revision` at least 1,
`active` or `revoked` status, `supersedes` lineage that must name an
earlier revision, and a nonempty unique fingerprint allowlist. Each
allowlist entry is a `TrustedIssuerGrant` binding the fingerprint to
exact capability, audience, and principal kind ceilings. Identity trust
anchors are rejected by construction.

`IssuerPolicyStore` loading opens with `O_NOFOLLOW`, requires a regular
file with exactly one link, bounds input at 64 KiB, decodes UTF-8,
rejects duplicate JSON object members at every nesting level, and
validates the document revision against the requested revision. Every
snapshot is a fresh strict read through `VersionedTrustedIssuerBackend`;
there is no cache and no stale fallback.

One semantic was hardened under this card: a revocation tombstone closes
its revision whenever the marker is present, even if the marker is
unreadable, corrupt, or names a mismatched revision. An ambiguous
revocation artifact can never resurrect a policy.

### Fail-closed matrix

`tests/test_capauth_issuer_policy_contract.py` (12 tests, 13 subtests):

- Malformed: truncated JSON, empty file, non-object roots, missing
  fields, extra fields, top-level and nested duplicate members, string
  revision, non-UTF-8 bytes, and oversized input all fail closed.
- Missing: absent revision, absent store root, and a directory at the
  document path all fail closed.
- No symlink: symlinked document, symlinked tombstone, and symlinked
  store root all fail closed; a hardlinked document (link count above
  one) fails closed.
- Changed: a tampered document immediately changes the snapshot
  revision, denies a previously allowed credential with
  `untrusted_issuer`, and a renumbered revision field fails closed. The
  snapshot passes the document `policy_version` through verbatim.
- Revoked: tombstoned revision, corrupt tombstone, mismatched tombstone,
  and in-document `revoked` status all fail closed.
- Rollback: explicit rollback to an active prior revision succeeds;
  rollback to a revoked, missing, or tampered revision fails closed.
- Ceilings: a four-case matrix through the real authorizer proves allow
  inside ceilings and `untrusted_issuer` denial on capability, audience,
  and principal kind breaches.

## Files changed

- `packages/capauth/src/sklegal_capauth/issuer.py` (`is_revoked`
  fail-closed hardening)
- `tests/test_capauth_issuer_policy_contract.py` (new)

## Tests and exact results

- `pytest -q tests/test_capauth_issuer_policy_contract.py`:
  12 passed, 13 subtests passed.
- Combined issuer suite (parent plus this card plus integration):
  37 passed, 15 subtests passed.
- `ruff check`, `ruff format --check`, and `mypy packages/capauth`:
  clean.

## Known limitations

- Policy integrity relies on filesystem protection of the store
  directory; signed policy distribution remains S1-03A composition work.
- Tombstone presence semantics mean an accidentally created marker file
  closes its revision until an operator removes it. That bias toward
  denial is deliberate.

## Rollback

Remove the new test module and revert the `is_revoked` hardening. No
data, migration, or production configuration was introduced.
