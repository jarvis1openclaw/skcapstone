# SKLegal MVP HANDOFF-02 overlap reconciliation

Date: 2026-08-23

Card: `0a5e4e0c`

Independent review: `7c2c9e20`

Disposition: `READY_FOR_INDEPENDENT_REVIEW_BASE_STILL_BLOCKED`

## Result

HANDOFF-02 resolves every input identified by HANDOFF-01 into one of two states: an exact immutable allowlist entry, or a fail-closed exclusion. No disputed shared-main byte is authorized for BASE-01. The migration namespace is collision-free by reservation, but reservations are not permission to write or apply migrations. Contract review `b417bc62` recorded `BLOCKED_P1_CLAIM_APPROVAL_SPLIT_TRUTH`, so candidate `d03305180c050f90243a63dee866cf657612f033` is excluded. Repair `09c527da` must produce an immutable replacement and rereview `cf83cb57` must return PASS. BASE-01 lacks formal dependencies on both `cf83cb57` and HANDOFF-02 review `7c2c9e20`; its `blocked_by` links are not dependency closure. BASE-01 remains blocked.

## Immutable custody

The isolated worktree is `/tmp/sklegal-swarm-20260823/0a5e4e0c` on branch `codex/0a5e4e0c-handoff-02`.

The clean starting point was:

- commit `523b3a7b7ca4dd513596d9d5ec73d344d922362d`
- tree `ae30d67b6ad5945985896ed034c0d7c9cc9bca87`

The owned source and manifest candidate is:

- commit `a44a5144913c7ab6277ce542a0de5687aeb80687`
- tree `c7892ceba1aab09600545ef98792c178143501c1`
- parent `523b3a7b7ca4dd513596d9d5ec73d344d922362d`

The final evidence-only descendant commit and tree are recorded on the append-only card after the evidence commit is created. This document does not self-assert its containing Git object.

## TDD precondition

The prescribed base did not contain `SKL-MVP-HANDOFF-02` or `SKL-MVP-HANDOFF-02R`. Before inventory, card, Git-object, branch, or collision analysis, the assignee added only those two exact sections to the isolated worktree, read both sections in full, and recorded `tdd_precondition=added-and-read-before-inventory-2026-08-23T17:08Z` on `0a5e4e0c`. Shared main and foreign TDD sections were not edited.

The owned TDD file at the source candidate has SHA-256 `c554b919cb988dd54a7d7448a6c5fc57ee0b5853f2c7999cff1f3c0bdaa453f8`. BASE-01 must apply the exact owned HANDOFF-02 and HANDOFF-02R section delta, not select this or any other whole-file TDD winner.

## Attributable owner receipt

The root integration owner supplied this attributable receipt:

1. The root owner owns only shared-main TDD sections `SKL-MVP-ARCH-01` through `SKL-MVP-CLEAN-01`, appended under card `5195197c`. Those bytes have no immutable commit and tree, so they remain excluded until a separately authorized clean handoff exists.
2. Shared-main `migrations/manifest.json` and `tests/integration/persistence_contract_security_boundary.py` must remain excluded absent one attributable owner and an immutable commit and tree.
3. No aggregate source commit and tree is known for `51ed3820`, `78c95e1f`, or `c4dfd1df` beyond existing card and evidence links.
4. `e7a3d02e` supplied no owner handoff or receipt after the HANDOFF-01 review.
5. No shared-main byte is authorized for absorption.

The receipt is recorded on the card as `owner_receipt_codex_root` and `unresolved_source_disposition=FAIL_CLOSED_EXCLUDE_FROM_BASE_01`.

## Seven blocker dispositions

