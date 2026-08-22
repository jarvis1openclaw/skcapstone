"""Shadow projection generations and the deterministic comparison harness.

This module implements the SKL-S3-04B slice of SKL-S3-04 under the
owner-approved amendment AMENDMENT-SKL-S2-10. It builds shadow vector
projection generations for two candidate embedding routes over the SKL-S3-04A
frozen evaluation dataset, executes every frozen query through the local
PostgreSQL retrieval plane adapter (the fixed
``sklegal_retrieval.vector_exact_v1`` call), and computes the deterministic
metric set: Recall@k, nDCG@k, MRR, citation accuracy, latency, and
cross-partition or privilege leakage.

The two candidate routes are pinned as logical embedding route identifiers:
the custom legal embedding route and the base BGE-M3 route. The committed
embedders are deterministic hash fixtures that stand in for the candidate
models so the harness itself stays deterministic, local, and repeatable.
Binding the real models behind the provider-neutral gateway is a later
qualification step and must not change any metric definition in this module.
Measurements produced by the fixture embedders never qualify, promote, or
demote any model; approval thresholds stay a human decision.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, model_validator

from .errors import RetrievalIntegrityError
from .evaluation_dataset import (
    RELEVANT_GRADE_THRESHOLD,
    EvalCaseClass,
    EvalDocument,
    EvalQuery,
    FrozenEvaluationDataset,
    tokenize,
)
from .fake import FakeProjectionSink
from .models import (
    DistanceMetric,
    OpaqueId,
    ProjectionPins,
    RetrievalComponent,
    RetrievalScope,
    RetrievalValue,
    SafeVersion,
    ScopeKind,
    Sha256,
    SourceProvenance,
    VectorPins,
)
from .postgres import (
    VECTOR_EXACT_STATEMENT,
    PostgresRetrievalAdapter,
)
from .projector import (
    OutboxEvent,
    OutboxProjector,
    ProjectedRow,
    project_rows_digest,
)
from .query_templates import bind_query_template

HARNESS_VERSION = "1.0.0"

#: Recall and nDCG depths reported by every run. Changing this tuple is a
#: harness revision and requires new tests and evidence.
K_VALUES: tuple[int, ...] = (1, 5, 10)

#: Result bound passed to the fixed vector template for every frozen query.
DEFAULT_MAX_RESULTS = 10

#: Dimension shared by the two fixture shadow routes.
FIXTURE_EMBEDDING_DIMENSION = 256

#: Marks measurements produced by the deterministic hash fixture embedders.
FIXTURE_EMBEDDER_KIND = "deterministic_hash_fixture"

type PartitionKey = tuple[UUID, UUID]
type Clock = Callable[[], float]

LEAKAGE_CROSS_PARTITION = "cross_partition"
LEAKAGE_PRIVILEGE_ESCALATION = "privilege_escalation"

_DEFAULT_GENERATION = 2
_DEFAULT_RELEASE_ID = "shadow-skl-s3-04b"
_DEFAULT_BACKEND_WATERMARK = 1


class ShadowComparisonError(RetrievalIntegrityError):
    """A shadow comparison generation, plane, or report failed its gates."""


class ShadowRouteLabel(StrEnum):
    CUSTOM_LEGAL = "custom_legal"
    BASE_BGE_M3 = "base_bge_m3"


class ShadowRoutePins(RetrievalValue):
    """One candidate embedding route pinned for shadow comparison."""

    label: ShadowRouteLabel
    vector: VectorPins
    seed: str = Field(min_length=8, max_length=128)
    embedder_kind: str = Field(min_length=1, max_length=64)


CUSTOM_LEGAL_ROUTE = ShadowRoutePins(
    label=ShadowRouteLabel.CUSTOM_LEGAL,
    vector=VectorPins(
        embedding_model_id="embedding.custom-legal.shadow",
        embedding_model_revision="fixture-v1",
        embedding_dimension=FIXTURE_EMBEDDING_DIMENSION,
        distance_metric=DistanceMetric.COSINE,
    ),
    seed="skl-s3-04b-custom-legal-fixture",
    embedder_kind=FIXTURE_EMBEDDER_KIND,
)

BASE_BGE_M3_ROUTE = ShadowRoutePins(
    label=ShadowRouteLabel.BASE_BGE_M3,
    vector=VectorPins(
        embedding_model_id="embedding.bge-m3-base.shadow",
        embedding_model_revision="fixture-v1",
        embedding_dimension=FIXTURE_EMBEDDING_DIMENSION,
        distance_metric=DistanceMetric.COSINE,
    ),
    seed="skl-s3-04b-bge-m3-base-fixture",
    embedder_kind=FIXTURE_EMBEDDER_KIND,
)


class ShadowEmbedder(Protocol):
    """Deterministic text-to-vector encoder pinned to one shadow route."""

    @property
    def pins(self) -> ShadowRoutePins: ...

    def embed(self, text: str) -> tuple[float, ...]: ...


class DeterministicHashEmbedder:
    """Seed-pinned hash embedder standing in for one candidate model.

    Every token hashes with the route seed into one dimension index and one
    signed weight in [-1, 1]. The accumulated vector is L2 normalized, so
    cosine distance is always well defined. The mapping depends only on the
    seed and the token sequence, which keeps shadow builds stable across runs
    and machines. Fixture stand-ins never measure a real model.
    """

    _DENOMINATOR = 9223372036854775808.0

    def __init__(self, pins: ShadowRoutePins) -> None:
        self._pins = pins

    @property
    def pins(self) -> ShadowRoutePins:
        return self._pins

    def embed(self, text: str) -> tuple[float, ...]:
        dimension = self._pins.vector.embedding_dimension
        seed = self._pins.seed
        vector = [0.0] * dimension
        for token in tokenize(text):
            digest = hashlib.sha256(f"{seed}:{token}".encode()).digest()
            index = int.from_bytes(digest[:8], "big") % dimension
            numerator = int.from_bytes(digest[8:16], "big")
            vector[index] += (numerator / self._DENOMINATOR) - 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            digest = hashlib.sha256(f"{seed}:anchor:{text}".encode()).digest()
            vector[int.from_bytes(digest[:8], "big") % dimension] += 1.0
            norm = math.sqrt(sum(value * value for value in vector))
        return tuple(value / norm for value in vector)


def cosine_distance(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine distance over two equal-length dense vectors."""

    if len(left) != len(right):
        raise ValueError("cosine distance requires equal-length vectors")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        raise ValueError("cosine distance is undefined for a zero vector")
    cosine = dot / (left_norm * right_norm)
    return max(0.0, min(2.0, 1.0 - cosine))


