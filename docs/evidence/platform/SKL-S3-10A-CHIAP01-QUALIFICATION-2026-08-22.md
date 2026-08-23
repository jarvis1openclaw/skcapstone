# SKL-S3-10A chiap01 deployment qualification evidence

Date: 2026-08-22 America/Chicago

Card: `72df1b66`

Result: `FAIL_CLOSED`. Protected traffic remains disabled.

## Scope and decision

This qualification used only synthetic prompts and public deployment
metadata. It did not read a provider key, service credential, capability
token, protected Matter content, or HammerTime Inbox material. It did not
promote a service or enable the SKLegal SKGateway transport profile.

The exact installed gateway cannot be qualified for a canonical SKLegal allow.
Its authorization client sends one bearer credential and `{path, method}`
resource data. The landed SKLegal adapter requires two separate credentials
and exact Tenant, Matter, material, route, purpose, classification, privilege,
and ethical-wall scope. Treating the gateway service credential as the
request-local CapAuth credential, or filling missing scope from a synthetic
bridge, would bypass the approved boundary. No such bypass was implemented.

## Exact revisions

- Installed SKGateway commit:
  `b4b4115df9a6d5c9c4621d98207a1074e2737ef5`
- Installed source composite revision:
  `c1c37fbf98378e417d17a8d144f4b9d8b8a0d58c71f43f2e679be87c57cc03d6`
- Reviewed package SHA-256:
  `544000121048cd3054542fee8e30e70f2282122510788f40e35e85491ffd4656`
- Reviewed lockfile SHA-256:
  `60374ebcde47a0af9561d1f02f20b7d37022bc660f7fd85cbd828767d297a647`
- SKLegal S3-10 transport seam commit: `b0ca530`
- SKLegal S3-11 route-policy commit: `5210a97`
- SKLegal S3-11 PostgreSQL snapshot commit: `10468d2`
- SKLegal S3-11 canonical evaluator commit: `f9301e4`
- Service identity pin:
  `capauth:sklegal-model-gateway@chiap01.skworld`
- Gateway smoke-test binding: loopback port `28780`, ephemeral only
- Canonical authorization endpoint binding: absent, no live policy revision or
  audit revision was produced

Exact installed live-path source hashes:

```text
54329e183262cdd5ce5171cd6c1a9c0642a055578ed4d7d769fb08007db0e99d  src/index.mjs
19beabb1e20430e8519a880cb41b4424eb81642370c6baa15a5c3237ea03a411  src/policy/authz_decide.mjs
e29fc297a8d0d4499dc99a64543037cc3995c631e0ddbc087faff2388826bf6e  src/policy/authz_gate.mjs
e37d5ea80f02eec6dc4317590ad038ab6f4c69cfdcc131ea3bf937148242a7af  src/policy/authz_routes.mjs
```

## Preflight result

`scripts/qualify_skgateway_chiap01.py` was copied to an isolated temporary
directory on chiap01, run against `/opt/skgateway`, and removed. It did not
read runtime environment values.

Passing source and custody checks:

- installed commit, package hash, and lockfile hash match
- only the reviewed package and lockfile remediation is present
- exact `skgateway.infer` route mapping is present
- master enforcement and strict internal-peer switches are present
- allow cache is disabled by the qualification contract

Failing compatibility checks:

- service header is absent
- separate request-local CapAuth forwarding is absent
- exact resource scope is absent
- exact policy context scope is absent
- the sanitized S3-11 response shape is not consumed
- `dashboard.enabled: false` is ignored by the live startup path
- `metrics.enabled: false` is ignored by the live startup path

The preflight returned `FAIL_CLOSED`, `wire_compatible=false`,
`activation_permitted=false`, and
`qualified_for_protected_traffic=false`. It marks every control in
`skgateway-live-path-gate/v1` unqualified because static source markers cannot
stand in for runtime evidence.

## Synthetic route results

Direct Qwen baseline before the gateway smoke test:

```text
model_id_sha256=656510237ebb5da76b8c1dc8f0f6db254b9957441da529a35479768e71c3bbe6
latency_ms=1162.2
content_exact=true
content_nonempty=true
usage={"completion_tokens":43,"prompt_tokens":69,"prompt_tokens_details":{"cached_tokens":0},"total_tokens":112}
```

Exact SKGateway production entrypoint with strict enforcement, internal trust
disabled, no authorization endpoint, no token, and a synthetic body:

```text
health_status=200
deny_status=403
deny_message=Forbidden by authorization policy
deny_reason=authz_decide fail-closed: no decide endpoint / token configured
deny_code=403
leak_fields=[]
health_body_sha256=cfc7cbaf1f58c93f580ec2afc8cd678879fa8120f63d2d2f5d745669b876e842
deny_body_sha256=477d757b85e8a83d655a70e4dcad4a29f93de68c0151e092b4aa2ad85e2ba482
audit_sha256=3a10fb1e8c8266502ad9e4174e9985cf84bcda99e6a12584acc9417ab967b0c2
audit_lines=1
```

