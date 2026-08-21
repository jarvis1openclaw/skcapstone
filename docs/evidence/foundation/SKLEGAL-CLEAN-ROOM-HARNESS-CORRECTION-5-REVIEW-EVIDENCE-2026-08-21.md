# SKLegal clean-room harness correction 5 review evidence

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

Correction 4 was frozen at inventory SHA-256
`f300bc2cfe067a7609592011ad6593b98d68592af035686bb677fa67f4f6d14b`
and was rejected. Its GPG broker validated a logical workspace home, then
passed and later resolved that mutable payload path from the unrestricted
parent. Replacing the logical home with an outside symlink could redirect both
GPG execution and gpg-agent cleanup. That frozen inventory and evidence remain
unchanged historical receipts.

Correction 5 closes the shared parent-path boundary by:

- opening and retaining the exact copied workspace directory descriptor before
  any checked payload starts;
- creating a separate `sklegal-broker-*` directory under trusted local `/tmp`,
  with its parent, directory, file, and keyring identities held by descriptors;
- staging the exact inventory Compose file through descriptor traversal before
  payload launch and passing Docker only the broker-private snapshot and
  private working directory;
- validating each first-use logical GPG home through the held workspace
  descriptor, then mapping its lexical identity to a newly created physical
  broker-private keyring;
- reusing only that physical mapping for key generation, listing, detached
  signing, verification, gpg-agent discovery, and cleanup, without later
  resolving the logical home;
- staging signature and payload verification inputs through held descriptors
  into per-request private snapshots, so parent GPG receives no payload path;
- holding every cleanup target as an immutable broker-owned descriptor and
  identity, failing closed on replacement, and removing only the exact private
  root without following a swapped entry; and
- closing all private descriptors and requiring exact broker-private directory
  removal before `external_brokers_cleaned` can be true; and
- replacing Landlock's root-wide read grant with explicit system read roots,
  so the checked payload cannot enumerate or read the parent's broker-private
  state under `/tmp`.

## TDD receipts

The new regressions were written before implementation. The initial GPG and
Compose use tests reported:

```text
.tools/bin/uv run --locked python -m unittest tests.test_clean_room_check.CleanRoomCheckTests.test_gpg_broker_uses_private_keyring_and_snapshots_verify_inputs tests.test_clean_room_check.CleanRoomCheckTests.test_compose_broker_uses_prechecked_private_snapshot_after_path_swap
Ran 2 tests in 0.013s
FAILED (errors=2)
```

Both errors were the missing `_private_workspace` boundary. The initial
Compose-link validation regression separately reported:

```text
Ran 1 test in 0.204s
FAILED (failures=1)
```

The vulnerable broker accepted the symlink instead of failing before launch.

An adjacent real-host visibility regression initially proved that the checked
payload could enumerate the parent broker's private directory:

```text
Ran 1 test in 1.760s
FAILED (failures=1)
observed private_state: visible
```

After narrowing the Landlock read policy, the same checked payload received an
access denial while its exact Docker and GPG broker operations still passed.

```text
Ran 1 test in 1.988s
OK
```

The final focused receipts are:

```text
.tools/bin/uv run --locked python -m unittest tests.test_clean_room_check
Ran 37 tests in 1.407s
OK

.tools/bin/uv run --locked ruff format --check scripts/clean_room_check.py tests/test_clean_room_check.py tests/integration/test_clean_room_containment.py
3 files already formatted

.tools/bin/uv run --locked ruff check scripts/clean_room_check.py tests/test_clean_room_check.py tests/integration/test_clean_room_containment.py
All checks passed!

.tools/bin/uv run --locked mypy scripts/clean_room_check.py
Success: no issues found in 1 source file
```

The unit tests cover initial logical Compose and GPG links, GPG home replacement
between mapping and use, private verification input snapshots, Compose source
replacement after prelaunch staging, logical-home replacement before cleanup,
and physical private-keyring replacement. They also prove that the Landlock
read roots exclude `/` and `/tmp`. Outside sentinel bytes and directory entries
remain exact. A private-ledger identity replacement makes close return false
while the exact broker-private root is still removed.

## Real broker swap and exact-contract proofs

The actual GPG and actual Docker Compose swap probe reported:

```text
SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1 .tools/bin/uv run --locked python -m unittest tests.integration.test_clean_room_containment.SystemdContainmentIntegrationTests.test_real_brokers_resist_gpg_home_and_compose_path_swaps
Ran 1 test in 0.524s
OK
```

It generated and listed a synthetic key, replaced the logical home with an
outside symlink, signed and verified a synthetic payload through the existing
physical mapping, replaced the workspace Compose path with an outside symlink,
and successfully rendered only the prelaunch private snapshot. The outside
directory remained sentinel-only, the tracked gpg-agent socket disappeared,
and the broker-private directory was removed.

The installed CapAuth and complete Docker contract replay also remained green:

```text
SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1 .tools/bin/uv run --locked python -m unittest tests.integration.test_clean_room_containment.SystemdContainmentIntegrationTests.test_brokers_run_exact_foundation_capauth_and_persistence_contracts
Ran 1 test in 147.148s
OK
```

The child ran 40 foundation, CapAuth, and persistence contracts. It exercised
CapAuth detached signing, exact verification, tampered-signature denial, every
foundation Compose form, and the full disposable PostgreSQL migration and
provisioner command surface.

## Complete disposable host proof

The first full host replay honestly failed before containment launch because
eight older synthetic fixture repositories omitted the now-required inventory
Compose file:

```text
SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1 .tools/bin/uv run --locked python -m unittest tests.integration.test_clean_room_containment
Ran 10 tests in 159.083s
FAILED (failures=3, errors=5)
```

No broker directory or transient unit remained. Adding the minimal synthetic
`deploy/chiap01/compose.dev.yml` inventory file to the shared fixture builder
made the two representative terminal and manager cases pass in 2.976 seconds.
The correction-5 matrix first reported 10/10 in 143.956 seconds. After adding
the broker-private visibility regression and narrowing Landlock's system read
roots, the complete matrix reported:

```text
Ran 10 tests in 147.310s
OK
```

These cases prove ordinary success and failure, timeout, cancellation,
controller and binding outage, direct cgroup kill, scratch and workspace rename
denial, direct manager and Unix-socket denial, broker contracts, path-swap
resistance, gpg-agent and private-root cleanup, and parent, `setsid`, and
double-fork descendant extinction.

## Broad non-clean-room receipts

```text
./scripts/run_checks.sh format-check
68 files already formatted; Prettier passed

./scripts/run_checks.sh lint
Ruff and ESLint passed

./scripts/run_checks.sh type-check
Mypy passed for 40 source files; TypeScript and the 15-module Vite build passed

./scripts/run_checks.sh unit-test
Python: 317 tests in 18.819s, OK
Frontend: 1 test, OK

./scripts/run_checks.sh integration-test
44 tests in 107.647s, OK

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
- The real host suite remains opt-in and uses only synthetic keys, files, and
  disposable PostgreSQL containers.
- No `.clean-bin`, `sklegal-broker-*`, broker-contract temporary directory,
  labeled PostgreSQL container, transient unit, cgroup, synthetic gpg-agent, or
  generated web build output remained at freeze.
