# SKL-S3-11 canonical policy evaluator checkpoint

Date: 2026-08-22

Card: `a060fa3d`

Parent consumer: `72df1b66`

## Implemented boundary

The SKGateway authorization endpoint now composes two distinct credentials.
`X-SKLegal-Service-Authorization` authenticates the deployment-pinned
SKGateway service. The standard `Authorization` header is reserved for the
request-local CapAuth credential consumed by the existing
`ProtectedRouteDependency`. Service authentication completes before the
protected route can reserve a credential.

The canonical evaluator accepts only the resulting `AuthorizedContext`. It
requires exact subject, model audience, tenant, Matter, material identifier,
material version, material digest, purpose, model route, workflow run, and
external `skgateway.infer` scope. It derives the model-context
`PolicyAccessRequest` and `PolicyBoundaryRequirement` from the signed CapAuth
grant, then calls the existing `PolicyGateway`.

`PolicyGateway` remains responsible for durable current-state reservation,
replay and expiry checks, exact grant matching, membership, conflict,
privilege, ethical-wall and classification evaluation, and durable policy
decision audit. The adapter maps its result to the bounded response contract
without returning credentials, prompts, Matter content, source spans, or
policy internals.

## Tests

Focused tests cover the authenticated endpoint, service-before-CapAuth
ordering, preserved CapAuth denial, exact grant-to-policy binding, wire scope
mismatch, policy denial, replay denial, audit outage, configuration parsing,
unknown fields, oversized selectors, and response sanitization.

The focused CapAuth, policy, model-gateway, and endpoint run passed 110 tests
and 42 subtests. The migration execute-inventory qualification passed 1 test
and 6 subtests after the migration 0017 function was added to the pinned
shared-runtime allowlist.

The full repository run reached 1819 passing tests, 15 skipped tests, and 1282
passing subtests. Its initial 10 failures included the now-corrected migration
allowlist omission. The remaining failures are outside this card: running
development containers, clean-room utility drift, missing test-environment
packages, an unavailable external HammerTime source locator, and a vendored
CapAuth environment-path mismatch.

## Remaining gates

This checkpoint does not activate the disabled deployment profile. Production
composition still requires durable dependency wiring on chiap01, live-path
allow and deny qualification, exact revision and test hashes, rollback proof,
and human approval. Protected Matter traffic remains denied until those gates
are complete.
