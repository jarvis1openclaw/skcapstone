# SKL-S3-10C chiap01 live-path qualification evidence

Date: 2026-08-22 America/Chicago

Card: `60cb0c9a`

Result: `FAIL_CLOSED`. Protected traffic remains denied and the SKLegal
SKGateway transport profile remains disabled.

## Scope and runtime decision

This qualification used public upstream source and synthetic fixtures only.
It did not read or create a credential, provider key, capability token,
protected Matter artifact, or HammerTime Inbox material. It did not activate
the SKLegal gateway profile or enable protected traffic.

The worker host was chiap08. There was no `/opt/skgateway` install on that
host. A credential-free request to the documented chiap01 qualification port,
`chiap01:28780`, returned connection failure with HTTP code `000`. No
already-running credential-free qualification endpoint was available. The
exact installed chiap01 service, live endpoint binding, policy revision, audit
revision, decision IDs, saturation state, and restart behavior therefore
remain unobserved. No synthetic allow is presented as a live allow.

## Exact source and composition revisions

Public upstream `main` resolved to:

```text
repository=https://github.com/smilinTux/skgateway.git
commit=3cf16fe6ca1a6e5ec92e5f090fc798dfd6404596
package_sha256=152544873a5f0405a5665dabef53f83f5c09b998282d908f265e8e809cde1fbc
lockfile_sha256=60374ebcde47a0af9561d1f02f20b7d37022bc660f7fd85cbd828767d297a647
source_composite_revision=5127c09c34a66eaf2a883fc5ea2e2fc2f8a34f47b17de04fa4fa85aecddff492
node=v22.23.2
npm=10.9.8
```

Integrated live-entrypoint source hashes:

```text
b94da4d03c422a7fd57f56b1bc15084e0a4e8d412ede947dc0b34757a948c216  src/index.mjs
60b8de8d30774e2eedd287692433f09fd494dac21a53a924300eda1594f1765c  src/config.mjs
7cea0e3e6964505c96b684cfd76917bc290ce3029c9f75dcb78bbff6927e40c6  src/policy/authz_decide.mjs
4d487eb8ad3dedd56ead9678da6adf50ad1983aa96af4267ca44f5968dc4f434  src/policy/authz_gate.mjs
e37d5ea80f02eec6dc4317590ad038ab6f4c69cfdcc131ea3bf937148242a7af  src/policy/authz_routes.mjs
fb009ea2f9467f3c31b73cd71e2ac668ac86cbbe03fd3f91d884b8d108239c02  src/policy/sklegal_authz_decide.mjs
97c3fff675308f7485534be9aa58784f937876c20a3139900df6fe1591d27c1a  src/proxy/router.mjs
```

SKLegal composition validation:

```text
sklegal_commit=c653cae62cb0dd4c51022836e231bac1b36733fa
composition_revision=sklegal-skgateway-authz-production-composition/v1
composition_source_sha256=98b09722eca21e1bb0592cc3daca8aa618eb39af5b687994c16867087713e16f
deployment_contract_sha256=7f3a8b2a4b65026d765b8c4601e9b925520c251fc90be7af51c5b7e686f1a42e
migration_0018_sha256=877ca73f682bbec2f3d0b4f33300ca5c18f5a1a3e0615b55d13ab3cf8da227b5
endpoint_reference=SKLEGAL_CAPAUTH_AUTHZ_ENDPOINT
endpoint_binding_contract=127.0.0.1:/v1/authz/decide
transport=authenticated-local-http
service_identity=capauth:sklegal-model-gateway@chiap01.skworld
profile_enabled=false
```

The source preflight returned `PASS`, `wire_compatible=true`, and
`composition.result=PASS`. It separately returned
`qualified_for_protected_traffic=false` and `activation_permitted=false`.
Every live control remains false because source compatibility is not runtime
evidence.

## Dependency and upstream test results

The isolated public checkout used `npm ci`. No native module or dependency
change was made in SKLegal.

```text
npm audit --omit=dev --audit-level=high
found 0 vulnerabilities

node --test --import ./tests/_setup.mjs \
  tests/sklegal-authz-wire.test.mjs \
  tests/authz-enforce-integration.test.mjs \
  tests/startup-disablement.test.mjs \
  tests/qwen-capacity-domain.test.mjs \
  tests/sanitizer.test.mjs
59 passed, 0 failed

SK_STANDALONE=1 npm test
1393 passed, 0 failed, 0 cancelled, 0 skipped
```

Focused upstream test hashes:

