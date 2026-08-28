# SKLegal MVP HANDOFF-03 clean coherence evidence

Date: 2026-08-24

Card: `433bb97c`

Independent review: `d0c03aae`

Producer disposition: `PASS_READY_FOR_INDEPENDENT_REVIEW`

## Result

This docs-only candidate restores immutable custody for the current MVP
coherence plan. It appends attributable central task TDD sections, replaces the
stale component map with explicit truth states, and records the current
front-to-back gate graph. It does not reconstruct or claim custody for the
earlier path-only ARCH-01, component-map, or shared TDD bytes.

The durable MVP does not exist as one joined application yet. The reviewed V2
browser shell is functional against public-synthetic fixture behavior. Six
durable horizontal lanes are accepted but unmounted. The durable corpus lane
is blocked. Central API integration, frontend wiring, dual PostgreSQL
composition, full browser qualification, and independent final review remain
required in that order.

This result grants no deployment authority.

## Immutable source custody

The worktree started from the accepted repaired base:

- commit `0a293dd1afe9d5b4150468057e4ed8e552d7221b`
- tree `5cccf34afa4653bf9bcfd59ebdad1b6eb3f61caa`

The source candidate containing the component map and attributable TDD
sections is:

- commit `55b2bbce7579c1cedfe9afbbbf7256d275ae4f28`
- tree `09031b352ddc43a188a79aefca02c310d9ebad49`
- parent `0a293dd1afe9d5b4150468057e4ed8e552d7221b`
- Git archive SHA-256
  `772cd3da4ca06be4e2fa15f59dcf34ea95ea82e4aa5ed082626db6751b44a19a`
- complete base-to-source binary diff SHA-256
  `47a62c300b9fc4e07e1f37c9e8dae0e223770bcecba219e127d411d565881613`

Owned source file custody is:

| File | Blob | SHA-256 | Base-to-source binary diff SHA-256 |
| --- | --- | --- | --- |
| `docs/tasks/SUBAGENT-TASK-TTDS.md` | `2e920efdfa93b343f50c2add17b31ab8a8edfe6f` | `efaf9fc8697cb663e8a08732ee74c102c2cddf8267052c26c88b071bed0ae88d` | `541df85c38bde66ac228920df713a87e97e2ef12423b72f51ad453d5913a77cd` |
| `docs/planning/wireframes/COMPONENT-API-MAP-V2.md` | `2b9da8e6e5b662f62c5b34e944c200ef79833d0b` | `a632d9b2c9a1ac436c5cba165e543d2c1bc7ea8c509e6d8920261cbf88136d63` | `d8e043f30d28bed722c775c39de448810b770dbef9de815ee94190e122d87901` |

The final evidence-only descendant commit, tree, evidence file SHA-256, and
complete diff SHA-256 are linked on card `433bb97c` after this document is
committed. A document cannot truthfully contain its own final blob hash.

## Non-custodied superseded history

| Historical item | Recorded hash | Custody disposition |
| --- | --- | --- |
| `docs/evidence/mvp/SKL-MVP-ARCH-01-FRONT-BACK-COHERENCE-2026-08-23.md` | SHA-256 `0c04aa2106807129d275308e58cfba5c961a25c19a8d6cb1c3cbc2f0eb723ae7` | No matching file, Git blob, commit, or tree was found. Superseded as path-only history. |
| Updated component map linked by `5195197c` | SHA-256 `45a4e70ae597189376c20adf164b1270af23910bdcf7308ef62f73884ab7708b` | No matching Git object or isolated-worktree file was found. Superseded, not reconstructed. |
| Shared TDD composite observed by HANDOFF-02 | SHA-256 `abe5bdd3d55d613a411cfb81450ea02f7ac87108b931ae70ee08f7c0e1050a64` | Observed working bytes only. Whole-file selection remains forbidden. |
| Root-attributed ARCH-01 through CLEAN-01 shared TDD range observed by HANDOFF-02 | SHA-256 `cf86ce5bc64af7a180653afa3c73a805012abacc660ee5459407f94237979b03` | Attributed historical range without commit or tree. Superseded by current sections derived from the CardStore fold. |

The original component map remains immutable history with:

- commit `3933e1609256b51b641106f6611ef69461b4a585`
- tree `afd8ca0b02fd8271f399cda98069ded03bff7772`
- parent `afba855f4d3f232aa23b93453b96d5c303162ade`
- blob `6ee22e7f512361257aa28b67092873076c16d826`
- SHA-256
  `8e139a1374f894c787aa758cb56ec3af3a711141ac73df223b5f04133f3dd1cd`

