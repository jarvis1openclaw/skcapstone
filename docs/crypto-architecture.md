# SKLegal cryptography architecture

## Maturity and assurance

SKLegal 0.1.0 is **T0 Classical** and experimental. It has not received an independent
cryptographic audit. Tests establish only the named interoperability and denial-path
properties.

## Current surfaces

| Surface | Current mechanism | Ownership | Claim boundary |
|---|---|---|---|
| Capability credentials | CapAuth OpenPGP detached signatures | CapAuth library and dedicated SKLegal issuer policy | Classical authenticity only |
| Issuer policy governance | Detached Casey OpenPGP signature over canonical JSON | SKCapstone identity custody and SKLegal deployment policy | Classical authenticity only |
| PostgreSQL transport | Deployment TLS configuration when activated | Deployment operator | Separate from capability signatures |
| Browser and API transport | Future approved unified ingress | Deployment operator | Not activated by repository version 0.1.0 |
| Data at rest | PostgreSQL and host storage controls | Deployment operator | No application-layer encryption claim yet |

The dedicated SKLegal issuer public fingerprint is declared in
`deploy/chiap01/issuer-policy/trusted-issuers.json`. Private keys and passphrases are
never repository inputs. Signing occurs on the authorized custody host and transfers
only public manifests and detached signatures.

## Algorithm posture

The current OpenPGP keys are RSA-based. SKLegal does not claim a hybrid KEM, hybrid
signature, post-quantum transport, or T1 through T4 maturity. Algorithm replacement
requires a versioned credential format, dual-read migration where needed, exact policy
rollout and rollback, interoperability tests, and updated evidence before activation.

## Verification

Repository gates verify the public policy/signature pair is present, scan for secrets,
exercise CapAuth parsing and authorization denial paths, and run isolated synthetic GPG
tests. Production custody, rotation, revocation, backup recovery, and issuer activation
remain separately authorized operational actions.
