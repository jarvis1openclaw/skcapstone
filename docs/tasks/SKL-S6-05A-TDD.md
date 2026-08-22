# SKL-S6-05A task design

SKCapstone card: `8ddb737e`

## Objective

Provide a deterministic HammerTime workflow that assembles one immutable dev
candidate release manifest for the official drafting standards batch. The
workflow must consume only explicit, sealed evidence and must not use the
repository-wide rebuild path.

## Inputs

- Batch ID `2026-08-21-official-government-style-manuals`
- Finalized reference file list from the S6-03 completion evidence
- Archived source-rights review with exact source IDs and purpose limits
- S6-03 completion evidence and projection bindings
- S6-04 machine-readable profile and core-principles artifact hashes
- Exact normalized files and exact decomposition artifacts
- Pinned dev runtime alias snapshot before candidate assembly

Inputs must be passed as explicit paths below approved non-Inbox roots. A path
with an `Inbox` component fails before any content read.

## Outputs

Dry run returns a deterministic JSON plan containing:

- candidate release ID and dev target
- exact normalized source paths and hashes
- original source IDs and hashes
- exact decomposition paths and hashes
- profile and principles artifact hashes
- completion and rights evidence hashes
- existing vector and graph projection bindings
- pre-assembly runtime-alias snapshot hash
- typed blockers and a write set

Apply mode may write only
`json/releases/corpus-release-<release-id>.json`. It must use exclusive create
semantics and fail on an existing path. It does not update processing state,
decomposed state, retrieval stores, or runtime aliases.

## Deterministic validation

1. The file list contains exactly the 14 normalized batch paths and no
   duplicate, absolute, traversal, unrelated, or Inbox path.
2. The completion evidence is complete and reports the same source,
   decomposition, vector, and graph counts.
3. The rights review clears the same 14 source IDs for the recorded internal
   purpose and has an empty rights quarantine.
4. Every normalized artifact exists and its frontmatter source ID or source
   hash agrees with the profile.
5. Every normalized artifact has exactly one decomposition whose `source_file`
   is the normalized path and whose chunks are non-empty.
6. The S6-04 profile contains the same 14 unique source IDs, preserves explicit
   currentness, scope, and contradiction fields, and remains human-review
   required.
7. The S6-03 projection evidence reports the exact dev collection and graph
   bindings used by the manifest.
8. The runtime alias file is hashed but never changed by this workflow.

## Failure behavior

All input errors fail closed before a write. Apply mode assembles the complete
manifest in memory, opens the final path with exclusive creation, writes once,
flushes, and verifies the exact bytes. An interrupted or failed write must not
replace an existing manifest or change an alias.

## Tests

- valid dry run with zero changed files
- valid apply into a synthetic HammerTime root
- missing normalized artifact
- unrelated or duplicate file-list entry
- Inbox and traversal path rejection
- rights quarantine or source-set mismatch
- changed normalized source hash
- missing, duplicate, empty, or wrong-parent decomposition
- completion or projection count mismatch
- existing release collision
- runtime alias immutability after success and every failure

## Rollback

Dry run needs no rollback because it writes nothing. Before qualification or
promotion, an unreferenced candidate manifest created by apply mode can be
removed under the exact task evidence because no alias points to it. Once an
alias references a candidate, only HammerTime's guarded rollback command may
change runtime state.