HANDOFF-03 updates that immutable starting point. It does not select any
shared working file as a winner.

## Accepted immutable matrix

| Lane | Accepted commit | Tree | Independent PASS | Integration truth |
| --- | --- | --- | --- | --- |
| Canonical V2 contract | `eade7626fb6190924e80d73b79473f685e1c2531` | `f003bd74f665465e8396255947dcd30e72d2dec5` | `cf83cb57` | Frozen contract accepted, not centrally composed |
| Repaired base | `0a293dd1afe9d5b4150468057e4ed8e552d7221b` | `5cccf34afa4653bf9bcfd59ebdad1b6eb3f61caa` | `dd4bb79d` | Central integration base |
| V2 browser shell | `8177c88c5fd371a487e2c206c53b24e3619c1855` | `a23e08dc6edfde8397298b3d1ee0b354434c3bcd` | `ac4c56f7` | Reviewed public-synthetic shell, not durable behavior |
| JOIN | `a9f87e1e1010ab75d446833268282ad531e3f173` | `2a975487b7c855a250864a70f88adcc660df63db` | `8a8b2d19` | Accepted durable lane, unmounted |
| RUN | `4bf27dbcd9b038d21b9458728a9d3ae1e81f0b7c` | `dc186c2f615207ff45755ac51618fcb69225c163` | `91cd4ff5` | Accepted durable lane, unmounted |
| ART | `701e42d1ba1016c3ba5d75661eaa4ca9c8f66bfc` | `1846438ad92a7654c23deddacdc861a794e0b946` | `00e4bced` | Accepted durable lane, unmounted |
| WP | `3935618e42bba9dee044893b61e08a24df8a7f6e` | `e1fbdf095a95d09b76b79e541e16792c45f31a61` | `5432d636` | Accepted durable lane, unmounted |
| TASK | `c2c61d2536572fe2d7f0908dfb43a77c29da5103` | `3e93f7fc206572c6c656d3933e430088da306adb` | `bf730a76` | Accepted durable lane, unmounted |
| ACT | `d0b0cf16a13cbcbfe431f042fe050f7d955e94` | `19a6d8a5ae27dedff85602117107727cffcb6664` | `980fb58e` | Accepted durable lane, unmounted |
| CORPUS | None | None | None | Blocked on native substrate, repair, and review |

Every accepted commit and tree above resolves as a local Git object. The
table is an allowlist, not an instruction to merge whole branches or choose a
whole-file TDD winner.

## Superseded and excluded candidates

| Excluded input | Reason |
| --- | --- |
| Contract `d03305180c050f90243a63dee866cf657612f033`, tree `3d1ad95d166ff6c8721bb8d2da947fd53e845182` | Blocked Claim and Approval split-truth parent. Replaced by `eade7626`. |
| Base `bfd1a973510d327c26f05096350a38a3d0cedaf1`, tree `f75fde4cdcb747278a122f473f5717af826aed74` | Replaced by repaired base `0a293dd1`. |
| JOIN `b052c6e2933c00ccd43598a010d6f19f9ae82b89`, tree `1fc13a0e30007c136eb5e9bc5262c872d6fae0bb` | Replaced by fail-closed repair `a9f87e1e`. |
| WP `cfe1b1500f881d268119da7e8ce882d0ee59ce1c`, tree `4a46b0ce125c47f9330d7ddfcf427416ac13c3b8` | Replaced by durable CapAuth snapshot repair `3935618e`. |
| TASK `84c7120ff132713c16e183318461cbd3aa643d90`, tree `a3d902796e7ab9b611e2ef27b1388424c109e02b` | Replaced by durable Task and Deadline repair `c2c61d25`. |
| ACT `c8349f6c7e59ef986b43ecbce304905ac4c73501`, tree `13111daaba096535f16dd0a6f2a02091eb3f4f2d` | Replaced by durable replay binding `d0b0cf16`. |
| CORPUS `42a82dd4e7af410f31080d826fe7a1f0116066cb`, tree `32b2fe6822a4cf9b0ba620bb1b110572fe104953` | Explicitly blocked because array and metadata proof do not qualify native pgvector 0.8.0 on PostgreSQL 17.7. |
| Sparse lane migration filenames `0022`, `0030`, `0040`, `0050`, `0060`, `0070`, and `0080` | May not be registered centrally. `ea3d5577` and `e67ac604` must reissue reviewed SQL as one contiguous sequence after exact `0020` and `0021` custody. |
| Cards `78277c6b` and `91988c9e` | Earlier final-review and base paths are superseded by the current central chain. They are not authority for c1 or deployment. |
| Human gate `13cb2667` and deployment card `e7acf14b` | Both are explicitly labeled `superseded` and `do-not-claim`. Their successors are `cc531c74` and `38639a54`. |

