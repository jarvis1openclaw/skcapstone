# doc-haus license and provenance audit

Audit card: `SKL-S0-05`

Audit date: 2026-08-20

Auditor identity: `codex-licensing`

## Outcome

Three narrowly bounded source candidates are approved for future attributed extraction or adaptation: the pure citation core, review-grid domain core, and DOCX proposal-conflict core. Approval applies only to the exact line segments in the machine manifest. It is not approval to import a whole file, dependency, asset, generated artifact, workbench, or OpenCode fork.

`SKL-S0-05` copied no doc-haus source code. Its no-copy gate remains strict for every candidate. A later implementation card must add an exact source-segment-to-destination mapping, preserve MIT license and copyright notices, test the adapted behavior, and add a narrow reviewed scanner exception before attributed source may appear in SKLegal. Rejected candidates require independent behavioral reimplementation whose implementers do not receive the rejected source.

The XLSX export and Docxodus tracked-changes adapter require further permission and notice resolution. The browser DOCX viewer, runtime citation adapters, matter workbench source, and OpenCode fork modifications are rejected for direct extraction. Media, branding, legal-content playbooks, and unresolved copied-source boundaries remain quarantined.

This report is engineering provenance evidence, not legal advice. Counsel or an authorized rights owner should review trademark use, corpus ingestion, distribution notices, and every `requires_permission` boundary.

## Deterministic repository and lineage pin

| Field | Evidence |
| --- | --- |
| Repository | Local clone origin is `https://github.com/sure-scale/doc-haus.git` |
| Branch and HEAD | `dochaus` at `f3cdfd15f7b675e651773b3f6a8ef9468f56ed14` |
| Local origin tracking ref | `refs/remotes/origin/dochaus` exactly matches audited HEAD |
| Read-only origin observation | Remote `dochaus` observed at the same commit at `2026-08-20T05:43:20Z`; informational, not the offline pin |
| Origin default branch | Local symbolic ref resolves to `dochaus` |
| Configured remotes | Exact set is one remote named `origin`; no `upstream` remote is configured |
| Working tree | Clean; empty porcelain SHA256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| Submodules and HEAD tags | None |
| Authoritative lineage base | `134f4136da372fdf8f0c3cec2cea3ff81010baf8` |
| First fork commit | Earliest local ancestry-path commit is `eec2e0a25fa446212ba6de21f2c6f252140aaf4d`; its parent is the base and it is an ancestor of HEAD |
| Commits and delta after base | 202 commits; 328 files, 31,888 insertions, 3,091 deletions |
| Volatile upstream observation | `anomalyco/opencode` `dev` observed at `e0e9bd7d5f2ae9bfa47dae0b82040c57207c9b24` at `2026-08-20T05:25:06Z`; informational only |

The scanner locally proves the configured remote set, origin symbolic default, origin tracking ref, base ancestry, derived first fork and parentage, first-fork ancestry, commit count, and aggregate numstat. The claimed official upstream repository and read-only remote observations are external evidence linked in the manifest, not facts inferred from local Git configuration.

## License, notice, and source boundaries

The root MIT file contains both `Copyright (c) 2026 doc.haus contributors` and `Copyright (c) 2025 opencode`. The repository-level source grant does not automatically resolve copied, vendored, generated, dependency, asset, font, icon, model, or legal-content rights.

