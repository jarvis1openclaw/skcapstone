# SKL-S3-05B completion evidence

Date: 2026-08-22

Board card: `b71d6d49` (slice of SKL-S3-05, card `d3514f35`; dependencies
`d1f389c1` SKL-S3-05A and `4c4ca6b0` SKL-S3-04 complete and merged in this
branch)

## Delivered

- `packages/domain/src/sklegal_domain/authority_verification.py`: the
  deterministic authority verification module over the SKL-S3-05A claim
  ledger. No model calls, no I/O, and no retrieval-package imports.
  - `normalize_jurisdiction`: whitespace-collapsing casefold so jurisdiction
    equality is exact text equality after normalization.
  - `MatterAuthorityScope`: the jurisdiction plus topical scope keys an
    authority must satisfy.
  - `AuthorityQuotation` / `QuotationVerification` /
    `verify_authority_quotation`: exact quotation verification against the
    pinned source version. The document SHA-256 proves the supplied text is
    the pinned source, the span bounds the excerpt, the excerpt SHA-256
    proves the exact bytes, and the text comparison proves the quoted
    wording. Every failing condition is reported together, never reduced.
  - `AuthorityCitation` / `evaluate_authority_citation`: per-citation checks
    `authority.binding` (tenant, matter, and quotation-to-authority
    binding), `authority.jurisdiction`, `authority.scope` (every matter
    scope key asserted), `authority.status` (proposed, challenged, not
    applicable, and superseded authorities all fail), and
    `authority.effective_interval` (an unknown interval cannot establish
    currency; an expired interval is stale).
  - `RetrievalPlaneBinding`: the qualified retrieval plane a contrary search
    ran over. The qualification verdict digest is required, so an
    unqualified plane cannot be expressed as an input at all.
  - `ContrarySearchCandidate` / `run_contrary_authority_search` /
    `ContraryAuthoritySearchResult`: contrary-authority search over the
    qualified plane. Each candidate is dispositioned to `qualified_lead`,
    `similarity_only`, or `not_applicable` from recorded factors only.
    Similarity is carried as ranking trace metadata and is not an input to
    the disposition function.
  - `verify_authority_support`: the claim-level gate. Eleven deterministic
    checks: citation present, per-citation applicability and status, exact
    quotation verified and pinned to the authority source, contrary search
    executed over a qualified plane, contrary leads reviewed (both qualified
    and similarity-only leads require human review before the gate passes),
    remedy present, and remedy authority support. The outcome is reduced
    from the checks alone; no model output is an input.
- `packages/domain/src/sklegal_domain/__init__.py`: public exports for the
  new module, keeping `__all__` alphabetically ordered after
  `PACKAGE_NAME`.
- `tests/test_authority_verification.py`: 55 tests across six suites
  (jurisdiction normalization, quotation verification, citation evaluation,
  contrary search, similarity-never-qualifies, support verification).

## Acceptance evidence

