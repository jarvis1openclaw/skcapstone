# Approved amendment: local PostgreSQL retrieval architecture

Amendment ID: `AMENDMENT-SKL-S2-10`
Cards: `f5b93935` (`SKL-S2-10`) and `c4ef2a90` (`SKL-S2-04`)
Recorded timestamp: 2026-08-21T17:10:40-05:00
Human owner: `skuser01`
Status: approved

## Human decision

After reviewing the recommended replacement topology, the human owner stated:

> i approve the changed arch - continue with the replacements and updates

This is recorded as approval of the scoped retrieval replacement proposed in
the immediately preceding review. It is not approval of any pending database
principal, external-action, deployment, production, corpus-processing, or
model-naming amendment.

## Original approved baseline

The original architecture was approved on 2026-08-19 with these hashes:

```text
63a6134dfedc2e1acd047d3b993e1ecfe6b2ac4262cd469caaacf2fc7c7cbc30  docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md
0705159e43f6dd28beda4b6cbcb72444b4e23678408bbbbccc76f1903d76c7a4  docs/architecture/LIBERTY-AUTO-PILOT-TDD.md
836b1604564c863f3614f3ddf5b49aaa4d340ff70968a004dfa4a42dbb304496  docs/planning/EPIC-SPRINT-PLAN.md
4debdeb5b6db65840aa8ac11eae2d684bb619f7af1af9535cb1b22fe90776fd1  docs/tasks/SUBAGENT-TASK-TTDS.md
4402c7dc2bdb9ed245b6d581d325f9eaa887b191d9a209737b0a1dcf2e3b4005  docs/approval/index.html
```

Those values remain historical evidence. They are not rewritten.

## Approved replacement

The initial protected retrieval data plane changes as follows:

- `sklegal-core-pg` is a dedicated PostgreSQL 17 cluster for canonical legal,
  policy, audit, outbox, workflow-reference, and projection-registry state.
- `sklegal-retrieval-pg` is a separate local PostgreSQL 17 cluster for
  rebuildable PostgreSQL full-text, pgvector, and optional Apache AGE
  projections.
- The core and retrieval services have separate databases, volumes, roles,
  credentials, resource controls, restart lifecycles, backup lifecycles, and
  private network identities.
- SKLegal does not share the live `skmem-pg` instance, application schema,
  volume, roles, credentials, migration lifecycle, or backup lifecycle.
- Initial vector search is exact pgvector over physical Tenant and generation
  partitions with forced Matter row security. Approximate indexes require a
  later frozen evaluation and explicit promotion.
- AGE is optional, derived, and activation-gated. Protected graphs are
  physically partitioned by Matter and projection generation. Explicitly
  Tenant-shared material uses separate Tenant-shared graphs.
- Activation pins the exact retrieval image, PostgreSQL and extension
  versions, source revisions and SHA-256 digests, SBOM, vulnerability and
  license evidence, and conditional AGE qualification evidence. Scan
  observation and advisory revisions plus any time-bounded high-risk
  acceptance are image-bound. A mismatch or expiry denies.
- AGE row-level security is not an authorization boundary. Registry-selected
  opaque graph names, exact Principal and graph-generation gateway credentials,
  hardened closed query gateways, graph ACLs, and full result validation
  provide the graph backstops.
- Core and retrieval credentials remain distinct. Retrieval uses an opaque,
  separately provisioned `session_user` login for one Principal, exact Matter
  or explicitly Tenant-shared scope, and projection generation. A CapAuth
  broker resolves it after policy approval, and database-owned
  bindings must match the current core policy and revocation watermark.
- Existing HammerTime Qdrant and FalkorDB projections remain upstream-owned
  compatibility and shadow-comparison sources. They are not a protected
  SKLegal runtime path and receive no new protected projection without a
  later approved amendment.
- The canonical transaction commits first. A transactional outbox drives
  idempotent projection, reconciliation, atomic generation cutover, and
  rollback.
- Physical PostgreSQL replication is a future availability or read-scale
  option. It is not a backup, write scaling, or deployment authorization.

The implementation contract is:

- `docs/development/RETRIEVAL-PARTITIONS.md`
- `config/retrieval/tenant-partition-contract.json`

## Preserved boundaries

This amendment does not change:

- HammerTime custody of original corpus artifacts and promoted releases;
- CapAuth, Tenant, Matter, conflict, privilege, ethical-wall, rights,
  retention, and legal-hold enforcement;
