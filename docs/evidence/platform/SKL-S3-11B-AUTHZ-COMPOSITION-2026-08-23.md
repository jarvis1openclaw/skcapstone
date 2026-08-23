# SKL-S3-11B authorization composition evidence

Date: 2026-08-23 America/Chicago

Card: `a53695e8`

Parent endpoint card: `a060fa3d`

Live-path consumer: `72df1b66`

Upstream wire prerequisite: `b2240a8d`, integrated SKGateway revision
`3cf16fe`

## Outcome

The narrow `/v1/authz/decide` endpoint now has an explicit production
composition revision:
`sklegal-skgateway-authz-production-composition/v1`.

The factory constructs the endpoint only from the canonical
`ProtectedRouteDependency`, PostgreSQL-backed CapAuth principal, revocation,
and replay adapters, `CapAuthCurrentStateVerifier`, `PolicyGateway`,
`PostgresPolicyBackend`, `SkGatewayRoutePolicyVerifier`, and append-only
PostgreSQL audit adapter. It rejects synthetic trusted-issuer state,
non-PostgreSQL audit composition, a noncanonical route verifier, a
non-qualification readiness backend, or any unavailable readiness check.

The binding remains:

- endpoint reference: `SKLEGAL_CAPAUTH_AUTHZ_ENDPOINT`
- bind host: `127.0.0.1`
- transport: `authenticated-local-http`
- service identity: `capauth:sklegal-model-gateway@chiap01.skworld`
- service secret reference: `vault:sklegal/skgateway/authz-service-token`
- service header: `X-SKLegal-Service-Authorization`
- profile enabled: `false`
- protected traffic: `false`

No endpoint address, secret value, credential, protected Matter content, or
provider key was read or recorded by this task.

## Durable current-state binding

Migration 0018 adds a tenant-scoped, forced-RLS policy authorization-use
reservation. `PostgresAuthorizationUseBackend` calls only the controlled
`reserve_policy_authorization_use` function. One canonical CapAuth decision
can bind to one exact PolicyGateway invocation across workers. A replay or an
unavailable result denies.

The request-local credential still passes the existing PostgreSQL CapAuth
principal, revocation, and credential replay checks first. The new reservation
does not replace or fork CapAuth. It closes the downstream current-state reuse
gap inside the existing PolicyGateway.

## Sanitized failure and rollback

Missing service authentication remains 401. Invalid service credential,
service identity, capability, or trusted scope remains 403. Unavailable
credential custody, CapAuth state, trusted snapshot, route policy, material
policy, replay reservation, or append-only audit remains 503 or a sanitized
denial response from the canonical gateway.

`build_skgateway_authz_rollback_router` replaces the decision surface with a
deterministic 503 body containing only
`authorization_backend_unavailable`. Migration 0018 has a narrow reverse
section that removes only its function, policies, and reservation table after
the endpoint has returned to deterministic denial. The disposable PostgreSQL
preflight proved all 18 migrations up, down, and up.

## Exact artifact evidence

- endpoint composition source SHA-256:
  `98b09722eca21e1bb0592cc3daca8aa618eb39af5b687994c16867087713e16f`
- canonical protected-route source SHA-256:
  `33945c9cb06f3f49bcc2dc84241c2ff26c678283fdac61c4fd2f3fd772e5cf3c`
- policy current-state adapter source SHA-256:
  `a7898318f22ce6b47d0ba17080fa7042a5f8910c388e1b35edbff4168c481939`
- append-only audit adapter source SHA-256:
  `a18bc161c78689d9a83902154a98e7fd8e6fd77aa0c3ef8886ff4687d24bc2f6`
- migration 0018 SHA-256:
  `877ca73f682bbec2f3d0b4f33300ca5c18f5a1a3e0615b55d13ab3cf8da227b5`
- composition test source SHA-256:
  `55345e49fd8d76adfbde3b10fdd384fa63d9e59710e1fdd1a925cfc66191e394`
- disabled deployment contract SHA-256:
  `7f3a8b2a4b65026d765b8c4601e9b925520c251fc90be7af51c5b7e686f1a42e`
- upstream wire handoff SHA-256:
  `5c90df03f8a8430e19ce7dcd33a01424f10fc1ae54b57d1f6c1cdf4eba939527`

## Verification results

- New composition suite: 11 passed.
- Focused S3-11, CapAuth composition, CapAuth boundary, PolicyGateway, and
  audit suites: 95 passed and 73 subtests passed.
- Disposable PostgreSQL policy authorization-use test: 1 passed.
- Disposable PostgreSQL isolation qualification after grant allowlist update:
  13 passed and 249 subtests passed.
- Development migration contract: 5 passed and 40 subtests passed.
- Migration manifest validation: 18 migrations valid.
- Focused Ruff check for every changed Python file: passed.
- `git diff --check`: passed.

The broader disposable persistence suite reached 44 passed and 74 subtests
passed, with one unrelated pre-existing domain mapping parity failure. The
repository-wide lint and type checks also retain unrelated pre-existing
failures outside this card. No unrelated source was changed to mask those
failures.

## Remaining gates and limitations

This is composition and disposable qualification evidence, not a chiap01
deployment or live-path report. The exact runtime policy revision, durable
audit event revision, installed upstream package and lockfile hashes, and live
synthetic decision IDs do not exist until card `60cb0c9a` performs isolated
qualification on the exact installed entrypoint. The disabled transport
profile therefore remains disabled, and protected traffic remains denied until
that report and separate human activation approval are recorded.
