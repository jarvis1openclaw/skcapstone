# SKL-UI-04 task design

Date: 2026-08-22
Card: `31194edb`
Size: M

## Objective

Extend the AI-first Matter architecture with one stable SKLegal model-routing
seam. The same agent specification, Proposal schema, Matter policy, and legal
gates must work while deployment switches from a temporary direct chiap08
Qwen binding to a qualified SKGateway binding.

## Dependencies

- `SKL-S3-02` (`b0c6495c`)
- `SKL-UI-03` (`ecf6d536`)

## Implementation

- Pin and review `smilinTux/skgateway` source, license, API, installation,
  routing, capacity, model-size, bucket, free-provider, secret, identity,
  policy, audit, and rollback contracts.
- Keep SKLegal's `ModelGateway.submit` and typed Proposal as the application
  trust boundary.
- Define deployment transport profiles for direct local Qwen and qualified
  SKGateway without putting hostnames, ports, or credentials into domain
  records, agent specifications, prompts, or the wireframe.
- Distinguish the requested logical route, transport profile, requested model
  or bucket, catalog and policy revisions, actual backend, exact served model,
  and retry or failover evidence.
- Map SKLegal data classifications to SKGateway trust zones explicitly and
  fail closed. Do not infer that a remote free model may receive protected
  Matter or private corpus content merely because it is free.
- Create the implementation contract and board card for installation,
  adapter work, configuration, secret provisioning, and qualification.

## Tests

- Static wireframe structure and local link validation
- Direct and SKGateway transport profiles visible
- No private endpoint, API key, or exact operator alias in product artifacts
- Chat Completions versus Responses API gap visible
- Live-path CapAuth and policy gap visible as a blocker
- Reviewed high-severity dependency audit result visible as a deployment gate
- Workload class and model parameter size kept distinct
- Exact served-model and route evidence visible
- ASCII-dash validation

## Acceptance

- Matter logic and agent specs use logical SKLegal route IDs only.
- Direct local Qwen remains the temporary and rollback binding.
- SKGateway is the preferred future binding only after the implementation
  gate passes.
- Protected use remains blocked while a required live-path control is absent.
- Free-provider and bucket use remains classification, rights, purpose, and
  evaluation constrained.
- Implementation task `bbf206c3` contains the install, secrets, parity,
  capacity, failure, live qualification, and rollback scope.

## Prohibited

- Production or fleet deployment
- External account or provider key creation
- Protected Matter or private corpus egress
- Raw secret or endpoint in a committed file
- Direct edit to the upstream SKGateway repository
- Commit or push
