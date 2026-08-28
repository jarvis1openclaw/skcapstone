# SKL-MVP-QUAL-01Q2F completion evidence

Card: `36ce9b05`

Verdict: `PASS`

## Exact candidate

- Implementation commit: `d2938d353ea77655a77d99107b04b2930c0f756e`
- Implementation tree: `b849b3d7c38d292e0749b097111dc3601a873fcc`
- Qualification result: `docs/evidence/mvp/SKL-MVP-QUAL-01Q2F-RESULT-2026-08-27.json`
- Qualification result SHA-256: `75315583e85ef472fca13d0ca6456267d61c5073b961e96517c6e9504b19fd27`
- Frozen V2 manifest SHA-256: `0857b0642c49531ae615362b7a40785e21aa8676a933ea6a27a5f1f057e11c66`
- Candidate OpenAPI SHA-256: `1555cec46c8143e0c4f69210b8ccf0f91ea90a9cea62166d645893462fb820b6`

## Acceptance evidence

- The durable factory mounts all seven reviewed feature routers through existing composition boundaries.
- OpenAPI contains all 21 required frozen method, path, and operation ID triples.
- All operation IDs are unique.
- Runtime stores use separate core and retrieval PostgreSQL boundaries.
- The public synthetic corpus client sends exact release, projection generation, and core watermark pins.
- Core and retrieval migrations replay deterministically from empty named volumes.
- Cross-Tenant and cross-Matter RLS checks deny.
- Retrieval outage fails closed while the core workspace remains available.
- Core outage, stale policy, revoked session, projection lag, restart, backup and restore, reset and reseed, and rollback checks pass.
- Chrome reports no failed requests or CSP events. The 390 pixel layout has no page overflow and retains three labelled scroll regions.
- Final cleanup proves the task containers and named volumes are absent.

## Tests

- Python focused and feature suite: `400 passed`, `13 warnings`.
- Frontend suite: `24 files passed`, `258 tests passed`.
- Full reversible Chrome and dual PostgreSQL qualification: `PASS`.
- Ruff: `PASS`.
- ESLint: `PASS`.
- MyPy on changed Python with workspace packages treated as external: `PASS`.
- Detect-secrets over changed source files: zero findings. The result JSON produced nine expected content-hash findings and no credential or private-key finding.
- `git diff --check`: `PASS`.

The warnings are pre-existing Pydantic OpenAPI serialization warnings plus one Starlette deprecation warning. No warning changed the verdict.

## Rollback and limits

Rollback stops only the exact qualification project and removes only its two named volumes. The qualification performed that cleanup and verified the safe state.

Remaining limits are public synthetic corpus only, simulated connectors only, and optional AGE activation remains gated. This card authorizes no protected content, provider traffic, credential use, non-loopback deployment, merge, push, external action, broad cleanup, or interaction with card `431db4dd`.

Independent review remains mandatory on card `a4ac0192` before Q3 requalification.
