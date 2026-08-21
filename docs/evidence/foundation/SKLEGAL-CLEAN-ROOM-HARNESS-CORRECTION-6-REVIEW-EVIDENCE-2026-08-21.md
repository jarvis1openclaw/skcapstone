# SKLegal clean-room harness correction 6 review evidence

Date: 2026-08-21

Board card: `d9e11d5d`

Owner: `codex-qualify`

State at freeze: Doing. Fresh independent review is required before any full
clean-room qualification or board transition.

## Scope and review history

This correction changes only the separate qualification harness, its synthetic
unit and disposable host tests, foundation documentation, and this evidence.
It does not change the accepted SKL-S1-05 audit migration, package source,
policy adapters, telemetry, or audit tests.

Correction 5 was frozen at inventory SHA-256
`81e60a5e3e3aa16766c0933f2eb7f15d179d6d44065a2cd51371a69cb2232f42`
and was rejected with two findings. The parent Docker and GPG brokers lacked a
finite session resource envelope, and the containment launcher still had an
interrupt window after process creation but before an owned handle was
registered and bound. That frozen inventory and evidence remain unchanged
historical receipts.

Correction 6 closes those boundaries by:

- sharing one immutable budget across the Docker and GPG loopback brokers,
  with four worker slots, two external-process slots, 1,536 requests, 16 MiB
  cumulative request bytes, 8 MiB cumulative response bytes, 8 MiB frames,
  4 MiB input and file limits, and 4 MiB combined command output limits;
- allowing only one PostgreSQL container creation attempt and one physical GPG
  keyring and agent per broker session, including after failure or removal;
- deleting every descriptor-held GPG verification snapshot in the command
  `finally` path and retaining only an immutable private-ledger handle until
  that deletion completes;
- returning a single sanitized budget-exhausted response and never exposing a
  broker credential or rejected payload in terminal output;
- applying two CPU, 1 GiB memory and swap, 256 PID, 1,024 file-descriptor, and
  30-second stop limits to the exact pinned disposable PostgreSQL container;
- wrapping the pinned image entrypoint in BusyBox `/usr/bin/timeout -s TERM -k
  30 900`, so the disposable container self-terminates even if its parent
  broker disappears;
- emitting only sanitized resource counters in the version 4 terminal receipt;
- registering a provisional exact unit and candidate-cgroup handle before
  controller process creation;
- blocking `SIGINT` and `SIGTERM` across registration, `Popen`, and immediate
  process-slot assignment, and restoring the previous signal mask afterward;
  and
- retaining the provisional or observed handle through every `BaseException`
  path, then applying `TERM`, population readback, `KILL`, and direct
  `cgroup.kill` fallback before accepting extinction.

## TDD receipts

The correction regressions were written before implementation. The initial
five-method unit selection reported:

```text
Ran 7 tests
FAILED (failures=2, errors=5)
```

The errors were the missing `_BrokerLimits`, missing broker resource receipt,
and missing containment lifecycle hook. The failures demonstrated that a
second distinct PostgreSQL container was accepted and GPG verification
snapshots accumulated until broker shutdown. No external service was used by
this red unit run.

After implementation, that exact five-method selection reported seven passing
subtests in 1.467 seconds. The final complete focused receipts are:

```text
.tools/bin/uv run --locked python -m unittest tests.test_clean_room_check
Ran 40 tests in 2.700s
OK

.tools/bin/uv run --locked ruff format --check scripts/clean_room_check.py tests/test_clean_room_check.py tests/integration/test_clean_room_containment.py
3 files already formatted

.tools/bin/uv run --locked ruff check scripts/clean_room_check.py tests/test_clean_room_check.py tests/integration/test_clean_room_containment.py
All checks passed!

.tools/bin/uv run --locked mypy scripts/clean_room_check.py
Success: no issues found in 1 source file
```

The unit matrix covers request, request-byte, response-byte, frame, input,
staged-file, and output exhaustion; concurrent worker and process ceilings;
repeated distinct container runs; one keyring; immediate verification snapshot
removal; sanitized denial; exact receipt counters; and injected interrupts
before spawn, immediately after process return, after process binding, and
after observed-cgroup binding.

## Real container limits and broker-backed contract proof

The first real container probe exposed a portability defect in the initial
TTL wrapper. BusyBox rejected GNU-style `--signal=TERM --kill-after=30`
arguments before PostgreSQL started. The exact labeled debug container was
removed and zero related container or transient-unit residue remained. The
implementation was corrected to BusyBox's supported `-s TERM -k 30 900` form.