| Blocker | Evidence | Decision | Required future boundary |
| --- | --- | --- | --- |
| Disputed multi-owner TDD composite | Shared observed SHA-256 `abe5bdd3d55d613a411cfb81450ea02f7ac87108b931ae70ee08f7c0e1050a64`; root-owned appended-range SHA-256 `cf86ce5bc64af7a180653afa3c73a805012abacc660ee5459407f94237979b03`; neither has aggregate Git custody | Exclude the entire shared working file | Reapply exact attributable sections from separately sealed commits. Never choose a whole-file winner. |
| Unowned migration manifest | Shared observed SHA-256 `347ad82aee1c414fd99da33ba216980050e9d747af11c3ab531eaae77063af09` | Exclude | BASE-01 regenerates the manifest from independently reviewed immutable migrations. |
| Unowned persistence security test | Shared observed SHA-256 `a8cc08ccfe5aa987dce1f956f429d7ff47b29442602dc4e12c5d79f5f3c814b6` | Exclude | The relevant feature owner reissues a scoped test delta from a clean base. |
| Missing aggregate pins for `51ed3820`, `78c95e1f`, and `c4dfd1df` | Evidence SHA-256 values `8f8da23c5fce5d8e9491cc93a99afa70d7f3e2a6472cffa052a7afef59ef3cef`, `d3b47ce9d9c72f2008dad5e0169a7af3e2675fa54d9490f12bfb13b6ffa01647`, and `05b693819a05ce801d8c6308dbe9a31574912ce3aa17a32a52602dd44f3a61b9`; no aggregate commit or tree | Exclude all aggregate source bytes | Reissue each needed delta through a single clean owner candidate with commit, tree, tests, evidence, rollback, and independent review. |
| Migration `0019` semantic collision | Reviewed candidate owns `0019_sentence_groundings.sql`, SHA-256 `734ee0e00b76778f1e0a3098e77c729d6f9ec5ee803abcb3f77c2acdc8958376`; dirty CapAuth file also used `0019` | Preserve candidate `0019`; exclude dirty collision | Reissue CapAuth delta as `0020_capauth_applied_history_delta.sql`; never rename or rewrite applied history. |
| Stale `e7a3d02e` | Claimed by `kimi-skl-s3-03b`; last attributable owner update `2026-08-23T09:10:42.566893+00:00`; no commit or tree | Retain claim and exclude | Accept a current owner handoff with exact immutable custody, or use a separately authorized append-only supersession. No takeover or cleanup here. |
| Scoped MVP board consistency | Exact scoped snapshot contains ten cards and an acyclic dependency graph, but `91988c9e` omits `cf83cb57` and `7c2c9e20` from its formal dependencies | Scoped graph is internally parseable; BASE-01 is ineligible and linkage is incomplete | Require `09c527da` replacement, `cf83cb57` PASS, `7c2c9e20` PASS, and formal BASE-01 dependencies on both review cards. Broader parity debt remains separate. |

The machine-readable decisions are in `SKL-MVP-HANDOFF-02-INCLUDE-EXCLUDE-2026-08-23.json`.

## Immutable allowlist

Only these inputs can be considered by BASE-01 after their stated independent review conditions:

| Input | Commit | Tree | Condition |
| --- | --- | --- | --- |
| Reviewed V2 candidate | `8177c88c5fd371a487e2c206c53b24e3619c1855` | `a23e08dc6edfde8397298b3d1ee0b354434c3bcd` | Final review `ac4c56f7` remains PASS and BASE-01 verifies exact lineage. |
| HANDOFF-02 owned TDD sections and three JSON manifests | `a44a5144913c7ab6277ce542a0de5687aeb80687` | `c7892ceba1aab09600545ef98792c178143501c1` | HANDOFF review `7c2c9e20` returns PASS and integration applies only the exact owned path and section allowlist. |

The V2 contract freeze candidate `d03305180c050f90243a63dee866cf657612f033`, tree `3d1ad95d166ff6c8721bb8d2da947fd53e845182`, is not on the allowlist. Review `b417bc62` sealed review commit `cd0cbf14835a40a938c610dd1b3f01ba781c40c6`, tree `f06758d908c0374f9676bb457a98b9c6cf6507ee`, with disposition `BLOCKED_P1_CLAIM_APPROVAL_SPLIT_TRUTH`. Repair card `09c527da` must produce the immutable replacement, and rereview `cf83cb57` must return PASS before that replacement can be considered.

An observed working-tree hash proves bytes only. It never proves ownership, lineage, or Git custody.

## Collision-free migration reservation

The reviewed baseline retains `0019_sentence_groundings.sql`. The following forward numbers are reserved:

- `0020_capauth_applied_history_delta.sql` for a new attributable reconciliation owner
- `0021_skgateway_qualification_scope.sql` for a new attributable qualification database owner, dependent on `0020`
- `0022` through `0029` for joined analysis, card `929c6ada`
- `0030` through `0039` for Agent Runs, card `85967293`
- `0040` through `0049` for artifact intake, card `6894d326`
- `0050` through `0059` for Work Products, card `0d1d81ce`
- `0060` through `0069` for Tasks and Deadlines, card `519832c7`
- `0070` through `0079` for Matter activity, card `ec0763b9`
- `0080` through `0089` for corpus, card `e0ad0f06`
- `0090` through `0099` for integration-only reconciliation, card `c1ee25da`

