# SKL-MVP-INTEG-01F2 secret finding classification

Card: `ba245418`
Owner: `codex-luna-ba245418`
Scope: exact c1 candidate `8b9553b4167a6e1acaaffbaf0f17d59892513841`, tree `9da2b879c08573ecd8dac421bcb43785570ff1a0`, refreshed from evidence commit `7638faf7f447d9db29f1f99c69cc54acf889576b`.

## Decision

`PASS` for this card's scoped secret classification. All ten additions to the
official scanner result are `Hex High Entropy String` findings whose values are
content hashes. No credential, capability token, private key, password,
protected identifier, or unrelated secret material was read, recorded, or
accepted. The original c1 integration failure remains preserved and this card
does not resolve the independent Party RLS failure.

## Exact finding inventory

The identifiers below are the scanner's opaque finding identities. Detected
values are intentionally omitted.

| Finding | Location | Classification | Evidence |
| --- | --- | --- | --- |
| `6aaaf13eeb5438062446bc6bf8a7bfddc481ecda` | `migrations/manifest.json:82` | immutable migration content hash | SHA-256 matches `migrations/0020_capauth_applied_history_delta.sql` |
| `cc03a4c4ebba3cf283abfcca60c5f99b1b5bdf7b` | `migrations/manifest.json:86` | immutable migration content hash | SHA-256 matches `migrations/0021_skgateway_qualification_scope.sql` |
| `09ea71e99f7d5da9896b6f8ff5e38939f6a55cf4` | `migrations/manifest.json:90` | immutable migration content hash | SHA-256 matches `migrations/0022_joined_analysis_snapshots.sql` |
| `c1e0a3b3d75d336283f6c227115a051020281baa` | `migrations/manifest.json:94` | immutable migration content hash | SHA-256 matches `migrations/0023_governed_agent_runs.sql` |
| `618021ea0626324786e47c80a60286b69054986d` | `migrations/manifest.json:98` | immutable migration content hash | SHA-256 matches `migrations/0024_artifact_intake.sql` |
| `e11b3dcd02ef4c739d758cffb5e9321c89614283` | `migrations/manifest.json:102` | immutable migration content hash | SHA-256 matches `migrations/0025_work_product_feature_lane.sql` |
| `92e61d356abff33af9030c2638e58dd8526299ea` | `migrations/manifest.json:106` | immutable migration content hash | SHA-256 matches `migrations/0026_task_deadlines.sql` |
| `a5349b1c4662bf7a4a0e3dfbaf9ba10ecdad17bc` | `migrations/manifest.json:110` | immutable migration content hash | SHA-256 matches `migrations/0027_matter_activity_projection.sql` |
| `0620bf4c129ca4d22f5eaeaccd72b293fdf8c4f0` | `migrations/manifest.json:114` | immutable migration content hash | SHA-256 matches `migrations/0028_governed_corpus.sql` |
| `4c76de7c164c04af5505ce2e4d4ff7785f4fccfc` | `tests/fixtures/mvp/public-synthetic-mvp-v2-acceptance.json:11` | public-synthetic content hash | SHA-256 matches the referenced component map |

The manifest hash comparison covered every migration entry, and every entry
matched its referenced SQL file. The fixture hash comparison covered its
wireframe, component map, and base fixture references, and each matched. The
fixture declares `publicSynthetic: true`, `classification: public`, and
`simulationOnly: true`.

## Files changed

- `.secrets.baseline`: added exactly the ten reviewed scanner identities with
  `is_secret: false`.
- `tests/test_secret_baseline.py`: updated the exact reviewed baseline count
  from 353 to 363.
- This evidence document.

No source value or fixture value was replaced because all ten values were
proven content hashes.

## Commands and results

- `python scripts/check_secrets.py`: PASS.
- `python scripts/check_fixture_safety.py`: PASS, 17 files.
- `python -m pytest -q tests/test_secret_baseline.py tests/test_foundation.py`:
  PASS, 23 passed, 16 subtests passed.
- `ruff check` on touched Python and check scripts: PASS.
- JSON parsing for `.secrets.baseline`, `migrations/manifest.json`, and the
  acceptance fixture: PASS.
- `git diff --check`: PASS.
- `npm run lint`: not run to completion because `eslint` is not installed in
  this isolated worktree.
- `npm run format:check`: not run because the web toolchain is not installed;
  Ruff format reports pre-existing formatting in `tests/test_secret_baseline.py`.
- `npm run typecheck` and `npm run build`: not run to completion because `tsc`
  is not installed in this isolated worktree.
- The locked repository wrapper could not run because `.tools/bin/uv` is absent.

## Limitations and safe state

This is a public-synthetic scanner-baseline repair only. It does not qualify
the complete c1 integration candidate. The independent Party RLS failure and
any other c1 integration blockers remain open. No merge, push, deployment,
restart, database mutation, protected-data access, HammerTime Inbox access,
provider traffic, credential use, external action, or cleanup was performed.
Card `431db4dd` was not touched.

## Rollback

Revert this local evidence and baseline refresh commit only to parent
`7638faf7f447d9db29f1f99c69cc54acf889576b`. No data or host rollback exists.
