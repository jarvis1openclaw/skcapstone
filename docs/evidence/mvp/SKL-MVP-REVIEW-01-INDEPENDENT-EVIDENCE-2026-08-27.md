# SKL-MVP-REVIEW-01 independent evidence

Card: `13473fb6`

Reviewer: `codex-sol-13473fb6`

Verdict: `PASS`

Recorded at: `2026-08-27T22:48:56Z`

## Exact custody and independence

- Candidate commit: `b8836c91a8337d125a86fc06fd87c270ba176b2e`
- Candidate tree: `c1f98547a2337c34e2248b0d77d89e299e3d122c`
- Candidate archive SHA-256: `33d08bbb5f9f3628ac43016343277035af829d98ed24a4796ab82a531a8cd8d6`
- Q3 evidence bundle SHA-256: `90072c68de6397cac2fed08101b0ecdbd01d678be0d2db769bcfabec648ddfa6`
- Implementation commit: `89c5e0e77bf2fcc362d66442b961439528583ace`
- Implementation tree: `dbf076fca56794efae6278c8c3cba5921f9b28f3`
- Implementation archive SHA-256: `82d709c35551120facaae1abed2bb1c0beaa8c0b04eb8750d970a12e2fa2833b`
- Preserved failed review commit: `9b21c8878b9e62faf46b1145293218181c64a4a2`
- Preserved failed review tree: `b060621ab012fae9054961f16abdce3593390d20`
- Preserved failed review archive SHA-256: `fc7b107e3a6f3528d40ac1fa7c474b4075fc2344ff210beb8da667a9e4476c88`

This review did not alter product source, tests, scripts, migrations,
configuration, or existing evidence. The original BLOCKED evidence remains
unchanged and visible. Only the six new review evidence files named below are
part of this review.

## Recomputed contract, migration, data, and build pins

- Frozen V2 surface manifest: `0857b0642c49531ae615362d7a40785e21aa8676a933ea6a27a5f1f057e11c66`
- V2 contract aggregate: `af766d16974506f14a4242a8016018c2a7ca1c1d891f477fc6af6a9245cc5bac`
- Live canonical OpenAPI: `1555cec46c8143e0c4f69210b8ccf0f91ea90a9cea62166d645893462fb820b6`
- Core migration manifest: `08c517e51124d68585ba16fef9791afb5a73c90f883b128790327ad0bc899c35`
- Retrieval migration manifest: `9d5b0c8118db9d4b56b9fc3ec432800833bcc70f9693bc9cc7bf323215f4cdf6`
- Core migration SQL aggregate: `be4e14723627eca23638b10da6dd3ad438db18045e5b98207e7ba515023fae1b`
- Core bootstrap: `9099bbbe4a79954f49d4f9d8e0d85aa506debddbc848d2b9d915a2a8d7e3b90b`
- Retrieval bootstrap: `638143b357337cd2e9dc1ad1040b90a260da9102b64ec107c2000f4256b6974f`
- Ordered manifests and bootstraps: `e269a2358a5c032996e64b5e070a25967639a530679a364fac64725b681248cd`
- Public-synthetic seed: `4b14516539289255daca9065dd28060a9e96aea365faec9ecbd1a679bdb47742`
- Deterministic web build aggregate: `889f45fccfc0953a23cca62bd60b63b802e34d8a63ad96a228cd8b80e4517ebf`
- Package lock: `b4da8dc8d7815a19b54bff1ee059c25167485708c0261f40ca6b6df2452e71e8`
- UV lock: `a77c624eae87f9908127c7af695ac1275eeab18efc519ab66185227fbc80d458`

Two consecutive standalone production builds produced the same aggregate.
The migration checker accepted 28 core migrations, and the fixture checker
accepted 17 public-synthetic fixture files.

## Independent durable qualification

The unmodified stock qualifier ran first. A second full qualifier used the
unmodified stock browser check followed by a temporary, uncommitted reviewer
extension for fresh Axe and PNG capture. Both runs used separate real core and
retrieval PostgreSQL, random loopback ports, random project names, fresh
credentials, isolated networks, and isolated volumes.

1. Stock run: `PASS` in `22.228s`.
   SHA-256: `3fd81b74e3321b64b80c813ad302964a369adcd5573e1a415c2fcbecd66e80a3`.
2. Extended browser run: `PASS` in `26.089s`.
   SHA-256: `d60db062ad744c281825783ec59a0eb8e84942018cab6bef789609e3b66908bc`.

After removing durations, random ports, backup dump hashes, the random
revocation revision, the added browser evidence, and the three additional
authorization audit rows caused by that added browser session, both results
normalize to SHA-256
`39c072709ec9fd8fa3bbf5d1df4411a64282031214e4cc72c02190af0f641842`.

Each run proved all 21 frozen operations returned HTTP 200 or 201. It also
proved exact method, path, and operation ID inventory; durable authorization;
Tenant and Matter isolation; revoked-session denial; RLS; audit and outbox
readback; idempotency; restart and outage behavior; stale-policy fail-closed;
projection lag safety and deterministic rebuild; core and retrieval backup and
restore; reset and reseed; fresh migration replay; rollback; and runtime safe
state. The added browser journey caused exactly three additional authorization
audit rows, from 41 to 44, and the final audit count increased from 48 to 51.

