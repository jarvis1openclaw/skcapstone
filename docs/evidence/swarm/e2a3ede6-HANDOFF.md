# HANDOFF: SKL-S3-05C blind challenge and release readiness gates

Card `e2a3ede6` (SKL-S3-05C, slice of SKL-S3-05 `d3514f35`).
Dependencies on the card: `b71d6d49` (SKL-S3-05B authority verification)
and `3e442852`. Both are merged in this branch and were used as committed.

Branch `swarm/e2a3ede6`. All work is inside this worktree. No push, pull,
remote operation, HammerTime access, external action, secret access, or
skcapstone board command was performed. jarvis owns board state and
completion.

## Files changed

- `packages/domain/src/sklegal_domain/release_gates.py` (new): the
  deterministic blind-challenge and release-readiness gate module.
  `ModelRunIdentity` (provider/name/revision triple; route and alias are
  trace-only), `label_challenge_independence` (INDEPENDENT / SAME_MODEL /
  NOT_BLIND), `BlindChallengeRecord`, `ChallengeDefectKind`,
  `ChallengeDefect`, `ChallengeOutcome`, `GateKind` (CLAIM_READY,
  DRAFT_READY, RELEASE_READY), 13 `GateCheckId` values, closed
  `GateFailureReason` vocabulary, `GateCheck`, `GateEvaluation` (a PASSED
  evaluation with a FAILED check is unrepresentable),
  `evaluate_claim_ready_gate`, `evaluate_draft_ready_gate`, and
  `evaluate_release_ready_gate`. Naive datetimes and inputs postdating the
  evaluation instant raise.
- `packages/domain/src/sklegal_domain/__init__.py` (modified): public
  exports for the new module, alphabetical `__all__` preserved.
- `tests/test_release_gates.py` (new): 61 tests across six suites
  (ModelRunIdentityTests, BlindChallengeRecordTests, ClaimReadyGateTests,
  DraftReadyGateTests, ReleaseReadyGateTests,
  ModelOutputCannotWaiveFailedGatesTests).
- `docs/evidence/domain/SKL-S3-05C-COMPLETION-EVIDENCE-2026-08-22.md`
  (new): completion evidence following the sibling pattern.
- `HANDOFF.md` (new, this file).

Commits: `dd60cec` (implementation and tests), `56adc76` (evidence), plus
this handoff commit.

## Tests and exact results

From the worktree root with
`UV_CACHE_DIR=$PWD/.tools/uv-cache /tmp/sklegal-uv/bin/uv` (this worktree
has no `.tools/bin/uv`; the shared `/tmp/sklegal-uv/bin/uv` is the same
tool):

```text
uv run --locked --package sklegal-domain --group dev pytest \
  tests/test_release_gates.py -q
Result: 61 passed in 0.16s

uv run --locked --package sklegal-domain --group dev pytest \
  tests/test_release_gates.py tests/test_authority_verification.py \
  tests/test_domain_entities.py -q
Result: 192 passed, 49 subtests passed in 0.24s

uv run --locked --package sklegal-persistence --group dev pytest \
  tests/test_claim_ledger.py -q
Result: 15 passed in 0.14s

uv run --locked --group dev mypy packages/domain
Result: Success: no issues found in 17 source files

uv run --locked --group dev ruff check packages/domain/src/sklegal_domain \
  tests/test_release_gates.py
Result: All checks passed

uv run --locked --group dev ruff format --check \
  packages/domain/src/sklegal_domain/release_gates.py \
  packages/domain/src/sklegal_domain/__init__.py \
  tests/test_release_gates.py
Result: all files already formatted

uv run --locked pytest tests/integration/test_domain_contract.py -q
Result: 4 passed, 35 subtests passed in 0.14s
```

Pre-existing environment failures, identical on unmodified HEAD
(documented in the SKL-S3-05B handoff), not caused by this change: the
full unit suite reports 8 failures in benchmark, clean-room,
load-saturation, and style-profile tests;
`tests/integration/test_foundation_contract.py` conflicts with already
running `sklegal-dev` compose containers; disposable-PostgreSQL
persistence contract tests time out on container readiness on this host.
This change touches no persistence or migration file.

