# SKL-S4-03A completion evidence

Date: 2026-08-22
Card: `7488582f` (slice of `f31e9c1e` SKL-S4-03)
Agent: `skl-s4-03a`

## Outcome

Built the `/corpus` research surface per the `s-research` wireframe
section in `docs/planning/wireframes/index.html`:

- Governed search bar with explicit scope chips. `this_matter` is the
  active matter scope; `tenant_corpus` and `official_sources` render as
  explicitly labelled unavailable states with reasons instead of being
  silently dropped, matching the matter-scoped CapAuth capability
  contract.
- Result list rendering the full S2-10 retrieval trace per response:
  scope kind, tenant, release id, projection generation, current
  projection generation, backend watermark, lag, query template id,
  version, and hash, rank path, source ids and source hashes. A stale
  projection generation raises a visible `role="alert"` caution showing
  both generation numbers; staleness is reported, never harmonized.
- Exact source-span viewer: highlighted span text (`mark`), exact
  locator, source hash, supersession status, and jurisdiction, plus
  per-result citation anchors (`#result-...` to `#span-...`) for
  citation navigation.
- Denial states: an inaccessible source is a distinct typed response
  shape that structurally excludes span text, locator, hashes, and
  citation, so the denial can never leak protected content. The UI
  renders the denial reason and a no-content message.

Corpus rows are labelled as unverified research proposals, never
verified Authority; official-source verification remains a separate
lane.

## Files changed

- `services/api/src/sklegal_api/corpus.py`
- `tests/test_api_corpus.py`
- `apps/web/src/api/types.ts`
- `apps/web/src/api/client.ts`
- `apps/web/src/design/tokens.ts`
- `apps/web/src/testing/corpus.ts`
- `apps/web/src/pages/CorpusPage.tsx`
- `apps/web/src/pages/corpusPage.test.tsx`
- `apps/web/src/router.tsx`

## API contract

- `POST /v1/matters/{matter_id}/corpus/search` with body `{"query": ...}`:
  requires `corpus.search` for purpose `legal_research` scoped to the
  exact matter, plus recorded matter membership. Returns results and
  the full retrieval trace in camelCase.
- `GET /v1/matters/{matter_id}/corpus/sources/{source_id}/span`:
  requires `corpus.artifact.read` for purpose `legal_research` pinned to
  the exact source artifact and matter. Returns a discriminated span
  state: `available` (exact span text and provenance) or `denied`
  (reason and message only).
- Fail-closed behavior: missing credential or wrong capability, matter,
  tenant, or resource yields 403 with a sanitized code; unknown matter,
  source, or query yields 404; store outage yields 503.

## Tests and exact results

- `UV_CACHE_DIR=$PWD/.tools/uv-cache .tools/bin/uv run --locked --package sklegal-api pytest tests/test_api_corpus.py tests/test_api_workspace.py -q`
  - 29 passed (16 new corpus tests, 13 existing workspace tests).
- `npm test` (web vitest)
  - 156 passed in 11 files (13 new corpus page tests; existing 143
    unchanged, including visual regression snapshots).
- `npm run typecheck` (`tsc -b`): passed.
- `npm run lint` (`eslint .`): passed.
- `npm run format:check` (`prettier --check .`): passed.
- `npm run build` (`vite build`): passed.
- `ruff format --check` and `ruff check` on the two new Python files:
  passed.

Required test coverage from the card: citation navigation (frontend
anchor tests), inaccessible source (backend denial-shape test plus
frontend no-leak tests), stale generation display (backend both-field
test plus frontend alert test).

## Known limitations

- The store is a Protocol plus an in-memory implementation, mirroring
  the SKL-S4-02 workspace pattern. Wiring a live retrieval-backed store
  is the parent card SKL-S4-03 work.
- `mypy services` reports 8 `import-untyped` errors in this environment;
  7 are pre-existing on the base tree and the 1 new line is the same
  class on the same import (`sklegal_capauth`) that `workspace.py`
  already carries. No new error class was introduced.
- `ruff format --check` over the whole repo flags 8 pre-existing files
  outside this card's scope (verified identical on the base tree); the
  files this card touched are format-clean.
- Router-level `validateSearch` accepts a `matterId` query parameter for
  deep links; the matter picker on `/corpus` without a parameter links
  to `/matters`.
