# SKL-S3-11 PostgreSQL authorization snapshot checkpoint

Date: 2026-08-22

Card: `a060fa3d`

Parent consumer: `72df1b66`

## Durable contract

Migration `0017_skgateway_authorization_snapshot.sql` adds one atomic,
security-definer snapshot for the SKGateway authorization adapter. It uses
the existing current-principal and material-policy snapshot functions and
does not introduce a second policy engine or a new credential store.

The function requires the exact chiap01 service identity, active current
principal subject, tenant and Matter membership, material identifier and
version, legal-research purpose, registered route selector, complete
classification state, and complete ethical-wall state. It derives the
effective classification, protective-label state, and ethical-wall result
from current durable policy state. Caller facts must exactly match the
derived normalized facts or the function denies.

The returned object contains only a revision, service identity, normalized
subject, fixed `skgateway.infer` capability, resource selectors, and derived
context. It contains no prompt, Matter content, source span, raw credential,
private key, or unrestricted policy snapshot.

The per-tenant PostgreSQL runtime provisioner grants this function through
its exact executable-function allowlist. Public execution remains revoked.

## Qualification

The migration manifest contains 17 contiguous, digest-pinned migrations.
The disposable PostgreSQL contract applies the complete sequence through the
real migrator, performs the reversible migration cycle, provisions the
least-privilege runtime role, exercises the successful snapshot, and proves
denial for mismatched classification and service identity.

Final test results and the exact commit are linked to the SKCapstone card.

## Remaining gates

This checkpoint does not activate SKGateway or authorize protected Matter
traffic. Canonical CapAuth and PolicyGateway evaluator composition, durable
decision audit, exact live production route qualification, rollback proof,
and human activation approval remain required.