## Fresh Chrome, Axe, and visual resolution

Google Chrome `151.0.7922.173` rendered all 18 expected surfaces. Real-browser
checks passed direct route sign-in, session continuity, CSP, empty script-
readable storage, keyboard-visible focus, zero external requests, zero failed
responses, zero page errors, 1440 pixel layout without overflow, 390 pixel
layout without overflow, and three labelled compact scroll regions.

Axe `4.13.0` ran fresh with `wcag2a`, `wcag2aa`, `wcag21a`, and `wcag21aa`.
It returned zero violations and 28 passed rules. Two manual-review results were
resolved without weakening the criteria:

- `aria-prohibited-attr` marked five focusable `code` identifiers incomplete.
  Chrome's full accessibility tree exposed all five as non-ignored `code`
  nodes with their exact labels, while their exact values remained visible,
  selectable text nodes. Keyboard focus was visible.
- `color-contrast` marked one non-text status glyph incomplete. The glyph is
  `aria-hidden=true`, every observed glyph has an adjacent visible text label,
  and the positive foreground and background pair has a 6.49:1 contrast ratio.

The latest screenshots were independently inspected and showed no clipping,
missing surface, global overflow, or obscured content:

- Expanded PNG SHA-256: `83fea41f29c9dbf722aac622ad916903c315a7dfe0b4d61383f12bfb7772216d`
- Compact PNG SHA-256: `90b31a6a0e5befdf9c17ee845b23ac8b75b0b9846af735bd99d36b3113d198cd`

Q3's lack of fresh Axe injection and retained PNG is therefore closed by this
independent evidence. No inherited browser claim was used as a substitute.

## Exact tests and static checks

- Request capability and composition: `20 passed in 0.80s`.
- CapAuth, API, Agent Run, and Matter activity: `174 passed, 97 subtests passed in 17.02s`.
- Durable feature owners: `348 passed, 1 inherited warning in 71.78s`.
- Web: `24 files passed, 258 tests passed`.
- Web lint and typecheck: `PASS`.
- Migration manifest: `PASS`, 28 migrations.
- Fixture safety: `PASS`, 17 files.
- Ruff lint: `PASS`.
- Ruff format over the exact 17 changed Python files: `PASS`.
- Python compilation over the exact 17 changed Python files: `PASS`.
- Implementation diff, ASCII dash, and forbidden-card checks: `PASS`.
- Frozen-operation registry mypy with local sources and skipped dependency
  bodies: `PASS`.
- Changed implementation secret scan: `PASS`, zero findings.
- Secret negative controls: `4 passed, 6 subtests passed`.

The durable suite warning is the inherited Starlette 422 constant deprecation.

## Evidence files and aggregate

- `SKL-MVP-REVIEW-01-RUN-1-2026-08-27.json`
- `SKL-MVP-REVIEW-01-RUN-2-2026-08-27.json`
- `SKL-MVP-REVIEW-01-EXPANDED-2026-08-27.png`
- `SKL-MVP-REVIEW-01-COMPACT-2026-08-27.png`
- `SKL-MVP-REVIEW-01-INDEPENDENT-EVIDENCE-2026-08-27.md`
- `SKL-MVP-REVIEW-01-RESULT-2026-08-27.json`

The sorted four-file runtime and visual evidence aggregate is
`e86f8855b1ac85d4118fe6569331c9fd9b80ede03e3e44a1a827fe9cab1c10e5`.

## Limitations

- The corpus is public synthetic only.
- Connectors remain simulation-only.
- Optional AGE remains activation-gated.
- The official repository secret gate remains inherited red on evidence hashes
  and metadata. Changed implementation files have zero findings, and planted
  secret controls pass. No baseline or detector was changed.
- The six new review evidence files produce 47 high-entropy findings, all
  immutable content hashes, plus one metadata keyword for the false
  `credential_access` safe-state field. No credential, token, cookie,
  password, private key, or provider secret detector fired.
- Expanded mypy over all 17 changed Python files reports 12 inherited errors
  in 6 files. The security-critical frozen-operation registry passes its
  focused mypy check.
- Web Prettier remains inherited red only on `apps/web/src/api/client.ts`.
  Web tests, lint, typecheck, visual checks, and deterministic builds pass.

None of these limitations changes the runtime, authorization, isolation,
accessibility, data durability, recovery, rollback, or safe-state result.

## Rollback and prohibited-boundary proof

Evidence rollback is a Git revert of the review evidence commit. Runtime
rollback stops only the exact random qualification project and removes only
its two named volumes. Every terminal run recorded task containers and volumes
absent.

All traffic was loopback and public synthetic. No protected content, provider
request, credential access, HammerTime `Inbox/` access, external action,
deployment, merge, push, broad cleanup, or mutation of card `431db4dd`
occurred.
