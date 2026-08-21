# SKL-S0-04 completion evidence

Date: 2026-08-19
Card: `b70f118c`
Agent: `codex-foundation`
Status: Ready for root review

## Outcome

The SKLegal repository now has a reproducible Python and npm workspace, the
approved source layout, a pinned local development dependency definition, and
one command for every required engineering check. No protected matter content,
production secret, HammerTime corpus record, external account, or live SKLegal
service was used.

## Acceptance evidence

1. `make dev-deps` is the single command that starts the PostgreSQL and Temporal
   development dependencies. `make dev-deps-check` validated the Compose model
   and Docker's full `up --detach --wait` plan in dry-run mode. PostgreSQL and
   Temporal use exact release tags and Linux x86_64 registry digests. Ports
   `15433` and `17233` bind only to `127.0.0.1`, and PostgreSQL state uses a
   local Docker volume rather than the NFS project mount.
2. `make check` is the single command that bootstraps pinned project-local
   tooling and runs design-hash, Compose, lock-drift, format, lint, type,
   frontend build, unit, integration, migration manifest, fixture safety,
   SBOM, secret, and vulnerability checks.
3. `make clean-room-check` copied 84 Git-eligible source files into a temporary
   directory under the NFS project and ran the complete `make check` workflow
   there with fresh `.tools`, `.venv`, and `node_modules` directories. It reused
   only the root workspace's NFS uv download cache. It passed and removed the
   temporary directory. The final allowlist contains no `dist`, `build`, SBOM,
   or TypeScript build-info artifacts.
4. The five approved design hashes still match
   `docs/approval/DESIGN-HASHES.sha256`.
5. The final Compose check and integration test verified that no
   `sklegal-dev` container remained running.

## Exact verification results

- `make check`: PASS, final run completed in about 11 seconds with a warm
  repository-local dependency stamp.
- `make clean-room-check`: PASS, final accepted run completed in about two
  minutes from an 84-file allowlist with fresh project-local tool and
  dependency directories.
- Python unit tests: 66 of 66 passed. This includes the existing 12 capacity
  tests, existing 46 security-policy tests, and 8 foundation tests.
- Frontend unit tests: 1 of 1 passed with Vitest.
- Foundation integration tests: 5 of 5 passed.
- Ruff format and lint: PASS.
- mypy: PASS on 14 source files, including capacity, security, migration,
  fixture, secret, and clean-room tooling plus Python workspace packages.
- Prettier, ESLint, TypeScript, and Vite production build: PASS.
- Migration manifest validation: PASS with zero migrations.
- Fixture safety: PASS for the platform-only fixture set.
- Secret scan: PASS with no findings outside an empty reviewed baseline.
- Python vulnerability scan: no known vulnerabilities in the hashed export of
  the 95-package `uv.lock` graph.
- npm vulnerability scan: zero vulnerabilities.
- Python CycloneDX 1.5 SBOM: 95 components and 96 dependency records.
- Node CycloneDX 1.6 SBOM: 306 components and 342 dependency records.
- Forbidden dash scan: no em dash or en dash in repository source.

## Reproducibility controls

- `uv.lock` SHA-256:
  `52fdbe0eabe6401690a396a2c3970580cdf48cb3c02017ba1daef3aa7f57cde1`
- `package-lock.json` SHA-256:
  `008175bbd3510df990d0df7b45baa23929331e5615c7430d19c4396f4aa7a9ce`
- `pyproject.toml` SHA-256:
  `37169698e279bafc612f665cc54960b107f6a42c8d1f45d3395a38de348b5cab`
- `deploy/chiap01/compose.dev.yml` SHA-256:
  `e6da66cbdff3678648fdc873d7a841e8aac01a2c871686754b76f9c0fb64e0a6`
- The official Astral Linux x86_64 uv 0.12.5 release archive is pinned by URL,
  version, and SHA-256 in `requirements/bootstrap.lock`. Its SHA-256 is
  `68a509da24b06b4223a1c0175fb5eb5bc79342b76cbeff0cfe51ac3f5b17b6b2`.
  Bootstrap verifies the downloaded bytes before bounded extraction and moves
  only `uv` and `uvx` into the ignored project-local `.tools/bin` directory.
  Both the freshly extracted executable and an existing `.tools/bin/uv` must
  report the pinned version or bootstrap fails closed.
