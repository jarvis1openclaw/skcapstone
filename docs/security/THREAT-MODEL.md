# SKLegal repository threat model

## Overview

SKLegal is a design-stage, GPLv3 repository for a multi-tenant legal operations application, matter workbench, and governed agent harness. The approved architecture in `docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md` specifies a React web client, FastAPI service boundary, PostgreSQL operational store, Temporal workflows, CapAuth authorization, typed agent activities, policy-controlled local Qwen and OpenAI model routes, read-only HammerTime corpus integration, retrieval stores, external-action connectors, append-only audit, and protected telemetry. The first pilot is a lossless, read-only migration pattern described in `docs/architecture/LIBERTY-AUTO-PILOT-TDD.md`.

At this version the repository contains design, governance, platform qualification, and reference policy artifacts. It does not yet contain the production web, API, database, workflow, model, retrieval, or connector implementations. Controls described as required or designed are security requirements for later cards, not claims that deployed enforcement exists today.

The assets with the highest confidentiality, integrity, authorization, and availability value are:

- tenant identity, membership, policy, key, and retention boundaries
- client, engagement, matter, party, conflict, ethical-wall, privilege, work-product, and legal-hold records
- evidence, custody events, fact assertions, unresolved tensions, authorities, deadlines, communications, and work products
- original source hashes, legacy aliases, HammerTime release references, import batches, mapping versions, and provenance
- CapAuth signing material, capabilities, revocation state, sessions, service identities, model credentials, and connector credentials
- workflow state, idempotency keys, approvals for exact artifact versions, dispatch state, receipts, and replay evidence
- audit events, policy-decision records, telemetry correlation, backup manifests, encryption-key references, and restore authorization
- availability needed to protect time-sensitive deadlines, preservation duties, human review, and safe degraded behavior

The primary security objectives are to prevent cross-tenant and unauthorized cross-matter access; preserve conflict, valid-waiver, privilege, independent protected-access, ethical-wall, retention, and hold controls before retrieval; ensure that models and untrusted content cannot gain authority; quarantine sources with unknown rights; prevent unapproved protected-content egress and external action; preserve evidence and audit integrity; make replay idempotent; and fail closed without losing an explicit, observable state.

Repository security guidance is authoritative in `SECURITY.md`. Data handling and source rights are defined in `docs/security/DATA-CLASSIFICATION-AND-SOURCE-RIGHTS.md`. `config/security/policy.json` and `scripts/evaluate_security_policy.py` are a narrow executable specification for Sprint 0 misuse decisions. They are not the future production policy service.

## Threat Model, Trust Boundaries, and Assumptions

### Actors and capabilities

- An unauthenticated external attacker can send requests to exposed web or API routes, submit malformed identifiers and payloads to public intake surfaces, influence public source content, and attempt credential theft or resource exhaustion.
- An authenticated tenant user can control matter text, uploads, search terms, destinations, labels, and ordinary workflow requests within granted access. The user can be malicious, compromised, mistaken, or assigned to conflicting work.
- A user with one matter can know or guess identifiers for another matter in the same tenant or a different tenant. Object knowledge is not access.
- A malicious or compromised tenant administrator can exercise broad tenant capabilities but cannot cross another tenant, bypass exact-version approval, gain platform secrets, or silently override immutable audit.
- An operator controls deployment configuration, service activation, backup and restore initiation, network routing, and emergency procedures. Operator actions remain attributable and bounded by separation of duties where the action affects protected content or external dispatch.
- A developer controls repository source, tests, migrations, build inputs, and dependency updates. Developer access does not imply production matter access or secret access.
- A compromised dependency or build input can attempt code execution, credential theft, artifact tampering, or policy weakening during development and deployment.
- Models are fallible proposal generators, not trusted principals. Model output can be incorrect, adversarially influenced, malformed, or crafted to invoke tools.
- Connector services and external providers can return malicious content, stale status, forged callbacks, ambiguous receipts, or unexpected redirects. Their responses do not grant authority.
- HammerTime releases provide source and provenance references under an approved integration contract. Corpus content can still contain malicious instructions, false assertions, contradictions, or poisoned metadata.

### Input-control categories

