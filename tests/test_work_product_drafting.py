from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sklegal_domain import (
    ArtifactBinding,
    DomainTransitionError,
    IdentityMutationError,
    WorkProduct,
    WorkProductStatus,
    WorkProductTemplate,
    WorkProductTemplateStatus,
    WorkProductTemplateVersion,
    WorkProductTemplateVersionStatus,
    WorkProductUnknown,
    WorkProductUnknownStatus,
    WorkProductVersion,
    WorkProductVersionStatus,
    assert_approval_matches_version,
    assert_freeze_ready,
    build_successor_version,
    extract_unknown_occurrences,
    register_unknown_keys,
    supersede_version,
    unresolved_blockers,
)
from sklegal_persistence import (
    PersistenceMetadata,
    decompose,
    reconstruct,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
STEP = timedelta(seconds=1)


def uid(value: int) -> UUID:
    return UUID(int=(1 << 127) | value)


def sha(value: str = "a") -> str:
    return value * 64


def audit(identifier: int) -> dict[str, object]:
    return {"id": uid(identifier), "created_at": T0, "updated_at": T0}


def protected(identifier: int) -> dict[str, object]:
    return {**audit(identifier), "tenant_id": uid(1)}


def scoped(identifier: int) -> dict[str, object]:
    return {**protected(identifier), "matter_id": uid(4)}


def version(
    identifier: int = 101,
    *,
    number: int = 1,
    digest: str = "a",
    work_product_id: UUID = uid(100),
) -> WorkProductVersion:
    return WorkProductVersion(
        **scoped(identifier),
        work_product_id=work_product_id,
        version_number=number,
        content_sha256=sha(digest),
        source_artifact_id=uid(600),
    )


def binding_for(artifact: WorkProductVersion) -> ArtifactBinding:
    return ArtifactBinding(
        artifact_id=artifact.id,
        artifact_version=artifact.version_number,
        content_sha256=artifact.content_sha256,
    )


def unknown(
    identifier: int = 200,
    *,
    artifact: WorkProductVersion,
    key: str = "client_name",
    tenant_id: UUID | None = None,
) -> WorkProductUnknown:
    payload = scoped(identifier)
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id
    return WorkProductUnknown(
        **payload,
        version_binding=binding_for(artifact),
        placeholder_key=key,
        hint="Full legal name",
    )


def frozen(artifact: WorkProductVersion) -> WorkProductVersion:
    return artifact.transition_to(WorkProductVersionStatus.FROZEN, at=T0 + STEP)


class UnknownExtractionTests(unittest.TestCase):
    def test_bracketed_unknowns_extract_in_document_order(self) -> None:
        content = (
            "Dear [?client_name: full legal name], your matter "
            "[?matter_ref] is due on [?filing_date: court-assigned]."
        )
        occurrences = extract_unknown_occurrences(content)
        self.assertEqual(
            ("client_name", "matter_ref", "filing_date"),
            tuple(item.placeholder_key for item in occurrences),
        )
        self.assertEqual("full legal name", occurrences[0].hint)
        self.assertIsNone(occurrences[1].hint)
        for item in occurrences:
            self.assertEqual(item.marker, content[item.start : item.end])

    def test_plain_brackets_and_malformed_markers_are_not_unknowns(self) -> None:
        content = "See [1] and [citation], plus [?], [?Bad], [?ok_key]."
        occurrences = extract_unknown_occurrences(content)
        self.assertEqual(
            ("ok_key",), tuple(item.placeholder_key for item in occurrences)
        )

    def test_duplicate_keys_stay_visible_and_register_once(self) -> None:
        content = "[?client_name] versus [?client_name: repeated] and [?forum]"
        occurrences = extract_unknown_occurrences(content)
        self.assertEqual(3, len(occurrences))
        registered = register_unknown_keys(occurrences)
        self.assertEqual(
            ("client_name", "forum"),
            tuple(item.placeholder_key for item in registered),
        )
        self.assertIsNone(registered[0].hint)

    def test_extraction_rejects_non_text(self) -> None:
        with self.assertRaises(TypeError):
            extract_unknown_occurrences(b"bytes")  # type: ignore[arg-type]


class UnknownBlockerTests(unittest.TestCase):
    def test_open_unknown_blocks_freeze_readiness(self) -> None:
        artifact = version()
        blocker = unknown(artifact=artifact)
        with self.assertRaisesRegex(
            DomainTransitionError, "unresolved bracketed unknowns: client_name"
        ):
            assert_freeze_ready(artifact, (blocker,))
        resolved = blocker.transition_to(
            WorkProductUnknownStatus.RESOLVED,
            at=T0 + STEP,
            resolved_by_principal_id=uid(700),
            resolved_at=T0 + STEP,
        )
        self.assertEqual((), unresolved_blockers(artifact, (resolved,)))
        assert_freeze_ready(artifact, (resolved,))

    def test_unknown_binds_only_its_exact_version(self) -> None:
        artifact = version(101, number=1, digest="a")
        successor = version(102, number=2, digest="b")
        blocker = unknown(artifact=artifact)
        self.assertEqual((), unresolved_blockers(successor, (blocker,)))
        assert_freeze_ready(successor, (blocker,))
        with self.assertRaisesRegex(DomainTransitionError, "client_name"):
            assert_freeze_ready(artifact, (blocker,))
        changed_content = version(101, number=1, digest="c")
        self.assertEqual((), unresolved_blockers(changed_content, (blocker,)))

    def test_unknown_from_another_tenant_never_binds(self) -> None:
        artifact = version()
        foreign = unknown(artifact=artifact, tenant_id=uid(2))
        self.assertEqual((), unresolved_blockers(artifact, (foreign,)))

    def test_unknown_resolution_evidence_is_set_once(self) -> None:
        artifact = version()
        blocker = unknown(artifact=artifact)
        resolved = blocker.transition_to(
            WorkProductUnknownStatus.RESOLVED,
            at=T0 + STEP,
            resolved_by_principal_id=uid(700),
            resolved_at=T0 + STEP,
        )
        with self.assertRaisesRegex(DomainTransitionError, "state evidence"):
            resolved.evolve(at=T0 + 2 * STEP, resolved_by_principal_id=uid(701))
        with self.assertRaisesRegex(DomainTransitionError, "state evidence"):
            blocker.evolve(at=T0 + STEP, resolved_at=T0 + STEP)
        with self.assertRaises(ValidationError):
            unknown(artifact=artifact).model_validate(
                {
                    **scoped(201),
                    "version_binding": binding_for(artifact).model_dump(mode="python"),
                    "placeholder_key": "client_name",
                    "status": "resolved",
                },
                strict=True,
            )


class VersionImmutabilityTests(unittest.TestCase):
    def test_work_product_version_payload_is_immutable(self) -> None:
        artifact = version()
        for changes in (
            {"content_sha256": sha("b")},
            {"version_number": 2},
            {"work_product_id": uid(999)},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(IdentityMutationError):
                    artifact.evolve(at=T0 + STEP, **changes)

    def test_work_product_version_transition_graph_is_closed(self) -> None:
        artifact = version()
        with self.assertRaisesRegex(DomainTransitionError, "invalid"):
            artifact.transition_to(WorkProductVersionStatus.SUPERSEDED, at=T0 + STEP)
        locked = frozen(artifact)
        with self.assertRaisesRegex(DomainTransitionError, "invalid"):
            locked.transition_to(WorkProductVersionStatus.DRAFT, at=T0 + 2 * STEP)
        superseded = supersede_version(locked, at=T0 + 2 * STEP)
        self.assertEqual(WorkProductVersionStatus.SUPERSEDED, superseded.status)
        with self.assertRaisesRegex(DomainTransitionError, "invalid"):
            superseded.transition_to(WorkProductVersionStatus.FROZEN, at=T0 + 3 * STEP)

    def test_template_version_payload_is_immutable(self) -> None:
        template_version = WorkProductTemplateVersion(
            **protected(301),
            template_id=uid(300),
            version_number=1,
            content_sha256=sha("d"),
        )
        with self.assertRaises(IdentityMutationError):
            template_version.evolve(at=T0 + STEP, content_sha256=sha("e"))
        locked = template_version.transition_to(
            WorkProductTemplateVersionStatus.FROZEN, at=T0 + STEP
        )
        with self.assertRaises(IdentityMutationError):
            locked.evolve(at=T0 + 2 * STEP, template_id=uid(999))
        archived = locked.transition_to(
            WorkProductTemplateVersionStatus.ARCHIVED, at=T0 + 2 * STEP
        )
        self.assertEqual(WorkProductTemplateVersionStatus.ARCHIVED, archived.status)

    def test_template_descriptive_payload_freezes_after_draft(self) -> None:
        template = WorkProductTemplate(
            **protected(300),
            name="Synthetic memo template",
            work_product_kind="memo",
            current_version_id=uid(301),
        )
        renamed = template.evolve(at=T0 + STEP, name="Renamed template")
        self.assertEqual("Renamed template", renamed.name)
        active = renamed.transition_to(
            WorkProductTemplateStatus.ACTIVE, at=T0 + 2 * STEP
        )
        with self.assertRaisesRegex(DomainTransitionError, "draft"):
            active.evolve(at=T0 + 3 * STEP, name="Late rename")
        with self.assertRaises(IdentityMutationError):
            template.evolve(at=T0 + STEP, work_product_kind="letter")
        retired = active.transition_to(
            WorkProductTemplateStatus.RETIRED, at=T0 + 3 * STEP
        )
        with self.assertRaisesRegex(DomainTransitionError, "invalid"):
            retired.transition_to(WorkProductTemplateStatus.ACTIVE, at=T0 + 4 * STEP)


class SupersessionChainTests(unittest.TestCase):
    def product(self) -> WorkProduct:
        return WorkProduct(
            **scoped(100),
            title="Synthetic memo",
            work_product_kind="memo",
            current_version_id=uid(101),
        )

    def test_successor_version_extends_the_chain(self) -> None:
        work_product = self.product()
        current = frozen(version())
        successor = build_successor_version(
            work_product,
            current,
            version_id=uid(102),
            content_sha256=sha("b"),
            source_artifact_id=uid(601),
            at=T0 + 2 * STEP,
        )
        self.assertEqual(2, successor.version_number)
        self.assertEqual(WorkProductVersionStatus.DRAFT, successor.status)
        self.assertEqual(current.work_product_id, successor.work_product_id)
        superseded = supersede_version(current, at=T0 + 3 * STEP)
        self.assertEqual(WorkProductVersionStatus.SUPERSEDED, superseded.status)

    def test_successor_requires_changed_digest_and_frozen_current(self) -> None:
        work_product = self.product()
        current = frozen(version())
        with self.assertRaisesRegex(DomainTransitionError, "digest"):
            build_successor_version(
                work_product,
                current,
                version_id=uid(102),
                content_sha256=current.content_sha256,
                source_artifact_id=uid(601),
                at=T0 + 2 * STEP,
            )
        draft = version()
        with self.assertRaisesRegex(DomainTransitionError, "frozen"):
            build_successor_version(
                work_product,
                draft,
                version_id=uid(102),
                content_sha256=sha("b"),
                source_artifact_id=uid(601),
                at=T0 + 2 * STEP,
            )
        other_product = WorkProduct(
            **scoped(900),
            title="Other memo",
            work_product_kind="memo",
            current_version_id=uid(901),
        )
        with self.assertRaisesRegex(DomainTransitionError, "scope"):
            build_successor_version(
                other_product,
                current,
                version_id=uid(102),
                content_sha256=sha("b"),
                source_artifact_id=uid(601),
                at=T0 + 2 * STEP,
            )

    def test_approval_binds_the_exact_version_and_rejects_changes(self) -> None:
        current = frozen(version())
        assert_approval_matches_version(binding_for(current), current)
        for artifact in (
            version(101, number=1, digest="c"),
            version(101, number=2, digest="a"),
            version(103, number=1, digest="a"),
        ):
            with self.subTest(artifact=artifact.id):
                with self.assertRaisesRegex(
                    DomainTransitionError, "changed after approval"
                ):
                    assert_approval_matches_version(binding_for(current), artifact)


class ApprovedVersionRollbackTests(unittest.TestCase):
    def test_approved_work_product_edits_require_a_new_version(self) -> None:
        work_product = WorkProduct(
            **scoped(100),
            title="Synthetic memo",
            work_product_kind="memo",
            current_version_id=uid(101),
        )
        reviewed = work_product.transition_to(WorkProductStatus.IN_REVIEW, at=T0 + STEP)
        validated = reviewed.transition_to(
            WorkProductStatus.VALIDATED,
            at=T0 + 2 * STEP,
            validation_result_id=uid(102),
        )
        approved = validated.transition_to(
            WorkProductStatus.APPROVED,
            at=T0 + 3 * STEP,
            approval_id=uid(103),
        )
        with self.assertRaisesRegex(DomainTransitionError, "reset"):
            approved.evolve(at=T0 + 4 * STEP, current_version_id=uid(104))

        current = frozen(version())
        successor = build_successor_version(
            approved,
            current,
            version_id=uid(104),
            content_sha256=sha("b"),
            source_artifact_id=uid(601),
            at=T0 + 4 * STEP,
        )
        rolled_back = approved.transition_to(
            WorkProductStatus.IN_REVIEW,
            at=T0 + 5 * STEP,
            current_version_id=successor.id,
            validation_result_id=None,
            approval_id=None,
        )
        self.assertEqual(successor.id, rolled_back.current_version_id)
        self.assertIsNone(rolled_back.validation_result_id)
        self.assertIsNone(rolled_back.approval_id)
        with self.assertRaises(DomainTransitionError):
            rolled_back.transition_to(WorkProductStatus.APPROVED, at=T0 + 6 * STEP)


class DraftingPersistenceMappingTests(unittest.TestCase):
    def test_template_round_trips_through_the_mapping_contract(self) -> None:
        template = WorkProductTemplate(
            **protected(300),
            name="Synthetic memo template",
            work_product_kind="memo",
            current_version_id=uid(301),
        )
        metadata = PersistenceMetadata(
            scalar={"current_version_number": 1, "current_content_sha256": sha("d")}
        )
        write = decompose(template, metadata)
        self.assertEqual("sklegal_legal.work_product_templates", write.table)
        self.assertEqual(template, reconstruct("WorkProductTemplate", write.row, {}))

    def test_template_version_round_trips_through_the_mapping_contract(self) -> None:
        template_version = WorkProductTemplateVersion(
            **protected(301),
            template_id=uid(300),
            version_number=1,
            content_sha256=sha("d"),
        )
        metadata = PersistenceMetadata(
            scalar={
                "encrypted_content": None,
                "encryption_key_ref": None,
                "encryption_algorithm": None,
                "encrypted_at": None,
            }
        )
        write = decompose(template_version, metadata)
        self.assertEqual("sklegal_legal.work_product_template_versions", write.table)
        self.assertEqual(
            template_version,
            reconstruct("WorkProductTemplateVersion", write.row, {}),
        )

    def test_unknown_round_trips_with_exact_version_binding(self) -> None:
        artifact = version()
        blocker = unknown(artifact=artifact)
        write = decompose(blocker)
        self.assertEqual("sklegal_legal.work_product_unknowns", write.table)
        self.assertEqual(artifact.id, write.row["work_product_version_id"])
        self.assertEqual(1, write.row["work_product_version_number"])
        self.assertEqual(
            artifact.content_sha256, write.row["work_product_content_sha256"]
        )
        restored = reconstruct("WorkProductUnknown", write.row, {})
        self.assertEqual(blocker, restored)
        self.assertEqual(binding_for(artifact), restored.version_binding)


if __name__ == "__main__":
    unittest.main()
