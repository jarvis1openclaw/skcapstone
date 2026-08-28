# SKL-MVP-02 bounded browser sessions evidence

Card: `431c4fbf`

## Scope

This change wires the React shell to the reviewed internal MVP API through a same-origin, server-verifiable session. All implementation and tests use deterministic public-synthetic identities and records. No host deployment, protected Matter content, provider request, credential issuance, external service, or external action occurred.

## Contract evidence

- Session routes: `POST /v1/session/bootstrap`, `GET /v1/session`, `POST /v1/session/refresh`, `POST /v1/session/tenant`, and `DELETE /v1/session`.
- Browser authentication uses the `__Host-sklegal_session` cookie with `Secure`, `HttpOnly`, `SameSite=Strict`, path `/`, and a 30-minute maximum age.
- The browser receives session metadata and a CSRF value, but never raw CapAuth capability material. Raw CapAuth values remain in server-side session state and are selected only for an exact HTTP method and route path.
- API requests use `credentials: same-origin`, exact `X-SKLegal-Tenant`, and a fresh `X-Correlation-ID`. State-changing requests also carry `X-CSRF-Token`.
- The API rejects cross-origin requests by default, does not emit CORS allow headers, validates Tenant context against server session state, and preserves server-side CapAuth authorization before resource lookup.
- Session expiry, server revocation, refresh denial, wrong Tenant, absent CSRF, and missing capability fail closed.
- Tenant changes rotate the server session. The React provider clears the protected query cache on sign-in, Tenant switch, denial, and sign-out.
- Multi-tab sign-out uses a value-free localStorage notification only. No session, CSRF, Tenant, Matter, capability, or protected data is written there.
- The API base is `VITE_SKLEGAL_API_BASE` with `/api` as the same-origin default. No private host is hard-coded.
- CSP is present in the HTML shell and every API response. API errors and outages map to bounded codes without response detail or transport detail.

## Test results

Commands run from `/tmp/sklegal-mvp-431c4fbf`:

```text
npm --workspace apps/web run typecheck
PASS

npm --workspace apps/web run lint
PASS

npm --workspace apps/web test
13 test files passed, 180 tests passed

PYTHONPATH=services/api/src:packages/capauth/src:packages/policies/src:packages/audit/src:packages/domain/src:packages/migration/src:packages/model_gateway/src:packages/retrieval/src python -m pytest tests/test_browser_sessions.py tests/test_api_app.py tests/test_api_workspace.py tests/test_api_claims.py tests/test_api_corpus.py tests/test_api_governance.py -q
66 passed, 12 pre-existing Pydantic OpenAPI warnings
```

Coverage includes sign-in, current session, sign-out, expiry, revocation, refresh denial, wrong Tenant, CSRF denial, same-origin cookies, correlation propagation, no bearer material in browser requests, API outage sanitization, 401 and 403 mapping, direct route guards, CSP, CORS denial, shell rendering, and query cache invalidation contract.

## Rollback

Revert the SKL-MVP-02 commit to restore the fixture-only development shell at immutable predecessor `4db104b8a4500f2a10c7d0bf863572cec551bb09`. No migration or data rollback is required. Server sessions are in-memory public-synthetic development state and disappear when the process stops.

## Known limitations

- This card defines the durable backend protocol but includes only the explicit public-synthetic in-memory backend. Production composition rejects that backend. A durable approved credential-reference adapter remains deployment work.
- Browser tests use Vitest and FastAPI TestClient rather than a full Playwright browser. Router, history-safe credential posture, storage leakage, multi-tab notification, and live API behavior are covered at their component and HTTP boundaries.
- Multi-tab sign-out signaling is best effort. Server revocation is authoritative, so another tab fails its next API or session refresh even if the storage event is unavailable.
- CSP enforcement at the reverse proxy remains a deployment responsibility. The application emits the policy in both API headers and shell metadata.
