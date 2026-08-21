# SKLegal clean-room harness correction 3 review evidence

Date: 2026-08-20

Board card: `d9e11d5d`

Owner: `codex-qualify`

State at freeze: Doing. Fresh independent review is required before any full
clean-room qualification or board transition.

## Scope and review history

This narrow correction changes only the separate qualification harness, its
hermetic and disposable host tests, foundation documentation, and this
evidence. It does not change the accepted SKL-S1-05 audit migration, package
source, policy adapters, telemetry, or audit tests.

Correction 2 was frozen at inventory SHA-256
`00e923bb08f546d23469fc358dabc0223e1e0060815a795b4fe2f64b79b2d99a`
and was rejected. It remains a historical receipt. No full clean-room command
was run against correction 2 or correction 3.

Correction 3 addresses the final merged findings by:

- invoking absolute `/usr/bin/git` through a held repository descriptor and
  command-scope `core.fsmonitor=false`, `core.hooksPath=/dev/null`,
  `core.untrackedCache=false`, and `core.preloadIndex=false` overrides;
- using a minimal Git environment with optional index locks disabled, then
  retaining and rechecking the same repository device and inode through copy;
- accepting only exact trusted `/tmp` or canonical private
  `/run/user/$uid` as scratch roots, never renameable descendant overrides;
- binding workspace cleanup to both the scratch-parent and temporary-target
  device and inode identities, while still removing the exact created target
  through the held descriptor when the parent path is renamed;
- registering an unbound containment handle immediately after `systemd-run`
  returns and before clock, controller, polling, or cgroup binding work;
- distinguishing an unobserved deterministic candidate cgroup from an exact
  controller-observed binding, so candidate absence can never be used as
  positive extinction proof;
- catching `BaseException` during binding and cleanup, attempting cgroup-wide
  `TERM`, rechecking actual population, and escalating residuals to `KILL`;
- writing the kernel cgroup v2 `cgroup.kill` file through `O_NOFOLLOW` as a
  conclusive fallback when the user-systemd controller is unavailable;
- declaring systemd bind, read-only-parent, and exact writable-path properties;
  and
- requiring Landlock ABI 5 or newer in the same transient cgroup before
  `make check`, with filesystem read and execute by default and writes only to
  the exact workspace, validated uv and npm caches, and `/dev/null`.

## Host mount-boundary finding

The host has systemd 255, a working user manager, cgroup v2, and Landlock ABI
8. A disposable probe proved that user-systemd accepts and reports
`PrivateUsers`, `BindPaths`, `ReadOnlyPaths`, and `ReadWritePaths`, but creates
no service mount namespace on this host. The copied workspace could still be
renamed with those properties alone. Unprivileged `unshare` and `bwrap` both
fail at UID-map creation on this host.

The harness does not claim those inert user-manager properties as effective
enforcement. The Landlock ruleset is the effective kernel filesystem boundary,
runs inside the systemd-owned cgroup, fails closed before `make`, and is
verified below with a real rename-denial test.

## TDD receipts

Required adversarial tests were written before implementation. The focused red
receipt was:

```text
.tools/bin/uv run --locked python -m unittest tests.test_clean_room_check
Ran 27 tests in 0.129s
FAILED (failures=7, errors=2)
```

The red failures reproduced local Git fsmonitor execution, missing command
overrides, repository swap acceptance, a false successful cleanup after
scratch-parent rename, absent filesystem sandbox properties, a lost handle on
`BaseException`, a TERM-only residual cgroup, and absent direct cgroup kill.

The final focused receipt is:

```text
.tools/bin/uv run --locked ruff format scripts/clean_room_check.py tests/test_clean_room_check.py tests/integration/test_clean_room_containment.py
3 files left unchanged

.tools/bin/uv run --locked ruff check scripts/clean_room_check.py tests/test_clean_room_check.py tests/integration/test_clean_room_containment.py
All checks passed!

timeout --kill-after=5 30 .tools/bin/uv run --locked mypy scripts/clean_room_check.py
Success: no issues found in 1 source file

.tools/bin/uv run --locked python -m unittest -v tests.test_clean_room_check
Ran 27 tests in 0.130s
OK
```

The unit suite includes a real disposable Git repository whose configured
fsmonitor hook writes a marker under the vulnerable implementation. The final
test proves the marker is never created. Other hermetic tests inject repository
replacement, parent and target replacement, process and controller failures,
cgroup contents, and direct kill files.

## Disposable host containment proof

```text
SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1 .tools/bin/uv run --locked python -m unittest -v tests.integration.test_clean_room_containment
Ran 6 tests in 5.983s
OK
```

Synthetic disposable repositories prove all terminal paths, binding-time
interrupt and controller outage, actual direct `cgroup.kill`, zero unit and
cgroup residue, Landlock workspace and scratch-root rename denial, the exact
closed payload environment, and timeout removal of a parent, `setsid` child,
and double-fork descendant. The unobserved binding-failure cases deliberately
return a failed receipt even after zero-residue cleanup because a guessed path
is not accepted as integrity proof.

The sixth host case uses only synthetic files to prove bootstrap-shaped writes
to `.tools`, `node_modules`, and the exact uv and npm caches. It executes the
host Node 22 binary through the same policy and completes a raw Unix connection
to `/run/docker.sock` without sending a Docker request or creating a container.
This is a policy compatibility proof, not a full bootstrap or clean-room run.

## Broad non-clean-room receipts

```text
./scripts/run_checks.sh format-check
68 files already formatted; Prettier passed

./scripts/run_checks.sh lint
Ruff and ESLint passed

./scripts/run_checks.sh type-check
Mypy passed for 40 source files; TypeScript and the 15-module Vite build passed

./scripts/run_checks.sh unit-test
Python: 307 tests in 15.259s, OK
Frontend: 1 test, OK

./scripts/run_checks.sh integration-test
44 tests in 108.236s, OK

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
expected historical mismatch remains `scripts/run_checks.sh`, previously
changed under this separate harness card only to register
`tests.test_clean_room_check`. Every other accepted audit source, migration,
adapter, model, telemetry, contract, and evidence path remains exact. The
accepted inventory receipt was not edited or regenerated.

## Safety and limitations

- `make clean-room-check` was intentionally not run.
- The production harness requires a user systemd manager, cgroup v2, and
  Landlock ABI 5 or newer and fails closed without any one of them.
- The real host integration module remains opt-in for ordinary hermetic test
  environments.
- No final qualification, board transition, deployment, commit, push,
  production action, live key access, protected corpus access, or external
  action occurred.
- All fixtures contain only synthetic strings and files.
- No `sklegal-cleanroom-*` directory, transient unit, cgroup, or synthetic
  descendant process remained after the host proof.
