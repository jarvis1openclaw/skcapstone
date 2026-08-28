# SKL-MVP-QUAL-01Q3 completion evidence

Card: `42b5b29f`

Worker: `codex-sol-42b5b29f`

Verdict: `PASS`

## Exact custody

- Reviewed parent commit: `faead2a1d8565787f78c371c8c5a243930a1031d`
- Reviewed parent tree: `0b65a67bc9a8bd4b89b3983534257e30e65f204f`
- Implementation commit: `89c5e0e77bf2fcc362d66442b961439528583ace`
- Implementation tree: `dbf076fca56794efae6278c8c3cba5921f9b28f3`
- Preserved failed review commit: `9b21c8878b9e62faf46b1145293218181c64a4a2`
- Preserved failed review tree: `b060621ab012fae9054961f16abdce3593390d20`

The implementation is an ancestor of the exact independently PASS-reviewed
parent. This card changed no product source, test, script, migration,
configuration, or existing evidence.

## Hash recomputation

- Candidate archive: `82d709c35551120facaae1abed2bb1c0beaa8c0b04eb8750d970a12e2fa2833b`
- Candidate changed-file aggregate: `b745b82cd68a0ab6e109a92ecfd2b67defe36eac13ad4a7684e793db54a7e81d`
- Reviewed parent archive: `aacb1a2a562ecfddcd67d8acda1e6e4f6ef699965229e6630e2cdbadd670bbb8`
- Preserved failed review archive: `fc7b107e3a6f3528d40ac1fa7c474b4075fc2344ff210beb8da667a9e4476c88`
- Review bundle aggregate: `6b8208aaf7b579e738e83f145e7821963c3870843ca0356ab393a2c9b8682c22`
- Frozen manifest: `0857b0642c49531ae615362b7a40785e21aa8676a933ea6a27a5f1f057e11c66`
- Contract aggregate: `af766d16974506f14a4242a8016018c2a7ca1c1d891f477fc6af6a9245cc5bac`
- OpenAPI: `1555cec46c8143e0c4f69210b8ccf0f91ea90a9cea62166d645893462fb820b6`
- Core migration manifest: `08c517e51124d68585ba16fef9791afb5a73c90f883b128790327ad0bc899c35`
- Retrieval migration manifest: `9d5b0c8118db9d4b56b9fc3ec432800833bcc70f9693bc9cc7bf323215f4cdf6`
- Migration SQL aggregate: `be4e14723627eca23638b10da6dd3ad438db18045e5b98207e7ba515023fae1b`
- Ordered migration and bootstrap aggregate: `e269a2358a5c032996e64b5e070a25967639a530679a364fac64725b681248cd`
- Public-synthetic seed: `4b14516539289255daca9065dd28060a9e96aea365faec9ecbd1a679bdb47742`
- Deterministic web build aggregate: `889f45fccfc0953a23cca62bd60b63b802e34d8a63ad96a228cd8b80e4517ebf`
- Package lock: `b4da8dc8d7815a19b54bff1ee059c25167485708c0261f40ca6b6df2452e71e8`
- UV lock: `a77c624eae87f9908127c7af695ac1275eeab18efc519ab66185227fbc80d458`

The contract and migration aggregates hash sorted `sha256sum` records. The
build aggregate uses the same method over the sorted built file list. Two
consecutive builds produced the same aggregate.

## Two full qualifications

Both runs used the stock qualifier from the exact reviewed parent. Each run
allocated fresh random project names, credentials, loopback ports, networks,
and volumes. Google Chrome was `151.0.7922.173`.

1. `PYTHONPATH=.:$(find packages services vendor -type d -name src -print | paste -sd:) /mnt/cloud/onedrive/projects/DAVE-AI/sklegal/.venv/bin/python scripts/qualify_durable_mvp.py --output docs/evidence/mvp/SKL-MVP-QUAL-01Q3-RUN-1-2026-08-27.json`

   Result: `PASS` in `24.205s`. SHA-256:
   `a79ad50ad5b16bcf1546cbdf96318518e82a602e298bfbb87792f13530ec3226`.

2. The same command with output
   `docs/evidence/mvp/SKL-MVP-QUAL-01Q3-RUN-2-2026-08-27.json`.

   Result: `PASS` in `23.281s`. SHA-256:
   `699f61ed5fa4bcb7f9075195c75a33cb8e1b0eb39423f6db5400d87060ef5ec7`.

After removing only durations, random ports, backup dump hashes, and the
random revocation revision, both results are byte-identical. Normalized
SHA-256: `0105317257b3ae5962c6a6147332a895419f315968aac39d8c46ae6ea6eb9738`.

Each run proved:

- all 21 frozen method, path, and operation ID triples were exact and unique;
- every operation returned HTTP 200 or 201;
- intake, joined analysis, Agent Run create and read, challenge,
  recommendation list and disposition, Task, Deadline, Work Product create,
  read, validation and exact Approval, simulation handoff, activity replay,
  and activity export completed;
- ten mutation lanes replayed idempotently and activity export enforced its
  decision-bound conflict;
- durable authorization, Agent Run, artifact, Work Product, Task, activity,
  idempotency, audit, and outbox rows were read back;
