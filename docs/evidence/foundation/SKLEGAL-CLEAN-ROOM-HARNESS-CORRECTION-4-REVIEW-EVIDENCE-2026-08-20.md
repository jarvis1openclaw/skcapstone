# SKLegal clean-room harness correction 4 review evidence

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

Correction 3 was frozen at inventory SHA-256
`491e627980056188580aa75ed6fdebf11c7efab2fdcf91e7ffc2744a209df486`
and was rejected. Its Landlock filesystem boundary did not mediate Unix socket
connections. A checked process could ask the user systemd manager to create a
separate service outside the registered cgroup or reach the host Docker socket.
That inventory and evidence remain unchanged as historical receipts. No full
clean-room command was run against correction 4.

Correction 4 closes the external-manager boundary by:

- restricting the checked transient service to `AF_INET` and `AF_INET6`, so it
  cannot connect to the user bus, Docker socket, GPG agent, or another host
  Unix socket;
- granting Landlock execute rights only to a curated exact executable set and
  copied-workspace files, excluding direct Docker, GPG, gpgconf, systemctl, and
  systemd-run paths;
- creating parent-owned loopback Docker and GPG brokers authenticated with a
  random per-run credential that is excluded from the terminal receipt;
- installing only generated `.clean-bin/docker` and `.clean-bin/gpg` shims in
  the copied workspace and placing that directory first in the closed path;
- accepting only the exact Compose, disposable PostgreSQL, psql, synthetic-key,
  detached-signing, and verification forms required by the frozen checks;
- rejecting arbitrary container images, mounts, networks, exec programs,
  untracked containers, Compose paths, keyrings, keys, and GPG operations;
- recording a container and synthetic-keyring cleanup ledger before external
  work, terminating active broker command process groups on every close path,
  and verifying container, gpg-agent socket, and worker extinction; and
- adding `external_brokers_cleaned` to the v3 machine receipt and failing closed
  when any broker or ledger cleanup cannot be proved.

## TDD receipts

The adversarial tests were written before implementation. The first focused
unit red receipt was:

```text
.tools/bin/uv run --locked python -m unittest tests.test_clean_room_check
Ran 30 tests in 0.119s
FAILED (failures=1, errors=18)
```

The first disposable host red receipt was:

```text
SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1 .tools/bin/uv run --locked python -m unittest tests.integration.test_clean_room_containment
Ran 8 tests in 26.719s
FAILED (failures=3)
```

Those failures reproduced raw Docker socket access, direct external-manager
execution, nested systemd service creation, missing address-family restriction,
and absent broker validators.

The final focused receipts are:

```text
.tools/bin/uv run --locked python -m unittest tests.test_clean_room_check
Ran 32 tests in 0.514s
OK

.tools/bin/uv run --locked ruff check scripts/clean_room_check.py tests/test_clean_room_check.py tests/integration/test_clean_room_containment.py
All checks passed!

.tools/bin/uv run --locked mypy scripts/clean_room_check.py
Success: no issues found in 1 source file
```

The unit suite covers exact systemd properties, direct-manager exclusion,
minimal environment construction, exact Docker and GPG validators, arbitrary
image, mount, network, exec and path denial, active broker-process termination,
cleanup failure propagation, and credential omission from the receipt.

## Exact CapAuth and persistence broker proof

The opt-in host probe ran the repository's real foundation, CapAuth, and full
persistence contract modules through the generated broker shims:

```text
SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1 .tools/bin/uv run --locked python -m unittest tests.integration.test_clean_room_containment.SystemdContainmentIntegrationTests.test_brokers_run_exact_foundation_capauth_and_persistence_contracts
Ran 1 test in 133.881s
OK
```

The selected child suite reported `Ran 40 tests in 132.227s` and `OK`. It used
the installed CapAuth public API to issue a capability, perform detached
OpenPGP signing, verify the exact payload, authorize it, alter the signature,
and deny the tampered capability as `INVALID_SIGNATURE`. The probe then closed
the broker while its tracked synthetic homes still existed and asserted every
tracked gpg-agent socket was gone.

The same child suite exercised every Docker argument shape used by the
foundation and complete persistence modules: Compose config, running-service
status, collision checks, exact pinned disposable PostgreSQL launch,
`pg_isready`, parameterized psql through migration and principal provisioner
`--docker-container` paths, inspect, filtered ps, and forced removal. It also
proved malformed Docker commands are denied and no labeled test container
remains.

The first run of this new wrapper was intentionally retained as an honest probe
receipt: all 40 selected child tests passed, but the wrapper expected 44 because
it mistakenly counted four unselected domain tests. The wrapper therefore
reported one assertion failure after 133.203 seconds. Correcting only the
expected selected count and keeping the temporary home alive through broker
cleanup produced the green receipt above.

## Complete disposable host containment proof

```text
SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1 .tools/bin/uv run --locked python -m unittest tests.integration.test_clean_room_containment
Ran 9 tests in 143.960s
OK
```

The nine cases use only synthetic disposable repositories and prove success,
ordinary failure, timeout, cancellation, controller and binding outage,
direct `cgroup.kill`, no unit or cgroup residue, scratch and workspace rename
denial, local Git hook nonexecution, closed child environment, raw Docker Unix
socket denial, direct systemd, Docker, and GPG binary denial, nested systemd
service denial, exact broker operation, and removal of parent, `setsid`, and
double-fork descendants.

The final exact external-manager case also probes the raw user bus socket:

```text
SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1 .tools/bin/uv run --locked python -m unittest tests.integration.test_clean_room_containment.SystemdContainmentIntegrationTests.test_external_manager_paths_and_nested_user_service_are_denied
Ran 1 test in 0.448s
OK
```

Both `/run/docker.sock` and `/run/user/$uid/bus` were denied from inside the
checked service.

## Broad non-clean-room receipts

```text
./scripts/run_checks.sh format-check
68 files already formatted; Prettier passed

./scripts/run_checks.sh lint
Ruff and ESLint passed

./scripts/run_checks.sh type-check
Mypy passed for 40 source files; TypeScript and the 15-module Vite build passed

./scripts/run_checks.sh unit-test
Python: 312 tests in 11.574s, OK
Frontend: 1 test, OK

./scripts/run_checks.sh integration-test
44 tests in 108.646s, OK

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
- The production harness requires a user systemd manager, cgroup v2, Landlock
  ABI 5 or newer, and local loopback networking. It fails closed without those
  controls.
- The real host integration module remains opt-in for ordinary hermetic test
  environments.
- Brokers expose only repository-test contracts. They are not general Docker,
  GPG, or external-service proxies.
- No final qualification, board transition, deployment, commit, push,
  production action, live key access, protected corpus access, or external
  action occurred.
- All fixtures contain only synthetic strings and files.
- No `.clean-bin`, broker-contract temporary directory, labeled PostgreSQL
  container, `sklegal-clean-room-*` unit, cgroup, or synthetic descendant
  remained after the host proof.