| Boundary | Evidence | Required treatment |
| --- | --- | --- |
| Root source | MIT, exact file hash pinned | Preserve both root notices and the MIT license text for attributed extraction or adaptation |
| `packages/docs` | Separate Mintlify MIT license and 2023 notice | Preserve its narrower notice if used |
| Desktop webview zoom | `Apache-2.0 OR MIT`, Tauri Programme 2019-2024 | Preserve the applicable dual-license choice and notice |
| Proxy environment adapter | Inline MIT, Rob Wu 2016-2018 | Preserve its file-specific license and notice |
| Effect Drizzle SQLite | Local manifest says MIT, but copied Drizzle provenance is incomplete | Quarantine until exact copied-source and notice records exist |
| xterm serialize port | Substantial xterm.js addon-serialize port, no embedded exact provenance or notice | `NOASSERTION`; do not extract |
| pi-mono error patterns | Mutable `main` reference, no pinned upstream commit or adaptation map | `NOASSERTION`; do not extract |
| Package declarations | 34 manifests | Inventory includes dependencies, dev, peer, optional, patched mappings, overrides, trusted dependencies, peer metadata, imports, and workspaces |
| Bun locks | 7 locks and 3,857 records | Lock records, not manifest ranges or declarations, control exact candidate dependency identity |
| Patches | 12 exact patch files | Treat each as a derivative boundary tied to an exact dependency |
| Tracked files | 5,822 exact Git-blob hashes | Full byte-for-byte no-copy comparison boundary |
| Implementation sources | 2,798 exact hashes | Separate source-focused inventory retained for review |
| Generated files | 334 exact files | Includes OpenAPI schema and alias, SDK, SST and migration output, four snapshots, the committed debug log, and four explicit generated-header source files |
| Assets | 1,650 exact files | Includes audio, icons, Android icon XML, brand ZIPs, web manifests, fonts, media, documents, and spreadsheets |

Manifest metadata is declaration-only. `optionalDependencies`, patch mappings, overrides, trusted dependencies, peer metadata, imports, and workspace configuration improve provenance coverage but do not prove an installed or transitive graph. Exact locked identity comes from the seven Bun locks. The desktop entitlements plist was inspected as security-sensitive configuration and remains covered by the complete tracked-file hash inventory; it is not a reuse candidate.

The three local `docxodus@6.4.0.patch` files share the same pinned SHA256. The patch is part of the quarantined Docxodus adapter provenance, not an independent source grant.

## Legal-content rights discovered

This audit discovered corpus-source rights work that belongs in a future governed corpus card. Root MIT does not control copied or adapted third-party legal content.

| Pinned local content | Local rights statement | Disposition |
| --- | --- | --- |
| NDA playbook | Bonterms material is described as public domain; oneNDA is described as CC-BY-ND reference-only with no copied text | `NOASSERTION`; exact Bonterms version, hash, adaptation map, and oneNDA non-copy verification remain unresolved |
| DPA playbook | Common Paper DPA is described as CC-BY-4.0 | `NOASSERTION`; exact version, hash, and attribution map remain unresolved |
| SaaS MSA playbook | Common Paper and Bonterms sources are described as CC-BY-4.0 | `NOASSERTION`; both exact source chains and attribution maps remain unresolved |
| CUAD taxonomy | CUAD category names are described as CC-BY-4.0 | `NOASSERTION`; exact release, hash, and local mapping attribution remain unresolved |

The oneNDA CC-BY-ND statement is reference-only and is not a grant to copy or adapt oneNDA text. None of these four files may enter the SKLegal corpus until a governed corpus card resolves exact upstream provenance, license terms, attribution, source-to-local mapping, and ingestion policy.

## Dependency inventory and CycloneDX SBOM

The deterministic CycloneDX 1.6 artifact is explicitly a flat locked-component inventory. It does not claim to contain dependency edges or a complete transitive graph. It preserves every raw resolution and classifies 2,859 unique registry packages, 28 workspace resolutions, one Git resolution, and one URL resolution. Only actual registry packages receive npm purls.

The seven locks contain 3,857 package records and 2,889 unique component identities. Exactly 13 unique registry components have matching name, version, SRI, and license evidence. The other 2,876 are `NOASSERTION` and quarantined. All 13 tarball SRI values were independently recomputed and matched. The exact Docxodus and OpenCode plugin tarballs contain no LICENSE or NOTICE file, so those records are not distribution-ready notice bundles.

