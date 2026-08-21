# SKL-S1-05 independent review evidence

Card: `61c43e1e`  
Implementer: `codex-audit`  
Date: 2026-08-20  
Status: Frozen for independent review, card remains Doing

This receipt covers the third corrected implementation of append-only audit,
transactional outbox delivery, projection watermarks, and protected telemetry
correlation after three frozen inventories were rejected by independent review.
The card has not received fresh independent acceptance. No final
`make check`, clean-room check, board Review transition, commit, push, pull
request, deployment, production action, live key access, or protected corpus
access has occurred.

## Dependency and claim readback

The unified board readback immediately before evidence sealing reported:

- card `61c43e1e`, SKL-S1-05, status `doing`, owner `codex-audit`;
- dependency `b04de409`, architecture approval, status `done`;
- dependency `84c6f30c`, S1-02 PostgreSQL and RLS, status `done`; and
- dependency `0ef8eb48`, S1-03 CapAuth, status `done`.

The card's exact TDD remains
`docs/tasks/SUBAGENT-TASK-TTDS.md#skl-s1-05-implement-append-only-audit-outbox-and-telemetry-correlation`.
The approved design-hash gate passed for all five architecture artifacts before
the implementation freeze.

## Implemented boundaries

1. **Closed event schema:** Strict immutable values carry opaque event, tenant,
   principal, optional matter, run, correlation, trace and span IDs; exact
   authorization and policy decision references; one closed boundary; safe
   action, outcome, reason, resource metadata, and server evidence. Unknown
   attributes and unvalidated model construction are rejected.
2. **Protected-field suppression:** Audit and telemetry have no arbitrary
   attribute, prompt, source document, query, message, tool argument, model
   output, credential, bearer, exception, or baggage field. PostgreSQL mirrors
   the strict Python key, type, pattern, and numeric range checks.
3. **CapAuth and policy correlation:** `DurableAuditSink` maps reviewed S1-03
   authorization decisions and S1-04 access and retention decisions to exact
   safe references without raw signed credentials, credential digests, policy
   content, waiver content, source material, or backend payloads.
4. **Append-only tenant chain:** Migration 0007 replaces the deliberately
   unavailable S1-02 scaffold with a controlled FORCE-RLS writer. It locks the
   tenant head, assigns exact sequence and trusted recording time, constructs
   canonical JSON, hashes it with SHA256 and the predecessor, and prevents
   direct insertion or later event change.
5. **Transactional outbox:** The same append transaction writes one local
   outbox message bound to the event hash. Rollback removes event, head change,
   and message together. Delivery creates one immutable deterministic receipt;
   a repeated acknowledgement is idempotent.
6. **Projection watermarks:** One exact compare-and-set function binds each
   tenant projection to an existing event sequence and hash. Exact replay is a
   no-op; stale, backward, and unknown advancement is denied.
7. **W3C correlation and local retention:** Propagation carries only
   `traceparent`, never baggage. A bounded process-local buffer uses trusted
   ingestion time, retains for no more than seven days, and stores only the
   closed telemetry span model. No collector or exporter is configured.
8. **Sanitized failure:** Durable sink and PostgreSQL adapter failures produce
   generic `AuditUnavailable` exceptions with no backend cause or context.
   The database uses bounded error messages that do not echo rejected values.
9. **Owner-safe rollback refusal:** Each append transaction writes a
   content-free singleton sentinel. It is visible to the NOBYPASSRLS migration
   owner, unavailable to runtime roles, and protected by the controlled writer.
   The down guard reads it before any destructive DDL.
10. **Complete-tenant safe verification:** A narrowly controlled function-local
    RLS policy gives the verifier full visibility for the exact authorized
    tenant. It checks row count, sequence, predecessors, canonical payloads,
    hashes, tip, and chain head, then returns only a boolean result.
11. **Stable canonical and validation contract:** Canonical timestamps render
    explicitly in UTC with a numeric four-digit year in the shared AD 1 through
    9999 domain. Python and PostgreSQL share lowercase safe-code rules and a
    100-character resource-kind limit. Audit copies, replacements,
    constructions, and telemetry ingestion all revalidate or deny.