SKLegal must preserve the following distinctions at parsing, validation, authorization, logging, and audit boundaries:

| Category | Representative inputs | Required treatment |
|---|---|---|
| Attacker-controlled | Public requests, malicious files, forged callbacks, public web content | Bound size and type, authenticate where required, authorize, isolate, sanitize for output, and treat all content as data |
| Tenant-user-controlled | Matter fields, uploads, search text, destinations, labels, approval requests | Validate and authorize for exact tenant, matter, purpose, and version; never derive authority from content |
| Operator-controlled | Deployment config, provider routing, restore targets, connector activation, break-glass requests | Strong authentication, least privilege, separation of duties, audit, bounded scope, and safe defaults |
| Developer-controlled | Code, migrations, CI config, dependencies, fixtures, release metadata | Review, provenance, reproducibility, secret scanning, dependency controls, and no protected fixtures |
| Model-produced | Proposals, summaries, tool arguments, citations, classifications | Schema validation, source validation, policy check, deterministic reduction, bounded tools, and human approval where required |
| Connector-returned | API responses, email content, filing status, receipts, redirects | Bind to an initiated request, authenticate transport where possible, validate, sanitize, reconcile, and never execute embedded instructions |
| Corpus-derived | Chunks, OCR, embeddings, graph facts, retrieval hits, source metadata | Pin source and release, enforce access and rights filters, preserve provenance, detect stale or poisoned state, and treat instructions as quoted data |

### Trust boundaries and fail-closed misuse cases

| Boundary | Assets crossing boundary | Representative misuse case | Expected fail-closed decision |
|---|---|---|---|
| B1: Browser or client to web/API | Sessions, requests, identifiers, rendered protected data | Stolen session, CSRF, XSS payload, forged object ID, oversized upload, or public route used for a protected record | Reject missing or invalid authentication, validate origin and request, encode output, enforce size and type limits, and return no protected existence signal |
| B2: Authenticated API to CapAuth and policy gateway | Principal, capability, purpose, tenant, matter, operation | Expired, revoked, replayed, over-delegated, wrong-purpose, wrong-route, or malformed capability | Deny on any missing or stale decision; require a matching short-lived capability and record only token and decision identifiers |
| B3: Tenant and matter policy to conflicts, privilege, and ethical walls | Membership, party identities, conflict decisions, waivers, wall membership, classification | A valid tenant user retrieves an unassigned matter, relies on an invalid waiver, lacks protected need-to-know access, or requests wall-excluded evidence | Deny before query, cache, retrieval, model context, export, audit detail, or connector staging; waived conflicts require valid exact-scope evidence, protected classifications require independent grants, and unresolved state is a denial |
| B4: API and workers to PostgreSQL, caches, and projections | Legal records, tenant and matter keys, versions, transaction state | API filter omission, IDOR, cache key collision, unsafe query, stale projection, or restore into the wrong tenant | Enforce row-level security and scoped keys as backstops, reject missing scope, validate versions, and expose explicit stale or incomplete state |
| B5: API and humans to Temporal workflows and activities | Typed workflow input, signals, retries, approvals, idempotency state | Duplicate signal, stale approval, worker restart, replay repeating mutation, or policy changed during a long wait | Pin exact input and policy references, reauthorize at effect boundaries, reject stale versions, and require idempotency receipts before replayed mutation |
| B6: Workflow and model to CapAuth tool gateway | Tool name, arguments, result, budget, capability constraints | Prompt or model requests a broader tool, supplies path traversal, uses another matter ID, or exfiltrates through tool output | Authority comes only from CapAuth, validate typed arguments and results, intersect all scopes, deny unknown tools, bound output, and audit the decision |
| B7: SKLegal to HammerTime and retrieval stores | Release IDs, source hashes, chunks, embeddings, graph results, provenance | Frontend constructs a path, unpromoted content is retrieved, poisoned chunk changes instructions, or vector/graph result crosses a partition | Use approved adapters, pin promoted release and alias, verify source hash and location, filter tenant/matter/rights, treat content as data, and return incomplete state on mismatch |
| B8: Model gateway to local Qwen or external model provider | Minimized prompts, evidence bundles, typed outputs, provider metadata | Protected content is routed externally, an unapproved provider is selected, credentials enter a prompt, provider response injects tools, or model invents authority | Deny disallowed classification or rights, require an explicitly approved provider route plus tenant and purpose approval, reject raw secret or capability material, constrain schemas and tools, and retain proposal status until validation and review |
| B9: Connector workers to external action providers | Exact artifact, destination, credentials, dispatch request, provider receipt | Malicious document triggers email, model chooses destination, callback forges success, or retry sends twice | Default to simulation; require connector and human approval, exact version, verified destination, scoped capability, idempotency, and receipt reconciliation |
| B10: Services to audit, logs, and telemetry | Event metadata, decision IDs, errors, traces, actor and run correlation | Protected prompt or token is logged, audit viewer crosses tenants, or attacker edits event history | Redact at producer, prohibit secrets and content, scope audit reads, make records append-only and tamper evident, and fail closed when redaction is uncertain |
| B11: Live services to backup, archive, and restore | Encrypted snapshots, manifests, keys, tenant scope, retention and hold metadata | Backup copied to an unapproved target, operator restores across tenants, missing hold state is treated as permission to delete, or restore loses walls | Require encryption, approved destination, key custody, manifest validation, scoped restore authorization, preserved classification and walls, and an explicit negative hold state before deletion |
| B12: Developer and CI to build, deployment, and secret stores | Source, dependencies, artifacts, migrations, configuration, credentials | Dependency compromise, secret committed, untrusted pull request gains credentials, migration weakens isolation, or artifact is replaced | Use protected CI contexts, reproducible evidence, review, SBOM and scanning gates, migration tests, signed or hashed artifacts, and no protected data in fixtures |
| B13: SKLegal to infrastructure and dependencies | CPU, memory, disk, queues, databases, model and retrieval availability | Flood exhausts workers, dependency outage causes an authorization bypass, disk pressure loses audit, or deadline processing silently stalls | Apply quotas and admission control, reserve critical capacity, use bounded retries and circuit breakers, never bypass policy, preserve durable state, and surface explicit degraded alerts |

