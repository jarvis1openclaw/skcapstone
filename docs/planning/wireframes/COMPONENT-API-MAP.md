# SKLegal component-to-backend map

Date: 2026-08-22
Card: SKL-UI-02 (35db7563)
Companion to: `docs/planning/wireframes/index.html`

Every wireframe region maps to the frontend component that will own it, the
backend package surface it binds to, the route capability from
`apps/web/src/auth/authorization.ts`, and the board card that delivers it.
No endpoint is invented here: where a service route does not exist yet, the
map names the owning package and the card that must create it.

## Existing surfaces (verified in repo)

- Frontend routes: `apps/web/src/router.tsx` registers `/`, `/sign-in`,
  `/forbidden`, `/clients`, `/clients/$clientId`, `/matters`,
  `/matters/$matterId`, `/calendar`, `/work-queue`, `/corpus`, `/agent-runs`,
  `/approvals`, `/administration`. The last six render placeholders today.
- API client: `apps/web/src/api/client.ts` implements `GET /v1/clients`,
  `GET /v1/clients/{id}`, `GET /v1/matters`, `GET /v1/matters/{id}`, all
  tenant-scoped with `X-Correlation-ID` and bearer credential.
- Design tokens: `apps/web/src/design/tokens.ts` (approved section 16
  language; status semantics never color-only).
- API service: `services/api/src/sklegal_api/` (governance, capauth modules).

## Screen map

| Screen (route) | Frontend owner | Backend binding | Capability | Card |
| --- | --- | --- | --- | --- |
| Sign-in (`/sign-in`) | `pages/SignInPage.tsx` | session issuance in `services/api`; principal resolution via `packages/capauth` | none (pre-auth) | S4-01 done |
| Home (`/`) | `pages/HomePage.tsx` | aggregate read models, `packages/persistence`; health strip from `packages/retrieval` registry (S2-05) | `home` | S4-01 done; strip S2-05 open |
| Clients (`/clients`) | `pages/ClientsPage.tsx` | `GET /v1/clients` | `clients` | S4-01 done |
| Client detail (`/clients/$clientId`) | `pages/ClientDetailPage.tsx` | `GET /v1/clients/{id}` + engagement and party reads, `packages/domain` | `clientDetail` | S4-02 in progress |
| Matters (`/matters`) | `pages/MattersPage.tsx` | `GET /v1/matters` | `matters` | S4-02 in progress |
| Matter workspace (`/matters/$matterId`) | `pages/MatterDetailPage.tsx` + new tab components | `GET /v1/matters/{id}` aggregate; tabs call domain, retrieval, audit surfaces | `matterDetail` | S4-02 in progress |
| Workspace tab: Overview | new `matter/OverviewTab.tsx` | stage summary (`packages/domain`), deadlines (`packages/workflow`), approvals (`services/api` governance) | `matterDetail` | S4-02 |
| Workspace tab: Parties | new `matter/PartiesTab.tsx` | party + role reads; party identity resolution from S2-08 follow-up | `matterDetail` | S4-02 |
| Workspace tab: Timeline | new `matter/TimelineTab.tsx` | matter events, `packages/domain` + `packages/persistence` | `matterDetail` | S4-02 |
| Workspace tab: Facts and tensions | new `matter/FactsTab.tsx` | fact assertions and tension groups from S2-02 importer; resolution decisions via S1-04A human decision APIs | `matterDetail` | S4-02 + S1-04A |
| Workspace tab: Evidence | new `matter/EvidenceTab.tsx` | evidence items; artifact bytes via read-only HammerTime adapter (S2-01), `packages/retrieval` | `matterDetail` | S4-02 |
| Workspace tab: Communications | new `matter/CommsTab.tsx` | communication records + connector receipts, `packages/connectors` | `matterDetail` | S4-02 + S4-06F done |
| Workspace tab: Documents | new `matter/DocumentsTab.tsx` | work product service, `packages/domain` + `packages/persistence`; DOCX export via Temporal activity | `matterDetail` + workproduct write | S4-04 open |
| Workspace tab: Deadlines | new `matter/DeadlinesTab.tsx` | deadline engine, `packages/workflow` (S4-05 backend done) | `matterDetail` | S4-05 backend done |
| Workspace tab: Audit | new `matter/AuditTab.tsx` | append-only audit query, `packages/audit` (S1-05) | `matterDetail` + audit read | S4-02 |
| Research and claims (`/corpus`) | new `pages/CorpusPage.tsx` | policy-filtered retrieval adapters (S2-04), claim ledger and gates (S3-05) | `corpus` | S4-03 open |
| Corpus registry (`/corpus` admin view) | registry panel on Corpus page | materialized corpus registry + reconciliation (S2-05), `packages/retrieval` | `corpus` | S2-05 open |
| Calendar (`/calendar`) | new `pages/CalendarPage.tsx` | deadline engine, ICS export, calendar connector simulation (S4-06E done) | `calendar` | S4-05 backend done |
| Work queue (`/work-queue`) | new `pages/WorkQueuePage.tsx` | task store (`packages/workflow`), external-action state machine (`packages/connectors`) | `workQueue` | S4-05 + S4-06 done |
| Agent runs (`/agent-runs`) | new `pages/AgentRunsPage.tsx` | Temporal run reads (`packages/workflow`), model gateway evidence (`packages/model_gateway`, S3-02) | `agentRuns` | S3-02 in progress, S3-03 open |
| Approvals (`/approvals`) | new `pages/ApprovalsPage.tsx` | human decision APIs (S1-04A, card bb103650), action approval endpoints | `approvals` | S1-04A done (APIs), UI open |
| Administration (`/administration`) | new `pages/AdminPage.tsx` | policy admin (walls, holds, retention) via S1-04A; connector registry (S2-06, b5b771be done) | `administration` | open |

