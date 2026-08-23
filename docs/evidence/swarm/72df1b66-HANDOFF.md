# SKL-S3-10A handoff

Card: `72df1b66`

Agent: `codex-skl-s3-10a`

Result: `FAIL_CLOSED`. The chiap01 SKGateway path is not qualified for
protected traffic and remains disabled.

## Delivered

- Added an activation-incapable chiap01 qualification contract at
  `config/model_gateway/deployment/skgateway-chiap01-qualification.json`.
- Added `scripts/qualify_skgateway_chiap01.py`, which checks the exact source,
  package, lockfile, working-tree, two-credential, exact-scope, response, and
  dashboard and metrics disable contracts without reading runtime values or
  secrets.
- Added a loopback-only, no-credential synthetic denial fixture at
  `deploy/chiap01/skgateway.synthetic-deny.yaml`.
- Added six new preflight and deployment-safety tests.
- Extended the chiap01 deployment runbook with preflight, synthetic denial,
  standalone, and cleanup requirements.
- Recorded exact revisions, tests, synthetic results, rollback evidence, and
  remaining gates in
  `docs/evidence/platform/SKL-S3-10A-CHIAP01-QUALIFICATION-2026-08-22.md`.

## Qualification finding

The installed gateway revision and reviewed dependency remediation match the
source pin, and authorization-unavailable traffic denies on the exact
production entrypoint. The gateway cannot produce a canonical allow because
its client sends one bearer credential and only `{path, method}` scope. The
landed S3-11 adapter requires a separate service credential, a request-local
CapAuth credential, and exact Tenant, Matter, material, route, purpose,
classification, privilege, and ethical-wall scope. The deployed startup path
also ignores `dashboard.enabled: false` and `metrics.enabled: false`.

The preflight therefore returns:

```text
result=FAIL_CLOSED
wire_compatible=false
activation_permitted=false
qualified_for_protected_traffic=false
```

No mock allow, credential substitution, trusted-fact synthesis, production
promotion, or protected request was used.

## Exact tests and results

```text
.tools/bin/uv run --locked pytest \
  tests/test_skgateway_chiap01_qualification.py \
  tests/test_skgateway_authz_config.py \
  tests/test_skgateway_authz_adapter.py \
  tests/test_skgateway_policy_evaluator.py \
  tests/test_model_gateway_skgateway_seam.py -q
95 passed, 15 subtests passed in 0.61s

.tools/bin/uv run --locked ruff check \
  scripts/qualify_skgateway_chiap01.py \
  tests/test_skgateway_chiap01_qualification.py
All checks passed!

.tools/bin/uv run --locked mypy \
  scripts/qualify_skgateway_chiap01.py \
  tests/test_skgateway_chiap01_qualification.py
Success: no issues found in 2 source files

SK_STANDALONE=1 npm test
1325 passed, 0 failed, 0 cancelled, 0 skipped

npm audit --omit=dev --audit-level=high
found 0 vulnerabilities
```

The repository-wide secret scan remains red due to baseline drift: 179 current
findings are outside the reviewed baseline, five from this card's published
hash pins and secret-reference field names. The card-specific safety test
rejects literal endpoints, bearer values, capability tokens, private-key
fields, and Inbox references. The baseline was not expanded to accept 174
unrelated findings.

## Synthetic and rollback results

- Direct baseline: exact sentinel, 1162.2 ms, model identity hash
  `656510237ebb5da76b8c1dc8f0f6db254b9957441da529a35479768e71c3bbe6`.
- Gateway public health: `200`.
- Gateway synthetic inference with unavailable authorization: `403` and no
  prohibited response fields.
- Gateway denial body SHA-256:
  `477d757b85e8a83d655a70e4dcad4a29f93de68c0151e092b4aa2ad85e2ba482`.
- Rollback direct route: exact sentinel, 2034.0 ms, same model identity hash.
- The ephemeral gateway stopped, both loopback ports closed, temporary files
  were deleted, and the final standalone test left no service registration.
- Deployment bindings were not changed. Direct Qwen remains enabled and
  SKGateway remains disabled.

## Remaining gates

- Gateway-side two-credential and exact-scope implementation and review.
- Durable chiap01 CapAuth, policy, replay, and audit composition.
- All 12 live controls, canonical synthetic allow and deny, failure matrix,
  parity, restart, saturation, and rollback on the resulting exact revision.
- Complete live-path report with decision, policy, audit, endpoint, and test
  revisions.
- Human security review and explicit activation approval.
- Separate Matter and policy approval before any protected traffic.

No board command was run, as required by the delegated contract. This handoff
and the evidence record are linked to card `72df1b66`. No push was performed.
