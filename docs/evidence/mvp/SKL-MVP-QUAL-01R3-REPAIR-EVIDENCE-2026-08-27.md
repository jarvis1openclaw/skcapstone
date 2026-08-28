# SKL-MVP-QUAL-01R3 repair evidence

Card: `73be3561`

Owner: `codex-luna-73be3561`

Verdict: `PASS`

## Exact inputs

- Blocked evidence commit: `9f4a5a74f8dfa61b45bf627f93fcbe33e6f3627a`
- Blocked evidence tree: `463a2e97a4eb5bd8a9025e1a4da3cf08dafd4751`
- Original durable candidate: `464e6b7de45dd51c93eb68d9edcbf95c934ca7fb`, recorded tree prefix `4d55cfbd`
- Implementation: `fee1514bcaca6b305ae5fb2e8b9ea2697a4f1ece`, recorded tree prefix `2921b15`
- Preserved blocked bundle SHA-256: `f300d65675533baa863682f47448a399b9f346d83a670c3121652309e2f69276`
- Blocked report SHA-256: `95c5bb2c7e64cfb73e5cd3b1c754970be66471538a1f849326c55eba6dbf9029`
- Blocked result SHA-256: `800712d82b255fdb20099734152275ce0e2306ed8baf070df86767adbb220167`

The preserved first Chrome pass signed in the public-synthetic reviewer and
then rendered `Access not permitted` for exact Matter
`44444444-4444-4444-8444-444444444441`.

## Diagnosis and repair

The Matter page starts workspace and claims requests concurrently. Each
request resolves the durable browser session and mints a complete set of
request-bound CapAuth credentials through the same gpg-agent custody boundary.
That signing section had no serialization. The bounded repair adds one
per-runtime lock around capability minting only.

No CapAuth rule, principal binding, Tenant or Matter scope, policy-currentness
check, membership check, revocation check, replay check, PostgreSQL role, or
RLS policy was removed or weakened.

Repair commit: `4897fb3d7d8a08e49e560839e9da589743bc41e7`

Repair tree: `11eb4c58972ea8255aad057cda366cf4b98aa184`

Repair patch SHA-256: `c9b86985a0068c893dc2fbdacb86b28cdeda57b134801f96a8e4a74c81714d70`

Changed file SHA-256 values:

- `services/api/src/sklegal_api/durable_public_synthetic.py`: `058eaee3a61688f0cf47ded837998c7b409b20e2ca8dcd2e34c9adfc5bbe8c73`
- `scripts/qualify_durable_mvp.py`: `44fb77a67e0b52509ffed56ecb624c42bddc81f75d2f864d90dafcd826e8d109`
- `tests/test_durable_mvp_composition.py`: `b175d95ae9e9bd4d57fb02c55a41b12f345074eb00cd85e7dc02d5962d0cc7d8`

## Reproduction truth

The original failure remains preserved in the blocked report and bundle. Three
fresh replays of the exact blocked commit and tree returned PASS rather than
re-triggering the 403:

- Run 1: PASS, 15.148 seconds, SHA-256 `72941bbf1c09471b79639c319af5a6636c83fa741037b32912f571a1785d2eb9`
- Run 2: PASS, 12.799 seconds, SHA-256 `3ca92c2bbfacb022f6d96da5865e3d7987185e9eb04c6932f3c97d13ecdfbd75`
- Run 3: PASS, 15.775 seconds, SHA-256 `a9afa590e41ad37679c50666469de021156d226e6fb45c966b6ff6ae12e01b05`

The historical report does not retain the precise CapAuth denial reason.
Accordingly, the concurrency diagnosis is based on the complete request path,
the first-page-only symptom, the concurrent workspace and claims fetches, and
the previously unsynchronized shared signer. This evidence does not claim that
the historical race was deterministically re-triggered.

## Qualification

The full repair qualification ran against the exact three-file working patch.
After the run, those unchanged files were staged and `git write-tree` returned
`11eb4c58972ea8255aad057cda366cf4b98aa184`, exactly matching the committed
repair tree. The raw qualification result retained the parent HEAD fields
because the source commit had not yet been created. Its SHA-256 is
`5ad2a61bda2bea3b468d17c833b5167fa6ddec42070402a7e02805f1ff01e834`.

Result: PASS in 13.296 seconds.

- Real Chrome signed in and opened the exact authorized Matter.
- Authorized workspace API returned 200.
- Cross-Matter API returned 403.
- Cross-Tenant API returned 403.
- Stale policy returned 503.
- Revoked browser session returned 401.
- Real core PostgreSQL cross-Tenant RLS denied access.
- Real retrieval PostgreSQL cross-Matter RLS denied access.
- Core and retrieval outage behavior failed closed and recovered.
- Migration replay, reset/reseed, projection rebuild, and backup/restore passed.
- Task containers and volumes were absent after cleanup.

Focused rerun after removing task-created dependency symlinks:

```text
pytest tests/test_durable_mvp_composition.py tests/test_browser_sessions.py
9 passed in 0.58s

ruff format --check <three changed files>
3 files already formatted

ruff check <three changed files>
All checks passed!

python -m py_compile <three changed files>
PASS

git diff --check
PASS
```

Additional gates:

- Migration manifest: PASS, 28 migrations.
- Vendored CapAuth: PASS, 136 files, version 0.3.1, upstream commit `183c04a7c623e8abcf37bd705bf8bca1deb4a364`.
- `npm audit --audit-level=high`: PASS, 0 vulnerabilities.

## Pre-existing nonpassing checks

These checks are recorded accurately and are not represented as PASS:

- Secret scan: FAIL with nine `Hex High Entropy String` findings. Every finding
  is in the pre-existing, tracked, and untouched file
  `docs/evidence/mvp/SKL-MVP-QUAL-01-BLOCKED-RESULT-2026-08-27.json`.
- Focused mypy command: FAIL with 43 errors in 10 files. The errors are in
  existing import typing, optional-value, and response typing paths. No error
  points at an added repair line. This repair does not claim repository mypy
  compliance.

## Limitations and rollback

- Public-synthetic corpus only.
- Simulated connectors only.
- Optional AGE remains activation-gated.
- Revert commit `4897fb3d7d8a08e49e560839e9da589743bc41e7` to remove the code repair.
- If qualification resources are active, stop and remove only its exact task
  project and two named volumes.

No protected content, provider traffic, credential access, deployment, merge,
push, external action, broad cleanup, review `75c5826d` claim, card
`431db4dd` interaction, or unrelated mutation occurred.
