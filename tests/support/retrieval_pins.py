"""Shared synthetic pins for retrieval adapter tests.

Every value is synthetic: no live Tenant, Matter, Principal, credential, or
release. Hash pins are fixed placeholder hex strings.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sklegal_retrieval.models import (
    AuthorizationPins,
    CredentialBindingPins,
    DistanceMetric,
    GraphPins,
    LexicalPins,
    ProjectionLag,
    ProjectionPins,
    QueryTemplateId,
    QueryTemplatePins,
    ReplicaPins,
    RetrievalComponent,
    RetrievalMode,
    RetrievalRequest,
    RetrievalScope,
    ScopeKind,
    SourceProvenance,
    VectorPins,
)
from sklegal_retrieval.query_templates import QUERY_TEMPLATES
from sklegal_retrieval.registry import (
    AgeQualification,
    ExtensionEvidence,
    RegistryLifecycle,
    RegistryRecord,
    SupplyChainEvidence,
)

TENANT_ID = UUID("30000000-0000-4000-8000-000000000001")
OTHER_TENANT_ID = UUID("30000000-0000-4000-8000-000000000011")
MATTER_ID = UUID("30000000-0000-4000-8000-000000000002")
OTHER_MATTER_ID = UUID("30000000-0000-4000-8000-000000000003")
PRINCIPAL_ID = UUID("30000000-0000-4000-8000-000000000004")
OTHER_PRINCIPAL_ID = UUID("30000000-0000-4000-8000-000000000005")
DECISION_ID = UUID("30000000-0000-4000-8000-000000000006")
PROJECTION_SET_ID = UUID("30000000-0000-4000-8000-000000000007")
PRIOR_PROJECTION_SET_ID = UUID("30000000-0000-4000-8000-000000000017")
REQUEST_ID = UUID("30000000-0000-4000-8000-000000000008")

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "0" * 64
FILTER_HASH = "1" * 64

SCAN_OBSERVED_AT = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def make_scope(
    *,
    tenant_id: UUID = TENANT_ID,
    scope_kind: ScopeKind = ScopeKind.MATTER,
    matter_id: UUID | None = MATTER_ID,
) -> RetrievalScope:
    return RetrievalScope(
        tenant_id=tenant_id,
        scope_kind=scope_kind,
        matter_id=matter_id,
    )


def make_authorization(
    scope: RetrievalScope,
    *,
    principal_id: UUID = PRINCIPAL_ID,
    policy_revision: str = HASH_B,
    rights_revision: str = HASH_C,
    required_core_watermark: int = 5,
) -> AuthorizationPins:
    shared = scope.scope_kind is ScopeKind.TENANT_SHARED
    return AuthorizationPins(
        principal_id=principal_id,
        scope=scope,
        authorization_decision_id=DECISION_ID,
        principal_policy_context_sha256=HASH_A,
        policy_revision=policy_revision,
        rights_revision=rights_revision,
        required_core_watermark=required_core_watermark,
        tenant_shared_policy_decision_id=DECISION_ID if shared else None,
    )


def make_projection(
    component: RetrievalComponent,
    scope: RetrievalScope,
    *,
    projection_set_id: UUID = PROJECTION_SET_ID,
    generation: int = 3,
    release_id: str = "release-1",
    watermark: int = 10,
    lag: ProjectionLag | None = None,
    replica: ReplicaPins | None = None,
    partition_suffix: str | None = None,
) -> ProjectionPins:
    suffix = (
        partition_suffix
        or {
            RetrievalComponent.LEXICAL: "1" * 32,
            RetrievalComponent.VECTOR: "2" * 32,
            RetrievalComponent.GRAPH: "3" * 32,
        }[component]
    )
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
    if lag is not None:
        common["lag"] = lag
    if replica is not None:
        common["replica"] = replica
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


def make_binding(
    scope: RetrievalScope,
    projection: ProjectionPins,
    *,
    principal_id: UUID = PRINCIPAL_ID,
    database_principal: str = "principal-db-1",
    policy_revision: str = HASH_B,
    rights_revision: str = HASH_C,
    projection_set_id: UUID | None = None,
    generation: int | None = None,
) -> CredentialBindingPins:
    return CredentialBindingPins(
        credential_ref="credential:retrieval-1",
        database_principal=database_principal,
        principal_id=principal_id,
        scope=scope,
        projection_set_id=projection_set_id or projection.projection_set_id,
        projection_generation=generation or projection.projection_generation,
        policy_revision=policy_revision,
        rights_revision=rights_revision,
        authorization_event_sequence=11,
        authorization_event_sha256=HASH_D,
    )


def make_template_pins(
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


def make_request(
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
        authorization=authorization or make_authorization(scope),
        credential_binding=binding or make_binding(scope, projections[0]),
        projections=projections,
        template=make_template_pins(template_id),
        retrieval_mode=mode,
        parameters=parameters,  # type: ignore[arg-type]
        structured_filter_sha256=FILTER_HASH,
        retrieval_adapter_version="1.0.0",
        optional_components=optional_components,
    )


def make_source(record_id: str, *, source_hash: str = HASH_A) -> SourceProvenance:
    return SourceProvenance(
        retrieval_record_id=record_id,
        source_id=f"source-{record_id}",
        source_version="1",
        source_sha256=source_hash,
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


def make_supply_chain(
    *,
    image_digest: str = HASH_A,
    postgresql_version: str = "17.7",
    postgresql_readback: str = "17.7",
    pgvector_revision: str = "v0.8.0",
    pgvector_sha256: str = HASH_C,
    pgvector_readback: str = "0.8.0",
    sbom_sha256: str = HASH_D,
    vulnerability_scan_sha256: str = HASH_E,
    advisory_revision: str = "advisory-2026-08-20",
    license_inventory_sha256: str = HASH_B,
    age_revision: str | None = None,
    age_sha256: str | None = None,
    age_readback: str | None = None,
    age_qualified: bool = False,
    age_evidence_sha256: str | None = None,
    gateway_definition_sha256: str | None = None,
    high_risk_ref: str | None = None,
    high_risk_expires: datetime | None = None,
) -> SupplyChainEvidence:
    age: ExtensionEvidence | None = None
    if age_revision is not None:
        age = ExtensionEvidence(
            source_revision=age_revision,
            source_sha256=age_sha256 or HASH_D,
            runtime_version_readback=age_readback or "1.5.0",
        )
    return SupplyChainEvidence(
        retrieval_image_digest=image_digest,
        postgresql_version=postgresql_version,
        postgresql_version_readback=postgresql_readback,
        sbom_sha256=sbom_sha256,
        vulnerability_scan_sha256=vulnerability_scan_sha256,
        vulnerability_scan_observed_at=SCAN_OBSERVED_AT,
        vulnerability_advisory_database_revision=advisory_revision,
        license_inventory_sha256=license_inventory_sha256,
        pgvector=ExtensionEvidence(
            source_revision=pgvector_revision,
            source_sha256=pgvector_sha256,
            runtime_version_readback=pgvector_readback,
        ),
        apache_age=age,
        apache_age_qualification_status=(
            AgeQualification.QUALIFIED
            if age_qualified
            else AgeQualification.UNQUALIFIED
        ),
        apache_age_qualification_evidence_sha256=(
            age_evidence_sha256 if age_qualified else None
        ),
        graph_gateway_definition_sha256=gateway_definition_sha256,
        high_risk_acceptance_ref=high_risk_ref,
        high_risk_acceptance_expires_at=high_risk_expires,
    )


def make_record(
    projection: ProjectionPins,
    *,
    lifecycle: RegistryLifecycle = RegistryLifecycle.READY,
    supply_chain: SupplyChainEvidence | None = None,
    idempotency_key: str = "activation-1",
    prior_projection_set_id: UUID | None = None,
    source_event_sequence: int = 7,
    legacy_qdrant_alias: str | None = None,
    legacy_falkordb_alias: str | None = None,
) -> RegistryRecord:
    return RegistryRecord(
        projection=projection,
        lifecycle=lifecycle,
        source_snapshot_sha256=HASH_F,
        source_event_sequence=source_event_sequence,
        source_event_sha256=HASH_A,
        policy_revision=HASH_B,
        rights_revision=HASH_C,
        supply_chain=supply_chain or make_supply_chain(),
        activation_idempotency_key=idempotency_key,
        content_digest=HASH_E,
        prior_projection_set_id=prior_projection_set_id,
        legacy_qdrant_collection_alias=legacy_qdrant_alias,
        legacy_falkordb_graph_alias=legacy_falkordb_alias,
    )
