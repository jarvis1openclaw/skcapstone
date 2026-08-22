# SKL-S3-10 implementation task design

Date: 2026-08-22
Card: `bbf206c3`
Size: L
Planning dependency: `31194edb`
Gateway dependency: `b0c6495c`

## Objective

Qualify and integrate SKGateway as the preferred OpenAI-compatible transport
router behind the existing SKLegal model gateway. Preserve a tested direct
chiap08 Qwen binding as initial deployment and rollback. The transition must
not change Matter logic, agent specifications, prompt or output schema pins,
or any legal gate.

## Source pin and custody

- Upstream: `https://github.com/smilinTux/skgateway.git`
- Planning review commit: `b4b4115df9a6d5c9c4621d98207a1074e2737ef5`
- License observed at that commit: MIT
- Install with a pinned commit and `npm ci` on the assigned host.
- Record repository URL, commit, lockfile hash, Node and npm versions,
  dependency audit, configuration hash, unit hash, service identity, install
  path, observed time, and rollback tag.
- The planning checkout installed `js-yaml@4.1.1`; `npm audit --omit=dev`
  reported one high-severity YAML denial-of-service advisory set. Pin an
  upstream update or a reviewed lockfile change and rerun the full gateway
  suite before deployment. Do not apply an unreviewed blanket audit fix.

Do not vendor the upstream checkout into SKLegal unless a later source and
maintenance decision explicitly requires it.

## SKLegal transport seam

Keep `ModelGateway.submit` as the outer trust boundary. Add a deployment-only
transport profile reference to each route binding:

- `direct_qwen`: calls the approved local Qwen OpenAI-compatible service
  through a secret-free local transport.
- `skgateway_chat`: calls SKGateway `/v1/chat/completions` through a
  CapAuth-attributed service identity.
- `openai_responses`: retains the current direct OpenAI Platform Responses API
  adapter for routes that require that API until explicit SKGateway Responses
  compatibility exists.

The route registry pins logical route ID, prompt and output schema hashes,
classification ceiling, timeout, retry policy, workload class, and an allowed
served-model or bucket policy. Environment-specific profile records resolve
base addresses and secret references outside domain state.

The SKGateway Chat Completions adapter must:

- submit the logical request model or bucket and bounded messages
- request structured output only after the exact upstream path is qualified
- always validate returned JSON against the SKLegal pinned schema
- capture request ID, backend, requested model, bucket, bucket member, exact
  served model, catalog generation, policy revision, usage, timing, retry,
  failover, and saturation evidence
- reject missing or conflicting served-model attribution
- never treat a successful HTTP response as Approval or state mutation

## Routing policy

Start with concrete routing for substantive private analysis:

- local Qwen remains the initial corpus analyst and default Matter strategist
- the concrete Qwen request alias resolves to the exact served operator model
- direct and registry Qwen paths share one four-active, four-queued,
  30-second capacity domain unless live measurement approves a new envelope

Use dynamic buckets only where the task contract permits model substitution:

- workload classes are `S`, `M`, `L`, and `XL`
- workload class expresses required task capability and risk
- model parameter size is separate metadata and never satisfies a workload
  class by itself
- SKLegal `public` maps only to a public trust-zone bucket
- SKLegal `internal` maps only to an internal-or-stricter trust-zone bucket
- confidential, highly restricted, privileged, and private-corpus contexts
  require an explicitly qualified sovereign-local member and SKLegal policy
  approval before any bucket request
- a misspelled, empty, stale, or ineligible bucket fails closed

Free-provider routes are optimization candidates, not entitlement. Qualify
them per model, provider, retention terms, data use, region, context size,
structured output, citation preservation, tool behavior, latency, quotas,
served-model truth, and task evaluation. Remote free routes start at public
content only. Local Qwen may be cost-free while retaining the local protected
classification ceiling.

## Required SKGateway live-path gate

The reviewed upstream README states that the production `routeAndSend` path
currently invokes routing, SIEM, and metrics but not all implemented controls.
Before protected SKLegal use, prove on the exact deployed commit that the live
entrypoint enforces:

