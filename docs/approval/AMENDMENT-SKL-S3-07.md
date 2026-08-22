# Amendment record: numeric RPO and RTO recovery targets

Amendment ID: `AMENDMENT-SKL-S3-07`
Card: `cb6326ce` (`SKL-S3-07`)
Recorded by: `kimi-skl-s3-07`
Status: proposed (pending human approval)

## Human decision

Pending. The human owner has not yet decided this amendment.

Approval should record the human decision in this section and update the
status above. The numeric statement under review is the recovery-target table
in `docs/development/RESILIENCE-SMOKE.md`, pinned at proposal time at hash:

```text
25aabb9e44bfa52d4c702331dcb750fe56248f3c27ae34b56d40a4c9a598fb80  docs/development/RESILIENCE-SMOKE.md
```

## Baseline

The approved S0-02 capacity and deployment baseline
(`docs/evidence/platform/CHIAP01-CAPACITY-QUALIFICATION-2026-08-19.md`)
qualifies host capacity but defines no numeric recovery targets. No
hash-pinned approved document states a recovery point objective (RPO) or
recovery time objective (RTO). The Sprint 5 qualification cards `9549c3be`
(S5-04D, backup and restore) and `2781e329` (S5-04C, outage and restart)
already reference the targets this amendment defines, so Sprint 5
qualification has no approved yardstick without this record.

The hash-pinned approved documents and
`docs/approval/DESIGN-HASHES.sha256` are unchanged by this proposal.

## Proposed amendment

Amend the S0-02 capacity baseline with the following numeric recovery
targets, identical to the development smoke contract in
`docs/development/RESILIENCE-SMOKE.md`:

| Boundary | Target | Measurement |
| --- | ---: | --- |
| Recovery point objective | 5 minutes or less | Latest durable PostgreSQL backup timestamp before the failure |
| Recovery time objective | 15 minutes or less | Failure observation to a healthy restored service accepting a probe |
| Worker interruption | No duplicate mutation or dispatch | Replayed workflow event log and receipt count |
| Temporal restart | No lost workflow history | Workflow query and completion after restart |
| PostgreSQL restore | Source marker and schema survive | Scratch restore probe and migration readback |

The RPO target applies to the canonical SKLegal PostgreSQL database. Temporal
history is durable workflow state, but it is not the legal audit record. The
audit ledger and its outbox remain the source of record after recovery.

A 5 minute RPO implies a durable backup cadence of 5 minutes or faster once a
backup scheduler exists; no scheduler is approved or built by this amendment.

## Approval trail effect

- No hash-pinned approved document was modified.
- The targets are proposed only until the human owner approves this
  amendment. The development smoke contract uses the same numbers for
  development qualification and does not by itself approve them for the S5-04
  qualification gate.
- Approval should update this record's human decision section and status.
- Backup tooling, scheduling, and production rollout require separate
  eligible cards and their own evidence.