class ShadowGenerationSpec(RetrievalValue):
    """Pins one shadow generation build to one route and one dataset freeze."""

    route: ShadowRoutePins
    dataset_freeze_sha256: Sha256
    projection_generation: int = Field(ge=1)
    release_id: OpaqueId = _DEFAULT_RELEASE_ID
    backend_watermark: int = Field(default=_DEFAULT_BACKEND_WATERMARK, ge=0)


@dataclass(frozen=True, slots=True)
class ShadowPartition:
    """One Matter partition of a shadow generation."""

    tenant_id: UUID
    matter_id: UUID
    projection: ProjectionPins
    rows: tuple[ProjectedRow, ...]
    documents: tuple[EvalDocument, ...]


@dataclass(frozen=True, slots=True)
class ShadowProjectionGeneration:
    """One rebuildable shadow projection generation over the frozen corpus."""

    spec: ShadowGenerationSpec
    projection_set_id: UUID
    partitions: tuple[ShadowPartition, ...]
    leak_partitions: bool = False
    leak_privilege: bool = False
    serve_generation: int = 1

    def partition_for(self, tenant_id: UUID, matter_id: UUID) -> ShadowPartition:
        for partition in self.partitions:
            if partition.tenant_id == tenant_id and partition.matter_id == matter_id:
                return partition
        raise ShadowComparisonError(
            "the shadow generation has no partition for the requested scope"
        )

    def rows_digest(self) -> str:
        """Deterministic digest over every staged row across partitions."""

        return project_rows_digest(
            tuple(row for partition in self.partitions for row in partition.rows)
        )


def _projection_with_generation(
    projection: ProjectionPins, generation: int
) -> ProjectionPins:
    payload = dict(projection.model_dump(mode="python"))
    payload["projection_generation"] = generation
    return ProjectionPins.model_validate(payload)


