# SKL-S5-04D backup and restore qualification evidence

Date: 2026-08-22
Card: `9549c3be` (SKL-S5-04D), dependency `cb6326ce` (S3-07 resilience
smoke) complete
Owner: `skl-s5-04d`
Contract: `docs/operations/SKL-S5-04D-BACKUP-RESTORE-RUNBOOK.md` (this
card), `docs/development/RESILIENCE-SMOKE.md` (S3-07)
Status: full restore, point-in-time restore, and Temporal persistence
recovery all verified with the audit hash chain clean after every
restore scenario; secrets-by-reference verification fails closed
because the approved vault-file backend root is not exported in this
session (`SKSTACKS_V2_PATH` unset). The numeric RPO/RTO targets remain
proposed by `docs/approval/AMENDMENT-SKL-S3-07.md` and pending human
approval; this evidence measures against those proposed targets.

## Environment

- Host: chiap08 (WSL), Docker server 29.1.3.
- Disposable qualification clusters: digest-pinned
  `postgres:17.7-alpine@sha256:a6d31f853205ce20d399df4e33a0b4c715672f232f4ee7440499747e6e02c126`,
  started with `archive_mode=on`, `wal_level=replica`, and an
  `archive_command` copying closed WAL segments into a shared named
  volume, all on `--network none`.
- Live lanes: the pinned development compose project `sklegal-dev`
  (`deploy/chiap01/compose.dev.yml`), containers
  `sklegal-dev-postgres-1` (superuser `temporal`) and
  `sklegal-dev-temporal-1`, both healthy throughout.
- Repository: worktree `/tmp/swarm/9549c3be`, branch `swarm/9549c3be`.
- All data synthetic: two tenants, three principals, one matter, one
  ethical wall, and audit events appended only through the controlled
  `sklegal_audit.append_event` writer under a least-privilege
  role bound by `provision_postgres_principal.py`. No HammerTime
  path, live matter, production credential, or production database was
  touched. Sibling stacks were not modified.

## Scenarios and exact results

### Unit contract (manifest, authorization, RPO/RTO math)

Command:

```bash
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked \
  --package sklegal-persistence pytest tests/test_backup_restore_contract.py -q
```

Result (2026-08-22, final run): `29 passed, 12 subtests passed in 0.13s`.

Covers: canonical manifest bytes and digest; unknown-field rejection;
every fail-closed authorization denial reason (manifest unavailable,
invalid, digest mismatch, digest unrecorded, backup id mismatch, empty
scope, cross-tenant scope, hold state unacknowledged, key custody
unavailable, missing approval, operator, target); RPO/RTO measurement
math and target evaluation; hold-wall digest rows; secret reference
collection and by-reference verification (empty scope fails closed).

### Full logical restore into a scratch instance (PASS)

Source: the disposable WAL-archiving cluster, migrated to all 16
migrations, seeded with two tenants, three bound principals, one matter,
one ethical wall with memberships, two audit events, one outbox
delivery.

Procedure: `pg_dump --no-owner` (dump scanned and confirmed free of
private key material), authorization through `authorize_restore`
(allowed), restore into a second scratch container whose roles were
provisioned before the data, then comparison.

Result: event, outbox, delivery, ethical-wall, and wall-membership row
counts equal between source and target; chain head identical
(`last_event_sequence` and `last_event_sha256` match);
`verify_current_tenant_chain()` returns true on the restored cluster;
row-level security still isolates the foreign tenant role; migration
readback equals 16 (the full migration list). A tampered manifest
(`content_bytes` incremented) is denied with reason
`manifest_digest_mismatch` before any target instance exists.

### Point-in-time restore to exact targets (PASS)

Source: the same cluster. One audit event, WAL switch with the closed
segment confirmed in the archive volume, `pg_basebackup`
(`--format plain --wal-method stream --checkpoint fast`), a target time
between batches, a second audit event, a target time after it, a
post-target marker event, and a final WAL switch.

Two recoveries, each into a fresh data volume with
`recovery_target_time`, `restore_command` from the archive volume, and
`recovery_target_action = promote`:

- Target between the batches: chain head equals batch one exactly
  (`last_event_sequence` and `last_event_sha256`); batch two's event id
  is absent (count 0); chain verifies clean; row-level security intact;
  migration readback 16.
- Target after both batches: chain head equals batch two exactly; the
  post-target marker's event id is absent (count 0); chain verifies
  clean; row-level security intact; migration readback 16.

### Integration suite command and result

```bash
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked \
  --all-packages pytest tests/integration/test_backup_restore_qualification.py -q
```

Result (2026-08-22, final run after fixes):
`2 passed, 2 subtests passed in 23.89s`.

