# HammerTime framework and ecosystem evaluation

Date: 2026-08-19  
Scope: Existing `../hammerTime`, live Qwen route, local doc.haus clone, shared concept, and referenced third-party projects

## Bottom line

HammerTime is not an early prototype. It is a large, mature corpus-processing framework with strong provenance, OCR and transcript quality controls, release aliases, rollback, custom retrieval, Qwen routing, and file-backed case workflows. Rebuilding those capabilities inside hammerTimeOS would be wasteful and risky.

It is not yet a legal application platform. Its material gaps are typed matter semantics, authorization, conflicts, privilege, ethical walls, operational database state, durable human workflows, claim and authority applicability gates, dependency packaging, bounded observability, and end-to-end evaluations.

The recommended architecture is therefore an envelope and adapter strategy, not a rewrite.

## Evaluation method

The review covered:

- active repository agent instructions and architecture documents
- ingestion, corpus release, profile, incident, and case-workbench workflows
- Qwen routing, verification, and production qualification
- custom embedding metadata and evaluation output
- repository scale, scripts, and tests
- live read-only health checks against Qwen on chiap08
- read-only execution of current case and corpus status tools
- local doc.haus source and security documentation
- official project documentation or repositories for referenced third-party components
- a targeted architecture critique through HammerTime's approved Qwen3.8 route

No HammerTime source or corpus files were modified. Its working tree already contains extensive unrelated changes and deletions, so this evaluation kept all new artifacts in hammerTimeOS.

## Repository snapshot

The snapshot observed during this review included approximately:

| Area | Observed scale |
|---|---:|
| Files under `reference/` | 129,468 |
| Files under `json/decomposed/` | 79,308 |
| Files under `knowledge/` | 427 |
| Files under `summaries/` | 463 |
| Files under `templates/` | 235 |
| Files under `profiles/` | 68 |
| Files under `incidents/` | 704 |
| Python lines under `scripts/` | about 60,747 |
| Pytest-style test functions | about 347 |

Large central scripts include a roughly 3,600-line context bridge and substantial Qwen, corpus release, store rebuild, facts, and secondary-review modules. This is enough production logic to justify a formal package boundary, dependency lock, type checking, and executable test workflow.

## What HammerTime already does well

### Corpus custody and provenance

The intake contract is unusually strong. It preserves original paths, folder chains, owners, hashes, extraction methods, and completion evidence. It distinguishes intake, processed archive, normalized artifacts, decompositions, release manifests, and derived stores. That should remain the basis of the new system.

### OCR and media quality control

HammerTime has deliberate routing for embedded text, scanned prose, complex layouts, PaddleOCR-VL, Tesseract, transcript QC, OCR QC, and reject or review states. Generic parsers may supplement this system, but they should not replace it without comparative evaluation.

### Release management

The corpus release design has immutable manifests, `dev`, `uat`, and `prod` aliases, `current` and `previous` pointers, secondary review, promotion, and rollback. This is the right contract for hammerTimeOS to consume.

### Retrieval overlays

The context bridge combines SKMemory, Qdrant, FalkorDB, rankings, authority tiers, contradiction detection, fact gaps, procedural timelines, and filing-ready skeletons. These are strong domain-specific assets. They should be exposed behind typed APIs and tested rather than rewritten from scratch.

### Qwen runtime discipline

The runtime is pinned, locally hosted, reversible, and extensively exercised for long context, structured JSON, tool syntax, thinking controls, vision, and concurrency. The live server check returned the expected Huihui Qwen3.8 alias and four 262,144-token unified-KV slots.

### Local-first operation

The current architecture keeps substantive corpus processing and the model route within the local stack. That is strategically aligned with privileged and confidential workloads, provided authorization and trace protection are added.

## Gaps that hammerTimeOS must close

### 1. Navigation is not authorization

