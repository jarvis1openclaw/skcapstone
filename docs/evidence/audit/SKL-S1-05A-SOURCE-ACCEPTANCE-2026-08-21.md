# SKL-S1-05A append-only audit source acceptance

Card: `271c1b58`  
Parent implementation card: `61c43e1e` (S1-05, done)  
Reviewer: `kimi-skl-s1-05a` (independent of implementer `codex-audit`)  
Date: 2026-08-21  
Disposition: **Accepted** for product-source contract scope

## Scope

This record accepts the product-source contract for append-only audit,
transactional outbox, correlation, canonicalization, RLS and definer
boundaries, rollback, watermark compare-and-set, and local telemetry. It is
the acceptance slice split from S1-05. Production backends, production
composition, host qualification, and clean-room qualification are explicitly
out of scope for this card and are not accepted here. They remain tracked in
cards `085b327f` (S1-05B production composition), `0e436d61` (host
qualification boundary), and `ed6b37e3` (S1-08 real-Postgres durable
coverage).

## Reviewed baseline

The review target is the third correction frozen inventory
`docs/evidence/audit/SKL-S1-05-CORRECTION-3-FROZEN-INVENTORY-2026-08-20.sha256`
(37 paths), whose own SHA256 is
`21a90c8f112b9ccde9f4f4d75ad48ff47d93a29b52764b77d509401a8c5d1ae0` (recorded
from the working tree at review time). The freeze predates git history; the
bootstrap commit `f218342` carries byte-identical content for every
spot-checked contract file, so all post-freeze change is observable through
git.

Because parallel sprint cards hold uncommitted edits in the shared worktree,
hash verification is pinned to the merged wave-1 commit `d14d166` (tag
`v0.2.0`), the state this card was assigned against. The exact reviewed
hashes of the same 37 paths at that commit are recorded in
`docs/evidence/audit/SKL-S1-05A-REVIEWED-INVENTORY-2026-08-21.sha256`.
Reproduce with:

```text
git show d14d166:<path> | sha256sum
```

for each listed path, or check out `d14d166` and run
`sha256sum -c SKL-S1-05A-REVIEWED-INVENTORY-2026-08-21.sha256`.

## Inventory verification result

24 of 37 frozen paths are byte-identical at `d14d166`. Every
contract-bearing path is unchanged:

- `migrations/0007_append_only_audit_outbox.sql`
  (`7a501b78b3211698310044851b04ea96b8f82242120e40f704b05a1aa2229eaa`)
- `packages/audit/pyproject.toml` and all five
  `packages/audit/src/sklegal_audit/` modules
- `tests/test_audit.py`
- `migrations/0004`, `0005`, `0006` (audit schema foundations)
- `scripts/provision_postgres_principal.py` (runtime grant allowlist and
  readback)
- `packages/capauth/src/sklegal_capauth/authorization.py` and `models.py`
- `packages/policies/src/sklegal_policies/` models and init
- all three historical rejected inventories and the S1-05 review receipt
- `docs/security/THREAT-MODEL.md`,
  `docs/security/DATA-CLASSIFICATION-AND-SOURCE-RIGHTS.md`,
  `docs/tasks/SUBAGENT-TASK-TTDS.md`

13 paths changed between the freeze and `d14d166`. Each was reviewed in
current form; none alters the audit contract:

1. `migrations/manifest.json`: grew from 7 to 12 migrations. The 0007 entry
   still records the exact frozen hash
   `7a501b78b3211698310044851b04ea96b8f82242120e40f704b05a1aa2229eaa`.
   Migrations 0008 through 0010 reuse the frozen
   `sklegal_audit.payload_sha256` for CapAuth revision digests, confirming
   the canonical hash function as a shared stable contract.
2. `packages/capauth/src/sklegal_capauth/__init__.py`: re-export surface
   extended with the S1-03A backend protocols and adapters. The reviewed
   authorization core files are byte-identical to the freeze.
3. `tests/integration/test_persistence_contract.py`: grew to 34 test
   methods. The migration 0007 durable-function coverage (controlled append,
   canonical digest parity, timezone independence, rollback sharing,
   idempotent delivery, watermark serialization and monotonic CAS,
   integrity-mode laundering denial, hidden-matter chain verification,
   data-bearing down refusal) is intact and passes live.
4. `tests/test_foundation.py`: added S1-14 migration-lint tests (recreated
   SECURITY DEFINER functions must re-apply privilege hardening). This
   strengthens the migration hygiene gate around the audit functions.
