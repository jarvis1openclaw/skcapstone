# SKLegal session handover

Date: 2026-08-23
Session state: paused after swarm implementation, qualification, and independent review

## Resume rule

The next session must read this handover and `AGENTS.md` before acting. Do not
activate protected Matter traffic, create credentials, deploy services, process
HammerTime `Inbox/`, or perform external actions without the required explicit
human gates.

The current safe state is fail closed. The SKGateway profile is disabled and
the direct chiap08 Qwen route remains the rollback route.

## Repository state

SKLegal repository:

- Path: `/mnt/cloud/onedrive/projects/DAVE-AI/sklegal`
- Branch: `main`
- Local HEAD: `c755e1e`
- `origin/main`: `c755e1e`
- Worktree: clean
- No temporary SKLegal implementation or review worktrees remain

SKGateway repository:

- Path: `/home/skuser01/work/skgateway`
- Branch: `main`
- Local and remote HEAD: `3cf16fe`
- Worktree: clean
- Pre-existing provider work was preserved, committed, rebased onto latest upstream, merged with the Codex work, tested, and pushed

The unrelated existing worktree `/tmp/swarm/0ad49216` may still exist. Do not
remove or modify it unless its owner explicitly authorizes that action.

## Completed SKGateway work

The following work was farmed to Codex workers in isolated worktrees, reviewed,
merged, tested, pushed, and cleaned up:

### Wire contract

Card: `b2240a8d`
Upstream integration: `fcbfeb6`, included in final upstream `3cf16fe`

Implemented:

- Separate `X-SKLegal-Service-Authorization` service credential.
- Request-local `Authorization` CapAuth credential.
- Exact Tenant, Matter, material, material version, route, purpose,
  classification, privilege, and ethical-wall scope.
- No caller-controlled scope authority.
- No governed internal-peer bypass.
- No governed allow cache.
- Bounded response validation.
- Decision, policy revision, correlation, and obligation propagation.
- Synthetic allow, deny, unavailable, malformed, and leakage tests.

Upstream verification after integration:

```text
npm test
1393 passed, 0 failed
```

### Startup disablement

Card: `171315a5`
Upstream integration: final upstream `3cf16fe`

Implemented:

- `dashboard.enabled=false` prevents dashboard construction and listener binding.
- `metrics.enabled=false` prevents metrics initialization, database creation,
  and writes.
- Disabled discovery cannot be force-refreshed.
- Qualification controls require loopback binding, strict authorization, no
  cache, and disabled auxiliary services.
- Enabled behavior remains covered by compatibility tests.

### Canonical endpoint composition

Card: `a53695e8`
SKLegal merge: `c653cae`
Evidence:
`docs/evidence/platform/SKL-S3-11B-AUTHZ-COMPOSITION-2026-08-23.md`

Implemented and tested:

- Canonical CapAuth and `PolicyGateway` composition.
- Durable current-state, replay, revocation, route, and audit dependencies.
- Startup rejection of missing, synthetic, or unavailable production
  dependencies.
- Authenticated loopback endpoint contract.
- Sanitized 401, 403, and 503 behavior.
- Migration `0018` for durable one-use policy invocation reservation.
- Deterministic rollback denial behavior.

### Exact chiap01 qualification package

Card: `60cb0c9a`
SKLegal merge: `c1f3a33`
Evidence:
`docs/evidence/platform/SKL-S3-10C-CHIAP01-LIVE-PATH-QUALIFICATION-2026-08-22.md`
Handoff:
`docs/evidence/swarm/60cb0c9a-HANDOFF.md`

Added:

- Integrated upstream source and package pins.
- SKLegal composition revision and hash checks.
- A 13-case fixture-only qualification matrix.
- Direct-Qwen parity fixture.
- Updated source preflight, tests, synthetic denial fixture, and runbook.
- Explicit dashboard, metrics, discovery, internal-bypass, and cache controls.

Result:

```text
Source preflight: PASS
Composition preflight: PASS
Protected traffic: false
Activation permitted: false
Live result: FAIL_CLOSED
```

The exact chiap01 endpoint was unavailable without credentials. The recorded
probe returned HTTP code `000`. No live allow, policy revision, audit revision,
or decision ID was invented.

### Independent review

Replacement review card: `2ebda0d1`
SKLegal merge: `c755e1e`
Evidence:
`docs/evidence/platform/SKL-S3-11-REVIEW-3-2026-08-22.md`

Disposition:

```text
BLOCKED
```

The review confirmed that source, composition, and disposable fixture evidence
supports the intended contract, but exact live chiap01 evidence is absent. All
live controls remain unqualified. Activation remains prohibited.

The older card `c6fc807e` was voided because its dependency pointed directly to
conditionally accepted `a060fa3d` and could not be claimed without force. It was
replaced by dependency-correct card `2ebda0d1`.

## Current board state

Completed:

- `b2240a8d` S3-11A exact two-credential wire contract
- `171315a5` S3-10B dashboard and metrics disablement
- `a53695e8` S3-11B canonical endpoint composition
- `60cb0c9a` S3-10C exact chiap01 qualification package
- `2ebda0d1` independent fail-closed live-path review

Still in review:

- `a060fa3d` S3-11 implementation card
- `72df1b66` S3-10A parent live-path card

The two cards remain in review because source and fixture completion does not
satisfy the missing live chiap01 gates.

## Test evidence summary

Upstream SKGateway:

```text
npm test: 1393 passed, 0 failed
npm audit --omit=dev --audit-level=high: zero vulnerabilities
```

SKLegal qualification and boundaries:

```text
Focused qualification suite: 113 passed, 16 subtests passed
Broader CapAuth, policy, audit, and composition suite: 88 passed, 73 subtests passed
Ruff: passed for the qualification scope
git diff --check: passed
```

Known environment limitations recorded in evidence:

- Mypy was unavailable during the qualification run.
- The repository secret scanner could not start because `detect-secrets` was
  unavailable.
- Card-specific leakage and forbidden-literal tests passed.
- These limitations do not qualify the live route.

## Required next steps

Do these only after the owner resumes the session and the required human gates
are confirmed:

1. Confirm the exact installed SKGateway revision on chiap01.
2. Confirm package, lockfile, configuration, service unit, and source hashes.
3. Confirm the authenticated local endpoint binding and pinned service identity.
4. Run the complete live synthetic matrix on the exact chiap01 entrypoint:
   allow, deny, PDP outage, audit outage, malformed request, oversized request,
   exact scope mismatch, saturation, restart, sanitizer leakage, attribution,
   direct-Qwen parity, and rollback.
5. Record live decision IDs, policy revision, audit revision, endpoint binding,
   service identity, test hashes, and rollback evidence.
6. Run the independent review again against the new live report.
7. Obtain separate human security approval and explicit activation approval.
8. Only then consider any profile transition. Protected Matter traffic remains
   denied until every gate passes.

Do not use force claims to bypass dependencies. Do not treat synthetic decision
IDs or fixture results as live authorization evidence.

## Startup commands for the next session

```bash
cd /mnt/cloud/onedrive/projects/DAVE-AI/sklegal
"${CODEX_HOME:-$HOME/.codex}/bin/load-sk-agent-context.sh"
skcapstone coord status
skcapstone coord reconcile-agents

# Verify both repositories before any work
 git -C /mnt/cloud/onedrive/projects/DAVE-AI/sklegal status --short --branch
 git -C /home/skuser01/work/skgateway status --short --branch
 git -C /home/skuser01/work/skgateway log -3 --oneline --decorate
```

The leading space before the final `git` command is intentional only for
readability and may be removed when running it.

## Safety boundary

No protected Matter data, provider credential, capability token, private key,
or HammerTime `Inbox/` material was accessed during this swarm wave. No
production service was activated. No external legal action, filing, service,
mailing, client communication, or calendar action was performed.