- npm reinstallation is bounded to five minutes and uses the npm cache while
  retaining `npm ci` lock enforcement. Unchanged workspaces reuse an installed
  tree only after a digest and `npm ls --all` validation.
- Python synchronization is bounded to ten minutes to expose an NFS stall.
- Generated environments, dependency trees, build output, SBOM output, audit
  exports, and TypeScript build information are ignored.
- GitHub Actions are pinned to immutable commits resolved from the official
  action repositories: `actions/checkout` v7.0.1 at
  `3d3c42e5aac5ba805825da76410c181273ba90b1`, `actions/setup-python` v7.0.0 at
  `5fda3b95a4ea91299a34e894583c3862153e4b97`, and `actions/setup-node` v7.0.0 at
  `820762786026740c76f36085b0efc47a31fe5020`.

## Clean-room attempt record

- An early complete run passed while a generated `tsconfig.tsbuildinfo` file
  was still Git-eligible. That run was rejected as acceptance evidence. The
  ignore rules were corrected, the generated file was removed from the source
  allowlist, and the allowlist was revalidated.
- The first corrected 84-file run failed only the Ruff format check at
  `scripts/clean_room_check.py:50`. Ruff formatting was applied and the normal
  full check passed afterward.
- A subsequent corrected run was interrupted after about 8 minutes when
  `python -m venv` spent more than 6 minutes installing pip through ensurepip
  on NFS. This exposed a reproducibility defect rather than a product-test
  failure. The bootstrap was changed to the pinned official uv archive, so it
  no longer creates a pip environment merely to obtain uv.
- The final 84-file run exercised the replacement archive bootstrap, created
  fresh project-local environments, passed the complete check suite, printed
  `clean-room check valid: 84 source file(s)`, and removed its temporary
  directory. This final run is the clean source/install acceptance evidence.
- Root review then required fail-closed version validation for an existing uv
  binary. Direct foundation coverage for both fresh and existing paths passed.
  The first aggregate rerun stopped only for Ruff formatting of the new test;
  formatting was applied and the final aggregate run passed all 66 Python
  tests and every other check.

## Files added or changed

- Root workflow: `Makefile`, `pyproject.toml`, `uv.lock`, `package.json`,
  `package-lock.json`, `.secrets.baseline`, `.gitignore`, and CI workflow.
- Frontend workspace: minimal React, Vite, TypeScript, ESLint, Prettier, and
  Vitest foundation under `apps/web`.
- Python workspace: package boundaries for API, worker, domain, policies,
  audit, model gateway, and retrieval.
- Reserved architecture paths: connectors, workflows, agent specs and schemas,
  jurisdiction packs, and evals.
- Development dependencies: digest-pinned Compose definition and operator
  instructions under `deploy/chiap01`.
- Checks: bootstrap, aggregate check runner, Compose wrapper, migration
  manifest validator, fixture safety validator, secret baseline validator, and
  clean-room runner under `scripts`.
- Tests: package discovery support, 8 foundation unit tests, and 5 foundation
  integration tests.
- Documentation: engineering foundation guide, updated repository status, and
  this evidence record.

## Limitations and follow-up

- The Compose start path was proven with Docker dry-run and config validation.
  It was not used to pull images or create persistent services in this task.
- Migration validation covers an ordered, hashed manifest only. Database schema
  migration and live rollback testing belong to `SKL-S1-02`.
- The GitHub workflow was authored but cannot be executed as a hosted checkout
  until the owner creates and publishes repository history. The local
  allowlisted clean-room run is equivalent setup evidence, not a claim of a
  Git clone test.
- The local-development PostgreSQL trust mode is restricted to the isolated
  Compose network and loopback port. It is prohibited for production.
- The first npm install on NFS is bounded but can take tens of seconds. A stale
  interrupted generated dependency tree of about 49 MB was moved to trash and
  can be recovered until trash is emptied.
- The exact temporary directory from the earlier interrupted cold-cache run
  was also moved to trash after its NFS cleanup stalled. It remains recoverable
  until trash is emptied. The final accepted run left no clean-room directory.
- Runtime domain behavior, database schemas, CapAuth integration, legal matter
  processing, connectors, and deployment remain outside this card.

## Rollback

No database, Docker volume, container, external account, corpus artifact, or
production service was created. Rollback is limited to removing the new
foundation files and ignored generated directories. The prior design,
capacity, and security artifacts were not modified. The clean-room temporary
directory was automatically removed, and the interrupted generated npm tree
was moved to trash rather than permanently deleted.
