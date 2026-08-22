# SKGateway deployment and rollback runbook (SKL-S3-10A)

Card: `72df1b66`. Parent qualification: `bbf206c3`. Host: chiap01.

SKGateway runs as a non-GPU control-plane service beside the SKLegal API on
chiap01. It forwards only after qualification to Qwen on chiap08:11439. The
direct chiap08 Qwen binding remains the rollback route. This runbook performs
no protected Matter activation and installs no provider keys.

## Placement and prerequisites

- chiap01: 32 logical CPUs, 64 GiB memory, qualified SKLegal host.
- chiap08: RTX 5090 host dedicated to Qwen3.8 inference and its shared
  four-active, four-queued capacity domain.
- Install path: `/opt/skgateway` on chiap01.
- Upstream commit: `b4b4115df9a6d5c9c4621d98207a1074e2737ef5`.
- Required runtime: Node.js 20 or newer, `npm ci`, and a clean production
  dependency audit.

## Install and qualify

1. Clone the pinned commit into `/opt/skgateway` with a dedicated service
   identity and record the repository, commit, lockfile hash, runtime
   versions, configuration hash, and observed time.
2. Run `npm ci` and the complete upstream test suite. Build native modules
   rather than using `--ignore-scripts`.
3. Apply the reviewed exact `js-yaml@5.3.0` lockfile update recorded in the
   source-pin record, then confirm `npm audit --omit=dev` reports zero high or
   critical findings. Do not use an unreviewed blanket audit fix.
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

## Activation and rollback

Activation requires a complete live-path report and explicit human approval.
Until then the SKLegal SKGateway profile stays disabled.

Rollback disables the gateway profile, drains queued work, restores the direct
chiap08 Qwen profile, verifies direct health, and replays the same synthetic
Proposal contract. Preserve all transition and audit evidence.

## Evidence

Link install, audit, service, capacity, live-path, parity, and rollback records
to card `72df1b66`. Link the parent hermetic qualification record to
`bbf206c3`.
