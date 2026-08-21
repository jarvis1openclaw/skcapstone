# SKLegal clean-room harness review evidence

Date: 2026-08-20

Board card: `d9e11d5d`

Owner: `codex-qualify`

State at freeze: Doing. Independent review is required before any full
clean-room qualification or board transition.

## Scope

This correction changes only the qualification harness, bootstrap bounds,
test registration, foundation documentation, and this evidence. It does not
change the accepted SKL-S1-05 audit implementation, migrations, adapters,
models, telemetry, or audit tests.

The harness now:

- selects validated local scratch from an eligible `XDG_RUNTIME_DIR` or `/tmp`;
- accepts `SKLEGAL_CLEAN_ROOM_ROOT` only when it is an existing absolute,
  canonical, privately writable directory owned by the current user;
- rejects repository overlap, synced-parent placement, path escapes, broad
  roots, unsafe permissions, and linked scratch or temporary roots;
- obtains the copy allowlist with exact `git ls-files --cached --others
  --exclude-standard -z` arguments and rejects generated-output path parts;
- rejects links and non-regular files at source and destination boundaries;
- compares SHA-256 before and after every copy, then emits a source inventory
  digest before starting the nested gate;
- shares only the existing uv and npm content cache paths, while copying no
  `.tools`, `.venv`, `node_modules`, `build`, or other generated output;
- invokes exactly `make check` in a new process group with the inherited
  environment plus canonical cache paths and `UV_LINK_MODE=copy`;
- emits flushed JSON phase events with elapsed time and one terminal
  `sklegal-clean-room-receipt/v1` receipt;
- bounds the gate, sends `TERM`, then `KILL`, normalizes child status, and uses
  `TemporaryDirectory` cleanup for success, failure, timeout, cancellation, and
  post-`KILL` wait exhaustion; and
- adds `timeout --kill-after=30` to every existing bootstrap timeout without
  changing exact uv lock or npm offline and lifecycle-audit flags.

## TDD receipts

Initial focused red:

```text
.tools/bin/uv run --locked python -m unittest tests.test_clean_room_check
Ran 8 tests
FAILED (errors=10)
```

The errors were missing harness injection and receipt contracts. A later
adversarial addition for invalid timeout values was also observed red as four
expected assertion failures before duration validation was implemented.

Focused green after the coherent implementation:

```text
.tools/bin/uv run --locked ruff format --check scripts/clean_room_check.py tests/test_clean_room_check.py
2 files already formatted

.tools/bin/uv run --locked ruff check scripts/clean_room_check.py tests/test_clean_room_check.py
All checks passed!

.tools/bin/uv run --locked mypy scripts/clean_room_check.py
Success: no issues found in 1 source file

.tools/bin/uv run --locked python -m unittest tests.test_clean_room_check
Ran 9 tests in 0.009s
OK
```

Hermetic tests use synthetic files, an injected Git allowlist runner, an
injected temporary-directory factory and scratch root, an injected copy
operation, an injected process factory, and an injected process-group signal
function. They cover local and explicit-override placement, scratch and link
denial, generated-output denial, copy drift, exact gate arguments and
environment, success, normalized failure, periodic progress, timeout, `TERM`,
`KILL`, post-`KILL` wait exhaustion, cancellation, and cleanup.

## Broad non-clean-room receipts

```text
./scripts/run_checks.sh format-check
67 files already formatted
Prettier: all matched files use Prettier code style
exit 0

./scripts/run_checks.sh lint
Ruff: all checks passed
ESLint: exit 0

./scripts/run_checks.sh type-check
mypy: success, no issues in 40 source files
TypeScript: exit 0
Vite build: 15 modules transformed, exit 0

./scripts/run_checks.sh unit-test
Python: 289 tests in 5.362s, OK
Frontend: 1 file and 1 test passed
exit 0

bash -n scripts/bootstrap.sh scripts/run_checks.sh
exit 0

./scripts/run_checks.sh fixture-check
fixture safety valid: 4 files

./scripts/run_checks.sh migration-check
migration manifest valid: 7 migrations

./scripts/run_checks.sh secret-scan
secret scan valid: no findings outside reviewed baseline
```

`make clean-room-check` was intentionally not run. This correction must first
receive independent review against the small frozen inventory.

## Accepted SKL-S1-05 boundary

The accepted audit inventory receipt remains byte-for-byte unchanged:

```text
21a90c8f112b9ccde9f4f4d75ad48ff47d93a29b52764b77d509401a8c5d1ae0  docs/evidence/audit/SKL-S1-05-CORRECTION-3-FROZEN-INVENTORY-2026-08-20.sha256
```

A current check of its 37 listed paths reports 36 matching paths. The sole
expected mismatch is `scripts/run_checks.sh`, changed only to register the new
qualification-harness unit module under this separate authorized card. All 36
audit source, migration, adapter, model, telemetry, contract, and evidence
paths remain exact. The historical accepted inventory receipt was not edited
or regenerated.

## Safety and limitations

- No full clean-room command, final qualification, deployment, commit, push,
  production action, live key access, protected corpus access, or external
  action occurred.
- Fixtures contain only synthetic strings and files.
- No `sklegal-cleanroom-*` directory remained under `/tmp` at freeze.
- Full clean-room runtime behavior remains deliberately unqualified until this
  harness inventory receives independent acceptance.
