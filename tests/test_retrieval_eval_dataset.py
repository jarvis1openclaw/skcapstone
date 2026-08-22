"""Tests for the SKL-S3-04A frozen retrieval evaluation dataset."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter
from sklegal_retrieval.evaluation_dataset import (
    DATASET_SCHEMA,
    DATASET_VERSION,
    DatasetManifest,
    EvalCaseClass,
    EvalDocument,
    EvalDocumentKind,
    EvalQuery,
    EvaluationDatasetError,
    ExpectedAnswerState,
    FrozenEvaluationDataset,
    JudgmentBasis,
    RelevanceJudgment,
    compute_freeze_sha256,
    dataset_invariant_violations,
    distinct_query_term_overlap,
    judgments_for_query,
    load_frozen_dataset,
    longest_common_word_run,
    relevant_document_ids,
    tokenize,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = REPO_ROOT / "evals/retrieval/frozen-v1"
MANIFEST = json.loads((DATASET_ROOT / "manifest.json").read_text(encoding="utf-8"))
TENANT_A = UUID("11111111-1111-4111-8111-111111111111")
TENANT_B = UUID("22222222-2222-4222-8222-222222222222")


@contextmanager
def tempfile_dir():
    with tempfile.TemporaryDirectory() as name:
        yield Path(name)


class FrozenFileIntegrityTests(unittest.TestCase):
    """The committed dataset bytes match the manifest pin for pin."""

    def test_manifest_pins_the_card_task_and_amendment(self) -> None:
        self.assertEqual(DATASET_SCHEMA, MANIFEST["schema"])
        self.assertEqual("e1bc4552", MANIFEST["card"])
        self.assertEqual("SKL-S3-04A", MANIFEST["task"])
        self.assertEqual("SKL-S3-04", MANIFEST["parent_task"])
        self.assertEqual("AMENDMENT-SKL-S2-10", MANIFEST["amendment"])
        self.assertEqual(DATASET_VERSION, MANIFEST["dataset_version"])

    def test_every_pinned_file_hash_matches_its_committed_bytes(self) -> None:
        for name in ("documents.json", "queries.json", "judgments.json"):
            with self.subTest(file=name):
                self.assertEqual(
                    MANIFEST[f"{name.removesuffix('.json')}_sha256"],
                    hashlib.sha256((DATASET_ROOT / name).read_bytes()).hexdigest(),
                )

    def test_freeze_hash_binds_schema_version_and_file_hashes(self) -> None:
        self.assertEqual(
            MANIFEST["freeze_sha256"],
            compute_freeze_sha256(
                schema=MANIFEST["schema"],
                dataset_version=MANIFEST["dataset_version"],
                documents_sha256=MANIFEST["documents_sha256"],
                queries_sha256=MANIFEST["queries_sha256"],
                judgments_sha256=MANIFEST["judgments_sha256"],
            ),
        )

    def test_freeze_hash_moves_when_any_input_moves(self) -> None:
        base = dict(
            schema=MANIFEST["schema"],
            dataset_version=MANIFEST["dataset_version"],
            documents_sha256=MANIFEST["documents_sha256"],
            queries_sha256=MANIFEST["queries_sha256"],
            judgments_sha256=MANIFEST["judgments_sha256"],
        )
        original = compute_freeze_sha256(**base)
        for key in (
            "schema",
            "dataset_version",
            "documents_sha256",
            "queries_sha256",
            "judgments_sha256",
        ):
            mutated = dict(base)
            mutated[key] = "0" * 64 if key.endswith("sha256") else "other"
            with self.subTest(mutated=key):
                self.assertNotEqual(original, compute_freeze_sha256(**mutated))

    def test_loader_verifies_and_returns_the_committed_dataset(self) -> None:
        dataset = load_frozen_dataset(DATASET_ROOT)
        self.assertEqual(MANIFEST["freeze_sha256"], dataset.manifest.freeze_sha256)
        self.assertTrue(dataset_invariant_violations(dataset) == ())

    def test_loader_rejects_a_mutated_judgment_byte(self) -> None:
        records = json.loads(
            (DATASET_ROOT / "judgments.json").read_text(encoding="utf-8")
        )
        records[0]["grade"] = 0 if records[0]["grade"] else 3
        with tempfile_dir() as scratch:
            for name in ("documents.json", "queries.json"):
                (scratch / name).write_bytes((DATASET_ROOT / name).read_bytes())
            (scratch / "judgments.json").write_text(
                json.dumps(records), encoding="utf-8"
            )
            (scratch / "manifest.json").write_bytes(
                (DATASET_ROOT / "manifest.json").read_bytes()
            )
            with self.assertRaises(EvaluationDatasetError):
                load_frozen_dataset(scratch)

    def test_loader_rejects_a_manifest_hash_forgery(self) -> None:
        with tempfile_dir() as scratch:
            for name in (
                "documents.json",
                "queries.json",
                "judgments.json",
                "manifest.json",
            ):
                (scratch / name).write_bytes((DATASET_ROOT / name).read_bytes())
            forged = dict(MANIFEST)
            forged["freeze_sha256"] = "f" * 64
            (scratch / "manifest.json").write_text(json.dumps(forged), encoding="utf-8")
            with self.assertRaises(EvaluationDatasetError):
                load_frozen_dataset(scratch)

    def test_loader_rejects_a_missing_file(self) -> None:
        with tempfile_dir() as scratch:
            for name in (
                "documents.json",
                "queries.json",
                "judgments.json",
                "manifest.json",
            ):
                (scratch / name).write_bytes((DATASET_ROOT / name).read_bytes())
            (scratch / "queries.json").unlink()
            with self.assertRaises(EvaluationDatasetError):
                load_frozen_dataset(scratch)


class FrozenCaseCoverageTests(unittest.TestCase):
    """The frozen set covers the ten amendment case classes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = load_frozen_dataset(DATASET_ROOT)

    def test_every_amendment_case_class_has_a_query(self) -> None:
        present = {query.case_class for query in self.dataset.queries}
        self.assertEqual(set(EvalCaseClass), present)

    def test_no_answer_queries_expect_no_answer(self) -> None:
        for query in self.dataset.queries:
            if query.case_class is EvalCaseClass.NO_ANSWER:
                self.assertIs(
                    query.expected_answer_state, ExpectedAnswerState.NO_ANSWER
                )

    def test_every_query_has_at_least_one_judgment(self) -> None:
        for query in self.dataset.queries:
            self.assertTrue(judgments_for_query(self.dataset, query.query_id))

    def test_privilege_covers_both_authorized_and_blocked_lanes(self) -> None:
        states = {
            query.expected_answer_state
            for query in self.dataset.queries
            if query.case_class is EvalCaseClass.PRIVILEGE_PARTITION
        }
        self.assertIn(ExpectedAnswerState.ANSWERABLE, states)
        self.assertIn(ExpectedAnswerState.BLOCKED, states)

    def test_injection_covers_document_and_query_origins(self) -> None:
        origins = set()
        for query in self.dataset.queries:
            if query.case_class is EvalCaseClass.PROMPT_INJECTION:
                origins.add(query.injection_origin.value)
        self.assertEqual({"document_borne", "query_borne"}, origins)

    def test_dataset_spans_two_tenants_and_at_least_three_matters(self) -> None:
        tenants = {query.tenant_id for query in self.dataset.queries}
        matters = {(query.tenant_id, query.matter_id) for query in self.dataset.queries}
        self.assertEqual(2, len(tenants))
        self.assertGreaterEqual(len(matters), 3)

    def test_superseded_fixture_points_at_a_current_successor(self) -> None:
        documents = {
            document.document_id: document for document in self.dataset.documents
        }
        found = [
            document
            for document in self.dataset.documents
            if document.authority_status is not None
            and document.authority_status.value == "superseded"
        ]
        self.assertTrue(found)
        for document in found:
            successor = documents[document.superseded_by]
            self.assertEqual("current", successor.authority_status.value)
            self.assertEqual(document.jurisdiction, successor.jurisdiction)

    def test_fixture_holds_an_ocr_derived_and_an_injection_document(self) -> None:
        self.assertTrue(
            any(document.ocr_derived for document in self.dataset.documents)
        )
        self.assertTrue(
            any(
                document.contains_prompt_injection
                for document in self.dataset.documents
            )
        )


