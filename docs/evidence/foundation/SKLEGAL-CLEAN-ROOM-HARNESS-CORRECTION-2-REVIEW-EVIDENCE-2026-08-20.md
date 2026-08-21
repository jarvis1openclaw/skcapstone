# SKLegal clean-room harness correction 2 review evidence

Date: 2026-08-20

Board card: `d9e11d5d`

Owner: `codex-qualify`

State at freeze: Doing. Fresh independent review is required before any full
clean-room qualification or board transition.

## Scope and review history

This correction changes only the separate qualification harness, its hermetic
tests, foundation documentation, and this evidence. It does not change the
accepted SKL-S1-05 audit migration, package source, policy adapters, telemetry,
or audit tests.

The earlier correction 1 inventory receipt had SHA-256
`cbe5fdb32fa84037ec01fa28a989f76e274b9be719b9619d0080a806be397147` and
was rejected. It remains historical and was not used as an acceptance receipt.
No full clean-room qualification was run against either correction.

Correction 2 addresses the merged review findings by:

- constructing the nested gate environment from an exact eight-key allowlist,
  clearing the systemd manager environment, and invoking the payload through
  `/usr/bin/env -i`;
- validating absolute root-owned launcher and controller executables, fixed
  trusted PATH directories, and canonical user-owned uv and npm cache roots;
- requiring a transient user systemd service with `ExitType=cgroup`,
  `KillMode=control-group`, deterministic unit identity, bounded runtime, and
  exact cgroup binding before the nested gate is accepted as started;
- failing closed when systemd, the user manager, exact cgroup binding, or final
  inactive and empty cgroup proof is unavailable;
- applying `TERM` then `KILL` to the complete cgroup on timeout, cancellation,
  exception, uncertain final state, and failed-start cleanup;
- accepting scratch only at exact trusted `/run/user/$uid`, trusted `/tmp`, or
  a canonical private user-owned override strictly beneath one of those local
  anchors;
- holding descriptors for the exact temporary directory and its parent,
  detecting rename or replacement, cleaning only the created device and inode,
  and preventing a passing receipt when cleanup or identity proof fails;
- moving repository validation and every preflight under the terminal receipt
  envelope;
- traversing source and destination parents with descriptor-relative
  `O_NOFOLLOW`, requiring regular singly-linked sources and destinations,
  requiring stable source device and inode metadata, creating destinations
  exclusively, and comparing every copied byte and digest before launch; and
- emitting flushed phase and elapsed-time events plus one terminal
  `sklegal-clean-room-receipt/v2` result bound to source inventory, temporary
  identity and cleanup, unit identity, and cgroup extinction.

## TDD receipts

Required adversarial tests were written before the implementation. The focused
red receipt was:

```text
.tools/bin/uv run --locked python -m unittest tests.test_clean_room_check
Ran 16 tests
FAILED (failures=6, errors=1)
```

The red failures covered ambient environment inheritance, absent descendant
containment, hardlink and inode-swap acceptance, a false pass after temporary
rename, arbitrary scratch placement, and repository preflight outside the
terminal receipt envelope.

The final focused source receipt is:

```text
.tools/bin/uv run --locked ruff format --check scripts/clean_room_check.py tests/test_clean_room_check.py tests/integration/test_clean_room_containment.py
3 files already formatted

.tools/bin/uv run --locked ruff check scripts/clean_room_check.py tests/test_clean_room_check.py tests/integration/test_clean_room_containment.py
All checks passed!

.tools/bin/uv run --locked mypy scripts/clean_room_check.py
Success: no issues found in 1 source file

.tools/bin/uv run --locked python -m unittest -v tests.test_clean_room_check
Ran 22 tests in 0.074s
OK
```

The hermetic unit suite injects the allowlist runner, scratch and cache roots,
workspace and containment boundary, process outcomes, and copy-race hooks. It
covers the exact closed child and controller environments, hostile ambient
credentials and startup controls, trusted local scratch, cache and link
denial, source and destination link and hardlink denial, inode replacement,
exclusive destination creation, copy drift, deterministic cgroup binding,
manager outage, failed-start kill cleanup, success, child failure, timeout,
post-`KILL` exhaustion, cancellation, generic exception, containment state
outage and recovery, non-extinction, temporary rename and replacement, exact
cleanup identity, progress, receipt output, and bootstrap kill bounds.

## Disposable host containment proof

The opt-in integration module used only synthetic `/tmp` repositories and
files and the host user systemd manager. It did not run the repository's full
clean-room command:

```text
SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1 .tools/bin/uv run --locked python -m unittest -v tests.integration.test_clean_room_containment
Ran 3 tests in 2.855s
OK
```

This proves success, child failure, delayed cancellation, delayed unexpected
exception, the exact closed payload environment, and timeout escalation. The
timeout case records a parent, a `setsid` child, and a double-fork descendant;
all three `/proc` entries are absent after final cgroup proof. Each case reports
the transient service extinct and the exact temporary directory cleaned.

## Broad non-clean-room receipts

```text
./scripts/run_checks.sh format-check
68 files already formatted
Prettier: all matched files use Prettier code style
exit 0

./scripts/run_checks.sh lint
Ruff: all checks passed
ESLint: exit 0

./scripts/run_checks.sh type-check
mypy: success, no issues in 40 source files
TypeScript: exit 0
Vite: 15 modules transformed, exit 0

./scripts/run_checks.sh unit-test
Python: 302 tests in 16.204s, OK
Frontend: 1 file and 1 test passed
exit 0

bash -n scripts/bootstrap.sh scripts/run_checks.sh scripts/clean_room_check.py
exit 0

./scripts/run_checks.sh fixture-check
fixture safety valid: 4 files

./scripts/run_checks.sh migration-check
migration manifest valid: 7 migrations

./scripts/run_checks.sh secret-scan
secret scan valid: no findings outside reviewed baseline

.tools/bin/uv run --locked python -m unittest -v tests.integration.test_clean_room_containment
Ran 3 tests in 0.000s
OK (skipped=3)
```

An intermediate secret scan correctly reported one `Secret Keyword` finding in
the hostile-environment unit fixture. The fixture did not contain a credential,
but its synthetic mapping combined a sensitive variable name with a literal
value. The test now selects that name from the harness's closed unset contract
and uses a neutral synthetic marker. The final focused unit suite and broad
secret scan both pass.

## Accepted SKL-S1-05 boundary

The historical accepted audit inventory receipt remains byte-for-byte
unchanged:

```text
21a90c8f112b9ccde9f4f4d75ad48ff47d93a29b52764b77d509401a8c5d1ae0  docs/evidence/audit/SKL-S1-05-CORRECTION-3-FROZEN-INVENTORY-2026-08-20.sha256
```

A current verification of its 37 listed paths reports 36 matches. The sole
expected historical mismatch is `scripts/run_checks.sh`, previously changed
under this separately authorized harness card only to register
`tests.test_clean_room_check`. All other accepted audit source, migration,
adapter, model, telemetry, contract, and evidence paths remain exact. The
accepted inventory receipt was not edited or regenerated.

## Safety and limitations

- `make clean-room-check` was intentionally not run.
- The host proof requires an available systemd user manager and cgroup v2; the
  production harness fails closed without them.
- The integration module is opt-in so ordinary hermetic unit environments do
  not gain an undeclared systemd dependency.
- No final qualification, board transition, deployment, commit, push,
  production action, live key access, protected corpus access, or external
  action occurred.
- All fixtures contain only synthetic strings and files.
- No `sklegal-cleanroom-*` directory or transient unit remained after the host
  proof.