## Frozen 18 surfaces

| # | Surface | Current truth |
| --- | --- | --- |
| 1 | Decision first | Reviewed shell |
| 2 | AI operating model | RUN accepted and unmounted; central adapter and frontend wiring owed |
| 3 | Corpus and verification map | Durable corpus blocked |
| 4 | Matter cockpit | Reviewed shell plus JOIN and RUN accepted and unmounted |
| 5 | AI intake | RUN accepted and unmounted; shell remains safely unavailable |
| 6 | Matter artifact intake | ART accepted and unmounted |
| 7 | Essential Elements matrix | JOIN accepted and unmounted |
| 8 | Ranked recommendation | RUN accepted and unmounted; browser ranking remains inert |
| 9 | Strategy and Authority | JOIN accepted and unmounted; corpus portion blocked |
| 10 | Agent team | RUN accepted and unmounted |
| 11 | Blind challenge | RUN accepted and unmounted; shell remains inert |
| 12 | Model routing evidence | RUN accepted and unmounted; provider use is not authorized |
| 13 | Work Product assembly | WP accepted and unmounted |
| 14 | Tasks, Deadlines and action handoff | TASK accepted and unmounted; external effect remains simulation-only |
| 15 | Source and run evidence | RUN accepted and unmounted; corpus portion blocked |
| 16 | Matter case log | ACT accepted and unmounted |
| 17 | Failure states | Reviewed shell; durable failure behavior awaits full qualification |
| 18 | Delivery map | Reviewed shell; no deployment authority |

## Frozen 21 operations

The authoritative manifest is
`docs/contracts/v2-mvp/v2-surface-manifest.v1.json` from accepted contract
`eade7626`, with SHA-256
`0857b0642c49531ae615362b7a40785e21aa8676a933ea6a27a5f1f057e11c66`.

| Lane | Frozen operation IDs | Count | Current truth |
| --- | --- | --- | --- |
| JOIN | `get_workspace`, `get_claim_ledger`, `get_joined_analysis` | 3 | Accepted and unmounted |
| RUN | `create_analysis_run`, `get_agent_run`, `create_challenge`, `list_recommendations`, `decide_recommendation` | 5 | Accepted and unmounted |
| ART | `create_artifact_intake`, `get_artifact` | 2 | Accepted and unmounted |
| WP | `get_work_product`, `create_work_product_version`, `validate_work_product`, `decide_approval` | 4 | Accepted and unmounted |
| TASK | `upsert_task`, `compute_deadline`, `create_simulation_handoff` | 3 | Accepted and unmounted |
| ACT | `list_activity`, `create_activity_export` | 2 | Accepted and unmounted |
| CORPUS | `search_corpus`, `get_corpus_span` | 2 | Blocked |
| Total | Exact frozen inventory | 21 | No joined durable composition exists |

Central card `c1ee25da` must match method, path, operation ID, capability,
purpose, scope, idempotency, audit, provenance, request, response, mutation
receipt, and closed error envelope. It must use explicit compatibility
adapters for reviewed lane drift and reject duplicate method and path
registration before startup.

## Dual PostgreSQL failure domains

The approved architecture requires two PostgreSQL failure domains:

| Identity | Responsibility | Required isolation |
| --- | --- | --- |
| `sklegal-core-pg` | Canonical legal, policy, audit, outbox, workflow-reference, and projection-registry state | Separate database, volume, roles, credentials, resource controls, restart and backup lifecycles, and private network identity |
| `sklegal-retrieval-pg` | Governed full-text, native pgvector, and optional AGE projections | Separate database, volume, roles, credentials, resource controls, restart and backup lifecycles, private network identity, and deterministic rebuild |

The clusters never share a superuser, transaction, or failure domain.
Retrieval never owns canonical state. Core must continue safely or fail closed
during retrieval outage. Unknown, unavailable, stale, and unauthorized remain
distinct results and none is healthy.