5. `scripts/run_checks.sh`: unit stage gained three test modules.
6. `pyproject.toml` and `uv.lock`: added the `sklegal-hammertime` workspace
   member. No dependency of `sklegal-audit` changed.
7. `docs/development/AUDIT.md`: added the SKL-S4-07 connector dispatch
   ownership section (decision record `ea2c9790`), which routes connector
   transitions through the migration 0007 append path without modifying it.
   The durable event, chain, outbox, watermark, telemetry, and rollback
   sections match the reviewed code.
8. `docs/development/CAPAUTH.md`: documents the S1-03A durable PostgreSQL
   backends and the two-half rollback. No audit contract change.
9. `docs/development/PERSISTENCE.md`: audit boundary section updated for the
   0007 writer and grant model; consistent with the migration and the
   provisioner.
10. `.secrets.baseline`: reviewed baseline entries only; the secret scan
    stage passes with no findings outside the baseline.
11. `AGENTS.md`, `README.md`: governance and repository documentation.

The working tree also carries uncommitted edits from other active cards
(including `docs/security/THREAT-MODEL.md`, `docs/tasks/SUBAGENT-TASK-TTDS.md`,
`docs/development/PERSISTENCE.md`, and `scripts/run_checks.sh`). Those are
not part of this acceptance and were not reviewed; the pinned commit removes
ambiguity.

## Contract verification findings

### Python and PostgreSQL canonical bytes

Python (`packages/audit/src/sklegal_audit/ledger.py`) and PostgreSQL
(migration 0007 `canonical_json_text`, `canonical_timestamp`,
`canonical_event_payload`, `payload_sha256`) implement the same byte
contract: recursive lexicographic object-key ordering (SQL uses `COLLATE
"C"`), preserved array order, compact separators, stable JSON scalars, UTF-8
encoding, null optional attributes stripped, and UTC timestamps rendered
with an explicitly numeric four-digit year, six fractional digits, and
terminal `Z` across the AD 1 through 9999 domain. Python never delegates
low-year rendering to platform `strftime`; SQL rejects non-finite, BC, and
out-of-domain years before any chain, event, outbox, or sentinel write.
`recompute_event_sha256` recomputes database-returned digests through the
same rules. Live parity is proven by
`test_12_postgres_events_match_python_canonical_digest_contract` and
`test_12_canonical_audit_payload_is_timezone_independent`.

### Chain verification

Python `verify_event_chain` revalidates tenant identity, sequence
contiguity, predecessor links, and recomputes every digest without trusting
stored values. SQL `verify_current_tenant_chain` clears caller-supplied
integrity mode, authorizes the current runtime scope, enables function-local
full-tenant visibility only after authorization, restores the prior mode on
success and exception paths, and returns only a boolean over the complete
tenant chain including hidden-matter rows. Proven by
`test_12_tenant_chain_verification_sees_hidden_matter_rows` and
`test_12_integrity_mode_cannot_be_laundered_through_definers`.

### RLS and definer boundaries

Migration 0007 applies FORCE ROW LEVEL SECURITY to the chain heads, outbox,
deliveries, and watermark tables; every write path requires the controlled
writer (`current_user = 'sklegal_migrator'` with a distinct `session_user`),
and event inserts additionally require record authorization and the exact
current principal. All four grantable audit definers are SECURITY DEFINER
with `search_path = pg_catalog`, clear caller-supplied integrity mode before
any RLS read, and REVOKE ALL FROM PUBLIC is explicit. Runtime roles receive
EXECUTE only through `scripts/provision_postgres_principal.py`, which
enforces an exact function allowlist (including the four audit definers) and
fails closed when the grant readback differs. Proven by
`test_03_least_privilege_grants_and_role_bypass_resistance`.

### Rollback

Each append transaction writes a content-free singleton sentinel to
`sklegal_audit.rollback_guard`. The down section refuses destructive DDL
while the sentinel exists, before any drop. The sentinel is owner-readable,
has no runtime grant, and is protected by the controlled-writer trigger.
Transaction rollback removes event, chain-head update, outbox row, and
sentinel together. Proven by
`test_12_audit_and_outbox_share_rollback_and_delivery_receipt` and
`test_14_data_bearing_audit_migration_refuses_down`.

### Watermark compare-and-set

`advance_projection_watermark` serializes initialization and advancement on
the tenant chain head, requires the exact current sequence and hash, binds
the target to an existing event sequence and hash, denies stale, backward,
and unknown targets, and returns false for exact replay. Proven by
`test_12_projection_watermark_initialization_is_serialized` and
`test_12_projection_watermark_is_exact_monotonic_and_idempotent`.

### Telemetry isolation

