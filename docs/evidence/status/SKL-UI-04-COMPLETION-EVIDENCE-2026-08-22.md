# SKL-UI-04 completion evidence

Date: 2026-08-22
Card: `31194edb`
Implementation card created: `bbf206c3`
Agent: `jarvis`

## Outcome

Extended the AI-first Matter architecture with one stable SKLegal model route
contract and two interchangeable deployment bindings:

- initial and rollback binding to the approved direct local Qwen service
- preferred future binding through SKGateway after qualification

The app, Agent specs, Matter workflows, prompts, output schemas, and legal
gates use logical SKLegal route IDs. Private hosts, operator aliases, and
credentials remain deployment configuration and exact run evidence.

The implementation TDD covers the OpenAI-compatible Chat Completions adapter,
direct-versus-gateway parity, live-path CapAuth and policy enforcement, exact
Qwen aliasing, shared four-slot capacity, workload buckets, model-size
metadata, free-provider qualification, secret custody, attribution, audit,
failure handling, and rollback.

## Files changed

- `AGENTS.md`
- `CLAUDE.md`
- `docs/tasks/SKL-UI-04-TDD.md`
- `docs/tasks/SKL-S3-10-TDD.md`
- `docs/planning/wireframes/index-v2.html`
- `docs/planning/wireframes/COMPONENT-API-MAP-V2.md`
- `docs/research/HOWTOWININCOURT-ARCHITECTURE-REVIEW-2026-08-22.md`
- `docs/research/SKGATEWAY-INTEGRATION-REVIEW-2026-08-22.md`
- `tests/test_ai_first_wireframes.py`
- `docs/evidence/status/SKL-UI-04-COMPLETION-EVIDENCE-2026-08-22.md`

## Artifact identity

| Artifact | SHA-256 |
| --- | --- |
| Extended version 2 wireframe | `4365a536119836f8d9e76ee0aeb222b4c2c68c11fc01c2a68026a46abd79e765` |
| SKGateway integration review | `be825e16da8ffb8b251893dd3371d1ed71fba8984c4214e8ff80e961dea7a368` |
| SKL-S3-10 implementation TDD | `475a4aa75a8fec3504053bce7e99b6565cb84bc5212aba89e06bb121673d7ce8` |
| Reviewed SKGateway commit | `b4b4115df9a6d5c9c4621d98207a1074e2737ef5` |

## Tests and exact results

1. `python3 -m unittest tests.test_ai_first_wireframes`
   Result: `Ran 9 tests in 0.046s`, `OK`.
2. `PYTHONPATH=packages/domain/src:packages/model_gateway/src python3 -m unittest tests.test_model_gateway_contracts tests.test_model_gateway tests.test_model_gateway_parity`
   Result: `Ran 46 tests in 0.289s`, `OK`.
3. Pinned upstream focused routing suite after documented `npm ci`:
   `qwen38-source-config`, `qwen-capacity-domain`, connection-pool admission,
   buckets, bucket routing, free-provider catalog, model size, sensitivity and
   trust zone, live authorization enforcement, and config-path tests.
   Result: 114 passed, 0 failed, 0 skipped.
4. Pinned upstream served-model and agent-attribution persistence suite.
   Result: 19 passed, 0 failed, 0 skipped.
5. `npm audit --omit=dev` on the reviewed upstream lockfile.
   Result: 1 high-severity production dependency advisory set against
   `js-yaml@4.1.1`. This is recorded as an implementation deployment blocker.
6. Headless Chrome opened the extended `index-v2.html` from its local file
   URL and produced `/tmp/sklegal-ai-first-v2-final.pdf`.
   Result: success, 20 pages, 1,154,097 bytes, tagged, no JavaScript.
7. Browser-rendered model routing pages were visually inspected.
   Result: routing seam, bindings, execution evidence, buckets, qualification
   gates, and free-model lane rendered legibly.
8. ASCII-dash and trailing-whitespace checks across every task artifact.
   Result: no violations.

The first temporary upstream install used `npm ci --ignore-scripts` as a
conservative review attempt. That intentionally omitted the native
`better-sqlite3` binding, so served-model persistence tests could not run and
the long combined run was stopped. The checkout was reinstalled with the
upstream documented `npm ci` command. The isolated persistence suite then
passed 19 of 19. No upstream source or lockfile was edited.

## Acceptance evidence

| Criterion | Evidence |
| --- | --- |
| Logical app route abstraction | `index-v2.html#model-routing`, component map, and instruction updates |
| Direct and router bindings | Initial direct local Qwen and preferred qualified SKGateway bindings shown separately |
| API mismatch visible | Research and wireframe identify Chat Completions versus Responses API and require a new adapter |
| Protected live-path gate | Research, wireframe, and S3-10 TDD require exact live CapAuth, Matter policy, rights, egress, sanitizer, and audit qualification |
| Exact routing provenance | Contract and observed identities include profile, gateway revision, backend, bucket, member, served model, retry, and failover |
| Qwen capacity integrity | Upstream focused suite proves direct and registry aliases share the four-slot domain |
| Bucket and model sizing | Workload class remains separate from model parameter size and requires measured task qualification |
| Free-model discipline | Remote free routes start with public synthetic work only; local Qwen remains the protected cost-free route |
| Secret custody | TDD requires secret references and owner-only runtime injection; no key value was requested or written |
| Dependency risk | One high-severity `js-yaml` audit result is an explicit pre-deployment gate |
| Implementation task | Board card `bbf206c3` and `docs/tasks/SKL-S3-10-TDD.md` cover setup, keys, tests, activation, and rollback |

## Known limitations

- This planning card did not modify SKLegal model gateway code, install a
  fleet service, change a runtime route, or send a live inference request.
- The implementation card is intentionally open and depends on this planning
  card. It requires an assigned deployment host and later human-supplied
  provider keys.
- SKGateway's reviewed production entrypoint must still qualify all required
  controls before protected Matter use.
- The reviewed lockfile has one unaccepted high-severity production dependency
  advisory set.
- SKGateway currently offers Chat Completions, not the existing SKLegal direct
  OpenAI Responses API contract.
- No remote free model has been approved for Matter or private-corpus content.

## Migration and rollback

No database, Matter, corpus, model route, service, API key, or deployment state
changed. No migration is required.

Rollback is documentation-only: remove the SKGateway model-routing screen and
links, revert the model-routing instruction additions, and remove the two task
TDDs, integration review, test additions, and this evidence file. The direct
local Qwen architecture remains unchanged.

## Board evidence

The planning TDD, implementation TDD, integration review, extended wireframe,
component map, test, and this completion record are linked to planning card
`31194edb`. The implementation TDD and review are also linked to open card
`bbf206c3`.