## Current formal gate graph

At CardStore capture `2026-08-23T21:33:10-05:00`, `c1ee25da` has 24 direct
dependencies. Twenty are DONE:

`519c01cf`, `91cd4ff5`, `9a06fcf7`, `38d27d89`, `07f79e8d`, `083855d4`,
`8a8b2d19`, `6074b95b`, `93e92ab0`, `17b8f330`, `bf730a76`, `083ffcef`,
`5432d636`, `00e4bced`, `7c9a4e1d`, `1b3e9ab2`, `a112f22c`, `8415b92a`,
`980fb58e`, and `deb2aba8`.

Four direct dependencies remain OPEN:

| Gate | Current blocker |
| --- | --- |
| `38d61700` | Waits for native corpus repair `5ae69846`, which waits for human substrate gate `71ecf523` |
| `e67ac604` | Waits for reissue `ea3d5577`, exact migration custody `f1e6d7ab`, and the corpus review |
| `a6c7fd2f` | Waits for SKCapstone uptake `db5ce143`, which requires human SKCoord release gate `0ef6f5f0` |
| `9dd76c45` | Waits for completed BOARD-10 producer `9dcba975`; independent review remains unclaimed at capture |

The central delivery chain is:

```text
[24 current direct c1 gates]
              |
              v
       c1ee25da INTEG-01
              |
              v
       7334b7e5 FE-01
              |
              v
       06a2686f COMPOSE-01
              |
              v
       465e000f QUAL-01
              |
              v
       13473fb6 REVIEW-01
```

The separately gated post-review deployment chain is:

```text
13473fb6 REVIEW-01 -> cc531c74 DEPLOY-H -> 38639a54 DEPLOY-01
```

`cc531c74` is a future verbatim human decision. `38639a54` may act only on
the exact approved bytes and topology. Neither is authorized or eligible now.

The clean docs review and board-enforcement lines are:

```text
433bb97c HANDOFF-03 -> d0c03aae HANDOFF-03R

9dcba975 BOARD-10 -> 2cc820df BOARD-11 -> 54d68b41 BOARD-11R
                                                   |
                                                   v
                          future c1 append of d0c03aae and 54d68b41
```

There is no current formal dependency edge from `d0c03aae` to `2cc820df`.
BOARD-11 owns the dependency-only c1 change after BOARD-10, and its own
criteria govern when reviewed links may replace path-only history. HANDOFF-03
does not mutate the c1 graph. Every claimant must recompute the CardStore fold
after a fresh fetch because the graph is append-only and may grow.

CardStore sources used for the capture include:

- c1 core SHA-256
  `4af8c796acb9f17d08544a91d6339f2684c7e56203273c19aa71225a0507dcc1`
- c1 root criteria and dependency event SHA-256
  `fb5a7ae3ef2adf395d43d4e81a56216b23e16f02a42375bd6a5e39cc0617c589`
- c1 BOARD-05, BOARD-07, BOARD-08, BOARD-09, and BOARD-10 event SHA-256
  values `995c2084bc0b514f97915468cec52e91f963dfe30ca68ce51cc9f07d3ba749a2`,
  `fac9fd79aefae652e4cd4eb6e90ceabfd3a48c50ac41bfc71b738564ade90d21`,
  `43dac6826a2414198b648f71bed2a45c9485855d61e5044b86ab161dfb79cdcd`,
  `427025e58ec563982bc168d2c4a23b31fec681377a642ea331ca19b7ce1752ad`,
  and `b22d885375d0c0027940ea570244feef527ffd4a3f6e635ced90034eabaa462e`
- COMPOSE core SHA-256
  `6a6c8870010ab1eca9b284f77d1ba179df9c0f58969fe71ec6fe659c7612864d`
- COMPOSE latest criteria event SHA-256
  `0e39e8ba3ebc2947f2ea7d1a012439204dd65254f69ef3af83483265ea7a40e6`

## Human blockers and authority

| Gate | Exact human action | Effect |
| --- | --- | --- |
| `71ecf523` | Approve and stage exact offline PostgreSQL 17.7 plus pgvector 0.8.0 substrate with immutable digests and native readback | Allows only local isolated corpus qualification |
| `f1e6d7ab` | Provide immutable, attributable, independently reviewed migration `0020` and `0021` handoffs | Allows the contiguous migration reissue to proceed |
| `0ef6f5f0` | Land the reviewed SKCoord criteria-fold repair through green CI and normal release automation | Allows SKCapstone criteria-fold uptake and its independent review |