12. **Cross-adapter canonical bytes:** Python and PostgreSQL recursively sort
    object keys, preserve array order, use compact separators and stable JSON
    scalars, remove null optional attributes, encode UTF-8, and render both
    timestamps with six fractional digits and terminal `Z`. A public safe
    recomputation helper verifies database-returned event digests.
13. **Request-local integrity mode:** Every grantable audit definer neutralizes
    caller-supplied integrity mode before RLS reads. Only the verifier enables
    it after current authorization and restores the prior value on normal and
    exception paths.
14. **Independent telemetry exports:** Every local export is a fresh deep,
    revalidated snapshot. Export mutation cannot alias stored state, and
    poisoned internal state fails closed without emitting a span.

## Independent review correction receipt

The initial inventory
`SKL-S1-05-FROZEN-INVENTORY-2026-08-20.sha256`, whose SHA256 is
`4aa00d5cc3672790d84bb3f9e6b6dfa21b2977a7dddd373f6c91eaf022c37666`,
was rejected with six merged findings plus one timezone candidate. It remains
historical evidence and is not the current review target.

The correction closes each finding as follows:

1. The migration down guard no longer infers emptiness through FORCE RLS. It
   reads a non-user-writable, content-free sentinel written transactionally by
   `append_event`, before any destructive DDL. Tests prove rollback denial with
   normal event and outbox data, event-only data, and outbox-only data. Empty
   databases still pass exact down and up cycles.
2. Projection initialization serializes on the tenant chain head and performs
   separate checked insert or update operations. Both concurrent transaction
   orders prove exactly one initializer succeeds, stale initialization denies,
   exact replay is idempotent, and no backward or different-event overwrite is
   possible.
3. Tenant chain verification uses a tightly scoped policy that requires the
   security-definer owner, a distinct safe runtime session, a function-local
   verification flag, and the exact current tenant. Direct callers cannot turn
   on full visibility. Hidden-matter tamper and visible-prefix gap tests prove
   the boolean covers the complete chain without returning row detail.
4. CapAuth mapping preserves UUID and non-UUID resource identity in a sanitized
   opaque field. Policy mapping preserves the exact protected data-flow
   boundary across retrieval, cache, model context, export, and detailed audit.
   Durable reconstruction tests cover every mapping.
5. Immutable audit values reject deprecated copy updates, partial copies,
   replacements, and unvalidated construction. The telemetry buffer revalidates
   input before storage and rejects a tampered protected attribute.
6. Python and PostgreSQL now use the same lowercase safe attribute vocabulary
   and exact 100-character resource-kind limit. Boundary tests prove 100 is
   accepted, 101 is denied, and uppercase attribute values are denied.
7. Canonical occurrence and recording timestamps have an explicit UTC format.
   Direct construction, append, reconstruction, digest, and verification remain
   identical across UTC, Pacific/Chatham, and America/Los_Angeles sessions.

## Second independent review correction receipt

The first correction inventory
`SKL-S1-05-CORRECTION-FROZEN-INVENTORY-2026-08-20.sha256`, whose SHA256 is
`b65d7027c9af026cc1248483731c4a0271a1ea533faa4ac210b91eefe27bbe17`,
was verified stable at 35 of 35 paths by both independent reviewers. Both
reviewers rejected it with the same three findings. It remains historical
evidence and is not the current review target.

The second correction closes those findings as follows:

1. PostgreSQL no longer hashes `jsonb::text`. A recursive canonical serializer
   produces compact JSON with lexicographically sorted object keys, preserved
   array order, stable scalar encoding, and UTF-8 bytes. Python uses the same
   rules, strips null optional attributes, and formats both event timestamps as
   UTC with exactly six fractional digits and terminal `Z`. Tests reconstruct
   the complete database-returned chain through `PostgresAuditRepository`,
   compare every SQL digest with Python recomputation, and require
   `verify_event_chain` to pass across zero and nonzero microseconds, null
   fields, populated attributes, chained predecessors, and non-UTC sessions.
2. `append_event`, `record_outbox_delivery`, and
   `advance_projection_watermark` clear caller-supplied integrity mode before
   any RLS read. The verifier also clears it before authorization, enables it
   only after current authorization, and restores the prior value on success or
   exception. Direct hidden counts remain zero, and watermark advancement after
   matter access loss denies identically with and without a caller-set mode.
