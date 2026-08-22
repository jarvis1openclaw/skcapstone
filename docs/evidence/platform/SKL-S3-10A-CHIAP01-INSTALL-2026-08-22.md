# SKL-S3-10A chiap01 installation evidence

Date: 2026-08-22

Card: `72df1b66`

## Installation

- Host: `chiap01`
- Install path: `/opt/skgateway`
- Repository: `https://github.com/smilinTux/skgateway.git`
- Pinned commit: `b4b4115df9a6d5c9c4621d98207a1074e2737ef5`
- License: MIT
- Node: `v20.20.2`
- npm: `10.8.2`
- Lockfile SHA-256 after reviewed remediation: `60374ebcde47a0af9561d1f02f20b7d37022bc660f7fd85cbd828767d297a647`
- Package SHA-256 after reviewed remediation: `544000121048cd3054542fee8e30e70f2282122510788f40e35e85491ffd4656`
- Dependency remediation: exact `js-yaml@5.3.0` lockfile update, tested against the pinned upstream commit.
- Native `better-sqlite3` module: built successfully for Node 20
- Provider keys: none installed
- Service activation: not performed

## Qualification result

The pinned upstream test suite passed after the native module build and
dependency remediation:

```text
tests 1325
pass 1325
fail 0
cancelled 0
skipped 0
```

The production dependency audit now passes with zero info, low, moderate,
high, or critical findings. The remediation was an exact `js-yaml@5.3.0`
lockfile update, not a blanket audit fix.

The live-path CapAuth, SKLegal policy, sanitizer, attribution, capacity,
synthetic parity, and rollback gates remain unqualified. Protected Matter
traffic therefore remains disabled. The chiap01 runbook is
`deploy/chiap01/SKGATEWAY-DEPLOYMENT-RUNBOOK.md`.

## Live-path denial smoke test

The promoted install was started ephemerally on loopback with strict
authorization enabled, no CapAuth endpoint or token, and no service activation.
The public health endpoint returned `200`, while a synthetic gated Chat
Completions request returned `403` with the fail-closed reason
`authz_decide fail-closed: empty subject or capability`. This proves the
production entrypoint denies when authorization is unavailable. It does not
prove an allowed CapAuth decision or the complete SKLegal policy composition.
