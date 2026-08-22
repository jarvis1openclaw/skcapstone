# Handover: Codex session for SKL-S1-12 (card fc814941)

Date: 2026-08-21. Prepared by: kimi (validation session). Supersedes the
earlier S1-13 handover: card 82f3ea82 was completed by the wave-3 swarm
(capauth vendored at vendor/capauth, hash-enforced by
scripts/check_vendor_capauth.py, committed in de9482d). Do not touch S1-13.

## Your assignment

Claim and complete card `fc814941`:

**[SKL-S1-12][S] CapAuth package hygiene: public child issuance, single
attenuation rule, env-file safety** (priority medium, sprint-1, leaf,
size-s, capauth).

Scope (from the card, with verified current locations):

- `packages/capauth/src/sklegal_capauth/authorization.py`: the private
  reach-through `self._issuer._issue_child(...)` is at line 804. Expose
  child issuance as a public CapabilityIssuer API (or injected port)
  instead. Note: the card text says `packages/capauth/authorization.py`;
  the real path includes `src/sklegal_capauth/`.
- Deduplicate the attenuation logic shared by `_is_monotonic`
  (authorization.py:558) and `_is_monotonic_request` (authorization.py:814)
  into one function, with the principal-type allowance table defined once.
- Deploy config: `deploy/chiap01/compose.sklegal.yml:9` references
  `./postgres.env`, which does not exist. Add `postgres.env.example`, and
  add a `*.env` gitignore rule (root `.gitignore` currently has `.env` and
  `.env.*` but that does not catch `postgres.env`). Also drop the redundant
  function-local `import json` at
  `packages/capauth/src/sklegal_capauth/postgres.py:41` (module-level import
  already at line 5).

Acceptance criteria:

- No private-member access to `_issue_child` remains.
- Attenuation rules exist in exactly one shared implementation.
- `compose.sklegal.yml` works from a documented env example and `*.env`
  cannot be committed.

## Startup (required by AGENTS.md)

```bash
"${CODEX_HOME:-$HOME/.codex}/bin/load-sk-agent-context.sh"
skcapstone coord status
skcapstone coord claim fc814941 --agent codex-skl-s1-12
```

Read the repo `AGENTS.md` before editing. Do not broaden the task.

## Repo state warnings (verified 2026-08-21 ~22:00 UTC)

- HEAD is moving fast: a second swarm (wave 2/3, branches `swarm2/*`,
  merged by jarvis) is actively committing. Current HEAD was `d739d29`
  at handover time. `git pull` / re-check `git log --oneline -5` before
  starting and before finishing.
- Uncommitted in the working tree: the in-flight S2-04 retrieval package
  (`packages/retrieval/src/sklegal_retrieval/*`,
  `tests/test_retrieval_*.py`, owner codex-skl-s2-04). Do not touch,
  revert, format, or commit those files.
- The capauth dependency is now vendored (`vendor/capauth`,
  `capauth==0.3.1` pinned in `packages/capauth/pyproject.toml`). Your
  changes are to SKLegal's own `sklegal_capauth` package, not the vendor
  tree. Never edit `vendor/capauth` (hash-enforced).
- Do not edit hash-pinned approval documents
  (`docs/approval/DESIGN-HASHES.sha256` covers the TDD family).

## Tests and evidence

- Run `./scripts/run_checks.sh all` before and after; it is currently
  green (verified exit 0 at handover). The unit gate auto-discovers all
  `tests/test_*.py`, so any new test module you add runs automatically.
  Note: the suite now includes a Docker-dependent smoke test
  (tests/test_audit_chain_head_benchmark.py).
- Targeted runs while iterating:
  `.tools/bin/uv run --locked python -m unittest tests.test_capauth_authorization tests.test_capauth_delegation tests.test_capauth_boundaries`
- Completion evidence per AGENTS.md: files changed, tests and exact
  results, acceptance-criteria evidence, known limitations, and a linked
  card update (`skcapstone coord describe` then move the card to review).
  Do not commit or push; the card does not request it.

## Board rules that apply

- Claim-time dependency gate is live; this card has no dependencies.
- Never use em or en dashes in code, comments, docs, or card updates.
- Use `rg` for search. Fail closed on anything security-relevant.