3. `LocalTelemetryBuffer.export_local` returns fresh deep validated objects.
   Mutating one export cannot change storage or a later export. Deliberately
   poisoned internal state raises sanitized `AuditUnavailable` and emits
   nothing.

## Third independent review correction receipt

The second correction inventory
`SKL-S1-05-CORRECTION-2-FROZEN-INVENTORY-2026-08-20.sha256`, whose SHA256 is
`c1c8b220cc10049c0c1eb9cc0048fa5822b6ae4504012416eb51c37f0d59d934`,
was verified stable at 36 of 36 paths by both independent reviewers. Both
reviewers rejected it with the same remaining timestamp-domain finding. It
remains historical evidence and is not the current review target.

The third correction closes that finding with one exact cross-adapter contract:

1. Python renders every UTC field numerically, including an explicitly
   zero-padded four-digit year, rather than delegating low-year behavior to
   platform `strftime`.
2. PostgreSQL uses one canonical timestamp helper for both occurrence and
   recording time. It rejects non-finite values, BC values, and every year
   outside AD 1 through 9999 before formatting. The granted append path calls
   that helper before any chain-head, event, outbox, or rollback-sentinel write.
3. Direct adapter tests compare exact canonical text and event digests for AD
   years 1, 9, 99, 999, 1000, and the current year, plus direct helper year
   9999. They retain zero and nonzero microseconds, null and nested attributes,
   chained events, and non-UTC sessions. BC and year 10000 append attempts leave
   event, outbox, and rollback-sentinel counts at zero and cannot produce a
   Python semantic event.

## Test-first receipts

The third correction began with an exact red test. Python rendered years 1, 9,
99, and 999 without the required leading zeroes. PostgreSQL accepted a BC
append, leaving event, outbox, rollback-sentinel, and target-event counts at
`1:1:1:1`. The SQL timestamp helper was initially absent. The new tests passed
only after both adapters shared the explicit AD 1 through 9999 contract and the
append validation moved ahead of all mutations.

The initial unit test failed on the missing PostgreSQL append adapter constant.
The foundation test then failed on the old six-migration contract. After the
first implementation, disposable PostgreSQL exposed a stale UUID constraint,
default PUBLIC execute on two trigger helpers, two invalid test fixtures, and a
composite alias error in chain verification. Each failure was isolated and
corrected before the next broader gate.

A nearby adversarial parity test was added after the first green live suite. It
proved ten malformed database attribute cases were accepted, including a
numeric capability, a string or fractional resource version, boolean or
out-of-range status code, and negative, fractional, or excessive retry count.
That test failed first. The SQL predicate was then narrowed to the exact Python
types and ranges, and the same disposable test passed.

## Corrected pre-review verification

```text
PASS: focused audit unit suite, 11 of 11 tests
PASS: focused corrected PostgreSQL boundaries, 9 of 9 cases
PASS: broader Python unit suite, 280 of 280 tests in 5.498 seconds
PASS: frontend unit suite, 1 of 1 test in 121 milliseconds
PASS: broader integration suite, 44 of 44 tests in 87.338 seconds
PASS: disposable PostgreSQL persistence contract, 34 cases in 87.786 seconds
PASS: migration up, down, and up plus every rollback depth in isolated databases
PASS: Ruff formatting across 66 files and full Ruff lint
PASS: mypy across 40 source files
PASS: frontend Prettier, ESLint, TypeScript, and production build
PASS: all 5 approved design hashes unchanged
PASS: uv.lock exact, 105 resolved packages
PASS: migration manifest, 7 migrations
PASS: synthetic fixture safety, 4 fixture files
PASS: no em dash or en dash in the SKL-S1-05 review paths
PASS: no disposable PostgreSQL or clean-room tree remains
```

The third correction migration SHA256 at freeze is recorded by the manifest. The
exact lock, secret baseline, source, tests, documentation, this receipt, and the
three historical rejected inventories are listed in
`docs/evidence/audit/SKL-S1-05-CORRECTION-3-FROZEN-INVENTORY-2026-08-20.sha256`.
That third correction inventory is the only byte set for the requested fresh
independent review. No source or evidence byte may change until the reviewer
returns a disposition.