class FrozenLeakageTests(unittest.TestCase):
    """Leakage invariants reject the failure shapes they exist to catch."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = load_frozen_dataset(DATASET_ROOT)

    def _mutated(self) -> FrozenEvaluationDataset:
        payload = json.loads(
            json.dumps(
                {
                    "manifest": self.dataset.manifest.model_dump(
                        mode="json", by_alias=True
                    ),
                    "documents": [
                        document.model_dump(mode="json")
                        for document in self.dataset.documents
                    ],
                    "queries": [
                        query.model_dump(mode="json") for query in self.dataset.queries
                    ],
                    "judgments": [
                        judgment.model_dump(mode="json")
                        for judgment in self.dataset.judgments
                    ],
                }
            )
        )
        return payload

    def _reload(self, payload: dict) -> FrozenEvaluationDataset:
        """Materialize records for the invariant engine.

        The integrity validator inside FrozenEvaluationDataset rejects
        dangling references before the leakage invariants run, so tests that
        exercise the invariants themselves rebuild the container through the
        parent constructor after validating each record.
        """

        manifest = TypeAdapter(DatasetManifest).validate_json(
            json.dumps(payload["manifest"])
        )
        dataset = FrozenEvaluationDataset(
            manifest=manifest,
            documents=tuple(
                TypeAdapter(EvalDocument).validate_json(json.dumps(item))
                for item in payload["documents"]
            ),
            queries=tuple(
                TypeAdapter(EvalQuery).validate_json(json.dumps(item))
                for item in payload["queries"]
            ),
            judgments=tuple(
                TypeAdapter(RelevanceJudgment).validate_json(json.dumps(item))
                for item in payload["judgments"]
            ),
        )
        return dataset

    def _reload_raw(self, payload: dict) -> FrozenEvaluationDataset:
        """Materialize records without the container reference validator."""

        from pydantic import ConfigDict

        class _Tolerant(FrozenEvaluationDataset):
            model_config = ConfigDict(validate_default=True)

            @classmethod
            def tolerant(
                cls,
                manifest: DatasetManifest,
                documents: tuple,
                queries: tuple,
                judgments: tuple,
            ) -> _Tolerant:
                values = {
                    "manifest": manifest,
                    "documents": documents,
                    "queries": queries,
                    "judgments": judgments,
                }
                instance = cls.__new__(cls)
                object.__setattr__(instance, "__dict__", values)
                object.__setattr__(instance, "__pydantic_fields_set__", set(values))
                return instance

        return _Tolerant.tolerant(
            TypeAdapter(DatasetManifest).validate_json(json.dumps(payload["manifest"])),
            tuple(
                TypeAdapter(EvalDocument).validate_json(json.dumps(item))
                for item in payload["documents"]
            ),
            tuple(
                TypeAdapter(EvalQuery).validate_json(json.dumps(item))
                for item in payload["queries"]
            ),
            tuple(
                TypeAdapter(RelevanceJudgment).validate_json(json.dumps(item))
                for item in payload["judgments"]
            ),
        )

    def test_cross_tenant_judgment_is_reported(self) -> None:
        payload = self._mutated()
        for judgment in payload["judgments"]:
            query = next(
                item
                for item in payload["queries"]
                if item["query_id"] == judgment["query_id"]
            )
            if query["tenant_id"].startswith("11111111"):
                judgment["document_id"] = "QB-DOC-0001"
                break
        violations = dataset_invariant_violations(self._reload_raw(payload))
        self.assertTrue(any(item.startswith("L03:") for item in violations))

    def test_privilege_escalation_through_a_query_is_reported(self) -> None:
        payload = self._mutated()
        for judgment in payload["judgments"]:
            if judgment["document_id"] == "QA-DOC-0008":
                judgment["grade"] = 3
                judgment["bases"] = ["relevant_support"]
        violations = dataset_invariant_violations(self._reload(payload))
        self.assertTrue(
            any(
                item.startswith(("L04:", "L06:", "L19:", "L24:")) for item in violations
            )
        )

    def test_missing_case_class_is_reported(self) -> None:
        payload = self._mutated()
        payload["queries"] = [
            query
            for query in payload["queries"]
            if query["case_class"] != EvalCaseClass.OCR_NOISE.value
        ]
        payload["judgments"] = [
            judgment
            for judgment in payload["judgments"]
            if judgment["query_id"] != "QA-Q-0008"
        ]
        violations = dataset_invariant_violations(self._reload(payload))
        self.assertTrue(any(item.startswith("L01:") for item in violations))

    def test_query_text_carrying_a_corpus_payload_is_reported(self) -> None:
        payload = self._mutated()
        payload_document = next(
            document
            for document in payload["documents"]
            if document.get("injection_payload")
        )
        for query in payload["queries"]:
            if query["case_class"] == EvalCaseClass.PARAPHRASE.value:
                query["query_text"] = (
                    payload_document["injection_payload"] + " " + query["query_text"]
                )
                break
        violations = dataset_invariant_violations(self._reload(payload))
        self.assertTrue(any(item.startswith("L18:") for item in violations))

    def test_judgment_without_a_query_is_rejected(self) -> None:
        payload = self._mutated()
        removed = payload["queries"][-1]["query_id"]
        payload["queries"] = payload["queries"][:-1]
        orphaned = [
            judgment
            for judgment in payload["judgments"]
            if judgment["query_id"] == removed
        ]
        self.assertTrue(orphaned)
        with self.assertRaises(ValueError):
            self._reload(payload)
        trimmed = {
            **payload,
            "judgments": [
                judgment
                for judgment in payload["judgments"]
                if judgment["query_id"] != removed
            ],
        }
        self.assertEqual((), dataset_invariant_violations(self._reload(trimmed)))

    def test_no_answer_query_with_a_relevant_hit_is_reported(self) -> None:
        payload = self._mutated()
        for judgment in payload["judgments"]:
            if judgment["query_id"] == "QA-Q-0009" and judgment["grade"] == 0:
                judgment["grade"] = 2
                judgment["bases"] = ["relevant_support"]
                break
        violations = dataset_invariant_violations(self._reload(payload))
        self.assertTrue(any(item.startswith("L07:") for item in violations))

    def test_superseded_authority_promotion_is_reported(self) -> None:
        payload = self._mutated()
        for judgment in payload["judgments"]:
            if judgment["document_id"] == "QA-DOC-0002":
                judgment["grade"] = 3
                judgment["bases"] = ["relevant_support"]
        violations = dataset_invariant_violations(self._reload(payload))
        self.assertTrue(any(item.startswith("L12:") for item in violations))

    def test_jurisdiction_distractor_promotion_is_reported(self) -> None:
        payload = self._mutated()
        for judgment in payload["judgments"]:
            if judgment["document_id"] == "QA-DOC-0003":
                judgment["grade"] = 2
                judgment["bases"] = ["relevant_support"]
        violations = dataset_invariant_violations(self._reload(payload))
        self.assertTrue(any(item.startswith("L13:") for item in violations))

    def test_answerable_query_without_support_is_reported(self) -> None:
        payload = self._mutated()
        for judgment in payload["judgments"]:
            if judgment["query_id"] == "QA-Q-0002":
                judgment["grade"] = 1
                judgment["bases"] = ["background"]
        violations = dataset_invariant_violations(self._reload(payload))
        self.assertTrue(any(item.startswith("L08:") for item in violations))


class FrozenJudgmentSemanticsTests(unittest.TestCase):
    """The frozen grades follow the documented semantics."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = load_frozen_dataset(DATASET_ROOT)

    def test_grades_stay_inside_the_documented_scale(self) -> None:
        for judgment in self.dataset.judgments:
            self.assertGreaterEqual(judgment.grade, 0)
            self.assertLessEqual(judgment.grade, 3)

    def test_distractor_bases_never_carry_relevant_grades(self) -> None:
        for judgment in self.dataset.judgments:
            if set(judgment.bases) & {
                JudgmentBasis.NEAR_NEIGHBOR_DISTRACTOR,
                JudgmentBasis.JURISDICTION_MISMATCH_DISTRACTOR,
                JudgmentBasis.SUPERSEDED_DISTRACTOR,
            }:
                self.assertLessEqual(judgment.grade, 1)

    def test_relevant_documents_resolve_for_every_answerable_query(self) -> None:
        for query in self.dataset.queries:
            if query.expected_answer_state is ExpectedAnswerState.ANSWERABLE:
                self.assertTrue(relevant_document_ids(self.dataset, query.query_id))

    def test_privileged_document_is_never_relevant_without_authorization(self) -> None:
        privileged = {
            document.document_id
            for document in self.dataset.documents
            if document.privileged
        }
        for judgment in self.dataset.judgments:
            query = next(
                item
                for item in self.dataset.queries
                if item.query_id == judgment.query_id
            )
            if judgment.document_id in privileged and not query.privilege_authorized:
                self.assertEqual(0, judgment.grade)
                self.assertIn(JudgmentBasis.PRIVILEGE_BLOCKED, judgment.bases)

    def test_evidence_and_authority_documents_both_exist(self) -> None:
        kinds = {document.document_kind for document in self.dataset.documents}
        self.assertIn(EvalDocumentKind.AUTHORITY, kinds)
        self.assertIn(EvalDocumentKind.EVIDENCE_ITEM, kinds)