### Core assumptions

- TLS, reverse-proxy hardening, session security, CapAuth key management, database row-level security, Temporal persistence, secret storage, and backup encryption will be implemented and independently tested by later cards.
- The local Qwen route reduces external egress but is not implicitly authorized. It still receives only policy-approved, tenant- and matter-scoped context.
- OpenAI and all other external providers are optional routes. No protected content is sent until tenant configuration, classification, purpose, rights, route, minimization, and required human approval pass.
- HammerTime remains the canonical owner of initial corpus originals and release artifacts. SKLegal does not inspect or modify unapproved Inbox content and does not treat arbitrary filesystem visibility as authorization.
- Qdrant, FalkorDB, and embedding outputs are derived indexes. They are not sources of legal authority and cannot replace exact source verification.
- Model and connector output is untrusted even when transport and service identity are valid.
- Platform root administrators can ultimately access host storage. Prevention of a malicious, unconstrained root administrator is outside the application authorization boundary, but operator attribution, encrypted backups, separation of duties, and minimization remain in scope.
- Physical host compromise, hypervisor compromise, and cryptographic algorithm breaks are not repository-level attacker stories unless SKLegal code weakens a documented deployment control.
- Substantive legal correctness is not guaranteed by the security layer. False provenance, unauthorized disclosure, unauthorized execution, or misrepresented approval remains a security concern.

## Attack Surface, Mitigations, and Attacker Stories

### Identity, session, account takeover, and authorization

The web/API boundary will handle credentials, sessions, tenant selection, matter identifiers, and mutations. Relevant vulnerability classes include account enumeration, credential stuffing, session theft, CSRF, XSS, insecure direct object reference, missing authorization, capability replay, excessive delegation, and confused-deputy behavior.

Required mitigations include secure session cookies, MFA support for consequential roles, rate limits, generic authentication failures, reauthentication for sensitive administration, short-lived CapAuth capabilities, revocation, audience and purpose binding, exact tenant/matter/resource constraints, validated waiver evidence, independent protected-access grants, and authorization at API, database, retrieval, model, audit, and connector layers.

