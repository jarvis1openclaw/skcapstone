# Amendment record: local analyst model naming correction

Amendment ID: `AMENDMENT-SKL-MODEL-NAMING`
Cards: flagged by `c976908e` (`SKL-S1-10`)
Record prepared by: `kimi` (validation session)
Status: approved

## Baseline

The approved documents named the initial local corpus analyst `Qwen3.8`.
Card `c976908e` flagged that `Qwen3.8` is not a real Qwen model identifier
and that the intended deployment is the local Qwen3 route operated by
HammerTime on chiap08. Because the approved documents are hash-pinned, the
correction required an explicit owner decision and a re-pin.

## Human decision

2026-08-21: the human owner approved the correction in this revision.

## Amendment

The token `Qwen3.8` was replaced with `Qwen3` in the four approved documents
that carried it:

- `docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md` (four occurrences)
- `docs/architecture/LIBERTY-AUTO-PILOT-TDD.md` (one occurrence, applied
  together with the approved SKL-S2-09 provenance path correction)
- `docs/planning/EPIC-SPRINT-PLAN.md` (one occurrence)
- `docs/approval/index.html` (two occurrences)

The same correction was applied to the living, non-pinned documents
`README.md`, `SOP.md`, and `AGENTS.md`. Historical snapshots under
`docs/legacy/` intentionally keep the original wording.

## Approval trail effect

- `docs/approval/DESIGN-HASHES.sha256` was re-pinned; the current hashes are
  recorded in `ARCHITECTURE-APPROVAL.md` and `AMENDMENT-SKL-S2-10.md`.
- No scope, boundary, or provider-route meaning changed; this is a naming
  correction only.