class ShadowQueryRunner:
    """Query-scoped runner behind the fixed vector-exact PostgreSQL call.

    The runner models the governed plane: it accepts only the pinned
    statement, resolves partitions through the projection component access
    references, enforces the active query's Matter scope and privilege
    authorization, and stamps served rows with the generation the plane is
    serving. It fails closed whenever no query context is active. The leak
    flags are explicit simulation seams used to prove the harness detects a
    broken plane; they mirror the seams in ``fake.py``.
    """

    def __init__(self, generation: ShadowProjectionGeneration) -> None:
        self._generation = generation
        self._active: EvalQuery | None = None

    def begin_query(self, query: EvalQuery) -> None:
        if self._active is not None:
            raise ShadowComparisonError("a shadow query is already active")
        self._active = query

    def end_query(self, query: EvalQuery) -> None:
        if self._active is not query:
            raise ShadowComparisonError("the active shadow query context was lost")
        self._active = None

    def fetch_all(
        self,
        *,
        component_access_refs: tuple[str, ...],
        statement: str,
        parameters: tuple[object, ...],
    ) -> tuple[dict[str, object], ...]:
        query = self._active
        if query is None:
            raise ShadowComparisonError(
                "the shadow plane refuses queries without an active query context"
            )
        if statement != VECTOR_EXACT_STATEMENT:
            raise ShadowComparisonError(
                "the shadow plane only executes the pinned vector-exact statement"
            )
        if len(parameters) != 2:
            raise ShadowComparisonError("the vector-exact call takes two parameters")
        embedding, max_results = parameters
        if not isinstance(embedding, (tuple, list)):
            raise ShadowComparisonError("the query embedding parameter is invalid")
        if type(max_results) is not int:
            raise ShadowComparisonError("the max-results parameter is invalid")
        query_embedding = tuple(float(value) for value in embedding)

        selected = self._generation.partition_for(query.tenant_id, query.matter_id)
        serve_projection = _projection_with_generation(
            selected.projection, self._generation.serve_generation
        )

        documents_by_id = {
            document.document_id: document
            for partition in self._generation.partitions
            for document in partition.documents
        }
        if self._generation.leak_partitions:
            scanned = self._generation.partitions
        else:
            references = set(component_access_refs)
            scanned = tuple(
                partition
                for partition in self._generation.partitions
                if partition.projection.component_access_ref in references
            )
            if not scanned:
                raise ShadowComparisonError(
                    "the access references selected no shadow partition"
                )
            if scanned != (selected,):
                raise ShadowComparisonError(
                    "the access references did not select the query partition"
                )

        scored: list[tuple[float, str, dict[str, object]]] = []
        for partition in scanned:
            for row in partition.rows:
                document = documents_by_id[row.source.document_id]
                if (
                    document.privileged
                    and not query.privilege_authorized
                    and not self._generation.leak_privilege
                ):
                    continue
                distance = cosine_distance(query_embedding, row.embedding or ())
                scored.append(
                    (
                        distance,
                        row.source.retrieval_record_id,
                        {
                            "projection": serve_projection,
                            "source": row.source,
                            "content": row.content,
                            "score": 1.0 / (1.0 + distance),
                            "distance": distance,
                        },
                    )
                )
        scored.sort(key=lambda item: (item[0], item[1]))
        return tuple(item[2] for item in scored[:max_results])


class ShadowRetrievedDocument(RetrievalValue):
    """One ranked document served to a frozen query."""

    document_id: OpaqueId
    rank: int = Field(ge=1)
    distance: float
    score: float

    @model_validator(mode="after")
    def validate_ranked_document(self) -> Self:
        if not math.isfinite(self.distance) or self.distance < 0:
            raise ValueError("retrieved distance must be finite and non-negative")
        if not math.isfinite(self.score):
            raise ValueError("retrieved score must be finite")
        return self


class ShadowQueryOutcome(RetrievalValue):
    """One measured frozen query against one shadow generation."""

    query_id: OpaqueId
    route_label: ShadowRouteLabel
    projection_generation: int = Field(ge=1)
    retrieved: tuple[ShadowRetrievedDocument, ...]
    latency_seconds: float

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if not math.isfinite(self.latency_seconds) or self.latency_seconds < 0:
            raise ValueError("query latency must be finite and non-negative")
        ranks = [item.rank for item in self.retrieved]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("retrieved ranks must be contiguous from one")
        return self

    def ranked_document_ids(self) -> tuple[str, ...]:
        return tuple(item.document_id for item in self.retrieved)


class ShadowPartitionMetrics(RetrievalValue):
    """Deterministic metric bundle for one route, Tenant, and Matter."""

    tenant_id: UUID
    matter_id: UUID
    route_label: ShadowRouteLabel
    queries_evaluated: int = Field(ge=0)
    recall_eligible_queries: int = Field(ge=0)
    citation_eligible_queries: int = Field(ge=0)
    k_values: tuple[int, ...] = Field(min_length=1)
    recall_at_k: tuple[float, ...]
    ndcg_at_k: tuple[float, ...]
    mrr: float
    citation_accuracy: float
    latency_mean_seconds: float
    latency_max_seconds: float
    leakage_codes: tuple[str, ...]

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        if len(self.recall_at_k) != len(self.k_values) or len(self.ndcg_at_k) != len(
            self.k_values
        ):
            raise ValueError("metric vectors must align with the pinned k values")
        if list(self.k_values) != sorted(set(self.k_values)) or not all(
            value >= 1 for value in self.k_values
        ):
            raise ValueError("k values must be unique positive integers")
        for value in (
            *self.recall_at_k,
            *self.ndcg_at_k,
            self.mrr,
            self.citation_accuracy,
        ):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("ranking metrics must be finite and within [0, 1]")
        for value in (self.latency_mean_seconds, self.latency_max_seconds):
            if not math.isfinite(value) or value < 0:
                raise ValueError("latency must be finite and non-negative")
        if self.latency_max_seconds < self.latency_mean_seconds:
            raise ValueError("maximum latency cannot be below the mean")
        if self.recall_eligible_queries > self.queries_evaluated:
            raise ValueError("recall eligibility cannot exceed evaluated queries")
        if self.citation_eligible_queries > self.queries_evaluated:
            raise ValueError("citation eligibility cannot exceed evaluated queries")
        if self.queries_evaluated == 0 and (
            self.recall_at_k or self.ndcg_at_k or self.leakage_codes
        ):
            raise ValueError("an empty partition carries no measurements")
        return self


