"""Frozen retrieval evaluation dataset values, loader, and leakage invariants.

This module implements the SKL-S3-04A slice of SKL-S3-04 under the
owner-approved amendment AMENDMENT-SKL-S2-10. It defines the typed frozen
query set, the synthetic fixture-only corpus documents, the graded relevance
judgments, the hash-frozen manifest, and the machine-checked leakage
invariants for the ten required case classes:

- exact citation
- paraphrase
- near-neighbor distinction
- jurisdiction mismatch
- superseded authority
- evidence versus authority
- OCR noise
- no answer
- privilege partition
- prompt injection

No model comparison runs in this slice. Every record is fixture-only
synthetic content; no HammerTime path, protected matter content, or
production identifier may appear in a frozen dataset.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from sklegal_retrieval.errors import RetrievalIntegrityError
from sklegal_retrieval.models import OpaqueId, RetrievalValue, SafeVersion, Sha256

DATASET_SCHEMA = "sklegal-retrieval-eval-frozen/v1"
DATASET_VERSION = "1.0.0"
DOCUMENTS_FILE = "documents.json"
QUERIES_FILE = "queries.json"
JUDGMENTS_FILE = "judgments.json"
MANIFEST_FILE = "manifest.json"

#: Grades at or above this threshold count as relevant for Recall-style
#: metrics in the later comparison slices.
RELEVANT_GRADE_THRESHOLD = 2

#: A paraphrase query that shares a longer contiguous word run with its
#: relevant document content is a copy, not a paraphrase, and would leak the
#: answer into the query text.
MAX_PARAPHRASE_COMMON_WORD_RUN = 3

#: A near-neighbor distractor must still share enough query vocabulary to
#: tempt a lexical ranker, both as an absolute floor and relative to the
#: relevant document overlap (integer tenths, no floating point).
MIN_NEAR_NEIGHBOR_DISTRACTOR_OVERLAP = 4

#: A document-borne injection payload that reached a query text would mean
#: the untrusted corpus content escaped its data-only lane.
MIN_NO_ANSWER_NEGATIVE_JUDGMENTS = 2

_NON_WORD = re.compile(r"[^a-z0-9]+")
_ISOC_TIMESTAMP = StringConstraints(
    strip_whitespace=True, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
)
IsoTimestamp = Annotated[str, _ISOC_TIMESTAMP]


class EvaluationDatasetError(RetrievalIntegrityError):
    """A frozen evaluation dataset failed integrity or invariant checks."""


class EvalCaseClass(StrEnum):
    EXACT_CITATION = "exact_citation"
    PARAPHRASE = "paraphrase"
    NEAR_NEIGHBOR_DISTINCTION = "near_neighbor_distinction"
    JURISDICTION_MISMATCH = "jurisdiction_mismatch"
    SUPERSEDED_AUTHORITY = "superseded_authority"
    EVIDENCE_VERSUS_AUTHORITY = "evidence_versus_authority"
    OCR_NOISE = "ocr_noise"
    NO_ANSWER = "no_answer"
    PRIVILEGE_PARTITION = "privilege_partition"
    PROMPT_INJECTION = "prompt_injection"


class EvalDocumentKind(StrEnum):
    AUTHORITY = "authority"
    EVIDENCE_ITEM = "evidence_item"


class AuthorityKind(StrEnum):
    STATUTE = "statute"
    CASE_DECISION = "case_decision"
    REGULATION = "regulation"


class AuthorityStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"


class ExpectedAnswerState(StrEnum):
    ANSWERABLE = "answerable"
    NO_ANSWER = "no_answer"
    BLOCKED = "blocked"


class PromptInjectionOrigin(StrEnum):
    DOCUMENT_BORNE = "document_borne"
    QUERY_BORNE = "query_borne"


class JudgmentBasis(StrEnum):
    EXACT_CITATION_MATCH = "exact_citation_match"
    PARAPHRASE_SUPPORT = "paraphrase_support"
    RELEVANT_SUPPORT = "relevant_support"
    BACKGROUND = "background"
    EVIDENCE_SUPPORTS_FACT = "evidence_supports_fact"
    AUTHORITY_NOT_FACT_SOURCE = "authority_not_fact_source"
    OCR_NOISE_TOLERANT = "ocr_noise_tolerant"
    PRIVILEGED_RELEVANT = "privileged_relevant"
    PRIVILEGE_BLOCKED = "privilege_blocked"
    INJECTION_UNTRUSTED_PAYLOAD = "injection_untrusted_payload"
    NEAR_NEIGHBOR_DISTRACTOR = "near_neighbor_distractor"
    JURISDICTION_MISMATCH_DISTRACTOR = "jurisdiction_mismatch_distractor"
    SUPERSEDED_DISTRACTOR = "superseded_distractor"
    NEGATIVE_IRRELEVANT = "negative_irrelevant"
    NEGATIVE_NO_ANSWER = "negative_no_answer"


#: Bases that can justify a grade 3 directly responsive judgment.
POSITIVE_BASES = frozenset(
    {
        JudgmentBasis.EXACT_CITATION_MATCH,
        JudgmentBasis.PARAPHRASE_SUPPORT,
        JudgmentBasis.RELEVANT_SUPPORT,
        JudgmentBasis.EVIDENCE_SUPPORTS_FACT,
        JudgmentBasis.OCR_NOISE_TOLERANT,
        JudgmentBasis.PRIVILEGED_RELEVANT,
        JudgmentBasis.INJECTION_UNTRUSTED_PAYLOAD,
    }
)

#: Bases that mark a judgment as a deliberate distractor. Distractors can
#: never carry a relevant grade.
DISTRACTOR_BASES = frozenset(
    {
        JudgmentBasis.NEAR_NEIGHBOR_DISTRACTOR,
        JudgmentBasis.JURISDICTION_MISMATCH_DISTRACTOR,
        JudgmentBasis.SUPERSEDED_DISTRACTOR,
    }
)

#: Grade semantics used by every judgment in the frozen set.
GRADE_SEMANTICS = (
    "0 not relevant",
    "1 background only",
    "2 relevant support",
    "3 directly responsive or controlling",
)


class EvalDocument(RetrievalValue):
    """One synthetic fixture corpus document with partition and authority pins."""

    document_id: OpaqueId
    tenant_id: UUID
    matter_id: UUID
    document_kind: EvalDocumentKind
    title: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1, max_length=65536)
    jurisdiction: str = Field(
        min_length=2, max_length=32, pattern=r"^[a-z]{2}(-[a-z0-9]+)*$"
    )
    classification: str = Field(
        min_length=1, max_length=128, pattern=r"^fixture-[a-z0-9-]+$"
    )
    privileged: bool = False
    privilege_label: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^fixture-[a-z0-9-]+$"
    )
    contains_prompt_injection: bool = False
    injection_payload: str | None = Field(default=None, min_length=8, max_length=2048)
    ocr_derived: bool = False
    authority_kind: AuthorityKind | None = None
    authority_status: AuthorityStatus | None = None
    superseded_by: OpaqueId | None = None
    fixture_only: Literal[True] = True

    @model_validator(mode="after")
    def validate_document_shape(self) -> Self:
        authority = self.document_kind is EvalDocumentKind.AUTHORITY
        if authority != (self.authority_kind is not None):
            raise ValueError("authority documents require exactly one authority kind")
        if authority != (self.authority_status is not None):
            raise ValueError("authority documents require exactly one authority status")
        superseded = self.authority_status is AuthorityStatus.SUPERSEDED
        if superseded != (self.superseded_by is not None):
            raise ValueError("superseded authority requires its superseding document")
        if self.superseded_by is not None and self.superseded_by == self.document_id:
            raise ValueError("an authority cannot supersede itself")
        if self.privileged != (self.privilege_label is not None):
            raise ValueError("privileged documents require exactly one privilege label")
        injection = self.contains_prompt_injection
        if injection != (self.injection_payload is not None):
            raise ValueError(
                "documents carrying an injection require the exact payload record"
            )
        return self


class EvalQuery(RetrievalValue):
    """One frozen evaluation query pinned to one Tenant and Matter partition."""

    query_id: OpaqueId
    case_class: EvalCaseClass
    query_text: str = Field(min_length=8, max_length=4096)
    tenant_id: UUID
    matter_id: UUID
    expected_answer_state: ExpectedAnswerState
    privilege_authorized: bool = False
    query_jurisdiction: str | None = Field(
        default=None, min_length=2, max_length=32, pattern=r"^[a-z]{2}(-[a-z0-9]+)*$"
    )
    exact_locator: str | None = Field(
        default=None,
        min_length=3,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9 ./:-]*$",
    )
    injection_origin: PromptInjectionOrigin | None = None
    rationale: str = Field(min_length=1, max_length=2048)
    fixture_only: Literal[True] = True

    @model_validator(mode="after")
    def validate_query_shape(self) -> Self:
        no_answer = self.case_class is EvalCaseClass.NO_ANSWER
        expected_no_answer = self.expected_answer_state is ExpectedAnswerState.NO_ANSWER
        if no_answer != expected_no_answer:
            raise ValueError(
                "the no-answer case class and expected answer state must agree"
            )
        mismatch = self.case_class is EvalCaseClass.JURISDICTION_MISMATCH
        if mismatch != (self.query_jurisdiction is not None):
            raise ValueError(
                "jurisdiction mismatch queries pin one controlling jurisdiction"
            )
        exact = self.case_class is EvalCaseClass.EXACT_CITATION
        if exact != (self.exact_locator is not None):
            raise ValueError("exact citation queries pin one exact locator")
        if exact and self.exact_locator not in self.query_text:
            raise ValueError("the exact locator must appear in the query text")
        injection = self.case_class is EvalCaseClass.PROMPT_INJECTION
        if injection != (self.injection_origin is not None):
            raise ValueError("prompt injection queries pin one injection origin")
        if self.expected_answer_state is ExpectedAnswerState.BLOCKED and (
            self.privilege_authorized
            or self.case_class
            not in {EvalCaseClass.PRIVILEGE_PARTITION, EvalCaseClass.PROMPT_INJECTION}
        ):
            raise ValueError(
                "the blocked answer state requires an unauthorized privilege or"
                " injection case"
            )
        if (
            self.expected_answer_state is ExpectedAnswerState.ANSWERABLE
            and self.case_class is EvalCaseClass.PRIVILEGE_PARTITION
            and not self.privilege_authorized
        ):
            raise ValueError(
                "an unauthorized privilege partition query cannot expect an answer"
            )
        return self


class RelevanceJudgment(RetrievalValue):
    """One graded relevance decision with machine-checkable bases."""

    query_id: OpaqueId
    document_id: OpaqueId
    grade: int = Field(ge=0, le=3)
    bases: tuple[JudgmentBasis, ...] = Field(min_length=1, max_length=4)
    rationale: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def validate_bases(self) -> Self:
        if len(self.bases) != len(set(self.bases)):
            raise ValueError("judgment bases must be unique")
        return self


class DatasetManifest(RetrievalValue):
    """Hash-freeze manifest pinning the frozen dataset files."""

    model_config = ConfigDict(populate_by_name=True)

    manifest_schema: Literal[DATASET_SCHEMA] = Field(
        default=DATASET_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    card: str = Field(min_length=1, max_length=64)
    task: str = Field(min_length=1, max_length=64)
    parent_task: str = Field(min_length=1, max_length=64)
    amendment: str = Field(min_length=1, max_length=64)
    dataset_version: SafeVersion = DATASET_VERSION
    frozen_at: IsoTimestamp
    paired_document: str = Field(min_length=1, max_length=512)
    documents_sha256: Sha256
    queries_sha256: Sha256
    judgments_sha256: Sha256
    freeze_sha256: Sha256
    synthetic_tenant_ids: tuple[UUID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_tenants(self) -> Self:
        if len(self.synthetic_tenant_ids) != len(set(self.synthetic_tenant_ids)):
            raise ValueError("synthetic tenant identifiers must be unique")
        return self


class FrozenEvaluationDataset(RetrievalValue):
    """Loaded frozen dataset with reference and ordering integrity."""

    manifest: DatasetManifest
    documents: tuple[EvalDocument, ...] = Field(min_length=1)
    queries: tuple[EvalQuery, ...] = Field(min_length=1)
    judgments: tuple[RelevanceJudgment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references_and_order(self) -> Self:
        document_ids = [document.document_id for document in self.documents]
        query_ids = [query.query_id for query in self.queries]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("document identifiers must be unique")
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("query identifiers must be unique")
        if document_ids != sorted(document_ids):
            raise ValueError("documents must be stored in sorted identifier order")
        if query_ids != sorted(query_ids):
            raise ValueError("queries must be stored in sorted identifier order")
        keys = [
            (judgment.query_id, judgment.document_id) for judgment in self.judgments
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("each query and document pair carries one judgment")
        if keys != sorted(keys):
            raise ValueError("judgments must be stored in sorted pair order")
        known_documents = set(document_ids)
        known_queries = set(query_ids)
        for judgment in self.judgments:
            if judgment.query_id not in known_queries:
                raise ValueError("judgment references an unknown query")
            if judgment.document_id not in known_documents:
                raise ValueError("judgment references an unknown document")
        for document in self.documents:
            if (
                document.superseded_by is not None
                and document.superseded_by not in known_documents
            ):
                raise ValueError("superseding authority is missing from the dataset")
        return self


def tokenize(text: str) -> tuple[str, ...]:
    """Lowercase word tokens used by the copy and overlap leakage checks."""

    return tuple(token for token in _NON_WORD.split(text.lower()) if token)


def longest_common_word_run(first: str, second: str) -> int:
    """Length of the longest contiguous shared word run between two texts."""

    left = tokenize(first)
    right = tokenize(second)
    right_positions: dict[str, list[int]] = {}
    for position, token in enumerate(right):
        right_positions.setdefault(token, []).append(position)
    best = 0
    for start, token in enumerate(left):
        for offset in right_positions.get(token, ()):
            length = 0
            while (
                start + length < len(left)
                and offset + length < len(right)
                and left[start + length] == right[offset + length]
            ):
                length += 1
            best = max(best, length)
    return best


def distinct_query_term_overlap(query_text: str, content: str) -> int:
    """Count distinct query terms that appear in the document content."""

    query_terms = {token for token in tokenize(query_text) if len(token) > 2}
    content_terms = set(tokenize(content))
    return len(query_terms & content_terms)


def compute_file_sha256(path: Path) -> str:
    """SHA-256 of one frozen dataset file's exact bytes."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_freeze_sha256(
    *,
    schema: str,
    dataset_version: str,
    documents_sha256: str,
    queries_sha256: str,
    judgments_sha256: str,
) -> str:
    """Deterministic freeze hash binding schema, version, and file hashes."""

    payload = "\n".join(
        (
            f"schema:{schema}",
            f"version:{dataset_version}",
            f"documents:{documents_sha256}",
            f"queries:{queries_sha256}",
            f"judgments:{judgments_sha256}",
        )
    )
    payload += "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _forbidden_reference(value: str) -> bool:
    lowered = value.lower()
    return "hammertime" in lowered or "inbox/" in lowered