- separate real core and retrieval PostgreSQL admins, application roles,
  volumes, networks, ports, restarts, backups, and restore paths were used;
- authorized Matter access succeeded and cross-Tenant, cross-Matter, revoked
  session, core RLS, and retrieval RLS probes denied;
- stale policy, retrieval outage, and core outage failed closed;
- core restart recovered, projection lag remained safe, and retrieval rebuild
  was deterministic and idempotent;
- core and retrieval backup/restore, reset/reseed, fresh migration replay, and
  rollback cleanup passed;
- Chrome direct reload, same-origin session, CSP, empty script-readable
  storage, zero failed network responses, seven named accessibility groups,
  390 pixel compact layout, no page overflow, and three labelled scroll
  regions passed.

## Frozen operation results

The 21 status results were identical in both runs:

- `compute_deadline=201`
- `create_activity_export=201`
- `create_analysis_run=201`
- `create_artifact_intake=201`
- `create_challenge=200`
- `create_simulation_handoff=201`
- `create_work_product_version=200`
- `decide_approval=200`
- `decide_recommendation=200`
- `get_agent_run=200`
- `get_artifact=200`
- `get_claim_ledger=200`
- `get_corpus_span=200`
- `get_joined_analysis=200`
- `get_work_product=200`
- `get_workspace=200`
- `list_activity=200`
- `list_recommendations=200`
- `search_corpus=200`
- `upsert_task=201`
- `validate_work_product=200`

The exact capability suite separately proved one reviewed request contract per
operation, concrete dynamic resources, one closed Matter grant, trusted
current Work Product version and content binding for Approval, and fail-closed
unknown or broadened requests. No wildcard, prefix, ambient, arbitrary-path,
or in-memory production grant was found.

## Owning and static checks

- Request capability and composition: `20 passed in 0.61s`.
- CapAuth, API, Agent Run, and Matter activity: `174 passed, 97 subtests passed in 18.19s`.
- Durable feature owners: `348 passed, 1 inherited warning in 74.99s`.
- Focused request capability negatives: `5 passed in 0.15s`.
- Web: `24 files passed, 258 tests passed`.
- Web lint: `PASS`.
- Web typecheck: `PASS`.
- Two production builds: `PASS`, same aggregate.
- Migration manifest: `PASS`, 28 migrations.
- Fixture safety: `PASS`, 17 files.
- Ruff: `PASS`.
- Ruff format over the 17 changed Python files: `PASS`.
- Python compilation: `PASS`.
- Diff check: `PASS`.
- ASCII dash and forbidden-card scans: `PASS`.
- Frozen-operation registry mypy: `PASS`.
- Changed implementation secret scan: `PASS`, zero findings.
- Secret negative controls: `4 passed, 6 subtests passed`.

The durable suite warning is the inherited Starlette 422 constant
deprecation in the governed corpus router.

## Visual, leakage, and static limitations

The current web suite includes keyboard, responsive, and committed visual
snapshot gates. Both real Chrome runs independently exercised the current
Matter and corpus pages, full accessibility tree, compact layout, direct
reload, CSP, network, and storage gates. Rendering source outside three API
transport files is byte-identical to the prior reviewed frontend that recorded
zero Axe violations and stable expanded and compact screenshot evidence. The
stock Q3 harness did not inject Axe again or retain new PNG files. This is a
visible evidence limitation, not a claim that a fresh Q3 Axe scan occurred.

The official repository secret gate remains inherited red. At the pre-report
checkpoint it reported 151 unreviewed additions: 147 hex-entropy entries and
4 metadata-keyword entries. The two Q3 run files accounted for 18 content-hash
entries. Changed implementation source had zero findings, and all planted AWS,
GitHub, bearer, private-key, password, and high-entropy controls failed closed.
No baseline, detector, threshold, or exclusion changed.
The final four Q3 evidence files contain 45 detector entries, all classified
as hex-entropy content hashes. No credential, token, cookie, password,
private-key, or provider-secret detector fired.

An expanded changed-file mypy probe remains nonpassing with 13 errors in 7
files. The security-critical new frozen-operation registry passes mypy. Web
Prettier remains inherited red on `apps/web/src/api/client.ts`; web tests,
lint, typecheck, visual snapshots, and builds pass. Neither static limitation
changes the Q3 runtime, isolation, authorization, leakage, or recovery result,
and neither was repaired or hidden.

## Prohibited-boundary proof

- Production composition tests prove no in-memory production dependency.
- Capability tests reject wildcard, prefix, ambient, arbitrary-path, unknown,
  and broadened grants.
- All task listeners were random loopback listeners.
- Fixtures were public synthetic only.
- Handoffs and connectors remained simulation-only.
- No protected content, provider request, credential access, external action,
  deployment, merge, push, broad cleanup, or mutation of card `431db4dd`
  occurred.

## Rollback and safe state

Evidence rollback is `git revert` of the Q3 evidence commit. Runtime rollback
stops only each exact random qualification Compose project and removes only
its two named volumes. Both qualifiers performed that cleanup and recorded
their container and volume safe-state flags true.

Before commit, only the four new Q3 evidence files remain. The task-generated
root `.tools` and `node_modules` links, web build, and local test caches are
removed by exact path. No task process, listener, container, or volume remains.
