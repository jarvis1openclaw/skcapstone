# Agent schemas

Machine-readable schema artifacts for typed agent proposals and validations.

- `agent-spec.v1.schema.json`: wire format for versioned, bounded agent role
  contracts. Authoritative validation lives in the `sklegal_agents` package
  (`packages/agents`), which also enforces bounded-authority rules that a
  schema alone cannot express (known-tool allowlist, enabled route pins,
  classification ceiling alignment).
- `corpus-summary-input.v1.schema.json`: input contract pinned by the
  `corpus-analyst` spec. Specs pin schema artifacts by id and SHA-256, so a
  schema edit requires a new spec version.
- `tool-<name>-input.v1.schema.json` and `tool-<name>-output.v1.schema.json`:
  argument and result contracts pinned by the tool contracts in
  `packages/agents/src/sklegal_agents/contracts.py`. The tool gateway
  (`sklegal_agents.gateway`) verifies each artifact's SHA-256 against its pin
  before any call and fails closed on a mismatch, so a schema edit requires
  a new pinned contract version.
