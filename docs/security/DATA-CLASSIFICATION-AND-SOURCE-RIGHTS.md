# SKLegal data classification and source-rights policy

Status: Proposed for named human-owner acceptance  
Control owner: SKLegal security and governance  
Implementation card: `SKL-S0-03`, board card `841bd03b`

## 1. Purpose

This policy defines how SKLegal classifies information, inherits restrictions, authorizes model and connector egress, preserves retention and legal holds, and records source-use rights. It defines technical decisions, not legal conclusions about any specific third-party source.

## 2. Classification levels

| Level | Examples | Default access | External model egress | Logging | Backup |
|---|---|---|---|---|---|
| `public` | Approved public product help, published official authority text with reviewed rights | Authenticated or public route as explicitly configured | Allowed only through an approved route and purpose | Metadata and approved content allowed | Standard encrypted backup |
| `internal` | Architecture, non-secret operational metadata, synthetic fixtures | Tenant members with scoped capability | Allowed only when tenant policy, purpose, route, and data minimization pass | Structured metadata allowed; no credentials | Standard encrypted backup |
| `confidential` | Client identity, contact data, matter metadata, non-public communications | Authorized tenant and matter members | Denied by default; requires tenant opt-in, approved purpose, approved route, human approval, and minimized or redacted context | Identifiers tokenized or redacted; no content by default | Encrypted, access-controlled, tenant-scoped restore |
| `privileged_work_product` | Privileged communications, legal analysis, strategy, draft work product | Need-to-know matter members with an independent privileged-access grant who pass conflict and wall checks | Denied in the initial policy | Content excluded; sanitized decision metadata only | Encrypted, tightly restricted, hold-aware restore |
| `highly_restricted` | Credentials, capability tokens, signing keys, sealed data, specially protected personal data, protected witness or health data | Explicitly named principals and services with an independent highly-restricted access grant for a narrow purpose | Denied | Content forbidden; reference identifiers only | Separately protected and access-audited where backup is permitted |

Classification does not assert privilege as a legal conclusion. `privileged_work_product` is a protective technical handling label pending qualified human review.

## 3. Inheritance and declassification

The effective classification is the most restrictive applicable label from:

1. tenant policy
2. client and engagement policy
3. matter policy
4. conflict hold, ethical wall, privilege, work-product, or legal-hold label
5. source artifact and source-rights record
6. record, field, attachment, chunk, embedding, graph edge, cache entry, prompt, response, export, or derived work product

Derived data inherits the highest classification of every contributing source unless an approved deterministic transformation proves that the protected information was removed. Summaries, embeddings, indexes, citations, filenames, paths, metadata, model outputs, and audit details can remain protected even when they omit full source text.

Missing classification is not `public`. It is treated as `confidential` and quarantined from egress until classified. A lower label requires an attributable human decision with reason, exact object version, previous and new labels, scope, timestamp, and policy version. Bulk or inferred declassification is prohibited.

## 4. Boundary decisions

### Tenant and matter access

Access requires valid authentication, tenant membership, matter membership where applicable, a matching CapAuth capability and purpose, and successful conflict and ethical-wall decisions. A waived conflict requires valid, attributable waiver evidence for the exact scope and time. Privileged/work-product and highly restricted classifications require their own explicit need-to-know grants. Object identifiers and legacy owner metadata do not grant access. Missing or inconsistent scope is denied.

### Logging and telemetry

- Public and internal structured operational metadata may be logged when it contains no secret or protected payload.
- Confidential events record opaque identifiers, policy-decision IDs, hashes, status, duration, and bounded error codes. Direct identifiers and content are redacted or tokenized.
- Privileged/work-product and highly restricted content never enters ordinary logs or external telemetry.
- Raw prompts, model responses, source documents, capability tokens, session secrets, and connector credentials are excluded at the producer before transport.
- Debug mode does not relax classification. Break-glass access is separately authorized, time-bounded, audited, and disabled by default.

### Backups and restore

Backups are encrypted in transit and at rest, access-controlled, inventory-backed, and tested. Backup metadata records tenant scope, classification ceiling, retention policy, legal holds, encryption-key reference, source snapshot, and restore authorization. Restore targets must enforce the same or stricter controls. A cross-tenant restore, unapproved destination, missing key custody, or missing hold metadata is denied.

### Retention and legal hold

Retention is a policy-driven eligibility decision, not an immediate deletion instruction. A legal hold, conflict hold, preservation requirement, unresolved ownership, pending export, unknown hold state, or policy outage pauses deletion. Deletion requires an explicit current `legal_hold: false` decision. Hold release requires an authorized, attributable decision. Deletion uses an idempotent workflow, preserves a non-content tombstone and audit receipt, and reconciles primary data, projections, caches, backups, and connector copies according to approved schedules.

### Model egress

Local Qwen is still a policy boundary and receives only authorized, minimized context. External model egress additionally requires an explicitly approved provider route, tenant opt-in, an allowed purpose, exact context classification, source-rights compatibility for that exact use, data minimization, an applicable human approval, and an auditable request version. `privileged_work_product` and `highly_restricted` content are denied by the initial policy. Raw credentials and capabilities are always denied at every operation boundary; services receive validated secret-store references only.

