# Contributing to SKLegal

Read [SOP.md](SOP.md), the [project specification index](docs/PROJECT-SPEC.md), and the
[security policy](SECURITY.md) before changing code. Participation is governed by the
[Code of Conduct](CODE_OF_CONDUCT.md), and contributions retain this repository's GNU
GPL version 3 only license.

## Workflow

1. Work from an eligible, claimed SKCapstone card with its dependencies satisfied.
2. Use a focused branch after the initial repository import.
3. Add denial-path tests before changing authorization, policy, persistence, audit,
   external-action, or secret-handling boundaries.
4. Run the smallest relevant tests, then `make check`.
5. Update `CHANGELOG.md` for user-visible, architecture, security, or operational
   changes.
6. Open a pull request with the card, threat boundary, evidence, migration impact, and
   rollback plan.

Never commit client material, raw capabilities, private keys, passphrases, protected
prompts, live database exports, or unreviewed corpus content. Do not weaken a gate to
make a change pass.

Commit subjects are conventional and imperative, such as `feat:`, `fix:`, `docs:`,
`test:`, and `ci:`. AI-assisted commits include an accurate `Co-Authored-By:` trailer.

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
