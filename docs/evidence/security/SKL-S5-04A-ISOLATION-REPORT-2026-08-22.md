# SKL-S5-04A security and tenant-isolation qualification report

- Card: SKL-S5-04A (SKCapstone id 1cb2aa72)
- Date: 2026-08-22
- Worktree: /tmp/swarm/1cb2aa72, branch at commits 7e95eef -> 5ae41b6 -> ec5a5eb
- Scope: prove cross-tenant and cross-matter isolation end to end before the
  S5-05 rollout gate, across three lanes: RLS contract tests at scale,
  retrieval-store partition leak tests per the S2-10 contract, and capability
  forgery, replay, and revocation-during-flight attempts against the API
  composition.

## Verdict

Zero unexplained leaks. Every executed probe in all three lanes fails closed,
and every not-yet-implementable probe is explicitly pinned in the blocked
accounting with its missing component. No product defect was found; the only
defects found and fixed were in three assertions of the recovered retrieval
test suite itself (commit 5ae41b6), none of which masked a product leak.

## Lane 1: RLS contract tests at scale

File: `tests/integration/test_isolation_qualification.py` (recovered in-flight
work, verified and extended coverage confirmed on 2026-08-22).

Command:

```
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked \
  --package sklegal-persistence \
  pytest tests/integration/test_isolation_qualification.py -q
```

Result: `13 passed, 249 subtests passed in 35.83s` against a disposable
Docker postgres (image postgres:17.7-alpine, pinned digest, network none,
tmpfs) created by the suite itself.

Coverage groups:

- Read matrix: cross-tenant reads across every forced table; cross-matter
  reads within the same tenant; unbound bypass and shared runtime roles read
  nothing.
- Write matrix: update/delete/truncate denied on every table; cross-scope
  insert blocked by RLS CHECK constraints; audit append cross-scope denied
  for bound roles.
- Policy-gateway bypass attempts: `SET ROLE` and session authorization
  denied; caller-set GUC is not an authorization fact; `pg_temp` shadowing
  cannot defeat RLS predicates; function EXECUTE inventory matches the
  allowlist; policy snapshot denied outside exact scope.
- Ethical-wall and privilege probes: barrier tables sealed for every runtime
  role; role bindings self-read-only.

## Lane 2: retrieval partition leak tests (S2-10 contract)

File: `tests/test_retrieval_partition_leak_matrix.py`.

Command:

```
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked \
  --package sklegal-retrieval \
  pytest tests/test_retrieval_partition_leak_matrix.py -q
```

Result: `38 passed, 71 subtests passed in 0.13s`.

Coverage: authorization denied before lexical, vector, or registry reads;
cross-tenant and cross-matter partition denial for lexical and vector lanes;
wrong-scope rows rejecting the entire response; raw SQL, tsquery config,
filter override, and raw Cypher rejection; replica LSN watermark enforcement;
policy and rights revision changes denying cached or in-flight results;
credential binding (wrong matter, wrong principal, wrong partition or
generation, revoked, stale policy binding); denial-shape uniformity across
foreign partition ids; mixed projection-set and release/generation component
denial; tenant-shared scope requiring an explicit decision; invalid registry
partition identifier rejection; legacy alias routing rejection.

S2-10 accounting: the suite pins every not-yet-implementable probe in
`BLOCKED_ENTRIES` with its missing component, and the accounting test
(`test_matrix_is_fully_partitioned_into_executed_and_blocked`) proves the
matrix partitions into executed plus blocked with zero unexplained gaps:

- projection-set activation registry not implemented: image digest or
  postgres version mismatch, extension revision checksum or runtime version
  mismatch, extension source sha256 algorithm or digest mismatch, SBOM
  vulnerability or license evidence mismatch, vulnerability scan revision or
  expired high-risk acceptance, AGE unqualified or evidence mismatch (6)
- retrieval cluster not provisioned: shared runtime login rejected (1)
- credential broker and connection pool not implemented: broker and pool
  connection reuse across principal scope or generation (1)
- AGE graph backend unavailable_unqualified: graph cross-tenant and
  cross-matter partition denial, catalog enumeration, gateway execute ACL,
  owner and search-path hardening, owner ACL across generations, schema
  qualification and pg_temp shadowing, public execute, definition hash
  mismatch, dynamic SQL query text DDL and mutation, exception shape,
  write templates, wrong-scope entity, relationship endpoint mismatch,
  mixed-scope path, count aggregate and existence (15)
