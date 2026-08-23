# SKL-S3-11 SKGateway wire and startup integration handoff

Date: 2026-08-23 America/Chicago

Cards: `b2240a8d`, `171315a5`
Upstream repository: `skgateway`
Integrated upstream revision: `3cf16fe`

## Scope

This handoff records source-level integration only. It does not activate the
SKLegal gateway profile, create credentials, send protected Matter traffic, or
process HammerTime Inbox material.

## Implemented upstream controls

- Separate `X-SKLegal-Service-Authorization` service credential and request-local `Authorization` credential.
- Exact trusted qualification scope: Tenant, Matter, material, material version, route, purpose, classification, privilege, and ethical-wall fields.
- Caller-supplied scope is not trusted. Qualification scope comes from explicit trusted process configuration and missing scope fails closed.
- Governed qualification routes do not use the legacy internal-peer bypass.
- Governed qualification decisions do not use an allow cache.
- Bounded response validation accepts only allow, reason, decision, policy, correlation, and obligations fields.
- Dashboard and metrics startup honors explicit disabled configuration.
- Disabled discovery cannot be force-refreshed through the qualification profile.

## Verification

On the clean upstream branches before merge:

- Wire worker: `npm test`: 1,349 passed, 0 failed.
- Runtime worker: `npm test`: 1,339 passed, 0 failed.

After integration on upstream `main`:

- `npm test`: 1,393 passed, 0 failed.
- `git diff --check`: passed.
- The integrated main branch was pushed after rebasing the pre-existing provider work onto latest `origin/main`.

## Remaining qualification gates

Source tests are not live chiap01 evidence. The following remain required:

- compose the actual SKLegal adapter endpoint with durable CapAuth, policy, replay, revocation, route, and audit dependencies;
- pin the exact installed source, package, lockfile, endpoint, service identity, policy revision, and audit revision;
- run the actual installed route with synthetic canonical allow, deny, unavailable, malformed, leakage, saturation, restart, parity, and rollback tests;
- independently review the complete live-path report;
- retain protected traffic denial until separate human activation approval.
