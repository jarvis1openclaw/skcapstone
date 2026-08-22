# SKGateway integration review for SKLegal

Date: 2026-08-22
Planning card: `31194edb`
Implementation card: `bbf206c3`
Upstream: `smilinTux/skgateway`
Reviewed commit: `b4b4115df9a6d5c9c4621d98207a1074e2737ef5`

## Decision

SKGateway is a good future transport router for SKLegal, but it should sit
inside the existing SKLegal model gateway rather than replace it.

SKLegal keeps ownership of:

- Tenant, Matter, purpose, classification, privilege, source-rights, and
  egress decisions
- logical route IDs, prompts, output schemas, timeouts, retry policy, and task
  workload class
- typed Proposal validation and all legal workflow gates
- the durable Agent Run and Matter provenance record

SKGateway may own:

- OpenAI-compatible Chat Completions transport
- backend discovery and health
- concrete model aliases and dynamic model buckets
- shared backend admission and queueing
- provider credentials, routing, failover, token and cost metrics, and
  served-model attribution

The initial deployment may bind the SKLegal logical local route directly to
chiap08 Qwen. The preferred future deployment binds the same logical route to
SKGateway after qualification. That switch changes deployment configuration,
not Matter logic or agent specifications.

## Verified upstream facts

The reviewed repository is MIT licensed and requires Node.js 20 or later. It
uses `npm ci` for a lockfile-based install. Its main proxy exposes
`POST /v1/chat/completions`, `GET /v1/models`, and health surfaces. It is not a
drop-in implementation of the OpenAI Responses API used by SKLegal's current
direct OpenAI adapter.

A clean `npm ci` at the reviewed commit installed 40 packages. The production
dependency audit reported one high-severity advisory set against
`js-yaml@4.1.1` involving quadratic CPU consumption in YAML merge and object
map processing. This is a deployment blocker until a pinned upstream update
or reviewed lockfile change passes the full suite. The planning review did not
run an automatic audit fix.

The source already contains:

- the exact chiap08 Qwen operator alias plus short request aliases
- a shared `chiap08-qwen38` capacity domain covering direct and registry paths
- four active slots, four queued requests, and a 30-second queue SLA
- model alias and backend priority routing
- dynamic model discovery and explicit free-model metadata
- model parameter parsing and separate `S`, `M`, `L`, `XL` size metadata
- workload buckets that combine task class with public, internal, or secret
  sensitivity
- exact served-model, backend, bucket, and bucket-member attribution
- environment-variable secret names rather than inline provider keys
- CapAuth, policy, sanitizer, rate-limit, audit, and metrics modules

Primary upstream sources:

- <https://github.com/smilinTux/skgateway/tree/b4b4115df9a6d5c9c4621d98207a1074e2737ef5>
- <https://github.com/smilinTux/skgateway/blob/b4b4115df9a6d5c9c4621d98207a1074e2737ef5/docs/INSTALL.md>
- <https://github.com/smilinTux/skgateway/blob/b4b4115df9a6d5c9c4621d98207a1074e2737ef5/docs/CONFIGURATION.md>
- <https://github.com/smilinTux/skgateway/blob/b4b4115df9a6d5c9c4621d98207a1074e2737ef5/docs/evidence/2026-08-21-qwen38-capacity-domain.md>

## Critical live-path gap

The reviewed upstream README distinguishes the production `routeAndSend` path
from an alternate `handleRequest` path. It says routing, SIEM, and metrics are
live, while sanitizer limits, tool budgets, CapAuth verification, the policy
engine, and risk or PII classifiers are not invoked by the production path.

That means SKLegal cannot infer production enforcement from the presence of
those modules or from their unit tests. Protected Matter and private corpus
traffic must remain on the current approved local path until the exact live
entrypoint is unified and qualified. Public synthetic integration tests can
start earlier.

Source:
<https://github.com/smilinTux/skgateway/blob/b4b4115df9a6d5c9c4621d98207a1074e2737ef5/README.md>.

## API compatibility gap

The present SKLegal adapters are not one generic OpenAI-compatible transport:

- `QwenLocalProvider` expects a local transport with a `generate` contract.
- `OpenAiResponsesProvider` calls the OpenAI Responses API and extracts its
  response shape.
- SKGateway currently fronts the Chat Completions API.