def dataset_invariant_violations(dataset: FrozenEvaluationDataset) -> tuple[str, ...]:
    """Return every leakage or shape violation in the dataset.

    The returned tuple is deterministic and empty exactly when the dataset
    satisfies every frozen-dataset invariant. The later comparison slices and
    the test suite treat a non-empty result as a hard failure.
    """

    violations: list[str] = []
    documents = {document.document_id: document for document in dataset.documents}
    queries = {query.query_id: query for query in dataset.queries}
    by_query: dict[str, list[RelevanceJudgment]] = {
        query_id: [] for query_id in queries
    }
    for judgment in dataset.judgments:
        by_query[judgment.query_id].append(judgment)

    def add(code: str, detail: str) -> None:
        violations.append(f"{code}: {detail}")

    # L01 required case-class coverage.
    classes_present = {query.case_class for query in dataset.queries}
    for case_class in EvalCaseClass:
        if case_class not in classes_present:
            add("L01", f"case class {case_class.value} has no query")

    # L02 every query carries at least one judgment.
    for query_id, judgments in by_query.items():
        if not judgments:
            add("L02", f"query {query_id} has no judgments")

    # L03 cross-partition leakage: judgments never cross Tenant or Matter.
    for judgment in dataset.judgments:
        query = queries[judgment.query_id]
        document = documents[judgment.document_id]
        if query.tenant_id != document.tenant_id:
            add(
                "L03",
                f"judgment {judgment.query_id}/{judgment.document_id} crosses tenants",
            )
        if query.matter_id != document.matter_id:
            add(
                "L03",
                f"judgment {judgment.query_id}/{judgment.document_id} crosses matters",
            )

    # L04 privileged documents never earn a relevant grade without authorization.
    for judgment in dataset.judgments:
        query = queries[judgment.query_id]
        document = documents[judgment.document_id]
        if (
            document.privileged
            and judgment.grade >= 1
            and not query.privilege_authorized
        ):
            add(
                "L04",
                f"judgment {judgment.query_id}/{judgment.document_id} leaks privilege",
            )

    # L05 every privileged document is relevant inside its authorized partition.
    privileged_ids = {
        document.document_id for document in dataset.documents if document.privileged
    }
    for document_id in sorted(privileged_ids):
        authorized = any(
            judgment.grade >= 1 and queries[judgment.query_id].privilege_authorized
            for judgments in by_query.values()
            for judgment in judgments
            if judgment.document_id == document_id
        )
        if not authorized:
            add("L05", f"privileged document {document_id} has no authorized judgment")

    # L06 blocked privilege judgments are explicit and stay at grade zero.
    for judgment in dataset.judgments:
        query = queries[judgment.query_id]
        document = documents[judgment.document_id]
        if document.privileged and not query.privilege_authorized:
            if judgment.grade != 0:
                add(
                    "L06",
                    f"judgment {judgment.query_id}/{judgment.document_id} is not blocked",
                )
            elif JudgmentBasis.PRIVILEGE_BLOCKED not in judgment.bases:
                add(
                    "L06",
                    f"judgment {judgment.query_id}/{judgment.document_id} lacks the block basis",
                )

    # L07 no-answer queries carry only explicit negatives.
    for query in dataset.queries:
        if query.expected_answer_state is not ExpectedAnswerState.NO_ANSWER:
            continue
        judgments = by_query[query.query_id]
        negatives = [
            judgment
            for judgment in judgments
            if judgment.grade == 0
            and JudgmentBasis.NEGATIVE_NO_ANSWER in judgment.bases
        ]
        if len(judgments) != len(negatives):
            add("L07", f"no-answer query {query.query_id} has a non-negative judgment")
        if len(negatives) < MIN_NO_ANSWER_NEGATIVE_JUDGMENTS:
            add(
                "L07",
                f"no-answer query {query.query_id} has fewer than"
                f" {MIN_NO_ANSWER_NEGATIVE_JUDGMENTS} explicit negatives",
            )

    # L08 answerable queries have at least one relevant document.
    for query in dataset.queries:
        if query.expected_answer_state is not ExpectedAnswerState.ANSWERABLE:
            continue
        if not any(
            judgment.grade >= RELEVANT_GRADE_THRESHOLD
            for judgment in by_query[query.query_id]
        ):
            add("L08", f"answerable query {query.query_id} has no relevant judgment")

    # L09 paraphrase queries must not copy their relevant document text.
    for query in dataset.queries:
        if query.case_class is not EvalCaseClass.PARAPHRASE:
            continue
        for judgment in by_query[query.query_id]:
            if judgment.grade < RELEVANT_GRADE_THRESHOLD:
                continue
            run = longest_common_word_run(
                query.query_text, documents[judgment.document_id].content
            )
            if run > MAX_PARAPHRASE_COMMON_WORD_RUN:
                add(
                    "L09",
                    f"paraphrase query {query.query_id} shares a {run}-word run with"
                    f" {judgment.document_id}",
                )

    # L10 exact citation queries resolve to exactly one controlling locator hit.
    for query in dataset.queries:
        if query.case_class is not EvalCaseClass.EXACT_CITATION:
            continue
        if query.exact_locator is None:
            add("L10", f"exact citation query {query.query_id} lost its locator")
            continue
        controlling = [
            judgment
            for judgment in by_query[query.query_id]
            if judgment.grade == 3
            and query.exact_locator in documents[judgment.document_id].content
        ]
        if len(controlling) != 1:
            add(
                "L10",
                f"exact citation query {query.query_id} has {len(controlling)}"
                " controlling locator hits",
            )
        elif JudgmentBasis.EXACT_CITATION_MATCH not in controlling[0].bases:
            add(
                "L10",
                f"exact citation query {query.query_id} lacks the citation basis",
            )

    # L11 near-neighbor distractors overlap at least the relevant document.
    for query in dataset.queries:
        if query.case_class is not EvalCaseClass.NEAR_NEIGHBOR_DISTINCTION:
            continue
        judgments = by_query[query.query_id]
        relevant = [
            judgment
            for judgment in judgments
            if judgment.grade >= RELEVANT_GRADE_THRESHOLD
        ]
        distractors = [
            judgment
            for judgment in judgments
            if JudgmentBasis.NEAR_NEIGHBOR_DISTRACTOR in judgment.bases
        ]
        if not relevant or not distractors:
            add(
                "L11",
                f"near-neighbor query {query.query_id} lacks a distractor or a relevant hit",
            )
            continue
        best_relevant = max(judgment.grade for judgment in relevant)
        relevant_overlap = max(
            distinct_query_term_overlap(
                query.query_text, documents[judgment.document_id].content
            )
            for judgment in relevant
        )
        for distractor in distractors:
            overlap = distinct_query_term_overlap(
                query.query_text, documents[distractor.document_id].content
            )
            if distractor.grade >= best_relevant:
                add(
                    "L11",
                    f"near-neighbor distractor {query.query_id}/{distractor.document_id}"
                    " is not ranked below the relevant document",
                )
            if overlap < MIN_NEAR_NEIGHBOR_DISTRACTOR_OVERLAP:
                add(
                    "L11",
                    f"near-neighbor distractor {query.query_id}/{distractor.document_id}"
                    f" overlaps only {overlap} query terms",
                )
            if overlap * 10 < relevant_overlap * 6:
                add(
                    "L11",
                    f"near-neighbor distractor {query.query_id}/{distractor.document_id}"
                    f" overlaps {overlap} of {relevant_overlap} relevant terms",
                )

    # L12 superseded authority stays background and its successor answers.
    for query in dataset.queries:
        if query.case_class is not EvalCaseClass.SUPERSEDED_AUTHORITY:
            continue
        judgments = by_query[query.query_id]
        superseded = [
            judgment
            for judgment in judgments
            if documents[judgment.document_id].authority_status
            is AuthorityStatus.SUPERSEDED
        ]
        if not superseded:
            add(
                "L12",
                f"superseded query {query.query_id} judges no superseded authority",
            )
        for judgment in superseded:
            if (
                judgment.grade > 1
                or JudgmentBasis.SUPERSEDED_DISTRACTOR not in judgment.bases
            ):
                add(
                    "L12",
                    f"superseded judgment {query.query_id}/{judgment.document_id}"
                    " is not marked as background",
                )
            successor = documents[judgment.document_id].superseded_by
            if successor is None:
                add(
                    "L12",
                    f"superseded document {judgment.document_id} lost its successor pin",
                )
                continue
            successor_document = documents[successor]
            if successor_document.authority_status is not AuthorityStatus.CURRENT:
                add("L12", f"successor {successor} is not current authority")
            elif (
                successor_document.jurisdiction
                != documents[judgment.document_id].jurisdiction
            ):
                add("L12", f"successor {successor} changes jurisdiction")
            elif not any(
                item.document_id == successor and item.grade >= RELEVANT_GRADE_THRESHOLD
                for item in judgments
            ):
                add(
                    "L12",
                    f"superseded query {query.query_id} does not retrieve successor"
                    f" {successor}",
                )

    # L13 jurisdiction mismatch distractors are foreign and stay background.
    for query in dataset.queries:
        if query.case_class is not EvalCaseClass.JURISDICTION_MISMATCH:
            continue
        if query.query_jurisdiction is None:
            add("L13", f"jurisdiction query {query.query_id} lost its jurisdiction")
            continue
        judgments = by_query[query.query_id]
        mismatched = [
            judgment
            for judgment in judgments
            if JudgmentBasis.JURISDICTION_MISMATCH_DISTRACTOR in judgment.bases
        ]
        if not mismatched:
            add(
                "L13",
                f"jurisdiction query {query.query_id} has no mismatch distractor",
            )
        for judgment in mismatched:
            document = documents[judgment.document_id]
            if document.jurisdiction == query.query_jurisdiction:
                add(
                    "L13",
                    f"mismatch distractor {query.query_id}/{judgment.document_id}"
                    " is not foreign",
                )
            if judgment.grade > 1:
                add(
                    "L13",
                    f"mismatch distractor {query.query_id}/{judgment.document_id}"
                    " is not background",
                )
        if not any(
            judgment.grade >= RELEVANT_GRADE_THRESHOLD
            and documents[judgment.document_id].jurisdiction == query.query_jurisdiction
            for judgment in judgments
        ):
            add(
                "L13",
                f"jurisdiction query {query.query_id} has no controlling-jurisdiction hit",
            )

    # L14 evidence versus authority separation holds inside each query.
    for query in dataset.queries:
        if query.case_class is not EvalCaseClass.EVIDENCE_VERSUS_AUTHORITY:
            continue
        judgments = by_query[query.query_id]
        relevant_evidence = [
            judgment
            for judgment in judgments
            if judgment.grade >= RELEVANT_GRADE_THRESHOLD
            and documents[judgment.document_id].document_kind
            is EvalDocumentKind.EVIDENCE_ITEM
        ]
        relevant_authority = [
            judgment
            for judgment in judgments
            if judgment.grade >= RELEVANT_GRADE_THRESHOLD
            and documents[judgment.document_id].document_kind
            is EvalDocumentKind.AUTHORITY
        ]
        if relevant_evidence:
            marked = [
                judgment
                for judgment in judgments
                if documents[judgment.document_id].document_kind
                is EvalDocumentKind.AUTHORITY
                and judgment.grade <= 1
                and JudgmentBasis.AUTHORITY_NOT_FACT_SOURCE in judgment.bases
            ]
            if not marked:
                add(
                    "L14",
                    f"evidence query {query.query_id} lacks an authority non-source judgment",
                )
        if relevant_authority:
            background_evidence = [
                judgment
                for judgment in judgments
                if documents[judgment.document_id].document_kind
                is EvalDocumentKind.EVIDENCE_ITEM
                and judgment.grade <= 1
            ]
            if not background_evidence:
                add(
                    "L14",
                    f"authority query {query.query_id} lacks a background evidence judgment",
                )

    # L15 the class covers both fact-seeking and law-seeking directions.
    evidence_direction = False
    authority_direction = False
    for query in dataset.queries:
        if query.case_class is not EvalCaseClass.EVIDENCE_VERSUS_AUTHORITY:
            continue
        for judgment in by_query[query.query_id]:
            if judgment.grade < RELEVANT_GRADE_THRESHOLD:
                continue
            kind = documents[judgment.document_id].document_kind
            if kind is EvalDocumentKind.EVIDENCE_ITEM:
                evidence_direction = True
            if kind is EvalDocumentKind.AUTHORITY:
                authority_direction = True
    if not evidence_direction or not authority_direction:
        add("L15", "evidence versus authority class misses one direction")

    # L16 OCR-noise hits tolerate degraded text without copying the query.
    for query in dataset.queries:
        if query.case_class is not EvalCaseClass.OCR_NOISE:
            continue
        hits = [
            judgment
            for judgment in by_query[query.query_id]
            if judgment.grade >= RELEVANT_GRADE_THRESHOLD
            and documents[judgment.document_id].ocr_derived
            and JudgmentBasis.OCR_NOISE_TOLERANT in judgment.bases
        ]
        if not hits:
            add("L16", f"OCR query {query.query_id} has no noise-tolerant hit")
        for judgment in hits:
            content = documents[judgment.document_id].content
            if query.query_text.strip().lower() in content.lower():
                add(
                    "L16",
                    f"OCR query {query.query_id} text appears verbatim in"
                    f" {judgment.document_id}",
                )

    # L17 document-borne injection hits stay relevance-graded as data.
    for query in dataset.queries:
        if query.case_class is not EvalCaseClass.PROMPT_INJECTION:
            continue
        if query.injection_origin is not PromptInjectionOrigin.DOCUMENT_BORNE:
            continue
        if not any(
            judgment.grade >= 1
            and documents[judgment.document_id].contains_prompt_injection
            and JudgmentBasis.INJECTION_UNTRUSTED_PAYLOAD in judgment.bases
            for judgment in by_query[query.query_id]
        ):
            add("L17", f"injection query {query.query_id} has no untrusted-payload hit")

    # L18 no corpus injection payload reaches any query text.
    payloads = [
        document.injection_payload
        for document in dataset.documents
        if document.injection_payload is not None
    ]
    for query in dataset.queries:
        for payload in payloads:
            if payload.lower() in query.query_text.lower():
                add("L18", f"query {query.query_id} carries a corpus injection payload")

    # L19 query-borne injection never escalates privilege.
    for query in dataset.queries:
        if (
            query.case_class is EvalCaseClass.PROMPT_INJECTION
            and query.injection_origin is PromptInjectionOrigin.QUERY_BORNE
        ):
            for judgment in by_query[query.query_id]:
                document = documents[judgment.document_id]
                if document.privileged and (
                    judgment.grade != 0
                    or JudgmentBasis.PRIVILEGE_BLOCKED not in judgment.bases
                ):
                    add(
                        "L19",
                        f"injection query {query.query_id} escalates privilege through"
                        f" {judgment.document_id}",
                    )

    # L20 distractor bases never carry a relevant grade.
    for judgment in dataset.judgments:
        if set(judgment.bases) & DISTRACTOR_BASES and judgment.grade > 1:
            add(
                "L20",
                f"judgment {judgment.query_id}/{judgment.document_id} grades a distractor",
            )

    # L21 grade three judgments need a directly responsive basis.
    for judgment in dataset.judgments:
        if judgment.grade == 3 and not set(judgment.bases) & POSITIVE_BASES:
            add(
                "L21",
                f"judgment {judgment.query_id}/{judgment.document_id} lacks a positive basis",
            )

    # L22 fixture hygiene: synthetic tenants, classifications, no HammerTime.
    synthetic_tenants = set(dataset.manifest.synthetic_tenant_ids)
    for document in dataset.documents:
        if document.tenant_id not in synthetic_tenants:
            add("L22", f"document {document.document_id} uses a non-synthetic tenant")
        if _forbidden_reference(
            f"{document.title} {document.content} {document.classification}"
            f" {document.injection_payload or ''}"
        ):
            add("L22", f"document {document.document_id} references a forbidden path")
    for query in dataset.queries:
        if query.tenant_id not in synthetic_tenants:
            add("L22", f"query {query.query_id} uses a non-synthetic tenant")
        if _forbidden_reference(f"{query.query_text} {query.rationale}"):
            add("L22", f"query {query.query_id} references a forbidden path")
    for judgment in dataset.judgments:
        if _forbidden_reference(judgment.rationale):
            add("L22", f"judgment {judgment.query_id} references a forbidden path")

    # L23 the no-answer basis belongs only to no-answer queries.
    for judgment in dataset.judgments:
        query = queries[judgment.query_id]
        if (
            JudgmentBasis.NEGATIVE_NO_ANSWER in judgment.bases
            and query.expected_answer_state is not ExpectedAnswerState.NO_ANSWER
        ):
            add(
                "L23",
                f"judgment {judgment.query_id}/{judgment.document_id} misuses the"
                " no-answer basis",
            )

    # L24 blocked queries never carry a relevant judgment.
    for query in dataset.queries:
        if query.expected_answer_state is not ExpectedAnswerState.BLOCKED:
            continue
        judgments = by_query[query.query_id]
        if not judgments:
            add("L24", f"blocked query {query.query_id} has no judgments")
        for judgment in judgments:
            document = documents[judgment.document_id]
            if judgment.grade >= RELEVANT_GRADE_THRESHOLD:
                add(
                    "L24",
                    f"blocked query {query.query_id} retrieves {judgment.document_id}",
                )
            if document.privileged and (
                judgment.grade != 0
                or JudgmentBasis.PRIVILEGE_BLOCKED not in judgment.bases
            ):
                add(
                    "L24",
                    f"blocked query {query.query_id} does not block"
                    f" {judgment.document_id}",
                )

    return tuple(violations)