This proves the exact entrypoint denies when authorization is unavailable. It
does not qualify a canonical allow. The response had none of `prompt`,
`matter_content`, `source_span`, `bearer_token`, `private_key`, or
`raw_capability`.

## Tests and audits

Focused SKLegal test command:

```text
.tools/bin/uv run --locked pytest \
  tests/test_skgateway_chiap01_qualification.py \
  tests/test_skgateway_authz_config.py \
  tests/test_skgateway_authz_adapter.py \
  tests/test_skgateway_policy_evaluator.py \
  tests/test_model_gateway_skgateway_seam.py -q
95 passed, 15 subtests passed in 0.61s
```

Static checks:

```text
.tools/bin/uv run --locked ruff check \
  scripts/qualify_skgateway_chiap01.py \
  tests/test_skgateway_chiap01_qualification.py
All checks passed!

.tools/bin/uv run --locked mypy \
  scripts/qualify_skgateway_chiap01.py \
  tests/test_skgateway_chiap01_qualification.py
Success: no issues found in 2 source files
```

Exact installed upstream commands, with `SK_STANDALONE=1` for the test suite:

```text
SK_STANDALONE=1 npm test
tests 1325
pass 1325
fail 0
cancelled 0
skipped 0

npm audit --omit=dev --audit-level=high
found 0 vulnerabilities
```

The repository-wide `scripts/check_secrets.py` gate did not pass because the
current reviewed baseline trails the repository by 179 findings. Five findings
are in this card: three published SHA-256 pins and one secret-reference keyword
in the JSON contract, plus the same reference keyword in the preflight. The
card-specific tests separately reject literal endpoints, bearer values,
capability tokens, private-key fields, and Inbox references. The baseline was
not broadened because that would also accept 174 unrelated findings.

Qualification artifact hashes before final documentation edits:

```text
d081e87ef891cb3a48871f25cc2e871fc24caaf49f80a37e2cfcf6ec5fefa378  scripts/qualify_skgateway_chiap01.py
a16a63d237e10c55c755c028ca9ee98d16c880055463dd78507c3cf705cf2c77  tests/test_skgateway_chiap01_qualification.py
2e638e4a082838c27ca4e017b154ecada53399fde9ca60df32a7f9b5c19480b7  config/model_gateway/deployment/skgateway-chiap01-qualification.json
e48179e1ece103ddf7609872751446c1b47eb7b9e9fc8ea2e55b8e6fef881114  deploy/chiap01/skgateway.synthetic-deny.yaml
acb1839e936a606a29adbed2f0f851df21efba3156bef950fe48a8567a2d4c19  services/api/src/sklegal_api/skgateway_authz.py
939baa597e6e913904485d75ab2dee0e43e973ec32edcd8ea804f56f6cb9198f  migrations/0017_skgateway_authorization_snapshot.sql
```

## Rollback evidence

No persistent SKGateway service was activated. The ephemeral loopback process
received `SIGTERM`, both test ports closed, and the temporary configuration,
response bodies, and synthetic audit file were removed. The disabled gateway
profile and enabled direct-Qwen profile remained unchanged.

The same direct endpoint then returned the exact rollback sentinel:

```text
model_id_sha256=656510237ebb5da76b8c1dc8f0f6db254b9957441da529a35479768e71c3bbe6
latency_ms=2034.0
content_exact=true
content_nonempty=true
usage={"completion_tokens":40,"prompt_tokens":72,"prompt_tokens_details":{"cached_tokens":0},"total_tokens":112}
```

An initial ephemeral launch and an upstream test process registered a stale
loopback health entry before the standalone guard was applied. Each exact
qualification-owned registry entry was validated, hashed, and removed. The
final standalone upstream test left no registration. No board command ran.

## Remaining gates

Technical gates:

- implement and review the gateway-side two-credential, exact-scope request
  contract without weakening S3-11
- wire durable chiap01 CapAuth, policy, replay, and audit dependencies
- make dashboard and metrics disablement effective and qualify all 12 exact
  live controls
- run canonical synthetic allow and deny, audit outage, policy outage,
  saturation, restart, parity, and rollback on the resulting exact revision
- record the live decision ID, policy revision, audit revision, endpoint
  binding, test hashes, and complete live-path report

Human gates:

- security review of the resulting source and deployment composition
- review and approval of the complete live-path report
- separate explicit approval before enabling the SKLegal gateway profile
- separate policy and Matter approval before any protected traffic

Until all gates pass, the direct Qwen profile remains the approved route and
the SKGateway profile remains disabled.
