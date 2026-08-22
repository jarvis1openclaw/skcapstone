# HowToWinInCourt architecture review for SKLegal

Date: 2026-08-22
Card: `SKL-UI-03` (`ecf6d536`)
Scope: private personal-use research and static product planning

## Result

The approved SKLegal architecture can support a case-in, ranked-next-steps
experience, but the useful product is a governed Matter analysis pipeline, not
an unconstrained chat answer. The existing control plane is coherent:

- HammerTime owns corpus sources, releases, and provenance.
- SKLegal owns Clients, Engagements, Matters, workflow state, Claims,
  Defenses, Work Products, Approvals, and Execution Events.
- local Qwen performs initial corpus interpretation through a pinned model
  route.
- an approved frontier route may assemble or format a Work Product only when
  classification and egress policy allow the exact context.
- every provider output is an inert typed proposal.

The missing product layer is a versioned recommendation contract and an
AI-first Matter cockpit that joins the existing records without collapsing
their legal meaning.

## Verified corpus inventory

The private semantic release reports:

| Inventory | Verified value |
| --- | ---: |
| Mapped course documents | 140 |
| Mapped media documents | 39 |
| Ordinary course retrieval files | 122 |
| Ordinary media retrieval files | 38 |
| Optional private reference files | 18 |
| Administrative archive files | 25 |
| Community or validation files used as sources | 0 |
| Proceeding phases | 10 |
| Matter types | 11 |
| Procedures | 389 |
| Work Product candidates | 212 |
| Topics | 375 |
| Jurisdiction signals | 162 |
| Defined terms | 431 |
| Installed private skills | 12 |
| Unique skill source routes | 42 |

Sources:

- `/mnt/cloud/onedrive/projects/DAVE-AI/hammerTime/private-corpus/how-to-win-in-court/semantic/semantic-manifest.json`
- `/mnt/cloud/onedrive/projects/DAVE-AI/hammerTime/private-corpus/how-to-win-in-court/semantic/semantic-verification.json`
- `/mnt/cloud/onedrive/projects/DAVE-AI/hammerTime/private-corpus/how-to-win-in-court/skills/private-skills-manifest.json`
- `/mnt/cloud/onedrive/projects/DAVE-AI/hammerTime/private-corpus/how-to-win-in-court/skills/private-skills-verification.json`

The 12 skills are curated procedural routers. They do not exhaust the ordinary
160-file retrieval set and must not become the only way the application can
search the course.

## Corpus method that should shape the product

The most durable course method is an element-centered record-building loop:

1. Identify the Claim or Defense.
2. List every essential Element and the applicable burden of proof.
3. Link each Element to Matter Fact Assertions.
4. Link each Fact Assertion to supporting and counter-supporting Evidence
   Items.
5. Identify gaps and the discovery tool that could close each gap.
6. Use pleadings, discovery, motions, hearing records, trial evidence, and
   post-judgment procedure to build and preserve the official record.
7. Verify every step against the rules and Authority controlling the exact
   Proceeding.

The corpus architecture also provides a strong deterministic routing key:

`Matter type + Proceeding phase -> skill family -> approved source routes`

Suggested phase routes from the corpus are:

| Proceeding phase | Course route | Candidate Work Products |
| --- | --- | --- |
| Pre-filing and planning | `planning -> elements -> causes` | Issue map, Element matrix, evidence plan |
| Pleadings | `complaints or answers -> defenses -> forms` | Complaint, Answer, affirmative defenses |
| Discovery | `discovery -> compelling -> depositions` | Admissions, production, interrogatories, subpoenas |
| Motion practice | `motions -> argument -> summary` | Motion, memorandum, proposed order |
| Trial and evidence | `evidence -> objections -> trial` | Exhibit plan, objection log, witness plan |
| Post-judgment and appeal | `collecting or show-cause -> appeals` | Writ request, enforcement motion, appeal record plan |

Source: `/mnt/cloud/onedrive/projects/DAVE-AI/hammerTime/private-corpus/how-to-win-in-court/semantic/source-architecture.md`.

## Required evidence lanes

Each recommendation must preserve five distinct lanes:

| Lane | What it can establish | What it cannot establish |
| --- | --- | --- |
| Course instruction | A source-derived method, tactic, definition, form pattern, or question to investigate | Current controlling law or a Matter fact |
| Matter record | What a party asserted, what an artifact shows, provenance, timing, and evidentiary state | Legal applicability by itself |
| Current Authority | Governing rule, statute, opinion, order, effective date, and jurisdictional applicability | That the Matter facts satisfy it |
| Model inference | A typed proposal linking the first three lanes | Workflow state, truth, Approval, or permission to act |
| Human decision | Review, challenge disposition, exact-version Approval, or rejection | A substitute for missing source support or a failed deterministic gate |

