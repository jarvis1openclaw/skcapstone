# Model gateway (SKL-S3-02)

The model gateway routes approved typed activities to the local Qwen endpoint
or to the OpenAI Platform API without provider-specific domain coupling. It
implements the provider boundary of `docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md`
section 11: models return proposals, and deterministic reducers and human
approvals perform every state change.

## Pinned route registry

`config/model_gateway/route-registry.json` is the machine-readable, versioned
route registry (`sklegal-model-route-registry/v1`). Every route pins:

- route id and enabled state
- provider (`qwen_local` or `openai`)
- model name and revision
- prompt template id and SHA-256 (templates live in
  `config/model_gateway/prompts/`)
- output schema id and SHA-256 (schemas are pinned pydantic payload models in
  `sklegal_model_gateway.schemas`)
- context token budget and max output tokens
- timeout seconds and retry class
- egress classification ceiling
- max concurrent in-flight calls
- for OpenAI routes only, a secret-store reference (never a raw key)

The gateway fails closed on integrity: a prompt template or output schema
whose content hash does not match the pinned SHA-256 raises
`RouteIntegrityError` before any provider call.

## Submit pipeline

`ModelGateway.submit(request, capability_ref=..., cancel_token=...)` runs, in
order:

1. Route resolution. Unknown routes raise `RouteNotFoundError`; disabled
   routes raise `RouteDisabledError`.
2. Capability verification through a narrow `CapabilityGate` protocol. A
   denial, an empty reference, or a verifier exception raises
   `CapabilityDeniedError` (fail closed).
3. Egress decision through `PolicyFileEgressGate`, which reads
   `classification_order` and `external_model_egress` from
   `config/security/policy.json` instead of re-implementing policy. The
   policy file failing to load raises `PolicyUnavailableError` (fail
   closed). For the external OpenAI provider the matrix semantics apply:
   `privileged_work_product` and `highly_restricted` are denied outright,
   `confidential` requires a recorded human approval reference, and
   `public` or `internal` are conditional. Every route also enforces its own
   egress classification ceiling. A denial raises `EgressDeniedError` with
   the reason code.
4. Redaction. Fields listed in `ProposalRequest.protected_fields` are
   replaced with `[REDACTED:<sha256 prefix>]` before prompt rendering, and
   each redaction is recorded on the proposal evidence.
5. Prompt rendering and budget. Strict template rendering; the deterministic
   token estimate must fit the pinned context budget or
   `ContextBudgetExceededError` is raised.
6. Admission. `AdmissionController` allows at most `max_concurrent` (four)
   in-flight calls per provider. The behavior is fail-fast: the fifth
   concurrent submission raises `ProviderSaturationError` and nothing queues.
   Slots release on success, timeout, cancellation, or error. Callers retry
   through the pinned Temporal retry class.
7. Provider invocation on a daemon thread with the pinned timeout. A timeout
   raises `ModelTimeoutError` and cancels the call token so cooperative
   transports exit. Caller cancellation raises `ModelCancelledError`, and a
   result arriving after cancellation is discarded.
8. Output validation. Provider text must be JSON that validates against the
   pinned output schema. Any failure raises `SchemaValidationError`; the
   gateway never returns partial provider output. A provider-reported model
   revision that differs from the pin raises `RouteIntegrityError`.

## Proposals cannot mutate state

The only output is `Proposal`, an immutable pydantic value. It carries
response evidence (route id, registry revision, provider, model name and
revision, prompt and schema hashes, redaction records, policy decision
reference, timing, and token usage) and the validated payload. A `Proposal`
holds no transport, registry, or store handle and exposes no mutating
methods, so provider output cannot mutate workflow or domain state directly.
Tests prove immutability and the absence of callable handles.

## Provider adapters

- `QwenLocalProvider` adapts a local endpoint transport (`QwenTransport`
  protocol). Tests bind fakes; the gateway performs no live network I/O.
- `OpenAiResponsesProvider` builds Responses API requests with a strict JSON
  Schema response format, the pinned deployment model, and `store=false`
  (retention minimization). The API key is resolved at call time through a
  `SecretResolver` from the route's secret reference and never appears in
  config, prompts, logs, or evidence.

## Transport seam (SKL-S3-10)

Direct Qwen and SKGateway are two transport bindings for the same pinned
routes; switching between them is a deployment change only. A route may name
a `transport_profile_id` from `config/model_gateway/deployment/transport-profiles.json`
(schema `sklegal-transport-profile-store/v1`). Each profile pins its kind
(`direct_qwen`, `skgateway_chat`, or `openai_responses`), an environment
variable `endpoint_reference` (literal addresses are rejected), a content
SHA-256, and, for SKGateway, the CapAuth service identity and capability
scope instead of a provider secret. The store fails closed on unknown,
disabled, stale-hash, or wrong-kind bindings, and a route with a bound
profile must not carry a `secret_reference`.

- Routes that share a `capacity_domain_id` draw from one admission envelope
  (`CapacityDomainController`, default four active plus four queued with a
  30 second queue deadline, `qwen.chiap08.shared.v1`), so direct and
  SKGateway calls cannot double-book the same served model. The ninth
  in-flight request raises `ProviderSaturationError`; a queue wait past the
  deadline raises `CapacityQueueTimeoutError`.
- `config/model_gateway/deployment/model-catalog.json` resolves the exact
  Qwen alias `qwen3-32b` to the served model `Qwen3-32B-Q6-K-xlw-20250428`
  and resolves S/M/L/XL workload buckets under trust-zone floors: `public`
  maps only to public-zone buckets, `internal` to internal-or-stricter, and
  confidential and above require a sovereign-local member plus a recorded
  human approval. Quarantined source-rights states deny, and free routes
  apply only where policy-approved.
- `SkGatewayChatProvider` adapts an OpenAI-compatible Chat Completions
  transport. It fails closed before any transport call unless
  `SkGatewayLivePathGate` shows all `LIVE_PATH_CONTROLS` enforced at gate
  revision `skgateway-live-path-gate/v1`, and the served model must agree
  across the response body, the `x_skgateway` attribution block, and any
  header attribute or the call raises `ServedModelAttributionError`.
  Structured output is validated client-side against the pinned schema;
  the adapter never requests provider-side JSON mode.
- `audit_records.py` provides content-free audit records (identifiers and
  policy references only) plus fail-closed sinks.

## Onboarding checkpoint

Before any OpenAI route is enabled for protected matter content, a human
completes `docs/security/OPENAI-PLATFORM-ONBOARDING.md` with the
machine-readable checklist
`config/model_gateway/openai-onboarding-checklist.json`. A consumer ChatGPT
subscription or browser session is never an application credential.
