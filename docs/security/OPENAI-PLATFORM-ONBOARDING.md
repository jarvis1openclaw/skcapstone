# OpenAI Platform onboarding checkpoint (SKL-S3-02)

This checkpoint is human-driven. No OpenAI route may serve protected matter
content until every item below is complete and evidenced. The
machine-readable form is
`config/model_gateway/openai-onboarding-checklist.json`, and a contract test
keeps both aligned.

## Credential boundary

A consumer ChatGPT subscription or browser session is never an application
credential. The application integrates only through the OpenAI Platform API
with a project-scoped API key held in the approved secret store, using the
Responses API with structured outputs. The gateway adapter resolves the key
through a secret reference at call time; the raw key never appears in
config, prompts, logs, workflow history, evidence, or committed files.

## Checklist

1. Organization and project selection. Select or create the OpenAI Platform
   organization and the exact project for SKLegal. Record the organization
   id and project id in the tenant configuration evidence.
2. API billing. Confirm Platform API billing is active on the selected
   organization. API billing is separate from any consumer ChatGPT
   subscription.
3. API key creation. Create a project-scoped API key restricted to the
   minimum required models and the Responses API. Record the key id in the
   onboarding evidence, never the raw key.
4. Retention settings. Confirm data retention settings for the project,
   request zero data retention where eligible, and confirm the application
   sends `store=false` on every Responses API call.
5. Spend limits. Set a project monthly budget and hard spend limit before
   the first application call. Record the limit values in the evidence.
6. Secret storage. Store the API key only in the approved secret store and
   record the secret reference used by the route registry (for example
   `vault:sklegal/openai/platform-api-key`).
7. Key rotation. Document and rehearse rotation: create a replacement key,
   update the secret store reference, verify a simulation submission, then
   revoke the prior key. Rotation evidence names both key ids.

## Evidence

Each completed item records the accountable human, the completion date, and
the evidence reference. Egress of confidential matter content additionally
requires the recorded human approval reference enforced by the egress gate;
privileged work product and highly restricted content are denied to external
providers regardless of onboarding state.
