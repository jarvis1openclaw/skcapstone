# SKLegal clean-room harness correction 8 review evidence

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

Correction 7 was frozen at inventory SHA-256
`b9e2c5ad5f7ba9251936872f1788accd611459f32cf47ab694d5e9028cde7f00`
and was rejected with two Medium findings. Parent external commands buffered
complete stdout and stderr before enforcing their output limit, and loopback
broker construction started threads before the broker could be registered in
the outer cleanup ledger. That frozen inventory and evidence remain unchanged
historical receipts.

Correction 8 closes those boundaries by:

- servicing external stdin, stdout, and stderr concurrently through
  nonblocking pipes instead of `communicate()`;
- counting external output as each pipe read completes and enforcing the
  4 MiB per-command and 8 MiB cumulative limits during production;
- reading only one detection byte beyond the available command or cumulative
  budget, clearing bounded capture immediately, escalating the process group
  from `TERM` to `KILL` when necessary, and returning only sanitized status
  126;
- retaining an external process in the cleanup ledger if immediate
  termination cannot be proven and denying all later parent work;
- making loopback broker construction thread-free and startup explicit;
- registering the provisional broker in the outer cleanup ledger before
  startup, then blocking `SIGINT` and `SIGTERM` across fixed worker and server
  creation;
- joining all partially started threads on every `BaseException`; and
- refusing to report broker cleanup until the registered thread set is empty.

The machine-readable receipt schema is now
`sklegal-clean-room-receipt/v6`. It adds only sanitized cumulative external
output byte and denial counts.

## TDD receipts

The correction regressions were written before implementation. The initial
three-test selection reported:

```text
Ran 3 tests in 0.009s
FAILED (failures=2, errors=1)
```

The error showed that no cumulative external-output budget existed. The first
failure found two already-running workers immediately after
`_LoopbackBroker.__init__`. The second showed that `run()` never called the
provisional broker's explicit startup method and incorrectly returned a passed
receipt after the injected startup interrupt.

The final focused receipt is:

```text
.tools/bin/uv run --locked python -W error::ResourceWarning -m unittest tests.test_clean_room_check
Ran 47 tests in 2.409s
OK

.tools/bin/uv run --locked ruff format --check scripts/clean_room_check.py tests/test_clean_room_check.py tests/integration/test_clean_room_containment.py
3 files already formatted

.tools/bin/uv run --locked ruff check scripts/clean_room_check.py tests/test_clean_room_check.py tests/integration/test_clean_room_containment.py
All checks passed!

.tools/bin/uv run --locked mypy scripts/clean_room_check.py
Success: no issues found in 1 source file
```

The focused external-process matrix covers stdout flood, stderr flood, mixed
streams, normal simultaneous input and output, oversized input, a process that
ignores `TERM`, and two individually valid 700-byte commands crossing a
1,200-byte cumulative cap. The cumulative case observed exactly 1,201 bytes,
killed the second process, retained no protected output, and returned only the
fixed denial. The lifecycle matrix proves initialization has zero threads,
successful startup has exactly the fixed set, and an exception after each of
two worker starts or the server start joins back to an empty set.

## Real host and PostgreSQL proof

The broker-start signal selection launched a fresh subprocess for every one of
four Docker worker starts, the Docker server start, four GPG worker starts, and
the GPG server start. A separate subprocess held repeated incomplete callers
open across both endpoints. The result was:

```text
SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1 .tools/bin/uv run --locked python -W error::ResourceWarning -m unittest -v tests.integration.test_clean_room_containment.SystemdContainmentIntegrationTests.test_real_sigterm_after_every_broker_thread_start_cleans tests.integration.test_clean_room_containment.SystemdContainmentIntegrationTests.test_real_sigterm_with_repeated_long_lived_broker_callers_cleans
Ran 2 tests in 4.934s
OK
```

Every child emitted a cancelled terminal receipt, reported broker cleanup only
after its thread count reached zero, and left no transient unit, cgroup,
container, broker directory, or caller process.

The allowed disposable PostgreSQL path ran a real `psql` query that generated
more than 4 MiB of stdout. The broker stopped capture while output was being
produced, returned empty stdout plus only `broker output limit exceeded`, and
normalized the command to status 126. A following `pg_isready` succeeded, and
final labeled-container readback was empty. The focused host case reported:

```text
Ran 1 test in 1.871s
OK
```

The final complete host matrix reported:

```text
SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1 .tools/bin/uv run --locked python -W error::ResourceWarning -m unittest -v tests.integration.test_clean_room_containment
Ran 15 tests in 126.036s
OK (skipped=1)
```

The one skip is the intentionally subprocess-only signal helper when the host
module is run directly. The two parent signal tests invoked it for all eleven
broker boundaries. All other containment, manager denial, filesystem,
path-swap, Docker, GPG, timeout, and descendant-extinction cases passed.

## Broker-backed 40-contract receipt

The complete installed foundation, CapAuth, and disposable PostgreSQL replay
passed through the two-phase fixed brokers. The resource receipt from the
final host suite was:

```text
broker-resource-counts={"budget_denials": 0, "connection_attempts": 966, "connections_denied": 0, "containers_created": 1, "external_output_bytes": 422806, "external_output_denials": 0, "keyrings_created": 1, "peak_external_processes": 2, "peak_workers": 2, "private_bytes_created": 3983, "private_bytes_current": 0, "private_bytes_peak": 2572, "private_bytes_removed": 3983, "private_files_created": 5, "private_files_current": 0, "private_files_peak": 3, "private_files_removed": 5, "request_bytes": 4062671, "requests_accepted": 966, "requests_denied": 0, "response_bytes": 619180, "verify_snapshots_created": 4, "wire_bytes_received": 4062671, "wire_bytes_sent": 619180}
```

Normal operation remained below every fixed request, wire, external-output,
worker, process, file, container, and keyring limit. Current private files and
bytes returned to zero, and created totals exactly matched removed totals.

## Broad non-clean-room receipts

```text
./scripts/run_checks.sh format-check
68 files already formatted; Prettier passed

./scripts/run_checks.sh lint
Ruff and ESLint passed

./scripts/run_checks.sh type-check
Mypy passed for 40 source files; TypeScript and the 15-module Vite build passed

./scripts/run_checks.sh unit-test
Python: 327 tests in 7.623s, OK
Frontend: 1 test, OK

./scripts/run_checks.sh integration-test
44 tests in 87.402s, OK

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
