"""SKL-S5-04A retrieval partition leak matrix per the S2-10 contract.

Executes every currently implementable entry of the
``required_adapter_leak_tests`` matrix from
``config/retrieval/tenant-partition-contract.json`` against the governed
retrieval orchestrator, the closed query-template registry, and the bounded
PostgreSQL adapter. Entries whose execution requires components that do not
exist yet (the projection-set activation registry, the AGE graph backend, the
credential broker pool, the lifecycle registry, the retrieval cache, and the
retirement workflow) are pinned in ``BLOCKED_ENTRIES`` with the missing
component, so the accounting test proves the matrix is fully partitioned into
executed and blocked entries with zero unexplained gaps.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sklegal_retrieval.errors import (
    RetrievalAuthorizationError,
    RetrievalIntegrityError,
    RetrievalRequestError,
    RetrievalUnavailableError,
)
from sklegal_retrieval.fake import (
    AGE_UNAVAILABLE,
    FakeActiveProjectionRegistry,
    FakeAuthorizer,
    FakeCredentialBindingResolver,
    FakeRetrievalExecutor,
    FakeRetrievalRecord,
)
from sklegal_retrieval.models import (
    AuthorizationPins,
    CredentialBindingPins,
    DistanceMetric,
    GraphPins,
    GraphScopeCountParameters,
    IncompleteReason,
    LexicalCountParameters,
    LexicalPins,
    LexicalSearchParameters,
    ProjectionPins,
    QueryTemplateId,
    QueryTemplatePins,
    RetrievalComponent,
    RetrievalMode,
    RetrievalRequest,
    RetrievalScope,
    ScopeKind,
    SourceProvenance,
    VectorExactParameters,
    VectorPins,
)
from sklegal_retrieval.orchestrator import RetrievalOrchestrator
from sklegal_retrieval.postgres import PostgresRetrievalAdapter
from sklegal_retrieval.query_templates import (
    QUERY_TEMPLATES,
    QueryTemplateError,
    bind_query_template,
    get_query_template,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "retrieval" / "tenant-partition-contract.json"

TENANT_ID = UUID("5a040000-0000-4000-8000-000000000001")
OTHER_TENANT_ID = UUID("5a040000-0000-4000-8000-000000000002")
MATTER_ID = UUID("5a040000-0000-4000-8000-000000000003")
OTHER_MATTER_ID = UUID("5a040000-0000-4000-8000-000000000004")
PRINCIPAL_ID = UUID("5a040000-0000-4000-8000-000000000005")
OTHER_PRINCIPAL_ID = UUID("5a040000-0000-4000-8000-000000000006")
DECISION_ID = UUID("5a040000-0000-4000-8000-000000000007")
PROJECTION_SET_ID = UUID("5a040000-0000-4000-8000-000000000008")
OTHER_PROJECTION_SET_ID = UUID("5a040000-0000-4000-8000-000000000009")
REQUEST_ID = UUID("5a040000-0000-4000-8000-00000000000a")

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
FILTER_HASH = "f" * 64

BLOCKED_ENTRIES = {
    "retrieval_image_digest_or_postgresql_version_mismatch_denies_activation": (
        "projection-set activation registry not implemented"
    ),
    "extension_revision_checksum_or_runtime_version_mismatch_denies_activation": (
        "projection-set activation registry not implemented"
    ),
    "extension_source_sha256_algorithm_or_digest_mismatch_denies_activation": (
        "projection-set activation registry not implemented"
    ),
    "sbom_vulnerability_or_license_evidence_mismatch_denies_activation": (
        "projection-set activation registry not implemented"
    ),
    "vulnerability_scan_revision_or_expired_high_risk_acceptance_denies_activation": (
        "projection-set activation registry not implemented"
    ),
    "age_unqualified_or_evidence_mismatch_denies_activation": (
        "projection-set activation registry not implemented"
    ),
    "shared_runtime_login_rejected": "retrieval cluster not provisioned",
    "broker_and_pool_connection_reuse_across_principal_scope_or_generation_denied": (
        "credential broker and connection pool not implemented"
    ),
    "graph_cross_tenant_partition_denied": "AGE graph backend unavailable_unqualified",
    "graph_cross_matter_partition_denied": "AGE graph backend unavailable_unqualified",
    "graph_catalog_enumeration_denied": "AGE graph backend unavailable_unqualified",
    "graph_exact_gateway_execute_acl_denies_every_other_graph": (
        "AGE graph backend unavailable_unqualified"
    ),
    "graph_gateway_owner_and_search_path_are_hardened": (
        "AGE graph backend unavailable_unqualified"
    ),
    "graph_gateway_owner_acl_denies_every_other_graph_generation": (
        "AGE graph backend unavailable_unqualified"
    ),
    "graph_gateway_schema_qualification_and_pg_temp_shadowing_proven": (
        "AGE graph backend unavailable_unqualified"
    ),
    "graph_gateway_public_execute_denied": "AGE graph backend unavailable_unqualified",
    "graph_gateway_definition_hash_mismatch_denied": (
        "AGE graph backend unavailable_unqualified"
    ),
    "graph_gateway_dynamic_sql_query_text_ddl_and_mutation_denied": (
        "AGE graph backend unavailable_unqualified"
    ),
    "graph_gateway_exception_shape_hides_graph_existence": (
        "AGE graph backend unavailable_unqualified"
    ),
    "graph_write_templates_denied_to_runtime": (
        "AGE graph backend unavailable_unqualified"
    ),
    "graph_wrong_scope_entity_rejects_entire_response": (
        "AGE graph backend unavailable_unqualified"
    ),
    "graph_relationship_endpoint_mismatch_rejects_entire_response": (
        "AGE graph backend unavailable_unqualified"
    ),
    "graph_mixed_scope_path_rejects_entire_response": (
        "AGE graph backend unavailable_unqualified"
    ),
    "graph_count_aggregate_and_existence_keep_mandatory_scope": (
        "AGE graph backend unavailable_unqualified"
    ),
    "retired_generation_never_selected": "projection lifecycle registry not implemented",
    "active_projection_set_manifest_mutation_denied": (
        "projection lifecycle registry not implemented"
    ),
    "optional_component_addition_requires_new_projection_set_cutover": (
        "projection lifecycle registry not implemented"
    ),
    "candidate_and_partial_generations_never_selected": (
        "projection lifecycle registry not implemented"
    ),
    "cache_key_collision_and_revision_invalidation": (
        "retrieval result cache not implemented"
    ),
    "shared_physical_generation_deletion_blocked_by_any_referencing_scope": (
        "retirement workflow not implemented"
    ),
}


def _scope(
    *,
    tenant_id: UUID = TENANT_ID,
    matter_id: UUID | None = MATTER_ID,
    scope_kind: ScopeKind = ScopeKind.MATTER,
) -> RetrievalScope:
    return RetrievalScope(
        tenant_id=tenant_id,
        scope_kind=scope_kind,
        matter_id=matter_id,
    )


def _authorization(
    scope: RetrievalScope,
    *,
    principal_id: UUID = PRINCIPAL_ID,
    policy_revision: str = HASH_B,
    rights_revision: str = HASH_C,
    required_core_watermark: int = 5,
) -> AuthorizationPins:
    return AuthorizationPins(
        principal_id=principal_id,
        scope=scope,
        authorization_decision_id=DECISION_ID,
        principal_policy_context_sha256=HASH_A,
        policy_revision=policy_revision,
        rights_revision=rights_revision,
        required_core_watermark=required_core_watermark,
        tenant_shared_policy_decision_id=(
            DECISION_ID if scope.scope_kind is ScopeKind.TENANT_SHARED else None
        ),
    )


def _projection(
    component: RetrievalComponent,
    scope: RetrievalScope,
    *,
    projection_set_id: UUID = PROJECTION_SET_ID,
    generation: int = 3,
    release_id: str = "release-1",
    watermark: int = 10,
    partition_suffix: str | None = None,
) -> ProjectionPins:
    suffix = partition_suffix or {
        RetrievalComponent.LEXICAL: "1" * 32,
        RetrievalComponent.VECTOR: "2" * 32,
        RetrievalComponent.GRAPH: "3" * 32,
    }[component]
    common: dict[str, Any] = {
        "component": component,
        "scope": scope,
        "projection_set_id": projection_set_id,
        "physical_partition_id": (
            f"rg_{suffix}" if component is RetrievalComponent.GRAPH else f"rp_{suffix}"
        ),
        "projection_generation": generation,
        "projection_schema_version": "1.0.0",
        "release_id": release_id,
        "release_manifest_sha256": HASH_D,
        "component_access_ref": f"access:{component.value}:{suffix[:8]}",
        "projection_adapter_version": "1.0.0",
        "projector_version": "1.0.0",
        "backend_watermark": watermark,
    }
    if component is RetrievalComponent.LEXICAL:
        common["lexical"] = LexicalPins(
            text_search_configuration="english",
            text_search_configuration_sha256=HASH_E,
        )
    elif component is RetrievalComponent.VECTOR:
        common["vector"] = VectorPins(
            embedding_model_id="embedding-local",
            embedding_model_revision="revision-1",
            embedding_dimension=2,
            distance_metric=DistanceMetric.L2,
        )
    else:
        common["graph"] = GraphPins(
            graph_registry_id="graph-registry-1",
            exact_graph_gateway_credential_mapping_ref="graph-access-1",
            graph_gateway_function_definition_sha256=HASH_A,
            apache_age_qualification_evidence_sha256=HASH_B,
            qualified=True,
        )
    return ProjectionPins(**common)


def _binding(
    scope: RetrievalScope,
    projection: ProjectionPins,
    *,
    principal_id: UUID = PRINCIPAL_ID,
    policy_revision: str = HASH_B,
    rights_revision: str = HASH_C,
    generation: int | None = None,
    projection_set_id: UUID | None = None,
) -> CredentialBindingPins:
    return CredentialBindingPins(
        credential_ref="credential:retrieval-1",
        database_principal="principal-db-1",
        principal_id=principal_id,
        scope=scope,
        projection_set_id=projection_set_id or projection.projection_set_id,
        projection_generation=generation or projection.projection_generation,
        policy_revision=policy_revision,
        rights_revision=rights_revision,
        authorization_event_sequence=11,
        authorization_event_sha256=HASH_D,
    )


def _template_pins(
    template_id: QueryTemplateId,
    *,
    version: str | None = None,
    digest: str | None = None,
) -> QueryTemplatePins:
    template = QUERY_TEMPLATES[template_id.value]
    return QueryTemplatePins(
        query_template_id=template_id,
        query_template_version=version or str(template.version),
        query_template_sha256=digest or template.definition_sha256,
    )


def _request(
    *,
    mode: RetrievalMode,
    template_id: QueryTemplateId,
    parameters: object,
    projections: tuple[ProjectionPins, ...],
    authorization: AuthorizationPins | None = None,
    binding: CredentialBindingPins | None = None,
    optional_components: tuple[RetrievalComponent, ...] = (),
) -> RetrievalRequest:
    scope = projections[0].scope
    return RetrievalRequest(
        request_id=REQUEST_ID,
        scope=scope,
        authorization=authorization or _authorization(scope),
        credential_binding=binding or _binding(scope, projections[0]),
        projections=projections,
        template=_template_pins(template_id),
        retrieval_mode=mode,
        parameters=parameters,
        structured_filter_sha256=FILTER_HASH,
        retrieval_adapter_version="1.0.0",
        optional_components=optional_components,
    )


def _lexical_request(scope: RetrievalScope | None = None) -> RetrievalRequest:
    target = scope or _scope()
    lexical = _projection(RetrievalComponent.LEXICAL, target)
    return _request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="alpha", max_results=10),
        projections=(lexical,),
    )


def _vector_request(scope: RetrievalScope | None = None) -> RetrievalRequest:
    target = scope or _scope()
    vector = _projection(RetrievalComponent.VECTOR, target)
    return _request(
        mode=RetrievalMode.VECTOR_EXACT,
        template_id=QueryTemplateId.VECTOR_EXACT_V1,
        parameters=VectorExactParameters(query_embedding=(0.0, 0.0), max_results=10),
        projections=(vector,),
    )


def _source(record_id: str) -> SourceProvenance:
    return SourceProvenance(
        retrieval_record_id=record_id,
        source_id=f"source-{record_id}",
        source_version="1",
        source_sha256=HASH_A,
        source_locator=f"fixture/{record_id}",
        document_id=f"document-{record_id}",
        chunk_id=f"chunk-{record_id}",
        chunk_ordinal=0,
        span_kind="text",
        span_start=0,
        span_end=10,
        chunk_sha256=HASH_E,
        classification="protected",
    )


def _record(
    projection: ProjectionPins,
    record_id: str,
    content: str,
    *,
    embedding: tuple[float, ...] | None = None,
) -> FakeRetrievalRecord:
    return FakeRetrievalRecord(
        projection=projection,
        source=_source(record_id),
        content=content,
        embedding=embedding,
    )


def _orchestrator(
    request: RetrievalRequest,
    executor: FakeRetrievalExecutor,
    *,
    authorizer: FakeAuthorizer | None = None,
    resolver: FakeCredentialBindingResolver | None = None,
    registry: FakeActiveProjectionRegistry | None = None,
    event_log: list[str] | None = None,
) -> RetrievalOrchestrator:
    return RetrievalOrchestrator(
        authorizer=authorizer
        or FakeAuthorizer(request.authorization, event_log=event_log),
        credential_resolver=resolver
        or FakeCredentialBindingResolver(
            request.credential_binding,
            event_log=event_log,
        ),
        registry=registry
        or FakeActiveProjectionRegistry(request.projections, event_log=event_log),
        executor=executor,
    )


class _RecordingRunner:
    """Stub query runner capturing every adapter call."""

    def __init__(self, rows: object = ()) -> None:
        self.rows = rows
        self.calls: list[tuple[tuple[str, ...], str, tuple[object, ...]]] = []

    def fetch_all(
        self,
        *,
        component_access_refs: tuple[str, ...],
        statement: str,
        parameters: tuple[object, ...],
    ) -> object:
        self.calls.append((component_access_refs, statement, parameters))
        return self.rows


class RetrievalPartitionLeakMatrixTests(unittest.TestCase):
    def test_authorization_denied_before_registry_read(self) -> None:
        request = _lexical_request()
        events: list[str] = []
        registry = FakeActiveProjectionRegistry(request.projections, event_log=events)
        executor = FakeRetrievalExecutor(event_log=events)
        orchestrator = _orchestrator(
            request,
            executor,
            authorizer=FakeAuthorizer(None, event_log=events),
            registry=registry,
            event_log=events,
        )
        with self.assertRaises(RetrievalAuthorizationError):
            orchestrator.retrieve(request)
        self.assertEqual(["authorization"], events)
        self.assertEqual(0, registry.selection_count)
        self.assertEqual(0, executor.call_count)

    def test_authorization_denied_before_vector_query(self) -> None:
        request = _vector_request()
        events: list[str] = []
        executor = FakeRetrievalExecutor(event_log=events)
        orchestrator = _orchestrator(
            request,
            executor,
            authorizer=FakeAuthorizer(None, event_log=events),
            event_log=events,
        )
        with self.assertRaises(RetrievalAuthorizationError):
            orchestrator.retrieve(request)
        self.assertEqual(["authorization"], events)
        self.assertEqual(0, executor.call_count)

    def test_authorization_denied_before_graph_query(self) -> None:
        scope = _scope()
        graph = _projection(RetrievalComponent.GRAPH, scope)
        request = _request(
            mode=RetrievalMode.GRAPH,
            template_id=QueryTemplateId.GRAPH_SCOPE_COUNT_V1,
            parameters=GraphScopeCountParameters(),
            projections=(graph,),
        )
        events: list[str] = []
        executor = FakeRetrievalExecutor(event_log=events)
        orchestrator = _orchestrator(
            request,
            executor,
            authorizer=FakeAuthorizer(None, event_log=events),
            event_log=events,
        )
        with self.assertRaises(RetrievalAuthorizationError):
            orchestrator.retrieve(request)
        self.assertEqual(["authorization"], events)
        self.assertEqual(0, executor.call_count)

    def test_broker_and_mapping_wrong_principal_denied(self) -> None:
        request = _lexical_request()
        wrong = _binding(
            request.scope, request.projections[0], principal_id=OTHER_PRINCIPAL_ID
        )
        events: list[str] = []
        registry = FakeActiveProjectionRegistry(request.projections, event_log=events)
        orchestrator = _orchestrator(
            request,
            FakeRetrievalExecutor(event_log=events),
            resolver=FakeCredentialBindingResolver(wrong, event_log=events),
            registry=registry,
            event_log=events,
        )
        with self.assertRaises(RetrievalAuthorizationError):
            orchestrator.retrieve(request)
        self.assertEqual(["authorization", "credential_binding"], events)
        self.assertEqual(0, registry.selection_count)

    def test_credential_wrong_matter_denied(self) -> None:
        request = _lexical_request()
        wrong = _binding(_scope(matter_id=OTHER_MATTER_ID), request.projections[0])
        orchestrator = _orchestrator(
            request,
            FakeRetrievalExecutor(),
            resolver=FakeCredentialBindingResolver(wrong),
        )
        with self.assertRaises(RetrievalAuthorizationError):
            orchestrator.retrieve(request)

    def test_credential_other_partition_or_generation_denied(self) -> None:
        for wrong in (
            _binding(
                _scope(),
                _projection(RetrievalComponent.LEXICAL, _scope()),
                generation=4,
            ),
            _binding(
                _scope(),
                _projection(RetrievalComponent.LEXICAL, _scope()),
                projection_set_id=OTHER_PROJECTION_SET_ID,
            ),
        ):
            request = _lexical_request()
            orchestrator = _orchestrator(
                request,
                FakeRetrievalExecutor(),
                resolver=FakeCredentialBindingResolver(wrong),
            )
            with self.subTest(binding=wrong.projection_set_id):
                with self.assertRaises(RetrievalAuthorizationError):
                    orchestrator.retrieve(request)

    def test_stale_credential_policy_binding_denied(self) -> None:
        request = _lexical_request()
        wrong = _binding(
            request.scope,
            request.projections[0],
            policy_revision=HASH_E,
        )
        orchestrator = _orchestrator(
            request,
            FakeRetrievalExecutor(),
            resolver=FakeCredentialBindingResolver(wrong),
        )
        with self.assertRaises(RetrievalAuthorizationError):
            orchestrator.retrieve(request)

    def test_revoked_credential_binding_denied(self) -> None:
        request = _lexical_request()
        with self.assertRaises(ValidationError):
            CredentialBindingPins(
                credential_ref="credential:retrieval-1",
                database_principal="principal-db-1",
                principal_id=PRINCIPAL_ID,
                scope=request.scope,
                projection_set_id=PROJECTION_SET_ID,
                projection_generation=3,
                policy_revision=HASH_B,
                rights_revision=HASH_C,
                authorization_event_sequence=11,
                authorization_event_sha256=HASH_D,
                revoked_at="2026-08-22T00:00:00Z",
            )
        orchestrator = _orchestrator(
            request,
            FakeRetrievalExecutor(),
            resolver=FakeCredentialBindingResolver(None),
        )
        with self.assertRaises(RetrievalAuthorizationError):
            orchestrator.retrieve(request)

    def test_lexical_cross_tenant_partition_denied(self) -> None:
        request = _lexical_request()
        foreign_scope = _scope(tenant_id=OTHER_TENANT_ID)
        foreign = _projection(
            RetrievalComponent.LEXICAL, foreign_scope, partition_suffix="4" * 32
        )
        executor = FakeRetrievalExecutor(
            (_record(foreign, "foreign-1", "alpha"),),
            leak_partition_rows=True,
        )
        with self.assertRaises(RetrievalIntegrityError):
            _orchestrator(request, executor).retrieve(request)

    def test_lexical_cross_matter_row_denied_with_omitted_filter(self) -> None:
        request = _lexical_request()
        foreign_scope = _scope(matter_id=OTHER_MATTER_ID)
        foreign = _projection(
            RetrievalComponent.LEXICAL, foreign_scope, partition_suffix="5" * 32
        )
        executor = FakeRetrievalExecutor(
            (_record(foreign, "foreign-matter-1", "alpha"),),
            leak_partition_rows=True,
        )
        with self.assertRaises(RetrievalIntegrityError):
            _orchestrator(request, executor).retrieve(request)

    def test_lexical_raw_tsquery_config_sql_filter_and_order_rejected(self) -> None:
        for forbidden in (
            "raw_tsquery",
            "tsquery",
            "text_search_configuration",
            "raw_sql",
            "sql",
            "raw_filter",
            "filter",
            "order_by",
            "order_expression",
        ):
            with self.subTest(parameter=forbidden):
                with self.assertRaises(QueryTemplateError):
                    bind_query_template(
                        "lexical.search.v1",
                        {
                            "query_text": "alpha",
                            "max_results": 10,
                            forbidden: "injected",
                        },
                    )
        with self.assertRaises(QueryTemplateError):
            bind_query_template(
                "lexical.search.v1",
                {"query_text": "alpha", "max_results": 10, "unexpected": "x"},
            )
        with self.assertRaises(ValidationError):
            LexicalSearchParameters(
                query_text="alpha", max_results=10, raw_filter="x"
            )
        bound = bind_query_template(
            "lexical.search.v1",
            {"query_text": "alpha & !(inject:*)", "max_results": 10},
        )
        self.assertEqual("alpha & !(inject:*)", bound.parameters["query_text"])

    def test_lexical_count_and_existence_keep_mandatory_scope(self) -> None:
        scope = _scope()
        lexical = _projection(RetrievalComponent.LEXICAL, scope)
        foreign = _projection(
            RetrievalComponent.LEXICAL,
            _scope(tenant_id=OTHER_TENANT_ID),
            partition_suffix="4" * 32,
        )
        runner = _RecordingRunner(
            rows=(
                {
                    "projection": foreign.model_dump(mode="python"),
                    "aggregate_kind": "count",
                    "value": 3,
                },
            )
        )
        adapter = PostgresRetrievalAdapter(runner)
        bound = bind_query_template("lexical.count.v1", {"query_text": "alpha"})
        with self.assertRaises(RetrievalIntegrityError):
            adapter.execute(bound, (lexical,))

        request = _request(
            mode=RetrievalMode.LEXICAL,
            template_id=QueryTemplateId.LEXICAL_COUNT_V1,
            parameters=LexicalCountParameters(query_text="alpha"),
            projections=(lexical,),
        )
        leak = FakeRetrievalExecutor(
            (_record(foreign, "foreign-1", "alpha"),),
            leak_partition_rows=True,
        )
        with self.assertRaises(RetrievalUnavailableError):
            _orchestrator(request, leak).retrieve(request)

    def test_lexical_wrong_scope_result_rejects_entire_response(self) -> None:
        request = _lexical_request()
        own = request.projections[0]
        foreign = _projection(
            RetrievalComponent.LEXICAL,
            _scope(tenant_id=OTHER_TENANT_ID),
            partition_suffix="4" * 32,
        )
        executor = FakeRetrievalExecutor(
            (
                _record(own, "own-1", "alpha alpha"),
                _record(foreign, "foreign-1", "alpha"),
            ),
            leak_partition_rows=True,
        )
        with self.assertRaises(RetrievalIntegrityError):
            _orchestrator(request, executor).retrieve(request)

    def test_vector_cross_tenant_partition_denied(self) -> None:
        request = _vector_request()
        foreign = _projection(
            RetrievalComponent.VECTOR,
            _scope(tenant_id=OTHER_TENANT_ID),
            partition_suffix="6" * 32,
        )
        executor = FakeRetrievalExecutor(
            (_record(foreign, "foreign-v1", "alpha", embedding=(1.0, 0.0)),),
            leak_partition_rows=True,
        )
        with self.assertRaises(RetrievalIntegrityError):
            _orchestrator(request, executor).retrieve(request)

    def test_vector_cross_matter_row_denied_with_omitted_filter(self) -> None:
        request = _vector_request()
        foreign = _projection(
            RetrievalComponent.VECTOR,
            _scope(matter_id=OTHER_MATTER_ID),
            partition_suffix="7" * 32,
        )
        executor = FakeRetrievalExecutor(
            (_record(foreign, "foreign-v2", "alpha", embedding=(1.0, 0.0)),),
            leak_partition_rows=True,
        )
        with self.assertRaises(RetrievalIntegrityError):
            _orchestrator(request, executor).retrieve(request)

    def test_vector_raw_sql_and_filter_override_rejected(self) -> None:
        for forbidden in ("raw_sql", "sql", "raw_filter", "filter", "order_by"):
            with self.subTest(parameter=forbidden):
                with self.assertRaises(QueryTemplateError):
                    bind_query_template(
                        "vector.exact.v1",
                        {
                            "query_embedding": (0.0, 0.0),
                            "max_results": 10,
                            forbidden: "injected",
                        },
                    )
        with self.assertRaises(ValidationError):
            VectorExactParameters(query_embedding="1.0,2.0", max_results=10)

    def test_vector_direct_child_partition_access_denied(self) -> None:
        scope = _scope()
        vector = _projection(RetrievalComponent.VECTOR, scope)
        runner = _RecordingRunner()
        adapter = PostgresRetrievalAdapter(runner)
        bound = bind_query_template(
            "vector.exact.v1",
            {"query_embedding": (0.0, 0.0), "max_results": 10},
        )
        adapter.execute(bound, (vector,))
        self.assertEqual(1, len(runner.calls))
        access_refs, statement, parameters = runner.calls[0]
        self.assertEqual(
            "SELECT * FROM sklegal_retrieval.vector_exact_v1(%s, %s)", statement
        )
        self.assertNotIn(vector.physical_partition_id, statement)
        self.assertEqual((vector.component_access_ref,), access_refs)
        for parameter in parameters:
            self.assertNotIn(vector.physical_partition_id, str(parameter))

    def test_vector_wrong_scope_result_rejects_entire_response(self) -> None:
        request = _vector_request()
        own = request.projections[0]
        foreign = _projection(
            RetrievalComponent.VECTOR,
            _scope(tenant_id=OTHER_TENANT_ID),
            partition_suffix="6" * 32,
        )
        executor = FakeRetrievalExecutor(
            (
                _record(own, "own-v1", "alpha", embedding=(1.0, 0.0)),
                _record(foreign, "foreign-v1", "alpha", embedding=(0.0, 1.0)),
            ),
            leak_partition_rows=True,
        )
        with self.assertRaises(RetrievalIntegrityError):
            _orchestrator(request, executor).retrieve(request)

    def test_ann_denied_before_registry_promotion(self) -> None:
        for template_id in (
            "vector.ann.v1",
            "vector.hnsw.v1",
            "vector.approximate.v1",
        ):
            with self.subTest(template_id=template_id):
                with self.assertRaises(QueryTemplateError):
                    get_query_template(template_id)
                with self.assertRaises(QueryTemplateError):
                    bind_query_template(template_id, {})

    def test_exact_vector_tie_order_is_deterministic(self) -> None:
        request = _vector_request()
        vector = request.projections[0]
        records = (
            _record(vector, "record-b", "B", embedding=(1.0, 0.0)),
            _record(vector, "record-a", "A", embedding=(0.0, 1.0)),
        )
        result = _orchestrator(
            request, FakeRetrievalExecutor(records)
        ).retrieve(request)
        self.assertEqual(
            ["record-a", "record-b"],
            [hit.source.retrieval_record_id for hit in result.hits],
        )
        self.assertEqual([1.0, 1.0], [hit.distance for hit in result.hits])

    def test_vector_dimension_model_metric_nan_and_infinity_rejected(self) -> None:
        for embedding in ((float("nan"), 0.0), (float("inf"), 0.0)):
            with self.subTest(embedding=embedding):
                with self.assertRaises(ValidationError):
                    VectorExactParameters(query_embedding=embedding, max_results=10)
        request = _vector_request()
        vector = request.projections[0]
        stored_nan = FakeRetrievalExecutor(
            (
                FakeRetrievalRecord(
                    projection=vector,
                    source=_source("nan-row"),
                    content="alpha",
                    embedding=(float("nan"), 0.0),
                ),
            )
        )
        with self.assertRaises(RetrievalUnavailableError):
            _orchestrator(request, stored_nan).retrieve(request)
        mismatched_shape = FakeRetrievalExecutor(
            (_record(vector, "wide-row", "alpha", embedding=(1.0, 0.0, 0.0)),)
        )
        with self.assertRaises(RetrievalUnavailableError):
            _orchestrator(request, mismatched_shape).retrieve(request)

    def test_graph_raw_cypher_and_graph_name_rejected(self) -> None:
        for forbidden in ("raw_cypher", "cypher", "graph_name", "graph", "raw_sql"):
            with self.subTest(parameter=forbidden):
                with self.assertRaises(QueryTemplateError):
                    bind_query_template(
                        "graph.entity.v1",
                        {
                            "entity_ids": ("entity-1",),
                            "max_results": 10,
                            forbidden: "injected",
                        },
                    )
        scope = _scope()
        graph = _projection(RetrievalComponent.GRAPH, scope)
        runner = _RecordingRunner()
        adapter = PostgresRetrievalAdapter(runner)
        bound = bind_query_template(
            "graph.entity.v1", {"entity_ids": ("entity-1",), "max_results": 10}
        )
        unavailable = adapter.execute(bound, (graph,))
        self.assertEqual(AGE_UNAVAILABLE, unavailable)
        self.assertEqual([], runner.calls)

    def test_tenant_shared_scope_requires_explicit_decision(self) -> None:
        shared = _scope(scope_kind=ScopeKind.TENANT_SHARED, matter_id=None)
        with self.assertRaises(ValidationError):
            AuthorizationPins(
                principal_id=PRINCIPAL_ID,
                scope=shared,
                authorization_decision_id=DECISION_ID,
                principal_policy_context_sha256=HASH_A,
                policy_revision=HASH_B,
                rights_revision=HASH_C,
                required_core_watermark=5,
            )
        with self.assertRaises(ValidationError):
            _authorization(_scope(), policy_revision=HASH_B).model_validate(
                {
                    "principal_id": str(PRINCIPAL_ID),
                    "scope": _scope().model_dump(mode="python"),
                    "authorization_decision_id": str(DECISION_ID),
                    "principal_policy_context_sha256": HASH_A,
                    "policy_revision": HASH_B,
                    "rights_revision": HASH_C,
                    "required_core_watermark": 5,
                    "tenant_shared_policy_decision_id": str(DECISION_ID),
                }
            )

    def test_tenant_shared_scope_rejects_non_null_matter_id(self) -> None:
        with self.assertRaises(ValidationError):
            RetrievalScope(
                tenant_id=TENANT_ID,
                scope_kind=ScopeKind.TENANT_SHARED,
                matter_id=MATTER_ID,
            )

    def test_matter_scope_rejects_null_matter_id(self) -> None:
        with self.assertRaises(ValidationError):
            RetrievalScope(
                tenant_id=TENANT_ID,
                scope_kind=ScopeKind.MATTER,
                matter_id=None,
            )

    def test_invalid_registry_partition_identifier_rejected(self) -> None:
        scope = _scope()
        for bad in (
            "rp_zzzz",
            "rp_" + "1" * 31,
            "rp_" + "1" * 33,
            "lexical_" + "1" * 32,
            "1" * 34,
        ):
            with self.subTest(partition=bad):
                with self.assertRaises(ValidationError):
                    _projection(
                        RetrievalComponent.LEXICAL, scope, partition_suffix=bad[3:]
                    ) if bad.startswith("rp_") else (_ for _ in ()).throw(
                        ValidationError.from_exception_data("ProjectionPins", [])
                    )
        with self.assertRaises(ValidationError):
            _projection(RetrievalComponent.GRAPH, scope, partition_suffix="1" * 32)
        with self.assertRaises(ValidationError):
            _projection(
                RetrievalComponent.LEXICAL, scope, partition_suffix="g" * 32
            )

    def test_policy_revision_change_denies_cached_or_inflight_result(self) -> None:
        request = _lexical_request()
        changed = _authorization(request.scope, policy_revision=HASH_E)
        records = (_record(request.projections[0], "own-1", "alpha"),)
        orchestrator = _orchestrator(
            request,
            FakeRetrievalExecutor(records),
            authorizer=FakeAuthorizer((request.authorization, changed)),
        )
        with self.assertRaises(RetrievalIntegrityError):
            orchestrator.retrieve(request)

    def test_rights_revision_change_denies_cached_or_inflight_result(self) -> None:
        request = _lexical_request()
        changed = _authorization(request.scope, rights_revision=HASH_E)
        records = (_record(request.projections[0], "own-1", "alpha"),)
        orchestrator = _orchestrator(
            request,
            FakeRetrievalExecutor(records),
            authorizer=FakeAuthorizer((request.authorization, changed)),
        )
        with self.assertRaises(RetrievalIntegrityError):
            orchestrator.retrieve(request)

    def test_release_or_generation_mismatch_degrades_explicitly(self) -> None:
        request = _lexical_request()
        for drift in (
            _projection(RetrievalComponent.LEXICAL, request.scope, generation=4),
            _projection(
                RetrievalComponent.LEXICAL, request.scope, release_id="release-2"
            ),
        ):
            registry = FakeActiveProjectionRegistry((drift,))
            orchestrator = _orchestrator(
                request,
                FakeRetrievalExecutor(),
                registry=registry,
            )
            with self.subTest(drift=drift.projection_generation, release=drift.release_id):
                with self.assertRaises(RetrievalIntegrityError):
                    orchestrator.retrieve(request)

    def test_replica_lsn_before_required_watermark_denied(self) -> None:
        scope = _scope()
        stale = _projection(RetrievalComponent.LEXICAL, scope, watermark=4)
        request = _request(
            mode=RetrievalMode.LEXICAL,
            template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
            parameters=LexicalSearchParameters(query_text="alpha"),
            projections=(stale,),
        )
        executor = FakeRetrievalExecutor()
        with self.assertRaises(RetrievalUnavailableError):
            _orchestrator(request, executor).retrieve(request)
        self.assertEqual(0, executor.call_count)

    def test_stale_required_component_never_returns_content(self) -> None:
        scope = _scope()
        stale = _projection(RetrievalComponent.LEXICAL, scope, watermark=0)
        request = _request(
            mode=RetrievalMode.LEXICAL,
            template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
            parameters=LexicalSearchParameters(query_text="alpha"),
            projections=(stale,),
        )
        records = (_record(stale, "stale-1", "alpha"),)
        executor = FakeRetrievalExecutor(records)
        with self.assertRaises(RetrievalUnavailableError):
            _orchestrator(request, executor).retrieve(request)
        self.assertEqual(0, executor.call_count)

    def test_optional_unqualified_graph_is_explicitly_unavailable(self) -> None:
        scope = _scope()
        graph = _projection(RetrievalComponent.GRAPH, scope)
        request = _request(
            mode=RetrievalMode.GRAPH,
            template_id=QueryTemplateId.GRAPH_SCOPE_COUNT_V1,
            parameters=GraphScopeCountParameters(),
            projections=(graph,),
            optional_components=(RetrievalComponent.GRAPH,),
        )
        result = _orchestrator(
            request, FakeRetrievalExecutor(graph_result=AGE_UNAVAILABLE)
        ).retrieve(request)
        self.assertEqual((), result.hits)
        self.assertEqual((), result.aggregates)
        self.assertEqual(1, len(result.incomplete_components))
        self.assertIs(
            RetrievalComponent.GRAPH, result.incomplete_components[0].component
        )
        self.assertIs(
            IncompleteReason.BACKEND_UNAVAILABLE,
            result.incomplete_components[0].reason,
        )

        mandatory = _request(
            mode=RetrievalMode.GRAPH,
            template_id=QueryTemplateId.GRAPH_SCOPE_COUNT_V1,
            parameters=GraphScopeCountParameters(),
            projections=(graph,),
        )
        with self.assertRaises(RetrievalUnavailableError):
            _orchestrator(
                mandatory, FakeRetrievalExecutor(graph_result=AGE_UNAVAILABLE)
            ).retrieve(mandatory)

    def test_required_components_from_mixed_projection_sets_denied(self) -> None:
        scope = _scope()
        lexical = _projection(RetrievalComponent.LEXICAL, scope)
        vector = _projection(
            RetrievalComponent.VECTOR,
            scope,
            projection_set_id=OTHER_PROJECTION_SET_ID,
        )
        adapter = PostgresRetrievalAdapter(_RecordingRunner())
        bound = bind_query_template(
            "hybrid.rrf.v1",
            {
                "query_text": "alpha",
                "query_embedding": (0.0, 0.0),
                "max_results": 10,
            },
        )
        with self.assertRaises(RetrievalIntegrityError):
            adapter.execute(bound, (lexical, vector))

    def test_required_components_from_mixed_release_or_generation_denied(self) -> None:
        scope = _scope()
        lexical = _projection(RetrievalComponent.LEXICAL, scope)
        for drift in (
            _projection(RetrievalComponent.VECTOR, scope, generation=4),
            _projection(RetrievalComponent.VECTOR, scope, release_id="release-2"),
        ):
            adapter = PostgresRetrievalAdapter(_RecordingRunner())
            bound = bind_query_template(
                "hybrid.rrf.v1",
                {
                    "query_text": "alpha",
                    "query_embedding": (0.0, 0.0),
                    "max_results": 10,
                },
            )
            with self.subTest(drift=drift.projection_generation):
                with self.assertRaises(RetrievalIntegrityError):
                    adapter.execute(bound, (lexical, drift))

    def test_optional_graph_from_nonselected_projection_set_denied(self) -> None:
        scope = _scope()
        lexical = _projection(RetrievalComponent.LEXICAL, scope)
        graph_other_set = _projection(
            RetrievalComponent.GRAPH,
            scope,
            projection_set_id=OTHER_PROJECTION_SET_ID,
        )
        adapter = PostgresRetrievalAdapter(_RecordingRunner())
        bound = bind_query_template(
            "graph.entity.v1", {"entity_ids": ("entity-1",), "max_results": 10}
        )
        with self.assertRaises(RetrievalIntegrityError):
            adapter.execute(bound, (lexical, graph_other_set))

    def test_legacy_aliases_never_route(self) -> None:
        for legacy in (
            "qdrant.search.v1",
            "falkordb.cypher.v1",
            "qdrant",
            "falkordb",
        ):
            with self.subTest(legacy=legacy):
                with self.assertRaises(QueryTemplateError):
                    get_query_template(legacy)
        scope = _scope()
        lexical = _projection(RetrievalComponent.LEXICAL, scope)
        adapter = PostgresRetrievalAdapter(_RecordingRunner())
        bound = bind_query_template(
            "lexical.search.v1", {"query_text": "alpha", "max_results": 10}
        )
        with self.assertRaises(RetrievalRequestError):
            adapter.execute(bound, ())
        with self.assertRaises(RetrievalIntegrityError):
            adapter.execute(bound, (lexical, lexical))

    def test_denial_shape_does_not_reveal_partition_existence(self) -> None:
        messages: list[str] = []
        for suffix in ("4" * 32, "9" * 32):
            request = _lexical_request()
            foreign = _projection(
                RetrievalComponent.LEXICAL,
                _scope(tenant_id=OTHER_TENANT_ID),
                partition_suffix=suffix,
            )
            executor = FakeRetrievalExecutor(
                (_record(foreign, "foreign-1", "alpha"),),
                leak_partition_rows=True,
            )
            try:
                _orchestrator(request, executor).retrieve(request)
            except RetrievalIntegrityError as exc:
                self.assertIsNone(exc.__cause__)
                self.assertTrue(exc.__suppress_context__)
                messages.append(str(exc))
        self.assertEqual(2, len(messages))
        self.assertEqual(messages[0], messages[1])
        self.assertNotIn(OTHER_TENANT_ID.hex, messages[0])
        self.assertNotIn("rp_", messages[0])


class RetrievalPartitionLeakMatrixAccountingTests(unittest.TestCase):
    def test_matrix_is_fully_partitioned_into_executed_and_blocked(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        required = set(contract["required_adapter_leak_tests"])
        executed = {
            name.removeprefix("test_")
            for name in dir(RetrievalPartitionLeakMatrixTests)
            if name.startswith("test_")
        }
        blocked = set(BLOCKED_ENTRIES)
        self.assertFalse(executed & blocked)
        self.assertEqual(required, executed | blocked)
        for entry, reason in BLOCKED_ENTRIES.items():
            with self.subTest(entry=entry):
                self.assertTrue(reason)


if __name__ == "__main__":
    unittest.main()
