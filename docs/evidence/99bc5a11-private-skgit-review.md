# Private SKGit observation and review source evidence

Date: 2026-09-20. Producer: Jarvis. Card: `99bc5a11`.
Base: `fd7e46f2b72882bbfb07df91b08cc5fb60263e1e`.

## Change and acceptance

The Link service templates now include SKLegal on its exact private forge while
preserving the four GitHub repositories. A confined standard-library transport
provides both lineage discovery and feed observations. Credentials remain in the
producer process. Explicit forge, repository, PR and commit bindings prevent
cross-forge or stale review reuse. Failure preserves the previous healthy feed.

The historical Seraph publisher at
`e85dd83380ec1bf6561185ed251312a4f73b7c3e` was recovered and strengthened with
current CardStore outcomes, independent artifact hashes, exact candidate and
actor bindings, sibling review resolution, and mediated receipt writes. The new
Forgejo adapter implements exact-commit approval and readback. Only Seraph gains
the narrow `PUBLISH_REVIEW` action. Operator contract:
[`seraph-private-forge-review.md`](../fleet/seraph-private-forge-review.md).

## Verification performed

```bash
PYTHONPATH=src /home/skuser01/.skenv/bin/python -m pytest -q \
  tests/test_forgejo.py tests/test_link_lineage.py \
  tests/test_link_forge_lineage.py tests/test_link_observation_producer.py \
  tests/test_link_observation_cycle.py tests/test_link_observation_feed.py \
  tests/test_link_cycle.py tests/test_seraph_review_publisher.py \
  tests/test_seraph_forgejo.py tests/fleet/test_seat_boundaries.py
```

Result: **291 passed in 1.81s**, Python 3.12.3, pytest 9.1.1.
Black and Ruff passed for all 15 changed Python files. `git diff --check` passed.
Documentation validator tiers 1, 2 and 3 passed using the exact workflow-pinned
sk-standards revision `ffa53c20d7e8a479e08f9c636ea99164e6aa0f8e`.
Hosted checks are separate and must qualify the published commit.

The independent reviewer reproduced the candidate checks, adding
`tests/test_link_merge_authority.py` and `tests/test_link_review_work.py` to the
same command: **349 passed in 3.11s**, technical source PASS. Findings repaired
before that decision: conflicting source commit
pins, boolean PR identifiers, independent evidence hash aliases, effective sibling
review outcomes, conflicting remote review rows, and exact receipt review-ID replay.

New Python files are below 500 lines. The existing `seat_boundaries.py` was already
538 lines and grows to 539 for the new action; no unrelated split is included.

## Actual read-only probes

The remote SKGit credential stayed on its existing host and was never printed or
copied into source. A probe read the actual three repair PRs, required check
statuses, authoritative reviewer candidates, and current card lineage. The normal
feed loader accepted all three records: healthy, zero exclusions, zero unresolved
records. Neither installed feed nor service configuration was changed.

Probe result: `/tmp/sklegal-private-feed-probe-result.json`.
SHA-256: `857a59d4d4097fd27236e66a2fb95b58bb993f7314e2f48ed2164fe12da3ac30`.
This snapshot predates documentation PR 5 and does not claim its review is complete.

| PR | Producer | Completed review | Exact reviewed commit |
| --- | --- | --- | --- |
| 2 | b268af2b | 714f9f62 | 216bd59b3562d078e38a45cb3cf6c41024ac5dc9 |
| 3 | 13975465 | c49f1254 | 1c2a989028d16dd8728d54ed32b7e5d60f8f66db |
| 4 | 4a0fc8c8 | 7e01d8ac | a8ad276ee1858c90f12cfff40898e17050284a27 |

Actual `LiveCardStoreGateway` and evidence hashing accepted all three pairs with
native producer `PASS_FOR_REVIEW`, completed independent Seraph `PASS`, exact
current revisions and independently recorded evidence bytes. No synthetic
authorization response was used for this probe.

Preflight result: `/tmp/sklegal-private-publisher-preflight.json`.
SHA-256: `feab3ee5f916c0ca022a50a5c45e5779e6e6d419a268cba9f56caad394085bfe`.

Live protected-main API readback confirmed the required status context,
one approval, protection applying to administrators, stale approval dismissal,
outdated/rejected review blocking, and disabled direct pushes. The actual
`git/commits/<sha>` API returned PR 2's exact SHA and main as its parent.

## Remaining activation work

No real independent forge credential, request-bound CapAuth verifier, or qualified
credential-scope attestor is configured for this publisher. Source qualification
does not install these bindings. The preflight CLI therefore stays blocked for
publication, and no approval was posted. Existing producer credentials cannot
approve the producer's PRs. No new external account has been created.

Composition card `03c9fb71` remains held until actual independent forge approvals
are read back for PRs 2, 3 and 4. A combined candidate then needs fresh tests and
independent review. No application or workflow deployment occurred.

## Migration and rollback

No data migration or installed service change. Revert the bounded source PR to
roll back code. Future activation must snapshot its executable and service bytes,
and preserve any remote approval and immutable receipt when rolled back.