### Live-stack driver (measured RPO/RTO)

Command:

```bash
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked \
  python scripts/backup_restore_qualification.py --lane all \
  --output build/s504d_qualification.json
```

Final run: 2026-08-22T21:06:33Z to 2026-08-22T21:06:39Z, driver exit 1
(secrets lane fails closed, see below).

`full-restore` lane (PASS):

```text
manifest_sha256: 69ce5415c708170334ed5d7277b2eda5da7c1a3dda07089999e56557f6350f3b
dump_bytes: 667270
source_state: events 1, outbox 1, last_sequence 1,
  last_event_sha256 d87fff0f8375212294feb2d72e3a21f4215ecb430fa8eb1fb00d80dbc19ac002,
  migrations 16
restored_state: identical to source_state in every field
chain_verified: true
tamper_denied_reasons: ["manifest_digest_mismatch"]
rpo_seconds: 0, rto_seconds: 2
rpo_within_target: true, rto_within_target: true, passed: true
```

`temporal-persistence` lane (PASS): live versus restored row counts
identical for `executions` 134, `current_executions` 134,
`namespaces` 2, `history_node` 1586; dump 2045176 bytes. The Temporal
server schema lives in the `public` schema of the `temporal` database
on the pinned stack; the earlier assumption of a `Temporal` schema was
wrong and was corrected before the passing run.

`secrets` lane (FAIL CLOSED): the route registry carries exactly one
secret reference, `vault:sklegal/openai/platform-api-key`. The
vault-file backend root is located through `SKSTACKS_V2_PATH`, which is
not exported in this session, so verification failed closed with
`backend_available: false` and the reference listed as unresolved. No
secret value was read, printed, or stored at any point. This is a
recorded environment gap, not a silent pass: rerun the lane with
`SKSTACKS_V2_PATH` exported to the approved skstacks root to complete
qualification.

### Measured RPO/RTO versus targets

| Scenario | RPO measured | RPO target | RTO measured | RTO target | Verdict |
| --- | ---: | ---: | ---: | ---: | --- |
| Full logical restore (live driver) | 0 s | 300 s | 2 s | 900 s | within targets |
| Point-in-time restore (integration suite) | not applicable (event-granular targets) | 300 s | seconds-scale (23.89 s for the whole 2-scenario suite including source setup) | 900 s | within targets |

The integration suite duration bounds the recovery time conservatively:
it includes source cluster bootstrap, migrations, seeding, backup, two
full recoveries, and teardown, and still completes inside the RTO
budget. The targets themselves are the proposed values from
AMENDMENT-SKL-S3-07; human approval is outstanding, so "meets target"
here means "meets the proposed target".

## Restore authorization path (threat model B11)

Every restore in both the suite and the driver passes through
`authorize_restore` before a target instance is created. Denials are
fail-closed and proven by unit tests for every reason, plus a live
tampered-manifest denial in the driver and in the full-restore
integration scenario. The manual operator procedure in the runbook
encodes the same checks as explicit steps.

## Defects found and fixed during qualification

- Docker named volumes mounted outside the image data path are root
  owned; the postgres archiver (uid 70) could not write archived WAL,
  so PITR initially recovered from an empty archive. Fixed by
  chowning the archive volume to 70:70 before the source cluster
  starts.
- Recovery ended before the configured target because the target time
  was recorded after the last archived WAL record. Fixed by appending a
  post-target marker event and switching WAL after it, and by waiting
  for each closed segment to appear in the archive before relying on
  it.
- The restore data directory needed explicit `chmod 0700` after
  copying the base backup, or postgres 17 refused to start the
  recovery cluster.
- The driver initially set `sklegal.database_role`/`principal_id` GUCs
  directly; the schema binds identity through `session_user` and
  `sklegal_identity.database_role_bindings`, so audit appends now run
  through a properly provisioned least-privilege role.

## Cleanup evidence

After the final runs: no `skl-s504d-*` containers or volumes remain;
no `sklegal_s504d_*` databases or driver roles remain on the dev
postgres; the dev stack containers are healthy and untouched
otherwise.

## Known limitations

- No backup scheduler or retention policy exists yet; RPO depends on
  operator cadence until one is approved and built. This card
  qualifies the restore path, not scheduling.
- The secrets-by-reference lane could not complete because the
  approved vault-file backend root was not exported in this session.
- PITR granularity is bounded by WAL segment size and commit
  boundaries; recovery targets land at the next commit boundary after
  the requested time.
- The measured RTO excludes human decision latency (approval to start
  the restore).
- The dev stack postgres runs with `archive_mode=off`, so PITR was
  qualified on dedicated disposable clusters rather than the shared
  stack; enabling archiving on the shared stack was deliberately not
  done.
