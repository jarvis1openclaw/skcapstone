# Fleet lane admission health

`scripts/fleet/skfleet-rotate.py` creates one health snapshot before selecting
work. It fetches the configured SKGateway `/health` and `/queue` endpoints once,
resolves the exact Git revision of the process serving that endpoint port, and
atomically replaces `~/.skcapstone/evidence/fleet-lane-health.json`. No separate
publisher, timer, service, installation, or deployment is required.

Each endpoint response and the sealed snapshot are capped at 65,536 bytes. The
snapshot expires after 120 seconds. Its version 2 contract includes:

- selector cycle ID and observation time
- normalized SKGateway endpoint
- exact active gateway Git revision
- fleet lane and requested model
- configured capacity domains
- per-domain health, quarantine, owner availability, and queue capacity
- bounded acquisition errors

Admission uses only the in-memory snapshot returned by that cycle's atomic
write. It requires exact cycle, endpoint, runtime revision, lane, model, and
capacity-domain matches. A domain is usable only when SKGateway observed it as
`up` or `degraded`, did not quarantine it, and reports positive queue capacity.

Failure is lane scoped. If one model owner or capacity domain is down, ordinary
compatible work may use another healthy lane. A lane with multiple configured
capacity domains remains usable while at least one exact domain is healthy.
Missing, malformed, oversized, stale, partial, mismatched, or ambiguous evidence
does not authorize a claim. Repeated blocker records remain limited to one per
card per UTC hour.

The endpoint defaults to `http://chiap01:18790`. Operators may set
`SKFLEET_GATEWAY_URL`, `SKFLEET_GATEWAY_SSH_USER`, or the existing per-lane
`SKFLEET_*_CAPACITY_DOMAINS` variables without changing model mappings or lane
capacity.
