# SKL-S5-04D backup and restore qualification runbook

Status: tested procedure. The automated evidence for every step below is
recorded in `docs/evidence/platform/SKL-S5-04D-BACKUP-RESTORE-2026-08-22.md`.

Scope: full and point-in-time restore of the canonical SKLegal PostgreSQL
database into a scratch instance, audit hash chain and outbox verification
after restore, Temporal persistence recovery, secrets and key-material
recovery by reference, and the restore authorization path from the threat
model (boundary B11).

Recovery targets under evaluation, from `docs/development/RESILIENCE-SMOKE.md`
and proposed for the capacity baseline by
`docs/approval/AMENDMENT-SKL-S3-07.md` (human approval outstanding):

| Boundary | Target | Measurement |
| --- | ---: | --- |
| Recovery point objective | 5 minutes or less | Latest durable backup timestamp before the failure |
| Recovery time objective | 15 minutes or less | Failure observation to a healthy restored service accepting a probe |

The RPO applies to the canonical SKLegal PostgreSQL database. Temporal
history is durable workflow state, not the legal audit record; the audit
ledger and its outbox remain the source of record after recovery.

Every step below runs on synthetic data only. No HammerTime path, live
matter, production credential, or production database is in scope.

## Preconditions

- The repository worktree and `.tools/bin/uv` are bootstrapped
  (`scripts/bootstrap.sh`).
- A Docker daemon is available. Sibling stacks (`sklegal-dev`, other test
  cards) must not be touched; every step below creates only its own
  scratch objects.
- For the live-stack steps, the pinned development stack is up:
  `./scripts/dev_dependencies.sh up`.

## Automated qualification (no operator decisions)

The full matrix runs without operator input. Run it first; the manual
procedure below is the same path with explicit checkpoints.

```bash
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --all-packages \
  pytest tests/integration/test_backup_restore_qualification.py -q
```

This covers, against a dedicated networkless disposable cluster started
with `archive_mode=on`:

- full logical restore into a separate scratch instance with chain,
  outbox, migration, ethical-wall, and row-level-security verification;
- point-in-time restore to a target between two audit batches and to a
  target after both, proving the chain ends exactly at the requested
  target and verifies clean;
- restore authorization gating each restore, including a tampered-manifest
  denial.

The unit contract (manifest, authorization, RPO/RTO math, hold digest,
secret verification) is covered by:

```bash
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked \
  --package sklegal-persistence pytest tests/test_backup_restore_contract.py -q
```

## Live-stack qualification with measured RPO/RTO

```bash
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked \
  python scripts/backup_restore_qualification.py --lane all \
  --output build/s504d_qualification.json
```

Lanes:

- `full-restore`: migrates a scratch database, appends synthetic audit
  events through the controlled `sklegal_audit.append_event` writer,
  takes a logical dump, simulates failure, restores into a second scratch
  database, then verifies chain, outbox, and migration readback. Records
  measured RPO (backup age at failure) and RTO (failure observation to
  verified restore).
- `temporal-persistence`: dumps the live Temporal persistence database
  (`temporal` on the pinned stack; its schema is `public`, not
  `Temporal`) and restores it into a scratch database, then compares
  durable workflow row counts (`executions`, `current_executions`,
  `namespaces`, `history_node`).
- `secrets`: collects every `secret_reference` from
  `config/model_gateway/route-registry.json` and verifies each resolves
  in the configured vault-file backend by reference only. Secret values
  are never read, printed, or stored. The backend root is located
  through the `SKSTACKS_V2_PATH` environment variable; when it is not
  exported the lane fails closed (verification failed, backend
  unavailable) and the driver exits 1. That result is recorded as-is;
  do not work around it by pointing the lane at ad-hoc files.

The report JSON is the measurement record; copy the relevant numbers into
the evidence file. Exit code 0 means every lane passed.

Note: point-in-time restore on the pinned compose stack is not possible
because that postgres runs with `archive_mode=off`; the PITR lane is
qualified by the integration suite above against a dedicated
WAL-archiving container. Do not enable archiving on the shared stack to
work around this.

Operational lessons baked into the integration suite, verified during
qualification:

- Docker named volumes mounted outside the image data path are root
  owned; the postgres archiver runs as uid 70, so the archive volume
  must be `chown 70:70` before the source cluster starts, or
  `archive_command` silently fails and archived WAL is empty.
