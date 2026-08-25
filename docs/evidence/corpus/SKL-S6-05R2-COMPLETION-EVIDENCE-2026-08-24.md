# SKL-S6-05R2 durable secondary-review completion evidence

- **Card:** `ec199c44`
- **Prior completed card:** `e9218ac4` (not modified)
- **Result:** qualified, exact eight-proposition challenge passed, five uncertainties preserved
- **Qualification:** `docs/evidence/corpus/SKL-S6-05-SECONDARY-REVIEW-QUALIFICATION-2026-08-24-EC199C44.json`
- **Qualification file SHA256:** `ab6d253607897ed500c1a10309577fa308ff2e6ab765546d432c10f0edda84e0`
- **Model output SHA256:** `96aee520b135131014fda0892cd3f8ddd2bdd40464b31b9a44524798d6f6aa69`

## Files changed

- `config/model_gateway/secondary-review-route.json`
- `packages/model_gateway/src/sklegal_model_gateway/secondary_review.py`
- `scripts/recreate_s6_05_secondary_review.py`
- `tests/test_secondary_review_route.py`
- `docs/evidence/corpus/SKL-S6-05-SECONDARY-REVIEW-QUALIFICATION-2026-08-24-EC199C44.json`
- this evidence file

## Exact pins and invocation

The run used the existing durable challenge and source pins:

- challenge SHA256: `9575d6db68d622e94c5a1d508e7229b43819381dc6ec659b3fe3e66b2adfd51d`
- profile SHA256: `9651ce6bdabf18cc27a39ff2bed87309b179b72fe37053c852f43cd288627959`
- scoped retrieval evidence SHA256: `c2d183be27e2503dde57e0a58c93461229aad3bccf5bf138edb079260b9ae04c`
- logical route: `sklegal.local-corpus-secondary-review`
- transport: `direct_qwen`
- gateway revision: route-config SHA256 `39f160140587e1e1e7e9e9c93e7bf5e7e74bfdc7a886cd612f0fec52fceca411`
- backend: `llama.cpp-local`
- requested and served model: `qwen3.8-27b-huihui-abliterated-q4_k_m`
- prompt SHA256: `88daaf419feed00733341d3a67b1ee0110dc2c0e6fa56846c5ef4c46f1f3fd8b`
- output schema SHA256: `a2e97fd1139b0043382635c36999128009dbc3592a7ea7aa4244c94efe9b6546`
- output SHA256: `96aee520b135131014fda0892cd3f8ddd2bdd40464b31b9a44524798d6f6aa69`

The endpoint was accessed only through the environment reference
`SKLEGAL_QWEN_REVIEW_ENDPOINT`, set for this invocation to the already-running
local loopback service. No endpoint literal or secret was written to product
configuration or evidence.

## Tests

```text
PYTHONPATH=packages/model_gateway/src:packages/domain/src python -m pytest -q tests/test_secondary_review_route.py
9 passed

ruff check packages/model_gateway/src/sklegal_model_gateway/secondary_review.py scripts/recreate_s6_05_secondary_review.py tests/test_secondary_review_route.py
All checks passed
```

The live invocation completed with `passed: true`. The review returned one
verdict for each P1 through P8 and preserved exactly these five uncertainties:

1. `EIA-STYLE-2020 currentness review required`
2. `NIJ-STYLE-2022 currentness discrepancy requires review`
3. `DOI-CORR-2014 currentness review required`
4. `CA7-TYPOGRAPHY currentness review required`
5. `EPA-STYLE-2009 supersession unknown`

## Prior hash comparison

The completed e9218ac4 card link records expected qualification SHA256
`21a77b4427d5774153d9701ff16940027d06bec0f7dea16f2e57ed9511638775`. The
original file lived in a cleaned temporary worktree and is unavailable, so
byte-for-byte reproduction is not possible. The regenerated qualification
records this attributable difference and preserves the same durable challenge,
source pins, logical route, model identity, and review contract.

## Safety, rollback, and limitations

- HammerTime Inbox was not accessed.
- No protected Matter content was used or sent out.
- No deployment, promotion, alias mutation, external action, commit, push, or merge occurred.
- Fail-closed tests cover missing endpoint, timeout, malformed output, served-model mismatch, incomplete proposition coverage, and uncertainty mismatch.
- Rollback deletes only the generated qualification JSON. Route config is a content-only local binding and source evidence is unchanged. No HammerTime or workflow state changed.
- This is a corpus review proposal and qualification evidence, not legal advice, approval, workflow authority, or authorization to act.