```text
1b3283991a7ea82ac0daa4b5962a676337fd4ad895aee6b0f9df70cb9d480fb3  tests/sklegal-authz-wire.test.mjs
2f3f7854ef2a372a3d20187699156c7688f5e120be5baf984699e22e67293cb6  tests/authz-enforce-integration.test.mjs
25966faf262a15cf67560aae7f51a7f0be1332baa782b5970faa0824861a7519  tests/startup-disablement.test.mjs
b150387a58a84651af5c577ff33025e65168fb7b275ad58cc101d09fb23b9ee7  tests/qwen-capacity-domain.test.mjs
4530a11f2a3e81bd431e2eff57d3351054e66860fc625c80a2d7284bbf516a4c  tests/sanitizer.test.mjs
```

## SKLegal tests and exact results

The workspace had no `uv` executable. Tests ran without dependency mutation by
placing the repository package source directories on `PYTHONPATH`.

```text
python3 -m pytest \
  tests/test_skgateway_chiap01_qualification.py \
  tests/test_skgateway_authz_config.py \
  tests/test_skgateway_authz_adapter.py \
  tests/test_skgateway_policy_evaluator.py \
  tests/test_skgateway_authz_composition.py \
  tests/test_model_gateway_skgateway_seam.py -q
113 passed, 16 subtests passed in 0.76s

python3 -m pytest \
  tests/test_api_capauth_composition.py \
  tests/test_api_capauth_composition_health.py \
  tests/test_capauth_boundaries.py \
  tests/test_policy_engine.py \
  tests/test_audit.py \
  tests/test_skgateway_authz_adapter.py \
  tests/test_skgateway_policy_evaluator.py \
  tests/test_skgateway_authz_composition.py -q
88 passed, 73 subtests passed in 0.63s

ruff check scripts/qualify_skgateway_chiap01.py \
  tests/test_skgateway_chiap01_qualification.py
All checks passed!

git diff --check
passed
```

Mypy and the repository secret scanner were unavailable in the active
environment. `python3 scripts/check_secrets.py` stopped with
`detect-secrets is not installed in the active environment`. Card-specific
tests reject literal HTTP endpoints, bearer values, Inbox references, raw
capability material, private-key fields, and activation.

## Synthetic matrix and attribution

The deterministic matrix is explicitly fixture-only:

```text
matrix_sha256=b4ef2f17cf76e1a732b6e62e3135d174a26303db8cb8652633478f069a897ac5
scenario_count=13
policy_revision=synthetic-policy-revision-v1
audit_revision=synthetic-audit-revision-v1
direct_qwen_parity_fixture_sha256=e600586709e9a4af4605f86b38abc63a5fcd8bd73732ea22bde1ea7858b1e35e
```

It covers canonical allow and deny, unavailable PDP and audit, malformed and
oversized selectors, exact scope mismatch, ninth-request saturation, restart
recovery, sanitizer and leakage denial, audit attribution, direct-Qwen parity,
and rollback. Synthetic decision IDs are named with the `synthetic-` prefix.
They are not live chiap01 decision IDs.

The upstream live-server fixtures prove the integrated production entrypoint
uses separate service and request-local credentials, exact configured scope,
no governed internal bypass, and no governed allow cache. The startup fixture
proves dashboard and metrics disablement and denies forced discovery refresh.
The capacity fixture proves the shared Qwen envelope and retryable saturation.
The SKLegal suites prove canonical current-state composition, sanitizer
behavior, response and audit attribution, policy and audit outage denial,
parity, and rollback contracts.

## Live-path control verdict

All 12 controls remain `UNQUALIFIED_LIVE`:

- CapAuth identity and exact `skgateway.infer` capability
- SKLegal policy and classification or egress decisions
- body and system limits
- secret handling and tool-budget stripping
- rate limits and shared Qwen capacity
- attributable audit and no prompt or credential leakage
- deterministic identity, policy, catalog, and audit denial

The public source and synthetic tests support compatibility only. The exact
installed chiap01 service must rerun the same matrix with credential-free or
human-authorized isolated service fixtures before a live allow can exist.

## Rollback evidence

No service, endpoint, profile, or protected route was activated. Repository
configuration still has `chiap08.skgateway-chat.v1` disabled and
`chiap08.direct-qwen.v1` enabled. The synthetic rollback fixture requires the
same Proposal schema and payload hash after rebinding to direct Qwen. The
rollback router returns deterministic sanitized `503` while the governed
endpoint is unavailable. No migration or persistent data change occurred, so
no data rollback was required.

## Remaining gates

- Install or identify exact `3cf16fe6ca1a6e5ec92e5f090fc798dfd6404596`
  on chiap01 and recheck package and lock hashes.
- Compose the exact endpoint with isolated disposable public fixtures and
  obtain live policy, audit, correlation, and decision revisions.
- Run live allow, deny, outage, malformed, scope, saturation, restart,
  leakage, attribution, parity, and rollback cases on that entrypoint.
- Obtain independent security review and separate human activation approval.
- Keep protected Matter traffic denied until all live evidence and human gates
  pass.
