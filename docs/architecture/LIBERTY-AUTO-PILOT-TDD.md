# Liberty Auto Plaza pilot migration technical design

Date: 2026-08-19  
Status: Approved, retrieval projection wording amended 2026-08-21
Source matter: `PRB-2026-009`  
Source event: `INC-016`  
Source path: `/mnt/cloud/onedrive/projects/DAVE-AI/hammerTime/incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order`

## 1. Purpose

Use one existing HammerTime record to prove a lossless, read-only migration pattern from legacy `Problem` and `Incident` storage into SKLegal's legal-domain model.

The pilot proves structure, provenance, access, retrieval, workflow, validation, and presentation. It does not approve, execute, sign, send, file, serve, or otherwise act on any matter document.

## 2. Source posture

The observed source contains:

- `PROBLEM.md`
- `EVIDENCE-LOG.md`
- `INCIDENT.md`
- `case-facts.json`
- packet fact versions v2, v3, and v4
- correspondence and owner directions
- phase review documents
- validation report
- referenced source and work-product artifacts

The source record currently reports the legacy matter open and the legacy incident in progress. The current SKMemory context identifies v4 as the review baseline and v3 as preserved historical material. It also records that human review is required before any execution or service and that no external execution event has begun.

Migration must not alter those facts or statuses.

## 3. Semantic mapping

Qwen3 reviewed the selected source records through HammerTime's approved local route and recommended a lossless projection rather than normalization.

| Legacy record | SKLegal target | Rule |
|---|---|---|
| `PRB-2026-009` | Matter | New UUID plus immutable legacy alias. |
| `INC-016` | Matter Event with subtype `transaction_review` | It is not automatically a Proceeding. |
| Retail and supplemental order records | Transaction and source Evidence Items | Preserve each source version and execution state. |
| Owner profile | Principal reference plus proposed Party Role | Membership and authorization require separate review. |
| Counterparty records | Party plus Party Role | Preserve source of identity assertion and verification status. |
| Evidence log entries | Evidence Items and Custody Events | Preserve legacy exhibit ID, hash, path, custodian, and source status. |
| Case facts and packet facts | Fact Assertions | Split into atomic, source-linked assertions. |
| Conflicting or variant facts | Tension Group | Do not merge or select a winner during import. |
| Authority metadata | Authority Record proposal | Preserve review status and source role. |
| Owner directions | Owner Instructions | Preserve order, scope, status, and supersession chain. |
| Printed or conditional dates | Deadline Candidate | Never promote to operative deadline without trigger and review. |
| Packet v2, v3, and v4 | Work Product Versions | Preserve historical versions. Mark v4 as current review baseline only. |
| Validation report | Validation Results | Preserve exact check result and scope. |
| Approval flags | Approval Records | Import negative or pending state exactly. |
| Execution flags | Execution Events | Import as not-started states, not completed events. |

## 4. Required tension preservation

The importer must preserve at least these tension classes:

- trust or party name variants across source records
- pre-contract or contingent status versus any attachment or completion description
- business-day versus calendar-day response periods
- printed dates versus established deadlines
- user-supplied identity facts versus externally verified identity facts
- packet-version changes and supersession

Each tension group contains the original assertions, source fields, observed values, review statuses, and a resolution state. Import never silently resolves the group.

## 5. Import data contract

### Legacy source reference

```json
{
  "source_system": "hammertime",
  "legacy_type": "incident",
  "legacy_id": "INC-016",
  "legacy_path": "incidents/problems/.../INCIDENT.md",
  "content_sha256": "sha256",
  "source_release_id": "release-or-unreleased-state",
  "adapter_version": "semver",
  "observed_at": "timestamp"
}
```

### Import record

```json
{
  "import_batch_id": "uuid",
  "tenant_id": "uuid",
  "target_type": "matter_event",
  "target_id": "uuid",
  "source_reference_id": "uuid",
  "mapping_rule": "incident.transaction_review@1",
  "mapping_status": "proposed",
  "reviewed_by": null,
  "reviewed_at": null
}
```

### Atomic fact assertion

```json
{
  "fact_assertion_id": "uuid",
  "matter_id": "uuid",
  "subject_ref": "uuid",
  "predicate": "controlled-predicate",
  "value": "exact source value",
  "value_type": "string",
  "source_reference_id": "uuid",
  "source_locator": "json-pointer-or-markdown-heading",
  "review_status": "verified|source_asserted|ambiguous|superseded",
  "tension_group_id": null,
  "valid_from": null,
  "valid_to": null,
  "observed_at": "timestamp"
}
```

## 6. Import ordering

