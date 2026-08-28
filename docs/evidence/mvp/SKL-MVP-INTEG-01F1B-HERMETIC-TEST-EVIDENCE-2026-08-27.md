# SKL-MVP-INTEG-01F1B hermetic test repair evidence

Card: 5d14dd5d
Successor to: 95e3db49
Repair agent: codex-luna-5d14dd5d
Reviewed parent commit: 36b84ef1021b9a012298767bf67e2de04799482b
Reviewed parent tree: c1cd64ac5e93795403e70bbf954fb1c100432c2f
Implementation commit: 4b9fcad20899735a4a631ea3ab35d4d70a1a46b7
Implementation tree: a8258ffdacb544336e1e36c17ce2eb0053919ff8

## Scope

This repair addresses only the two order dependencies recorded by blocked
review 95e3db49. The canonical parity test now ensures that its existing
write-first public-synthetic fixture exists before the RLS round-trip test.
The audit timestamp test now compares its postcondition with the pre-test
counts, so it remains valid when the rollback guard already contains evidence.

The existing SentenceGrounding mapping was not changed. No migration, domain,
RLS, CapAuth, tenant or Matter isolation, append-only history, schema guard,
fixture source, production code, credential, protected data, HammerTime Inbox,
provider, deployment, merge, push, restart, external action, or 431db4dd was
touched.

## Changed paths and hashes

- `tests/integration/persistence_contract_canonical_parity.py`
  SHA-256: 99bb6dbe14be973a8563a75a162af16fdc03e35067e305d04fdbf63629a510bc
- `tests/integration/persistence_contract_security_boundary.py`
  SHA-256: ed5ab565ed4c24a04c58467cec4eaf1f260be7f4b71aef933ba046de98aea0ae
- `docs/evidence/mvp/SKL-MVP-INTEG-01F1B-HERMETIC-TEST-EVIDENCE-2026-08-27.md`
  SHA-256: sealed in the durable evidence bundle receipt

## Reproduction before repair

Command:

```text
.tools/bin/uv run --locked --all-packages pytest -q tests/integration/persistence_contract_canonical_parity.py::PersistenceContract08CanonicalParityTests::test_11_every_domain_entity_round_trips_through_rls -vv
```

Result: `1 failed` in a fresh disposable PostgreSQL process. The exact error
was `FATAL: role "sklegal_test_fresh_writer" does not exist`.

The blocked review also recorded the combined audit failure: the timestamp
test expected `0:0:0` after the rollback guard had already created `0:1:1`.

## Repair verification

All PostgreSQL runs used the pinned PostgreSQL 17.7 Alpine image already
declared by the test harness, a networkless disposable container, and teardown
after each process.

1. Isolated canonical parity:
   `1 passed, 37 subtests passed`.
2. Isolated audit timestamp:
   `1 passed, 6 subtests passed`.
3. Canonical pair in normal order:
   `2 passed, 37 subtests passed`.
4. Canonical pair in reversed order:
   `2 passed, 37 subtests passed`.
5. Rollback-guard test first, audit timestamp second:
   `2 passed, 6 subtests passed`.
6. Focused combined disposable PostgreSQL suite over security boundary,
   canonical parity, and migration guards:
   `7 passed, 45 subtests passed`.

The focused suite also passed the real RLS, forced-RLS, CapAuth snapshot,
append-only audit, disposable-runtime, and migration rollback-guard checks.

## Static and repository checks

- Ruff check on both changed files: PASS.
- Ruff format check on both changed files: PASS.
- Python compile check on both changed files: PASS.
- Migration manifest check: `28 migration(s)` valid.
- Fixture safety check: `17 file(s)` valid.
- Changed-file detect-secrets scan: `0 finding(s)`.
- Changed-file ASCII dash scan: PASS.
- `git diff --check`: PASS.
- Scope check confirmed no changed path references card 431db4dd.

The repository-wide secret scan remains blocked by ten inherited findings in
`migrations/manifest.json` and
`tests/fixtures/mvp/public-synthetic-mvp-v2-acceptance.json`. Neither path is
changed by this card, and no baseline change was made.

The direct mypy run remains blocked by inherited missing type-marker and test
harness typing errors outside this repair. No typing suppression or unrelated
source change was made.

## Acceptance

The two recorded order dependencies are repaired and pass alone, in reversed
order, and in the focused combined real PostgreSQL suite. Security and data
integrity assertions remain unchanged. The inherited repository-wide secret
and mypy limitations remain visible for their own repair lanes.

## Rollback

Revert only implementation commit
`4b9fcad20899735a4a631ea3ab35d4d70a1a46b7` and this evidence commit to return
the isolated worktree to parent commit
`36b84ef1021b9a012298767bf67e2de04799482b`. No runtime, database, credential,
deployment, merge, push, or host rollback exists.