`TraceContextPropagator` carries only a strict W3C version-00 `traceparent`;
baggage and arbitrary carrier attributes are excluded. `LocalTelemetryBuffer`
accepts only strict `TelemetrySpan` values over the closed attribute schema,
uses trusted ingestion time, enforces positive retention of at most seven
days and a 100,000-span bound, revalidates at record and export, returns
fresh deep revalidated snapshots, and fails closed with a sanitized
`AuditUnavailable` when internal state is poisoned. No collector or exporter
is configured. Proven by the `tests/test_audit.py` suite.

### Protected-field suppression

The closed `AuditAttributes` schema has no prompt, document, message, query,
tool argument, model output, credential, bearer, exception, or arbitrary
attribute field, and PostgreSQL mirrors the key, type, pattern, and numeric
range rules before accepting an event. `DurableAuditSink` maps CapAuth and
policy decisions to exact safe references without credential material,
digests, policy content, waiver content, or backend payloads; all durable
failures raise sanitized `AuditUnavailable` with no retained cause.

## Test evidence

Full gate `./scripts/run_checks.sh all`, final run exit 0:

```text
PASS: design hashes (docs/approval/DESIGN-HASHES.sha256)
PASS: compose check
PASS: lock check (uv.lock exact; npm ls)
PASS: license audit (doc-haus provenance and CycloneDX schema)
PASS: format check (Ruff format; Prettier)
PASS: lint (Ruff; ESLint)
PASS: type check (mypy; tsc; production build)
PASS: unit suite, all discovered modules, including:
      tests/test_audit.py: 11 passed, 19 subtests passed
      tests/test_foundation.py: 13 passed, 14 subtests passed
      frontend: 9 test files, 85 tests passed
PASS: integration suite, tests/integration/test_persistence_contract.py:
      34 passed, 69 subtests passed in 90.53 seconds, disposable
      digest-pinned PostgreSQL 17.7
PASS: migration manifest valid: 12 migrations
PASS: fixture safety valid: 4 files
PASS: SBOM export (python and node)
PASS: secret scan: no findings outside reviewed baseline
PASS: vulnerability scan: no known vulnerabilities (python registry,
      capauth 0.3.1 release, npm audit)
```

Focused rerun during review:
`.tools/bin/uv run --locked python -m pytest tests/test_audit.py -q`
gave `11 passed, 19 subtests passed`.

Gate run history: the first full run passed with exit 0 before the evidence
files existed. One intermediate run failed with
`ERROR: file or directory not found: tests/test_s1_07_probe.py`: a transient
probe file from the parallel S1-07 test-runner card was enumerated by the
modified discovery-based `scripts/run_checks.sh` and deleted by its owner
before pytest collected it. That failure is unrelated to this card and did
not reproduce; the final run with this card's two evidence files present
passed with exit 0. The gate executed against the shared worktree, which
also contained other cards' uncommitted in-flight edits; all stages passed
with those present. The hash acceptance above is pinned to commit `d14d166`.

## Acceptance decision

Acceptance criterion 1 (freeze and independently review the 37-path
inventory and migration contract): met. The inventory was recomputed
path-by-path; the 24 contract-bearing paths are byte-stable and the 13
changed paths are individually reviewed above with exact hashes in the
reviewed inventory file.

Acceptance criterion 2 (verify canonical bytes, chain verification, RLS,
rollback, watermark CAS, telemetry isolation): met, per the findings above
and the green gate.

Acceptance criterion 3 (production backends and clean-room qualification out
of scope): met. This record accepts source contracts only and explicitly
excludes production composition, host qualification, and clean-room
qualification.

## Known limitations

- The SHA256 tenant chain is tamper evident, not a cryptographic signature
  or external timestamp; superuser or host compromise remains a deployment
  threat for S1-05B and qualification cards.
- An outbox receipt proves database acknowledgement, not that a
  non-idempotent destination effect could never occur before a worker crash.
- The in-memory ledger and telemetry buffer are process-local development
  adapters and never a production default.
- Real-Postgres coverage of the durable CapAuth functions is tracked in
  card `ed6b37e3` (S1-08) and is not a blocker for this source acceptance.
- Migration down after any recorded append is refused by design; a
  data-bearing environment requires a separately approved preservation and
  restore plan.

## Security and data assurance

- Review used synthetic fixtures only; no HammerTime Inbox or corpus path
  was inspected, read, or changed.
- No production database, service, account, keyring, signing key, prompt,
  document, model, connector, collector, exporter, or external action was
  accessed.
- No git mutation of any kind was performed; the record and inventory are
  the only files added.
