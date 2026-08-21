# SKL-S1-01 completion evidence

Card: `9df32976`  
Implementer: `codex-domain`  
Date: 2026-08-20  
Status: Ready for review

## Scope delivered

- Added strict, frozen Pydantic value objects and entity bases.
- Added typed legal-domain entities listed by the approved TDD.
- Added deterministic, revalidated state transitions with immutable identity.
- Added UTC effective-time behavior and explicit unknown-time handling.
- Added strict, lossless HammerTime legacy aliases without canonical ITIL entities.
- Added exact-version approvals with immutable decision and separate revocation
  evidence.
- Added typed execution gate snapshots, exact append-only event transitions, and
  event-plus-receipt completion evidence.
- Closed unvalidated Pydantic copy, replacement, and construction bypasses.
- Added explicit review resets that prevent changed work products or communications
  from retaining stale validation, approval, destination, or execution evidence.
- Added generic owner-audit bounds for embedded source provenance and exhaustive
  coverage across every current source-bearing aggregate.
- Added owner-audit bounds for nested execution gates, events, and receipts plus an
  exact receipt-ID-to-event-step invariant.
- Added fixture-only unit and cross-aggregate integration coverage.
- Added the package contract, limitations, and exact code rollback procedure.

## Acceptance evidence

| Acceptance requirement | Evidence |
|---|---|
| Typed legal-domain models | `packages/domain/src/sklegal_domain/entities.py` and public exports |
| Valid and invalid transitions | `tests/test_domain_entities.py` |
| Effective-time behavior | `ValueObjectTests` and fact effective-time tests |
| Immutable identity and provenance | aggregate, copy-bypass, alias, evidence, approval, event, and receipt tests |
| Strict legacy aliases | `LegacyAliasTests` |
| No canonical ITIL public entities | unit and integration public-contract tests |
| No database or UI coupling | integration AST import-boundary test |
| Every domain entity constructs and round trips | 32-record synthetic graph integration test |

## Verification results

Focused verification at the corrected source checkpoint:

```text
PASS: 76 domain unit tests
PASS: 4 domain integration tests
PASS: Ruff format and lint for domain source and tests
PASS: mypy for all 6 domain package modules
PASS: independent review, 45 adversarial probes with no findings
```

Final full-repository verification from the independently accepted source:

```text
PASS: make check
PASS: approximately 31.0 seconds elapsed
PASS: 189 Python unit tests
PASS: 1 frontend Vitest
PASS: 9 integration tests
PASS: Ruff format and lint, mypy 20 source files, frontend typecheck and build
PASS: five approved design hashes
PASS: Compose dry run with no retained development services
PASS: doc.haus no-copy audit, 10 decisions, 3857 package records,
      2889 CycloneDX components, 40 implementation files, 193718 token windows
PASS: CycloneDX schema validation and SBOM generation
PASS: migration manifest with 0 migrations and fixture safety with 2 files
PASS: secret scan with no findings outside the reviewed baseline
PASS: Python and Node vulnerability audits with 0 known vulnerabilities
```

Final clean-room verification from an isolated source copy:

```text
PASS: make clean-room-check
PASS: approximately 106.18 seconds elapsed
PASS: 101 source files copied and verified
PASS: fresh pinned uv 0.12.5 bootstrap and 92-package Python environment
PASS: fresh 318-package Node installation
PASS: 189 Python unit tests
PASS: 1 frontend Vitest
PASS: 9 integration tests
PASS: the same format, lint, type, build, design-hash, provenance, SBOM,
      migration, fixture, secret, and vulnerability gates as the normal run
PASS: temporary directory .sklegal-cleanroom-n59h1a28 removed automatically
PASS: no sklegal-dev containers retained
```

## Data and environment assurance

- Test inputs are wholly synthetic and live under `tests/fixtures/domain` or test
  factories.
- No HammerTime path or Inbox was read or written.
- No protected matter or production data was used.
- No database, container, service, external account, connector, or deployment was
  created or changed.
- No commit or push was performed.
- The approved architecture hash files are unchanged.

## Known limitations

- Persistence, RLS, and concurrency enforcement belong to `SKL-S1-02`.
- CapAuth enforcement belongs to `SKL-S1-03`.
- Conflicts, privilege, walls, retention, and holds belong to `SKL-S1-04`.
- Append-only durable audit and outbox delivery belong to `SKL-S1-05`.
- Legacy import and alias-attachment workflows belong to Sprint 2.
- External action connectors and operational receipts belong to Sprint 4.

## Rollback

This card has no data migration or external-state rollback. Apply the source-only
rollback documented in `docs/development/DOMAIN.md`, then run `make check`. The
existing foundation and lockfiles remain valid.
