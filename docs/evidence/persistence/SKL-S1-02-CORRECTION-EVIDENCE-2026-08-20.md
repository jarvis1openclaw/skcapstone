# SKL-S1-02 correction evidence

Card: `84c6f30c`  
Implementer: `codex-persistence`  
Date: 2026-08-20  
Status: Independently accepted with zero findings; final qualification complete

## Scope delivered

- Added five ordered, reversible, digest-pinned PostgreSQL migrations for
  identity, legal records, integrations, workflow references, audit, and RLS.
- Added exact migration-owner preflight, transactional migration state, drift
  validation, explicit rollback inventory, and full object-owner readback.
- Added a least-privilege runtime principal provisioner with revoke-first exact
  grants and readback.
- Added scoped composite key ownership and FORCE RLS for every application
  table, including original-session authorization through controlled writers.
- Added all 31 approved `SKL-S1-01` entities with an explicit parity matrix.
- Added a reusable driver-neutral persistence mapper with bidirectional dynamic
  coverage of all 31 public entity types, strict decomposition and
  reconstruction, normalized value-object and relation joins, closed DB-only
  metadata retention, bidirectional owner, scope, discriminator, and scalar-FK
  binding, and fail-closed unknown or missing data behavior.
- Added a closed production write contract that assigns every decomposed leaf to
  canonical input or declared database output, names the writer authority and
  operation for every entity, and exposes the Authority identity auxiliary row.
- Added optimistic versions, monotonic update time, domain state constraints,
  immutable provenance and relation evidence, and database-controlled authority
  assertion history.
- Added a total fail-closed encrypted-field hook and complete nullability matrix.
- Added strict PRB/problem-to-Matter and INC/incident-to-Matter Event legacy
  provenance without canonical ITIL tables.
- Added controlled Work Product, Work Product Version, Approval, Communication,
  and Execution transitions with exact artifact gates and immutable event and
  receipt evidence.
- Added append-only, FORCE-RLS approved-decision snapshots captured atomically
  on approval. Execution binds the exact `approval_version` and reconstructs
  historical approval evidence from `approval_history`, never mutable current
  Approval state.
- Made Approval revocation prospective. Exact-bound live Work Product,
  Execution, and Communication consumers block revocation; reset or terminal
  consumers permit it. New progression requires current approval while an
  already dispatched Execution can still reach receipt verification or failure
  from its captured snapshot.
- Preserved connector `received_at` and `verified_at` values as exact canonical
  inputs through phase-aware dispatch, receipt decomposition, controlled write,
  readback, and strict reconstruction.
- Added controlled Communication creation and atomic participant replacement;
  runtime roles cannot directly insert either the aggregate or participant rows.
- Added one parent Work Product to exact version lock order across Work Product,
  Execution, Communication, and supersession. Live exact-bound consumers block
  supersession while terminal completion remains reachable.
- Added exact Claim versus Defense Element cardinality from the triggering table,
  including same-scoped UUID cross-satisfaction denial.
- Added typed legal validation subjects, nonempty check evidence, exact prior
  version and actor binding, and failed, unrelated, stale, and cross-kind denial.
- Added append-only alias owner audit and version advancement, full role-graph
  drift checks, nil-UUID enforcement, and dynamic scalar boundary probes.
- Added live active Tenant and Principal authorization, trigger-synchronized
  self-visible authorization state, suspended recovery, and terminal closed or
  revoked identity guards.
- Added optimistic Work Product and Communication edit and reset writers, exact
  frozen-version gate locking, stale gate clearing, and both supersede-versus-
  gate concurrency order probes.
- Added the accepted Matter lifecycle including a proposed Matter closed before
  opening and terminal archive behavior.
- Left audit writes intentionally fail-closed for the attributable chain and
  outbox writer in `SKL-S1-05`.
- Added synthetic unit and isolated PostgreSQL 17.7 integration coverage plus a
  development and operations contract.

## Acceptance matrix

