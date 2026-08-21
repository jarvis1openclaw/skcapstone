# HammerTime connector boundary

Read-only release and artifact adapter (`sklegal-hammertime`, card SKL-S2-01).

The adapter is the only SKLegal component allowed to construct HammerTime
paths. It wraps the documented HammerTime contract behind typed read-only
APIs:

- release manifests under `json/releases/corpus-release-<id>.json`
- runtime aliases in `json/state/runtime-aliases.json` (`current` and
  `previous` per environment target, with stale-release detection and
  verbatim drift reporting)
- decomposition artifacts under `json/decomposed/` and the sealed snapshot
  state in `json/state/decomposed-state.json`
- bounded artifact reads with SHA-256 pinning and source-hash verification
- authorization-first legacy matter resolution through
  `incidents/_incident-registry.md`,
  `PROBLEM.md` and `INCIDENT.md` frontmatter, validation reports, packet
  facts references, and owner-direction records

Hard boundaries:

- No write, move, delete, or dispatch surface exists in this package.
- `Inbox/` is never searched, read, or returned, in any casing.
- Matter-scoped reads require a configured matter authorizer and fail
  closed before registry or path discovery without an allow decision.
- Every path component is opened relative to a verified directory descriptor
  with `O_NOFOLLOW`; symlinked fixed paths and descriptor targets outside the
  configured root or inside `Inbox/` are denied.
- Every response pins the exact relative path, content SHA-256, and UTC
  observation time of the bytes read.

The governed ingestion bridge (write path through HammerTime's own intake
contract) belongs to a separate card and is not implemented here.