- After each `pg_switch_wal`, wait for the closed segment to appear in
  the archive before relying on it; archiving is asynchronous.
- `recovery_target_time` must be covered by available WAL records on
  both sides; record targets between committed transactions and append a
  post-target marker before switching WAL, or recovery ends before the
  target and the restore fails closed.
- The pinned dev stack's postgres superuser is `temporal`
  (`POSTGRES_USER`), not `postgres`; cluster administration in the
  driver passes `--admin-user temporal` accordingly.

## Manual procedure (operator restore under authorization)

This is the procedure an operator follows for a real restore. It encodes
the authorization path; the automated lanes exercise the same checks.

1. Obtain an approved restore authorization recording: the backup id, the
   scratch target identity, the requesting operator, the approval
   reference, the requested tenant scope, hold-state acknowledgment, and
   the decryption key reference when the backup is encrypted.
2. Verify the backup manifest against the digest recorded in the backup
   catalog at backup time. A mismatch, an unreadable manifest, or an
   unknown field denies the restore before any target is touched.
3. Check the requested tenant scope against the manifest tenant scope.
   Any tenant not present in the backup is a cross-tenant restore and is
   denied (`restore_scope_crosses_backup_scope`).
4. Confirm the explicit hold-state acknowledgment. Restores without it
   are denied; legal holds and ethical walls must never be treated as
   absent merely because the backup predates them.
5. For encrypted backups, confirm the decryption key reference resolves
   and matches the manifest key custody reference. A missing or
   mismatched reference denies the restore.
6. Restore into the authorized scratch instance only. Never restore over
   the source database. For logical restores use `pg_dump`/`psql` with
   `--no-owner`; for PITR use a base backup plus archived WAL with
   `recovery_target_time` and `recovery_target_action = promote` in a
   fresh data directory.
7. After restore, before any service points at the restored data, verify:
   - the audit hash chain recomputes clean end to end (sequence
     contiguity, predecessor linkage, payload digests);
   - the outbox row and delivery counts match the source at backup time;
   - the migration readback matches the expected migration list;
   - ethical-wall and hold rows match the manifest hold digest;
   - row-level security still isolates a foreign tenant role;
   - for PITR, the chain head equals the requested recovery target.
8. Record the measured backup age (RPO) and failure-to-verified-restore
   elapsed time (RTO) in the evidence record.
9. Decommission the scratch instance only after the evidence record is
   complete.

## Secrets and key-material recovery

Secrets are recovered by reference, never by value:

- the model gateway route registry carries `secret_reference` values
  (for example `vault:sklegal/openai/platform-api-key`), not key
  material;
- the recovery procedure verifies each reference resolves in the restored
  secret backend;
- an empty reference set is a failed verification, not a pass;
- encrypted backups carry a `key_custody_reference` in their manifest and
  restores are denied unless the operator presents the same reference;
- no step in this runbook reads, prints, logs, or commits a secret value.

## Restore authorization reference

`packages/persistence/src/sklegal_persistence/backup.py` defines the
fail-closed decision. Denial reasons and their meaning:

- `manifest_unavailable` / `manifest_invalid` / `manifest_digest_mismatch`
  / `manifest_digest_unrecorded`: the manifest is missing, malformed, or
  does not match the catalog digest.
- `backup_id_mismatch`: the request names a different backup.
- `restore_scope_empty` / `restore_scope_crosses_backup_scope`: no scope
  requested, or a requested tenant was not in the backup.
- `hold_state_not_acknowledged`: the operator did not explicitly
  acknowledge hold and wall state.
- `key_custody_unavailable`: encrypted backup without a matching key
  reference.
- `restore_approval_missing` / `restore_operator_unattributed` /
  `restore_target_unidentified`: the authorization record is incomplete.

## Known limits of this procedure

- A 5 minute RPO implies a durable backup cadence of 5 minutes or faster
  once a scheduler exists; no backup scheduler or retention policy is
  approved or built. This runbook qualifies the restore path, not
  scheduling.
- PITR recovery granularity is bounded by WAL segment size and the
  archive command; targets land at the next commit boundary after the
  requested time.
- The RTO measured here excludes human decision latency (approval to
  start the restore); the automated lanes measure failure observation to
  a verified restored state.
