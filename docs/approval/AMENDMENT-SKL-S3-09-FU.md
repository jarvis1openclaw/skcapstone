# Amendment record: audit chain-head scaling decision

Amendment ID: `AMENDMENT-SKL-S3-09-FU`
Card: `2a41d17d` (follow-up to `SKL-S3-09`, card `1b0a7b29`)
Recorded by: `kimi`
Status: approved

## Human decision

2026-08-21: the human owner approved this amendment.
`docs/architecture/AUDIT-CHAIN-SCALING.md` is the governing chain-scaling
decision, recorded at hash:

```text
da578eeb877aec9b82771a71164b9f079f9c3a619deda2599d6158a2daafd217  docs/architecture/AUDIT-CHAIN-SCALING.md
```

## Baseline

The approved architecture requires an append-only, hash-chained audit trail
with a transactional outbox. Migration `0007_append_only_audit_outbox.sql`
implements this with one locked chain-head row per Tenant. The measurement
in `docs/evidence/audit/SKL-S3-09-CHAIN-HEAD-SERIALIZATION-2026-08-21.md`
quantified the resulting per-Tenant append ceiling at roughly 80 to 96
appends per second.

## Proposed amendment

Add `docs/architecture/AUDIT-CHAIN-SCALING.md` as the governing
chain-scaling decision:

- No schema change before or during the Sprint 5 pilot; the single-chain
  design stands.
- Per-Matter chain heads are the design direction if a mitigation is
  required; batch append is rejected as the primary mitigation because it
  conflicts with the atomic mutation-plus-audit-plus-outbox transaction
  guarantee.
- A numeric trigger gate (sustained 50 appends per second per Tenant, p99
  append latency above 100 ms at pilot concurrency, or any multi-tenant
  load claim beyond the pilot) makes the mitigation eligible work, with
  S5-04B load qualification measuring against it.

## Approval trail effect

- No hash-pinned approved document was modified.
- No migration, schema, code, or deployment change is authorized by this
  amendment. Implementation of per-Matter chains requires its own eligible
  cards, tests, dry run, and rollback plan after the trigger gate fires.
- Approval should record the exact hash of
  `docs/architecture/AUDIT-CHAIN-SCALING.md` and update this record's
  status.
