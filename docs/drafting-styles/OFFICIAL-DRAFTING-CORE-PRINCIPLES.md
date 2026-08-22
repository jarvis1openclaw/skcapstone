# Official drafting style profiles: core-principles guide

Status: human review required. This guide is a source-linked Qwen3.8 semantic proposal for the exact normalized batch `2026-08-21-official-government-style-manuals`. It is not approval, workflow state, legal advice, or authorization for an external action.

## Operating principles

1. Select by scope. GPO-2016 is the general federal baseline, but an agency, legislative office, Federal Register, or court guide controls within its declared scope when it is more specific.
2. Preserve source differences. A style profile records each authority's modality, exception, uncertainty, contradiction, and supersession status. It does not merge incompatible rules into a universal house style.
3. Preserve the evidence chain. Each matrix rule carries an exact normalized locator, issuing body, version, and source SHA-256. The source catalog carries all 14 batch sources.
4. Treat modality literally. `must` is reserved for a source requirement, `should` for a recommendation, `may` for permission, and `informational` for scope or routing information. A model proposal never becomes workflow authority.
5. Review before use. Every rule is marked `human_review_required`. Currentness review remains open for NIJ, DOI, Seventh Circuit, EIA, and archived EPA material. The Ford-era White House manual is historical and never current.

## Profile map

| Profile | Primary source | Use |
| --- | --- | --- |
| Core principles | GPO-2016 and source routing | Select the most specific applicable authority. |
| Document settings, typography, lists, tables | DOE-TECH-STYLE-2015 | DOE technical standards only. |
| Capitalization, abbreviations, plain language, web content | NIJ-STYLE-2022 and NARA-STYLE-2024 | Agency publication and web contexts, with review of update status. |
| Punctuation, numbers, citations | DOI-CORR-2014 | DOI secretarial correspondence and its stated exceptions. |
| Correspondence and accessibility | USCG-CORR-2024 | USCG electronic and paper correspondence. |
| Legislative drafting | HOLC-STYLE-2022 and HOLC-INTRO-2025 | House legislative drafting. |
| Regulatory drafting | OFR-DDH-2018-R2.2 and OFR-IBR-2023 | Federal Register and IBR-specific work. |
| Court-specific rules | CA7-TYPOGRAPHY | Seventh Circuit papers only. |

## Conflict and review handling

When two sources apply, retain both rules and route by specificity. Do not infer supersession unless the source or the Qwen3.8 routing evidence states it. USCG-2024 explicitly cancels COMDTINST M5216.4D. Historical White House guidance is retained for provenance but is not a current recommendation. The machine-readable matrix is the authoritative record for this proposal; any publication or use requires the next release and human-review gate.

Machine-readable profile: `config/drafting_styles/official-drafting-style-profiles.json`.
Schema: `config/drafting_styles/official-drafting-style-profiles.schema.json`.
