# SKL-MVP-QUAL-01 blocked evidence

Date: 2026-08-27
Card: `465e000f`
Owner: `codex-sol-465e000f`
Verdict: BLOCKED

## Exact candidate

- Candidate commit: `464e6b7de45dd51c93eb68d9edcbf95c934ca7fb`
- Candidate tree: `4d55cfbd93127683a534941f8c38dfe3b995416d`
- Implementation commit: `fee1514bcaca6b305ae5fb2e8b9ea2697a4f1ece`
- Implementation tree: `2921b158ae9f2823a33b2f71628b915d42ea43cf`
- Predecessor evidence bundle: `5186d8acc4d5584b0afb0351fa725bc05f7699828f717c5aedf0cc8fe18d676d`
- Predecessor qualification SHA-256: `b80c9b7a41f1a9b87a4842728c1abade93001e05d209e9454149fe44f64d4ad8`

The worktree was clean at the exact candidate before qualification. Dependency
`06a2686f` was DONE with verdict PASS. The approved dual PostgreSQL topology was
read from `docs/approval/AMENDMENT-SKL-S2-10.md`.

## Recomputed preflight pins

- Public-synthetic fixture SHA-256: `4b14516539289255daca9065dd28060a9e96aea365faec9ecbd1a679bdb47742`
- V2 contract aggregate SHA-256: `af766d16974506f14a4242a8016018c2a7ca1c1d891f477fc6af6a9245cc5bac`
- Migration SQL aggregate SHA-256: `be4e14723627eca23638b10da6dd3ad438db18045e5b98207e7ba515023fae1b`
- Durable topology aggregate SHA-256: `094c4e49ce3b9aa03d85be350aaa5ead8d24ae94d0d407d73e746ff5f5efed61`
- First-pass web build aggregate SHA-256: `cfa544dfe6c5a44ca6f29d004f147b5724edc1582eaeceee5adea5c7143802c0`
- Dual PostgreSQL approval SHA-256: `3fce310da887cc91c7b34bbcff6dbeb1e28c00fd941bb04d8a6ee30e3091225c`

## Decisive first-pass failure

Command:

```text
python scripts/qualify_durable_mvp.py --output docs/evidence/mvp/SKL-MVP-QUAL-01-RUN-1-2026-08-27.json
```

Runtime versions:

```text
Docker Compose 2.40.3
Google Chrome 151.0.7922.173
Node.js 22.23.2
npm 10.9.8
```

The real Chrome journey established the public-synthetic session and navigated
to exact Matter `44444444-4444-4444-8444-444444444441`. The page then rendered
`Access not permitted` instead of the expected `AI Matter cockpit`.

Exact observed browser excerpt:

```text
expected AI Matter cockpit, received undefined at
/matters/44444444-4444-4444-8444-444444444441

Access not permitted
You do not have access to this record.
Correlation: f16f5a34-6944-44d2-90d0-2218ffc4fdf0
Session correlation: eb2ca9d0-587a-4223-b3a1-07956aeba128
```

The API process started successfully and reported application startup complete.
The failure occurred at the authorized synthetic Matter access boundary after
sign-in. This is a product qualification defect or an unresolved deterministic
authorization-state defect. Card `465e000f` prohibits product repair, so the
qualification stopped immediately without a retry.

## Unreached acceptance criteria

The second deterministic Chrome pass and the remaining full qualification
sequence were not run. No PASS is claimed for intake, analysis, Agent Run,
recommendation, challenge, Task, Work Product, Approval, simulation handoff,
activity replay, restart, outage, backup, restore, reset, rollback, visual
determinism, or complete hash reconciliation under this card.

## Safe final state

- No task-created Docker container remained.
- No task-created Docker volume remained.
- No task-created API, preview, Chrome, or PostgreSQL process remained.
- No task-created loopback listener remained.
- Only qualification evidence is retained in the worktree.
- No protected content, provider request, credential access, HammerTime
  `Inbox/` access, external action, merge, push, broad cleanup, or interaction
  with card `431db4dd` occurred.

## Required successor chain

Create one bounded repair card for the durable public-synthetic session to
Matter authorization failure. After the repair publishes exact source and test
evidence, run a distinct independent rereview that reproduces this failure and
returns PASS or FAIL. Card `465e000f` remains visibly BLOCKED until that chain
passes and a fresh qualification successor is eligible.
