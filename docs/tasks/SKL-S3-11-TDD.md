# SKL-S3-11 implementation task design

Card: `a060fa3d`
Parent consumer: `72df1b66`
Date: 2026-08-22

## Objective

Provide a narrow, authenticated authorization-decision adapter for the
SKGateway live path on chiap01. The adapter must delegate to SKLegal's
canonical CapAuth and policy gateways. It must not adopt the broad CapAuth
FastAPI service, port `capauth.authz.decide` into a second policy engine, or
make authorization decisions from model or request content alone.

## Request contract

The internal endpoint may use the minimum compatible shape:

```json
{
  "subject": "service-or-agent-principal",
  "capability": "skgateway.infer",
  "resource": {
    "tenant_id": "...",
    "matter_id": "...",
    "material_id": "...",
    "material_version": "1",
    "route_id": "..."
  },
  "context": {
    "purpose": "...",
    "classification": "...",
    "privilege": "...",
    "ethical_wall": "..."
  }
}
```

The endpoint authenticates the SKGateway service caller with a scoped service
credential or local authenticated transport. `subject`, capability, resource,
purpose, classification, privilege, and ethical-wall facts are validated
against trusted current state. Caller-supplied facts never grant authority.
`material_id` and `material_version` are required by the durable snapshot so
classification, privilege, and ethical-wall facts can be derived from the
existing material policy state instead of echoed from caller content.

The response is sanitized and attributable:

```json
{
  "allow": false,
  "reason": "policy_denied",
  "decision_id": "...",
  "policy_revision": "...",
  "correlation_id": "...",
  "obligations": []
}
```

Never return prompts, Matter content, source spans, raw credentials, bearer
tokens, private keys, or unrestricted policy internals.

## Required behavior

- Reuse `PolicyGateway`, `ProtectedRouteDependency`, the durable CapAuth
  current-state verifier, and the policy audit sink.
- Require exact capability `skgateway.infer` and bind it to model inference,
  tenant, Matter, purpose, classification, and route scope.
- Enforce privilege and ethical-wall boundaries before returning allow.
- Fail closed on missing, expired, revoked, replayed, stale, malformed, or
  unavailable identity, capability, policy, audit, or scope state.
- Return deterministic `401`, `403`, or `503` classes without leaking causes.
- Record only sanitized decision and correlation fields in the durable audit.
- Bind the listener to loopback or an authenticated local network interface.
- Keep the SKGateway profile disabled until the endpoint passes live-path
  qualification and a human approves activation.

## Tests

- valid service caller and approved synthetic public request
- wrong service identity, missing credential, expired credential, and replay
- wrong capability, tenant, Matter, route, purpose, classification, privilege,
  or ethical wall
- policy, CapAuth current-state, revocation, replay, scope, and audit outage
- malformed request, unknown fields, oversized body, and reason sanitization
- actual SKGateway production route synthetic allow and deny
- no prompt, Matter, capability, or secret leakage in response, audit, or logs
- rollback to deterministic denial with the endpoint unavailable

## Acceptance

The resulting live-path report must identify the exact adapter revision,
service identity, policy revision, audit revision, endpoint binding, and test
hashes. It must be linked to `72df1b66`. Protected Matter traffic remains
denied until the complete report and human approval exist.