## Acceptance criteria evidence

- No failed gate can be waived by model output:
  - Structurally, `test_gate_signatures_admit_only_domain_typed_records`
    walks every parameter of all three gate functions with
    `inspect.signature(eval_str=True)` and asserts each resolves to
    `datetime`, `uuid`, `None`, or a type defined in `sklegal_domain`. No
    waiver, assertion, note, or free payload parameter can be added
    without failing this test.
  - By construction,
    `test_passing_evaluation_with_a_failed_check_is_unrepresentable` and
    `test_failed_evaluation_without_failed_checks_is_unrepresentable`
    prove `GateEvaluation` validates outcome against its checks; the
    outcome is the fold of the checks.
  - Behaviorally,
    `test_a_no_defect_model_challenge_cannot_waive_failed_support`,
    `test_a_same_model_no_defect_challenge_still_fails`, and
    `test_clean_challenge_evidence_cannot_flip_a_failed_gate` prove no
    typed record the model produces can flip a failed check to passed.
- Same-model challenge labeling: identity is the normalized
  provider/name/revision triple and survives route/alias changes
  (`test_same_model_across_routes_and_aliases`); a same-model challenge is
  labeled `SAME_MODEL` even when blind (`test_same_model_challenge_is_labeled`),
  fails independence alone (`test_same_model_challenge_cannot_satisfy_independence`),
  and accumulates its reason with `NOT_BLIND`
  (`test_same_model_and_not_blind_reasons_are_reported_together`). A later
  independent no-defect challenge recovers the gate
  (`test_later_independent_no_defect_challenge_recovers_the_gate`) because
  records are append-only evidence and the gate reduces the set.
- Changed artifact after approval: digest change, approval bound to
  another artifact version, superseded version, and replaced current
  version each report `ARTIFACT_CHANGED_AFTER_APPROVAL`
  (`test_changed_artifact_after_approval_fails_the_gate`,
  `test_approval_for_another_version_is_a_changed_artifact`,
  `test_superseded_version_is_a_changed_artifact`,
  `test_replaced_current_version_is_a_changed_artifact`).
- The three gates: `ClaimReadyGateTests` (18 tests), `DraftReadyGateTests`
  (14 tests), and `ReleaseReadyGateTests` (13 tests) cover the happy paths
  (every check id asserted present), support verification failures,
  challenge failures, version state failures, approval validity failures,
  unknown-resolution blocking, scope crossing on every record type, claim
  regression, future-timestamp rejection, naive-datetime rejection, and
  deterministic re-evaluation.
- Fail closed: missing support, missing challenge, missing approval, and
  ungrounded drafts each fail with explicit reasons
  (`test_missing_authority_support_fails_closed`,
  `test_missing_challenge_fails_closed`, `test_missing_approval_fails_closed`,
  `test_draft_without_grounding_fails_closed`).

## Known limitations

- Deterministic domain layer only. The S4-03 and S4-04 orchestration that
  calls these gates, the retrieval-plane challenge runner that produces
  `BlindChallengeRecord` values, persistence mapping for the new records,
  and UI wiring belong to later slices.
- `BlindChallengeRecord` values are typed inputs; the orchestration that
  runs a challenger model and constructs the record (including recording
  whether the challenger saw the challenged conclusion) is out of scope
  here. The gate only reduces the records it is given.
- Same-model detection is by declared identity triple; a misdeclared
  deployment routing the same weights under a different revision string
  cannot be detected by this layer.
- The staleness rule is `issued_at >= claim.updated_at`; ordering within
  one clock reading is not distinguishable.

## Rollback

Revert the commits on this branch. No data, migration, or configuration
change requires rollback; the ledger and verification modules from
SKL-S3-05A and SKL-S3-05B are untouched.
