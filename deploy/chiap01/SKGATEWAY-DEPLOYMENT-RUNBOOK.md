# SKGateway deployment and rollback runbook (SKL-S3-10C)

Card: `60cb0c9a`. Parent qualification: `72df1b66`. Host: chiap01.

SKGateway runs as a non-GPU control-plane service beside the SKLegal API on
chiap01. It forwards only after qualification to Qwen on chiap08:11439. The
direct chiap08 Qwen binding remains the rollback route. This runbook performs
no protected Matter activation and installs no provider keys.

## Placement and prerequisites

- chiap01: 32 logical CPUs, 64 GiB memory, qualified SKLegal host.
- chiap08: RTX 5090 host dedicated to Qwen3.8 inference and its shared
  four-active, four-queued capacity domain.
- Install path: `/opt/skgateway` on chiap01.
- Integrated upstream commit:
  `3cf16fe6ca1a6e5ec92e5f090fc798dfd6404596`.
- Required runtime: Node.js 20 or newer, `npm ci`, and a clean production
  dependency audit.

## Install and qualify

1. Clone the pinned commit into `/opt/skgateway` with a dedicated service
   identity and record the repository, commit, lockfile hash, runtime
   versions, configuration hash, and observed time.
2. Run `npm ci` and the complete upstream test suite. Build native modules
   rather than using `--ignore-scripts`.
3. Confirm the integrated package and lock hashes match the source-pin record,
   then confirm `npm audit --omit=dev --audit-level=high` reports no
   vulnerability. Do not use an unreviewed blanket audit fix.
4. Bind the proxy and dashboard to the narrowest approved interface. Use
   authenticated transport and network policy for any non-loopback hop.
5. Configure the Qwen backend to chiap08:11439 without putting a private
   address, capability, or provider key in SKLegal source or logs.
6. Prove CapAuth identity, SKLegal classification and egress policy,
   sanitizer limits, rate limits, shared capacity admission, attributable
   audit, and deterministic failure on the exact production entrypoint.
7. Run synthetic direct-versus-gateway parity, latency, saturation, outage,
   audit, and restart tests. Protected Matter traffic remains disabled.

## Authorization adapter preflight

Before starting a persistent service, load
`config/model_gateway/deployment/skgateway-authz-adapter.json` through
`load_skgateway_authz_deployment`. The preflight must reject any contract that
is enabled, uses a literal endpoint or secret, changes the chiap01 service
identity, expands the safe response fields, weakens the prohibited-field set,
enables an allow cache, or changes the exact `skgateway.infer` capability.

The adapter remains disabled until its trusted-facts resolver and canonical
SKLegal policy evaluator use durable current state and the live-path report is
approved.

Also run the source compatibility preflight against the exact promoted tree:

```bash
python3 scripts/qualify_skgateway_chiap01.py \
  --source-dir /opt/skgateway \
  --require-pass
```

The contract is
`config/model_gateway/deployment/skgateway-chiap01-qualification.json`. The
preflight reads public source and dependency metadata only. It must not read
runtime environment values. It rejects a single-credential authorization
client, missing exact Tenant and Matter selectors, dashboard or metrics disable
flags that the live source ignores, an unexpected source change, or any source
and lockfile hash mismatch. A successful source preflight is not a live-path
report. Every live control remains unqualified until runtime evidence proves
it on the exact production entrypoint.

## Synthetic denial smoke test

`skgateway.synthetic-deny.yaml` is an explicit qualification-only fixture. It
binds the proxy to loopback, enables strict authorization, disables the
internal-peer bypass and allow cache, adds no live backend, and supplies no
credential. The upstream deep merge still retains its default backend catalog,
so the authorization denial must occur before dispatch. Start it only
ephemerally with `SK_STANDALONE=1` so the test does not register a service or
publish an alert:

```bash
SK_STANDALONE=1 \
SKGATEWAY_CONFIG=/path/to/skgateway.synthetic-deny.yaml \
SKGATEWAY_AUTHZ_ENFORCE=1 \
SKGATEWAY_AUTHZ_TRUST_INTERNAL=0 \
timeout --signal=TERM --kill-after=2s 20s node src/index.mjs
```

The expected result is public health `200` and synthetic Chat Completions
`403` before upstream dispatch. Hash and inspect the sanitized response and
audit line, then delete only the temporary fixture and output. This proves
deterministic denial only. It does not prove an allow, canonical policy
composition, or a qualified live path.

## Activation and rollback

Activation requires a complete live-path report and explicit human approval.
Until then the SKLegal SKGateway profile stays disabled.

Rollback disables the gateway profile, drains queued work, restores the direct
chiap08 Qwen profile, verifies direct health, and replays the same synthetic
Proposal contract. Preserve all transition and audit evidence.

## Evidence

Link install, audit, service, capacity, live-path, parity, and rollback records
to card `60cb0c9a`. Retain links to parent cards `72df1b66` and `bbf206c3`.