| Requirement | Evidence |
|---|---|
| Ordered reversible migrations | `migrations/0001_*` through `0005_*`, manifest, and up/down/up integration proof |
| Identity, legal, integration, workflow, audit schemas | migration files and schema-isolation assertion |
| Domain persistence parity | dynamic public export enumeration, `domain-table-parity.json`, reusable mapper contract, reverse read and fresh write-first strict reconstruction, canonical comparison, declared writer authority and per-leaf disposition, bidirectional relation-owner binding, and retained metadata for all 31 entities |
| Cross-tenant denial | unfiltered SELECT plus INSERT and UPDATE denial probes |
| Unassigned-matter denial | same-tenant SELECT plus INSERT and UPDATE denial probes |
| API-filter omission safety | all runtime read probes use unfiltered tables or scoped-ID reuse |
| Ownership and BYPASSRLS resistance | exact migrator profile rejection before bootstrap for every attribute and both membership directions, full runtime role-attribute and membership-graph drift probes, object-owner provisioning denial and readback, unsafe binding, and exact grants |
| Optimistic concurrency | stale version rejection and concurrent version-conflict probes |
| Monotonic timestamps | long-transaction update synchronized through `pg_stat_activity` |
| Scoped uniqueness | same UUID in three authorized scopes and same-scope duplicate denial |
| Transaction rollback | ordinary duplicate DML rollback |
| Migration rollback | intentionally failing synthetic 0006 with absent DDL and five-row manifest state |
| Bitemporal authority | append-only versions, database assertion time, current and history view tests |
| Encrypted-field hooks | all 16 nullability combinations plus malformed-value denial |
| Typed validation, artifact and execution evidence | exact legal subject kind, identity and version, nonempty checks, actor binding, adjacent transitions, exact frozen artifact locks, immutable approval-version snapshots, event-prefix replay, exact connector receipt timestamp binding, and direct-write denial |
| Live identity state | proposed and suspended Tenant plus suspended Principal deny all authorization; suspended recovery succeeds; closed Tenant and revoked Principal are terminal |
| Controlled legal evolution | draft edit and gated reset writers for Work Product and Communication, atomic relation replacement and gate clearing, owner version advancement, direct DML denial, and post-queue mutation denial |
| Artifact and approval lifecycle concurrency | parent-to-version-to-approval-to-consumer lock order, exact frozen recheck, live Execution and Communication supersession blocks, prospective revocation, deterministic gate-first, supersede-first, approval-first, and revocation-first races, and receipt or failure completion from dispatch |
| Durable approval evidence | append-only FORCE-RLS history, exact scoped approval ID, approval version, and artifact binding, no runtime DML, terminal Execution reconstruction after current Approval revocation |
| Receipt write fidelity | post-dispatch canonical construction, production decomposition, exact `received_at` and `verified_at` controlled-writer input and identical readback, with no database-clock substitution |
| Typed theory ownership | table-derived Claim or Defense owner kind, same-UUID cross-satisfaction denial in both directions, and own-kind positive cases |
| Matter lifecycle | proposed, open, closed-never-opened, closed-after-opened, reopened, and archived construct, transition, insert, and strict reconstruction coverage |
| Audit attribution safety | FORCE RLS, no INSERT policy or runtime grant, reject-all placeholder writer |
| Isolation from SKMemory | before and after `skmem-pg` identity comparison and no connection path |
| Disposable database | no network, port, named volume, or retained container |

## Correction review closure

The merged correction pass addresses all consolidated domain and security review
items, including the following original set:

1. typed Work Product Version, Execution, Event, and Receipt persistence;
2. audit insertion deferred fail-closed to the attributable S1-05 writer;
3. total encryption completeness;
4. strict scoped legacy provenance;
5. append-only tension membership and authority history;
6. monotonic update time;
7. scoped identifiers and least-privilege DML;
8. no credential-bearing database URL in process arguments;
9. 31-entity domain parity and synthetic round-trip and invalid-state coverage;
10. migration-owner preflight and owner verification;
11. explicit down-index inventory; and
12. revoke-first runtime provisioning with exact grant readback.