- projection lifecycle registry not implemented: retired generation never
  selected, manifest mutation denied, optional component addition requires
  cutover, candidate and partial generations never selected (4)
- retrieval result cache not implemented: cache key collision and revision
  invalidation (1)
- retirement workflow not implemented: shared physical generation deletion
  blocked by any referencing scope (1)

Total: 29 blocked entries, 38 executed tests, zero unexplained.

Test-suite fixes (not product fixes) made in commit 5ae41b6: the denial
shape probe now actually compares public error envelopes across two foreign
partition ids; the invalid identifier probe replaces a broken
generator-throw hack with a suffix and kind matrix; the legacy alias probe
expects `RetrievalIntegrityError`, the exception actually raised for an
invalid projection set.

## Lane 3: capability forgery, replay, and revocation in flight

File: `tests/test_api_capability_attack_battery.py` (new, commit ec5a5eb).

Command:

```
UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked \
  --package sklegal-api \
  pytest tests/test_api_capability_attack_battery.py -q
```

Result: `16 passed, 24 subtests passed in 0.23s`; `ruff check` clean.

The battery drives the production composition seam
`build_postgres_capability_authorizer` (`services/api/src/sklegal_api/
capauth.py`) over fake durable executor state implementing exactly the four
SECURITY DEFINER SQL functions, plus the `ProtectedRouteDependency` HTTP
boundary with a FastAPI TestClient. Findings by attack class:

- Forgery: stale signatures over tampered payloads deny INVALID_SIGNATURE
  (matter, full-principal swap, credential nonce); empty, garbage, or
  missing signatures fail closed; self-declared issuers deny
  UNTRUSTED_ISSUER even when the signature verifies; expiry extension is
  caught pre-signature by the TTL cap; recomputing the unkeyed payload
  identity does not help an attacker without signing material.
- Request binding: validly re-signed credentials naming another real
  principal, matter, or route deny WRONG_PRINCIPAL, WRONG_MATTER,
  WRONG_TARGET; cross-tenant re-points fail closed at the durable snapshot
  (BACKEND_UNAVAILABLE), matching the SQL scope guard; AuthorizationRequest
  itself refuses tenant mismatch structurally; purpose forgery is
  structurally impossible (each capability has exactly one valid purpose),
  so the tampered token is MALFORMED_CREDENTIAL before any signature check.
- Replay: sequential repeats deny REPLAYED (reservation keyed on tenant plus
  leaf digest); delegation consumes the parent reservation so re-delegation
  and parent reuse both fail; byte-identical re-presentation stays a replay,
  only genuinely fresh issuance allows again.
- Revocation during flight: a revocation or suspension write landing between
  the snapshot read and the replay reservation allows exactly the in-flight
  call (documented ordering) and closes every subsequent decision with
  REVOKED, ANCESTOR_REVOKED, or PRINCIPAL_INACTIVE. No window, cache, or
  single-decision state survives into the next decision.
- Audit failure: a failing durable audit write rescinds the allow
  (AUDIT_UNAVAILABLE); an allow is never silently kept.
- HTTP surface: forged, foreign-issuer, malformed, wrong-scheme, and
  replayed bearers return 403 `capability_denied` with a decision id and
  without echoing credential bytes, tenant ids, or claims metadata.

## Findings for triage before S5-05

Zero product findings. All probes fail closed. The three test-assertion
defects in the recovered retrieval suite were fixed in-repo (commit 5ae41b6)
and are not candidate cards. No cards need to be created from this work.

## Known limitations

- Lane 3 exercises the composition through fake durable executor state, not
  live PostgreSQL; the SECURITY DEFINER functions' real SQL behavior in lane
  1 covers that side. The two lanes have not been run as one combined
  live-database capability test.
- Lane 1 and lane 2 coverage gaps are pinned as blocked entries above and
  depend on components that later cards must implement (activation registry,
  retrieval cluster, credential broker and pool, qualified AGE backend,
  lifecycle registry, result cache, retirement workflow).
- Delegation depth beyond one level, TTL boundary arithmetic beyond the
  cap probe, and concurrent mid-flight revocation races are covered by the
  neighboring composition-health suite rather than re-tested here.
