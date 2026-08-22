# SKL-UI-01 completion evidence

Date: 2026-08-21  
Card: `6775be53`  
Owner: `jarvis`  
Status: Complete

## Outcome

Rebalanced SKLegal around visible application progress and deployed the first
continuously available status UI on chiap01. The service is healthy at
`http://127.0.0.1:15174/status/` on chiap01 and is reachable from a trusted
workstation through the documented SSH tunnel.

The delivery overlay reviews Sprints 0 through 6 and preserves stable task
keys and dependency gates. Wave 1 is the deployed progress UI. Wave 2 is the
pilot importer, Client and Matter workspace, tasks and deadlines, and active
retrieval adapter. Sprint 6 is a separate official-standards corpus lane that
does not block the first useful workspace.

## Files changed by SKL-UI-01

- `docs/status/index.html`: refreshed evidence-backed status and seven-wave
  UI-first roadmap.
- `docs/planning/UI-FIRST-EXECUTION-PLAN.md`: sprint review, execution overlay,
  task contract, gates, and rollback.
- `docs/approval/AMENDMENT-SKL-UI-01.md`: owner decision and preserved
  architecture boundaries.
- `docs/approval/ARCHITECTURE-APPROVAL.md`: additive amendment-trail entry.
- `deploy/chiap01/compose.progress-ui.yml`: pinned, loopback-only static service.
- `deploy/chiap01/progress-ui.nginx.conf`: closed static routes and security headers.
- `deploy/chiap01/README.md`: validation, start, tunnel, health, stop, and rollback.
- `tests/test_status_page.py`: current status facts and card references.
- `tests/test_progress_ui_deployment.py`: deployment hardening and scope contracts.
- `docs/evidence/status/SKL-UI-01-COMPLETION-EVIDENCE-2026-08-21.md`: this record.

The concurrent Sprint 6 task modified
`docs/tasks/SUBAGENT-TASK-TTDS.md` during SKL-UI-01 verification. That edit is
not part of this card and was not overwritten.

## Verification

### Focused status and deployment tests

Command:

```text
.tools/bin/uv run --locked pytest -q tests/test_status_page.py tests/test_progress_ui_deployment.py
```

Result: `9 passed, 30 subtests passed`.

### Repository unit tests

Command:

```text
./scripts/run_checks.sh unit-test
```

Result: all 44 discovered Python unit modules passed. Collection reported 734
tests. Frontend Vitest reported 9 files and 85 tests passed.

### Repository integration tests

Command:

```text
./scripts/run_checks.sh integration-test
```

Result: 46 passed and 15 skipped across 7 discovered modules. The 15 skips are
the existing disposable-host containment proofs, each requiring
`SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1`.

### Format, lint, type, and build

Commands:

```text
./scripts/run_checks.sh format-check
./scripts/run_checks.sh lint
./scripts/run_checks.sh type-check
./node_modules/.bin/prettier --check docs/status/index.html
git diff --check
```

Results:

- Ruff format: 131 files already formatted.
- Frontend Prettier: passed.
- Ruff lint and frontend ESLint: passed.
- Mypy: no issues in 67 source files.
- TypeScript: passed.
- Vite: 174 modules transformed and production build completed.
- Status-page Prettier and Git whitespace checks: passed.

### Compose and chiap01 runtime

Commands:

```text
docker compose -f deploy/chiap01/compose.progress-ui.yml config --quiet
docker compose -f deploy/chiap01/compose.progress-ui.yml up -d --wait --wait-timeout 60 progress-ui
curl --fail --silent --show-error --head http://127.0.0.1:15174/status/
```

Results:

- Compose validation passed.
- The pinned nginx image was pulled for Linux x86_64.
- Container status is `healthy`.
- Runtime user is `101:101`.
- Root filesystem is read-only.
- All Linux capabilities are dropped.
- `no-new-privileges:true` is active.
- Host binding is exactly `127.0.0.1:15174` to container port `8080`.
- HTTP status is 200.
- Content Security Policy denies all default sources and frames.
- Referrer Policy, MIME-sniffing denial, and frame denial headers are present.
- The live page contains card `6775be53`, and the live execution-plan route
  contains the complete sprint review.

### Browser qualification

- Google Chrome headless render at 1440 by 1200 passed visual review.
- Google Chrome headless render at 390 by 844 passed visual review.
- No remote asset, script, Client content, or Matter content is loaded.

### Approved design integrity

`sha256sum --check docs/approval/DESIGN-HASHES.sha256` passed before the
concurrent Sprint 6 TDD edit. A final rerun now reports only
`docs/tasks/SUBAGENT-TASK-TTDS.md` as changed. The other four approved files
remain verified. SKL-UI-01 did not modify the pinned TDD and did not rewrite
the concurrent task's work. The condition is recorded here for the Sprint 6
task to reconcile in its own approved hash trail.

## Acceptance criteria evidence

| Criterion | Evidence |
| --- | --- |
| Review every sprint and record a dependency-safe UI-first order | `UI-FIRST-EXECUTION-PLAN.md` reviews Sprints 0 through 6 and defines Waves 0 through 6 without renaming a task or removing a dependency. |
| Publish a responsive progress UI | `docs/status/index.html` reports shipped, active, next, blocked, deferred, gate, and evidence state; desktop and mobile browser renders passed. |
| Provide a reproducible safe deployment | The chiap01 container is healthy, loopback-only, unprivileged, read-only, capability-free, security-header protected, and image-pinned. |
| Reprioritize the live board | Sprint 2, Sprint 4, S2-04, S4-02, and S4-05 were amended to critical. Wave labels now identify UI, first workspace, assurance, official corpus, actions, and pilot order. |
| Preserve protected boundaries | No production application activation, public bind, Client or Matter content, external account, HammerTime `Inbox/` operation, additional Matter migration, or outbound legal action occurred. |

## Known limitations

- The service is intentionally loopback-only. Remote viewing requires the
  documented SSH tunnel.
- The page is a timestamped snapshot, not a live SKCapstone API client.
- This surface shows delivery progress, not authenticated application data.
- The final approved-design hash check is temporarily red because of the
  concurrent Sprint 6 TDD edit described above.

## Migration and rollback

No database, corpus, Client, Matter, HammerTime source, or external account was
changed. The deployment has no persistent volume.

Rollback command on chiap01:

```text
docker compose -f deploy/chiap01/compose.progress-ui.yml down
```

Rollback removes only the stateless container and its Compose network. The
status source and evidence remain available in the repository.

## SKCapstone update

Card `6775be53` links the plan, status page, deployment definition, amendment,
and this evidence. Its priority is critical, it carries `wave-1-visible`, and
it was completed after the UI-specific checks and live runtime verification
passed. The concurrent approved-hash condition remains assigned to Sprint 6.
