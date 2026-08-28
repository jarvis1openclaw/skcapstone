# SKL-MVP-BASE-01F4 conflict ledger

## Integration boundary

- Card: `c9162ae6`
- Base: `bfd1a973510d327c26f05096350a38a3d0cedaf1`
- Base tree: `f75fde4cdcb747278a122f473f5717af826aed74`
- Integration order: F1, F2, F3
- Independent review: `dd4bb79d`

Only the following independently reviewed repair candidates were eligible:

| Lane | Candidate | Tree | Review card | Review evidence commit | Review evidence SHA-256 |
| --- | --- | --- | --- | --- | --- |
| F1 hermetic regression | `b734feeb946a3d139fbdfbe10ec315838a89fd8a` | `047684d80411af1ee143667db8e176f0d5df2273` | `e7fdf28d` | `a15d7f7775ccaaa99e5461691a5fd32fb2072b43` | `b0e6e2bc4cce0ea1ddb2cc6fbc2f4ad997dfa663bc85f7a5543fef795ceef39d` |
| F2 static gates | `abf01e6f7ef4a8b2d195327ff7352dacb1478a14` | `146163ff1cb9be61da968c13c3f469769b9afe1c` | `0040b661` | `5a976ae091ddb32e0b8f7ae594104ac257980b80` | `8a9f1f5941f0d096bf4e598095090c61d170803d1c2fcdc339312dc70d601ebb` |
| F3 secret baseline | `7f8abf808f680d720a58a143527a2783d3dc3ae5` | `d24acf785f89780514713ddb901e197fdefdca8f` | `ebf37535` | `cc5a7b480737cc147b1dc45df5dfe33c117a1ec9` | `e2bf8c085386d90ca7df87dfe94a020956e87fe5dd2a31b680b38e0541715a02` |

Each candidate has the immutable BASE as its sole parent. Every recorded review has disposition PASS, matches the candidate commit and tree, and has an independently recomputed evidence hash.

## Changed-file ownership

F1 owns only:

- `docs/tasks/SUBAGENT-TASK-TTDS.md`, exact F1 and F1R append-only sections
- `packages/connectors/hammertime/src/sklegal_hammertime/candidate_release.py`
- `scripts/clean_room_check.py`
- `tests/integration/test_foundation_contract.py`
- `tests/test_official_drafting_candidate_release.py`

F2 owns only:

- `docs/tasks/SUBAGENT-TASK-TTDS.md`, exact F2 append-only section
- `tests/support/mvp_v2_cockpit.py`
- `vendor/capauth/src/capauth/__init__.py`
- `vendor/capauth/VENDOR-MANIFEST.json`

F3 owns only:

- `.secrets.baseline`
- `docs/evidence/mvp/SKL-MVP-BASE-01F3-SECRET-FINDING-INVENTORY-2026-08-23.json`
- `docs/tasks/SUBAGENT-TASK-TTDS.md`, exact F3 and F3R append-only sections
- `scripts/check_secrets.py`
- `tests/test_secret_baseline.py`

F4 additionally owns only its exact append-only TDD, this conflict ledger, final evidence, and isolated integration commits.

## Conflicts

Exactly three genuine merge conflicts occurred. Each was an append-only EOF conflict in `docs/tasks/SUBAGENT-TASK-TTDS.md` because F1, F2, F3, and F4 independently appended sections to the same BASE file.

1. F1 versus F4: preserved the previously committed F4 section and appended the exact F1 and F1R candidate sections.
2. F2 versus integrated F1 and F4: preserved all prior sections and appended the exact F2 candidate section.
3. F3 versus integrated F1, F2, and F4: preserved all prior sections and appended the exact F3 and F3R candidate sections.

No source, manifest, scanner baseline, fixture, migration, lockfile, or product semantic conflict occurred. Every non-TDD path is byte-identical to its reviewed candidate. Conflict resolution added no semantic repair.

## Preserved BASE boundaries

The nine fail-closed exclusions from BASE-01 remain in force. Migration `0019_sentence_groundings.sql`, the migration manifest, and the persistence security contract remain unchanged from BASE. Migrations `0020` and `0021` remain absent and reserved. Feature migration ranges `0022` through `0099` remain unchanged. No producer evidence descendant, shared-main byte, foreign worktree change, or unreviewed repair was imported.

## Rollback

Rollback is the complete inverse of the final F4 candidate to exact BASE `bfd1a973510d327c26f05096350a38a3d0cedaf1`. This removes only reviewed F1, F2, F3, and attributable F4 planning and evidence bytes. No database, runtime, service, credential, provider, protected-content, external-action, or shared-main rollback is required.
