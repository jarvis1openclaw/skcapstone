# SKLegal

**SKLegal is a governed, multi-tenant legal operations platform for the SK sovereign stack.** It provides a legal matter workbench, deterministic policy and persistence boundaries, and typed agent workflows while preserving HammerTime source provenance.
**Maturity tier:** T0 Classical for the signed capability surface. No post-quantum protection is claimed.
**Version:** 0.1.0, Active v2 development line.

> **Experimental and unaudited.** SKLegal and its CapAuth integration have not
> undergone an independent third-party security or cryptographic audit. The test
> suite provides behavior and interoperability evidence, not an assurance guarantee.

## Quickstart

```bash
./scripts/bootstrap.sh
make check
make dev-deps
```

The development PostgreSQL and Temporal ports bind only to loopback. Stop them with
`make dev-deps-down`.

## Approval and current scope

The human owner approved the architecture on 2026-08-19. Implementation proceeds
through eligible, explicitly claimed SKCapstone cards and their dependency and human
approval gates.

The repository contains the engineering foundation, legal domain, PostgreSQL
persistence and row-level security, CapAuth authorization adapter, policy engine,
append-only audit and outbox contracts, and development deployment definitions. It
does not claim that the complete product workbench, production connectors, or pilot
migration is active. External actions remain simulation-first and separately gated.

## Documentation

- [Operational SOP](SOP.md): architecture, build, test, deployment, configuration,
  API entry points, troubleshooting, and executable documentation evidence.
- [Project specification index](docs/PROJECT-SPEC.md): approved designs, sprint plan,
  task specifications, and current implementation contracts.
- [High-level technical design](docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md).
- [Pilot migration technical design](docs/architecture/LIBERTY-AUTO-PILOT-TDD.md).
- [Security policy](SECURITY.md) and [threat model](docs/security/THREAT-MODEL.md).
- [Cryptography surface inventory](docs/crypto-architecture.md).
- [Contributing guide](CONTRIBUTING.md), [Code of Conduct](CODE_OF_CONDUCT.md), and
  [changelog](CHANGELOG.md).
- [Architecture approval](docs/approval/ARCHITECTURE-APPROVAL.md) and
  [approved design hashes](docs/approval/DESIGN-HASHES.sha256).
- [Foundation](docs/development/FOUNDATION.md),
  [persistence](docs/development/PERSISTENCE.md),
  [CapAuth](docs/development/CAPAUTH.md),
  [policy](docs/development/POLICIES.md), and
  [audit](docs/development/AUDIT.md) development contracts.

## Core product decisions

| Decision | Current value |
|---|---|
| Product | SKLegal |
| License | GNU GPL version 3 only |
| Initial deployment host | chiap01 |
| Local model route | Qwen3 on chiap08 |
| Optional external model route | OpenAI API through the governed provider adapter |
| Initial source corpus | Existing HammerTime releases and processes |
| Pilot migration | One Liberty Auto Plaza matter as the verified pattern |
| Authorization | CapAuth plus independent SKLegal policy gates |
| External actions | Simulation-first, approval-gated, and receipt-verified |

## Honest security and cryptography posture

- Capability credentials use versioned CapAuth envelopes and detached classical
  OpenPGP signatures. The current dedicated SKLegal issuer is RSA-based.
- The repository has no application-layer hybrid KEM or hybrid signature suite and
  therefore remains T0. It does not claim T1 crypto agility, T2 hybrid KEM, T3 hybrid
  signatures, or T4 transport closure.
- Models produce typed proposals. Deterministic reducers and authorized humans own
  mutations, approvals, and external actions.
- HammerTime remains the source and provenance owner for the initial corpus. SKLegal
  does not write arbitrary HammerTime paths.
- A green test run proves the named checks passed. It does not prove the absence of
  vulnerabilities or establish legal correctness.

## Related projects / See also

- **Depends on:** [CapAuth](https://github.com/smilinTux/capauth) for signed,
  scoped capability credentials and verification primitives, vendored under
  `vendor/capauth` (see `docs/development/CAPAUTH.md`).
- **Integrates with:** the separately governed HammerTime corpus through source and
  release adapters. The historical `hammerTimeOS` proposal is retained as
  [legacy provenance](docs/legacy/hammerTimeOS-2026-08-19/README.md), not as a second
  product or repository.
- **Coordinated by:** [SKCapstone](https://github.com/smilinTux/skcapstone) for
  engineering cards, change evidence, and agent continuity.
- **Standards:** [sk-standards](https://github.com/smilinTux/sk-standards) for
  repository documentation, testing, security disclosure, cryptography, and
  architecture conventions.

License: [GNU GPL version 3 only](LICENSE).
