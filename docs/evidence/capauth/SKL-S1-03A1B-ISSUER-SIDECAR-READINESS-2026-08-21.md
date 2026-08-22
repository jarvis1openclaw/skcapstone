# SKL-S1-03A1b isolated issuer signing-sidecar readiness

Card: `d7574c4c`
Implementer: `kimi` (swarm2 worktree `swarm2/c01d6bd7`)
Date: 2026-08-21
Status: Complete; ready for review

Small issuer-custody slice: the signing boundary and readiness checks,
separate from trusted policy storage. No live key was created; every test
key is a synthetic throwaway identity in an isolated temporary directory.
The board claim is held by the swarm orchestrator `jarvis`, so the claim
command refused reassignment and work proceeded under the swarm
assignment.

## Acceptance evidence

### Protected signing handle without passphrases

Two handles implement the `IssuerSigningHandle` protocol in
`packages/capauth/src/sklegal_capauth/issuer.py`:

- `GpgAgentSigningHandle` (parent card `c01d6bd7`) invokes gpg in batch
  mode with `--homedir` bound to the custody declaration. No passphrase
  appears in arguments, environment, logs, or rows; unit tests assert the
  spawned command carries no `--passphrase` or `--pinentry-mode` flag and
  that the environment is untouched.
- `SidecarSigningHandle` (this card) speaks the strict
  `sklegal-issuer-sidecar/v1` protocol over a private unix socket with a
  narrow sidecar process that owns the key home. The wire contract has
  exactly two operations, readiness and detached signing. Requests carry
  only schema, operation, fingerprint, and base64 payload; payloads are
  bounded at 256 KiB, responses at 128 KiB, and a bounded timeout applies.
  Unit tests assert the exact request member set, the absence of any
  passphrase material, and an untouched process environment.

### Sanitized failure matrix

`tests/test_capauth_issuer_sidecar.py` (10 tests, 5 subtests):

- Unavailable signer (no socket): `SigningUnavailable` with the static
  message `issuer signer is unavailable`; the socket path never appears
  in the message. Readiness reports not ready with a sanitized detail.
- Wrong fingerprint: readiness reports `issuer key is not held by the
  signer` and signing raises `issuer signing operation failed`.
- Malformed sidecar responses: non-JSON bytes, wrong schema, non-object
  body, empty signature, and null signature all fail sanitized.
- Oversized response header and oversized request payload hit explicit
  size limits.
- Process/socket outage mid-sign (connection closed before response) and
  slow sidecar timeout both fail sanitized.
- Custody kind and timeout bounds are validated at construction.

`tests/integration/test_issuer_sidecar_contract.py` uses two synthetic
throwaway ed25519 keys in an isolated temporary home and the synthetic
test sidecar in `tests/support/sidecar_server.py`, and proves with real
gpg: readiness, sign and authorize inside ceilings, rotation (new custody
lineage plus new policy revision authorizes while the retired fingerprint
denies `untrusted_issuer`), explicit rollback to the prior revision,
wrong-fingerprint readiness and signing failure, and process/socket
outage with sanitized failure after the sidecar stops.

## Files changed

- `packages/capauth/src/sklegal_capauth/issuer.py`
  (`SidecarSigningHandle`, sidecar protocol constants)
- `packages/capauth/src/sklegal_capauth/__init__.py` (exports)
- `tests/support/sidecar_server.py` (new, synthetic test scaffolding)
- `tests/test_capauth_issuer_sidecar.py` (new)
- `tests/integration/test_issuer_sidecar_contract.py` (new)
- `docs/development/CAPAUTH.md` (sidecar handle contract)

## Tests and exact results

- `pytest -q tests/test_capauth_issuer_sidecar.py`: 10 passed,
  5 subtests passed.
- `pytest -q tests/integration/test_issuer_sidecar_contract.py`:
  1 passed.
- Combined issuer vertical (both parents and children):
  48 passed, 20 subtests passed.
- `ruff check`, `ruff format --check`, and `mypy packages/capauth`:
  clean.

## Known limitations

- The production sidecar daemon is S1-03A composition work; this card
  delivers the handle, the wire contract, readiness, and the sanitized
  failure matrix against synthetic test scaffolding.
- The sidecar socket relies on filesystem permissions for channel
  protection; peer credential checks are composition work.
- Synthetic key generation in tests passes an empty passphrase to gpg in
  an isolated temporary home, matching the existing repository contract
  test pattern. The handles themselves never accept a passphrase.

## Rollback

Remove the sidecar handle, its exports, the test scaffolding, and the
test modules. No data, migration, production configuration, or live key
was introduced.
