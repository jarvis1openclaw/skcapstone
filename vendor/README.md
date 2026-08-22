# Vendored third-party dependencies

This directory carries third-party source vendored into the SKLegal
repository so the build never fetches from a personal remote at install time.

## capauth

`vendor/capauth` is CapAuth `0.3.1`, copied from upstream commit
`183c04a7c623e8abcf37bd705bf8bca1deb4a364` of
`https://github.com/smilinTux/capauth.git`. The provenance manifest
`vendor/capauth/VENDOR-MANIFEST.json` records the upstream commit, the
package version, the included and excluded upstream paths, the local build
patch, and a SHA256 for every vendored file.
`scripts/check_vendor_capauth.py` verifies the tree against the manifest and
fails closed on any missing, added, or altered file. The full contract and
the update path for future upstream revisions are documented in
`docs/development/CAPAUTH.md`.

Vendored content is verbatim upstream source under its own license
(GPL-3.0-or-later for capauth, see `vendor/capauth/LICENSE`). It keeps
upstream editorial style and is excluded from SKLegal lint, format, and type
check targets. The detect-secrets scan also excludes `vendor/` because the
provenance manifest already hash-pins every vendored file; SKLegal-authored
code remains fully scanned. Build artifacts (`__pycache__`, `*.egg-info`,
`*.pyc`) are never vendored and are ignored by the manifest.
