# SKL-S3-03 parent review evidence

Date: 2026-08-22

SKCapstone card: `3e442852`

## Outcome

The three decomposed implementation slices are present on main and the parent
acceptance suite is green. SKL-S3-03 is ready for human review. This record
does not complete the parent card or grant deployment approval.

## Landed slices

- `d66b8a19`, S3-03A, commit `6747454`: versioned Agent specification schema,
  registry, schema pins, bounded context, tool allowlist, budgets, model routes,
  retries, and human escalation.
- `ecaf34bc`, S3-03B, commit `2b76dc2`: CapAuth-mediated domain tool gateway,
  pinned argument and result schemas, finite run budgets, and sanitized call
  records. The original slice identifier `fac64b7c` was voided as a duplicate.
- `27a8a6a9`, S3-03C, commit `323eeac`: adversarial qualification and a
  sanitized denial trail for failures that occur before CapAuth authorization.

All three commits are ancestors of the current main branch.

## Acceptance evidence

The versioned `corpus-analyst` specification pins one task purpose, input and
output schema hashes, confidential Matter-scoped context, a four-tool domain
allowlist, finite call, time, retry budgets, the logical
`qwen.corpus-summary.v1` route, and `human.attorney-review` escalation.

The only registered domain tools are Matter read, Evidence search and read,
Authority search, Deadline computation, and Work Product drafting. There is no
general shell, arbitrary network, browser, email, filing, service, calendar,
or dispatch tool. The registry rejects unknown tools before execution.

The gateway validates tool arguments before authorization, verifies a narrow
CapAuth boundary, never passes presented credentials to handlers or models,
validates results before recording them, and records content-free denial
evidence. Adversarial tests prove that prompt injection, document-borne action
requests, unauthorized tools, malformed arguments, budget exhaustion, expired
or over-delegated capabilities, principal revocation, and validator bypasses
fail closed.

## Tests and exact results

Command:

```text
.tools/bin/uv run --locked pytest -q tests/test_agent_spec_registry.py tests/test_tool_gateway.py tests/test_tool_gateway_adversarial.py
```

Result:

```text
81 passed, 9 subtests passed in 2.28s
```

Commands:

```text
.tools/bin/uv run --locked ruff check packages/agents tests/test_agent_spec_registry.py tests/test_tool_gateway.py tests/test_tool_gateway_adversarial.py
.tools/bin/uv run --locked ruff format --check packages/agents tests/test_agent_spec_registry.py tests/test_tool_gateway.py tests/test_tool_gateway_adversarial.py
```

Results:

```text
All checks passed!
10 files already formatted
```

## Known limitations

- The denial trail is process-local. Durable append-only projection belongs to
  the workflow and audit integration boundary, not this parent card.
- Denial records intentionally omit hostile arguments, results, and credential
  material. They preserve stage, error type, run identity, and time only.
- A dependency-complete type check over domain, CapAuth, model gateway, and
  agents currently reports four unrelated errors in two model-gateway files
  being changed by other active cards. The same errors are present outside the
  S3-03 package scope; the focused runtime and schema tests are green.

## Migration and rollback

No data migration or runtime deployment is part of this parent review. Code
rollback is the ordinary reversal of commits `323eeac`, `2b76dc2`, and
`6747454` in reverse order. Reversal would also remove their Agent schemas and
tests, so any dependent card must be stopped first.

## Human review checklist

- Confirm the bounded Agent specification fields satisfy the S3-03 TDD.
- Confirm the tool catalog contains no general shell or arbitrary network tool.
- Confirm raw credential material cannot reach a handler, model, run record,
  denial record, or audit payload.
- Confirm every named adversarial case fails closed with attributable evidence.
- Accept or reject the documented process-local denial-trail limitation.
