# Agent specifications

Versioned, bounded agent role specifications loaded by `sklegal_agents`
(`packages/agents`) through `AgentSpecRegistry`.

Rules for this directory:

- Every file is one immutable spec version with the
  `sklegal-agent-spec/v1` schema marker.
- A `(spec_id, version)` pair never changes content. To revise a role, add
  a new file with the next version; the registry rejects a different
  document that claims an existing version.
- Specs are hash-pinned: loaders verify the SHA-256 of each file against a
  manifest when one is supplied, and every loaded record carries its pin.
- Tool allowlists name exact ids from the known domain-tool catalog.
  Wildcards and unknown tools are rejected at load.
- Model routes must be pinned and enabled in
  `config/model_gateway/route-registry.json`, and the allowed-context
  classification ceiling may not exceed the route egress ceiling.

Current specs:

- `corpus-analyst.v1.json`: summarizes released corpus material for one
  matter into a typed corpus-summary proposal via the local Qwen route.
