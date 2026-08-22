"""Contract tests for the read-only HammerTime release and artifact adapter.

Covers the card-required scenarios: missing path, changed hash, stale
release, malformed frontmatter, unauthorized matter, and read-only
filesystem enforcement, plus happy-path pinning for every adapter API.
All fixtures are synthetic and built in a temporary directory.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import stat
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import sklegal_hammertime
from sklegal_domain import LegacyRecordKind
from sklegal_hammertime import (
    AdapterUnavailableError,
    AmbiguousLegacyIdError,
    ForbiddenPathError,
    HammerTimeReleaseAdapter,
    MalformedFrontmatterError,
    MatterAccessDenied,
    MatterAccessRequest,
    MissingPathError,
    RegistryMismatchError,
    SourceHashMismatchError,
    StaleReleaseError,
)

from tests.support import hammertime_fixture as fixture

FIXED_NOW = datetime(2099, 1, 2, 3, 4, 5, tzinfo=UTC)
PACKAGE_SRC = (
    Path(sklegal_hammertime.__file__).resolve().parent.parent.parent
    / "src"
    / "sklegal_hammertime"
)


def allow_all(request: MatterAccessRequest) -> bool:
    return True


class AdapterTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        fixture.build_hammertime_fixture(self.root)
        self.adapter = HammerTimeReleaseAdapter(
            root=self.root,
            matter_authorizer=allow_all,
            clock=lambda: FIXED_NOW,
        )

    def tearDown(self) -> None:
        # Restore write permission so TemporaryDirectory cleanup works even
        # after the read-only enforcement test locked the tree down.
        for dirpath, dirnames, filenames in os.walk(self.root):
            os.chmod(dirpath, 0o755)
            for name in filenames:
                os.chmod(os.path.join(dirpath, name), 0o644)


class ReleaseAndAliasTests(AdapterTestCase):
    def test_list_releases_pins_every_manifest(self) -> None:
        summaries = self.adapter.list_releases()
        self.assertEqual(
            {item.release_id for item in summaries},
            {fixture.RELEASE_ID, fixture.PREVIOUS_RELEASE_ID},
        )
        for item in summaries:
            self.assertEqual(item.release_target, "dev")
            self.assertEqual(
                item.pin.content_sha256,
                fixture.fixture_sha256(self.root, item.pin.relative_path),
            )
            self.assertEqual(item.pin.observed_at, FIXED_NOW)
            self.assertEqual(item.pin.release_id, item.release_id)

    def test_get_release_manifest_is_typed_lossless_and_pinned(self) -> None:
        manifest = self.adapter.get_release_manifest(fixture.RELEASE_ID)
        self.assertEqual(manifest.release_id, fixture.RELEASE_ID)
        self.assertEqual(manifest.release_target, "dev")
        self.assertEqual(manifest.schema_version, 1)
        self.assertEqual(manifest.vector_collection, "hammertime-v3-dev")
        self.assertEqual(manifest.graph_name, "hammertime-v4-dev")
        self.assertEqual(manifest.document_counts.new, 1)
        self.assertEqual(manifest.documents["new"], [fixture.REFERENCE_RELATIVE])
        self.assertEqual(manifest.verification["qdrant_collection_ok"], True)
        self.assertEqual(manifest.decomposed_snapshot["file_count"], 1)
        relative = f"json/releases/corpus-release-{fixture.RELEASE_ID}.json"
        self.assertEqual(manifest.raw, json.loads((self.root / relative).read_text()))
        self.assertEqual(
            manifest.pin.content_sha256,
            fixture.fixture_sha256(self.root, relative),
        )

    def test_get_release_manifest_rejects_invalid_id(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.get_release_manifest("../escape")
        with self.assertRaises(ValueError):
            self.adapter.get_release_manifest("UPPERCASE")

    def test_get_runtime_aliases(self) -> None:
        snapshot = self.adapter.get_runtime_aliases()
        self.assertEqual(snapshot.schema_version, 2)
        alias = snapshot.aliases["dev"]
        self.assertEqual(alias.current.release_id, fixture.RELEASE_ID)
        self.assertIsNotNone(alias.previous)
        self.assertEqual(alias.previous.release_id, fixture.PREVIOUS_RELEASE_ID)
        self.assertEqual(
            snapshot.pin.content_sha256,
            fixture.fixture_sha256(self.root, "json/state/runtime-aliases.json"),
        )

    def test_resolve_current_release_without_drift(self) -> None:
        resolved = self.adapter.resolve_current_release("dev")
        self.assertEqual(resolved.manifest.release_id, fixture.RELEASE_ID)
        self.assertEqual(resolved.drift, [])
        self.assertEqual(resolved.manifest.pin.alias_target, "dev")
        self.assertEqual(
            resolved.alias.previous.release_id, fixture.PREVIOUS_RELEASE_ID
        )
        self.assertEqual(
            resolved.aliases_pin.content_sha256,
            fixture.fixture_sha256(self.root, "json/state/runtime-aliases.json"),
        )

    def test_resolve_current_release_preserves_binding_drift(self) -> None:
        aliases_path = self.root / "json/state/runtime-aliases.json"
        payload = json.loads(aliases_path.read_text())
        payload["aliases"]["dev"]["current"]["vector_collection"] = "hammertime-v3-old"
        payload["aliases"]["dev"]["current"]["manifest_path"] = "/elsewhere/corpus.json"
        aliases_path.write_text(json.dumps(payload))
        resolved = self.adapter.resolve_current_release("dev")
        self.assertEqual(len(resolved.drift), 2)
        self.assertIn("vector collection", resolved.drift[1])


class MissingPathTests(AdapterTestCase):
    def test_missing_release_manifest(self) -> None:
        with self.assertRaises(MissingPathError):
            self.adapter.get_release_manifest("dev-20990101-absent-release")

    def test_missing_artifact(self) -> None:
        with self.assertRaises(MissingPathError):
            self.adapter.read_artifact("reference/legal/absent.md")

    def test_missing_decomposition(self) -> None:
        with self.assertRaises(MissingPathError):
            self.adapter.get_decomposition("9999-absent-doc-0000000000")

    def test_missing_legacy_matter(self) -> None:
        with self.assertRaises(MissingPathError):
            self.adapter.legacy_matter_path("PRB-2099-999")

    def test_missing_releases_directory(self) -> None:
        empty = self.root / "empty-root"
        empty.mkdir()
        adapter = HammerTimeReleaseAdapter(root=empty, clock=lambda: FIXED_NOW)
        with self.assertRaises(MissingPathError):
            adapter.list_releases()

    def test_unconfigured_root_fails_closed(self) -> None:
        with self.assertRaises(AdapterUnavailableError):
            HammerTimeReleaseAdapter(
                root=self.root / "not-a-directory", clock=lambda: FIXED_NOW
            )


class ChangedHashTests(AdapterTestCase):
    def test_changed_decomposition_hash(self) -> None:
        relative = f"json/decomposed/{fixture.DECOMPOSITION_ID}.json"
        pinned = fixture.fixture_sha256(self.root, relative)
        read = self.adapter.get_decomposition(
            fixture.DECOMPOSITION_ID, expected_sha256=pinned
        )
        self.assertEqual(read.pin.content_sha256, pinned)
        with self.assertRaises(SourceHashMismatchError):
            self.adapter.get_decomposition(
                fixture.DECOMPOSITION_ID,
                expected_sha256="0" * 64,
            )

    def test_changed_artifact_hash(self) -> None:
        with self.assertRaises(SourceHashMismatchError):
            self.adapter.read_artifact(
                fixture.REFERENCE_RELATIVE, expected_sha256="f" * 64
            )

    def test_verify_source_hash_detects_mutation(self) -> None:
        pinned = fixture.fixture_sha256(self.root, fixture.REFERENCE_RELATIVE)
        verification = self.adapter.verify_source_hash(
            fixture.REFERENCE_RELATIVE, expected_sha256=pinned
        )
        self.assertTrue(verification.verified)
        target = self.root / fixture.REFERENCE_RELATIVE
        target.write_text(target.read_text() + "mutation\n")
        with self.assertRaises(SourceHashMismatchError):
            self.adapter.verify_source_hash(
                fixture.REFERENCE_RELATIVE, expected_sha256=pinned
            )


class StaleReleaseTests(AdapterTestCase):
    def test_alias_pointing_at_missing_release(self) -> None:
        aliases_path = self.root / "json/state/runtime-aliases.json"
        payload = json.loads(aliases_path.read_text())
        payload["aliases"]["dev"]["current"]["release_id"] = "dev-20990101-removed"
        aliases_path.write_text(json.dumps(payload))
        with self.assertRaises(StaleReleaseError):
            self.adapter.resolve_current_release("dev")

    def test_alias_manifest_identity_mismatch(self) -> None:
        release_path = (
            self.root / f"json/releases/corpus-release-{fixture.RELEASE_ID}.json"
        )
        payload = json.loads(release_path.read_text())
        payload["release_id"] = "dev-20990101-rewritten"
        release_path.write_text(json.dumps(payload))
        with self.assertRaises(StaleReleaseError):
            self.adapter.resolve_current_release("dev")

    def test_unknown_alias_target(self) -> None:
        with self.assertRaises(StaleReleaseError):
            self.adapter.resolve_current_release("prod")

    def test_invalid_alias_target(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.resolve_current_release("../etc")


class MalformedFrontmatterTests(AdapterTestCase):
    def _break_problem(self, content: str) -> None:
        (self.root / fixture.PROBLEM_RELATIVE).write_text(content)

    def test_invalid_yaml_frontmatter(self) -> None:
        self._break_problem("---\nproblem_id: [unterminated\n---\nbody\n")
        with self.assertRaises(MalformedFrontmatterError):
            self.adapter.resolve_legacy_matter(fixture.PROBLEM_ID)

    def test_unterminated_frontmatter(self) -> None:
        self._break_problem(
            f"---\nproblem_id: {fixture.PROBLEM_ID}\nslug: {fixture.PROBLEM_SLUG}\n"
        )
        with self.assertRaises(MalformedFrontmatterError):
            self.adapter.resolve_legacy_matter(fixture.PROBLEM_ID)

    def test_missing_frontmatter(self) -> None:
        self._break_problem("# no frontmatter at all\n")
        with self.assertRaises(MalformedFrontmatterError):
            self.adapter.resolve_legacy_matter(fixture.PROBLEM_ID)

    def test_non_mapping_frontmatter(self) -> None:
        self._break_problem("---\n- just\n- a\n- list\n---\nbody\n")
        with self.assertRaises(MalformedFrontmatterError):
            self.adapter.resolve_legacy_matter(fixture.PROBLEM_ID)

    def test_registry_frontmatter_identity_mismatch(self) -> None:
        self._break_problem(
            f"---\nproblem_id: PRB-2099-901\nslug: {fixture.PROBLEM_SLUG}\n---\nbody\n"
        )
        with self.assertRaises(RegistryMismatchError):
            self.adapter.resolve_legacy_matter(fixture.PROBLEM_ID)


class UnauthorizedMatterTests(AdapterTestCase):
    def _adapter_without_authorizer(self) -> HammerTimeReleaseAdapter:
        return HammerTimeReleaseAdapter(root=self.root, clock=lambda: FIXED_NOW)

    def test_no_authorizer_denies_matter_reads(self) -> None:
        adapter = self._adapter_without_authorizer()
        with self.assertRaises(MatterAccessDenied):
            adapter.legacy_matter_path(fixture.PROBLEM_ID)
        with self.assertRaises(MatterAccessDenied):
            adapter.resolve_legacy_matter(fixture.PROBLEM_ID)
        with self.assertRaises(MatterAccessDenied):
            adapter.get_matter_validation_report(
                fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
            )
        with self.assertRaises(MatterAccessDenied):
            adapter.list_packet_references(
                fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
            )
        with self.assertRaises(MatterAccessDenied):
            adapter.get_owner_directions(
                fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
            )

    def test_denying_authorizer_blocks_matter_reads(self) -> None:
        adapter = HammerTimeReleaseAdapter(
            root=self.root,
            matter_authorizer=lambda request: False,
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaises(MatterAccessDenied):
            adapter.resolve_legacy_matter(fixture.PROBLEM_ID)

    def test_failing_authorizer_fails_closed(self) -> None:
        def broken(request: MatterAccessRequest) -> bool:
            raise RuntimeError("policy backend unavailable")

        adapter = HammerTimeReleaseAdapter(
            root=self.root, matter_authorizer=broken, clock=lambda: FIXED_NOW
        )
        with self.assertRaises(MatterAccessDenied):
            adapter.resolve_legacy_matter(fixture.PROBLEM_ID)

    def test_corpus_reads_do_not_require_matter_authorization(self) -> None:
        adapter = self._adapter_without_authorizer()
        self.assertEqual(
            adapter.get_release_manifest(fixture.RELEASE_ID).release_id,
            fixture.RELEASE_ID,
        )
        self.assertEqual(
            adapter.get_decomposition(fixture.DECOMPOSITION_ID).document_id,
            fixture.DECOMPOSITION_ID,
        )
        self.assertTrue(
            adapter.verify_source_hash(
                fixture.REFERENCE_RELATIVE,
                expected_sha256=fixture.fixture_sha256(
                    self.root, fixture.REFERENCE_RELATIVE
                ),
            ).verified
        )

    def test_authorizer_receives_sanitized_request(self) -> None:
        seen: list[MatterAccessRequest] = []

        def recording(request: MatterAccessRequest) -> bool:
            seen.append(request)
            return True

        adapter = HammerTimeReleaseAdapter(
            root=self.root, matter_authorizer=recording, clock=lambda: FIXED_NOW
        )
        adapter.resolve_legacy_matter(fixture.PROBLEM_ID)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0].legacy_id, fixture.PROBLEM_ID)
        self.assertEqual(seen[0].record_kind, LegacyRecordKind.CONTAINER)
        self.assertIsNone(seen[0].relative_path)
        self.assertEqual(seen[1].relative_path, fixture.PROBLEM_RELATIVE)

    def test_denied_callers_cannot_probe_matter_existence(self) -> None:
        adapter = self._adapter_without_authorizer()
        for legacy_id in (fixture.PROBLEM_ID, "PRB-2099-999"):
            with self.subTest(legacy_id=legacy_id):
                with self.assertRaises(MatterAccessDenied):
                    adapter.legacy_matter_path(legacy_id)
                with self.assertRaises(MatterAccessDenied):
                    adapter.resolve_legacy_matter(legacy_id)

    def test_denial_happens_before_registry_discovery(self) -> None:
        registry = self.root / "incidents/_incident-registry.md"
        registry.unlink()
        adapter = self._adapter_without_authorizer()
        calls = (
            lambda: adapter.legacy_matter_path(fixture.PROBLEM_ID),
            lambda: adapter.resolve_legacy_matter(fixture.PROBLEM_ID),
            lambda: adapter.get_matter_validation_report(
                fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
            ),
            lambda: adapter.list_packet_references(
                fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
            ),
            lambda: adapter.get_owner_directions(
                fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
            ),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(MatterAccessDenied):
                    call()


class LegacyMatterTests(AdapterTestCase):
    def test_legacy_matter_path_resolution(self) -> None:
        problem = self.adapter.legacy_matter_path(fixture.PROBLEM_ID)
        self.assertEqual(problem.relative_path, fixture.PROBLEM_RELATIVE)
        self.assertEqual(
            problem.pin.content_sha256,
            fixture.fixture_sha256(self.root, fixture.PROBLEM_RELATIVE),
        )
        self.assertEqual(
            problem.registry_pin.content_sha256,
            fixture.fixture_sha256(self.root, "incidents/_incident-registry.md"),
        )
        incident = self.adapter.legacy_matter_path(
            fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
        )
        self.assertEqual(
            incident.relative_path,
            fixture.INCIDENT_RELATIVE,
        )
        self.assertEqual(incident.parent_legacy_id, fixture.PROBLEM_ID)

    def test_incident_resolution_requires_parent(self) -> None:
        with self.assertRaises(AmbiguousLegacyIdError):
            self.adapter.legacy_matter_path(fixture.INCIDENT_ID)

    def test_resolve_legacy_matter_is_pinned_and_lossless(self) -> None:
        record = self.adapter.resolve_legacy_matter(fixture.PROBLEM_ID)
        self.assertEqual(record.legacy_id, fixture.PROBLEM_ID)
        self.assertEqual(record.record_kind, LegacyRecordKind.CONTAINER)
        self.assertEqual(record.slug, fixture.PROBLEM_SLUG)
        self.assertEqual(record.frontmatter["status"], "open")
        self.assertIn("Synthetic body text", record.body)
        self.assertEqual(
            record.pin.content_sha256,
            fixture.fixture_sha256(self.root, fixture.PROBLEM_RELATIVE),
        )
        self.assertEqual(
            record.registry_pin.content_sha256,
            fixture.fixture_sha256(self.root, "incidents/_incident-registry.md"),
        )
        alias = record.to_legacy_alias(source_version=fixture.RELEASE_ID)
        self.assertEqual(alias.legacy_id, fixture.PROBLEM_ID)
        self.assertEqual(alias.legacy_path, fixture.PROBLEM_RELATIVE)
        self.assertEqual(alias.content_sha256, record.pin.content_sha256)
        self.assertEqual(alias.source_version, fixture.RELEASE_ID)

    def test_resolve_legacy_incident(self) -> None:
        record = self.adapter.resolve_legacy_matter(
            fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
        )
        self.assertEqual(record.record_kind, LegacyRecordKind.ACTIVITY)
        self.assertEqual(record.parent_legacy_id, fixture.PROBLEM_ID)
        self.assertEqual(record.relative_path, fixture.INCIDENT_RELATIVE)

    def test_invalid_legacy_ids_rejected(self) -> None:
        for bad in ("PRB-209-900", "INC-", "prb-2099-900", "../INC-900"):
            with self.assertRaises(ValueError):
                self.adapter.legacy_matter_path(bad)


class MatterArtifactTests(AdapterTestCase):
    def test_validation_report(self) -> None:
        report = self.adapter.get_matter_validation_report(
            fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
        )
        self.assertEqual(report.legacy_id, fixture.INCIDENT_ID)
        self.assertTrue(report.valid)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.notices, ["synthetic fixture notice"])
        self.assertEqual(report.sections["source_preservation"]["hashed"], 2)
        relative = f"{fixture.INCIDENT_DIR_RELATIVE}/validation-report.json"
        self.assertEqual(
            report.pin.content_sha256, fixture.fixture_sha256(self.root, relative)
        )

    def test_packet_references_preserve_version_lineage(self) -> None:
        references = self.adapter.list_packet_references(
            fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
        )
        self.assertEqual([item.packet_version for item in references], [1, 2])
        self.assertIsNone(references[0].review_pin)
        self.assertEqual(
            references[1].review_path,
            f"{fixture.INCIDENT_DIR_RELATIVE}/phase-0-wave-1-v2-review.md",
        )
        self.assertIsNotNone(references[1].review_pin)
        review_relative = f"{fixture.INCIDENT_DIR_RELATIVE}/phase-0-wave-1-v2-review.md"
        self.assertEqual(
            references[1].review_pin.content_sha256,
            fixture.fixture_sha256(self.root, review_relative),
        )
        self.assertEqual(references[1].facts["packet_version"], 2)
        relative = f"{fixture.INCIDENT_DIR_RELATIVE}/packet-v2-facts.json"
        self.assertEqual(
            references[1].facts_pin.content_sha256,
            fixture.fixture_sha256(self.root, relative),
        )

    def test_owner_directions(self) -> None:
        read = self.adapter.get_owner_directions(
            fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
        )
        self.assertIn("Synthetic owner direction record", read.text)
        self.assertEqual(
            read.pin.relative_path,
            f"{fixture.INCIDENT_DIR_RELATIVE}/correspondence/OWNER-DIRECTIONS.md",
        )


class DecompositionTests(AdapterTestCase):
    def test_get_decomposition_typed_and_pinned(self) -> None:
        decomposition = self.adapter.get_decomposition(fixture.DECOMPOSITION_ID)
        self.assertEqual(decomposition.document_id, fixture.DECOMPOSITION_ID)
        self.assertEqual(decomposition.source_file, fixture.REFERENCE_RELATIVE)
        self.assertEqual(decomposition.stats["claims"], 1)
        self.assertEqual(decomposition.chunks[0].chunk_id, "chk_fixture0001")
        self.assertEqual(decomposition.claims[0].line, 5)
        self.assertEqual(decomposition.entities[0].type, "Organization")
        self.assertEqual(decomposition.citations, [])
        self.assertEqual(decomposition.frontmatter["title"], "Fixture reference")
        relative = f"json/decomposed/{fixture.DECOMPOSITION_ID}.json"
        self.assertEqual(
            decomposition.pin.content_sha256,
            fixture.fixture_sha256(self.root, relative),
        )
        self.assertEqual(decomposition.raw["stats"]["chunks"], 1)

    def test_get_decomposed_state(self) -> None:
        seal = self.adapter.get_decomposed_state()
        self.assertEqual(seal.release_id, "dev-live")
        self.assertEqual(seal.schema_version, "1")
        self.assertIn("files", seal.snapshot)
        self.assertEqual(seal.pin.relative_path, "json/state/decomposed-state.json")

    def test_invalid_decomposition_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.get_decomposition("../escape")


class PathSafetyTests(AdapterTestCase):
    def test_inbox_is_never_readable(self) -> None:
        for path in (
            "Inbox/do-not-read.md",
            "inbox/do-not-read.md",
            "INBOX/x",
            "json/Inbox/do-not-read.md",
        ):
            with self.assertRaises(ForbiddenPathError):
                self.adapter.read_artifact(path)

    def test_incidents_require_gated_matter_apis(self) -> None:
        with self.assertRaises(ForbiddenPathError):
            self.adapter.read_artifact(fixture.PROBLEM_RELATIVE)

    def test_path_escape_denied(self) -> None:
        for path in (
            "../outside.md",
            "/etc/passwd",
            "json/../incidents/_incident-registry.md",
            "json//state/runtime-aliases.json",
            "reference/./legal/fixture-reference.md",
            " reference/legal/fixture-reference.md",
        ):
            with self.assertRaises(ForbiddenPathError):
                self.adapter.read_artifact(path)

    def test_unlisted_subtree_denied(self) -> None:
        with self.assertRaises(ForbiddenPathError):
            self.adapter.read_artifact("logs/ingestion.log")

    def test_symlink_escape_denied(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside.md"
        outside.write_text("outside the root\n")
        self.addCleanup(outside.unlink)
        link = self.root / "reference" / "legal" / "escape-link.md"
        link.symlink_to(outside)
        with self.assertRaises(ForbiddenPathError):
            self.adapter.read_artifact("reference/legal/escape-link.md")

    def test_fixed_internal_path_symlink_denied(self) -> None:
        outside = self.root.parent / f"{self.root.name}-aliases.json"
        outside.write_bytes(
            (self.root / "json/state/runtime-aliases.json").read_bytes()
        )
        self.addCleanup(outside.unlink)
        alias_path = self.root / "json/state/runtime-aliases.json"
        alias_path.unlink()
        alias_path.symlink_to(outside)
        with self.assertRaises(ForbiddenPathError):
            self.adapter.get_runtime_aliases()

    def test_fixed_internal_path_cannot_symlink_into_inbox(self) -> None:
        alias_path = self.root / "json/state/runtime-aliases.json"
        inbox_alias = self.root / "Inbox/runtime-aliases.json"
        inbox_alias.write_bytes(alias_path.read_bytes())
        alias_path.unlink()
        alias_path.symlink_to(inbox_alias)
        with self.assertRaises(ForbiddenPathError):
            self.adapter.get_runtime_aliases()

    def test_fixed_internal_directory_cannot_symlink_into_inbox(self) -> None:
        state_dir = self.root / "json/state"
        inbox_state = self.root / "Inbox/state"
        state_dir.rename(inbox_state)
        state_dir.symlink_to(inbox_state, target_is_directory=True)
        with self.assertRaises(ForbiddenPathError):
            self.adapter.get_runtime_aliases()


class ReadOnlyEnforcementTests(AdapterTestCase):
    def test_every_public_response_carries_all_source_pins(self) -> None:
        self.assertTrue(all(item.pin for item in self.adapter.list_releases()))
        self.assertTrue(self.adapter.get_release_manifest(fixture.RELEASE_ID).pin)
        self.assertTrue(self.adapter.get_runtime_aliases().pin)
        resolved = self.adapter.resolve_current_release("dev")
        self.assertTrue(resolved.aliases_pin)
        self.assertTrue(resolved.manifest.pin)
        self.assertTrue(self.adapter.get_decomposed_state().pin)
        self.assertTrue(self.adapter.get_decomposition(fixture.DECOMPOSITION_ID).pin)
        self.assertTrue(self.adapter.read_artifact(fixture.REFERENCE_RELATIVE).pin)
        verified = self.adapter.verify_source_hash(
            fixture.REFERENCE_RELATIVE,
            expected_sha256=fixture.fixture_sha256(
                self.root, fixture.REFERENCE_RELATIVE
            ),
        )
        self.assertTrue(verified.pin)
        resolution = self.adapter.legacy_matter_path(fixture.PROBLEM_ID)
        self.assertTrue(resolution.pin)
        self.assertTrue(resolution.registry_pin)
        matter = self.adapter.resolve_legacy_matter(fixture.PROBLEM_ID)
        self.assertTrue(matter.pin)
        self.assertTrue(matter.registry_pin)
        report = self.adapter.get_matter_validation_report(
            fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
        )
        self.assertTrue(report.pin)
        packets = self.adapter.list_packet_references(
            fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
        )
        self.assertTrue(all(item.facts_pin for item in packets))
        self.assertTrue(next(item.review_pin for item in packets if item.review_pin))
        directions = self.adapter.get_owner_directions(
            fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
        )
        self.assertTrue(directions.pin)
        inventory = self.adapter.list_matter_artifacts(fixture.PROBLEM_ID)
        self.assertTrue(all(item.pin for item in inventory.artifacts))
        artifact = self.adapter.read_matter_artifact(
            fixture.INCIDENT_ID,
            f"{fixture.INCIDENT_DIR_RELATIVE}/validation-report.json",
            parent_legacy_id=fixture.PROBLEM_ID,
        )
        self.assertTrue(artifact.pin)

    def test_full_api_surface_runs_on_read_only_filesystem(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self.root):
            os.chmod(dirpath, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)
            for name in filenames:
                os.chmod(os.path.join(dirpath, name), stat.S_IRUSR | stat.S_IRGRP)
        self.assertFalse(os.access(self.root, os.W_OK))
        self.adapter.list_releases()
        self.adapter.get_release_manifest(fixture.RELEASE_ID)
        self.adapter.get_runtime_aliases()
        self.adapter.resolve_current_release("dev")
        self.adapter.get_decomposed_state()
        self.adapter.get_decomposition(fixture.DECOMPOSITION_ID)
        self.adapter.read_artifact(fixture.REFERENCE_RELATIVE)
        self.adapter.verify_source_hash(
            fixture.REFERENCE_RELATIVE,
            expected_sha256=fixture.fixture_sha256(
                self.root, fixture.REFERENCE_RELATIVE
            ),
        )
        self.adapter.legacy_matter_path(fixture.PROBLEM_ID)
        self.adapter.resolve_legacy_matter(fixture.PROBLEM_ID)
        self.adapter.resolve_legacy_matter(
            fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
        )
        self.adapter.get_matter_validation_report(
            fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
        )
        self.adapter.list_packet_references(
            fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
        )
        self.adapter.get_owner_directions(
            fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
        )
        self.adapter.list_matter_artifacts(fixture.PROBLEM_ID)
        self.adapter.list_matter_artifacts(
            fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
        )
        self.adapter.read_matter_artifact(
            fixture.INCIDENT_ID,
            f"{fixture.INCIDENT_DIR_RELATIVE}/validation-report.json",
            parent_legacy_id=fixture.PROBLEM_ID,
        )

    def test_adapter_exposes_no_write_surface(self) -> None:
        forbidden_prefixes = (
            "write",
            "delete",
            "create",
            "update",
            "promote",
            "move",
            "rename",
            "remove",
            "submit",
            "dispatch",
            "mutate",
        )
        for name, member in inspect.getmembers(HammerTimeReleaseAdapter):
            if name.startswith("_") or not callable(member):
                continue
            self.assertNotIn(
                name.split("_")[0],
                forbidden_prefixes,
                f"adapter method {name!r} looks like a write surface",
            )

    def test_package_source_contains_no_write_calls(self) -> None:
        forbidden_patterns = (
            re.compile(r"\.write_text\("),
            re.compile(r"\.write_bytes\("),
            re.compile(r"\bos\.remove\("),
            re.compile(r"\bos\.unlink\("),
            re.compile(r"\bos\.rename\("),
            re.compile(r"\bos\.replace\("),
            re.compile(r"\bos\.mkdir\("),
            re.compile(r"\bos\.makedirs\("),
            re.compile(r"\bos\.rmdir\("),
            re.compile(r"\bshutil\."),
            re.compile(r"\btempfile\."),
            re.compile(r"O_WRONLY|O_RDWR|O_CREAT|O_TRUNC|O_APPEND"),
            re.compile(r"""open\([^)]*['"][wax+]"""),
        )
        sources = sorted(PACKAGE_SRC.rglob("*.py"))
        self.assertTrue(sources, "adapter package sources were not found")
        for source in sources:
            text = source.read_text(encoding="utf-8")
            for pattern in forbidden_patterns:
                self.assertIsNone(
                    pattern.search(text),
                    f"write-capable call {pattern.pattern!r} found in {source.name}",
                )


