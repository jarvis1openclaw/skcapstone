# SKLegal clean-room harness correction 7 review evidence

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

Correction 6 was frozen at inventory SHA-256
`2fbe012b655a45b719e72158d23f18b9882803ab2e7bc3943b7e1d711dca0618`
and was rejected with three findings. Broker byte limits were applied only
after a complete frame had been allocated and parsed, per-connection threads
were retained, failed private staging could leave partial destinations until
broker shutdown, and process `SIGTERM` could bypass the outer cleanup and
terminal receipt path. That frozen inventory and evidence remain unchanged
historical receipts.

Correction 7 closes those boundaries by:

- reserving cumulative request capacity after the four-byte prefix and before
  any declared body allocation or read;
- reserving response capacity before sending any frame and counting each byte
  actually returned by `recv` and `send`, including headers, malformed frames,
  truncated bodies, semantic denials, handler errors, and budget failures;
- recording sanitized connection attempts and denials separately from complete
  requests and request denials;
- replacing all per-connection thread creation with a fixed precreated worker
  tuple and bounded connection queue, while retaining the shared active-worker
  ceiling across both endpoints;
- closing and identity-unlinking each newly created copy destination in
  `_copy_one` before rethrowing any copy or verification failure;
- applying an independent immediate cleanup path to every broker-private file
  stage and private keyring directory creation;
- tracking descriptor-staged file and byte creation, removal, current, and peak
  counts, and requiring both current counts to return to zero at broker close;
- installing a sanitized parent `SIGTERM` handler before preflight and retaining
  it through containment, broker, and workspace cleanup; and
- converting a signal pending in the blocked process-creation window, or a
  signal during the running gate, into one controlled cancelled receipt before
  restoring the original handler.

## TDD receipts

The correction regressions were written before implementation. The initial
three-test unit selection reported:

```text
Ran 3 tests in 0.060s
FAILED (failures=1, errors=2)
```

The errors were the missing bounded-queue limit and missing private file and
byte ledger counters. The failure showed that real `SIGTERM` killed the child
test process with return code `-15` immediately after the preflight phase,
without a terminal receipt.

The final focused receipt is:

```text
.tools/bin/uv run --locked python -W error::ResourceWarning -m unittest tests.test_clean_room_check
Ran 43 tests in 4.116s
OK

.tools/bin/uv run --locked ruff format --check scripts/clean_room_check.py tests/test_clean_room_check.py tests/integration/test_clean_room_containment.py
3 files already formatted

.tools/bin/uv run --locked ruff check scripts/clean_room_check.py tests/test_clean_room_check.py tests/integration/test_clean_room_containment.py
All checks passed!

.tools/bin/uv run --locked mypy scripts/clean_room_check.py
Success: no issues found in 1 source file
```

The wire test executes 300 sequential connections, one oversized declaration,
one truncated body, and one handler exception. The worker tuple retains the
same identities, the connection set and queue return to zero, and received and
sent byte counts match the exact frame sizes. A separate cumulative-budget
case reads only the four-byte header, does not allocate or parse the denied
body, returns a sanitized response, and never exceeds the four-byte budget.
The concurrent test fills one active worker and one queued slot, then verifies
that the next connection is denied with sanitized status 126.

The private-stage tests repeat post-copy reopen failure, source-copy exception,
and private keyring-open failure. Every created destination is absent before
the next attempt, the outside fixtures remain unchanged, and staged file and
byte counters remain exact.

## Real SIGTERM and host containment proof

A dedicated parent test launched fresh subprocesses and delivered actual
`SIGTERM` at `before_spawn`, `process_bound`, `observed_bound`, and the running
gate phase. The focused parent result was:

```text
SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1 .tools/bin/uv run --locked python -W error::ResourceWarning -m unittest -v tests.integration.test_clean_room_containment.SystemdContainmentIntegrationTests.test_real_sigterm_at_every_launch_and_run_boundary_cleans
Ran 1 test in 2.875s
OK
```

Each child emitted a cancelled receipt, reported broker and workspace cleanup,
restored its original handler, and left its exact transient unit and cgroup
absent. A signal queued while `SIGTERM` was blocked did not default-kill the
parent between controller creation and process-slot assignment.

The final complete host matrix reported:

```text
SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1 .tools/bin/uv run --locked python -W error::ResourceWarning -m unittest -v tests.integration.test_clean_room_containment
Ran 13 tests in 120.057s
OK (skipped=1)
```

The one skip is the intentionally subprocess-only SIGTERM helper when the host
module is run directly. The parent test invoked that helper once for each of
the four required signal phases. All other success, failure, timeout,
cancellation, direct cgroup kill, manager denial, filesystem sandbox, broker,
path-swap, and descendant-extinction cases passed.

## Broker-backed 40-contract resource receipt

The final host suite ran the installed foundation, CapAuth, and complete
disposable PostgreSQL persistence contracts through the fixed brokers. Its
post-close resource receipt was:

```text
broker-resource-counts={"budget_denials": 0, "connection_attempts": 965, "connections_denied": 0, "containers_created": 1, "keyrings_created": 1, "peak_external_processes": 2, "peak_workers": 2, "private_bytes_created": 3983, "private_bytes_current": 0, "private_bytes_peak": 2572, "private_bytes_removed": 3983, "private_files_created": 5, "private_files_current": 0, "private_files_peak": 3, "private_files_removed": 5, "request_bytes": 4062082, "requests_accepted": 965, "requests_denied": 0, "response_bytes": 619104, "verify_snapshots_created": 4, "wire_bytes_received": 4062082, "wire_bytes_sent": 619104}
```

The replay is below every frozen request, byte, worker, process, file, output,
container, and keyring limit. All four verification snapshots were removed in
their command `finally` paths. Final Compose cleanup brought current private
files and bytes to zero, and created totals exactly equal removed totals.

## Broad non-clean-room receipts

```text
./scripts/run_checks.sh format-check
68 files already formatted; Prettier passed

./scripts/run_checks.sh lint
Ruff and ESLint passed

./scripts/run_checks.sh type-check
Mypy passed for 40 source files; TypeScript and the 15-module Vite build passed

./scripts/run_checks.sh unit-test
Python: 323 tests in 8.888s, OK
Frontend: 1 test, OK

./scripts/run_checks.sh integration-test
44 tests in 87.706s, OK

./scripts/run_checks.sh migration-check
migration manifest valid: 7 migration(s)

./scripts/run_checks.sh fixture-check
fixture safety valid: 4 file(s)

./scripts/run_checks.sh secret-scan
secret scan valid: no findings outside reviewed baseline
```

`scripts/bootstrap.sh` and `scripts/run_checks.sh` also pass `bash -n`.

## Cleanup and accepted SKL-S1-05 boundary

The initial red private-ledger test stopped at its expected missing counter
before entering its fixture `finally` block. Readback later found that one
synthetic broker directory with only its Compose snapshot. Its exact file was
unlinked and its exact directory was removed. Subsequent focused and host runs
left no broker or clean-room temporary directory, generated web artifact,
`.clean-bin`, labeled container, transient unit, cgroup, or synthetic gpg-agent.

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