Pointing an existing base URL at SKGateway is therefore insufficient. The
implementation needs an `OpenAiCompatibleChatProvider` or equivalent
transport profile behind `ModelGateway.submit`. It must translate the pinned
SKLegal prompt and output schema into bounded Chat Completions input and then
perform the same client-side strict output validation.

SKGateway may transparently forward a `response_format` field, but that is not
an approved guarantee until the exact Qwen and selected provider paths pass
schema-parity tests. A route that cannot guarantee structured output remains
disabled for typed SKLegal proposals.

## Route identity model

The route evidence needs two identities instead of one:

1. Requested contract identity
   - SKLegal logical route ID
   - prompt and output schema hashes
   - transport profile
   - requested concrete model or bucket
   - workload class and classification ceiling
2. Observed execution identity
   - exact SKGateway commit and configuration hash
   - request and correlation IDs
   - catalog and policy revisions
   - backend and capacity domain
   - bucket and bucket member when applicable
   - exact served model
   - retry, failover, saturation, latency, and token evidence

A missing or conflicting observed identity is a provider contract failure.
Silent model substitution is never accepted merely because the output schema
validates.

## Bucket and sizing decision

SKGateway correctly separates workload class from model parameter size. The
same letters appear in both concepts, so the integration must retain distinct
field names and evidence.

- `workload_class` is the minimum capability and risk class for a task.
- `model_size_class` is metadata based on total model parameters and may help
  rank candidates.
- Measured task capability, structured-output reliability, citation
  preservation, and held-out SKLegal evaluations decide eligibility.
- Parameter count alone never makes a model qualified for a legal task.

The safest initial mapping is concrete, not dynamic: substantive private
course analysis requests the exact local Qwen route. Buckets are introduced
for tasks where model substitution is explicitly acceptable, beginning with
public synthetic and low-risk utility work.

SKLegal and SKGateway classification vocabularies do not match one-to-one.
An implementation-owned mapping must fail closed:

| SKLegal context | Maximum initial SKGateway target |
| --- | --- |
| Public | Public bucket with an evaluated member |
| Internal | Internal or stricter bucket with an evaluated member |
| Confidential | Concrete sovereign-local route until a qualified secret bucket exists |
| Highly restricted | Concrete sovereign-local route only |
| Privileged Work Product | Concrete sovereign-local route only unless an existing SKLegal human and policy gate expressly approves otherwise |
| Private course corpus | Concrete local Qwen route unless source rights and egress policy expressly approve another route |

## Free-model decision

Free is a price attribute, not a security, quality, or rights decision. A
remote free route must be qualified for provider retention and data use,
geography, model identity, context and output limits, schema adherence,
citation preservation, tool behavior, quotas, rate limits, availability, and
the specific SKLegal task evaluation.

Initial rule:

- local Qwen can remain the cost-free protected route
- remote free models receive public synthetic prompts only
- no remote free provider receives private course or Matter content by default
- each provider key is added through secret custody after the human chooses
  the provider
- failures cannot fall back across a stricter trust-zone ceiling

## Installation and secret decision

Pin the upstream commit and lockfile. Install with `npm ci` under a dedicated
service identity. Bind to the narrowest interface, use authenticated TLS for
any remote hop, and use an owner-only runtime secret file or stronger secret
injection. Committed configuration stores only environment variable names and
secret references.

The upstream loader reportedly falls back to defaults after invalid YAML.
SKLegal deployment must add a preflight that rejects invalid, missing, stale,
or unexpectedly defaulted production configuration before service activation.
The preflight and source qualification must also reject an unaccepted high or
critical production dependency vulnerability.

Provider API keys are a later human input. The implementation can install and
qualify the local Qwen route without them, then add one provider at a time.

## Delivery tasks

The complete implementation contract is
`docs/tasks/SKL-S3-10-TDD.md`, board card `bbf206c3`. Its hard sequence is:

1. transport abstraction and fake tests
2. dependency-audit remediation at a new pinned source or lockfile revision
3. pinned SKGateway install without external keys
4. exact Qwen and capacity configuration
5. live-path control unification and qualification
6. synthetic direct-versus-gateway parity and rollback
7. human-provided provider key onboarding
8. public-only free-model qualification
9. one approved synthetic SKLegal Agent Run

No protected Matter deployment is approved by this research card.
