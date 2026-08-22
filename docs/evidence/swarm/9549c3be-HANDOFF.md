# SKL-S5-04D handoff: backup and restore qualification

Card: `9549c3be` (SKL-S5-04D), dependency `cb6326ce` complete.
Branch: `swarm/9549c3be` in this worktree. No push performed; jarvis
owns board state and completion.

## Files changed

- `packages/persistence/src/sklegal_persistence/backup.py` (new):
  pure backup/restore contract. `BackupManifest` with canonical JSON
  bytes and digest; `parse_manifest` rejecting unknown fields;
  `RestoreRequest`; `authorize_restore` fail-closed decision with the
  full denial-reason vocabulary; `RecoveryMeasurement` with RPO/RTO
  seconds; `RecoveryTargets` and `evaluate_recovery` against the
  300 s / 900 s targets; `hold_wall_digest_rows`;
  `collect_secret_references`; `verify_secret_recovery` (by reference
  only, empty scope fails closed).
- `packages/persistence/src/sklegal_persistence/__init__.py`: exports
  the contract surface.
- `tests/test_backup_restore_contract.py` (new): unit tests, no Docker.
- `tests/integration/test_backup_restore_qualification.py` (new):
  full logical restore and point-in-time restore against dedicated
  networkless WAL-archiving postgres containers, with chain, outbox,
  delivery, ethical-wall, migration-readback, RLS, and tamper-denial
  verification after every restore.
- `scripts/backup_restore_qualification.py` (new, executable):
  operator driver with `full-restore`, `temporal-persistence`, and
  `secrets` lanes; writes a JSON measurement report.
- `docs/operations/SKL-S5-04D-BACKUP-RESTORE-RUNBOOK.md` (new): the
  tested procedure, automated matrix, manual operator path encoding
  the authorization checks, secrets-by-reference policy, denial
  reference, and known limits.
- `docs/evidence/platform/SKL-S5-04D-BACKUP-RESTORE-2026-08-22.md`
  (new): execution evidence with exact numbers.

## Tests and exact results

Unit contract:

```bash
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked \
  --package sklegal-persistence pytest tests/test_backup_restore_contract.py -q
# 29 passed, 12 subtests passed in 0.13s
```

Integration qualification:

```bash
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked \
  --all-packages pytest tests/integration/test_backup_restore_qualification.py -q
# 2 passed, 2 subtests passed in 23.89s
```

Live driver:

```bash
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked \
  python scripts/backup_restore_qualification.py --lane all \
  --output build/s504d_qualification.json
# full-restore PASS, temporal-persistence PASS, secrets FAIL CLOSED,
# exit 1 (secrets lane only)
```

Ruff format and check are clean on all new Python files. No em or en
dashes in any new file.

## Acceptance criteria evidence

- Measured RPO/RTO meet the approved targets: measured 0 s RPO and 2 s
  RTO for the full logical restore, and the entire two-scenario PITR
  suite including source bootstrap completes in 23.89 s, all inside
  the 300 s / 900 s targets. Caveat stated in the evidence file: the
  numeric targets are the values proposed by
  `docs/approval/AMENDMENT-SKL-S3-07.md` and human approval is still
  outstanding, so "meets target" means "meets the proposed target".
- Audit chain verifies clean after every restore scenario: verified by
  `verify_current_tenant_chain()` (suite) and a full recompute of
  sequence contiguity, predecessor linkage, and payload digests
  (driver) after the full restore and after both PITR targets, with
  chain heads matching the requested targets exactly.
- Runbook updated with the tested procedure:
  `docs/operations/SKL-S5-04D-BACKUP-RESTORE-RUNBOOK.md`, reflecting
  exactly the commands that were run and the operational lessons
  found during qualification.

## Known limitations

- The secrets lane fails closed because `SKSTACKS_V2_PATH` is not
  exported in this session; the approved vault-file backend root must
  be provided to complete that lane. No secret value was read at any
  point.
- No backup scheduler or retention policy exists; RPO depends on
  operator cadence. This card qualifies restore, not scheduling.
- PITR granularity is commit-bounded by WAL archiving.
- Measured RTO excludes human approval latency.
- Dev stack postgres has `archive_mode=off`, so PITR ran on dedicated
  disposable clusters, not the shared stack.

## Migration or rollback evidence

No production or shared data changed. All scratch objects (databases
`sklegal_s504d_*`, roles, containers, volumes) were removed after the
runs; the dev stack is healthy and unmodified otherwise. The evidence
file records the cleanup state.