None of these actions authorizes deployment, protected Matter traffic,
provider use, credentials, external action, or remote history bypass.

Historical human gate `13cb2667` is safely labeled `superseded` and
`do-not-claim`, links successor `cc531c74`, and grants no authority. Historical
deployment card `e7acf14b` is likewise superseded by `38639a54`. The current
human durable deployment gate is `cc531c74`, depends on final review
`13473fb6`, and is backlog. It still carries stale links to the non-custodied
ARCH-01 path and the generic shared TDD path. BOARD-11 or a later authorized
board-only card must replace those links with reviewed HANDOFF-03 lineage.
Until exact final bytes exist, REVIEW-01 passes, and the human records verbatim
approval, deployment remains prohibited.

## Browser truth

Independent final review `ac4c56f7` recorded PASS for exact shell candidate
`8177c88c`, tree `a23e08dc`, in Chrome `151.0.7922.108`. It recorded 204 web
tests, 138 focused Python tests, typecheck, lint, formatting, build, audit,
diff, ASCII, Chrome, and axe PASS. It observed all 18 sections, direct Matter
reload, compact and expanded layouts, zero axe violations, zero clipped
sections or exact identifiers, zero external requests, empty browser storage,
and sanitized denial and outage recovery.

That same evidence states that recommendation, Agent Run, model routing, Task,
Deadline, artifact ingestion, and external-action capabilities remained inert
or unavailable. Therefore it is evidence for the reviewed presentation shell,
not evidence that the accepted durable lanes are mounted or that a durable
browser path exists.

HANDOFF-03 did not query, start, stop, or mutate any preview or browser
runtime. Real-browser durability remains owned by FE-01, QUAL-01, and
REVIEW-01.

## Coherence findings

- P0: no new product P0 was found in this docs-only reconciliation.
- P1: durable corpus remains blocked on the exact native substrate and
  independent review.
- P1: contiguous migrations remain blocked on human `0020` and `0021`
  custody, corpus review, reissue, and rereview.
- P1: c1 remains blocked on criteria-fold uptake review and BOARD-10 review.
- P2 governance: current human gate `cc531c74` correctly depends on
  `13473fb6`, but its ARCH-01 and TDD links remain stale pending BOARD-11 or a
  later authorized board-only link replacement. The superseded `13cb2667`
  path is already fail-closed and is not an active blocker.
- P2 resolved pending independent review: the component map now states
  accepted-unmounted, blocked, central-adapter-owed, frontend-wiring-owed, and
  post-integration truth instead of calling foundations fully implemented.
- P1 custody resolved pending independent review: exact central and
  HANDOFF-03 TDD sections now have immutable source commit and tree custody.

## Verification

The source candidate passed:

- exact base, source, parent, tree, blob, and all accepted commit and tree
  object checks
- frozen manifest parse with exactly 18 surfaces and 21 operations
- exact 3 + 5 + 2 + 4 + 3 + 2 + 2 operation count sensitivity
- exact 24 direct c1 dependency fold with 20 DONE and 4 OPEN at capture
- current central chain and clean docs chain existence and acyclic graph check
- exact one-instance heading checks for INTEG-01, FE-01, COMPOSE-01, QUAL-01,
  REVIEW-01, HANDOFF-03, and HANDOFF-03R
- `git diff --check`: PASS
- ASCII dash scan of owned files: PASS
- official repository `scripts/check_secrets.py` against the unchanged
  reviewed baseline: `secret scan valid: no findings outside reviewed baseline`
- changed-path scope: exactly the component map and task TDD in the source
  candidate

No baseline change was needed or made.

## Changed files, limitations, and rollback

The source candidate changes only:

- `docs/planning/wireframes/COMPONENT-API-MAP-V2.md`
- `docs/tasks/SUBAGENT-TASK-TTDS.md`

The final evidence-only descendant adds this document. No product source,
migration, manifest, runtime, preview, shared checkout, baseline, remote,
deployment, provider, credential, protected content, HammerTime `Inbox/`,
connector, or external-action state changed.

Rollback reverses only the HANDOFF-03 source and evidence commits to exact
base `0a293dd1afe9d5b4150468057e4ed8e552d7221b`. Board events remain
append-only. No runtime, database, migration, or host rollback exists.