| Requirement | Evidence |
|---|---|
| Similarity score alone never qualifies authority | `SimilarityNeverQualifiesTests`: a 1.0-similarity candidate with no recorded factors is dispositioned `similarity_only`; a 0.0-similarity candidate with complete factors becomes the qualified lead. `_contrary_candidate_disposition` receives only applicability factors, so similarity is structurally unreachable from the decision path. An unresolved similarity-only lead fails the `contrary.leads_reviewed` check. |
| Wrong jurisdiction | `CitationEvaluationTests.test_wrong_jurisdiction_fails`, `ContraryAuthoritySearchTests.test_wrong_jurisdiction_candidate_is_not_applicable`, `AuthoritySupportVerificationTests.test_wrong_jurisdiction_fails_the_gate` |
| Stale authority | `test_superseded_authority_fails_status_check`, `test_proposed_authority_is_not_verified`, `test_challenged_authority_requires_review`, `test_authority_marked_not_applicable_fails`, `test_expired_authority_is_stale`, `test_unknown_effective_interval_cannot_establish_currency`, `test_superseded_candidate_is_not_applicable`, `test_stale_superseded_authority_fails_the_gate`, `test_expired_authority_fails_the_gate` |
| Quotation mismatch | `test_changed_source_bytes_fail_the_document_digest`, `test_wrong_expected_digest_fails_even_with_exact_text`, `test_span_beyond_the_document_is_out_of_bounds`, `test_excerpt_digest_mismatch`, `test_quoted_text_mismatch_alone_fails`, `test_quotation_mismatch_fails_the_gate`, `test_quotation_unpinned_to_authority_source_fails`, `test_failed_quotation_verification_is_a_mismatch`, `test_missing_quotation_fails`, `test_unverified_quotation_fails` |
| Missing remedy | `test_missing_remedy_fails_the_gate`, `test_remedy_without_authority_support_fails_the_gate`, `test_remedy_from_another_claim_crosses_scope` |
| Fail closed | `test_missing_contrary_search_fails_closed`, `test_qualified_contrary_lead_still_requires_review`, `test_no_authority_citation_fails_the_gate`, `test_passed_verification_cannot_hide_a_failed_check` |

## Verification results

From the worktree root with
`UV_CACHE_DIR=$PWD/.tools/uv-cache /tmp/sklegal-uv/bin/uv` (this worktree
has no `.tools/bin/uv`; the shared `/tmp/sklegal-uv/bin/uv` is the same
tool):

```text
uv run --locked --package sklegal-domain --group dev pytest \
  tests/test_authority_verification.py -q
Result: 55 passed in 0.14s

uv run --locked --package sklegal-domain --group dev pytest \
  tests/test_authority_verification.py tests/test_domain_entities.py \
  tests/test_claim_ledger.py -q
Result: 146 passed, 49 subtests passed in 0.23s

uv run --locked --group dev mypy packages/domain
Result: Success: no issues found in 16 source files

uv run --locked --group dev ruff check packages/domain/src/sklegal_domain \
  tests/test_authority_verification.py
Result: All checks passed

uv run --locked --group dev ruff format --check \
  packages/domain/src/sklegal_domain tests/test_authority_verification.py
Result: all files already formatted

uv run --locked pytest tests/integration/test_domain_contract.py -q
Result: 4 passed, 35 subtests passed in 0.13s
```

Boundary conditions observed in this environment, unrelated to this
change (verified identical on the unmodified HEAD via `git stash`):

- The full unit suite reports 8 failures (`test_audit_chain_head_benchmark`,
  `test_clean_room_check` landlock allowlist, `test_load_postgres_saturation`,
  3 in `test_load_saturation`, `test_official_drafting_style_profiles`).
  The same 8 fail on clean HEAD; they involve benchmarks, load drivers,
  Landlock, and GPO-manual fixture inputs.
- `tests/integration/test_foundation_contract.py` one failure: two
  `sklegal-dev` compose containers are already running on this host.
- Disposable-PostgreSQL persistence contract tests time out on readiness in
  this host environment.

## Known limitations

- This slice is the deterministic verification layer only. The
  `CLAIM_READY`, `DRAFT_READY`, and `RELEASE_READY` gate wiring, blind
  challenge, persistence, and persistence mapping for the new value objects
  belong to sibling slices of SKL-S3-05.
- Contrary search candidates are typed inputs. The adapter that runs an
  actual query through the qualified retrieval plane and produces
  `ContrarySearchCandidate` records belongs to the retrieval integration
  slice.
- Human review of qualified and similarity-only contrary leads is required
  but not automated here; the gate fails closed until leads are resolved by
  re-running the search without them or recording review evidence.
- Jurisdiction matching is normalized string equality. Deeper
  jurisdictional hierarchy (for example circuit within federal) is future
  scope.

## Rollback

The change adds one new domain module, one new test module, and export
lines in the domain package `__init__.py`. Reverting the two commits
restores the previous behavior; no data, migration, or configuration
change requires rollback.
