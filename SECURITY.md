# SKLegal security policy

## Scope

This policy applies to the entire SKLegal repository and to the designed SKLegal runtime: web and API surfaces, PostgreSQL records, Temporal workflows, CapAuth authorization, agent and model gateways, retrieval adapters, HammerTime integration, external-action connectors, audit and telemetry, backups, deployment configuration, and developer tooling.

SKLegal is designed to hold multi-tenant legal information, including personal data, client confidences, attorney-client privileged material, attorney work product, credentials, legal-hold data, and consequential action drafts. Security review must assume that compromise can harm clients, matters, legal rights, and evidentiary integrity.

## Security invariants

1. Every protected record and operation is scoped to one `tenant_id`; matter-scoped material is also scoped to one `matter_id`.
2. Authentication, tenant membership, matter membership, CapAuth capability, purpose, conflict status, valid waiver evidence, ethical-wall status, classification, protected need-to-know access, source rights, and destination policy are independent gates. A pass at one gate cannot override a denial or unknown result at another.
3. Policy-service errors, missing attributes, stale snapshots, expired or revoked capabilities, unresolved conflicts, and unknown rights fail closed.
4. Database row-level security is a required backstop. API filters, retrieval filters, cache keys, vector partitions, graph queries, model context, audit views, exports, backups, and connector jobs must preserve the same tenant and matter scope.
5. Profile ownership, legacy ownership, object identifiers, URLs, workflow identifiers, model output, document instructions, and connector responses never grant authority.
6. Raw CapAuth tokens, credentials, session secrets, and private keys are denied at every operation boundary. Services use validated secret-store references. Protected source text and unredacted prompts are excluded from browser storage, Temporal history, ordinary logs, external telemetry, and committed files.
7. Models produce typed proposals only. Deterministic code and authorized humans own state transitions, approvals, and external actions.
8. Prompt text, documents, retrieved passages, model output, and connector-returned content are data, not instructions. They cannot expand tool authority, alter policy, or choose credentials.
9. External actions require validation, exact-version approval, destination verification, a scoped capability, idempotency, immutable audit, and receipt reconciliation. Simulation is the default.
10. Unknown, unverified, expired, or revoked source rights deny ordinary source read and retrieval and quarantine content from corpus promotion, model training, redistribution, and derived publication. The only read exception is an explicitly authorized isolated quarantine review with its dedicated purpose.
11. Classification inherits the most restrictive applicable label from tenant, client, engagement, matter, source, record, field, legal hold, and approved policy override. Privileged/work-product and highly restricted content each require an independent need-to-know access grant. Declassification requires an attributable human decision.
12. Legal hold overrides deletion, retention expiry, archival automation, and user erasure requests until an authorized release is recorded. Deletion requires an explicitly known negative hold state; missing or unknown hold state denies deletion.
13. Original evidence, source hashes, provenance, version lineage, contradictions, and approval state remain tamper evident and replayable. Workflow replay cannot repeat a mutation or external dispatch.
14. Backup and restore preserve encryption, tenant scope, classification, retention, holds, provenance, and access controls. Restore is not an authorization bypass.
15. HammerTime remains the owner of initial corpus originals and releases. SKLegal uses approved adapters and cannot write arbitrary HammerTime paths or bypass ingestion finalization.

## Reportable security criteria

Report behavior that can plausibly cause any of the following:

- cross-tenant or unauthorized cross-matter access, inference, mutation, retrieval, cache disclosure, export, or restore
- bypass of CapAuth, conflict hold, waiver validation, ethical wall, privileged or highly restricted need-to-know access, classification, source-rights, legal-hold, or exact-version approval controls
- account takeover, session fixation, capability forgery, capability replay, excessive delegation, or credential disclosure
- unapproved protected-content egress to a model, connector, telemetry service, browser, backup target, or other external destination
- prompt injection, retrieval poisoning, model output, or connector content expanding authority or reaching a privileged sink
- unauthorized email, filing, service, mailing, calendar, client communication, corpus promotion, or other consequential action
- loss or corruption of evidence, provenance, source hashes, work-product lineage, audit integrity, approvals, receipts, or replay guarantees
- source-rights bypass that promotes, trains on, redistributes, or publishes quarantined content
- retention deletion during legal hold, or restore behavior that loses or bypasses retention and hold state
- secret exposure through source, build artifacts, logs, telemetry, prompts, workflow history, caches, or error messages
- resource exhaustion or dependency failure that bypasses safety controls, silently drops protected records, or prevents time-sensitive legal work without an explicit degraded state

