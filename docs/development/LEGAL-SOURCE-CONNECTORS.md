# Legal-source connector registry

Card `SKL-S2-06` provides a governed registry for official and free legal
research sources. It deliberately does not fetch content, create accounts,
accept terms, retain credentials, or submit material to HammerTime.

The registry lives at `config/legal-sources/connector-registry.json`. It starts
with federal, Illinois, court, legislature, regulator, and Free Law Project
entries. Each entry records its jurisdiction, role, publisher, canonical and
terms locators, attribution instruction, rate budget, freshness target,
account requirement, and rights state.

## Enablement gate

An entry remains disabled and quarantined until a qualified human reviewer
records a source-specific decision. To enable it, the exact version must have:

- a reviewed compatible rights state;
- a reviewer and UTC review timestamp;
- a hash of captured terms or other rights evidence;
- explicit permitted purposes;
- an accountable account owner when an account is required; and
- a secret-store reference when an enabled account requires credentials.

The registry stores only a secret-store reference, never a token, cookie, key,
or secret URL. A human performs login, MFA, terms acceptance, billing, and any
other consequential confirmation.

## Runtime rules

`ConnectorRegistry.require_available` is a pure preflight decision. It denies
unknown, unverified, expired, revoked, disabled, and purpose-mismatched
sources. It also denies stale, rate-limited, revoked-account, and outage
health states. A caller captures the content SHA-256 at acquisition, but a
separate approved ingestion bridge owns any corpus promotion.

No entry in the initial registry is enabled. Public visibility and free access
are not rights approval. This preserves the source-rights policy while making
the inventory ready for accountable human review.
