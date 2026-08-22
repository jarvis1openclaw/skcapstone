# SKL-S3-10 handoff: SKGateway as the SKLegal model router

Card: `bbf206c3` (SKL-S3-10). Worktree: `/tmp/swarm/bbf206c3`, branch
`swarm/bbf206c3`. Contract: `docs/tasks/SKL-S3-10-TDD.md`.

## Files changed

Package `packages/model_gateway/src/sklegal_model_gateway/`:

- `models.py` (modified): `TransportKind`, `TransportProfile`,
  `TransportEvidence`, route fields `transport_profile_id`,
  `capacity_domain_id`, `workload_class`, `allowed_served_model_alias`,
  `allowed_bucket_id`, and the secret-reference XOR profile rule.
- `errors.py` (modified): typed errors for profile state, transport
  binding, capacity queueing, catalog, buckets, source rights, free
  routes, served-model attribution, and audit outage.
- `providers.py` (modified): `ProviderCall` carries the bound transport
  profile; provider protocol exposes `transport_kind`.
- `gateway.py` (modified): profile resolution and fail-closed dispatch,
  shared capacity-domain admission, catalog alias and bucket resolution,
  transport evidence on proposals.
- `__init__.py` (modified): public exports.
- `transport_profiles.py` (new): profile store, content-hash pinning,
  `from_file`, binding resolution.
- `capacity.py` (new): `CapacityDomainController` with per-domain
  envelopes (4 active, 4 queued, 30 s queue deadline).
- `catalog.py` (new): model catalog, exact alias, S/M/L/XL buckets,
  trust-zone floors, source-rights quarantine, free routes.
- `skgateway.py` (new): Chat Completions adapter, live-path gate with 12
  `LIVE_PATH_CONTROLS`, served-model attribution agreement.
- `audit_records.py` (new): content-free audit records and fail-closed
  sinks.

Config and deployment:

- `config/model_gateway/route-registry.json` (modified): transport seam
  section plus routes `qwen.corpus-summary.direct.v1` (enabled),
  `qwen.corpus-summary.skgateway.v1` (enabled route, disabled profile),
  `qwen.corpus-summary.bucket-xl.v1` (disabled bucket route).
- `config/model_gateway/deployment/transport-profiles.json` (new).
- `config/model_gateway/deployment/model-catalog.json` (new).
- `config/model_gateway/deployment/capacity-policy.json` (new).
- `config/model_gateway/deployment/skgateway-source-pin.json` (new).
- `deploy/chiap08/SKGATEWAY-DEPLOYMENT-RUNBOOK.md` (new).
- `scripts/recompute_transport_profile_hashes.py` (new).

Tests and docs:

- `tests/test_model_gateway_skgateway_seam.py` (new): 46 tests, 11
  subtests.
- `tests/test_model_gateway_contracts.py` (modified): relaxed to routes
  >= 2 and boolean enabled so the three new routes coexist with the
  legacy assertions.
- `docs/development/MODEL-GATEWAY.md` (modified): transport seam section.
- `docs/evidence/SKL-S3-10-SKGATEWAY-QUALIFICATION-2026-08-22.md` (new):
  the path pinned by the catalog's `free.local-qwen.v1.evaluation_ref`.

## Tests and exact results

All commands from the worktree root with
`UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked
--package sklegal-model-gateway pytest ... -q`:

- `tests/test_model_gateway.py tests/test_model_gateway_parity.py
  tests/test_model_gateway_contracts.py
  tests/test_model_gateway_skgateway_seam.py`:
  92 passed, 57 subtests passed in 0.68s.
- `tests/test_worker_workflows.py tests/test_tool_gateway.py
  tests/test_agent_spec_registry.py
  tests/integration/test_foundation_contract.py`:
  104 passed, 35 subtests passed, 1 failed.
- Combined final run (all seven files above minus the docker-dependent
  integration file): 192 passed, 76 subtests passed in 2.44s.
- Ruff on all changed Python files: All checks passed.

The single failure,
`FoundationContractTests::test_foundation_checks_leave_no_development_containers_running`,
is environmental: the shared `sklegal-dev` postgres and temporal compose
containers were already running on this host (started 2026-08-22T20:06Z
by a process outside this card). This card neither starts nor stops
them, and no code in the diff touches docker or compose. The same test's
sibling checks (compose validity, package imports) pass.

## Acceptance criteria evidence

1. Switching direct Qwen to SKGateway changes deployment binding only.
   `GatewayTransportSeamTests::test_direct_and_gateway_bindings_produce_the_same_proposal_contract`
   proves identical payload, payload hash, schema id and hash, prompt
   hash, provider, and policy classification across the two bindings.
   Agent specs, proposal schemas, Matter logic, and legal gates are
   untouched by the diff (only the model gateway package, config, tests,
   and docs changed).
2. Protected traffic denied until the live path is enforced.
   `SkGatewayAdapterTests::test_unqualified_live_path_denies_before_any_transport`
   denies with `ProviderUnavailableError` when any of the 12
   `LIVE_PATH_CONTROLS` is unenforced;
   `test_repository_disabled_skgateway_profile_denies_traffic` proves
   the shipped profile stays disabled; profile-less or stale stores fail
   closed (`TransportProfileStoreTests`); the source pin records
   protected traffic as denied until qualification.
3. Exact alias, shared capacity, buckets, served-model evidence, and free
   routes. `ModelCatalogTests` covers alias resolution, misspelling
   denial, trust-zone floors, approval requirements, source-rights
   quarantine, and both free routes.
   `SharedCapacityDomainTests::test_direct_and_gateway_share_one_domain`
   proves direct and SKGateway calls contend for the same four-plus-four
   envelope and the ninth call saturates. Served-model evidence must
   agree across body, attribution block, and header
   (`ServedModelAttributionError` tests). Hermetic qualification
   evidence is recorded in
   `docs/evidence/SKL-S3-10-SKGATEWAY-QUALIFICATION-2026-08-22.md`;
   live qualification on chiap08 remains open by design.
4. API keys only from secret storage. `TransportProfileValidationTests`
   rejects secrets on direct and profile-bound SKGateway profiles;
   profile endpoints are environment variable references only;
   `test_no_literal_addresses_or_raw_keys_in_deployment_config` scans
   the deployment JSON. No secret value exists anywhere in the diff.
5. Rollback tested. `RollbackParityTests::test_rollback_binding_keeps_the_proposal_contract`
   rebinding to the direct profile reproduces the identical proposal
   contract with no schema or workflow change.

## Known limitations

- The SKGateway profile `chiap08.skgateway-chat.v1` ships disabled. Live
  qualification on chiap08 (pinned commit install, js-yaml advisory
  resolution, live-path report, synthetic Agent Run, rollback drill) is
  intentionally out of this worktree and tracked by the runbook.
- The audit recorder is a standalone sink (`AuditRecorder` protocol,
  in-memory and failing implementations); the gateway submit loop does
  not yet append records itself. Callers wire the sink.
- The capacity controller does not yet free queue waiters on cancellation
  of the waiting caller; the queue deadline bounds the wait.
- Bucket route `qwen.corpus-summary.bucket-xl.v1` ships disabled until a
  task qualifies XL workloads.
- The one integration test failure documented above is environmental and
  requires no action from this card.