1. Create import batch and pin source snapshot.
2. Create tenant fixture and approved client fixture.
3. Create Matter with legacy problem alias.
4. Create source artifact references and hashes.
5. Create Parties and proposed Party Roles.
6. Create Transaction.
7. Create Matter Event with legacy incident alias.
8. Create Evidence Items and Custody Events.
9. Create atomic Fact Assertions and Tension Groups.
10. Create Owner Instructions and Communications.
11. Create Authority Record proposals.
12. Create Deadline Candidates and Tasks.
13. Create Work Product versions in historical order.
14. Create Validation Results.
15. Create Approval and Execution states.
16. Reconcile counts, hashes, aliases, links, status, and unresolved gaps.
17. Generate a human migration review report.

No target record becomes operationally active until the migration review report is approved.

## 7. Idempotency

Primary import key:

```text
sha256(source_system + legacy_path + content_sha256 + adapter_version + target_type + mapping_rule)
```

Requirements:

- rerunning the same source snapshot creates no duplicates
- changed source content creates a new source version and a new proposed mapping revision
- no imported target silently overwrites a human-reviewed target
- import batches can be compared and superseded
- derived full-text, vector, or graph projection work uses the committed target version as its idempotency input

## 8. Read-only demo slice

### Phase A: inventory

- calculate hashes for the selected source metadata files
- record file counts and relative paths
- read JSON keys and Markdown frontmatter
- create a dry-run mapping report
- write nothing to HammerTime

### Phase B: structured import

- create one tenant fixture
- create one client fixture
- import Matter and Matter Event
- import source references, work-product versions, approval states, and record gaps
- do not import protected source text into an external model route

### Phase C: semantic records

- import proposed Parties, Party Roles, Transaction, Facts, Tension Groups, Evidence, and Authority metadata
- require human review for ambiguous mappings
- keep v4 as current review baseline and v3 as historical

### Phase D: workbench proof

- display Matter overview
- display legacy provenance
- display facts and unresolved tensions
- display evidence and source hashes
- display work-product version lineage
- display approval and execution state
- display validation results
- display the audit replay

### Phase E: governed agent proof

- authorize a read-only matter analysis task
- retrieve only allowed context from the pinned HammerTime snapshot
- have Qwen return one typed issue proposal
- validate source references
- require human acceptance or rejection
- store the proposal and decision without altering HammerTime

## 9. Verification plan

### Source preservation

- record pre-import hashes for every selected file
- record post-import hashes and prove equality
- prove no HammerTime file modification time changed due to import
- prove no source moved or archived

### Count reconciliation

- legacy files inventoried equals source references created or explicitly excluded
- legacy aliases are unique within their source scope
- every work-product version has a source reference
- every validation result points to the exact version it validated
- every approval state points to the exact artifact hash or records that no artifact was approved

### Semantic verification

- `PRB-2026-009` appears as Matter, not Problem
- `INC-016` appears as a transaction-review Matter Event, not a generic Incident
- variant party or trust names remain separate assertions in one tension group
- mixed timing rules remain separate assertions in one tension group
- contingent dates remain deadline candidates, not active deadlines
- negative execution flags remain negative
- record gaps remain open

### Security verification

- a user outside the tenant cannot see the matter
- a tenant user without matter membership cannot see the matter
- an agent without `matter.read` cannot retrieve context
- an agent with `matter.read` but without `evidence.read` cannot retrieve protected evidence
- OpenAI egress is denied for this matter unless a separate policy decision allows a redacted test context

### Replay verification

The migration report must identify:

- import batch
- source snapshot and hashes
- adapter and mapping-rule versions
- database migrations
- actor and capability
- records created, revised, skipped, or ambiguous
- all validator results
- human decision

## 10. Rollback

Rollback affects only the SKLegal import batch and derived projections.

- mark the import batch withdrawn
- invalidate imported operational activation
- remove or tombstone derived projections using the batch watermark
- preserve audit, mappings, and human decisions
- do not delete or modify HammerTime source material
- do not reuse withdrawn target UUIDs for a later import

Physical deletion of pilot database rows is permitted only in a disposable development database and must be covered by a test fixture reset command.

## 11. Pilot acceptance

The pilot passes only when:

- all selected source hashes remain unchanged
- import is idempotent
- legal terminology is used throughout the UI and API
- legacy provenance remains visible and queryable
- source tensions remain unresolved and visible
- tenant and matter isolation tests pass
- v4 is shown as the current review baseline without destroying v3 history
- no approval or execution state is advanced
- one Qwen proposal completes through CapAuth, source validation, and human review
- the complete run can be replayed from pinned evidence