| Component | Exact version | License | Engineering status |
| --- | --- | --- | --- |
| `docxodus` | 6.4.0 | MIT | Immutable upstream license evidence exists, but tarball notice, embedded, transitive, and patch boundaries keep adapter adoption quarantined |
| `xlsx` | 0.18.5 | Apache-2.0 | Exact tarball license pinned; export remains quarantined pending bundled and transitive notice review |
| `docx` | 9.7.1 | MIT | Exact tarball license pinned; dependency-only evidence |
| Space Grotesk Fontsource | 5.2.10 | OFL-1.1 | Exact tarball license pinned; does not authorize unrelated font or branding assets |
| React and ReactDOM | 19.2.7 | MIT | Exact lock and tarball license evidence |
| React Markdown | 10.1.0 | MIT | Exact lock and tarball license evidence |
| React Router DOM | 7.17.0 | MIT | Exact lock and tarball license evidence |
| Mammoth | 1.12.0 | BSD-2-Clause | Exact lock and tarball license evidence |
| unpdf | 1.6.2 | MIT | Exact lock and tarball license evidence |
| Hono | 4.10.7 | MIT | Exact lock and tarball license evidence |
| fflate | 0.8.3 | MIT | Exact lock and tarball license evidence |
| OpenCode plugin | 1.16.0 | MIT | Exact SRI and registry metadata; tarball lacks LICENSE or NOTICE and is not distribution-ready |

## Candidate decisions

Any file or line not explicitly listed as approved is not approved. Complementary citation ranges are represented separately so machine decisions never overlap across dispositions.

| Candidate | Disposition | Exact boundary | Primary risk |
| --- | --- | --- | --- |
| Pure citation core | `approved_for_attributed_extraction` | `citations.ts` 9-28, `extract.ts` 31-55, `verify-quote.ts` 23-56 | Duplicate passages, empty or long input, regex cost, Unicode normalization, and raw-offset correctness |
| Citation runtime and rendering adapters | `rejected` | Exact complementary ranges plus whole CitationView, Markdown, and cite runtime files | Rendering safety, live paths, database tenancy, and tool authority |
| Review-grid domain core | `approved_for_attributed_extraction` | `ReviewGrid.tsx` 15-25 and `grid.ts` 12-34 | Collision-prone identity and missing immutable receipts |
| Review-grid XLSX export | `requires_permission` | `ReviewGrid.tsx` 178-211 | Bundled and transitive rights, formula safety, and content safety |
| DOCX proposal-conflict core | `approved_for_attributed_extraction` | `redlines.ts` 54-65 and 80-97 | Unicode, repeated text, stale anchors, and concurrency |
| Docx tracked-changes adapter | `requires_permission` | Exact listed adapter files only | Docxodus notice chain, WebAssembly packaging, patch provenance, and mutation authority |
| Browser DOCX viewer | `rejected` | No extraction | Converted-HTML rendering and browser mutation |
| Matter workbench shell | `rejected` | Information architecture may be studied without source access by implementers | Missing tenancy and policy boundaries, browser state, and OpenCode coupling |
| OpenCode fork modifications | `rejected` | Behavioral requirements only | Large dependency surface, file authority, drift, and absent legal policy model |
| Branding, templates, fixtures | `requires_permission` | No copy or modification | Trademark, authorship, embedded content, personal data, and provenance |

The three attributed-extraction candidates carry no package dependencies. Every non-`NOASSERTION` dependency listed on any candidate disposition must resolve to an exact manifest evidence record and exact lock component. Every resolved evidence record must occur in the locks.

## No-copy and offline integrity controls

`make license-audit` and direct stored-artifact validation are offline. They validate the pinned manifest, all inventory section counts and canonical hashes, CycloneDX deterministic projection and schema, license evidence, candidate vocabulary and coverage, non-overlapping line decisions, exact lock closure, dirty-state evidence, and `NOASSERTION` quarantine. The broader `make check` also runs bootstrap and vulnerability checks that may use the network.