Profile ownership and case indexes decide what the workbench displays. They do not enforce tenant isolation, conflicts, privilege, ethical walls, field-level access, or model egress. These controls must apply in the data-access and tool layers before retrieval.

### 2. ITIL terminology obscures the legal domain

`Problem`, `Incident`, root cause, and incident registry are not sufficiently expressive for engagements, matters, proceedings, issues, claims, facts, evidence, deadlines, communications, and work products. SK ITIL should continue to manage platform operations. Legal matter operations need their own model and a compatibility adapter.

### 3. File state and operational state are mixed

Markdown registries, templates, generated artifacts, profile indexes, and packet-status JSON collectively act as a database. This makes cross-matter queries, transactions, access control, versioning, and reliable dashboards difficult.

The current actionable case dashboard reported one stale in-progress item at 132 days even though the registry listed more matters and incidents. That is evidence that artifact-driven dashboards do not represent complete operational state.

### 4. Registry and deadline data can become stale

The incident registry inspected in August still contained earlier 2026 upcoming-deadline sections. Status categories and manually incremented IDs are inconsistent, and `INC-001` can repeat under different problems. hammerTimeOS needs global immutable IDs, generated views, deterministic deadline records, and freshness alarms.

### 5. Authority and claims are not first-class typed records

Decomposition artifacts contain claims and citations, but the framework does not expose a unified typed `MatterProfile`, `AuthorityRecord`, or `ClaimRecord` with status, applicability, counter-support, and approval transitions. This is the center of the proposed legal operating layer.

### 6. Health checks do unbounded work

`scripts/corpus-status.py` reached the Qwen service successfully, then spent more than 90 seconds scanning tens of thousands of decomposition files before the review stopped it. Health endpoints should read materialized metadata with a fixed time budget. Full-tree reconciliation belongs in a scheduled maintenance job.

### 7. Documentation has drifted

The active architecture document describes the current file-backed and external-service model well. `PLANNING.md` still describes a much earlier document collection, and project guidance still contains statements that there is no code project or unit-test need. The repository now contains tens of thousands of lines of Python and hundreds of test functions. Architecture, planning, and contributor guidance need one current source.

### 8. Packaging and test reproducibility are incomplete

There is no unified root Python dependency manifest for the large scripts layer. Direct `unittest` discovery did not match the pytest-style tests, and pytest was not installed in the active environment. No dependencies were installed during this read-only evaluation. A root locked environment and one documented test command are prerequisite foundation work.

### 9. Retrieval coverage is not yet reconciled

The context bridge reports about 1,181 vector documents while the source and decomposition trees contain far more artifacts. Those numbers may represent different units or selected release scope, but the discrepancy needs an explicit corpus coverage report: source artifact count, normalized count, chunk count, vector count, graph count, rejection count, and orphan count by release.

### 10. Runtime qualification is not legal-task qualification

The Qwen production suite establishes runtime stability and capability. It does not establish legal retrieval accuracy, authority status accuracy, citation faithfulness, deadline correctness, privilege containment, or resistance to prompt injection in matter documents. Those require a separate assurance suite.

## Custom embedding assessment

`models/bge-legal-v2` is based on BAAI BGE-M3, uses 1,024 dimensions, and records 57,491 training pairs. Its current evaluation is a small collection of pairwise similarity values. For example:

| Pair | Before | After |
|---|---:|---:|
| `PERSON` versus `person` | 0.9559 | 0.9633 |
| `PERSON` versus corporation | 0.5149 | 0.3899 |
| person versus human | 0.7508 | 0.5670 |
| UCC holder in due course pair | 0.5108 | 0.2235 |

Some pairs may be intended to separate, so a lower score may be correct. The file does not record relevance labels or expected directions, and its simple difference field calls all decreases negative improvements. It also lacks a frozen holdout, leakage analysis, query-to-corpus ranking metrics, jurisdiction slices, authority slices, or no-answer tests.