The baseline migrations were corrected in their logical layers. No patch
migration preserves a known-bad unreleased state.

Earlier independent checkpoints were rejected. This checkpoint additionally
addresses every merged re-review finding: live identity status, complete
controlled Work Product and Communication evolution, true write-first mapping,
closed persistence metadata, unambiguous Element ownership, legal-only
ValidationSubject kinds, the closed-never-opened Matter state, frozen Work
Product Version locking and race resistance, and the exact migration-owner
profile. The final merged pass additionally closes bidirectional relation-owner
binding, same-UUID Claim and Defense theory cardinality, consistent artifact
lifecycle locking and terminal completion, the participant xmin bypass,
machine-verifiable auxiliary and controlled write dispositions, and exact
nine-kind vocabulary parity. The final narrow correction also adds durable
approved-decision history, immutable `approval_version` capture, prospective
revocation and race-safe consumer locking, strict historical Execution
reconstruction after revocation, and exact connector receipt timestamp
fidelity. Both independent reviewers accepted these claims against exact frozen
inventory SHA-256
`0fd889ca9a7662b4c12d549506afd10c8f3902699f8f56dc39487a8ced625a2b`.

The closed `ValidationSubject` vocabulary is the nine implemented and
resolver-backed kinds: party, party role, matter event, fact assertion, evidence
item, authority, claim, defense, and deadline. The seven previously documented
but unsupported kinds were a documentation overstatement, not an implemented
contract, and have been removed from the documentation rather than silently
broadening the resolver surface.

Persistence review exposed one narrow S1-01 representation gap: a
`ValidationResult` could previously describe only digest-bearing
`ArtifactBinding` subjects. The backward-compatible `ValidationSubject` value
object adds a required closed `subject_kind` discriminator, non-nil identity,
positive version, and explicit digest applicability. Existing ArtifactBinding
input remains valid. Approval and Execution subjects remain ArtifactBinding
only. Approved S1-01 architecture and design-hash files were not changed. Both
independent reviewers accepted this backward-compatible source-level extension
in the exact frozen review.

## Targeted verification results

Results at the final merged correction checkpoint before independent re-review:

```text
PASS: migration manifest valid, 5 migrations
PASS: PostgreSQL integration contract, 24 tests in 93.037 seconds
PASS: migration up/down/up
PASS: migration suffix down/up for each step count 1 through 5
PASS: synthetic failed migration DDL and registry rollback
PASS: all 31 canonical types production-decomposed before fresh-scope writes
PASS: all 31 fresh records written by declared authority, read, strictly reconstructed, and canonically compared with retained PersistenceMetadata
PASS: independent reverse read-first strict reconstruction of all 31 types
PASS: broader Python unit selection, 212 of 212 tests, plus 1 frontend test
PASS: foundation and domain integration selection, 9 of 9 tests
PASS: Ruff format and lint across scripts, tests, services, and packages
PASS: mypy across changed scripts, services, and packages
PASS: frontend Prettier, lint, typecheck, and production build
PASS: uv.lock exact with 96 resolved packages
PASS: npm dependency tree lock readback
PASS: all 5 approved design hashes unchanged
PASS: synthetic fixture safety, 4 files
PASS: secret scan, no findings outside the reviewed baseline
PASS: Doc Haus provenance, 10 decisions, 3,857 package records, 2,889 CycloneDX components, 55 implementation files, and 432,519 token windows
PASS: CycloneDX 1.6 schema validation
PASS: development dependency compose dry-run, no service mutation
PASS: generated egg-info directories are excluded from Git eligibility
PASS: no retained SKL-S1-02 PostgreSQL container or service
```

The combined live suite contains these exact behavioral tests:

```text
test_00_migrations_preflight_up_down_up_and_owners
test_00_migrator_exact_profile_rejects_every_drift_before_bootstrap
test_01_domain_parity_matrix_and_forced_rls
test_02_unfiltered_scope_and_scoped_duplicate_ids
test_03_least_privilege_grants_and_role_bypass_resistance
test_03_live_tenant_and_principal_status_fail_closed_and_reactivate
test_07_matter_state_graph_including_closed_never_opened_round_trips
test_04_encryption_completeness_is_total_and_fail_closed
test_05_strict_legacy_alias_binding_and_metadata
test_06_append_only_tensions_and_database_authority_history
test_07_claim_and_defense_cardinality_never_cross_satisfies
test_07_representative_domain_state_constraints
test_07_typed_validation_rejects_failed_unrelated_and_stale_evidence
test_07_uuid_scalar_and_effective_interval_boundaries
test_08_full_synthetic_legal_record_round_trip
test_09_optimistic_conflict_and_monotonic_long_transaction_time
test_10_artifact_consumer_lifecycle_serializes_and_completes
test_10_controlled_communication_participants_are_atomic_and_sealed
test_10_controlled_work_product_approval_and_execution_paths
test_10_work_product_gate_race_and_draft_revision_boundaries
test_11_canonical_entities_write_first_through_declared_authorities
test_11_every_domain_entity_round_trips_through_rls
test_12_audit_is_force_rls_append_only_and_writer_fail_closed
test_13_schema_isolation_and_disposable_runtime
```

Write authority is explicit in the write-first proof. Tenant, Client,
Engagement, and Matter use administrative bootstrap. Ordinary allowlisted legal
aggregates use runtime RLS inserts. Communication, Work Product Version,
Approval, Execution, Execution Event, and Execution Receipt use controlled
writers. Authority additionally emits and consumes its declared administrative
identity-row command. The production decomposer supplies every scalar and
relation row plus a complete canonical-input or DB-output disposition. The
synthetic adapter only translates that closed output to parameterized-equivalent
SQL and grants no new runtime privilege. Approval capture adds only the
explicitly declared database-owned `captured_at` metadata; the approved domain
snapshot is otherwise preserved exactly. Receipt evidence is constructed only
after dispatch, and its decomposed `received_at` and `verified_at` canonical
inputs are passed unchanged to the controlled transition and compared exactly
after readback.

The Claim and Defense cardinality adversarial probe uses administrative
bootstrap for the otherwise fail-closed aggregate status mutation and a runtime
principal for the attributable validation evidence. This authority split does
not add a runtime state-transition privilege; it isolates and tests the deferred
database invariant in both cross-kind directions and both own-kind positive
cases.

## Independent acceptance receipts

Both independent reviewers accepted the exact 36-path frozen inventory whose
SHA-256 is
`0fd889ca9a7662b4c12d549506afd10c8f3902699f8f56dc39487a8ced625a2b`.
All 36 paths were byte-stable before and after review.

The independent domain receipt is an agent disposition, not a file artifact:

```text
s1_02_domain_review: ACCEPT
Findings: zero High, Medium, or Low findings
Focused tests: 108 passed
Disposable PostgreSQL tests: 24 passed
Dynamic mapper parity: 31 of 31
Migrations: 5 valid
Inventory before and after: 0fd889ca9a7662b4c12d549506afd10c8f3902699f8f56dc39487a8ced625a2b
Paths stable: 36 of 36
Side effects: no repository, board, or service changes
```

The sealed security review is retained at
`/tmp/codex-security-sklegal-closure.QiShfDgH`. It completed at
2026-08-20T14:42:39Z with scan ID
`scan_sklegal_s1_02_closure_20260820`, complete coverage, and zero reportable
findings. Canonical receipt hashes are:

```text
scan-manifest.json  21aa566b244c5c08e1949841b9ae13f724512ba87e78405e47a75adcecb51f90
findings.json       2e013afbc6529c849c09189562ddd631f16d7e194206e173b1e337baeddd4161
coverage.json       ca3d21d1b5aa9c687a376ba6ceb1cee76cb0e46e94f64df68ff4220bf8d29ee0
report.md           800d0d218efa55c3289dbbfb3907fc0655aefb2091868a8b0cb4fd8f6a87fa9d
results.sarif       b454b983adb818beaae1f138d9464996f542551ff8a99e767c29255c932eb53a
```

