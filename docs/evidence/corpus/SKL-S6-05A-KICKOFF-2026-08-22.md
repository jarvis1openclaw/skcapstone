# SKL-S6-05A kickoff evidence

Date: 2026-08-22

SKCapstone card: `8ddb737e`

Parent qualification card: `0ad49216`

## Outcome

A separate dependency-eligible workflow card now owns the batch-scoped
HammerTime candidate release builder required by S6-05. This avoids the
repository-wide rebuild command, which is unsafe while the HammerTime worktree
contains extensive unrelated source changes.

The builder accepts only explicit sealed evidence, rejects every exact `Inbox`
path component before reading, and defaults to dry run. Apply mode can create
only one new immutable dev release manifest with exclusive-create semantics. It
does not update processing state, decomposed state, retrieval stores, or runtime
aliases.

## Files changed

SKLegal:

- `docs/tasks/SUBAGENT-TASK-TTDS.md`
- `docs/tasks/SKL-S6-05A-TDD.md`
- `docs/evidence/corpus/SKL-S6-05A-KICKOFF-2026-08-22.md`

HammerTime:

- `scripts/build-batch-candidate-release.py`
- `tests/test_build_batch_candidate_release.py`

## Tests and exact results

Command:

```text
python3 -m pytest -q tests/test_build_batch_candidate_release.py tests/test_acquire_official_style_manuals.py tests/test_build_official_style_manual_routing.py
```

Result:

```text
22 passed in 0.14s
```

Command:

```text
python3 -m py_compile scripts/build-batch-candidate-release.py tests/test_build_batch_candidate_release.py
```

Result: exit 0.

The new test module covers dry-run no-write behavior, exclusive apply, existing
release collision, Inbox rejection, unrelated file rejection, changed source
hash, rights quarantine, wrong decomposition parent, projection-count mismatch,
and alias immutability.

An additional run including `scripts/test_corpus_release.py` reached the prior
28 passing checks but remained in host I/O for 2 minutes 50 seconds while
reading the large OneDrive-backed corpus. The test process was terminated and
classified as an infrastructure-limited broad scan. No failed assertion was
reported and no state hash changed.

## Live 14-source dry run

Candidate release ID:

```text
dev-20260822-official-drafting-standards-candidate-1
```

Result:

```json
{
  "status": "ready",
  "dry_run": true,
  "source_count": 14,
  "decomposition_count": 14,
  "manifest_sha256": "2ee914c26cf93e61138416c84b25d8e18dd5ee9ab7e1921b204d9b9176bbf752",
  "actual_write_set": []
}
```

Before and after hashes were identical:

- Runtime aliases: `59381d410c53f9a33f86739b39661ef468a35e4cd15140369c533a4eb9123984`
- Corpus processing state: `9bcc4e8b5b4f1171bc00ed17b01a59c154a14fbf15110994aca5907c323d8c6c`
- Decomposed state: `ac9a33acdeecd04ade43decdacc7e6ce4d00aad8825cf891180042784596500d`
- Release-directory listing: `500dab7bcd5cb99cb98c0fffd1fa55feca496e7c8c05dbd6273e0e80aae9316c`

The planned manifest path does not exist. No release or alias mutation occurred.

## Acceptance evidence

1. Dry run used the exact finalized 14-path file list and wrote nothing.
2. All 14 source IDs matched the rights-cleared profile set.
3. Every normalized file matched its original source hash and had exactly one
   non-empty decomposition with the correct parent path.
4. S6-03 completion counts and vector and graph bindings matched the candidate.
5. The S6-04 profile and core-principles artifacts were hashed into the planned
   manifest evidence.
6. The existing dev current and previous bindings were pinned by the alias file
   hash and remained unchanged.

## Known limitations and next gate

- Apply mode passed synthetic tests and was used once on the live HammerTime
  root after a fresh dry run reproduced the reviewed manifest hash.
- Creating the candidate manifest does not authorize dev promotion. Deep health,
  scoped retrieval, secondary Qwen3.8 review, alias-safe promotion, and rollback
  remain separate parent-card gates.

## Live immutable candidate apply

The fresh pre-apply dry run reproduced manifest SHA-256
`2ee914c26cf93e61138416c84b25d8e18dd5ee9ab7e1921b204d9b9176bbf752`.
The candidate path was absent before apply.

Apply created exactly:

```text
json/releases/corpus-release-dev-20260822-official-drafting-standards-candidate-1.json
```

The written file hash exactly matched the dry-run plan:

```text
2ee914c26cf93e61138416c84b25d8e18dd5ee9ab7e1921b204d9b9176bbf752
```

Protected state remained byte-identical after apply:

- Runtime aliases: `59381d410c53f9a33f86739b39661ef468a35e4cd15140369c533a4eb9123984`
- Corpus processing state: `9bcc4e8b5b4f1171bc00ed17b01a59c154a14fbf15110994aca5907c323d8c6c`
- Decomposed state: `ac9a33acdeecd04ade43decdacc7e6ce4d00aad8825cf891180042784596500d`

Post-apply boundary verification:

```text
python3 -m pytest -q tests/test_build_batch_candidate_release.py tests/test_acquire_official_style_manuals.py tests/test_build_official_style_manual_routing.py
22 passed in 0.17s

.venv/bin/pytest -q tests/test_official_drafting_release_qualification.py
14 passed in 0.75s
```

The first two SKLegal invocations used the system interpreter and stopped at
collection because workspace packages were not installed there. The canonical
workspace virtual environment completed all 14 tests with no failures.

## Commit, tag, and publication status

HammerTime local `main` contains:

- `b7f30d8b8`: deterministic builder and focused tests;
- `e7f1e5f09`: immutable candidate manifest; and
- annotated tag `v2026.08.22.001` at `e7f1e5f09`.

The task branches were fast-forward merged and deleted. SKGit publication is
pending because this agent has no accepted Forgejo SSH key or HTTPS credential.
The SKGit host presented RSA fingerprint
`SHA256:532y4a4fN0pO0b+1VDCBk9eKzpxV3eB7UIolTgC13ik`; host verification passed,
then authentication failed closed before any remote update.

## Rollback

The candidate remains unreferenced because the runtime alias hash did not
change. It can therefore be removed under exact task evidence if parent
qualification rejects it. Once referenced, rollback must use HammerTime's
guarded rollback command and preserve current and previous bindings.
