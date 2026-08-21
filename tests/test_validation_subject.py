from __future__ import annotations

import re
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args
from uuid import UUID

from pydantic import ValidationError
from sklegal_domain import (
    LEGAL_VALIDATION_SUBJECT_KINDS,
    Approval,
    ArtifactBinding,
    Execution,
    ValidationOutcome,
    ValidationResult,
    ValidationSubject,
)

AT = datetime(2026, 1, 1, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def uid(value: int) -> UUID:
    return UUID(int=(1 << 127) | value)


class ValidationSubjectTests(unittest.TestCase):
    def test_closed_vocabulary_matches_python_sql_mapper_and_docs(self) -> None:
        expected = set(LEGAL_VALIDATION_SUBJECT_KINDS)
        annotation = ValidationSubject.model_fields["subject_kind"].annotation
        self.assertEqual(expected, set(get_args(annotation)))

        sql = (ROOT / "migrations/0003_legal_records.sql").read_text(encoding="utf-8")
        table_match = re.search(
            r"subject_kind text NOT NULL CHECK \(subject_kind IN \((.*?)\)\),",
            sql,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(table_match)
        assert table_match is not None
        sql_kinds = set(re.findall(r"'([^']+)'", table_match.group(1)))
        self.assertEqual(expected | {"work_product_version"}, sql_kinds)

        resolver_match = re.search(
            r"ELSIF NOT \(CASE NEW\.subject_kind(.*?)ELSE false\s+END\) THEN",
            sql,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(resolver_match)
        assert resolver_match is not None
        resolver_kinds = set(
            re.findall(r"WHEN '([^']+)' THEN EXISTS", resolver_match.group(1))
        )
        self.assertEqual(expected, resolver_kinds)

        domain_doc = (ROOT / "docs/development/DOMAIN.md").read_text(encoding="utf-8")
        persistence_doc = (ROOT / "docs/development/PERSISTENCE.md").read_text(
            encoding="utf-8"
        )
        for kind in expected:
            with self.subTest(kind=kind):
                self.assertIn(f"`{kind}`", domain_doc)
                self.assertIn(f"`{kind}`", persistence_doc)

    def test_typed_nonartifact_subject_strict_json_round_trip(self) -> None:
        result = ValidationResult(
            id=uid(1),
            tenant_id=uid(2),
            matter_id=uid(3),
            created_at=AT,
            updated_at=AT,
            subject=ValidationSubject(
                subject_kind="party",
                artifact_id=uid(4),
                artifact_version=2,
            ),
            outcome=ValidationOutcome.PASSED,
            check_ids=("synthetic.party",),
            validator_principal_id=uid(5),
            validated_at=AT,
            rationale="Synthetic exact party validation.",
        )
        restored = ValidationResult.model_validate_json(result.model_dump_json())
        self.assertEqual(result, restored)
        self.assertIsInstance(restored.subject, ValidationSubject)

    def test_artifact_binding_remains_backward_compatible(self) -> None:
        binding = ArtifactBinding(
            artifact_id=uid(10), artifact_version=1, content_sha256="a" * 64
        )
        result = ValidationResult(
            id=uid(11),
            tenant_id=uid(2),
            matter_id=uid(3),
            created_at=AT,
            updated_at=AT,
            subject=binding,
            outcome=ValidationOutcome.PASSED,
            check_ids=("synthetic.artifact",),
            validator_principal_id=uid(5),
            validated_at=AT,
            rationale="Synthetic exact artifact validation.",
        )
        self.assertEqual(binding, result.subject)

    def test_invalid_kind_nil_id_version_and_digest_rules_are_rejected(self) -> None:
        invalid_payloads = (
            {
                "subject_kind": "unknown",
                "artifact_id": uid(4),
                "artifact_version": 1,
            },
            {
                "subject_kind": "party",
                "artifact_id": UUID(int=0),
                "artifact_version": 1,
            },
            {
                "subject_kind": "party",
                "artifact_id": uid(4),
                "artifact_version": 0,
            },
            {
                "subject_kind": "party",
                "artifact_id": uid(4),
                "artifact_version": 1,
                "content_sha256": "b" * 64,
            },
            {
                "subject_kind": "work_product_version",
                "artifact_id": uid(4),
                "artifact_version": 1,
                "content_sha256": "c" * 64,
            },
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    ValidationSubject.model_validate(payload, strict=True)

    def test_approval_and_execution_gates_remain_artifact_only(self) -> None:
        self.assertIs(ArtifactBinding, Approval.model_fields["subject"].annotation)
        self.assertIs(ArtifactBinding, Execution.model_fields["subject"].annotation)


if __name__ == "__main__":
    unittest.main()
