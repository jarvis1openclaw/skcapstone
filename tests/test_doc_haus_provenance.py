from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import audit_doc_haus_provenance as audit  # noqa: E402

SOURCE = """export function calculate(values: number[]) {
  const filtered = values.filter((value) => value > 0)
  const doubled = filtered.map((value) => value * 2)
  const total = doubled.reduce((left, right) => left + right, 0)
  const average = doubled.length === 0 ? 0 : total / doubled.length
  return { filtered, doubled, total, average, count: doubled.length }
}
"""


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def fixture_repository(root: Path) -> dict[str, object]:
    (root / "src").mkdir()
    (root / "assets").mkdir()
    (root / "generated" / "gen").mkdir(parents=True)
    (root / "patches").mkdir()
    (root / "packages" / "desktop" / "icons" / "dev" / "android" / "values").mkdir(
        parents=True
    )
    (root / "packages" / "desktop" / "resources").mkdir(parents=True)
    (root / "packages" / "storybook").mkdir(parents=True)
    (root / "LICENSE").write_text("Synthetic MIT fixture\n", encoding="utf-8")
    (root / "src" / "candidate.ts").write_text(SOURCE, encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "synthetic-audit-fixture",
                "version": "1.0.0",
                "private": True,
                "dependencies": {"known": "1.0.0", "unknown": "2.0.0"},
                "optionalDependencies": {"optional-known": "3.0.0"},
                "patchedDependencies": {"known@1.0.0": "patches/known.patch"},
                "overrides": {"known": "1.0.0"},
                "trustedDependencies": ["known"],
                "peerDependenciesMeta": {"unknown": {"optional": True}},
                "imports": {"#fixture": "./src/candidate.ts"},
                "workspaces": ["packages/*"],
            }
        ),
        encoding="utf-8",
    )
    integrity = "sha256-" + "A" * 43 + "="
    (root / "bun.lock").write_text(
        "{\n"
        '  "lockfileVersion": 1,\n'
        '  "packages": {\n'
        f'    "known": ["known@1.0.0", "", {{}}, "{integrity}"],\n'
        f'    "unknown": ["unknown@2.0.0", "", {{}}, "{integrity}"],\n'
        "  },\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "assets" / "fixture.pdf").write_bytes(b"synthetic pdf fixture")
    for name in ("sound.aac", "icon.icns", "brand.zip", "site.webmanifest"):
        (root / "assets" / name).write_bytes(f"synthetic {name}".encode())
    (
        root
        / "packages"
        / "desktop"
        / "icons"
        / "dev"
        / "android"
        / "values"
        / "ic_launcher.xml"
    ).write_text("<resources/>\n", encoding="utf-8")
    (root / "packages" / "desktop" / "resources" / "entitlements.plist").write_text(
        "<plist/>\n", encoding="utf-8"
    )
    (root / "generated" / "gen" / "client.ts").write_text(
        "export const generated = true\n", encoding="utf-8"
    )
    (root / "patches" / "known.patch").write_text(
        "synthetic patch fixture\n", encoding="utf-8"
    )
    (root / "generated" / "result.snap").write_text(
        "synthetic snapshot\n", encoding="utf-8"
    )
    (root / "packages" / "storybook" / "debug-storybook.log").write_text(
        "synthetic debug log\n", encoding="utf-8"
    )
    git(root, "init", "-q")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "SKLegal Fixture")
    git(root, "remote", "add", "origin", "https://example.invalid/fixture.git")
    git(root, "add", ".")
    git(root, "commit", "-qm", "upstream base fixture")
    upstream_base = git(root, "rev-parse", "HEAD")
    (root / "fork-marker.txt").write_text("synthetic fork\n", encoding="utf-8")
    git(root, "add", "fork-marker.txt")
    git(root, "commit", "-qm", "first fork fixture")
    first_fork_commit = git(root, "rev-parse", "HEAD")
    branch = git(root, "branch", "--show-current")
    git(
        root,
        "update-ref",
        f"refs/remotes/origin/{branch}",
        first_fork_commit,
    )
    git(
        root,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        f"refs/remotes/origin/{branch}",
    )
    snapshot = audit.repository_snapshot(root)
    source_digest = audit.sha256_file(root / "src" / "candidate.ts")
    candidates = []
    for category in sorted(audit.REQUIRED_CATEGORIES):
        candidates.append(
            {
                "id": f"fixture-{category}",
                "category": category,
                "disposition": "rejected",
                "paths": [{"path": "src/candidate.ts", "sha256": source_digest}],
                "license_basis": "Synthetic fixture license evidence.",
                "copyright_notice_obligations": "Keep the fixture notice.",
                "dependencies": [],
                "engineering_fit": "Fixture-only validation.",
                "extraction_boundary": "Do not extract fixture source.",
                "unresolved_risks": "Synthetic behavior is intentionally narrow.",
            }
        )
    repository = {
        "head": snapshot["head"],
        "head_commit_time": snapshot["head_commit_time"],
        "branch": snapshot["branch"],
        "configured_remotes": snapshot["configured_remotes"],
        "origin": snapshot["origin"],
        "origin_branch_head": first_fork_commit,
        "origin_branch_observed_head": first_fork_commit,
        "origin_branch_observed_at": "2026-08-20T00:00:00Z",
        "origin_branch_observation_role": "Synthetic informational fixture.",
        "origin_default_branch": branch,
        "upstream_remote_configured": False,
        "upstream_base": upstream_base,
        "first_fork_commit": first_fork_commit,
        "commits_after_upstream_base": 1,
        "delta_from_upstream_base": {
            "files_changed": 1,
            "insertions": 1,
            "deletions": 0,
        },
        "submodules": [],
        "tags_at_head": [],
        "working_tree": snapshot["working_tree"],
    }
    manifest = {
        "schema": "sklegal-doc-haus-provenance/v1",
        "source_approval_default": "not_approved",
        "source_approval_rule": "Every unlisted fixture source line is not approved.",
        "future_attributed_extraction_gate": {
            "current_card": "The fixture audit copied no source.",
            "required_future_control": "A future fixture card must map and review reuse.",
        },
        "exact_hash_exceptions": [],
        "candidate_text_line_counts": {"src/candidate.ts": len(SOURCE.splitlines())},
        "repository": repository,
        "resolved_component_evidence_scope": {
            "verification": "Synthetic exact fixture package evidence.",
            "distribution_limitation": "Not a distribution-ready notice bundle.",
            "tarball_license_notice_absences": [],
        },
        "license_evidence": [
            {
                "path": "LICENSE",
                "sha256": audit.sha256_file(root / "LICENSE"),
                "license": "MIT",
                "notices": ["Synthetic fixture notice"],
                "scope": "Fixture files only",
            }
        ],
        "third_party_content_boundaries": [
            {
                "id": "fixture-content",
                "path": "fork-marker.txt",
                "sha256": audit.sha256_file(root / "fork-marker.txt"),
                "rights": "NOASSERTION",
                "quarantined": True,
                "exact_upstream_provenance": "unresolved",
                "declared_rights": [
                    {"source": "Synthetic content", "claim": "Fixture-only claim"}
                ],
                "rule": "Do not reuse fixture content.",
            }
        ],
        "copied_adapted_source_boundaries": [
            {
                "id": "fixture-adapted-source",
                "path": "src/candidate.ts",
                "sha256": source_digest,
                "rights": "NOASSERTION",
                "quarantined": True,
                "exact_upstream_provenance": "unresolved",
                "declared_rights": [
                    {"source": "Synthetic source", "claim": "Fixture-only claim"}
                ],
                "rule": "Do not reuse fixture adapted source.",
            }
        ],
        "resolved_third_party_components": [
            {
                "name": "known",
                "version": "1.0.0",
                "license": "MIT",
                "integrity": integrity,
                "evidence": "https://example.invalid/known/1.0.0",
                "notices": ["Synthetic known package notice"],
                "tarball_license_path": "package/LICENSE",
                "tarball_license_sha256": "1" * 64,
            }
        ],
        "candidates": candidates,
        "quarantined_boundaries": [
            {
                "id": "fixture-unknown",
                "scope": "Unknown fixture package",
                "rights": "NOASSERTION",
                "quarantined": True,
                "rule": "Do not extract unknown fixture source.",
            }
        ],
    }
    rights = audit.build_rights_inventory(root, manifest, "0" * 64)
    manifest["expected_inventory"] = audit.inventory_expectations(rights)
    return manifest


