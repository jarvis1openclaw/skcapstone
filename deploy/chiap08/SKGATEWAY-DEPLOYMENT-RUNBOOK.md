# SKGateway deployment and rollback runbook (historical superseded copy)

Card: `bbf206c3`. Historical host: chiap08. The approved placement is now
chiap01; use `deploy/chiap01/SKGATEWAY-DEPLOYMENT-RUNBOOK.md` for execution.
All commands are run by the human owner or
an explicitly authorized operator. This worktree performs no live
installation; this runbook is the reviewed procedure for that step.

## Scope and boundaries

- SKGateway is pinned, installed, and configured on chiap08 only.
- The SKLegal side of the seam is configuration: the transport profile
  store, route registry, capacity policy, and model catalog under
  `config/model_gateway/deployment/`.
- No provider API key is installed by this task. Keys arrive only when the
  human owner adds a qualified provider, one at a time, through the approved
  secret-management workflow.
- Never paste a secret into a shell command, shell history, systemd unit,
  repository, prompt, log, test fixture, dashboard, or completion evidence.

## 1. Install the pinned source

```bash
cd /opt
git clone https://github.com/smilinTux/skgateway.git skgateway
cd skgateway
git checkout <qualified-commit>        # planning review baseline:
                                      # b4b4115df9a6d5c9c4621d98207a1074e2737ef5
npm ci
```

Record on install (see `config/model_gateway/deployment/skgateway-source-pin.json`
for the record contract):

- repository URL and pinned commit
- `npm ls --package-lock-only` lockfile hash
- `node --version` and `npm --version`
- `npm audit --omit=dev` output
- configuration file hash after step 3
- unit test hash and results
- service identity (the CapAuth principal)
- install path and observed UTC time
- rollback tag

## 2. Clear the dependency audit gate

The planning checkout installed `js-yaml@4.1.1` and `npm audit --omit=dev`
reported one high-severity YAML denial-of-service advisory set. Before
deployment:

1. Pin an upstream commit that updates the dependency, or apply a reviewed
   lockfile change that bumps only the affected advisory set.
2. Rerun `npm audit --omit=dev` and confirm no high or critical finding
   remains.
3. Rerun the full gateway unit suite.
4. Record the new commit or lockfile hash in the pin record.

Do not apply an unreviewed blanket `npm audit fix`.

## 3. Configure SKGateway

- Bind the proxy and dashboard to the narrowest interface
  (`127.0.0.1` or the chiap08 tailnet address, never `0.0.0.0`).
- Put any remote access behind authenticated TLS and network policy.
- Provider keys, if later added, are environment variable names in
  SKGateway configuration whose values are injected from an owner-only
  `0600` EnvironmentFile or a stronger mechanism.
- Record the configuration hash in the pin record.

## 4. Qualify the live-path control gate

Before any protected SKLegal traffic traverses SKGateway, prove on the
exact deployed commit that the production `routeAndSend` entrypoint
enforces every control in
`packages/model_gateway/src/sklegal_model_gateway/skgateway.py::LIVE_PATH_CONTROLS`:

- CapAuth identity verification and the `skgateway.infer` capability
- SKLegal Tenant, Matter, purpose, classification, and egress decision
- body and system limits plus secret and sensitive-data handling
- tool-budget stripping or rejection appropriate to a model-only route
- rate limits and the qualified shared Qwen capacity domain
- attributable audit with no prompt, source, secret, or raw capability leak
- deterministic denial when identity, policy, catalog, or audit is missing

The upstream README states the production path currently invokes routing,
SIEM, and metrics but not all implemented controls. A control proven only
in an alternate library path or the test suite does not satisfy this gate.
Unify and qualify the controls as a reviewed upstream change or a reviewed
wrapper before activation.

The SKLegal-side check is
`SkGatewayLivePathGate.require_qualified()`: the profile
`chiap08.skgateway-chat.v1` stays `enabled: false` and the gate denies
until a qualification report bound to the deployed commit marks every
control enforced.

## 5. Activate (after human review)

1. Flip `chiap08.skgateway-chat.v1` to `enabled: true` in the transport
   profile store.
2. Recompute profile hashes:
   `uv run --locked --package sklegal-model-gateway python
   scripts/recompute_transport_profile_hashes.py`.
3. Route `qwen.corpus-summary.skgateway.v1` traffic through the gateway
   with synthetic prompts first.
4. Run the parity, failure, load, audit, and rollback evidence battery.

## 6. Rollback to the direct Qwen binding

1. Disable the SKGateway transport profile (`enabled: false`) and
   recompute hashes.
2. Point Matter traffic at `qwen.corpus-summary.direct.v1`, which shares
   the same prompt template, output schema, and Proposal contract.
3. Cancel or drain queued gateway work.
4. Verify direct health with a synthetic run.
5. Replay the same synthetic Proposal contract and confirm identical
   schema pins and payload shape.
6. Preserve all Agent Run, gateway, policy, and transition evidence. Do
   not delete the failed deployment or its configuration hashes until
   review is complete.

Rollback changes the deployment binding only: agent specs, proposal
schemas, Matter logic, and legal gates are untouched.

## 7. Evidence

Link every artifact to card `bbf206c3` under
`docs/evidence/model_gateway/`:

- install and pin record (commit, hashes, versions, audit)
- live-path control qualification matrix
- direct-versus-gateway parity (schema, attribution, latency, cancellation)
- saturation and load results against the shared capacity domain
- audit-trail integrity and secret-scan results
- rollback replay output
