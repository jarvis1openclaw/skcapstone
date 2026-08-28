# SKL-MVP-04R1 audit and persistence repair evidence

Date: 2026-08-23
Card: `6f3cd09d`
Disposition: PASS for browser qualification and independent review

## Immutable candidate

- Base: `51cb9c3301c69737bf727c8d46360ab95a9d9e6f`
- Candidate commit: `9d6fad3afd0893a62d7bd0b1138c84a48bdc03c7`
- Candidate tree: `b150cb207af2be09687daf30271b18a16ab8ab63`
- Source archive SHA-256: `7d69811d80a47c689fd1214a22f2d32954eabb5e13f7907e60d85991a7b8da65`
- `uv.lock` SHA-256: `74141a126906894c87ab4e1bf3792bc9d8d7fc74075c7154871a6fe9d41ba21f`
- `package-lock.json` SHA-256: `b4da8dc8d7815a19b54bff1ee059c25167485708c0261f40ca6b6df2452e71e8`
- Migration 0019 SHA-256: `734ee0e00b76778f1e0a3098e77c729d6f9ec5ee803abcb3f77c2acdc8958376`
- Migration manifest SHA-256: `93666474456b984d0bab5139ceda4209d8f520f206583bcdcda8a23f596dedf9`

## Acceptance evidence

- Browser bootstrap, refresh, Tenant switch, and revoke now pass through an injected append-only audit transaction boundary. Public-synthetic audit events contain correlation, operation, outcome, Tenant and principal identity where known, session digest, reason, time, and provenance revision. They contain no cookie, CSRF, capability, or credential value.
- Audit unavailability fails with bounded HTTP 503 before mutation. Production composition rejects the synthetic session and audit implementations and requires them as a pair.
- `SentenceGrounding` now has a declared persistence mapping and migration 0019 with exact Work Product Version and LedgerClaim bindings, append-only enforcement, forced RLS, runtime insert grant, decomposition, reconstruction, fixture parity, and rollback.
- The independent-review Ruff import and formatting failures were repaired without changing their semantics.

## Tests

- Focused API, persistence, and foundation: `42 passed, 112 subtests passed`.
- Broader focused boundary suite before final migration integration: `431 passed, 12 warnings, 308 subtests passed`.
- Full disposable PostgreSQL contract: `46 passed, 75 subtests passed in 100.28s`.
- Full non-integration Python suite: `1859 passed, 920 subtests passed`; five failures were observed. Two migration-documentation failures caused by 0019 were repaired and then passed. Three base or environment failures remain: clean-room `cat` allowlist drift, a pre-existing HammerTime read-only token match, and the absent sibling HammerTime source locator in the isolated worktree.
- Repository lint command: PASS, including Ruff and web ESLint.
- Repository format command completed but reports 17 pre-existing unformatted files outside this repair. Changed Python files are Ruff formatted.
- Web typecheck: PASS. Web Vitest: `13 files, 181 tests passed`. Web build: PASS with 176 modules. NPM audit: 0 vulnerabilities.
- Python dependency audit: no known vulnerabilities; local packages were skipped because they are not PyPI distributions.
- Migration manifest check: PASS, 19 migrations.
- `git diff --check`: PASS.
- Unicode dash check over changed implementation and evidence: PASS.
- Repository secret scan completed with only existing high-entropy fixtures, hashes, and secret-keyword baseline findings. Manual changed-file inspection found no credential, token, cookie, CSRF, or capability value.

## Dependencies and limitations

- Completed card `95af661a`, commit `44b7ce72001aeae5945a215443f3ddbe545b8aee`, tree `810b4c41f676bdbe41d26cca8cd9f2b2e0ce76ce`, supplies component accessibility and responsive evidence only.
- Card `1e39b105` remains unclaimed and must synthesize the exact 95af661a and 6f3cd09d candidates, then perform actual-browser public-synthetic API replay, accessibility automation, keyboard and focus validation, responsive layouts, and pixel screenshots.
- Card `bdb1bd1b` remains unclaimed for independent end-to-end rereview after 1e39b105.
- The in-memory audit sink is a public-synthetic adapter. A durable production audit adapter is not part of this card, and production remains fail closed without one.
- No deployment, service, durable database, credential, protected data, provider request, external action, merge, push, or live consumer change occurred.

## Rollback

Revert candidate `9d6fad3afd0893a62d7bd0b1138c84a48bdc03c7` to base `51cb9c3301c69737bf727c8d46360ab95a9d9e6f`. If migration 0019 has been applied only in an authorized disposable or future deployment, its down section removes the SentenceGrounding index, policies, triggers, and table. This card performed no live data or host mutation.