The map reserves 78 unique forward numbers from `0022` through `0099`, plus exact reservations `0020` and `0021`. No number overlaps. A reservation gives no authority to edit a migration, regenerate `migrations/manifest.json`, apply a database change, or mutate an applied registry.

Every future migration owner must prove fresh install, historical `0012` upgrade, reviewed-candidate `0019` upgrade, up/down/up behavior where supported, digest-prefix integrity, dependency order, and duplicate-number rejection. Applied migration filenames, bytes, digests, order, and registry values are immutable.

The executable reservation data and rules are in `SKL-MVP-HANDOFF-02-MIGRATION-RESERVATIONS-2026-08-23.json`.

## Scoped board result

The captured scoped dependency graph is acyclic and every referenced dependency exists. At capture:

- `5195197c`, `cb60092e`, `d6c5f135`, and `539344d7` were done.
- `b417bc62` was done with fail-closed disposition `BLOCKED_P1_CLAIM_APPROVAL_SPLIT_TRUTH`; it was not a PASS.
- `0a5e4e0c` was doing.
- `09c527da`, `cf83cb57`, `7c2c9e20`, and `91988c9e` were backlog.
- `91988c9e` had `cf83cb57` and `7c2c9e20` only in its `blocked_by` link. Its formal dependency list was only `b417bc62` and `539344d7`. A link is not dependency closure, so BASE-01 cannot be represented as safe or eligible.

The scoped snapshot is `SKL-MVP-HANDOFF-02-SCOPED-BOARD-2026-08-23.json`. It does not claim global parity. The broader observed projection remained ALERT with 894 checked, 510 matched, 114 mismatches, 270 missing, 164 legacy open, 155 store open, and 9 open drift. HANDOFF-02 performed no reconciliation or card repair outside its scoped append-only updates.

## Verification

The source candidate was verified with:

- JSON parse for all three manifests: PASS
- include and exclude manifest structural assertions: PASS, 2 allowlist entries and 9 exclusions
- migration reservation uniqueness and range expansion: PASS, 78 lane numbers plus exact `0020` and `0021`
- scoped dependency existence and acyclic graph: PASS, 10 cards; formal BASE-01 review dependency enforcement remains incomplete and is recorded as a blocker
- exact base, parent, candidate, and tree Git object checks: PASS
- `git diff --check`: PASS
- ASCII dash scan over owned files: PASS
- scoped credential-keyword scan: PASS
- secret scan with evidence-identifier fields excluded: PASS; raw high-entropy findings were only the expected Git and SHA-256 evidence identifiers
- loopback preview listener preservation check: PASS at observation; no preview process was started, stopped, or mutated

The independent reviewer must recompute these results and card state. This handoff does not assert review PASS.

## Changed files

The source candidate changed only:

- `docs/tasks/SUBAGENT-TASK-TTDS.md`, limited to HANDOFF-02 and HANDOFF-02R sections
- `docs/evidence/mvp/SKL-MVP-HANDOFF-02-INCLUDE-EXCLUDE-2026-08-23.json`
- `docs/evidence/mvp/SKL-MVP-HANDOFF-02-MIGRATION-RESERVATIONS-2026-08-23.json`
- `docs/evidence/mvp/SKL-MVP-HANDOFF-02-SCOPED-BOARD-2026-08-23.json`

The final descendant adds this evidence file and completes the pinned include manifest entry. No product source, migration, shared main, preview runtime, database, listener, credential, protected content, provider, HammerTime `Inbox/`, or external-action state was changed.

## Limitations and rollback

This is an inventory and remediation handoff, not integration. It creates no clean aggregate for excluded cards and does not make BASE-01 eligible. It does not adjudicate broader parity debt, stale foreign work, or application correctness.

Rollback is to revert only the exact HANDOFF-02 TDD sections and the four HANDOFF-02 evidence files from the isolated candidate. Card events remain append-only. No foreign branch, worktree, source file, migration, runtime, database, or credential is altered or deleted.