Conclusion: the model is a valuable candidate, but the current artifact does not qualify it for production retrieval. Compare it with base BGE-M3 on a frozen gold query set using shadow collections and Recall@k, nDCG, MRR, reranked precision, citation accuracy, partition leakage, latency, and resource use.

## Qwen3.8 assessment

### Strengths

- local endpoint with pinned model and projector hashes
- 262,144-token trained context exercised near its limit
- verified JSON schema, function-call syntax, thinking control, vision, recall, and rollback
- four-slot production profile with unified KV and successful concurrency soak
- existing HammerTime prompt and context assembly route
- no need to expose a general shell or external network to the model

### Risks

- the active checkpoint is abliterated, so document-borne malicious instructions and unsafe tool intent require stronger containment testing
- one model family cannot provide independent review of itself
- long requests contend for one shared KV pool even though four slots are exposed
- same-model planner, grader, and writer loops may amplify a shared error
- exact derivative and base-weight license terms need a recorded product-use review

### Recommendation

Keep Qwen as the first bounded corpus analyst and issue spotter. It submits typed proposals and has no direct write, release, filing, email, or arbitrary-network capability. Reserve capacity through a queue, serialize very-long runs initially, and build human plus deterministic review. Qualify a materially distinct model only after the first single-model path is stable.

## Shared concept evaluation

The shared concept's central thesis is sound: search broadly, determine applicability narrowly, use deterministic orchestration, make authority and claims first-class, employ adversarial review, and require human approval.

The most important refinements are:

1. Do not adopt Temporal, LangGraph, and PydanticAI as overlapping orchestrators on day one. Use Temporal outside and PydanticAI inside model activities.
2. Do not introduce Postgres, OpenSearch, a vector database, and a new graph database simultaneously. Keep Qdrant and the existing FalkorDB adapter, use PostgreSQL full-text search first, and add OpenSearch only after measured need.
3. Do not treat a panel of named agents as architecture. Treat tasks, schemas, tools, gates, and reviews as architecture.
4. Do not make an external legal XML or rule standard the internal domain model. Add interchange adapters later.
5. Do not make the abliterated model the final adjudicator or its own independent reviewer.
6. Treat conflict, privilege, ethical-wall, and retention enforcement as foundation work, not a later compliance layer.

HammerTime's own Qwen3.8 architecture review conditionally approved the envelope approach. It highlighted the legacy-schema adapter, dual-source divergence, Temporal overhead, Qwen exclusivity, and adapter observability as the top risks. Its minimum slice and hard gates are reflected in the target design.

## Third-party evaluation

Repository activity and license metadata are a point-in-time snapshot. A dependency decision still requires a pinned commit, exact `LICENSE` review, dependency and model-license inventory, SBOM, vulnerability scan, and maintenance-owner decision.