- typed model proposal and human-approval boundaries;
- the Liberty Auto Plaza pilot scope;
- simulation-only external actions and connector activation gates;
- the existing per-principal PostgreSQL `session_user` contract;
- the pending S3-08 database-principal amendment;
- the recorded Qwen model naming issue; or
- the prohibition on production deployment, external account creation,
  additional Matter migration, HammerTime `Inbox/` processing, and outbound
  legal actions without their own eligible cards and gates.

## Independent review and verification

A read-only governance and security review independently checked the topology,
authorization binding, connection-pool isolation, relational partitions,
optional graph gateway, supply chain, atomic activation, retirement, trace and
cache pins, legacy-backend boundary, approval scope, and machine-to-narrative
consistency. All reported blockers were resolved. The final pre-hash result was
PASS.

Pre-hash evidence:

```text
.venv/bin/python -m unittest -v tests.test_retrieval_partition_contract
Result: 15 tests passed.

.venv/bin/python -m unittest -v tests.test_status_page.StatusPageTests.test_page_has_accessible_static_landmarks tests.test_status_page.StatusPageTests.test_status_content_preserves_current_gates_and_legal_vocabulary tests.test_status_page.StatusPageTests.test_local_evidence_links_resolve_and_no_remote_assets_are_loaded
Result: 3 tests passed.

python -m json.tool config/retrieval/tenant-partition-contract.json
Result: passed.

.tools/bin/uv run --locked ruff format --check tests/test_retrieval_partition_contract.py
.tools/bin/uv run --locked ruff check tests/test_retrieval_partition_contract.py
Result: passed.

git diff --check -- config/retrieval/tenant-partition-contract.json docs/development/RETRIEVAL-PARTITIONS.md docs/approval/AMENDMENT-SKL-S2-10.md docs/security/THREAT-MODEL.md docs/tasks/SUBAGENT-TASK-TTDS.md docs/approval/index.html tests/test_retrieval_partition_contract.py tests/test_status_page.py
Result: passed.

rg -n '\\x{2013}|\\x{2014}' config/retrieval/tenant-partition-contract.json docs/development/RETRIEVAL-PARTITIONS.md docs/approval/AMENDMENT-SKL-S2-10.md docs/security/THREAT-MODEL.md docs/tasks/SUBAGENT-TASK-TTDS.md tests/test_retrieval_partition_contract.py tests/test_status_page.py
Result: no findings.

rg -n -i 'Build Qdrant and FalkorDB|primary.*Qdrant|primary.*FalkorDB' docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md docs/architecture/LIBERTY-AUTO-PILOT-TDD.md docs/planning/EPIC-SPRINT-PLAN.md docs/tasks/SUBAGENT-TASK-TTDS.md docs/approval/index.html
Result: no stale primary-backend wording.
```

The independent review did not authorize or perform deployment, data access,
database or container changes, HammerTime `Inbox/` processing, external action,
or board mutation.

## Current approved revision

The final current hashes are recorded here after validation and match
`docs/approval/DESIGN-HASHES.sha256`:

```text
033d09ffaf715a825fb64e15b7b69ba388ca6a0e6775aa82cc2465bec813910f  docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md
5c796030a888a7bebb197f55b2dde44d14711092856c752d03997907d05b85fd  docs/architecture/LIBERTY-AUTO-PILOT-TDD.md
a6c4e60759d10290fe4936072af4bf4a0ff850946dcf4bc4e70d0f90a2539ba4  docs/planning/EPIC-SPRINT-PLAN.md
7d7155e6cb23566e9cee3226de176a9e14999c5174e30fb24f75185dfc3de8cc  docs/tasks/SUBAGENT-TASK-TTDS.md
3155d2567280ca69b94040a4f5b57befac059b54fac8ac9167f703f23fd4f4a2  docs/approval/index.html
```

The current hashes above reflect the 2026-08-21 approved revision that also
applied the SKL-S2-09 provenance path correction and the model-naming
correction (`AMENDMENT-SKL-MODEL-NAMING.md`).

## Approval effect

- Replace the retrieval references in the five approved design documents and
  re-pin their current hashes while retaining the original hash history.
- Replace the abandoned S2-10 Qdrant and FalkorDB draft with the local
  PostgreSQL partition contract.
- Retitle and amend the folded S2-04 and S2-10 board contracts.
- Keep persistent image composition, database provisioning, deployment,
  backup, restore, and production activation under later eligible cards.