class MatterArtifactInventoryTests(AdapterTestCase):
    def test_inventory_pins_every_regular_file_in_the_matter_tree(self) -> None:
        inventory = self.adapter.list_matter_artifacts(fixture.PROBLEM_ID)

        self.assertEqual(
            inventory.matter_root, f"incidents/problems/{fixture.PROBLEM_SLUG}"
        )
        paths = {item.pin.relative_path for item in inventory.artifacts}
        expected = {
            fixture.PROBLEM_RELATIVE,
            fixture.INCIDENT_RELATIVE,
            f"{fixture.INCIDENT_DIR_RELATIVE}/validation-report.json",
            f"{fixture.INCIDENT_DIR_RELATIVE}/packet-v1-facts.json",
            f"{fixture.INCIDENT_DIR_RELATIVE}/packet-v2-facts.json",
            f"{fixture.INCIDENT_DIR_RELATIVE}/phase-0-wave-1-v2-review.md",
            f"{fixture.INCIDENT_DIR_RELATIVE}/correspondence/OWNER-DIRECTIONS.md",
        }
        self.assertEqual(paths, expected)
        for item in inventory.artifacts:
            self.assertEqual(
                item.pin.content_sha256,
                fixture.fixture_sha256(self.root, item.pin.relative_path),
            )
            self.assertEqual(
                item.byte_count,
                len((self.root / item.pin.relative_path).read_bytes()),
            )
            self.assertEqual(item.modified_at.tzinfo, UTC)
            self.assertEqual(item.pin.observed_at, FIXED_NOW)
        self.assertEqual(inventory.skipped, [])

    def test_inventory_for_activity_scopes_to_the_incident_dir(self) -> None:
        inventory = self.adapter.list_matter_artifacts(
            fixture.INCIDENT_ID, parent_legacy_id=fixture.PROBLEM_ID
        )

        self.assertEqual(inventory.matter_root, fixture.INCIDENT_DIR_RELATIVE)
        paths = {item.pin.relative_path for item in inventory.artifacts}
        self.assertIn(fixture.INCIDENT_RELATIVE, paths)
        self.assertNotIn(fixture.PROBLEM_RELATIVE, paths)

    def test_inventory_fails_closed_without_matter_authorization(self) -> None:
        unauthorized = HammerTimeReleaseAdapter(root=self.root, clock=lambda: FIXED_NOW)
        with self.assertRaises(MatterAccessDenied):
            unauthorized.list_matter_artifacts(fixture.PROBLEM_ID)
        denied = HammerTimeReleaseAdapter(
            root=self.root,
            matter_authorizer=lambda request: False,
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaises(MatterAccessDenied):
            denied.list_matter_artifacts(fixture.PROBLEM_ID)

    def test_inventory_skips_symlinks_and_inbox_without_following(self) -> None:
        problem_dir = self.root / "incidents" / "problems" / fixture.PROBLEM_SLUG
        (problem_dir / "inbox").mkdir()
        (problem_dir / "inbox" / "queued.md").write_text("denied\n")
        os.symlink("PROBLEM.md", problem_dir / "linked.md")

        inventory = self.adapter.list_matter_artifacts(fixture.PROBLEM_ID)
        paths = {item.pin.relative_path for item in inventory.artifacts}

        self.assertNotIn(f"incidents/problems/{fixture.PROBLEM_SLUG}/linked.md", paths)
        self.assertFalse(any("inbox" in path.lower() for path in paths))
        skipped = "\n".join(inventory.skipped)
        self.assertIn("linked.md:symlink", skipped)
        self.assertIn("inbox:forbidden-inbox", skipped)

    def test_read_matter_artifact_reads_gated_content_with_hash_check(self) -> None:
        relative = f"{fixture.INCIDENT_DIR_RELATIVE}/validation-report.json"
        expected = fixture.fixture_sha256(self.root, relative)

        read = self.adapter.read_matter_artifact(
            fixture.INCIDENT_ID,
            relative,
            parent_legacy_id=fixture.PROBLEM_ID,
            expected_sha256=expected,
        )
        self.assertEqual(read.pin.content_sha256, expected)
        self.assertIn(b"source_preservation", read.content)

        with self.assertRaises(SourceHashMismatchError):
            self.adapter.read_matter_artifact(
                fixture.INCIDENT_ID,
                relative,
                parent_legacy_id=fixture.PROBLEM_ID,
                expected_sha256="0" * 64,
            )

    def test_read_matter_artifact_refuses_paths_outside_the_matter_tree(self) -> None:
        with self.assertRaises(ForbiddenPathError):
            self.adapter.read_matter_artifact(
                fixture.PROBLEM_ID,
                fixture.REFERENCE_RELATIVE,
            )
        with self.assertRaises(ForbiddenPathError):
            self.adapter.read_matter_artifact(
                fixture.PROBLEM_ID,
                "incidents/_incident-registry.md",
            )
        with self.assertRaises(ForbiddenPathError):
            self.adapter.read_matter_artifact(
                fixture.PROBLEM_ID,
                f"incidents/problems/{fixture.PROBLEM_SLUG}/../_incident-registry.md",
            )

    def test_read_matter_artifact_fails_closed_without_authorization(self) -> None:
        unauthorized = HammerTimeReleaseAdapter(root=self.root, clock=lambda: FIXED_NOW)
        with self.assertRaises(MatterAccessDenied):
            unauthorized.read_matter_artifact(
                fixture.INCIDENT_ID,
                f"{fixture.INCIDENT_DIR_RELATIVE}/validation-report.json",
                parent_legacy_id=fixture.PROBLEM_ID,
            )


if __name__ == "__main__":
    unittest.main()