The no-copy gate compares every Git-eligible SKLegal file against exact SHA256 values for all 5,822 tracked doc-haus files. For Git-eligible SKLegal files with a suffix in `IMPLEMENTATION_SUFFIXES`, it also checks three stride-1 32-token streams derived only from exact candidate source segments, including the pinned Docxodus patch candidate. The semantic stream strips comments and replaces each whole string with its digest to reduce noise; it contains 25,312 distinct stored fingerprints. The verbatim lexical stream preserves identifiers, operators, and text inside comments and strings; it contains 49,392 distinct stored fingerprints. The wrapper-neutral lexical-content stream preserves source text while removing common carrier delimiters for line, block, SQL, hash, and HTML comments; it contains 47,832 distinct stored fingerprints. The stored wrapper binds all three algorithms, all 30 records, all three counts, manifest-owned file line counts, exact segment ranges and purposes, and one canonical hash. Exact digests are checked before text classification. Git-eligible symlinks, including broken symlinks, fail closed. There are no audit-directory prefix exclusions.

The transformed-copy checks are bounded heuristics, not proof against arbitrary transformation. Fragments below 12 tokens and partial copies stored only in other text or configuration suffixes are outside the transformed-copy guarantee, although the whole-file SHA256 comparison still applies. Deliberate AST rewrites, systematic token injection, or transformations that destroy every contiguous 32-token window can also evade the heuristics. Future attributed extraction therefore requires reviewed source mappings and exceptions, while independent behavioral reimplementation retains provenance without giving implementers rejected source.

One exact non-implementation collision is explicitly reviewed: SKLegal `apps/web/.prettierignore` and doc-haus `packages/plugin/.gitignore` are both the same five-byte common `dist` ignore entry. The exception binds both exact paths and the exact digest, cannot apply to implementation or candidate source, and fails if unused or changed.

A separate live command requires explicit `DOC_HAUS_ROOT`. It re-derives the local lineage facts, verifies every pinned file and segment boundary, and proves the rights inventory and SBOM still match the clean target without executing project code. Among the S0-05 provenance artifacts, only the generated rights inventory and SBOM JSON are newly excluded from the secret scan because they contain thousands of expected integrities and fingerprints. Existing standard exclusions also cover tool, cache, build, and dependency directories, the reviewed baseline, and lockfiles. The manifest, report, scanner, and tests remain scanned.

## GPL-3.0-only boundary

This is a narrow engineering interpretation, not legal advice. MIT source is generally compatible with incorporation into a GPLv3 work. Copied or adapted MIT segments retain their MIT attribution, copyright, and license-notice duties. A combined SKLegal distribution may remain `GPL-3.0-only` without claiming that the upstream MIT work was relicensed. Independent behavioral reimplementation remains distinct from attributed source reuse, and both paths must retain provenance records.

## Recommended next actions

1. Create separate attributed-extraction implementation cards for the three approved candidates. Require exact source-to-destination mappings, preserved notices, behavior and adversarial tests, and narrow reviewed scanner exceptions.
2. Keep rejected-source implementers independent from the rejected source. Give them behavior specifications and tests, not code.
3. Resolve the xlsx bundled and transitive notice chain before adopting the exporter.
4. Resolve Docxodus tarball notice absence, embedded binaries, transitive packages, and all exact patches before reconsidering the tracked-changes adapter.
5. Create a governed corpus-source rights card for Bonterms, Common Paper, oneNDA references, CUAD, and future legal materials before ingestion.
6. Create SKLegal-owned branding and synthetic document fixtures. Do not reuse quarantined doc-haus assets.
7. Generate a release SBOM from the actual SKLegal dependency graph at every release gate. This audit SBOM is a flat inventory of the inspected source candidate, not a future SKLegal release graph.

## Rollback and scope assurance

The audit made no changes to doc-haus or any upstream repository and executed no project code. Rolling back this card consists only of removing the SKLegal audit scanner, manifest, tests, report, generated artifacts, and foundation-check integration. No services, corpus, Inbox, accounts, deployments, or external writes were involved.