class ShadowRouteReport(RetrievalValue):
    """One route's measurements over every queried partition."""

    route: ShadowRoutePins
    dataset_freeze_sha256: Sha256
    projection_set_id: UUID
    projection_generation: int = Field(ge=1)
    max_results: int = Field(ge=1)
    partitions: tuple[ShadowPartitionMetrics, ...] = Field(min_length=1)
    macro_recall_at_k: tuple[float, ...]
    macro_ndcg_at_k: tuple[float, ...]
    macro_mrr: float
    macro_citation_accuracy: float
    macro_latency_mean_seconds: float
    max_latency_seconds: float
    leakage_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        keys = [(item.tenant_id, item.matter_id) for item in self.partitions]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("partition metrics must be sorted and unique")
        if len(self.macro_recall_at_k) != len(self.macro_ndcg_at_k):
            raise ValueError("macro metric vectors must align")
        for value in (
            *self.macro_recall_at_k,
            *self.macro_ndcg_at_k,
            self.macro_mrr,
            self.macro_citation_accuracy,
        ):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("macro metrics must be finite and within [0, 1]")
        if (
            not math.isfinite(self.macro_latency_mean_seconds)
            or self.macro_latency_mean_seconds < 0
        ):
            raise ValueError("macro latency must be finite and non-negative")
        if (
            not math.isfinite(self.max_latency_seconds)
            or self.max_latency_seconds < self.macro_latency_mean_seconds
        ):
            raise ValueError("route maximum latency cannot be below the macro mean")
        if self.leakage_count != sum(
            len(item.leakage_codes) for item in self.partitions
        ):
            raise ValueError("route leakage count must match its partitions")
        for item in self.partitions:
            if item.k_values != self.partitions[0].k_values:
                raise ValueError("every partition must report the same k values")
            if item.route_label != self.route.label:
                raise ValueError("partition metrics must carry the route label")
        return self