class TextHelperTests(unittest.TestCase):
    """Deterministic text helpers used by the leakage invariants."""

    def test_tokenize_is_lowercase_and_word_bounded(self) -> None:
        self.assertEqual(
            ("the", "quick", "brown"),
            tokenize("The QUICK, brown!"),
        )

    def test_longest_common_word_run_counts_contiguous_matches(self) -> None:
        self.assertEqual(
            2,
            longest_common_word_run(
                "alpha bravo charlie delta", "zero bravo charlie echo"
            ),
        )
        self.assertEqual(
            0,
            longest_common_word_run("alpha bravo", "charlie delta"),
        )

    def test_distinct_overlap_counts_unique_shared_terms(self) -> None:
        self.assertEqual(
            2,
            distinct_query_term_overlap("red blue green", "blue red orange"),
        )


class RecordShapeTests(unittest.TestCase):
    """Invalid records are rejected at validation time."""

    def test_superseded_authority_requires_a_successor(self) -> None:
        with self.assertRaises(ValueError):
            TypeAdapter(EvalDocument).validate_json(
                json.dumps(
                    {
                        "document_id": "QA-DOC-9999",
                        "tenant_id": str(TENANT_A),
                        "matter_id": str(TENANT_B),
                        "document_kind": "authority",
                        "title": "fixture",
                        "content": "fixture content for validation",
                        "jurisdiction": "us-il",
                        "classification": "fixture-public",
                        "fixture_only": True,
                        "authority_kind": "statute",
                        "authority_status": "superseded",
                    }
                )
            )

    def test_privileged_document_requires_a_label(self) -> None:
        with self.assertRaises(ValueError):
            TypeAdapter(EvalDocument).validate_json(
                json.dumps(
                    {
                        "document_id": "QA-DOC-9999",
                        "tenant_id": str(TENANT_A),
                        "matter_id": str(TENANT_B),
                        "document_kind": "evidence_item",
                        "title": "fixture",
                        "content": "fixture content for validation",
                        "jurisdiction": "us-il",
                        "classification": "fixture-privileged",
                        "privileged": True,
                        "fixture_only": True,
                    }
                )
            )

    def test_exact_citation_locator_must_appear_in_the_query(self) -> None:
        with self.assertRaises(ValueError):
            TypeAdapter(EvalQuery).validate_json(
                json.dumps(
                    {
                        "query_id": "QA-Q-9999",
                        "case_class": "exact_citation",
                        "query_text": "an unrelated question about leases",
                        "tenant_id": str(TENANT_A),
                        "matter_id": str(TENANT_B),
                        "expected_answer_state": "answerable",
                        "fixture_only": True,
                        "rationale": "fixture",
                        "exact_locator": "815 ILCS 5/2L",
                    }
                )
            )

    def test_unauthorized_privilege_query_cannot_expect_an_answer(self) -> None:
        with self.assertRaises(ValueError):
            TypeAdapter(EvalQuery).validate_json(
                json.dumps(
                    {
                        "query_id": "QA-Q-9999",
                        "case_class": "privilege_partition",
                        "query_text": "what does the privileged memo conclude here",
                        "tenant_id": str(TENANT_A),
                        "matter_id": str(TENANT_B),
                        "expected_answer_state": "answerable",
                        "fixture_only": True,
                        "rationale": "fixture",
                    }
                )
            )


if __name__ == "__main__":
    unittest.main()