Realistic attacker story: a compromised tenant user changes a matter ID in an API request or retrieves a cached result created for another user. The request must be denied before data access, with row-level security and tenant/matter cache keys providing independent backstops. Another realistic story is a stolen capability replayed after revocation or for a different workflow. Verification must reject expiry, revocation, audience, purpose, scope, and replay mismatches.

### Tenant isolation, conflicts, privilege, and ethical walls

Tenant isolation is the primary security boundary. Matter membership is narrower than tenancy. Conflict holds, privilege/work-product labels, and ethical walls can further restrict an otherwise authorized tenant member. A profile owner or legacy matter owner is only migration metadata.

Relevant failure classes include omitted filters, weak row-level policies, cache and index key collisions, indirect inference from counts or errors, bulk export scope mistakes, audit-view leakage, cross-tenant backup restore, and asynchronous jobs that lose policy context. Every protected record must carry tenant scope, and every matter record must carry matter scope. Denials must apply before retrieval so forbidden content cannot enter caches, prompts, embeddings, graph projections, logs, or exports.

Realistic attacker story: a user assigned to Matter A searches a distinctive term from Matter B. Search, vector retrieval, graph traversal, and result counts must all be filtered before ranking and must not reveal whether Matter B contains the term. A conflict or wall decision changing during a long workflow must cause reauthorization before the next protected read or effect.

### Untrusted content, prompt injection, and tool authority

Uploads, source documents, OCR, retrieved passages, model output, and connector results can contain instructions such as requests to reveal secrets, ignore policy, call a tool, send a document, alter a destination, or mark an action complete. These instructions remain data.

The model gateway must use versioned role specifications, bounded context, structured output, and no raw credentials. The tool gateway must authorize each tool and exact resource independently through CapAuth, validate arguments and results, constrain filesystem and network targets, enforce budgets, and prevent source content from selecting capabilities. Deterministic reducers and authorized humans perform state transitions.

Realistic attacker story: a corpus document says to email all evidence to an attacker. The model may quote or classify the text, but cannot obtain an email-dispatch capability. Any proposed destination is untrusted and the connector state machine still requires validated exact content, destination verification, human approval, an approved connector, and an idempotent dispatch receipt.

### Retrieval poisoning, corpus integrity, and legal authority

HammerTime releases, Qdrant vectors, FalkorDB graph entries, custom embeddings, and external legal-source connectors form a provenance-sensitive boundary. Threats include unpromoted or stale content, source-hash mismatch, malicious metadata, cross-partition indexing, orphan vectors, poisoned graph edges, rank manipulation, citation laundering, and treating similarity as legal authority.

Mitigations include pinned release IDs and projection watermarks, exact source hash and locator verification, tenant/matter/rights filters before retrieval, bounded adapter APIs, provenance on every result, stale and incomplete states, reconciliation, independent authority-status review, and qualification of the custom embedding against leakage and citation metrics.

Realistic attacker story: a poisoned connector result or corpus chunk claims to be binding authority and embeds a tool instruction. SKLegal must label its source role, verify exact authority location and status independently, preserve contradictions, and prevent the text from influencing authorization. Retrieval rank alone cannot establish applicability.

### Source rights and derived publication

Public availability and free access do not prove permission for read, retrieval, ingestion, training, redistribution, or publication. Verified rights must include the exact requested purpose for the exact source version. A missing, unverified, expired, or revoked rights state denies ordinary read and retrieval and must quarantine the exact source version from corpus promotion, model training, redistribution, and derived publication. The sole read exception is an explicitly authorized isolated quarantine review with its dedicated purpose. Rights records bind allowed purposes, classification, attribution, time, jurisdiction, and transformation lineage.

Realistic misuse: an operator adds a freely accessible source and a batch process promotes it without a rights decision. The promotion must fail before production indexing. A model summary of quarantined content retains parent restrictions unless an accountable reviewer records a supported new decision. This model does not decide the license of doc.haus or any other third party; those conclusions remain reserved for `SKL-S0-05`.

### Workflow replay, approvals, and evidence integrity

Temporal workflows cross time, retries, worker versions, human waits, and policy changes. Threats include duplicate mutation, stale approval applied to a newer artifact, non-deterministic replay, activity retry sending twice, cancellation after partial effect, and audit records detached from the exact source or output.