class ShadowComparisonReport(RetrievalValue):
    """The deterministic comparison artifact over one dataset freeze."""

    harness_version: SafeVersion = HARNESS_VERSION
    dataset_freeze_sha256: Sha256
    k_values: tuple[int, ...] = Field(min_length=1)
    routes: tuple[ShadowRouteReport, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_comparison(self) -> Self:
        labels = [report.route.label for report in self.routes]
        if len(labels) != len(set(labels)):
            raise ValueError("each embedding route appears once per comparison")
        freeze = self.routes[0].dataset_freeze_sha256
        keys = [(item.tenant_id, item.matter_id) for item in self.routes[0].partitions]
        for report in self.routes:
            if report.dataset_freeze_sha256 != freeze:
                raise ValueError("compared routes must share one dataset freeze")
            if self.k_values != report.partitions[0].k_values:
                raise ValueError("compared routes must report the same k values")
            report_keys = [
                (item.tenant_id, item.matter_id) for item in report.partitions
            ]
            if report_keys != keys:
                raise ValueError("compared routes must cover identical partitions")
        return self


def recall_at_k(
    ranked: Sequence[str], relevant: Collection[str], k: int
) -> float | None:
    """Recall@k over one ranked list; None when nothing is relevant."""

    if not relevant:
        return None
    hits = sum(1 for document_id in ranked[:k] if document_id in relevant)
    return hits / len(relevant)


def ndcg_at_k(ranked: Sequence[str], grades: Mapping[str, int], k: int) -> float:
    """Graded nDCG@k with exponential gain and logarithmic discount."""

    def gain(grade: int) -> float:
        return float(2**grade - 1)

    dcg = sum(
        gain(grades.get(document_id, 0)) / math.log2(position + 1)
        for position, document_id in enumerate(ranked[:k], start=1)
    )
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum(
        gain(grade) / math.log2(position + 1)
        for position, grade in enumerate(ideal, start=1)
    )
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def reciprocal_rank(ranked: Sequence[str], relevant: Collection[str]) -> float:
    """Reciprocal rank of the first relevant document, or zero."""

    for position, document_id in enumerate(ranked, start=1):
        if document_id in relevant:
            return 1.0 / position
    return 0.0


def document_text(document: EvalDocument) -> str:
    """Text projected for one frozen document."""

    return f"{document.title}\n{document.content}"


def _source_provenance(document: EvalDocument, content_sha256: str) -> SourceProvenance:
    return SourceProvenance(
        retrieval_record_id=f"shadow:{document.document_id}",
        source_id=f"frozen-v1:{document.document_id}",
        source_version="1",
        source_sha256=content_sha256,
        source_locator=(
            f"evals/retrieval/frozen-v1/documents.json#document={document.document_id}"
        ),
        document_id=document.document_id,
        chunk_id=f"{document.document_id}:chunk-0",
        chunk_ordinal=0,
        span_kind="text",
        span_start=0,
        span_end=len(document.content),
        chunk_sha256=content_sha256,
        classification=document.classification,
    )


def build_shadow_generation(
    dataset: FrozenEvaluationDataset,
    route: ShadowRoutePins,
    *,
    projection_generation: int = _DEFAULT_GENERATION,
    release_id: str = _DEFAULT_RELEASE_ID,
    backend_watermark: int = _DEFAULT_BACKEND_WATERMARK,
    embedder: ShadowEmbedder | None = None,
    leak_partitions: bool = False,
    leak_privilege: bool = False,
    serve_generation: int | None = None,
) -> ShadowProjectionGeneration:
    """Build one shadow vector projection generation over the frozen corpus.

    Documents are grouped per Matter partition, embedded with the route
    embedder, and staged through the idempotent outbox projector, so a replay
    of the same frozen corpus rebuilds identical rows.
    """

    if embedder is None:
        embedder = DeterministicHashEmbedder(route)
    if embedder.pins.vector != route.vector or embedder.pins.seed != route.seed:
        raise ShadowComparisonError("the embedder does not match the shadow route")
    freeze = dataset.manifest.freeze_sha256
    spec = ShadowGenerationSpec(
        route=route,
        dataset_freeze_sha256=freeze,
        projection_generation=projection_generation,
        release_id=release_id,
        backend_watermark=backend_watermark,
    )
    projection_set_id = uuid5(
        NAMESPACE_URL, f"sklegal-retrieval-shadow:{route.label.value}:{freeze}"
    )
    grouped: dict[PartitionKey, list[EvalDocument]] = {}
    for document in dataset.documents:
        grouped.setdefault((document.tenant_id, document.matter_id), []).append(
            document
        )
    partitions: list[ShadowPartition] = []
    for key in sorted(grouped):
        tenant_id, matter_id = key
        documents = tuple(grouped[key])
        scope = RetrievalScope(
            tenant_id=tenant_id,
            scope_kind=ScopeKind.MATTER,
            matter_id=matter_id,
        )
        suffix = hashlib.sha256(
            f"shadow:{route.label.value}:{freeze}:{tenant_id}:{matter_id}".encode()
        ).hexdigest()[:32]
        projection = ProjectionPins(
            component=RetrievalComponent.VECTOR,
            scope=scope,
            projection_set_id=projection_set_id,
            physical_partition_id=f"rp_{suffix}",
            projection_generation=projection_generation,
            projection_schema_version="1.0.0",
            release_id=release_id,
            release_manifest_sha256=freeze,
            component_access_ref=f"shadow:{route.label.value}:{suffix[:12]}",
            projection_adapter_version="1.0.0",
            projector_version="1.0.0",
            backend_watermark=backend_watermark,
            vector=route.vector,
        )
        events: list[OutboxEvent] = []
        for ordinal, document in enumerate(documents, start=1):
            text = document_text(document)
            content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
            events.append(
                OutboxEvent(
                    event_sequence=ordinal,
                    idempotency_key=(
                        f"{route.label.value}:{freeze}:{document.document_id}"
                    ),
                    row=ProjectedRow(
                        projection=projection,
                        source=_source_provenance(document, content_sha256),
                        content=document.content,
                        embedding=embedder.embed(text),
                    ),
                )
            )
        sink = OutboxProjector().rebuild(tuple(events), FakeProjectionSink)
        partitions.append(
            ShadowPartition(
                tenant_id=tenant_id,
                matter_id=matter_id,
                projection=projection,
                rows=sink.rows(),
                documents=documents,
            )
        )
    return ShadowProjectionGeneration(
        spec=spec,
        projection_set_id=projection_set_id,
        partitions=tuple(partitions),
        leak_partitions=leak_partitions,
        leak_privilege=leak_privilege,
        serve_generation=(
            projection_generation if serve_generation is None else serve_generation
        ),
    )


def stale_shadow_generation(
    generation: ShadowProjectionGeneration, served_generation: int
) -> ShadowProjectionGeneration:
    """Simulation seam: serve rows pinned to another projection generation.

    The copy keeps the true selected pins while the plane serves rows stamped
    with ``served_generation``, exactly how a stale replica manifests at the
    adapter boundary. The evaluation gates must reject it.
    """

    return ShadowProjectionGeneration(
        spec=generation.spec,
        projection_set_id=generation.projection_set_id,
        partitions=generation.partitions,
        leak_partitions=generation.leak_partitions,
        leak_privilege=generation.leak_privilege,
        serve_generation=served_generation,
    )


class ShadowEvaluationPlane:
    """Execute frozen queries against one generation through the adapter."""

    def __init__(
        self,
        generation: ShadowProjectionGeneration,
        *,
        adapter: PostgresRetrievalAdapter | None = None,
    ) -> None:
        self._generation = generation
        self._runner = ShadowQueryRunner(generation)
        self._adapter = (
            adapter if adapter is not None else PostgresRetrievalAdapter(self._runner)
        )

    def execute_query(
        self,
        query: EvalQuery,
        embedder: ShadowEmbedder,
        *,
        max_results: int = DEFAULT_MAX_RESULTS,
        clock: Clock = time.perf_counter,
    ) -> ShadowQueryOutcome:
        if embedder.pins.vector != self._generation.spec.route.vector:
            raise ShadowComparisonError(
                "the query embedder does not match the shadow generation route"
            )
        partition = self._generation.partition_for(query.tenant_id, query.matter_id)
        projection = partition.projection
        started = clock()
        embedding = embedder.embed(query.query_text)
        if len(embedding) != projection.vector.embedding_dimension:
            raise ShadowComparisonError(
                "the query embedding dimension does not match the pinned generation"
            )
        bound = bind_query_template(
            "vector.exact.v1",
            {"query_embedding": embedding, "max_results": max_results},
        )
        self._runner.begin_query(query)
        try:
            rows = self._adapter.execute(bound, (projection,))
        except RetrievalIntegrityError as error:
            raise ShadowComparisonError(
                "the shadow plane served rows that do not match the selected"
                f" projection generation: {error}"
            ) from error
        finally:
            self._runner.end_query(query)
        finished = clock()
        latency = finished - started
        if not math.isfinite(latency) or latency < 0:
            raise ShadowComparisonError("the measured query latency is invalid")
        retrieved = tuple(
            ShadowRetrievedDocument(
                document_id=row.source.document_id,
                rank=rank,
                distance=row.distance if row.distance is not None else 0.0,
                score=row.score,
            )
            for rank, row in enumerate(rows, start=1)
        )
        return ShadowQueryOutcome(
            query_id=query.query_id,
            route_label=self._generation.spec.route.label,
            projection_generation=self._generation.spec.projection_generation,
            retrieved=retrieved,
            latency_seconds=latency,
        )


def evaluate_shadow_route(
    dataset: FrozenEvaluationDataset,
    generation: ShadowProjectionGeneration,
    embedder: ShadowEmbedder,
    *,
    k_values: tuple[int, ...] = K_VALUES,
    max_results: int = DEFAULT_MAX_RESULTS,
    clock: Clock = time.perf_counter,
) -> ShadowRouteReport:
    """Run every frozen query against one generation and compute its metrics."""

    if dataset.manifest.freeze_sha256 != generation.spec.dataset_freeze_sha256:
        raise ShadowComparisonError(
            "the shadow generation was built against a different dataset freeze"
        )
    plane = ShadowEvaluationPlane(generation)
    outcomes = [
        plane.execute_query(query, embedder, max_results=max_results, clock=clock)
        for query in dataset.queries
    ]
    return build_route_report(
        dataset,
        generation,
        outcomes,
        k_values=k_values,
        max_results=max_results,
    )


def build_route_report(
    dataset: FrozenEvaluationDataset,
    generation: ShadowProjectionGeneration,
    outcomes: Sequence[ShadowQueryOutcome],
    *,
    k_values: tuple[int, ...] = K_VALUES,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> ShadowRouteReport:
    """Compute the deterministic per-partition metrics over measured outcomes."""

    queries_by_id = {query.query_id: query for query in dataset.queries}
    documents_by_id = {document.document_id: document for document in dataset.documents}
    grades_by_query: dict[str, dict[str, int]] = {}
    for judgment in dataset.judgments:
        grades_by_query.setdefault(judgment.query_id, {})[judgment.document_id] = (
            judgment.grade
        )
    outcomes_by_id = {outcome.query_id: outcome for outcome in outcomes}
    if set(outcomes_by_id) != set(queries_by_id):
        raise ShadowComparisonError(
            "the measured outcomes do not cover exactly the frozen queries"
        )
    partition_keys = sorted(
        {(query.tenant_id, query.matter_id) for query in dataset.queries}
    )
    metrics = tuple(
        _partition_metrics(
            dataset=dataset,
            documents_by_id=documents_by_id,
            grades_by_query=grades_by_query,
            outcomes_by_id=outcomes_by_id,
            partition=key,
            route_label=generation.spec.route.label,
            k_values=k_values,
        )
        for key in partition_keys
    )
    recall_vectors = [
        item.recall_at_k for item in metrics if item.recall_eligible_queries > 0
    ]
    citation_values = [
        item.citation_accuracy for item in metrics if item.citation_eligible_queries > 0
    ]
    latencies = [item.latency_mean_seconds for item in metrics]
    return ShadowRouteReport(
        route=generation.spec.route,
        dataset_freeze_sha256=generation.spec.dataset_freeze_sha256,
        projection_set_id=generation.projection_set_id,
        projection_generation=generation.spec.projection_generation,
        max_results=max_results,
        partitions=metrics,
        macro_recall_at_k=_macro(recall_vectors, len(k_values)),
        macro_ndcg_at_k=_macro([item.ndcg_at_k for item in metrics], len(k_values)),
        macro_mrr=_mean([item.mrr for item in metrics]),
        macro_citation_accuracy=_mean(citation_values),
        macro_latency_mean_seconds=_mean(latencies),
        max_latency_seconds=max(
            (item.latency_max_seconds for item in metrics), default=0.0
        ),
        leakage_count=sum(len(item.leakage_codes) for item in metrics),
    )


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _macro(vectors: Sequence[tuple[float, ...]], width: int) -> tuple[float, ...]:
    if not vectors:
        return (0.0,) * width
    return tuple(
        sum(vector[index] for vector in vectors) / len(vectors)
        for index in range(width)
    )


def _partition_metrics(
    *,
    dataset: FrozenEvaluationDataset,
    documents_by_id: Mapping[str, EvalDocument],
    grades_by_query: Mapping[str, Mapping[str, int]],
    outcomes_by_id: Mapping[str, ShadowQueryOutcome],
    partition: PartitionKey,
    route_label: ShadowRouteLabel,
    k_values: tuple[int, ...],
) -> ShadowPartitionMetrics:
    tenant_id, matter_id = partition
    queries = [
        query
        for query in dataset.queries
        if (query.tenant_id, query.matter_id) == partition
    ]
    outcomes = [outcomes_by_id[query.query_id] for query in queries]
    recall_sums = [0.0] * len(k_values)
    ndcg_sums = [0.0] * len(k_values)
    recall_eligible = 0
    citation_eligible = 0
    citation_correct = 0
    reciprocal_ranks: list[float] = []
    leakage: set[str] = set()
    for query, outcome in zip(queries, outcomes, strict=True):
        grades = grades_by_query.get(query.query_id, {})
        ranked = outcome.ranked_document_ids()
        relevant = frozenset(
            document_id
            for document_id, grade in grades.items()
            if grade >= RELEVANT_GRADE_THRESHOLD
        )
        recalls = [recall_at_k(ranked, relevant, k) for k in k_values]
        if relevant:
            recall_eligible += 1
            for index, value in enumerate(recalls):
                recall_sums[index] += value if value is not None else 0.0
        for index, k in enumerate(k_values):
            ndcg_sums[index] += ndcg_at_k(ranked, grades, k)
        reciprocal_ranks.append(reciprocal_rank(ranked, relevant))
        if query.case_class is EvalCaseClass.EXACT_CITATION:
            citation_eligible += 1
            controlling = [
                document_id for document_id, grade in grades.items() if grade == 3
            ]
            if len(controlling) == 1 and ranked[:1] == controlling:
                citation_correct += 1
        for retrieved in outcome.retrieved:
            document = documents_by_id.get(retrieved.document_id)
            if document is None:
                raise ShadowComparisonError(
                    "the shadow plane served a document absent from the freeze"
                )
            if (document.tenant_id, document.matter_id) != partition:
                leakage.add(
                    f"{LEAKAGE_CROSS_PARTITION}:{query.query_id}:{document.document_id}"
                )
            if document.privileged and not query.privilege_authorized:
                leakage.add(
                    f"{LEAKAGE_PRIVILEGE_ESCALATION}:{query.query_id}"
                    f":{document.document_id}"
                )
    latencies = [outcome.latency_seconds for outcome in outcomes]
    return ShadowPartitionMetrics(
        tenant_id=tenant_id,
        matter_id=matter_id,
        route_label=route_label,
        queries_evaluated=len(queries),
        recall_eligible_queries=recall_eligible,
        citation_eligible_queries=citation_eligible,
        k_values=k_values,
        recall_at_k=tuple(
            value / recall_eligible if recall_eligible else 0.0 for value in recall_sums
        ),
        ndcg_at_k=tuple(
            value / len(queries) if queries else 0.0 for value in ndcg_sums
        ),
        mrr=_mean(reciprocal_ranks),
        citation_accuracy=(
            citation_correct / citation_eligible if citation_eligible else 0.0
        ),
        latency_mean_seconds=_mean(latencies),
        latency_max_seconds=max(latencies, default=0.0),
        leakage_codes=tuple(sorted(leakage)),
    )


@dataclass(frozen=True, slots=True)
class ShadowCandidate:
    """One generation bound to its embedder for a comparison run."""

    generation: ShadowProjectionGeneration
    embedder: ShadowEmbedder

    @property
    def label(self) -> ShadowRouteLabel:
        return self.generation.spec.route.label


def compare_shadow_routes(
    dataset: FrozenEvaluationDataset,
    candidates: Sequence[ShadowCandidate],
    *,
    k_values: tuple[int, ...] = K_VALUES,
    max_results: int = DEFAULT_MAX_RESULTS,
    clock: Clock = time.perf_counter,
) -> ShadowComparisonReport:
    """Measure every candidate over the same freeze and partition set."""

    if not candidates:
        raise ShadowComparisonError("a comparison needs at least one candidate")
    reports = [
        evaluate_shadow_route(
            dataset,
            candidate.generation,
            candidate.embedder,
            k_values=k_values,
            max_results=max_results,
            clock=clock,
        )
        for candidate in candidates
    ]
    return ShadowComparisonReport(
        dataset_freeze_sha256=dataset.manifest.freeze_sha256,
        k_values=k_values,
        routes=tuple(reports),
    )


def build_fixture_candidates(
    dataset: FrozenEvaluationDataset,
    *,
    projection_generation: int = _DEFAULT_GENERATION,
    leak_partitions: bool = False,
    leak_privilege: bool = False,
) -> tuple[ShadowCandidate, ShadowCandidate]:
    """Build the two deterministic fixture candidates for one freeze.

    The custom legal route is always the first candidate and base BGE-M3 the
    second. The embedders are hash fixtures standing in for the candidate
    models: they exercise the harness deterministically and their numbers
    never measure or qualify a real model.
    """

    custom = build_shadow_generation(
        dataset,
        CUSTOM_LEGAL_ROUTE,
        projection_generation=projection_generation,
        leak_partitions=leak_partitions,
        leak_privilege=leak_privilege,
    )
    base = build_shadow_generation(
        dataset,
        BASE_BGE_M3_ROUTE,
        projection_generation=projection_generation,
        leak_partitions=leak_partitions,
        leak_privilege=leak_privilege,
    )
    return (
        ShadowCandidate(custom, DeterministicHashEmbedder(CUSTOM_LEGAL_ROUTE)),
        ShadowCandidate(base, DeterministicHashEmbedder(BASE_BGE_M3_ROUTE)),
    )


__all__ = [
    "BASE_BGE_M3_ROUTE",
    "CUSTOM_LEGAL_ROUTE",
    "DEFAULT_MAX_RESULTS",
    "DeterministicHashEmbedder",
    "FIXTURE_EMBEDDING_DIMENSION",
    "FIXTURE_EMBEDDER_KIND",
    "HARNESS_VERSION",
    "K_VALUES",
    "LEAKAGE_CROSS_PARTITION",
    "LEAKAGE_PRIVILEGE_ESCALATION",
    "ShadowCandidate",
    "ShadowComparisonError",
    "ShadowComparisonReport",
    "ShadowEmbedder",
    "ShadowEvaluationPlane",
    "ShadowGenerationSpec",
    "ShadowPartition",
    "ShadowPartitionMetrics",
    "ShadowProjectionGeneration",
    "ShadowQueryOutcome",
    "ShadowQueryRunner",
    "ShadowRetrievedDocument",
    "ShadowRouteLabel",
    "ShadowRoutePins",
    "ShadowRouteReport",
    "build_fixture_candidates",
    "build_route_report",
    "build_shadow_generation",
    "compare_shadow_routes",
    "cosine_distance",
    "document_text",
    "evaluate_shadow_route",
    "ndcg_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "stale_shadow_generation",
]