A report must identify a reachable boundary, violated invariant, affected scope, required attacker control, and evidence. Design-only gaps are tracked as architecture requirements, not represented as vulnerabilities in code that does not yet exist.

## Exclusions and non-findings

The following are not reportable without a concrete security impact:

- style, terminology, documentation wording, or speculative best practices
- denial of an operation that is intentionally fail closed
- local development availability issues that cannot affect protected data, production credentials, or release integrity
- third-party license conclusions without evidence; licensing and component reuse decisions belong to `SKL-S0-05`
- weaknesses requiring trusted operator or developer control when that control is outside the stated attacker model, unless privilege separation is itself the claimed boundary
- HammerTime corpus content and Inbox behavior not inspected under the assigned task
- legal merits, legal advice quality, or substantive case conclusions except where a technical control misrepresents provenance, approval, authority status, or execution state

## Severity context

- Critical: scalable or direct compromise of tenant isolation, protected-content confidentiality, signing or dispatch authority, platform credentials, evidence integrity, or legal-hold records with severe legal impact.
- High: unauthorized access or action limited to one tenant or matter, protected model egress, durable authorization bypass, serious audit or provenance corruption, or practical account takeover.
- Medium: bounded disclosure or integrity loss requiring meaningful preconditions, recoverable workflow replay failure without external effect, or availability loss with a tested workaround and no safety bypass.
- Low: limited metadata exposure, defense-in-depth weakness, or local-only issue with no plausible protected-data, authority, evidentiary, or consequential-action impact.

Severity is reduced when required attacker control does not exist in the real deployment, a boundary is simulation-only, or a separate enforced control prevents the outcome. It is increased by cross-tenant reach, privileged or highly restricted data, external dispatch, persistence, low detectability, or inability to restore evidentiary truth.

## Security review and reporting

Security changes require denial-path tests and evidence linked to the assigned SKCapstone card. Do not include client material, secrets, raw capability tokens, or protected prompts in reports. Report suspected vulnerabilities privately to the repository owner and preserve only the minimum sanitized reproduction evidence.

Use GitHub private vulnerability reporting for `smilinTux/sklegal`. If that channel is
unavailable, contact the smilinTux/SKWorld maintainers privately through the GitHub
organization profile. Do not open a public issue. Expect acknowledgement within 72
hours. Coordinated disclosure is targeted within 90 days, subject to client safety,
legal obligations, and a tested correction being available.

Good-faith research that avoids client data, service disruption, persistence, social
engineering, and secret access is eligible for safe-harbor consideration. Stop and
report immediately if protected data or credentials are encountered.

## Supported versions and assurance

| Version | Security support |
|---|---|
| 0.1.x | Active development support |
| Earlier or unversioned snapshots | Unsupported |

SKLegal is experimental and unaudited. Its capability signature surface is T0
Classical. No post-quantum, hybrid KEM, hybrid signature, or independent assurance
claim is made. See [the cryptography inventory](docs/crypto-architecture.md).

## Current persistence limitations

`SKL-S1-02` supplies tenant and matter RLS, exact database-role binding,
least-privilege grants, typed validation and artifact gates, controlled execution,
and fail-closed audit insertion. It does not claim that RLS can contain a login
after a database administrator grants that login `BYPASSRLS`; startup role-drift
checks and administrative role governance are required deployment controls.

CapAuth enforcement, conflict and ethical-wall policy, retention and legal holds,
and the attributable chained audit and outbox writer are delivered by later cards.
Until the S1-05 writer exists, audit insertion remains intentionally unavailable.
Communication persistence currently stores governed metadata and exact artifact,
destination, approval, and execution bindings, but no ciphertext payload tuple.