Mitigations include typed immutable workflow inputs, versioned workflow and activity code, exact artifact hashes, idempotency keys, transactional outbox, durable approval signals bound to versions, policy reauthorization at effect boundaries, compensation, receipts, and replay tests. Models and workflow history do not store raw capabilities or secrets.

Realistic attacker story: an approved email draft changes before dispatch or a worker restarts after the provider accepted a message. The exact-version check must reject changed content, and reconciliation must use the idempotency key and provider receipt rather than issue another blind dispatch.

### Connectors and consequential external actions

Email, filing, service, mailing, calendar, and client communication can cause legal or practical consequences. The connector boundary includes destination confusion, credential misuse, malicious callbacks, SSRF, unsafe redirects, attachment substitution, duplicate sends, forged receipts, and provider-account takeover.

Production dispatch remains separately activated per connector. It requires `draft -> validated -> approved -> queued -> dispatched -> receipt_verified`, with exact artifact hash, destination, sender, capability, policy decision, approver, connector, provider response, and reconciliation state. Simulation must be the development default. Egress allowlists, rate limits, timeout classes, request signing where supported, callback authentication, and constrained network destinations are required.

Realistic attacker story: a connector response contains a redirect to an internal metadata service or reports success for another tenant's request. The worker must constrain destinations, bind callbacks and receipts to the initiated tenant/matter/action/idempotency tuple, and leave the action unresolved on ambiguity.

### Audit, telemetry, logs, and error handling

Audit is both a high-value evidence asset and a possible side channel. Threats include raw prompt or document logging, capability leakage, cross-tenant audit search, mutable history, excessive error details, trace export to an external collector, unbounded logs, and missing correlation after partial failure.

Required controls are producer-side redaction, field allowlists, opaque object and policy IDs, local protected-content filtering, tenant/matter authorization for audit reads, append-only events, tamper evidence, retention and hold-aware storage, bounded errors, access monitoring, and durable correlation without payload capture.

Realistic misuse: an operator enables debug logging during an outage and captures privileged matter text or bearer tokens. Debug mode must not disable redaction, and secrets must be rejected before the logging transport. Break-glass access requires separate authorization, duration, reason, and audit.

### Backup, retention, legal hold, and deletion

Backups can concentrate data across tenants and outlive operational records. Threats include unencrypted archives, broad restore permissions, lost key custody, restore into the wrong tenant, missing wall or hold metadata, deletion during legal hold, orphan projections, and disposal without reconciliation.

Mitigations include encrypted and inventoried backups, approved destinations, separate key references, tenant and classification manifests, scoped restore authorization, tested recovery targets, immutable hold metadata, idempotent deletion, non-content tombstones, and reconciliation across primary data, caches, derived indexes, backups, and connector copies.

Realistic attacker story: retention expires while a legal hold is active. Deletion must be denied regardless of user request or ordinary retention policy. Deletion requires an explicit current negative hold state; missing, unknown, or unavailable state remains paused. Restore cannot lower classification or remove a wall.

### Secrets, build, deployment, and developer tooling

Secrets can enter source, `.env` files, CI logs, container layers, browser bundles, prompts, test fixtures, Temporal history, connector jobs, backups, exports, or ordinary data operations. Raw secret, credential, or capability material is denied at every operation boundary. Services carry validated secret-store references rather than raw credential content. Build and dependency threats include typosquatting, malicious install scripts, compromised registries, mutable image tags, unsigned artifacts, and migration code that weakens authorization.

Required controls include secret-store references, least-privilege service identities, protected CI contexts, no production secrets for untrusted changes, lockfiles, SBOMs, vulnerability and license review, pinned images, reproducible build evidence, migration tests, artifact hashes, and rollback plans. Synthetic fixture data must not resemble or derive from protected matters.

A developer with unrestricted production host and database access is outside application-level prevention, but accidental secret leakage, unsafe defaults, privilege concentration, and unaudited release paths remain relevant hardening concerns.

### Availability and safe degradation

