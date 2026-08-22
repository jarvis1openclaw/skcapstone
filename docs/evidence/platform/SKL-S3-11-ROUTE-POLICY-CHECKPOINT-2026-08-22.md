# SKL-S3-11 route policy checkpoint

Date: 2026-08-22

Card: `a060fa3d`

Parent consumer: `72df1b66`

## Implemented boundary

The SKGateway trusted-facts resolver now requires a canonical route-policy
verifier before any facts reach the authorization evaluator. The verifier
reuses the existing SKLegal model route registry, transport profile store,
and security-policy egress gate.

An authorization attempt fails closed when the exact route is absent or
disabled, the transport profile is absent, stale, disabled, or not an
SKGateway binding, the service identity or `skgateway.infer` capability does
not match the pinned profile, the classification is unknown, or the route
classification ceiling and egress policy deny the request.

A successful check returns revision evidence for the route registry,
transport profile store, and egress policy. It returns no prompt, Matter
content, source span, raw credential, or policy internals.

## Verification

Focused route and API tests:

```text
82 passed, 14 subtests passed
```

The final combined test result and commit are linked to the SKCapstone card.

## Remaining gates

This checkpoint does not activate SKGateway and does not authorize protected
Matter traffic. The durable PostgreSQL trusted snapshot function, canonical
CapAuth and PolicyGateway evaluator composition, durable audit evidence, live
production route allow and deny qualification, rollback proof, and human
activation approval remain required.
