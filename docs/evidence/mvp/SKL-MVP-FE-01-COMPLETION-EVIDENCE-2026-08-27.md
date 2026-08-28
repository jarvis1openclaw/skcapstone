# SKL-MVP-FE-01 completion evidence

## Identity and exact source

- Card: `7334b7e5`
- Claimed agent: `codex-luna-7334b7e5`
- Worktree: `/tmp/sklegal-7334b7e5-v2-cockpit`
- Required parent commit: `94dcd1f948bc75602b6f33670cf79669efc8db9f`
- Required parent tree: `520e902a6eabe67fe0feec2c7e408b0cd72360be`
- Final source commit before evidence: `94dcd1f948bc75602b6f33670cf79669efc8db9f`
- Final source tree before evidence: `520e902a6eabe67fe0feec2c7e408b0cd72360be`
- Result: `PASS`

The inherited reviewed frontend already satisfied the scoped acceptance
criteria. No source correction was necessary. The only tracked change for
this card is this completion evidence document.

## Scope review

Inspected the assigned frontend scope:

- `apps/web/src/pages/MatterCockpit.tsx`
- `apps/web/src/api/client.ts`
- `apps/web/src/api/types.ts`
- `apps/web/src/router.tsx`
- `apps/web/src/styles.css`
- `tests/fixtures/mvp/public-synthetic-mvp-v1.json`

The cockpit renders exactly the 18 canonical manifest section IDs. Workspace
and Claim data are read through the shared API client. Evidence, Claim,
Work Product, source, and activity views use authorized Matter responses.
Surfaces without a qualified current API remain explicitly labelled as
`proposal` or `safely-unavailable`; their controls are inert and no React-side
recommendation, model result, workflow state, or external effect is invented.

## Automated checks

All commands ran in the pinned worktree against the public-synthetic preview
configuration.

- `npm test --workspace @sklegal/web`: 24 test files, 257 tests passed.
- `npm run typecheck --workspace @sklegal/web`: passed.
- `npm run lint --workspace @sklegal/web`: passed.
- `npm run format:check --workspace @sklegal/web`: passed.
- `npm run build --workspace @sklegal/web`: passed, Vite 8.2.1, 178 modules.
- `tests/qualification/session_reload_csp_qualification.mjs`: `PASS`.

Input hashes:

- `package-lock.json`: `b4da8dc8d7815a19b54bff1ee059c25167485708c0261f40ca6b6df2452e71e8`
- `uv.lock`: `74141a126906894c87ab4e1bf3792bc9d8d7fc74075c7154871a6fe9d41ba21f`
- Built distribution file-list hash: `cfa544dfe6c5a44ca6f29d004f147b5724edc1582eaeceee5adea5c7143802c0`

## Real Chrome browser gate

Browser: `/usr/bin/google-chrome`, headless Chrome through DevTools and
Playwright Core. Preview endpoints were loopback only:

- Web: `http://127.0.0.1:15183`
- API: `http://127.0.0.1:15182`
- Fixture: public synthetic, simulation only

Observed result:

- 18 of 18 section IDs matched the canonical manifest.
- Expanded layout: 1440 client width, 1440 document width, no page or surface overflow.
- Compact layout: 390 client width, 390 document width, no page or surface overflow.
- Keyboard focus was visible at expanded and compact sizes.
- The existing session qualification reported 7 named accessibility groups and 3 labelled compact scroll regions.
- Axe: zero violations.
- Reload: normalized DOM equal and main-cockpit screenshot equal.
- Normalized body hash: `bda63723fac04841593292a7ca38bb8092c5a71175dbe45a2deb32cb060a65d3`.
- Main-cockpit screenshot hash: `086efa073bab85df4694a54cd3a00e0435229aa325634164371d7e0327929046`.
- The raw full-page DOM differs only in the expected random session-correlation UUID rendered by the audit footer. The UUID was normalized only for deterministic visual comparison; it was not removed from the product.
- Forced workspace transport outage rendered the generic sanitized `unknown` error state, with no record detail, and the cockpit recovered after the route was restored.
- Wrong Matter read: HTTP 403, no fixture leakage.
- Wrong tenant header: HTTP 403, no fixture leakage.
- Script-readable local storage, session storage, and cookies were empty.
- CSP: `default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; object-src 'none'`.
- External requests: none.
- Provider requests: none.
- Page errors: none.

The browser gate exercised only public-synthetic simulation. It did not use
protected Matter traffic, providers, credentials, external actions, or
HammerTime Inbox paths.

## Limitations and rollback

The current preview does not authorize durable feature mutations or provider
execution. Recommendation, Agent Run, challenge, deadline mutation, artifact
mutation, and external dispatch remain safely unavailable or proposal-only as
specified by the canonical surface manifest. This card does not qualify or
enable those lanes.

Rollback is a local Git revert of the completion-evidence commit. No service,
database, credential, protected-data, deployment, merge, push, or external
state was changed. Card `431db4dd` was not read, claimed, or modified.

Loopback services were stopped after qualification and the generated
`.venv` symlink was removed. The target worktree is clean before the evidence
commit.