def load_frozen_dataset(root: Path) -> FrozenEvaluationDataset:
    """Load, hash-verify, and invariant-check one frozen dataset directory."""

    root = Path(root)
    try:
        documents_bytes = (root / DOCUMENTS_FILE).read_bytes()
        queries_bytes = (root / QUERIES_FILE).read_bytes()
        judgments_bytes = (root / JUDGMENTS_FILE).read_bytes()
        manifest_bytes = (root / MANIFEST_FILE).read_bytes()
    except OSError as error:
        raise EvaluationDatasetError(
            f"unable to read the frozen dataset files under {root}"
        ) from error

    try:
        manifest = TypeAdapter(DatasetManifest).validate_json(manifest_bytes)
        documents = TypeAdapter(list[EvalDocument]).validate_json(documents_bytes)
        queries = TypeAdapter(list[EvalQuery]).validate_json(queries_bytes)
        judgments = TypeAdapter(list[RelevanceJudgment]).validate_json(judgments_bytes)
    except ValueError as error:
        raise EvaluationDatasetError(
            f"frozen dataset records failed schema validation: {error}"
        ) from error

    documents_sha256 = hashlib.sha256(documents_bytes).hexdigest()
    queries_sha256 = hashlib.sha256(queries_bytes).hexdigest()
    judgments_sha256 = hashlib.sha256(judgments_bytes).hexdigest()
    expected = {
        DOCUMENTS_FILE: (manifest.documents_sha256, documents_sha256),
        QUERIES_FILE: (manifest.queries_sha256, queries_sha256),
        JUDGMENTS_FILE: (manifest.judgments_sha256, judgments_sha256),
    }
    for name, (pinned, computed) in expected.items():
        if pinned != computed:
            raise EvaluationDatasetError(
                f"frozen dataset file {name} does not match its pinned hash"
            )

    freeze_sha256 = compute_freeze_sha256(
        schema=manifest.manifest_schema,
        dataset_version=manifest.dataset_version,
        documents_sha256=documents_sha256,
        queries_sha256=queries_sha256,
        judgments_sha256=judgments_sha256,
    )
    if freeze_sha256 != manifest.freeze_sha256:
        raise EvaluationDatasetError(
            "frozen dataset freeze hash does not match the manifest"
        )

    dataset = FrozenEvaluationDataset(
        manifest=manifest,
        documents=tuple(documents),
        queries=tuple(queries),
        judgments=tuple(judgments),
    )
    violations = dataset_invariant_violations(dataset)
    if violations:
        raise EvaluationDatasetError(
            "frozen dataset invariant violations: " + "; ".join(violations[:5])
        )
    return dataset


def judgments_for_query(
    dataset: FrozenEvaluationDataset, query_id: str
) -> tuple[RelevanceJudgment, ...]:
    """Return the frozen judgments for one query in deterministic order."""

    return tuple(
        judgment for judgment in dataset.judgments if judgment.query_id == query_id
    )


def relevant_document_ids(
    dataset: FrozenEvaluationDataset, query_id: str
) -> tuple[str, ...]:
    """Return document identifiers judged relevant for one query."""

    return tuple(
        judgment.document_id
        for judgment in dataset.judgments
        if judgment.query_id == query_id and judgment.grade >= RELEVANT_GRADE_THRESHOLD
    )
