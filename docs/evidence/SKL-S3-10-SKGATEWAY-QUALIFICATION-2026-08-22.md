# SKL-S3-10 SKGateway qualification evidence (hermetic scope)

Date: 2026-08-22

Board card: `bbf206c3` (SKL-S3-10)

Owner: `skl-s3-10`

Scope: hermetic qualification inside the SKLegal worktree only. This record
covers what was implemented and verified with fakes in this worktree. It is
referenced by `config/model_gateway/deployment/model-catalog.json` as the
`evaluation_ref` of free route `free.local-qwen.v1`.

## What is qualified hermetically

Every item below is enforced by
`tests/test_model_gateway_skgateway_seam.py` (46 tests, 11 subtests, all
passing on 2026-08-22):

1. Transport seam is deployment-only. `TransportProfile` validation rejects
   literal endpoint addresses (environment variable references only),
   provider secrets on profile-bound SKGateway bindings, and secrets on
   direct profiles. The shipped route registry adds
   `qwen.corpus-summary.direct.v1` (enabled, direct binding) and
   `qwen.corpus-summary.skgateway.v1` (route enabled, profile disabled
   until live qualification) without touching agent specs, proposal
   schemas, prompt or schema pins, Matter logic, or legal gates. The
   parity test proves the two bindings produce identical proposal
   contracts (payload, payload SHA-256, schema id and hash, prompt hash,
   provider, policy classification).
2. Profile store fails closed. Unknown profile, disabled profile, stale
   content hash, and wrong-provider-kind bindings all raise typed errors
   before any provider call. The repository store loads with pinned
   hashes; the SKGateway profile ships disabled.
3. Shared capacity domain. Direct and SKGateway routes naming
   `qwen.chiap08.shared.v1` draw from one envelope of four active plus
   four queued with a 30 second queue deadline. The ninth in-flight
   request raises `ProviderSaturationError` regardless of which route
   issues it; a queue wait past the deadline raises
   `CapacityQueueTimeoutError`; release returns the domain to (0, 0).
4. Model catalog. The exact alias `qwen3-32b` resolves to served model
   `Qwen3-32B-Q6-K-xlw-20250428` at revision 2025-04-28 in trust zone
   `sovereign_local`. A misspelled alias fails closed. Workload buckets
   enforce trust-zone floors: internal classification cannot use a
   public-only bucket; confidential requires a sovereign-local member plus
   a recorded human approval reference; workload-class mismatch, unknown
   buckets, and quarantined source-rights states (`unknown`,
   `unverified`, `expired`, `revoked`) all deny. Free route
   `free.local-qwen.v1` is approved with a local classification ceiling;
   `free.gpt-4o-eval.v1` is unapproved and public-only, and is denied for
   internal content.
5. SKGateway adapter. The adapter sends the catalog alias as the request
   model when the route pins one, sets `stream=false`, never requests
   provider-side structured output (client-side validation only), and
   requires the served model to agree across the response body, the
   `x_skgateway` attribution block, and any header attribute
   (`ServedModelAttributionError` otherwise). An unqualified live path
   (any of the 12 `LIVE_PATH_CONTROLS` unenforced) denies before any
   transport call with `ProviderUnavailableError`; a transport outage is
   typed. A missing capability reference denies.
6. Audit records. `AuditRecord` carries identifiers and policy references
   only; its validator rejects keys containing leak fragments, and the
   canonical JSON of a real proposal record contains no prompt text, no
   capability reference, and no key material. A failing audit sink raises
   so callers fail closed.
7. Rollback parity. Rebinding from the SKGateway profile back to the
   direct profile reproduces the identical proposal contract (schema id
   and hash, payload hash, model pin) with no schema or workflow change.
8. Deployment artifacts. `capacity-policy.json` pins the shared domain
   envelope; the route registry pins `capacity_domain_id` on all Qwen
   routes; the direct and SKGateway routes share prompt and schema pins;
   the bucket route pins workload XL and `bucket.xlarge.v1` and ships
   disabled; all deployment JSON and the runbook are ASCII-dash-only and
   contain no literal addresses or raw keys;
   `skgateway-source-pin.json` records the planning-reviewed upstream
   commit `b4b4115df9a6d5c9c4621d98207a1074e2737ef5` (MIT, `npm ci`,
   not vendored) and states that protected traffic is denied until the
   live-path gate is qualified.

## What is NOT qualified by this record

The following require the live chiap08 host and remain open for the
deployment follow-through recorded in
`deploy/chiap08/SKGATEWAY-DEPLOYMENT-RUNBOOK.md`:

- Installing the pinned upstream commit on chiap08 under `/opt/skgateway`.
- Resolving the `js-yaml` high-severity advisory observed in the planning
  checkout before any protected traffic flows.
- Live-path qualification of CapAuth identity, SKLegal classification and
  egress policy, sanitizer limits, and attributable audit on the deployed
  revision, recorded as an `SkGatewayLivePathReport`.
- Enabling the `chiap08.skgateway-chat.v1` profile (it ships disabled).
- One policy-approved synthetic Agent Run end to end through SKGateway.
- The live rollback drill from SKGateway back to the direct binding.

## Exact test evidence

Commands (from the worktree root, bootstrap already run):

    UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked \
      --package sklegal-model-gateway pytest \
      tests/test_model_gateway.py tests/test_model_gateway_parity.py \
      tests/test_model_gateway_contracts.py \
      tests/test_model_gateway_skgateway_seam.py -q
    # 92 passed, 57 subtests passed in 0.68s

    UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked \
      --package sklegal-model-gateway pytest \
      tests/test_worker_workflows.py tests/test_tool_gateway.py \
      tests/test_agent_spec_registry.py \
      tests/integration/test_foundation_contract.py -q
    # 104 passed, 35 subtests passed, 1 failed

The single failure,
`test_foundation_checks_leave_no_development_containers_running`, is
environmental and unrelated to this card: `sklegal-dev` postgres and
temporal containers were already running on this shared host (started
2026-08-22T20:06Z by another process) and this card neither starts nor
stops them. No code in this card touches docker or compose.

Ruff: all changed Python files pass
`uv run --locked ruff check packages/model_gateway/src/sklegal_model_gateway/
tests/test_model_gateway_skgateway_seam.py
tests/test_model_gateway_contracts.py
scripts/recompute_transport_profile_hashes.py` (All checks passed).
