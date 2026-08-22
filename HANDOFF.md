# SKL-S4-03A handoff

Card: `7488582f` (SKL-S4-03A, slice of `f31e9c1e` SKL-S4-03)
Branch: `swarm/7488582f`
Commits:

- `feat(api): corpus research read API with retrieval trace and span viewer (SKL-S4-03A)`
- `feat(web): /corpus research surface with retrieval trace and span viewer (SKL-S4-03A)`
- this handoff and the evidence file

## What was built

The `/corpus` research surface from the `s-research` wireframe section:

1. Governed search bar (backend `POST /v1/matters/{matter_id}/corpus/search`,
   body `{"query": ...}`) gated by `corpus.search` / `legal_research`
   scoped to the exact matter, plus matter membership. Scope chips:
   `this_matter` active; `tenant_corpus` and `official_sources` render
   labelled unavailable states with reasons, never silently dropped.
2. Result list rendering the full S2-10 retrieval trace: scope kind,
   tenant, release id, projection generation and current projection
   generation, backend watermark, lag, query template id/version/hash,
   rank path, source ids and source hashes. Every row also carries its
   own source hash, locator, span coordinates, supersession status, and
   an explicit unverified-research-proposal label (never Authority).
3. Exact source-span viewer (backend `GET
   /v1/matters/{matter_id}/corpus/sources/{source_id}/span`) gated by
   `corpus.artifact.read` / `legal_research` pinned to the source
   artifact. Available state renders the exact highlighted span,
   locator, hash, supersession status, and jurisdiction. Denied state is
   a separate typed shape with no span text, locator, hash, or citation
   fields, so a denial cannot leak protected content.
4. Frontend: `/corpus` route now renders `CorpusPage` (was a
   placeholder), deep-linkable via `?matterId=`; without a matter it
   links to `/matters`. Citation anchors `#result-...` and `#span-...`
   give citation navigation. Stale projection generation renders a
   `role="alert"` caution with both generation numbers.

Fail-closed behavior mirrors SKL-S4-02: 403 with sanitized codes for
credential, capability, matter, tenant, or resource mismatch;
membership denial; 404 for unknown matter/source/query; 503 for store
outage.

## Files changed

Backend:

- `services/api/src/sklegal_api/corpus.py` (new)
- `tests/test_api_corpus.py` (new)

Frontend:

- `apps/web/src/api/types.ts` (corpus types appended)
- `apps/web/src/api/client.ts` (private `postJson` helper, `searchCorpus`, `getCorpusSpan`)
- `apps/web/src/design/tokens.ts` (`supersessionStatus`, `spanAccessibility`)
- `apps/web/src/testing/corpus.ts` (new synthetic fixture)
- `apps/web/src/pages/CorpusPage.tsx` (new)
- `apps/web/src/pages/corpusPage.test.tsx` (new)
- `apps/web/src/router.tsx` (corpusRoute renders CorpusPage, validateSearch for matterId)

Docs:

- `docs/evidence/status/SKL-S4-03A-COMPLETION-EVIDENCE-2026-08-22.md` (new)

## Tests and exact results

- `UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-api pytest tests/test_api_corpus.py tests/test_api_workspace.py -q`
  - `29 passed in 0.46s` (16 new, 13 existing unchanged).
- `npm test`: `156 passed (156)` in 11 files (13 new; existing 143
  unchanged, visual regression snapshots untouched).
- `npm run typecheck` (`tsc -b`): clean.
- `npm run lint` (`eslint .`): clean.
- `npm run format:check` (`prettier --check .`): clean.
- `npm run build` (`vite build`): succeeded.
- `ruff format --check services/api/src/sklegal_api/corpus.py tests/test_api_corpus.py`
  and `ruff check` on the same files: clean.

Card-required coverage and where it lives:

- citation navigation: `corpusPage.test.tsx` citation-navigation describe
  block (anchors and aria labels).
- inaccessible source: `test_api_corpus.py`
  `test_inaccessible_source_renders_denial_without_content` (denied
  shape has no spanText, sourceLocator, or sourceSha256 keys) plus the
  two frontend denial tests (no span text on the page; no locator,
  hash, or citation inside the denial view).
- stale generation display:
  `test_api_corpus.py::test_search_reports_stale_projection_generation`
  (both generations survive on the wire) plus the frontend stale-alert
  test showing both numbers with `role="alert"`.

## Acceptance criteria evidence

The card body records no explicit acceptance criteria list; the
description's requirements map as follows:

- governed search bar: scope chips test + capability-gated search route
  tests (missing credential, wrong capability, cross-matter, cross-tenant).
- result list rendering the full S2-10 retrieval trace: trace-fields
  frontend test plus the camelCase wire test asserting every trace
  field listed in the card (scope, release, source hashes, projection
  generation, query-template ID, watermark, rank path).
- exact source-span viewer with denial states: available-span and
  denial tests on both sides of the boundary.

## Known limitations

- Store is a Protocol plus an in-memory implementation following the
  S2-02 workspace pattern; a live retrieval-backed store is parent-card
  SKL-S4-03 work. No persistence or migration changes were needed.
- `mypy services` shows 8 `import-untyped` errors in this environment;
  7 pre-exist on the base tree and the one new line is the same class
  on the same import that `workspace.py` already carries
  (`sklegal_capauth` via editable install without `py.typed`).
- Repo-wide `ruff format --check` flags 8 pre-existing files outside
  this card's scope; verified identical on the base tree. Files touched
  by this card are format-clean.
- The frontend span viewer defaults to the highest-ranked result and
  the citations expose `#span-...` anchors; wiring interactive
  result-to-span selection is parent-card work because the SSR test rig
  has no event simulation. The anchors provide the navigation contract.
- Rollback: pure additive revert of the listed files; no data
  migration, no schema change, no external effects.

## Boundaries respected

No push/pull/remote git, no changes outside this worktree, no
HammerTime access, no external actions, no secrets, no skcapstone board
commands, ASCII hyphens only, synthetic fixtures throughout.