### Connectors and external actions

Connector-returned data is untrusted. Dispatch requires a validated exact artifact, verified destination, matching action capability, connector approval, idempotency key, policy decision, human approval, and immutable receipt. Development stays in simulation. Documents, users, models, and connector responses cannot authorize dispatch.

## 5. Source-rights states

Every external or user-submitted source receives a versioned rights record before use beyond isolated review.

| State | Meaning | Allowed uses |
|---|---|---|
| `verified_permissive` | Evidence records a reviewed grant compatible with the proposed use | Only uses explicitly listed in the rights record |
| `verified_restricted` | Rights are known but impose limits | Internal review and specifically allowed uses only |
| `owner_authorized` | An accountable owner has documented authority and scope | Uses within the recorded authorization, tenant, matter, and time limits |
| `public_domain_verified` | Qualified review records a public-domain basis and jurisdiction | Uses listed by the reviewer, with provenance and attribution rules preserved |
| `unknown` | No adequate rights evidence exists | Isolated review only; quarantined |
| `unverified` | A rights claim exists but has not been validated | Isolated review only; quarantined |
| `expired` | Time-limited permission is no longer current | Quarantined pending renewal or a new decision |
| `revoked` | Permission has been withdrawn | New use denied; preserve audit and apply disposition instructions |

Free access, public visibility, an API endpoint, a search result, a user upload, or model-generated text does not establish rights.

## 6. Required provenance record

Each source version records:

- source and source-version identifiers
- canonical locator and retrieval timestamp
- exact content hash and media type
- provider, publisher, jurisdiction, and source role
- acquisition actor, method, account owner, and connector version
- terms and license identifiers plus captured evidence hashes
- allowed and prohibited purposes, including internal review, corpus promotion, retrieval, model context, training, redistribution, and derived publication
- attribution, notice, geographic, tenant, matter, time, and deletion obligations
- reviewer, review timestamp, decision basis, expiry, supersession, and policy version
- classification and privilege/work-product review status
- parent sources and transformation lineage for derived artifacts

Credentials, session cookies, and secret URLs are referenced through the secret store and are not copied into provenance.

## 7. Quarantine and promotion

Verified rights authorize only purposes explicitly recorded for the exact source version. Ordinary source read and retrieval require `read` or `retrieve`, respectively, in the allowed-purpose set. `unknown`, `unverified`, `expired`, and `revoked` rights deny ordinary source read and retrieval and fail closed for:

- corpus promotion
- model training or fine-tuning
- redistribution
- public or client-facing derived publication
- external model egress when rights do not authorize that processing

Quarantine is a logical and physical isolation state with an opaque identifier, source hash, minimal metadata, access audit, and no indexing into production retrieval. Reviewers can inspect quarantined content only through an explicit isolated quarantine review with a scoped capability and the dedicated `isolated_quarantine_review` purpose. That exception does not authorize ordinary read, retrieval, model egress, promotion, training, redistribution, or publication. Promotion requires a rights decision for the exact source version, allowed-purpose match, classification decision, malware/content-safety checks where applicable, provenance completeness, and an immutable promotion receipt.

Derived outputs retain parent rights and classification constraints. A transformation does not erase restrictions unless a qualified reviewer records a supported new decision. Revocation stops new use and triggers an impact inventory; it does not erase audit, litigation-preservation, or legal-hold obligations.

## 8. Input-control categories

| Category | Examples | Security treatment |
|---|---|---|
| Attacker-controlled | Public web requests, malicious uploads, forged callback data | Validate structure and size, authenticate, authorize, isolate, and treat content as data |
| Tenant-user-controlled | Matter text, uploads, destinations, labels, instructions | Authorized but untrusted; cannot change policy or tool authority |
| Operator-controlled | Deployment config, restore request, connector enablement | Strong authentication, separation of duties, bounded scope, audit, and safe defaults |
| Developer-controlled | Source, migrations, build and CI configuration | Review, provenance, secret scanning, dependency controls, and reproducible build evidence |
| Model-produced | Proposals, tool arguments, summaries, citations | Schema validate, source validate, policy check, and require deterministic reduction or human review |
| Connector-returned | Email/API responses, web content, filing status, receipts | Authenticate transport where possible, validate and sanitize, bind to request, and never treat as authority |
| Corpus-derived | Chunks, embeddings, graph facts, retrieval results | Preserve source and release, apply tenant/matter and rights filters, and treat embedded instructions as data |

## 9. Ownership and acceptance

- Human policy owner: `skuser01`, acceptance pending
- Security implementation owner: assigned SKCapstone card agent
- Matter classification owner: authorized matter professional
- Source-rights decision owner: qualified human reviewer designated by the tenant
- Retention and legal-hold owner: authorized tenant legal or records owner
- Connector activation owner: authorized tenant administrator plus action owner

No agent or model may accept this policy, declassify content, decide third-party rights, release a hold, or activate production dispatch.