The security review was a complete custom frozen-inventory diff because this
new repository has no usable Git baseline. TAC was unavailable because its
connector was not connected, and no hosted scan was possible without a
trustworthy Git coordinate. Final qualification was outside that review scope
and was run separately after acceptance.

## Final qualification receipts

The first full accepted-source `make check` passed in 135.22 seconds of wall
time, 37.16 seconds of user CPU time, and 22.83 seconds of system CPU time. It
included 212 Python unit tests in 14.232 seconds, one frontend test in 118 ms,
and all 33 integration tests in 93.946 seconds. The integration total consists
of 9 foundation and domain tests plus all 24 disposable PostgreSQL persistence
tests. Migration, fixture, design, dependency lock, provenance, formatting,
lint, type, build, SBOM, secret, Python vulnerability, and npm vulnerability
checks all passed. Both vulnerability checks reported zero known findings.

The one authorized `make clean-room-check` copied 122 allowlisted source files
into a fresh temporary tree, bootstrapped the pinned toolchain, and passed its
full nested `make check`. It completed in 322.47 seconds of wall time, 53.95
seconds of user CPU time, and 36.00 seconds of system CPU time. The clean-room
run included 212 Python unit tests in 15.875 seconds, one frontend test in 141
ms, and all 33 integration tests in 93.687 seconds, again with zero Python or
npm vulnerabilities. Its temporary source tree and disposable database were
removed automatically.

The final evidence-state `make check` is recorded on SKCapstone card
`84c6f30c` rather than written back into this self-referential source receipt.
It runs only after this evidence and paired inventory update, preventing a
post-check file edit from invalidating the checked state.

## Data and environment assurance

- All fixtures and database records are synthetic.
- The test database used a digest-pinned PostgreSQL image, tmpfs storage,
  network mode `none`, no published port, no named volume, and automatic
  cleanup.
- No HammerTime corpus or Inbox path was read or written.
- No protected matter or production data was used.
- No production secret, external account, connector, deployment, or persistent
  service was created or changed.
- The `skmem-pg` application schema was not connected to or modified.
- No commit or push was performed.
- Approved architecture and planning files remain byte-identical to their
  recorded hashes.
- The secret scanner keeps `migrations/manifest.json` in scope. Its five exact
  migration SHA-256 findings are individually reviewed in `.secrets.baseline`,
  and a regression proves that a new high-entropy manifest field still fails
  the scan. Migration structure and digest correctness remain independently
  fail-closed in `check_migrations.py`.
- Workspace package metadata generated by local editable installs remains
  ignored by the repository-wide `*.egg-info/` rule and is absent from the
  Git-eligible correction inventory.
- The frozen SHA-256 manifest records the complete review surface. The manifest
  itself is separately identified in the reviewer handoff because a hash file
  cannot contain its own digest.

## Deferred boundaries

- CapAuth capability enforcement belongs to `SKL-S1-03`.
- Conflict, privilege, ethical wall, retention, and legal hold enforcement
  belongs to `SKL-S1-04`.
- The attributable chained audit and outbox writer belongs to `SKL-S1-05`.
- Legacy import orchestration and the protected pilot matter remain outside this
  card.
- Deployment and persistent service provisioning remain outside this card.

The database fails closed when a runtime role leaves the exact safe profile,
and provisioning and runtime checks detect every role-graph edge. PostgreSQL
RLS cannot protect rows after a database administrator grants the login
`BYPASSRLS`; startup drift enforcement and administrative role governance are
therefore explicit deployment trust boundaries, not claims made by S1-02.

## Rollback

For an empty disposable or development database, run all five explicit down
sections through `manage_migrations.py down --steps all`. For any data-bearing
database, require an authorized backup and restore decision before destructive
rollback. Source-only rollback removes the files listed in the frozen
correction inventory and reruns the prior foundation checks.
