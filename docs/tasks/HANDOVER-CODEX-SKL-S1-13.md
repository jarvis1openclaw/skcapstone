# Handover: Codex session for SKL-S1-13 (card 82f3ea82)

Date: 2026-08-21. Prepared by: kimi (analysis session).

## Your assignment

Claim and complete card `82f3ea82`:

**[SKL-S1-13][S] Vendor or mirror the capauth upstream dependency** (priority medium, sprint-1, leaf, size-s, supply-chain).

Outcome: the authorization core no longer depends on a single personal
GitHub repository pinned to one commit.

Scope (from the card):

- Mirror `github.com/smilinTux/capauth` under organizational control or
  vendor it into the repo with hash verification.
- Update the pin, SBOM, and pip-audit surrogate handling accordingly.
- Document the update path for future upstream revisions.

Acceptance criteria:

- Build no longer fetches capauth from the personal remote at install time.
- SBOM and vuln-scan scripts pass against the vendored or mirrored source.

## Startup (required by AGENTS.md)

```bash
"${CODEX_HOME:-$HOME/.codex}/bin/load-sk-agent-context.sh"
skcapstone coord status
skcapstone coord claim 82f3ea82 --agent codex-skl-s1-13
```

Read `docs/tasks/SUBAGENT-TASK-TTDS.md` and the repo `AGENTS.md` before
editing. Do not broaden the task.

## Where the dependency lives today

- `packages/capauth/pyproject.toml:12`:
  `capauth @ git+https://github.com/smilinTux/capauth.git@183c04a7c623e8abcf37bd705bf8bca1deb4a364`
- `uv.lock:147` and `uv.lock:1220`: same git pin
  (rev `183c04a7c623e8abcf37bd705bf8bca1deb4a364`).

## Coherence context (read before choosing mirror vs vendor)

- Card `d5457c69` (codex-skcapstone-reliability, in review, tagged
  merged-to-main) fixed `pip-audit --require-hashes` rejecting the exact
  git-pinned capauth requirement. Your vendored/mirrored solution must keep
  that surrogate path working; do not regress it.
- Card `fc814941` (SKL-S1-12, open, unclaimed) also touches
  `packages/capauth/` (hygiene fixes). If both run concurrently, stay
  inside dependency-manifest files (`pyproject.toml`, `uv.lock`, SBOM and
  scan scripts) and leave `packages/capauth/authorization.py` alone.
- Do not edit hash-pinned approval documents
  (`docs/approval/DESIGN-HASHES.sha256` pins the TDD family). Route any
  amendment through the approval trail.

## Working tree warning

The repo currently carries uncommitted work from a completed kimi swarm
(all eight cards are in board review). Do not revert, reformat, or commit
any of it. Notable dirty areas:

- `apps/web/` (new untracked frontend from SKL-S4-01)
- `services/worker/src/sklegal_worker/__init__.py` (SKL-S3-01 Temporal)
- `scripts/benchmark_capauth_hotpath.py`, `scripts/run_checks.sh`,
  `docs/development/CAPAUTH.md`, `pyproject.toml`, `uv.lock` (modified)
- `docs/evidence/**` (new evidence receipts, untracked)

Note: `pyproject.toml` and `uv.lock` are already modified by the swarm.
Before editing them, run `git diff pyproject.toml uv.lock` and preserve
those changes; your diff must be additive on top. If the review batch is
committed before you start, rebase your expectations onto the new HEAD.

Do not commit or push; the card does not request it.

## Tests and evidence

- Run `./scripts/run_checks.sh` before and after; it must stay green
  (SKL-S1-06 just repaired it; a red gate you did not cause is a blocker,
  report it, do not patch around it).
- Exercise the SBOM and vuln-scan scripts explicitly since they are named
  in the acceptance criteria: check `scripts/` for the SBOM
  (`build/sbom/`) and audit entry points.
- Completion evidence per AGENTS.md: files changed, tests and exact
  results, acceptance criteria evidence, known limitations, and a linked
  SKCapstone card update (`skcapstone coord describe` /
  `coord complete 82f3ea82` when done).

## Board rules that apply

- The claim-time dependency gate is live; this card has no dependencies
  and is claimable immediately.
- Use legal-domain vocabulary in anything user-facing. Never use em or en
  dashes in code, comments, docs, or card updates.
- Use `rg` for search. Fail closed on anything security-relevant.
