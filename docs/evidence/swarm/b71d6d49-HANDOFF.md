# HANDOFF: SKL-S3-05B authority status, applicability, quotation, contrary search

Card `b71d6d49` (SKL-S3-05B, slice of SKL-S3-05 `d3514f35`).
Dependencies on the card: `d1f389c1` (SKL-S3-05A claim ledger) and
`4c4ca6b0` (SKL-S3-04). Both are merged in this branch and were used as
committed.

Branch `swarm/b71d6d49`. All work is inside this worktree. No push, pull,
remote operation, HammerTime access, external action, secret access, or
skcapstone board command was performed. jarvis owns board state and
completion.

## Files changed

- `packages/domain/src/sklegal_domain/authority_verification.py` (new):
  the deterministic authority verification module. `normalize_jurisdiction`,
  `MatterAuthorityScope`, `AuthorityQuotation`,
  `verify_authority_quotation` (document digest, span bounds, excerpt
  digest, and text equality, all failures reported together),
  `AuthorityCitation`, `evaluate_authority_citation` (binding, jurisdiction,
  scope, status, and effective-interval checks), `RetrievalPlaneBinding`
  (requires the qualification verdict digest), `ContrarySearchCandidate`,
  `run_contrary_authority_search`, `ContraryAuthoritySearchResult`
  (dispositions: qualified lead, similarity only, not applicable),
  `verify_authority_support` (eleven deterministic claim-level checks),
  and their closed failure-reason vocabulary.
- `packages/domain/src/sklegal_domain/__init__.py` (modified): public
  exports for the new module, alphabetical `__all__` preserved.
- `tests/test_authority_verification.py` (new): 55 tests.
- `docs/evidence/domain/SKL-S3-05B-COMPLETION-EVIDENCE-2026-08-22.md`
  (new): completion evidence following the sibling pattern.

Commits: `f0c09f7` (implementation and tests), `d5631dd` (evidence).

## Tests and exact results

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
Result: 3 files already formatted

uv run --locked pytest tests/integration/test_domain_contract.py -q
Result: 4 passed, 35 subtests passed in 0.13s
```

Pre-existing environment failures, identical on unmodified HEAD (verified
with `git stash`), not caused by this change:

- Full unit suite: 8 failures in `test_audit_chain_head_benchmark`,
  `test_clean_room_check` (Landlock allowlist),
  `test_load_postgres_saturation`, `test_load_saturation` (3), and
  `test_official_drafting_style_profiles` (GPO-manual fixture inputs).
- `tests/integration/test_foundation_contract.py`: one failure because two
  `sklegal-dev` compose containers are already running on this host.
- Disposable-PostgreSQL persistence contract tests time out on container
  readiness in this host environment; this change touches no persistence
  or migration file.

## Acceptance criteria evidence

- Similarity score alone never qualifies authority:
  `SimilarityNeverQualifiesTests` proves a 1.0-similarity candidate with no
  recorded factors is dispositioned `similarity_only`, while a
  0.0-similarity candidate with complete factors becomes the qualified
  lead. Structurally, `_contrary_candidate_disposition` receives only
  applicability factors and never the similarity field. An unresolved
  similarity-only lead fails the `contrary.leads_reviewed` check, so the
  gate fails closed.
- Wrong jurisdiction: authority from the wrong jurisdiction fails the
  citation check, a contrary candidate from the wrong jurisdiction is
  `not_applicable`, and the claim-level gate fails with
  `wrong_jurisdiction`.
- Stale authority: superseded, proposed, challenged, and not-applicable
  statuses each fail the status check; expired and unknown effective
  intervals each fail the effective-interval check.
- Quotation mismatch: changed source bytes, wrong expected digest,
  out-of-bounds span, excerpt digest mismatch, quoted-text mismatch,
  unpinned source, missing and unverified quotations each fail.
- Missing remedy: absent remedy, remedy without authority support, and
  remedy crossing claim scope each fail the gate.

## Known limitations

- Deterministic verification layer only: `CLAIM_READY`, `DRAFT_READY`, and
  `RELEASE_READY` gate wiring, blind challenge, and persistence mapping
  for the new value objects belong to sibling slices of SKL-S3-05.
- Contrary-search candidates are typed inputs; the adapter that runs a
  query through the qualified retrieval plane to produce them belongs to
  the retrieval integration slice.
- Human review of contrary leads is required but recorded out of band
  here; the gate fails closed until leads are resolved.
- Jurisdiction matching is normalized string equality; jurisdictional
  hierarchy depth is future scope.

## Rollback

Revert the two commits. No data, migration, or configuration change
requires rollback; the ledger tables from SKL-S3-05A are untouched.