## Acceptance matrix

| Requirement | Frozen evidence |
|---|---|
| Run IDs and boundary correlation | one run replays ordered API, workflow, tool, model, human, and connector events with one trace and distinct spans |
| Append-only events | immutable strict models, tenant sequence and predecessor hash, canonical payload rehash, direct insert/update/delete denials |
| Policy-decision references | exact CapAuth authorization ID, UUID or opaque resource identity, S1-04 access or retention decision ID, and exact policy boundary in safe metadata |
| Transactional outbox | event, head, and outbox rollback together after a forced transaction failure |
| Duplicate delivery | one immutable receipt and deterministic idempotency key; second acknowledgement returns false |
| Projection watermarks | serialized initialization, exact existing hash target, stale compare-and-set denial, monotonic advancement, idempotent replay |
| OpenTelemetry propagation | strict W3C version 00 `traceparent`; baggage and arbitrary carrier attributes excluded |
| Redaction and local retention | closed schemas deny protected fields; bounded trusted-time buffer expires spans and exports fresh deep validated snapshots |
| Tamper evidence | Python and PostgreSQL share recursively sorted compact UTF-8 canonical bytes and numeric four-digit, six-microsecond UTC timestamps across AD 1 through 9999 and the complete tenant chain |
| Protected-field suppression | Python copy and telemetry revalidation matrix plus PostgreSQL unknown-key, lowercase vocabulary, and strict type/range matrix |
| Rollback safety | owner-safe content-free sentinel denies normal, event-only, and outbox-only data-bearing rollback before destructive DDL |
| Integrity mode | direct caller mode cannot reveal hidden events or authorize watermark advancement; verifier alone enables full-tenant boolean verification after authorization and restores state |

## Security and data assurance

- All events, identities, resources, traces, policy decisions, payload
  sentinels, and delivery evidence used in tests are synthetic.
- No HammerTime Inbox or corpus path was inspected, read, or changed.
- No production database, service, account, keyring, signing key, prompt,
  document, model, connector, cache, vector store, graph store, collector,
  exporter, or external action was accessed.
- Disposable PostgreSQL tests used the digest-pinned PostgreSQL 17.7 image with
  no network, no published port, tmpfs data, automatic removal, and no named
  volume.
- The pre-existing `skmem-pg` container, if present, was observed only by its
  identifier for isolation assurance and was not used or changed.
- The secret baseline addition is only the reviewed SHA256 from migration
  manifest line 30. It is not a credential or secret.

## Production limitations and prerequisites

This card provides contracts and a qualified isolated PostgreSQL writer. It
does not claim production composition. Production remains blocked on:

1. service routes and workers that append audit on the same connection and in
   the same transaction as every mutation, and record every external boundary;
2. authorized outbox worker composition with bounded retry, lag and health
   reporting, and idempotent destination effects;
3. authorized audit-read APIs that reapply CapAuth, policy, RLS, information
   barriers, and sanitized response shaping;
4. an approved local OpenTelemetry collector configuration and explicit review
   before any external telemetry export;
5. audit archival, backup, restore, legal-hold, and optional independent chain
   anchor decisions; and
6. operational monitoring for audit failure, projection lag, storage pressure,
   receipt ambiguity, and retention failure.

The SHA256 tenant chain is tamper evident, not a cryptographic signature or an
external timestamp. An outbox receipt proves the database acknowledgement, not
that a non-idempotent destination effect could never occur before a worker
crash. The in-memory ledger and telemetry buffer do not coordinate across
processes and are never a production default.

## Review and rollback boundary

Independent review must verify every path and hash in the frozen inventory,
inspect the database privilege and RLS boundary, challenge protected-field
suppression, transaction rollback, duplicate delivery, chain verification,
watermark monotonicity, and exception sanitation, then recheck the same hashes
before returning acceptance or rejection.

No final `make check` or clean-room check is authorized before that disposition.
The board must remain Doing. Migration down is refused after any successful
append by the owner-safe content-free sentinel, even if privileged fault
simulation leaves only event or only outbox data. An empty development database
can restore the earlier unavailable writer scaffold; a data-bearing environment
requires separately approved preservation and restore and must not discard
audit evidence for rollback.
