# SKL-MVP-COMPOSE-01 completion evidence

Date: 2026-08-27
Card: `06a2686f`
Owner: `codex-sol-06a2686f`
Verdict: PASS

## Exact source

- Required frontend parent commit: `6f323db1d1309dcf15ee0241524aecf4828b609a`
- Required frontend parent tree: `100cfea6c2b98f7f793f5a876fb6e6fbdcf372aa`
- Qualified implementation commit: `fee1514bcaca6b305ae5fb2e8b9ea2697a4f1ece`
- Qualified implementation tree: `2921b158ae9f2823a33b2f71628b915d42ea43cf`
- Qualification JSON SHA-256: `b80c9b7a41f1a9b87a4842728c1abade93001e05d209e9454149fe44f64d4ad8`
- Public-synthetic fixture SHA-256: `4b14516539289255daca9065dd28060a9e96aea365faec9ecbd1a679bdb47742`
- Deterministic projection SHA-256: `90580aa438825b4fcdfcf58a5e3339decdaf1749bcaf23928da55bc414e69e34`

The immutable machine-readable qualification result is
`docs/evidence/mvp/SKL-MVP-COMPOSE-01-QUALIFICATION-2026-08-27.json`.

## Delivered composition

- `sklegal-core-pg` and `sklegal-retrieval-pg` run as separate pinned
  PostgreSQL 17 processes with distinct databases, administrator roles,
  application roles, credentials, volumes, networks, restarts, backups, and
  loopback identities.
- Core owns canonical public-synthetic workspace, Claim, policy, session,
  revocation, replay, append-only audit, outbox, and projection-registry state.
- Retrieval owns only rebuildable full-text and native pgvector projections.
  It cannot own canonical legal, policy, session, audit, or outbox state.
- Production-mode FastAPI uses PostgreSQL-backed session, CapAuth current
  state, replay, revocation, audit, policy-freshness, workspace, Claim, and
  corpus adapters. No in-memory dependency is mounted.
- Capability credentials are minted per request through an ephemeral dedicated
  GPG-backed public-synthetic issuer. Raw capability credentials are not stored
  in PostgreSQL, browser storage, logs, or evidence.
- Human policy mutations and all connector actions remain disabled. The only
  data source is the hash-pinned public-synthetic corpus and all connector
  behavior remains simulated.

## Decisive qualification

The exact sealed drill completed in 14.382 seconds with PASS:

- Real Chrome same-origin session, reload, Matter workspace, Claim, corpus
  search, source span, CSP, compact layout, accessibility, and empty
  script-readable storage: PASS.
- Browser to durable session to CapAuth to current policy to core RLS to
  append-only audit and outbox to governed retrieval projection to UI: PASS.
- Cross-Tenant core RLS denial and cross-Matter retrieval RLS denial: PASS.
- Retrieval outage: canonical workspace continued safely and corpus retrieval
  failed closed with HTTP 503. Recovery passed after independent restart.
- Core outage: health failed closed with HTTP 503. Existing durable session and
  workspace recovered after independent restart.
- Stale policy: health and protected workspace both failed closed with HTTP
  503. Recovery passed after current policy restoration.
- Projection lag left canonical workspace safe. From-scratch rebuild produced
  the exact expected projection hash and duplicate replay retained one row.
- CapAuth revocation state persisted and returned the exact revoked digest with
  revision `ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb`.
- Core backup restored four canonical records in a scratch database. Retrieval
  backup restored one derived projection in a separate scratch database.
- Reset and reseed reproduced the exact fixture and projection pins.
- Fresh-volume migration replay reproduced four records, one outbox event, one
  pgvector projection, and no audit event before browser activity.
- Final safe state proved every task container and task volume absent.

## Regression and supply-chain gates

- Focused Python MVP, API, contract, browser-session, and durable composition
  suite: `85 passed`, with 12 inherited Pydantic schema warnings.
- Web Vitest: `24 files`, `257 tests passed`.
- Web TypeScript, ESLint, Prettier, and production Vite build: PASS.
- Migration manifest: PASS, `28 migration(s)`.
- Fixture safety: PASS, `17 file(s)`.
- Repository secret scan: PASS, no findings outside the reviewed baseline.
- Vulnerability scan: PASS, no known Python or Node vulnerabilities and
  vendored CapAuth verified at 136 files.
- Ruff, Python compile, Compose render, `git diff --check`, and ASCII dash
  checks: PASS.

## Files changed

- Added the dual PostgreSQL Compose topology and isolated initialization SQL.
- Added the durable FastAPI public-synthetic composition.
- Added the complete reversible qualification driver and static contract tests.
- Added an API module override to the existing loopback preview lifecycle.
- Added Psycopg 3.2.13 to the locked API dependency set.
- Classified seven exact machine-evidence digests as reviewed nonsecret hashes
  in the official secret baseline without changing any detector or threshold.
- Added bounded browser diagnostic context and the chiap01 qualification
  runbook entry.

## Limitations

- This candidate is public-synthetic and loopback-only.
- Optional Apache AGE remains activation-gated and is not enabled.
- Connector behavior remains simulated.
- This card does not authorize deployment, non-loopback listeners, protected
  content, provider traffic, credentials from another lifecycle, HammerTime
  `Inbox/`, external actions, merge, push, or card `431db4dd` interaction.

## Rollback

Revert implementation commit `fee1514bcaca6b305ae5fb2e8b9ea2697a4f1ece`
and this evidence-only commit. For any in-progress qualification, run the
driver finalizer or stop the exact `sklegal-06a2686f-*` Compose project and
remove only its two exact named volumes. The final sealed run already proved
no task container, volume, process, or temporary issuer remained.
