# SKL-S3-05C completion evidence

Date: 2026-08-22

Board card: `e2a3ede6` (SKL-S3-05C, slice of SKL-S3-05, card `d3514f35`;
dependencies `b71d6d49` SKL-S3-05B and `3e442852` complete and merged in this
branch)

## Delivered

- `packages/domain/src/sklegal_domain/release_gates.py`: the deterministic
  blind-challenge and release-readiness gate module over the SKL-S3-05A claim
  ledger and SKL-S3-05B authority verification. No model calls, no I/O, and no
  retrieval-package imports.
  - `ModelRunIdentity`: provider, model name, and model revision triple with
    casefolded comparison. Route ID and served alias are carried as trace
    metadata only; the same model reached through a different route or alias
    is still the same model for independence purposes.
  - `label_challenge_independence`: labels a challenge `INDEPENDENT`,
    `SAME_MODEL`, or `NOT_BLIND` from the record alone. A same-model
    challenge is labeled `SAME_MODEL` even when it was blind.
  - `BlindChallengeRecord`: the typed record of one challenge against one
    claim, with the challenger identity, whether the challenger saw the
    challenged conclusion (blindness), the outcome, and the defect list.
  - `GateKind` (`CLAIM_READY`, `DRAFT_READY`, `RELEASE_READY`), 13
    `GateCheckId` values, a closed `GateFailureReason` vocabulary,
    `GateCheck` (outcome and reasons are validated for consistency), and
    `GateEvaluation` (a PASSED evaluation containing a failed check cannot be
    constructed; the constructor raises).
  - `evaluate_claim_ready_gate`: support verification present, passed, and
    current (at or after the claim's last update, not in the future relative
    to the evaluation instant); claim status supported; challenge executed
    for this claim, current, independent (same-model and not-blind reasons
    accumulate together), and free of defects. A later independent no-defect
    challenge recovers the gate: records are append-only evidence and the
    gate reduces the set.
  - `evaluate_draft_ready_gate`: one claim-readiness check per grounding
    claim (passed or failed, never omitted), version frozen, current, and
    bound to the work product, and unresolved blocking unknowns absent
    (resolved unknowns do not block).
  - `evaluate_release_ready_gate`: work product approved with the approval
    linked, approval valid (present, approved, not revoked, in scope, decided
    at or before the evaluation instant), the approved artifact unchanged
    (digest, version binding, superseded version, and replaced current
    version all report `ARTIFACT_CHANGED_AFTER_APPROVAL`), and all grounding
    claims claim-ready.
  - Every gate rejects naive datetimes and raises on verification or
    challenge timestamps that postdate the evaluation instant.
- `packages/domain/src/sklegal_domain/__init__.py`: public exports for the
  new module, keeping `__all__` alphabetically ordered after
  `PACKAGE_NAME`.
- `tests/test_release_gates.py`: 61 tests across six suites (model identity,
  blind challenge records, claim gate, draft gate, release gate, and
  model-output-cannot-waive).

## Acceptance evidence

| Requirement | Evidence |
|---|---|
| No failed gate can be waived by model output | `ModelOutputCannotWaiveFailedGatesTests`. Structurally: every parameter of every gate function resolves to `datetime`, `uuid`, `None`, or types defined in `sklegal_domain` (`test_gate_signatures_admit_only_domain_typed_records`); no waiver, assertion, note, or payload parameter exists. By construction: a PASSED `GateEvaluation` containing a FAILED check cannot be constructed (`test_passing_evaluation_with_a_failed_check_is_unrepresentable`; the converse `test_failed_evaluation_without_failed_checks_is_unrepresentable` also raises). Behaviorally: `test_a_no_defect_model_challenge_cannot_waive_failed_support`, `test_a_same_model_no_defect_challenge_still_fails`, `test_clean_challenge_evidence_cannot_flip_a_failed_gate`. |
| Same-model challenge labeling | `ModelRunIdentityTests` (4 tests): identity is the normalized provider/name/revision triple; route and alias changes do not change identity (`test_same_model_across_routes_and_aliases`). `BlindChallengeRecordTests.test_same_model_challenge_is_labeled`, `test_not_blind_challenge_is_labeled`, `test_independent_challenge_is_labeled`. `ClaimReadyGateTests.test_same_model_challenge_cannot_satisfy_independence`, `test_same_model_and_not_blind_reasons_are_reported_together`, `test_later_independent_no_defect_challenge_recovers_the_gate`. |
| Changed artifact after approval | `ReleaseReadyGateTests.test_changed_artifact_after_approval_fails_the_gate`, `test_superseded_version_is_a_changed_artifact`, `test_replaced_current_version_is_a_changed_artifact`, `test_approval_for_another_version_is_a_changed_artifact`. |
| CLAIM_READY gate | `ClaimReadyGateTests` (18 tests): happy path checks every expected check id (`test_supported_challenged_claim_passes_every_check`); missing, failed, and stale support verification fail closed; scope crossing on verification and challenge; unsupported claim status; missing, stale, scoped, same-model, not-blind, and defect-bearing challenges all fail with distinct reasons; recovery through a later independent challenge; future challenge and verification timestamps raise (`test_future_challenge_is_rejected`, `test_future_support_verification_is_rejected`). |
| DRAFT_READY gate | `DraftReadyGateTests` (14 tests): frozen grounded draft passes every check; one check per grounding claim passed or failed; unfrozen, non-current, and scope-crossing versions fail; an open or bracketed unknown blocks while a resolved one does not (`test_open_bracketed_unknown_blocks_the_draft`); non-CLAIM_READY gate inputs and duplicate claim gates raise. |
| RELEASE_READY gate | `ReleaseReadyGateTests` (13 tests): approved work product with valid approval and unchanged artifact passes; unapproved work product, missing linkage, missing/revoked/future/scope-crossing approval, all changed-artifact shapes, and claim regression fail. |
| Fail closed | `test_missing_authority_support_fails_closed`, `test_missing_challenge_fails_closed`, `test_missing_approval_fails_closed`, `test_draft_without_grounding_fails_closed`, plus every scope-crossing test above. |

## Verification results

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

Boundary conditions observed in this environment, unrelated to this
change (documented in the SKL-S3-05B handoff, verified identical on the
unmodified HEAD): the full unit suite reports 8 pre-existing failures in
benchmark, clean-room, load-saturation, and style-profile tests;
`tests/integration/test_foundation_contract.py` conflicts with already
running `sklegal-dev` compose containers; disposable-PostgreSQL
persistence contract tests time out on readiness on this host.

## Known limitations

- This slice is the deterministic gate layer only. The S4-03 and S4-04
  orchestration that calls these gates, the retrieval-plane challenge
  runner that produces `BlindChallengeRecord` values, persistence mapping
  for the new records, and UI wiring belong to later slices.
- `BlindChallengeRecord` values are typed inputs. The orchestration that
  runs a challenger model and constructs the record (including recording
  whether the challenger saw the challenged conclusion) is out of scope
  here; the gate only reduces the records it is given.
- Same-model detection is by declared identity triple. A misdeclared
  deployment that routes the same weights under a different revision string
  cannot be detected by this layer.
- The staleness rule is `issued_at >= claim.updated_at`. A challenge issued
  between two claim updates within the same clock reading is not
  distinguishable; sub-timestamp ordering is future scope.

## Rollback

The change adds one new domain module, one new test module, and export
lines in the domain package `__init__.py`. Reverting the commits restores
the previous behavior; no data, migration, or configuration change
requires rollback.