The UI must show these lanes separately at the proposition level. It must not
hide them inside one blended citation list.

## Retrieval finding

Focused questions about planning, elements, pleadings, discovery, and the
record returned relevant course spans. A broad abstract query asking the
retriever to compare strategic options by proof, risk, posture, and contrary
Authority produced weak similarity scores near 0.34 and irrelevant rule-of-
order material.

This supports a hybrid pipeline:

1. deterministic route by Matter type and Proceeding phase
2. closed lexical and exact-source lookups for course families
3. vector retrieval inside the already authorized scope
4. source-role classification and exact-span verification
5. deterministic eligibility gates
6. model proposal generation
7. versioned multi-factor ranking and blind challenge

Vector similarity may help find material. It must not create Authority status,
Element support, applicability, or recommendation readiness.

## Current-rule drift example

The imported media transcript
`private-corpus/how-to-win-in-court/media/transcripts/floridasupremecourt-0c3187445682.md`
records an advocate arguing against a proposed limit on Requests for
Admissions. It is hearing advocacy, not an opinion or rule. A source-role
classifier must retain the speaker and genre instead of presenting the
transcript as controlling Authority.

The Supreme Court of Florida later adopted a 30-request limit, including
subparts, absent leave for good cause or a stipulation. The amendment became
effective April 1, 2026. Official source:
<https://flcourts-media.flcourts.gov/content/download/2483730/opinion/Opinion_SC2024-0779.pdf>.

The course also repeatedly uses the historical discovery formula
`reasonably calculated to lead to the discovery of admissible evidence` in
`easy-guide-cf12d3e93439.md`. Current Federal Rule of Civil Procedure 26(b)(1)
uses relevance to a claim or defense and proportionality to the needs of the
case. Official source:
<https://www.uscourts.gov/forms-rules/current-rules-practice-procedure/federal-rules-civil-procedure>.

These examples do not reduce the course's value. They prove that course
instruction and current Authority need separate retrieval, verification,
effective-date, and contrary-material lanes.

## Recommendation contract

A ranked recommendation should include at least:

- recommendation ID, version, and scoring-policy version
- Matter type and Proceeding phase
- targeted Issue, Claim or Defense, and Element
- proposed Task or Work Product type
- why the action is proposed now
- prerequisites and prohibited sequencing
- course source spans and release hashes
- current Authority and applicability state
- Matter Fact Assertions and Evidence Items used
- support, counter-support, unresolved tensions, and record gaps
- candidate Deadline trigger and deterministic calculation state
- consequences of acting, waiting, or losing the issue
- provider route, model revision, prompt hash, schema hash, and confidence
- blind-challenge result and same-model disclosure
- required capability, human decision, and downstream workflow gate

The rank must come from a versioned, visible policy. Suggested dimensions are
jurisdiction fit, Proceeding-phase fit, Matter-type fit, Element coverage,
Evidence strength, Authority status, Deadline urgency, prerequisite
completion, contrary support, record-gap severity, source freshness,
challenge outcome, and action readiness. The architecture should not approve
fixed weights in a wireframe. Implementing cards must freeze and evaluate the
policy against synthetic and held-out Matters.

## Model roles

The approved canonical SKLegal role name is `Qwen3 local`. HammerTime currently
serves an operator alias containing `qwen3.8`. The domain model should not hard-
code either name. Each run should resolve a route registry record and show the
exact served model and revision in its evidence drawer.

Recommended role split:

- local Qwen: course interpretation, source routing, Element mapping,
  contradiction extraction, missing-proof questions, initial strategy and
  drafting proposals
- optional frontier route: second-pass challenge, long-context synthesis,
  document structure, citation-preserving assembly, version comparison, and
  formatting when policy permits
- deterministic services: authorization, source-role validation, exact quote
  checks, effective-date handling, Deadline calculation, state transitions,
  ranking eligibility, artifact hashing, Approval binding, and dispatch

An abliterated model profile changes model behavior. It does not change access,
authority, approval, or action policy.

