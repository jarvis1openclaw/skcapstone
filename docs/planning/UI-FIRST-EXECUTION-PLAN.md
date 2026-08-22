# SKLegal UI-first execution plan

Date: 2026-08-21  
Status: Approved execution rebaseline  
Owner directive: Make visible web application progress the immediate delivery focus  
Board card: `6775be53` (`SKL-UI-01`)

## Decision

SKLegal will deliver a continuously available, read-only progress web UI before
the remaining backend and agent work is complete. The progress surface may show
public project status and synthetic demonstration state only. It does not expose
protected Client or Matter content and it is not a production application
deployment.

The original sprint numbers, stable task keys, dependencies, and approved
architecture remain unchanged. This plan changes execution order and board
priority only. A card becomes claimable only after every recorded dependency is
done.

## Current status

The SKCapstone fold for direct children of epic `3457eb15` reported 75 records
after creation of `SKL-UI-01` and the Sprint 6 container:

| State | Count |
| --- | ---: |
| Done | 35 |
| In progress | 1 |
| Review | 1 |
| Ready and open | 17 |
| Blocked | 21 |

The material delivery facts are:

- Architecture approval and Sprint 0 are complete.
- The five original Sprint 1 foundation cards are complete.
- The React design system and application shell are complete.
- The read-only progress UI is deployed and healthy on chiap01 loopback.
- The read-only HammerTime adapter and the retrieval partition contract are complete.
- Temporal workflow foundations are complete.
- PostgreSQL retrieval and graph adapters are in progress.
- CapAuth production composition is in review.
- The Client and Matter workspace is blocked on the pilot importer.
- Sprint 6 official drafting standards research is active as a separate,
  governed corpus lane. Its later acquisition and ingestion cards remain
  blocked on their own explicit dependencies.
- Production application deployment, additional Matter migration, HammerTime
  `Inbox/` processing, external account creation, and outbound legal actions
  remain outside this rebaseline.
- Production application activation requires its own eligible task and human
  gates after this local progress surface.

## Review of all sprints

| Original sprint | Current posture | UI-first disposition |
| --- | --- | --- |
| Sprint 0 | Complete | Preserve as the approved foundation. |
| Sprint 1 | Original core complete; production composition is in review and a real PostgreSQL qualification card is ready. | Continue hardening in parallel. Do not make the static progress surface wait for production composition. |
| Sprint 2 | Read-only adapter and partition contract are done; retrieval adapters are active; importer and ingestion cards are ready; corpus reconciliation is blocked. | Elevate the pilot importer and active retrieval adapter because they unlock the first useful Matter workspace. |
| Sprint 3 | Temporal foundations are done; model gateway is ready; tool, evaluation, and assurance cards are dependency-blocked. | Keep the model and assurance chain behind the first visual vertical slice, while preserving its critical legal gates. |
| Sprint 4 | Application shell and two connector decisions are done; tasks and deadlines are ready; Matter, research, document, and connector views are blocked by explicit dependencies. | Pull the safe UI edge forward now. Prioritize Matter workspace and tasks after their exact dependencies. Defer connectors. |
| Sprint 5 | Qualification and pilot acceptance are mostly blocked or not started. | Keep this as the final governed rollout wave. Early synthetic security and load checks may run only under their own eligible cards. |
| Sprint 6 | The official government drafting standards inventory is active; acquisition, ingestion, style profiles, and release are dependency-blocked. | Keep research parallel to the UI path. Run its HammerTime acquisition and ingestion only under the exact Sprint 6 cards, after their source-rights and completion gates. |

## New execution waves

### Wave 0: Shipped foundation

Preserve the completed architecture, legal domain, persistence, CapAuth,
policy, audit, Temporal, read-only corpus, and application-shell evidence.

### Wave 1: Visible progress now

1. `SKL-UI-01` packages and serves the read-only status UI on loopback.
2. The page reports shipped, active, next, blocked, and deferred work.
3. Health, accessibility, security-header, and rollback checks must pass.

### Wave 2: First useful legal workspace

1. `SKL-S2-02` builds the lossless pilot importer.
2. `SKL-S4-02` uses that governed snapshot to deliver the Client and Matter
   workspace.
3. `SKL-S4-05` may advance in parallel because its recorded dependencies are
   complete.
4. `SKL-S2-04` remains active because policy-filtered retrieval is part of the
   next useful vertical slice.

### Wave 3: Retrieval, research, and legal assurance

1. Complete `SKL-S2-04`, then `SKL-S2-05` and `SKL-S3-04`.
2. Complete `SKL-S3-02`, then `SKL-S3-03` and `SKL-S3-05`.
3. Deliver `SKL-S4-03` only after claim and authority gates are complete.

### Wave 4: Official drafting standards corpus

Run Sprint 6 as a separate governed corpus lane. Inventory and rights review may
continue in parallel, but acquisition, HammerTime intake, semantic processing,
profile generation, and release must follow `SKL-S6-01` through `SKL-S6-05`
in order. Sprint 6 does not block the first visible UI or Matter workspace.

### Wave 5: Work products and action workflows

Complete document drafting, deadline depth, and the external-action connector
suite in simulation mode. No connector may skip validation, exact-version
approval, destination verification, capability verification, or immutable
audit.

### Wave 6: Pilot proof and rollout gate

Run the single approved Matter pilot, governed Qwen workflow, security and
recovery qualification, and human acceptance. Production activation remains a
separate decision after the evidence gate.

## SKL-UI-01 task contract

- **Agent:** Product delivery and deployment
- **Size:** M
- **Dependencies:** `SKL-S0-01`, `SKL-S4-01`, `SKL-STATUS-01`
- **Objective:** Make current application progress continuously visible without
  waiting for protected-data or production-readiness gates.
- **Implementation:** Refresh `docs/status/index.html`, add a hardened static
  server definition bound to `127.0.0.1`, document start, health, stop, and
  rollback commands, and update the board priority signal.
- **Tests:** Static content and links, accessibility landmarks, approved-design
  hash integrity, Compose validation, pinned image, loopback binding, read-only
  filesystem, no-new-privileges, dropped capabilities, health check, response
  headers, and runtime HTTP response.
- **Acceptance:** A browser can open the progress UI locally, the service is
  healthy, board facts are traceable, and no protected content or remote asset
  is present.
- **Prohibited:** No production application activation, public bind, protected
  Client or Matter data, external account creation, HammerTime `Inbox/`
  processing, additional Matter migration, or outbound legal action.

## Rollback

Stop the progress service with the documented Compose down command. This
removes the stateless container and network only. The source page and evidence
remain in Git, and no database, corpus, Client, Matter, or external system state
is changed.