The system depends on PostgreSQL, Temporal, CapAuth, HammerTime adapters, Qdrant, FalkorDB, model providers, connectors, storage, and telemetry. Attackers or failures can exhaust uploads, workflow queues, model slots, database connections, disk, logs, retrieval fanout, or connector retries. Deadline and preservation workflows make silent failure dangerous.

Mitigations include request and upload limits, admission control, per-tenant quotas, bounded retrieval, four-slot Qwen admission control, queue separation, rate limits, backpressure, timeouts, circuit breakers, disk and lag alarms, durable state, backup and restore tests, and visible degraded status. Dependency outages cannot relax authorization, source rights, human approval, or exact-version checks. Operations remain denied or pending rather than silently skipped.

### Less applicable and out-of-scope stories

- A model producing poor legal analysis without causing authorization, provenance, confidentiality, integrity, or execution failure is a quality issue handled by legal assurance and human review, not by itself a security vulnerability.
- A third-party source having ambiguous license terms is a governance state that must quarantine use. The security failure would be bypassing quarantine, not the ambiguity itself.
- Local denial of a deliberately simulation-only connector has low impact until a production connector is approved and exposed.
- A vulnerability requiring arbitrary root access to chiap01 may not represent a new application boundary unless SKLegal increases impact, exposes credentials, defeats encryption, or violates a documented separation control.
- Physical theft, firmware compromise, hypervisor compromise, and cryptographic breaks are deployment and platform risks outside this repository review unless repository configuration materially creates the exposure.
- The HammerTime protected corpus and Inbox are outside this task's inspected scope. Future adapter and ingestion tasks must test their contract without treating filesystem access as authorization.

## Severity Calibration

### Critical

Critical impact includes a practical path to cross-tenant compromise at scale, theft of platform signing or connector credentials enabling broad impersonation, unauthorized filing or service with severe consequence, deletion or corruption of held evidence without recoverability, or a policy/tool bypass that lets untrusted content execute privileged actions across tenants.

Examples include a row-level authorization design that permits any tenant user to enumerate all tenants' privileged work product; a prompt injection that can obtain unrestricted connector credentials and dispatch externally without review; or a restore workflow that overwrites evidence and legal-hold history across the platform.

### High

High impact includes protected disclosure or unauthorized action bounded to one tenant or matter, practical account takeover of a consequential role, durable bypass of conflict or ethical-wall controls, unapproved external-model egress of confidential material, serious evidence or audit integrity loss, or corpus promotion and publication despite an explicit quarantine.

Examples include an IDOR exposing privileged matter documents within one tenant; a stale exact-version approval dispatching a materially changed filing; a retrieval cache leaking one walled matter; or a forged connector receipt marking an external action complete and preventing reconciliation.

### Medium

Medium impact includes bounded metadata disclosure, integrity or replay failure requiring significant preconditions, temporary availability loss with explicit safe degradation, or a defense-in-depth failure where an independent control blocks protected access or external effect.

Examples include cross-matter record counts visible without content when no sensitive inference is possible; a duplicate internal task that is detected and causes no dispatch; overly detailed operational errors restricted to an authenticated tenant administrator; or a model route outage that leaves work visibly pending and preserves deadlines through fallback procedures.

### Low

Low impact includes limited non-sensitive metadata exposure, local development-only weaknesses, hardening opportunities without a plausible protected sink, or availability issues with no safety, deadline, evidence, or authorization consequence.

Examples include a public product-help response missing a non-security header when no session is involved; a verbose local fixture error containing only synthetic identifiers; or a simulation-only connector status mismatch that cannot contact an external provider and is immediately visible.

Severity is raised by cross-tenant scope, privileged/work-product or highly restricted data, stealth, persistence, low required privileges, external dispatch, evidence destruction, legal-hold impact, or weak recovery. Severity is lowered when the attacker lacks real control of the required input, the affected surface is not deployed, a separate enforced control prevents the outcome, only synthetic data is involved, or the effect is limited to an explicitly non-production simulation.

Repository: codex-security-target/v1:sha256:c6e40eccc8c3e51964dec69a9ce8425dfa829504a16303bfab6e8b27594dc626
Version: codex-security-snapshot/v1:sha256:18c07025c254a46adfec1bcfa405e594052417e927b1602971c0419fca8e901a