The deployment transport is also abstract. The initial logical local route may
bind directly to chiap08 Qwen. A qualified SKGateway profile may later carry
the same logical route through its OpenAI-compatible Chat Completions API.
That switch must preserve prompt and schema pins, classification, legal gates,
and the typed Proposal while adding exact gateway, backend, bucket, and served-
model evidence. See
`docs/research/SKGATEWAY-INTEGRATION-REVIEW-2026-08-22.md`.

## Current SKLegal gaps

The approved architecture has the needed aggregates and boundaries, but the
current application lacks the contracts required to render the whole cockpit:

- no typed Matter-analysis or ranked-recommendation output schema
- no Matter strategist or blind-challenge agent specification
- no HTTP surface for Issues, Claims, Defenses, Elements, Authorities, claim
  support, or model-run evidence
- no general human decision surface for recommendations and Work Products
- no model route connecting the Work Product draft tool to a drafting agent
- no implemented jurisdiction overlay or current-Authority snapshot in the
  Matter workspace response
- no versioned recommendation-scoring policy or evaluation set
- no source-role distinction between course lesson, example form, advocate
  statement, current rule, current opinion, Matter evidence, and model
  inference
- no unified Matter artifact intake surface that preserves the original,
  extraction lineage, custody, AI classifications, human corrections, and
  case-theory links in one place
- no joined Matter activity projection spanning legal chronology, Agent Runs,
  tool calls, recommendation versions, artifact lineage, decisions,
  Approvals, Execution Events, and receipts
- no one-click production of a case chronology, artifact manifest, analysis
  dossier, or action-and-receipt log as a versioned Work Product

Version 2 labels these as backend contracts owed by the existing S3-05,
S4-03, S4-04, and S5-02 work families. It does not invent endpoint paths.

## Anti-patterns

- one answer box with blended facts, course text, and Authority
- one similarity score presented as legal confidence
- course skill names treated as Authority
- a hearing transcript presented as a ruling
- silent correction or harmonization of aged or conflicting course material
- drafting operative facts into prose when the record contains a gap
- hard-coded model brands in domain state
- frontier egress merely because formatting would improve
- accepting a model suggestion directly as a workflow transition
- direct File, Send, Serve, or Schedule buttons on a model proposal
- calculating a consequential Deadline from model text alone
- external action before exact-version validation, Approval, destination
  verification, capability verification, audit, and receipt reconciliation

## Version 2 product decision

Keep `/matters/$matterId` as the primary cockpit. Add an analysis request,
workflow ribbon, ranked recommendation queue, Element proof matrix, course
strategy panel, jurisdiction overlay, source-span viewer, challenge panel,
Work Product assembler, Matter artifact inbox, missing-artifact request
builder, run-evidence drawer, and unified case activity log. Preserve every
original and derived artifact with exact lineage. Make the case log readable
for a person while retaining direct links to the append-only audit,
provenance, model-run, Approval, Execution Event, and receipt records beneath
it. Keep `/corpus` as the deeper research and corpus-health surface rather
than making users leave the Matter to understand what to do next.

## Provenance and artifact handling decision

The architecture already has the right durable pieces: Source Artifact
Reference, Matter Snapshot, Evidence Item, Custody Event, Matter Event, Agent
Run, Tool Call, Work Product Version, Artifact Binding, Approval, Execution
Event, Receipt, and a content-free tamper-evident audit ledger. Version 2
turns those pieces into two simple user experiences.

First, `Add to this Matter` accepts authorized case material. The system
preserves original bytes, source identity, filename, media type, byte count,
hash, acquisition time, custody, classification, and policy context. It then
creates separately hashed OCR, transcript, thumbnail, redaction, translation,
or extraction children. AI proposes Fact Assertions, Evidence Items, Matter
Events, and links to Issues, Claims, Elements, Tasks, and Work Products. Human
corrections supersede the proposal and never erase its history.

Second, the Matter activity log joins the legal chronology with system work.
It shows what happened, who or what did it, what inputs and versions were
used, what changed, why it changed, what the result affected, and whether it
had external effect. Filters keep the default view readable. A provenance
drawer exposes hashes, correlation IDs, model and tool pins, source spans,
policy decisions, version transitions, Approvals, and receipts when deeper
review is needed.

Missing material is handled from the proof map. The AI converts an Evidence
gap into a proposed artifact request with purpose, target Element, suggested
custodian, requested format, and due state. It may prepare the request and a
secure collection flow. Sending the Communication remains a separately
approved external action.

The detailed layout is in `docs/planning/wireframes/index-v2.html` and the
contract map is in `docs/planning/wireframes/COMPONENT-API-MAP-V2.md`.