class ProvenanceTests(unittest.TestCase):
    def test_repository_manifest_is_complete(self) -> None:
        manifest = audit.load_json(
            REPO_ROOT / "config" / "provenance" / "doc-haus-audit.json"
        )
        audit.validate_manifest(manifest)
        dispositions = {item["disposition"] for item in manifest["candidates"]}
        self.assertEqual(audit.DISPOSITIONS, dispositions)
        docx_core = next(
            item
            for item in manifest["candidates"]
            if item["id"] == "docx-proposal-conflict-core"
        )
        segments = docx_core["paths"][0]["segments"]
        self.assertEqual(
            [(54, 65), (80, 97)], [(s["start_line"], s["end_line"]) for s in segments]
        )
        self.assertIn(".patch", audit.CANDIDATE_TEXT_SUFFIXES)

    def test_invalid_disposition_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = fixture_repository(Path(temporary))
            manifest["candidates"][0]["disposition"] = "maybe"  # type: ignore[index]
            with self.assertRaisesRegex(audit.AuditError, "invalid candidate"):
                audit.validate_manifest(manifest)

    def test_missing_candidate_category_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = fixture_repository(Path(temporary))
            manifest["candidates"].pop()  # type: ignore[union-attr]
            with self.assertRaisesRegex(audit.AuditError, "categories are missing"):
                audit.validate_manifest(manifest)

    def test_unknown_candidate_category_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = fixture_repository(Path(temporary))
            manifest["candidates"][0]["category"] = "unknown"  # type: ignore[index]
            with self.assertRaisesRegex(audit.AuditError, "invalid candidate category"):
                audit.validate_manifest(manifest)

    def test_candidate_ranges_cannot_overlap_across_dispositions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = fixture_repository(Path(temporary))
            first = manifest["candidates"][0]  # type: ignore[index]
            second = manifest["candidates"][1]  # type: ignore[index]
            first["disposition"] = "approved_for_attributed_extraction"
            first["paths"][0]["segments"] = [  # type: ignore[index]
                {"start_line": 1, "end_line": 4, "purpose": "approved fixture"}
            ]
            second["paths"][0]["segments"] = [  # type: ignore[index]
                {"start_line": 4, "end_line": 8, "purpose": "rejected fixture"}
            ]
            with self.assertRaisesRegex(audit.AuditError, "overlap"):
                audit.validate_manifest(manifest)

    def test_license_evidence_digest_and_record_types_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = fixture_repository(Path(temporary))
            broken = copy.deepcopy(manifest)
            broken["license_evidence"][0]["sha256"] = "bad"  # type: ignore[index]
            with self.assertRaisesRegex(audit.AuditError, "invalid sha256"):
                audit.validate_manifest(broken)

    def test_supplemental_rights_boundaries_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = fixture_repository(Path(temporary))
            broken = copy.deepcopy(manifest)
            boundary = broken["third_party_content_boundaries"][0]  # type: ignore[index]
            boundary["quarantined"] = False
            with self.assertRaisesRegex(
                audit.AuditError, "NOASSERTION and quarantined"
            ):
                audit.validate_manifest(broken)
            broken = copy.deepcopy(manifest)
            boundary = broken["third_party_content_boundaries"][0]  # type: ignore[index]
            boundary["rights"] = "MIT"
            with self.assertRaisesRegex(
                audit.AuditError, "NOASSERTION and quarantined"
            ):
                audit.validate_manifest(broken)
            broken = copy.deepcopy(manifest)
            boundary = broken["copied_adapted_source_boundaries"][0]  # type: ignore[index]
            boundary["sha256"] = "bad"
            with self.assertRaisesRegex(audit.AuditError, "invalid sha256"):
                audit.validate_manifest(broken)
            broken = copy.deepcopy(manifest)
            broken["license_evidence"][0]["notices"] = "not-a-list"  # type: ignore[index]
            with self.assertRaisesRegex(audit.AuditError, "notices"):
                audit.validate_manifest(broken)

    def test_approved_candidate_cannot_carry_quarantined_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = fixture_repository(Path(temporary))
            candidate = manifest["candidates"][0]  # type: ignore[index]
            candidate["disposition"] = "approved_for_attributed_extraction"
            candidate["dependencies"] = [
                {
                    "name": "unknown",
                    "version": "2.0.0",
                    "license": "NOASSERTION",
                    "adoption": "quarantined",
                }
            ]
            with self.assertRaisesRegex(audit.AuditError, "quarantined dependency"):
                audit.validate_manifest(manifest)

    def test_dirty_snapshot_has_deterministic_content_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture_repository(root)
            modified = b"modified synthetic fixture\n"
            untracked = b"untracked synthetic fixture\n"
            (root / "LICENSE").write_bytes(modified)
            (root / "untracked.txt").write_bytes(untracked)
            snapshot = audit.working_tree_snapshot(root)
            self.assertFalse(snapshot["clean"])
            entries = {item["path"]: item for item in snapshot["entries"]}
            self.assertEqual(digest(modified), entries["LICENSE"]["sha256"])
            self.assertEqual(digest(untracked), entries["untracked.txt"]["sha256"])

    def test_inventory_covers_boundaries_and_quarantines_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            rights = audit.build_rights_inventory(root, manifest, "2" * 64)
            self.assertEqual(1, len(rights["package_manifests"]))
            self.assertEqual(1, len(rights["lockfiles"]))
            self.assertEqual(1, len(rights["patches"]))
            self.assertEqual(6, len(rights["assets"]))
            self.assertEqual(3, len(rights["generated_files"]))
            tracked_paths = {item["path"] for item in rights["tracked_files"]}
            self.assertIn(
                "packages/desktop/resources/entitlements.plist", tracked_paths
            )
            asset_paths = {item["path"] for item in rights["assets"]}
            self.assertIn(
                "packages/desktop/icons/dev/android/values/ic_launcher.xml", asset_paths
            )
            generated_paths = {item["path"] for item in rights["generated_files"]}
            self.assertIn("generated/result.snap", generated_paths)
            self.assertIn("packages/storybook/debug-storybook.log", generated_paths)
            unknown = [
                item for item in rights["components"] if item["name"] == "unknown"
            ]
            self.assertEqual("NOASSERTION", unknown[0]["license"])
            self.assertTrue(unknown[0]["quarantined_for_extraction"])

    def test_generated_header_paths_are_classified(self) -> None:
        paths = {
            "packages/docs/openapi.json",
            "packages/sdk/openapi.json",
            "packages/core/src/database/migration.gen.ts",
            "packages/console/core/migrations/20250902065410_fluffy_raza/migration.sql",
            "packages/core/migration/20260127222353_familiar_lady_ursula/migration.sql",
            "packages/core/src/database/migration/20260127222353_familiar_lady_ursula.ts",
            "packages/opencode/migration/20260511173437_session-metadata/migration.sql",
            "packages/stats/core/migrations/20260522121617_common_dust/migration.sql",
            "packages/ui/src/styles/tailwind/colors.css",
            "packages/ui/src/components/app-icons/types.ts",
            "packages/ui/src/components/provider-icons/types.ts",
            "packages/ui/src/components/file-icons/types.ts",
        }
        self.assertTrue(all(audit.is_generated_path(path) for path in paths))

    def test_package_manifest_records_declaration_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            rights = audit.build_rights_inventory(root, manifest, "f" * 64)
            package = rights["package_manifests"][0]
            scopes = {item["scope"] for item in package["dependencies"]}
            self.assertIn("optionalDependencies", scopes)
            self.assertEqual(
                {"known@1.0.0": "patches/known.patch"},
                package["patched_dependencies"],
            )
            self.assertEqual({"known": "1.0.0"}, package["overrides"])
            self.assertEqual(["known"], package["trusted_dependencies"])
            self.assertEqual(
                {"unknown": {"optional": True}}, package["peer_dependencies_meta"]
            )
            self.assertEqual({"#fixture": "./src/candidate.ts"}, package["imports"])
            self.assertEqual(["packages/*"], package["workspaces"])

    def test_integrity_mismatch_fails_closed_to_noassertion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            manifest["resolved_third_party_components"][0]["integrity"] = (  # type: ignore[index]
                "sha256-" + "B" * 43 + "="
            )
            rights = audit.build_rights_inventory(root, manifest, "7" * 64)
            known = [item for item in rights["components"] if item["name"] == "known"]
            self.assertEqual("NOASSERTION", known[0]["license"])
            self.assertTrue(known[0]["quarantined_for_extraction"])

    def test_offline_inventory_rejects_tampered_resolved_license(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            rights = audit.build_rights_inventory(root, manifest, "8" * 64)
            known = [item for item in rights["components"] if item["name"] == "known"]
            known[0]["license"] = "Apache-2.0"
            manifest["expected_inventory"] = audit.inventory_expectations(rights)
            with self.assertRaisesRegex(audit.AuditError, "exact lockfile projection"):
                audit.validate_rights_inventory(rights, manifest, "8" * 64)

    def test_approved_candidate_dependency_must_exist_in_exact_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            candidate = manifest["candidates"][0]  # type: ignore[index]
            for item in manifest["candidates"]:  # type: ignore[union-attr]
                item["disposition"] = "approved_for_attributed_extraction"
            candidate["dependencies"] = [
                {
                    "name": "known",
                    "version": "9.0.0",
                    "license": "MIT",
                    "adoption": "dependency_only",
                }
            ]
            resolved = manifest["resolved_third_party_components"][0]  # type: ignore[index]
            resolved["version"] = "9.0.0"
            audit.validate_manifest(manifest)
            rights = audit.build_rights_inventory(root, manifest, "9" * 64)
            manifest["expected_inventory"] = audit.inventory_expectations(rights)
            with self.assertRaisesRegex(audit.AuditError, "absent from exact lock"):
                audit.validate_rights_inventory(rights, manifest, "9" * 64)

    def test_nonapproved_candidate_dependency_requires_exact_resolved_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = fixture_repository(Path(temporary))
            for disposition in ("rejected", "requires_permission"):
                with self.subTest(disposition=disposition):
                    broken = copy.deepcopy(manifest)
                    candidate = broken["candidates"][0]  # type: ignore[index]
                    candidate["disposition"] = disposition
                    candidate["dependencies"] = [
                        {
                            "name": "known",
                            "version": "9.0.0",
                            "license": "MIT",
                            "adoption": "excluded",
                        }
                    ]
                    with self.assertRaisesRegex(
                        audit.AuditError, "exact resolved evidence"
                    ):
                        audit.validate_manifest(broken)

    def test_offline_inventory_rejects_truncated_components(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            rights = audit.build_rights_inventory(root, manifest, "d" * 64)
            rights["components"].pop()
            with self.assertRaisesRegex(audit.AuditError, "components count mismatch"):
                audit.validate_rights_inventory(rights, manifest, "d" * 64)

    def test_offline_inventory_rejects_mutated_lockfile_section(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            rights = audit.build_rights_inventory(root, manifest, "e" * 64)
            rights["lockfiles"][0]["packages"].pop()
            with self.assertRaisesRegex(audit.AuditError, "lockfiles digest mismatch"):
                audit.validate_rights_inventory(rights, manifest, "e" * 64)

    def test_offline_inventory_rejects_emptied_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            rights = audit.build_rights_inventory(root, manifest, "1" * 64)
            for record in rights["candidate_source_fingerprints"]["records"]:
                record["fingerprints"] = []
                record["verbatim_fingerprints"] = []
                for segment in record["segment_fingerprints"]:
                    segment["fingerprints"] = []
                    segment["verbatim_fingerprints"] = []
            with self.assertRaisesRegex(audit.AuditError, "fingerprint count mismatch"):
                audit.validate_rights_inventory(rights, manifest, "1" * 64)

    def test_offline_inventory_rejects_emptied_verbatim_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            rights = audit.build_rights_inventory(root, manifest, "1" * 64)
            for record in rights["candidate_source_fingerprints"]["records"]:
                record["verbatim_fingerprints"] = []
                for segment in record["segment_fingerprints"]:
                    segment["verbatim_fingerprints"] = []
            with self.assertRaisesRegex(
                audit.AuditError, "verbatim fingerprint count mismatch"
            ):
                audit.validate_rights_inventory(rights, manifest, "1" * 64)

    def test_offline_inventory_rejects_emptied_wrapper_stripped_fingerprints(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            rights = audit.build_rights_inventory(root, manifest, "1" * 64)
            for record in rights["candidate_source_fingerprints"]["records"]:
                record["wrapper_stripped_fingerprints"] = []
                for segment in record["segment_fingerprints"]:
                    segment["wrapper_stripped_fingerprints"] = []
            with self.assertRaisesRegex(
                audit.AuditError, "wrapper-stripped fingerprint count mismatch"
            ):
                audit.validate_rights_inventory(rights, manifest, "1" * 64)

    def test_cyclonedx_is_deterministic_and_quarantines_noassertion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            rights = audit.build_rights_inventory(root, manifest, "3" * 64)
            first = audit.build_cyclonedx(rights, manifest, "3" * 64)
            second = audit.build_cyclonedx(rights, manifest, "3" * 64)
            self.assertEqual(audit.canonical_json(first), audit.canonical_json(second))
            self.assertEqual("1.6", first["specVersion"])
            unknown = [
                item for item in first["components"] if item["name"] == "unknown"
            ][0]
            self.assertEqual(
                "true",
                audit.properties(unknown)["sklegal:audit:quarantined-for-extraction"],
            )
            self.assertNotIn("timestamp", first["metadata"])
            self.assertEqual(
                "absent-flat-locked-component-inventory",
                audit.properties(first["metadata"])["sklegal:audit:dependency-graph"],
            )

    def test_resolution_types_do_not_create_false_registry_purls(self) -> None:
        scoped_url = audit.parse_resolution(
            "@solidjs/start@https://pkg.pr.new/@solidjs/start@dfb2020",
            "@solidjs/start",
        )
        workspace = audit.parse_resolution(
            "@opencode-ai/app@workspace:packages/app", "@opencode-ai/app"
        )
        github = audit.parse_resolution(
            "ghostty-web@github:anomalyco/ghostty-web#20bd361", "ghostty-web"
        )
        self.assertEqual(
            ("@solidjs/start", "url"),
            (scoped_url["name"], scoped_url["resolution_type"]),
        )
        self.assertEqual("workspace", workspace["resolution_type"])
        self.assertEqual("git", github["resolution_type"])
        for resolution in (scoped_url, workspace, github):
            self.assertEqual("NOASSERTION", resolution["version"])
            self.assertIsNone(audit.component_purl(resolution))

    def test_exact_candidate_copy_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as source_temporary:
            source = Path(source_temporary)
            manifest = fixture_repository(source)
            rights = audit.build_rights_inventory(source, manifest, "4" * 64)
            with tempfile.TemporaryDirectory() as project_temporary:
                project = Path(project_temporary)
                (project / "copied.ts").write_text(SOURCE, encoding="utf-8")
                with self.assertRaisesRegex(audit.AuditError, "exact doc-haus"):
                    audit.verify_no_code_copy(project, manifest, rights)

    def test_exact_non_candidate_source_copy_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as source_temporary:
            source = Path(source_temporary)
            manifest = fixture_repository(source)
            rights = audit.build_rights_inventory(source, manifest, "a" * 64)
            copied = (source / "generated" / "gen" / "client.ts").read_text(
                encoding="utf-8"
            )
            with tempfile.TemporaryDirectory() as project_temporary:
                project = Path(project_temporary)
                (project / "copied.ts").write_text(copied, encoding="utf-8")
                with self.assertRaisesRegex(audit.AuditError, "tracked-file copy"):
                    audit.verify_no_code_copy(project, manifest, rights)

    def test_exact_nonimplementation_tracked_copy_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as source_temporary:
            source = Path(source_temporary)
            manifest = fixture_repository(source)
            rights = audit.build_rights_inventory(source, manifest, "a" * 64)
            copied = (source / "fork-marker.txt").read_text(encoding="utf-8")
            with tempfile.TemporaryDirectory() as project_temporary:
                project = Path(project_temporary)
                (project / "copied.txt").write_text(copied, encoding="utf-8")
                with self.assertRaisesRegex(audit.AuditError, "tracked-file copy"):
                    audit.verify_no_code_copy(project, manifest, rights)

    def test_exact_hash_exception_must_be_narrow_and_used(self) -> None:
        with tempfile.TemporaryDirectory() as source_temporary:
            source = Path(source_temporary)
            manifest = fixture_repository(source)
            rights = audit.build_rights_inventory(source, manifest, "a" * 64)
            manifest["exact_hash_exceptions"] = [
                {
                    "project_path": "missing.ignore",
                    "source_path": "LICENSE",
                    "sha256": audit.sha256_file(source / "LICENSE"),
                    "reason": "Synthetic unused non-code exception.",
                }
            ]
            audit.validate_manifest(manifest)
            with tempfile.TemporaryDirectory() as project_temporary:
                project = Path(project_temporary)
                (project / "independent.txt").write_text(
                    "independent\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(audit.AuditError, "unused exact hash"):
                    audit.verify_no_code_copy(project, manifest, rights)
            manifest["exact_hash_exceptions"][0]["source_path"] = "src/candidate.ts"
            with self.assertRaisesRegex(audit.AuditError, "implementation source"):
                audit.validate_manifest(manifest)

    def test_exact_hash_exception_project_paths_are_unique(self) -> None:
        with tempfile.TemporaryDirectory() as source_temporary:
            source = Path(source_temporary)
            manifest = fixture_repository(source)
            manifest["exact_hash_exceptions"] = [
                {
                    "project_path": "duplicate.ignore",
                    "source_path": "LICENSE",
                    "sha256": audit.sha256_file(source / "LICENSE"),
                    "reason": "First synthetic exception.",
                },
                {
                    "project_path": "duplicate.ignore",
                    "source_path": "fork-marker.txt",
                    "sha256": audit.sha256_file(source / "fork-marker.txt"),
                    "reason": "Second synthetic exception.",
                },
            ]
            with self.assertRaisesRegex(audit.AuditError, "duplicate.*project path"):
                audit.validate_manifest(manifest)

    def test_normalized_token_window_copy_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as source_temporary:
            source = Path(source_temporary)
            manifest = fixture_repository(source)
            rights = audit.build_rights_inventory(source, manifest, "5" * 64)
            transformed = SOURCE.replace(
                "export function", "// changed comment\nexport    function"
            ).replace("filtered, doubled", "filtered,    doubled")
            with tempfile.TemporaryDirectory() as project_temporary:
                project = Path(project_temporary)
                (project / "transformed.ts").write_text(transformed, encoding="utf-8")
                with self.assertRaisesRegex(audit.AuditError, "normalized"):
                    audit.verify_no_code_copy(project, manifest, rights)

    def test_shifted_candidate_copy_is_detected_at_every_token_offset(self) -> None:
        with tempfile.TemporaryDirectory() as source_temporary:
            source = Path(source_temporary)
            manifest = fixture_repository(source)
            rights = audit.build_rights_inventory(source, manifest, "b" * 64)
            for count in (1, 2, 3):
                with self.subTest(prepended_tokens=count):
                    prefix = " ".join(f"prefix{index}" for index in range(count))
                    with tempfile.TemporaryDirectory() as project_temporary:
                        project = Path(project_temporary)
                        (project / "shifted.ts").write_text(
                            f"{prefix}\n{SOURCE}", encoding="utf-8"
                        )
                        with self.assertRaisesRegex(audit.AuditError, "normalized"):
                            audit.verify_no_code_copy(project, manifest, rights)

    def test_comment_wrapped_candidate_copy_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as source_temporary:
            source = Path(source_temporary)
            manifest = fixture_repository(source)
            rights = audit.build_rights_inventory(source, manifest, "b" * 64)
            with tempfile.TemporaryDirectory() as project_temporary:
                project = Path(project_temporary)
                (project / "commented.ts").write_text(
                    f"/*\n{SOURCE}\n*/\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(audit.AuditError, "verbatim"):
                    audit.verify_no_code_copy(project, manifest, rights)

    def test_template_wrapped_candidate_copy_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as source_temporary:
            source = Path(source_temporary)
            manifest = fixture_repository(source)
            rights = audit.build_rights_inventory(source, manifest, "b" * 64)
            with tempfile.TemporaryDirectory() as project_temporary:
                project = Path(project_temporary)
                (project / "templated.ts").write_text(
                    f"const hidden = `\n{SOURCE}\n`\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(audit.AuditError, "verbatim"):
                    audit.verify_no_code_copy(project, manifest, rights)

    def test_line_comment_wrapped_multiline_candidate_copy_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as source_temporary:
            source = Path(source_temporary)
            manifest = fixture_repository(source)
            rights = audit.build_rights_inventory(source, manifest, "b" * 64)
            commented = "\n".join(f"// {line}" for line in SOURCE.splitlines())
            with tempfile.TemporaryDirectory() as project_temporary:
                project = Path(project_temporary)
                (project / "line-commented.ts").write_text(
                    commented + "\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(audit.AuditError, "wrapper-stripped"):
                    audit.verify_no_code_copy(project, manifest, rights)

    def test_line_comment_wrapped_short_candidate_segment_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as source_temporary:
            source = Path(source_temporary)
            manifest = fixture_repository(source)
            for candidate in manifest["candidates"]:  # type: ignore[union-attr]
                candidate["disposition"] = "approved_for_attributed_extraction"
                candidate["paths"][0]["segments"] = [  # type: ignore[index]
                    {"start_line": 6, "end_line": 6, "purpose": "short fixture"}
                ]
            rights = audit.build_rights_inventory(source, manifest, "b" * 64)
            short_segment = SOURCE.splitlines()[5]
            with tempfile.TemporaryDirectory() as project_temporary:
                project = Path(project_temporary)
                (project / "short-commented.ts").write_text(
                    f"// {short_segment}\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(audit.AuditError, "wrapper-stripped"):
                    audit.verify_no_code_copy(project, manifest, rights)

    def test_other_comment_wrapped_short_candidate_segments_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as source_temporary:
            source = Path(source_temporary)
            manifest = fixture_repository(source)
            for candidate in manifest["candidates"]:  # type: ignore[union-attr]
                candidate["disposition"] = "approved_for_attributed_extraction"
                candidate["paths"][0]["segments"] = [  # type: ignore[index]
                    {"start_line": 6, "end_line": 6, "purpose": "short fixture"}
                ]
            rights = audit.build_rights_inventory(source, manifest, "b" * 64)
            short_segment = SOURCE.splitlines()[5]
            wrappers = {
                "sql": ("wrapped.sql", f"-- {short_segment}\n"),
                "hash": ("wrapped.py", f"# {short_segment}\n"),
                "block": ("wrapped.ts", f"/* {short_segment} */\n"),
                "html": ("wrapped.html", f"<!-- {short_segment} -->\n"),
            }
            expected = audit.wrapper_stripped_token_fingerprints(short_segment)
            for name, (filename, wrapped) in wrappers.items():
                with self.subTest(carrier=name):
                    self.assertEqual(
                        expected,
                        audit.wrapper_stripped_token_fingerprints(wrapped),
                    )
                    with tempfile.TemporaryDirectory() as project_temporary:
                        project = Path(project_temporary)
                        (project / filename).write_text(wrapped, encoding="utf-8")
                        with self.assertRaisesRegex(
                            audit.AuditError, "candidate-source overlap"
                        ):
                            audit.verify_no_code_copy(project, manifest, rights)

    def test_patch_candidate_gets_text_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            candidate = manifest["candidates"][0]  # type: ignore[index]
            candidate["paths"].append(  # type: ignore[union-attr]
                {
                    "path": "patches/known.patch",
                    "sha256": audit.sha256_file(root / "patches" / "known.patch"),
                }
            )
            manifest["candidate_text_line_counts"]["patches/known.patch"] = 1  # type: ignore[index]
            rights = audit.build_rights_inventory(root, manifest, "b" * 64)
            covered = {
                record["path"]
                for record in rights["candidate_source_fingerprints"]["records"]
            }
            self.assertIn("patches/known.patch", covered)

    def test_fingerprint_records_cross_bind_to_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            mutations = {
                "identity": lambda record: record.update(candidate_id="missing"),
                "digest": lambda record: record.update(file_sha256="2" * 64),
                "purpose": lambda record: record["segments"][0].update(
                    purpose="tampered"
                ),
                "line_count": lambda record: record.update(
                    line_count=record["line_count"] + 1
                ),
                "whole_file_end": lambda record: record["segments"][0].update(
                    end_line=record["segments"][0]["end_line"] - 1
                ),
            }
            for name, mutate in mutations.items():
                with self.subTest(mutation=name):
                    case_manifest = copy.deepcopy(manifest)
                    rights = audit.build_rights_inventory(root, case_manifest, "b" * 64)
                    mutate(rights["candidate_source_fingerprints"]["records"][0])
                    case_manifest["expected_inventory"][
                        "candidate_source_fingerprints"
                    ] = audit.inventory_expectations(rights)[
                        "candidate_source_fingerprints"
                    ]
                    with self.assertRaisesRegex(
                        audit.AuditError, "manifest|line count"
                    ):
                        audit.validate_rights_inventory(rights, case_manifest, "b" * 64)

    def test_fingerprints_never_span_noncontiguous_segments(self) -> None:
        lines = [
            " ".join(f"approved_start_{index}" for index in range(48)),
            " ".join(f"excluded_database_{index}" for index in range(48)),
            " ".join(f"approved_end_{index}" for index in range(48)),
        ]
        segments = [
            {"start_line": 1, "end_line": 1, "purpose": "first"},
            {"start_line": 3, "end_line": 3, "purpose": "second"},
        ]
        records = audit.fingerprint_segments(lines, segments)
        observed = {
            fingerprint for record in records for fingerprint in record["fingerprints"]
        }
        observed_verbatim = {
            fingerprint
            for record in records
            for fingerprint in record["verbatim_fingerprints"]
        }
        observed_wrapper_stripped = {
            fingerprint
            for record in records
            for fingerprint in record["wrapper_stripped_fingerprints"]
        }
        excluded = set(audit.token_fingerprints(lines[1]))
        joined = set(audit.token_fingerprints(f"{lines[0]}\n{lines[2]}"))
        self.assertTrue(excluded.isdisjoint(observed))
        self.assertTrue(joined - observed)
        self.assertTrue(
            set(audit.verbatim_token_fingerprints(lines[1])).isdisjoint(
                observed_verbatim
            )
        )
        self.assertTrue(
            set(audit.wrapper_stripped_token_fingerprints(lines[1])).isdisjoint(
                observed_wrapper_stripped
            )
        )

    def test_different_string_arrays_do_not_create_generic_overlap(self) -> None:
        first = "const values = [" + ",".join(f'"first-{index}"' for index in range(40))
        second = "const values = [" + ",".join(
            f'"second-{index}"' for index in range(40)
        )
        self.assertTrue(
            set(audit.token_fingerprints(first)).isdisjoint(
                audit.token_fingerprints(second)
            )
        )

    def test_unrelated_implementation_passes_no_copy_check(self) -> None:
        with tempfile.TemporaryDirectory() as source_temporary:
            source = Path(source_temporary)
            manifest = fixture_repository(source)
            rights = audit.build_rights_inventory(source, manifest, "6" * 64)
            with tempfile.TemporaryDirectory() as project_temporary:
                project = Path(project_temporary)
                (project / "independent.py").write_text(
                    "def independent_fixture():\n    return 7\n", encoding="utf-8"
                )
                inspected, _ = audit.verify_no_code_copy(project, manifest, rights)
                self.assertEqual(1, inspected)

    def test_git_eligible_symlink_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as source_temporary:
            source = Path(source_temporary)
            manifest = fixture_repository(source)
            rights = audit.build_rights_inventory(source, manifest, "c" * 64)
            with tempfile.TemporaryDirectory() as project_temporary:
                project = Path(project_temporary)
                target = project / "target.py"
                target.write_text("value = 7\n", encoding="utf-8")
                (project / "linked.py").symlink_to(target.name)
                with self.assertRaisesRegex(audit.AuditError, "symlink"):
                    audit.verify_no_code_copy(project, manifest, rights)

    def test_nested_project_root_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            outer = Path(temporary)
            git(outer, "init", "-q")
            root = outer / "nested"
            root.mkdir()
            source = root / "source.py"
            source.write_text("value = 7\n", encoding="utf-8")
            ignored = root / "node_modules" / "ignored.js"
            ignored.parent.mkdir()
            ignored.write_text("const ignored = true\n", encoding="utf-8")
            with self.assertRaisesRegex(audit.AuditError, "nested"):
                audit.project_files(root)

    def test_non_git_fallback_includes_broken_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "broken.py").symlink_to("missing.py")
            self.assertEqual([root / "broken.py"], audit.project_files(root))
            with tempfile.TemporaryDirectory() as source_temporary:
                source = Path(source_temporary)
                manifest = fixture_repository(source)
                rights = audit.build_rights_inventory(source, manifest, "c" * 64)
                with self.assertRaisesRegex(audit.AuditError, "symlink"):
                    audit.verify_no_code_copy(root, manifest, rights)

    def test_live_reconciliation_rejects_dirty_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            (root / "untracked.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(audit.AuditError, "working-tree"):
                audit.compare_repository_pin(
                    manifest, audit.repository_snapshot(root, manifest["repository"])
                )

    def test_live_lineage_reconciliation_validates_local_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            snapshot = audit.repository_snapshot(root, manifest["repository"])
            audit.compare_repository_pin(manifest, snapshot)
            self.assertTrue(snapshot["upstream_base_ancestor"])
            self.assertEqual(
                manifest["repository"]["configured_remotes"],
                snapshot["configured_remotes"],
            )
            self.assertEqual(
                manifest["repository"]["origin_default_branch"],
                snapshot["origin_default_branch"],
            )
            self.assertFalse(snapshot["upstream_remote_configured"])
            self.assertEqual(
                manifest["repository"]["upstream_base"], snapshot["first_fork_parent"]
            )
            broken = copy.deepcopy(manifest)
            broken["repository"]["commits_after_upstream_base"] = 2
            with self.assertRaisesRegex(audit.AuditError, "commit count"):
                audit.compare_repository_pin(broken, snapshot)

    def test_live_segment_cannot_exceed_pinned_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture_repository(root)
            manifest["candidates"][0]["paths"][0]["segments"] = [  # type: ignore[index]
                {"start_line": 1, "end_line": 999, "purpose": "Invalid fixture"}
            ]
            with self.assertRaisesRegex(audit.AuditError, "segment exceeds"):
                audit.verify_evidence_hashes(root, manifest)


if __name__ == "__main__":
    unittest.main()
