# SKL-MVP-02A session reload and CSP evidence

Date: 2026-08-23

Card: `cc214fff`

Disposition: `PASS_READY_FOR_FINAL_V2_SYNTHESIS`

## Immutable candidate

- Base V2 candidate: `48502082fe3be7c115b88937ab222844a4f6f5ff`
- Implementation commit: `0f8f72caab3e666824d4f8ae1da80b872c7a7adf`
- Qualification commit: `9b9e072682b8b5c6e24e9365fc765371efec381b`
- Qualification tree: `d6860d7c3a03c5fd974d4df56acc2b54724a5f24`
- Reusable Chrome qualification SHA-256: `09c600fd1a7abcf868e0844124b2f4a6b385f88e0ded708167cbebb3895e517e`

## Implementation

The web entrypoint now resolves the current same-origin server session before creating the router. Only bounded principal, Tenant, and capability-name identity enters the in-memory usability guard. CSRF, expiry, cookie, and capability material remain outside the route store. An absent, expired, malformed, denied, or unavailable current session clears the store and fails closed.

The HTML no longer attempts to deliver `frame-ancestors` through a meta element. The bounded loopback preview server adds the complete CSP, referrer policy, content-type protection, and no-store response headers to static assets, direct-route fallbacks, API proxy responses, health, and error responses.

## Verification

- Session bootstrap and client tests: `7` passed.
- Complete web suite: `16` files, `199` tests passed.
- Preview launcher tests: `8` passed.
- Preview and browser-session Python tests: `13` passed.
- TypeScript typecheck: passed.
- ESLint: passed.
- Flagged Vite production build: passed, `178` modules transformed.
- Real Google Chrome DevTools qualification: passed.
- Direct authorized Matter reload stayed at `/matters/20000000-0000-4000-8000-000000000101` and rendered `AI Matter cockpit`.
- Browser local storage, session storage, and script-readable cookie state: empty.
- Direct-route CSP header exactly included `frame-ancestors 'none'`.
- Chrome CSP console events: zero.
- `git diff --check`, JavaScript syntax, Prettier, and ASCII dash checks: passed.

The reusable browser lane is committed at `tests/qualification/session_reload_csp_qualification.mjs`. It uses the Node 22 built-in WebSocket and real Google Chrome DevTools Protocol, so it does not depend on an ephemeral browser automation package.

## Safe final state

The qualification instance `cc214fff` was stopped and reset. Loopback ports `15472` and `15473` are closed. The separate owner preview on `15172` and `15173` was not changed.

## Limitation and handoff

This candidate repairs the session and response-header defects on the exact V2 base. Final synthesis must combine it with the enriched API candidate `ca60896554fbe36a64e08d6b81a1aa92e717a0df` and the independent UX repair without changing these browser boundaries, then rerun this real Chrome lane on the exact final candidate.

## Rollback

Revert commits `9b9e072682b8b5c6e24e9365fc765371efec381b` and `0f8f72caab3e666824d4f8ae1da80b872c7a7adf` to restore exact base `48502082fe3be7c115b88937ab222844a4f6f5ff`. No data migration or host rollback exists.
