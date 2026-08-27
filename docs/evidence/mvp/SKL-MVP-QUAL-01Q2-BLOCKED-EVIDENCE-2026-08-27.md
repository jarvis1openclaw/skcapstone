# SKL-MVP-QUAL-01Q2 blocked evidence

Date: 2026-08-27

Card: `1adc9af3`

Owner: `codex-sol-1adc9af3`

Verdict: `BLOCKED_DURABLE_V2_ROUTER_COMPOSITION`

## Exact inputs

- Evidence commit: `a85e1f816d322a83763bef5438d214a3820d4243`
- Evidence tree: `3f443b22440ecd8c5d089893fff0721d496a7a87`
- Repair commit: `4897fb3d7d8a08e49e560839e9da589743bc41e7`
- Repair tree: `11eb4c58972ea8255aad057cda366cf4b98aa184`
- Independent PASS bundle: `7541ee856e3827df046ddac23c8bca90694736c6c0308721104ec552f351bc1c`
- Original BLOCKED bundle: `f300d65675533baa863682f47448a399b9f346d83a670c3121652309e2f69276`
- COMPOSE PASS bundle: `5186d8acc4d5584b0afb0351fa725bc05f7699828f717c5aedf0cc8fe18d676d`

The original `465e000f` authorization failure and every predecessor record remain
preserved. This successor made no product source change.

## Recomputed pins

- Public-synthetic fixture: `4b14516539289255daca9065dd28060a9e96aea365faec9ecbd1a679bdb47742`
- V2 contract aggregate: `af766d16974506f14a4242a8016018c2a7ca1c1d891f477fc6af6a9245cc5bac`
- Migration SQL aggregate: `be4e14723627eca23638b10da6dd3ad438db18045e5b98207e7ba515023fae1b`
- Durable topology aggregate: `094c4e49ce3b9aa03d85be350aaa5ead8d24ae94d0d407d73e746ff5f5efed61`
- Preview web build aggregate: `cfa544dfe6c5a44ca6f29d004f147b5724edc1582eaeceee5adea5c7143802c0`
- Dual PostgreSQL approval: `3fce310da887cc91c7b34bbcff6dbeb1e28c00fd941bb04d8a6ee30e3091225c`
- Package lock: `b4da8dc8d7815a19b54bff1ee059c25167485708c0261f40ca6b6df2452e71e8`

## Successful subset qualification

Two fresh deterministic full driver runs passed with real Chrome and separate
core and retrieval PostgreSQL:

- Run 1 SHA-256: `ce6aad873c367262f6b3d4708d1a8123731f934cf082bca8387364f84a6bfe35`
- Run 2 SHA-256: `a5075e8267271e224ab13dae68a87e34a42ed344c111121a8f63f57fb7b3209e`
- Normalized result SHA-256 for both runs: `85c51972d7e0d21d7f667f8fcb1522091ca739542465a492c345c1ef8ff3a8b5`

Both runs proved authorized Matter access without the historical 403,
cross-Tenant and cross-Matter denial, stale-policy denial, revoked-session
denial, core and retrieval RLS, independent outages, restart recovery,
retrieval lag and deterministic rebuild, backup and restore, reset and reseed,
migration replay, CSP, compact browser layout, empty script-readable storage,
and task container and volume cleanup.

The broader checks also passed:

- Original focused Python suite: `320 passed, 4 errors`. All four errors were
  environment setup failures because candidate-local `.tools/bin/uv` was absent.
  The original log is preserved with SHA-256
  `8f663d19fba479833f5ee3733c4dd6181659199c845e95425bbf044d0e8b2d94`.
- Corrected identical focused Python suite: `324 passed in 65.78s` after the
  candidate-local locked tool path was made available. The corrected log is
  preserved with SHA-256
  `8470a1f23e402b44385c632a40153c579eecc52c8df8cea696e88741999547fa`.
- Web suite: `24` files and `257` tests passed.
- TypeScript, ESLint, Prettier, preview build, Ruff, Python compilation,
  migration manifest, fixture safety, vendored CapAuth, Python and Node
  vulnerability scans: PASS.

The first preflight attempt and the first broad Python invocation were invalid
test setup, not product failures. The initial virtual environment resolved
editable packages from another worktree, and the broad suite initially lacked
the required local `.tools/bin/uv`. Candidate-local import custody and a fresh
locked environment corrected those setup defects without changing product
source. The full focused suite was rerun from the beginning, not resumed from
the four errored tests.

## Decisive product blocker

The durable production composition does not supply any `feature_routers` to
`MvpApiComposition`. The field defaults to an empty tuple. `create_mvp_app`
mounts the canonical V2 adapter only when that tuple is nonempty.

The exact factory OpenAPI comparison against the frozen manifest found:

- Frozen V2 operations required: `21`
- Exact operation matches: `0`
- Required method and path pairs absent: `17`
- Required method and path pairs present with wrong operation IDs: `4`
- Factory OpenAPI SHA-256: `21daadaf38e4f38c12b37aef6b2f21b26e97ad0bc9de8e3a435b41f7589fd8fb`
- Gap result SHA-256: `a691f848713c1a63af1c1683ca6ddd8839ba42015bc8dff99606b7d24b6376bc`

The absent operations include joined analysis, Agent Run start and read,
challenge, recommendation list and decision, artifact intake and read, Work
Product read, version, validation and Approval decision, Task, Deadline,
simulation handoff, activity replay, and activity export. The four existing
workspace, Claim, corpus search, and corpus span paths retain pre-adapter
operation IDs.

Feature-local and browser-fixture tests do not prove these operations are
mounted in the durable runtime. Therefore this card cannot claim the requested
intake, analysis, Agent Run, recommendation, challenge, Task, Work Product,
Approval, simulation handoff, or activity replay qualification.

## Scanner and typing classification

The official scanner remains nonpassing and is not waived:

- `9` inherited findings are exact Git and evidence hashes in the preserved
  original BLOCKED result.
- `32` new high-entropy findings are evidence, log, backup dump, fixture,
  projection, revocation revision, source commit, and tree hashes in this
  successor's machine-readable evidence.
- `1` new keyword finding is the machine-readable `secret_scan` gate name.
- Classification: `41` content hashes, `1` nonsecret metadata key, `0` genuine
  secrets.
- No detector, threshold, exclusion, or baseline was changed.

Focused mypy remains nonpassing with the same `22` inherited errors in the same
three files recorded by independent review `75c5826d`. This card does not claim
mypy compliance.

## Required successor

Create one bounded implementation card that mounts the exact reviewed feature
routers in the durable production composition with durable PostgreSQL-backed
stores and no in-memory production dependency. It must regenerate exact
OpenAPI, rerun all 21 operations against real core and retrieval PostgreSQL,
and publish source, migration, rollback, scanner, and safe-state evidence. A
distinct independent rereview must return PASS before another full
qualification successor is eligible.

No product repair, protected content, provider request, credential access,
non-loopback listener, external action, merge, push, broad cleanup, or
interaction with card `431db4dd` occurred.

## Safe final state

Task-created API, web, Chrome, and PostgreSQL processes stopped. No task-named
container or volume remains. Candidate-local `.tools` and `node_modules`
symlinks, the candidate-local virtual environment, test caches, Python build
metadata, web build output, and generated build directories were removed.
Only the four qualification evidence files remain as worktree changes.