- CapAuth identity verification and the `skgateway.infer` capability
- SKLegal Tenant, Matter, purpose, classification, and egress decision
- body and system limits plus secret and sensitive-data handling
- tool-budget stripping or rejection appropriate to a model-only route
- rate limits and the qualified shared Qwen capacity domain
- attributable audit with no prompt, source, secret, or raw capability leak
- deterministic denial when identity, policy, catalog, or audit is unavailable

Do not rely on a control merely because it exists in an alternate library path
or test suite.

## Secrets and installation

- Provider API keys are optional and supplied only when the human owner adds
  a qualified provider.
- Obtain keys through the approved secret-management workflow.
- Store only secret references in SKLegal and environment variable names in
  SKGateway configuration.
- Runtime values use an owner-only `0600` EnvironmentFile or a stronger secret
  injection mechanism.
- Never paste secrets into a shell command, shell history, systemd unit,
  repository, prompt, log, test fixture, dashboard, or completion evidence.
- Bind the proxy and dashboard to the narrowest interface. Put any remote
  access behind authenticated TLS and network policy.

## Implementation sequence

1. Freeze upstream source, license, lockfile, and deployment topology.
2. Clear the high-severity dependency audit gate through a pinned, tested
   upstream source or reviewed lockfile update.
3. Add the SKLegal transport-profile schema and Chat Completions adapter with
   fake transports.
4. Add direct-Qwen and disabled-SKGateway deployment profiles without literal
   private addresses.
5. Add exact Qwen alias, capacity domain, model catalog, workload bucket, and
   trust-zone configuration overlays in the deployment repository.
6. Install the pinned SKGateway source and runtime service on the assigned
   host with no provider secrets.
7. Unify and qualify the SKGateway live-path controls.
8. Run direct-versus-gateway schema, attribution, latency, cancellation,
   saturation, and failure parity against synthetic prompts.
9. Add human-supplied provider keys one at a time, then qualify free models
   using public synthetic prompts only.
10. Run one policy-approved synthetic SKLegal Agent Run through SKGateway.
11. Exercise rollback to the direct Qwen profile and verify no workflow or
    Proposal contract changes.

## Tests

- unknown, disabled, stale, or malformed transport profile
- direct Qwen and SKGateway output-schema parity
- Chat Completions structured-output rejection and client-side validation
- missing, spoofed, or conflicting served-model headers and body identity
- CapAuth absent, invalid, revoked, wrong purpose, wrong Matter, or expired
- SKLegal policy unavailable, egress denied, source rights denied, or audit
  unavailable
- secret scan of git, process arguments, logs, audit, metrics, and evidence
- dependency audit, lockfile integrity, license, unsupported dependency, and
  native-module rebuild
- Qwen four active plus four queued, ninth-request rejection, timeout,
  cancellation, and permit recovery
- backend outage, no eligible bucket member, wrong trust zone, provider 429,
  invalid model, retry budget, and no unauthorized cross-zone failover
- exact concrete Qwen alias and dynamic bucket attribution
- free-model model-swap, retention, schema, citation, context, and quota tests
- direct rollback with the same request and Proposal schema

## Acceptance

- App and agent code use logical SKLegal route IDs only.
- Direct and SKGateway bindings are configuration-selectable and produce the
  same validated Proposal contract.
- The exact served model and every routing or failover choice are recorded.
- Qwen capacity cannot be double-counted across direct and registry aliases.
- Protected traffic cannot traverse an unqualified live SKGateway path or a
  remote free model.
- No provider key or raw capability appears outside approved secret custody.
- No unaccepted high or critical production dependency vulnerability remains.
- Installation, health, load, policy, audit, parity, and rollback evidence is
  linked to card `bbf206c3`.

## Rollback

Disable the SKGateway transport profile, restore the last approved direct-Qwen
profile reference, cancel or drain queued gateway work, verify direct health,
and replay the same synthetic Proposal contract. Preserve all Agent Run,
gateway, policy, and transition evidence. Do not delete the failed deployment
or its configuration hashes until review is complete.

## Prohibited

- Consumer ChatGPT or consumer subscription credentials
- Raw provider OAuth tokens used as third-party app credentials
- Remote free-model routing for protected or private-corpus context by default
- Model or bucket selection that bypasses SKLegal classification and rights
- Silent model substitution or fallback
- Production activation before the live-path control gate and human review
