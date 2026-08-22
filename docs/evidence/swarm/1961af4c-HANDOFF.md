# HANDOFF: SKL-S5-02A governed Qwen proposal run

Card: `1961af4c` (slice of `dfd37d07` SKL-S5-02)
Agent: `skl-s5-02a`
Branch: `swarm/1961af4c`
Implementation commit: `8db6d90` (feat(worker): governed Qwen proposal
run with typed validation). Evidence commit follows this file.

Full completion evidence:
`docs/evidence/status/SKL-S5-02A-COMPLETION-EVIDENCE-2026-08-22.md`

## Scope delivered

One narrow capability: one governed proposal run over the pilot matter,
executed as one Temporal activity. The runner resolves a pinned
retrieval context through the S2-04 adapters, retrieves it before and
after the model call, validates source ids and hashes against the
pinned inventory, submits the composed prompt to local Qwen through the
S3-02 gateway, validates the typed output, and writes exactly one
content-free record to an idempotent proposal ledger. The provider
output remains a typed proposal and mutates no state.

## Files changed

- `services/worker/pyproject.toml`: add `sklegal-model-gateway` and
  `sklegal-retrieval` dependencies.
- `uv.lock`: relocked.
- `services/worker/src/sklegal_worker/errors.py`: add
  `ProposalOutputInvalidError`, `SourceLinkValidationError`,
  `ContextRetrievalUnavailableError`.
- `services/worker/src/sklegal_worker/models.py`: add `SourcePin`,
  `ProposalRunInput`, `ProposalRunOutcome`, `GovernedProposalInput`.
- `services/worker/src/sklegal_worker/proposal_run.py`: new module
  (pinned context registry and Protocol, `GovernedProposalRunner`,
  `InMemoryProposalLedger`, crash-durable `FileProposalLedger`,
  `proposal_record_fingerprint`, `GovernedProposalActivities`).
- `services/worker/src/sklegal_worker/workflows.py`: add
  `ACTIVITY_RUN_GOVERNED_PROPOSAL` and `GovernedProposalWorkflow`
  (deterministic body; all effects in the activity).
- `services/worker/src/sklegal_worker/__init__.py`: export the new
  errors, models, workflow, and lazy proposal run exports.
- `tests/test_proposal_run.py`: new, 29 tests.
- `docs/evidence/status/SKL-S5-02A-COMPLETION-EVIDENCE-2026-08-22.md`:
  completion evidence.
- `HANDOFF.md`: this file.

## Tests and exact results

All commands run from the repo root with
`UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-worker ...`:

- `pytest tests/test_proposal_run.py -q`: 29 passed.
- `pytest tests/test_worker_workflows.py tests/test_proposal_run.py tests/test_model_gateway.py tests/test_retrieval_orchestrator.py -q`:
  129 passed, 10 subtests passed.
- `pytest tests/test_load_saturation.py tests/test_outage_qualification.py -q`:
  44 passed, 18 subtests passed.
- `ruff check` on all changed files: passed.
- `ruff format --check` on all changed files: passed.
- Project mypy gate invocation (scripts, services, packages): 11 errors
  in 4 files, identical to the base tree (verified via stash); zero
  errors in files this card touched.
- ASCII hyphen scan of all changed files: clean.

## Acceptance criteria evidence

- Retrieval through the S2-04 adapters: the runner retrieves the pinned
  typed `RetrievalRequest` through `RetrievalOrchestrator` twice per run
  and validates the trace source set and hashes against the pinned
  inventory (`test_records_one_content_free_record_with_pinned_evidence`,
  `test_source_inventory_is_reverified_after_the_provider_call`).
- Qwen invocation through the S3-02 gateway: the runner submits a
  `ProposalRequest` on `qwen.corpus-summary.v1` with a capability
  reference; the fake transport records the rendered prompt with
  explicit source markers
  (`test_prompt_carries_explicit_source_markers`).
- Typed output and source link validation: schema-violating and
  non-JSON output raise `ProposalOutputInvalidError`
  (`test_non_json_output_maps_to_proposal_output_invalid`,
  `test_schema_violating_output_maps_to_proposal_output_invalid`);
  unknown pins fail closed; empty or unpinned traces raise
  `SourceLinkValidationError`.
- Qwen outage: transport outage maps to `ModelUnavailableError` with no
  ledger entry (`test_qwen_outage_maps_to_model_unavailable_and_records_nothing`).
- Malformed output: both classes record nothing.
- Source change mid-run: a switching executor changes the source hash
  (or id) on the second retrieval; the runner raises
  `SourceLinkValidationError` after the provider call with no ledger
  entry (`test_source_hash_change_mid_run_fails_closed`,
  `test_source_set_change_mid_run_fails_closed`).
- Provider output remains a proposal and mutates no state: the ledger
  record and workflow input carry no matter content, no payload, and no
  prompt (`ProposalStatelessnessTests`); replay is idempotent with one
  ledger record, one gateway call, and two retrievals; the dispatch and
  approval machinery is untouched.

## Known limitations

- Qwen is simulated via a fake transport over the real gateway stack;
  no live model endpoint is called in tests.
- The pinned `RetrievalRequest` lives only in the worker process behind
  a Protocol registry; a persistent pin store is parent-card work.
- `worker.py` queue specs are untouched: the activity is qualified by
  tests but not yet registered on a running worker's task queue.
- Human decision, review, and second-pass challenge are parent SKL-S5-02
  scope, not this slice.
- `tests/integration/test_foundation_contract.py` has 1 pre-existing
  environment failure (dev-container hygiene), identical on the base
  tree.
- `tests/test_load_saturation.py::DriverSmokeTests::test_signing_scenario_real_openpgp_mode`
  is flaky in this environment (failed once, then passed eight
  consecutive reruns); it touches real OpenPGP signing and none of this
  card's code paths.

## Migration or rollback

No data changes and no schema changes. Rollback is reverting the two
commits on this branch. The `FileProposalLedger` state file is created
only where a caller passes a path; nothing in this slice writes one at
rest anywhere by default.
