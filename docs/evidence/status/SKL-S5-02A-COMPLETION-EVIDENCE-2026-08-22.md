# SKL-S5-02A completion evidence

Date: 2026-08-22
Card: `1961af4c` (slice of `dfd37d07` SKL-S5-02)
Agent: `skl-s5-02a`

## Outcome

One narrow capability: a governed end-to-end Qwen proposal run over
pinned pilot matter context, executed as one Temporal activity run.

- `GovernedProposalRunner` resolves one pinned retrieval context by its
  pin id (in-process registry, fail-closed on unknown or reused ids),
  retrieves it twice through the SKL-S2-04 `RetrievalOrchestrator`
  (once before the model call, once after), validates the trace's source
  ids and hashes against the pinned `SourcePin` inventory with exact set
  equality, and detects a mid-run source change by comparing the two
  traces.
- The prompt context is composed as ranked blocks with explicit source
  id, locator, and sha256 markers, then submitted to local Qwen through
  the SKL-S3-02 `ModelGateway` as a `ProposalRequest` on the
  `qwen.corpus-summary.v1` route with a capability reference.
- Typed output validation happens inside the gateway against the pinned
  schema; the runner maps gateway failures onto worker error types with
  explicit retry semantics (outage and timeout to retryable
  `ModelUnavailableError`, schema and contract failures to retryable
  `ProposalOutputInvalidError`, capability and egress denials to
  non-retryable `PolicyDeniedError`, retrieval authorization denial to
  non-retryable `PolicyDeniedError`, retrieval backend failure to
  retryable `ContextRetrievalUnavailableError`).
- Exactly one content-free `ProposalRunRecord` is written to an
  idempotent ledger (`InMemoryProposalLedger` or crash-durable
  `FileProposalLedger` with atomic tmp+fsync+rename writes,
  first-write-wins, fingerprint conflict detection). The proposal
  payload, prompt text, and retrieved content never enter the record.
- `GovernedProposalWorkflow` is a deterministic workflow definition that
  validates the queue contract and delegates every effect to the
  `run_governed_proposal` activity under `RetryClass.MODEL`.

Acceptance: provider output remains a proposal and mutates no state.
The runner writes only to the injected proposal ledger. There is no
domain, workflow-state, approval, dispatch, or connector mutation, and
no external action. Rejection or acceptance is a separate human
decision outside this slice.

## Files changed

- `services/worker/pyproject.toml` (add `sklegal-model-gateway` and
  `sklegal-retrieval` dependencies)
- `uv.lock`
- `services/worker/src/sklegal_worker/errors.py`
  (`ProposalOutputInvalidError`, `SourceLinkValidationError`,
  `ContextRetrievalUnavailableError`)
- `services/worker/src/sklegal_worker/models.py` (`SourcePin`,
  `ProposalRunInput`, `ProposalRunOutcome`, `GovernedProposalInput`)
- `services/worker/src/sklegal_worker/proposal_run.py` (new: pinned
  context registry, runner, ledgers, activity shim)
- `services/worker/src/sklegal_worker/workflows.py`
  (`ACTIVITY_RUN_GOVERNED_PROPOSAL`, `GovernedProposalWorkflow`)
- `services/worker/src/sklegal_worker/__init__.py` (exports; proposal
  run exports load lazily so Temporal sandbox workflow validation never
  imports `sklegal_retrieval`)
- `tests/test_proposal_run.py` (new, 29 tests)

## Tests and exact results

- `UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-worker pytest tests/test_proposal_run.py -q`
  - 29 passed.
- `UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-worker pytest tests/test_worker_workflows.py tests/test_proposal_run.py tests/test_model_gateway.py tests/test_retrieval_orchestrator.py -q`
  - 129 passed, 10 subtests passed (no regression in the gateway or
    retrieval adapters or the existing worker suite).
- `UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-worker pytest tests/test_load_saturation.py tests/test_outage_qualification.py -q`
  - 44 passed, 18 subtests passed. This includes the Temporal sandbox
    smoke test, which catches sandbox-restricted imports in workflow
    validation; it passes because the package-level proposal run exports
    load lazily.
- `ruff check` and `ruff format --check` on all changed files: passed.
- Project `mypy` gate invocation over scripts, services, and packages:
  11 errors in 4 files, identical count and files to the base tree
  (verified via stash); zero errors in files this card touched.
- ASCII hyphen scan of all changed files: no en or em dashes.

Required test coverage from the card, all present and passing:

- Qwen outage: transport raises `ProviderUnavailableError`; the runner
  raises `ModelUnavailableError` and records nothing.
- Malformed output: non-JSON response text and schema-violating JSON
  both raise `ProposalOutputInvalidError` and record nothing.
- Source change mid-run: a switching executor returns a different source
  hash (and a separate case, a different source id) on the second
  retrieval; the runner raises `SourceLinkValidationError`, records
  nothing, and the failure is detected after the provider call.

## Known limitations

- Qwen is simulated through a fake transport over the real gateway
  stack: the real `QwenLocalProvider`, route registry, prompt store,
  schema registry, capability gate, and egress policy are exercised, but
  no live model endpoint is called in tests.
- The pinned `RetrievalRequest` lives only in the worker process behind
  a registry Protocol. Wiring a persistent pin store is parent-card
  work.
- `worker.py` queue specs are untouched: the new activity is qualified
  by tests and is not yet registered on a running worker's task queue.
- Human decision, review, and second-pass challenge from the parent
  SKL-S5-02 card are explicitly out of scope for this slice.
- `tests/integration/test_foundation_contract.py` has 1 failure in this
  environment (`test_foundation_checks_leave_no_development_containers_running`,
  a dev-container hygiene check); verified identical on the base tree
  and unrelated to this card.
- `tests/test_load_saturation.py::DriverSmokeTests::test_signing_scenario_real_openpgp_mode`
  failed once in one combined run and passed in eight consecutive
  reruns (isolated, paired, and combined). It exercises real OpenPGP
  signing, touches none of this card's code paths, and appears flaky in
  this environment.