| Project | Useful capability | Finding | Recommendation |
|---|---|---|---|
| [Temporal](https://github.com/temporalio/temporal) | Durable workflows | Mature, MIT-licensed server and active ecosystem. Python workflows require deterministic code and activities for I/O. | Adopt as outer workflow layer after a small operational spike. |
| [PydanticAI](https://github.com/pydantic/pydantic-ai) | Typed model activities, tools, evals, telemetry | Active, MIT licensed, provider-neutral, and aligned with Python domain schemas. | Adopt inside activities. Do not let it become a second system of record. |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Stateful cyclic agent graphs | Capable and MIT licensed, but overlaps Temporal and PydanticAI graphs. | Defer. Add only for a proven inner-loop requirement. |
| [OPA](https://github.com/open-policy-agent/opa) | Declarative policy decisions | CNCF project, Apache 2.0, good for explainable policy evaluation. | Evaluate after CapAuth capability contracts and core policies stabilize. |
| [Docling](https://github.com/docling-project/docling) | Unified document parsing and OCR or VLM interfaces | Active and MIT licensed with broad format support. HammerTime already has sophisticated routing and QC. | Benchmark as an extraction adapter, not a wholesale replacement. |
| [Qdrant](https://github.com/qdrant/qdrant) | Vector retrieval | Active, Apache 2.0, already integrated. | Keep and formalize release watermark and alias contracts. |
| [FalkorDB](https://github.com/FalkorDB/FalkorDB) | Graph retrieval | Already integrated, but current repository licensing is SSPL. | Keep behind an adapter. Obtain distribution and service-use review before productization. |
| [pgvector](https://github.com/pgvector/pgvector) | PostgreSQL vector extension | PostgreSQL licensed and operationally simpler for small workloads. | Keep as a contingency, not a reason to migrate the current Qdrant corpus. |
| [OpenSearch](https://github.com/opensearch-project/OpenSearch) | Large-scale lexical and hybrid search | Active and Apache 2.0, but adds another cluster. | Defer until PostgreSQL FTS plus Qdrant fails a measured requirement. |
| [Akoma Ntoso](https://github.com/oasis-open/legaldocml) | Legislative and legal-document interchange | Mature standard assets with separate content licensing considerations. | Future import and export adapter only. |
| [LegalRuleML](https://www.oasis-open.org/committees/tc_home.php?wg_abbrev=legalruleml) | Legal rule interchange | OASIS standard with schemas and examples. It does not solve matter state or factual proof. | Future interchange or research lane only. |
| [Catala](https://github.com/CatalaLang/catala) | Executable legislative logic | Apache 2.0, but its own materials describe an unstable research language focused on statutory computation. | Optional isolated pilot for a concrete rule-calculation use case. |
| [OpenFisca](https://github.com/openfisca/openfisca-core) | Tax and benefit microsimulation | AGPL 3.0 and domain-specific. | Isolated service only if an actual tax or benefits use case requires it. |
| [CourtListener API client](https://github.com/freelawproject/courtlistener-api-client) | Official case-law API, citations, alerts, MCP | BSD licensed and maintained by Free Law Project. | Prefer the official connector and reconcile it with HammerTime's existing skill. |
| [eyecite](https://github.com/freelawproject/eyecite) | Citation extraction and source offsets | BSD licensed and focused. It is a parser, not a citator. | Adopt or benchmark for deterministic citation extraction. |
| [reporters-db](https://github.com/freelawproject/reporters-db) | Reporter metadata | BSD licensed and complements eyecite. | Adopt as curated citation metadata. |
| [juriscraper](https://github.com/freelawproject/juriscraper) | Court-site parsing | BSD licensed and actively maintained. Scraping still needs source-specific reliability controls. | Use behind official-source connectors and monitoring. |
| [Thomas More AI legal skills](https://github.com/ThomasMoreAI/legal-skills-open) | Jurisdiction and task skill material | The referenced library reports 3,500+ skills across 39 jurisdictions and 200+ practice plugins under Apache 2.0, but content is community-curated and may retain source-specific metadata. | Quarantine, review, pin, test, and approve individual packs. Never auto-install them as authority. |
| [LegalBench](https://github.com/HazyResearch/legalbench) | Legal task evaluation | Broad task collection, but task and dataset licenses vary. | Select compatible tasks and supplement them with a private gold matter suite. |
| [Multi-Agent-Legal-RAG](https://github.com/vamsigudipati/Multi-Agent-Legal-RAG) | Planner, executor, grader, retry, and run-snapshot patterns | MIT licensed, very small adoption footprint, and built around LangGraph, Chroma, Ollama, and LangSmith. Same-model grading is not independent assurance. | Mine patterns and test cases only. Do not add as a runtime dependency. |
| [doc.haus](https://github.com/sure-scale/doc-haus) | Legal workbench, citations, redlines, review grid, workflow builder | Strong local UX and OpenCode-derived harness. Security docs say it lacks built-in multi-tenant auth. GitHub metadata currently reports `NOASSERTION` despite an MIT claim in project materials. | Reuse audited UI and DOCX components behind hammerTimeOS. Do not use its SQLite, MiniLM index, or permission system as the legal core. Resolve license chain first. |
| [AI Blueprint](https://github.com/rohasnagpal/AI-Blueprint) | Interactive agent-team teaching UI | MIT, intentionally a single browser file, external-provider oriented, and explicitly educational rather than a matter system. | Reuse interaction and visualization ideas only. |
| [ClauseGuard](https://github.com/adityaviki/ClauseGuard) | FastAPI and React contract-review patterns | Small prototype with Elasticsearch, MiniLM, and an OpenAI-compatible endpoint. GitHub reports no license and zero stars at review time. | Study patterns only. Do not copy code without a license grant. |
| [OpenHands](https://github.com/All-Hands-AI/OpenHands) | Software engineering agents | Strong coding platform, not a legal matter runtime. | Optional development automation only. Keep it outside protected matter workflows. |
| [awesome-legal-data](https://github.com/openlegaldata/awesome-legal-data) and [awesome-legal-nlp](https://github.com/maastrichtlawtech/awesome-legal-nlp) | Discovery lists | Useful catalogs, not curated dependencies or authority. Licensing varies by linked project. | Use for discovery and independently review every source. |

## Legal and corpus licensing posture

Code license, model license, dataset license, document copyright, terms of use, privacy restrictions, and citation permission are separate questions. The application needs a machine-readable source-rights record that includes:

- source and acquisition method
- code, model, dataset, or document role
- exact version and hash
- asserted license and evidence location
- jurisdiction or territorial restrictions
- commercial, redistribution, derivative, and hosted-service rights
- attribution requirements
- terms-of-service constraints
- personal-data and confidentiality classification
- review status and reviewer
- allowed uses in retrieval, fine-tuning, evaluation, export, and generated work product
- retention and deletion obligations

Unknown rights mean quarantine. Discovery repositories and public accessibility do not imply permission to ingest, redistribute, fine-tune, or expose content in generated outputs.

For public authority connectors, prefer official primary sources and store acquisition time, URL, response hash, status, and applicable terms. Keep externally verified material separated from protected HammerTime corpus content unless an approved ingestion explicitly brings it into a corpus release.

## Reuse, adapt, retire

### Reuse now

- HammerTime intake, provenance, OCR and transcript QC
- decompositions and release manifests
- Qdrant collection and alias contracts
- curated FalkorDB graph import contract
- context bridge retrieval logic and authority-tier signals
- Qwen gateway and qualification records
- case facts, packet generation, and existing source-linked artifacts
- SKMemory for session continuity
- CapAuth for scoped capabilities
- SKPerf for latency, cost, and evaluation tracking

### Adapt behind interfaces

- `Problem` and `Incident` paths
- profile ownership metadata
- Markdown registries and packet status
- case dashboard and calendar generation
- CourtListener and citation skills
- evidence templates with forum-dependent rules
- corpus health and coverage reporting
- secondary review, which must distinguish same-model variance from independent review

### Retire from the target architecture

- manually incremented operational IDs as canonical identity
- manually maintained registry pages as operational truth
- profile filters as access control
- unbounded full-tree health checks
- vector-only retrieval decisions
- direct model writes to canonical state
- prompts as the only privilege, conflict, or approval control
- a permanent collection of personality agents
- multiple overlapping orchestration frameworks in the first release

## Recommended next decisions

1. Select the first vertical slice. Illinois state plus federal authority and UCC-related corpus is a practical candidate because the existing framework is strongest there, but the exact matter type should be non-sensitive and bounded.
2. Approve the legal domain vocabulary and legacy mapping rules.
3. Decide the initial source-of-truth boundary for private matter originals.
4. Record the Qwen model and FalkorDB license decisions.
5. Assign human owners for conflicts, privilege policy, ethical walls, source rights, security, and release assurance.
6. Build the evaluation set before building more agents.