The final host proof inspected the running container and confirmed two CPUs,
1 GiB memory and swap, 256 PIDs, `nofile=1024:1024`, a 30-second stop timeout,
the `/usr/bin/timeout` entrypoint, and the exact BusyBox TTL arguments before
`pg_isready` succeeded. A second distinct run was denied with status 126, the
first container was removed, and label readback found no residue.

The complete installed CapAuth and Docker broker-backed replay ran all 40
foundation, CapAuth, and persistence contracts. The final sanitized resource
measurement was:

```text
broker-resource-counts={"budget_denials": 0, "containers_created": 1, "keyrings_created": 1, "peak_external_processes": 2, "peak_workers": 2, "request_bytes": 4058226, "requests_accepted": 965, "requests_denied": 0, "response_bytes": 615260, "verify_snapshots_created": 4}
```

This is below every frozen session budget. It includes exact Compose forms,
the disposable PostgreSQL lifecycle, migration and provisioning commands,
synthetic OpenPGP key generation, detached signing and verification, and
tampered-signature denial. No snapshot file, synthetic gpg-agent, broker
directory, or labeled container remained.

## Complete disposable host proof

The targeted three-case host selection covering all injected launch
boundaries, binding and controller outage, and the exact resource-limited
broker path reported:

```text
Ran 3 tests in 6.735s
OK
```

The first complete correction-6 host matrix reported 10 of 11 tests passing in
147.236 seconds. The only mismatch was a stale assertion that expected an
interrupt at the earlier binding boundary to report false extinction, although
the strengthened provisional handle proved and returned true extinction. The
assertion was updated to the stronger invariant. The final exact-source host
matrix reported:

```text
SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1 .tools/bin/uv run --locked python -W error::ResourceWarning -m unittest -v tests.integration.test_clean_room_containment
Ran 11 tests in 147.245s
OK
```

It proves success, child failure, timeout, cancellation, every launch-boundary
interrupt, controller and binding outage, direct `cgroup.kill` fallback,
session budget denial, container limits and TTL, no parent-broker snapshot
accumulation, exact broker-backed contracts, and zero final process, cgroup,
unit, agent, broker-directory, or container residue.

## Broad non-clean-room receipts

```text
./scripts/run_checks.sh format-check
68 files already formatted; Prettier passed

./scripts/run_checks.sh lint
Ruff and ESLint passed

./scripts/run_checks.sh type-check
Mypy passed for 40 source files; TypeScript and the 15-module Vite build passed

./scripts/run_checks.sh unit-test
Python: 320 tests in 15.448s, OK
Frontend: 1 test, OK

./scripts/run_checks.sh integration-test
44 tests in 98.721s, OK

./scripts/run_checks.sh migration-check
migration manifest valid: 7 migration(s)

./scripts/run_checks.sh fixture-check
fixture safety valid: 4 file(s)

./scripts/run_checks.sh secret-scan
secret scan valid: no findings outside reviewed baseline
```

`scripts/bootstrap.sh` and `scripts/run_checks.sh` also pass `bash -n`.

## Accepted SKL-S1-05 boundary

The historical accepted audit inventory receipt remains byte-for-byte
unchanged:

```text
21a90c8f112b9ccde9f4f4d75ad48ff47d93a29b52764b77d509401a8c5d1ae0  docs/evidence/audit/SKL-S1-05-CORRECTION-3-FROZEN-INVENTORY-2026-08-20.sha256
```

A current verification of its 37 listed paths reports 36 matches. The sole
expected historical mismatch remains `scripts/run_checks.sh`, changed before
this correction under the separate harness card to register the harness unit
module. Every other accepted audit source, migration, adapter, model,
telemetry, contract, and evidence path remains exact. The accepted inventory
receipt was not edited or regenerated.

## Safety and limitations

- `make clean-room-check` was intentionally not run.
- No final qualification, board transition, deployment, commit, push,
  production action, live key access, protected corpus access, or external
  action occurred.
- The real host suite used only synthetic keys, files, and disposable
  PostgreSQL containers.
- No `.clean-bin`, `sklegal-broker-*`, broker-contract temporary directory,
  labeled PostgreSQL container, transient unit, cgroup, synthetic gpg-agent,
  or generated web build output remained at freeze.
