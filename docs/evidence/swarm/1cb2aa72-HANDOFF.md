# SKL-S5-04A handoff

Card: SKL-S5-04A, security and tenant-isolation qualification (SKCapstone id
1cb2aa72). Worktree /tmp/swarm/1cb2aa72, branch local-only at HEAD cb72b42.
Dependencies on the card: ea2c9790, 48fde7c1 (complete; board state owned by
jarvis, not touched per boundary).

## Files changed

| File | Change | Commit |
| --- | --- | --- |
| tests/integration/test_isolation_qualification.py | verified recovered suite, no edits | (pre-existing 7e95eef) |
| tests/test_retrieval_partition_leak_matrix.py | fixed 3 defective assertions in the recovered suite | 5ae41b6 |
| tests/test_api_capability_attack_battery.py | new adversarial suite, 16 tests | ec5a5eb |
| docs/evidence/security/SKL-S5-04A-ISOLATION-REPORT-2026-08-22.md | new qualification report | cb72b42 |
| HANDOFF.md | this file | (this commit) |

`.swarm-brief.md` arrived untracked and stays untracked. No files outside the
worktree were touched. No HammerTime paths, no secrets, no board commands, no
external actions.

## Tests and exact results

All commands run from the worktree root with the repo-pinned toolchain
(`UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package <pkg>`).

```
# Lane 1, RLS contract tests at scale (Docker postgres, disposable)
uv run --locked --package sklegal-persistence \
  pytest tests/integration/test_isolation_qualification.py -q
  -> 13 passed, 249 subtests passed in 35.83s

# Lane 2, retrieval partition leak matrix (S2-10 contract)
uv run --locked --package sklegal-retrieval \
  pytest tests/test_retrieval_partition_leak_matrix.py -q
  -> 38 passed, 71 subtests passed in 0.13s

# Lane 3, capability attack battery (composition + HTTP seam)
uv run --locked --package sklegal-api \
  pytest tests/test_api_capability_attack_battery.py -q
  -> 16 passed, 24 subtests passed in 0.23s

# Lint
uv run --locked --package sklegal-api ruff check tests/test_api_capability_attack_battery.py
  -> All checks passed!
uv run --locked --package sklegal-retrieval ruff check tests/test_retrieval_partition_leak_matrix.py
  -> All checks passed!
```

## Acceptance criteria evidence

1. Isolation test report with zero unexplained leaks:
   `docs/evidence/security/SKL-S5-04A-ISOLATION-REPORT-2026-08-22.md`.
   Verdict section states zero unexplained leaks; every executed probe fails
   closed; 29 not-yet-implementable probes are pinned with their missing
   component; zero product defects found.
2. Retrieval partition tests follow the S2-10 contract: the matrix in
   `tests/test_retrieval_partition_leak_matrix.py` partitions fully into
   executed (38) plus BLOCKED_ENTRIES (29) with the accounting test proving
   zero unexplained gaps; blocked reasons cite the missing component per the
   S2-10 contract (activation registry, retrieval cluster, broker/pool,
   unqualified AGE backend, lifecycle registry, cache, retirement workflow).
3. Findings triaged into cards before S5-05: zero product findings, so no
   cards are needed. The only defects found were 3 broken assertions inside
   the recovered retrieval test suite itself, fixed in commit 5ae41b6; they
   did not mask any product leak. Per the task boundary I did not run board
   commands; jarvis owns card creation and completion.

## Scope notes per lane

- Lane 1 (recovered work verified): cross-tenant reads on every forced
  table, cross-matter reads within tenant, unbound bypass and shared runtime
  roles read nothing; update/delete/truncate denied everywhere, cross-scope
  insert and audit append denied; SET ROLE and session authorization
  denied, caller-set GUC not an authorization fact, pg_temp shadowing
  ineffective, function EXECUTE allowlist inventory exact, policy snapshot
  scope-exact; barrier tables sealed for every runtime role, role bindings
  self-read-only.
- Lane 2: fixed three defective assertions (denial-shape probe now compares
  public error envelopes across two foreign partition ids and checks no
  tenant or physical partition id leakage; invalid-identifier probe uses a
  suffix and kind matrix through ProjectionPins; legacy-alias probe expects
  RetrievalIntegrityError, the exception actually raised).
- Lane 3 (new): forgery (stale signature over tampered payload, absent and
  garbage signatures, self-declared untrusted issuer with valid signature,
  TTL-cap expiry extension, unkeyed payload identity recompute), request
  binding (wrong principal/matter/target with validly re-signed tokens;
  cross-tenant re-point fails closed at the durable snapshot;
  AuthorizationRequest refuses tenant mismatch; purpose forgery is
  structurally malformed), replay (sequential, delegation-consumed parent,
  byte-identical re-presentation), revocation during flight (leaf, ancestor,
  principal suspension: the in-flight call allows, every later decision
  denies), audit-write failure rescinds the allow, and HTTP bearer surface
  (forged, foreign-issuer, malformed, wrong-scheme, replayed: 403
  capability_denied, decision id present, no credential or tenant bytes
  echoed).

## Known limitations

- Lane 3 drives the composition over fake durable executor state; live
  PostgreSQL behavior of the same SECURITY DEFINER functions is covered by
  lane 1, but the two were not combined into one live-database capability
  attack run.
- 29 S2-10 probes remain blocked on unimplemented components (listed in the
  evidence report); they must be unblocked by the cards that implement
  those components before S5-05 relies on them.
- Deep delegation chains (depth > 1), TTL boundary arithmetic beyond the
  cap, and concurrent mid-flight revocation races are covered by the
  neighboring composition-health suite (test_api_capauth_composition_health.py),
  not re-tested here.
- The attack battery models a leaked signing key via the capauth testing
  stub; real PGP key compromise behavior is outside this suite's reach.

## Migration or rollback

Tests and documentation only; no data or schema changes, no migration, no
rollback needed.