## Cross-cutting contracts the UI must honor

- Every list and detail response is tenant-scoped and policy-filtered
  server-side; hidden UI never substitutes for authorization (S4-01
  acceptance).
- Every request carries `X-Correlation-ID`; every mutation is idempotent and
  emits append-only audit events (S1-05).
- Retrieval results must satisfy the S2-10 trace contract: tenant and matter
  scope, release, source hashes, projection generation, query-template ID,
  watermark, and rank path on every row.
- Model output is always a typed proposal with source links; accept, reject,
  and challenge are human decisions recorded via S1-04A APIs.
- External actions render the six-state machine from
  `docs/architecture/EXTERNAL-ACTION-STATE-MACHINE.md` including failure and
  retry edges (S4-08); connectors stay simulation-only until S5-03
  qualification.
- Denied material renders as absence or an explicit denial state, never as
  redacted content (S1-04).

## New API surfaces the backend still owes the UI

| Surface | Owning package | Card |
| --- | --- | --- |
| Matter aggregate read (parties, events, facts, evidence, comms in one call or per-tab calls) | services/api + packages/persistence | S4-02 |
| Fact tension resolution decision endpoints | services/api governance | S1-04A (bb103650, done) |
| Claim ledger + gate state reads | packages/domain + services/api | S3-05 (d3514f35, open) |
| Retrieval query + trace endpoint | packages/retrieval + services/api | S2-04 (c4ef2a90, in progress) |
| Corpus registry + health read | packages/retrieval | S2-05 (f5ed9d24, open) |
| Work product CRUD + version compare + DOCX export job | packages/domain + services/api + worker | S4-04 (2e5462d8, open) |
| Deadline list/detail + ICS export | packages/workflow + services/api | S4-05 (07cca7a9, done backend) |
| External action list/detail + transition endpoints | packages/connectors + services/api | S4-06 suite done, S5-03 qualification open |
| Agent run list/detail + decision recording | packages/workflow + model_gateway | S3-02 in progress, S3-03 open |
| Approval inbox query + decision endpoints | services/api governance | S1-04A done (APIs) |
