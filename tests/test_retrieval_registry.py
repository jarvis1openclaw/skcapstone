"""Leak and qualification tests for the projection registry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sklegal_retrieval.errors import (
    RetrievalRequestError,
    RetrievalUnavailableError,
)
from sklegal_retrieval.fake import FakeRegistryStore
from sklegal_retrieval.models import RetrievalComponent, ScopeKind
from sklegal_retrieval.registry import (
    ActivationKey,
    AgeQualification,
    ProjectionRegistry,
    RegistryLifecycle,
    RegistryRecord,
    RetirementGates,
    SupplyChainEvidence,
)

from tests.support.retrieval_pins import (
    HASH_A,
    NOW,
    OTHER_TENANT_ID,
    PRIOR_PROJECTION_SET_ID,
    PROJECTION_SET_ID,
    SCAN_OBSERVED_AT,
    make_projection,
    make_record,
    make_scope,
    make_supply_chain,
)


def _ready_set(
    *,
    scope: object = None,
    set_id: object = PROJECTION_SET_ID,
    generation: int = 3,
    release_id: str = "release-1",
    supply_chain: SupplyChainEvidence | None = None,
    idempotency_key: str = "activation-1",
    prior: object = None,
    with_graph: bool = False,
    lifecycle: RegistryLifecycle = RegistryLifecycle.READY,
) -> tuple[RegistryRecord, ...]:
    actual_scope = scope if scope is not None else make_scope()
    components = [RetrievalComponent.LEXICAL, RetrievalComponent.VECTOR]
    if with_graph:
        components.append(RetrievalComponent.GRAPH)
    return tuple(
        make_record(
            make_projection(
                component,
                actual_scope,  # type: ignore[arg-type]
                projection_set_id=set_id,  # type: ignore[arg-type]
                generation=generation,
                release_id=release_id,
            ),
            lifecycle=lifecycle,
            supply_chain=supply_chain,
            idempotency_key=idempotency_key,
            prior_projection_set_id=prior,  # type: ignore[arg-type]
        )
        for component in components
    )


def _active_set(**kwargs: object) -> tuple[RegistryRecord, ...]:
    kwargs.setdefault("lifecycle", RegistryLifecycle.ACTIVE)
    return _ready_set(**kwargs)  # type: ignore[arg-type]


def _registry(*records: RegistryRecord) -> tuple[ProjectionRegistry, FakeRegistryStore]:
    store = FakeRegistryStore(records)
    return ProjectionRegistry(store), store


def test_activation_cutover_is_atomic_and_selectable() -> None:
    scope = make_scope()
    prior = _active_set(
        scope=scope, set_id=PRIOR_PROJECTION_SET_ID, generation=2, idempotency_key="a0"
    )
    registry, _store = _registry(*prior)
    candidate = _ready_set(scope=scope, prior=PRIOR_PROJECTION_SET_ID)
    registry.activate(candidate, running=make_supply_chain(), now=NOW)
    selected = registry.select_active(
        scope, (RetrievalComponent.LEXICAL, RetrievalComponent.VECTOR)
    )
    assert {item.projection_set_id for item in selected} == {PROJECTION_SET_ID}
    lifecycles = {
        record.lifecycle
        for record in registry._store.snapshot()
        if record.projection.projection_set_id == PROJECTION_SET_ID
    }
    assert lifecycles == {RegistryLifecycle.ACTIVE}
    prior_states = {
        record.lifecycle
        for record in registry._store.snapshot()
        if record.projection.projection_set_id == PRIOR_PROJECTION_SET_ID
    }
    assert prior_states == {RegistryLifecycle.RETIRING}


def test_activation_replay_with_the_same_idempotency_key_is_a_noop() -> None:
    registry, store = _registry()
    candidate = _ready_set()
    first = registry.activate(candidate, running=make_supply_chain(), now=NOW)
    replayed = registry.activate(candidate, running=make_supply_chain(), now=NOW)
    assert replayed == first == store.snapshot()
    active = [
        record
        for record in store.snapshot()
        if record.lifecycle is RegistryLifecycle.ACTIVE
    ]
    assert len(active) == 2


def test_activation_denies_mixed_sets_releases_generations_and_scopes() -> None:
    registry, _store = _registry()
    base = _ready_set()
    other_set = make_record(
        make_projection(
            RetrievalComponent.VECTOR,
            make_scope(),
            projection_set_id=PRIOR_PROJECTION_SET_ID,
        )
    )
    other_release = make_record(
        make_projection(RetrievalComponent.VECTOR, make_scope(), release_id="release-2")
    )
    other_generation = make_record(
        make_projection(RetrievalComponent.VECTOR, make_scope(), generation=4)
    )
    other_scope = make_record(
        make_projection(
            RetrievalComponent.VECTOR, make_scope(tenant_id=OTHER_TENANT_ID)
        )
    )
    for replacement in (other_set, other_release, other_generation, other_scope):
        with pytest.raises(RetrievalRequestError):
            registry.activate(
                (base[0], replacement), running=make_supply_chain(), now=NOW
            )


def test_activation_denies_partial_candidate_sets() -> None:
    registry, _store = _registry()
    lexical_only = _ready_set()[:1]
    with pytest.raises(RetrievalUnavailableError):
        registry.activate(lexical_only, running=make_supply_chain(), now=NOW)
    graph_only = _ready_set(with_graph=True)[2:]
    with pytest.raises(RetrievalUnavailableError):
        registry.activate(graph_only, running=make_supply_chain(), now=NOW)


def test_activation_denies_non_ready_candidates() -> None:
    registry, _store = _registry()
    for lifecycle in (
        RegistryLifecycle.BUILDING,
        RegistryLifecycle.ACTIVE,
        RegistryLifecycle.RETIRING,
        RegistryLifecycle.RETIRED,
        RegistryLifecycle.REJECTED,
        RegistryLifecycle.FAILED,
    ):
        with pytest.raises(RetrievalUnavailableError):
            registry.activate(
                _ready_set(lifecycle=lifecycle),
                running=make_supply_chain(),
                now=NOW,
            )


def test_failed_compare_and_swap_leaves_the_previous_active_set_untouched() -> None:
    scope = make_scope()
    prior = _active_set(
        scope=scope, set_id=PRIOR_PROJECTION_SET_ID, generation=2, idempotency_key="a0"
    )
    registry, store = _registry(*prior)

    class _RacingStore:
        def snapshot(self) -> tuple[RegistryRecord, ...]:
            return store.snapshot()

        def compare_and_swap(self, **kwargs: object) -> None:
            return None

    racing = ProjectionRegistry(_RacingStore())  # type: ignore[arg-type]
    with pytest.raises(RetrievalUnavailableError):
        racing.activate(_ready_set(scope=scope), running=make_supply_chain(), now=NOW)
    assert store.snapshot() == prior


def test_store_outage_denies_selection_and_activation() -> None:
    store = FakeRegistryStore(_active_set(), available=False)
    registry = ProjectionRegistry(store)
    scope = make_scope()
    with pytest.raises(RetrievalUnavailableError):
        registry.select_active(scope, (RetrievalComponent.LEXICAL,))
    with pytest.raises(RetrievalUnavailableError):
        registry.activate(_ready_set(), running=make_supply_chain(), now=NOW)


@pytest.mark.parametrize(
    "running",
    [
        make_supply_chain(image_digest="1" * 64),
        make_supply_chain(postgresql_readback="17.6"),
        make_supply_chain(postgresql_version="17.6"),
        make_supply_chain(pgvector_revision="v0.7.4"),
        make_supply_chain(pgvector_sha256="2" * 64),
        make_supply_chain(pgvector_readback="0.7.4"),
        make_supply_chain(sbom_sha256="3" * 64),
        make_supply_chain(vulnerability_scan_sha256="4" * 64),
        make_supply_chain(advisory_revision="advisory-2026-08-19"),
        make_supply_chain(license_inventory_sha256="5" * 64),
    ],
    ids=[
        "image_digest",
        "postgresql_readback",
        "postgresql_version",
        "pgvector_revision",
        "pgvector_sha256",
        "pgvector_readback",
        "sbom",
        "vulnerability_scan",
        "advisory_revision",
        "license_inventory",
    ],
)
def test_supply_chain_mismatch_denies_activation(
    running: SupplyChainEvidence,
) -> None:
    registry, store = _registry()
    with pytest.raises(RetrievalUnavailableError):
        registry.activate(_ready_set(), running=running, now=NOW)
    assert store.snapshot() == ()


def test_non_sha256_extension_checksum_cannot_be_constructed() -> None:
    with pytest.raises(ValidationError):
        make_supply_chain(pgvector_sha256="d41d8cd98f00b204e9800998ecf8427e")


def test_expired_high_risk_acceptance_denies_activation() -> None:
    expired = make_supply_chain(
        high_risk_ref="risk-acceptance-1",
        high_risk_expires=NOW - timedelta(seconds=1),
    )
    registry, _store = _registry()
    with pytest.raises(RetrievalUnavailableError):
        registry.activate(_ready_set(supply_chain=expired), running=expired, now=NOW)
    valid = make_supply_chain(
        high_risk_ref="risk-acceptance-1",
        high_risk_expires=NOW + timedelta(days=30),
    )
    registry.activate(_ready_set(supply_chain=valid), running=valid, now=NOW)


def test_supply_chain_evidence_rejects_inconsistent_pins() -> None:
    with pytest.raises(ValidationError):
        make_supply_chain(high_risk_ref="risk-acceptance-1")
    with pytest.raises(ValidationError):
        make_supply_chain(
            age_revision="v1.5.0", age_qualified=True, age_evidence_sha256=None
        )
    with pytest.raises(ValidationError):
        SupplyChainEvidence.model_validate(
            {
                **make_supply_chain().model_dump(),
                "vulnerability_scan_observed_at": datetime(2026, 8, 20),
            }
        )


def test_unqualified_age_denies_graph_set_activation() -> None:
    registry, store = _registry()
    candidate = _ready_set(with_graph=True)
    with pytest.raises(RetrievalUnavailableError):
        registry.activate(candidate, running=make_supply_chain(), now=NOW)
    assert store.snapshot() == ()


def test_qualified_age_with_gateway_hash_mismatch_denies_activation() -> None:
    qualified = make_supply_chain(
        age_revision="v1.5.0-pg17",
        age_sha256="6" * 64,
        age_qualified=True,
        age_evidence_sha256="7" * 64,
        gateway_definition_sha256="8" * 64,
    )
    registry, _store = _registry()
    with pytest.raises(RetrievalUnavailableError):
        registry.activate(
            _ready_set(with_graph=True, supply_chain=qualified),
            running=qualified,
            now=NOW,
        )


def test_qualified_age_with_matching_gateway_hash_activates() -> None:
    qualified = make_supply_chain(
        age_revision="v1.5.0-pg17",
        age_sha256="6" * 64,
        age_qualified=True,
        age_evidence_sha256="7" * 64,
        gateway_definition_sha256=HASH_A,
    )
    registry, _store = _registry()
    registry.activate(
        _ready_set(with_graph=True, supply_chain=qualified),
        running=qualified,
        now=NOW,
    )
    selected = registry.select_active(make_scope(), (RetrievalComponent.GRAPH,))
    assert selected[0].component is RetrievalComponent.GRAPH


def test_age_evidence_mismatch_denies_activation() -> None:
    recorded = make_supply_chain(
        age_revision="v1.5.0-pg17",
        age_sha256="6" * 64,
        age_qualified=True,
        age_evidence_sha256="7" * 64,
        gateway_definition_sha256=HASH_A,
    )
    running = make_supply_chain(
        age_revision="v1.5.0-pg17",
        age_sha256="9" * 64,
        age_qualified=True,
        age_evidence_sha256="7" * 64,
        gateway_definition_sha256=HASH_A,
    )
    registry, _store = _registry()
    with pytest.raises(RetrievalUnavailableError):
        registry.activate(
            _ready_set(with_graph=True, supply_chain=recorded),
            running=running,
            now=NOW,
        )


def test_graph_addition_to_an_active_set_requires_a_new_set_cutover() -> None:
    qualified = make_supply_chain(
        age_revision="v1.5.0-pg17",
        age_qualified=True,
        age_evidence_sha256="7" * 64,
        gateway_definition_sha256=HASH_A,
    )
    registry, _store = _registry()
    registry.activate(_ready_set(), running=make_supply_chain(), now=NOW)
    same_set_with_graph = _ready_set(
        with_graph=True, idempotency_key="activation-2", supply_chain=qualified
    )
    with pytest.raises(RetrievalRequestError):
        registry.activate(same_set_with_graph, running=qualified, now=NOW)


def test_active_manifest_mutation_is_denied() -> None:
    registry, store = _registry()
    registry.activate(_ready_set(), running=make_supply_chain(), now=NOW)
    active = [
        record
        for record in store.snapshot()
        if record.lifecycle is RegistryLifecycle.ACTIVE
    ][0]
    with pytest.raises(ValueError, match="unvalidated retrieval copy"):
        active.model_copy(update={"lifecycle": RegistryLifecycle.RETIRED})
    tampered = RegistryRecord.model_validate(
        {**active.model_dump(), "content_digest": "8" * 64}
    )
    result = store.compare_and_swap(
        key=ActivationKey.from_scope(make_scope()),
        expected_active=(tampered,),
        next_records=(tampered,),
        idempotency_key="activation-tampered",
    )
    assert result is None


def test_rollback_restores_the_prior_generation_atomically() -> None:
    scope = make_scope()
    prior = _active_set(
        scope=scope, set_id=PRIOR_PROJECTION_SET_ID, generation=2, idempotency_key="a0"
    )
    registry, store = _registry(*prior)
    registry.activate(
        _ready_set(scope=scope, prior=PRIOR_PROJECTION_SET_ID),
        running=make_supply_chain(),
        now=NOW,
    )
    registry.rollback(scope)
    selected = registry.select_active(
        scope, (RetrievalComponent.LEXICAL, RetrievalComponent.VECTOR)
    )
    assert {item.projection_set_id for item in selected} == {PRIOR_PROJECTION_SET_ID}
    assert {item.projection_generation for item in selected} == {2}
    states = {
        record.projection.projection_set_id: record.lifecycle
        for record in store.snapshot()
    }
    assert states[PRIOR_PROJECTION_SET_ID] is RegistryLifecycle.ACTIVE
    assert states[PROJECTION_SET_ID] is RegistryLifecycle.RETIRING


def test_rollback_without_a_prior_set_is_denied() -> None:
    registry, _store = _registry()
    registry.activate(_ready_set(), running=make_supply_chain(), now=NOW)
    with pytest.raises(RetrievalUnavailableError):
        registry.rollback(make_scope())
    with pytest.raises(RetrievalUnavailableError):
        ProjectionRegistry(FakeRegistryStore()).rollback(make_scope())


def test_retired_and_candidate_generations_are_never_selected() -> None:
    scope = make_scope()
    for lifecycle in (
        RegistryLifecycle.BUILDING,
        RegistryLifecycle.READY,
        RegistryLifecycle.RETIRING,
        RegistryLifecycle.RETIRED,
        RegistryLifecycle.REJECTED,
        RegistryLifecycle.FAILED,
    ):
        registry, _store = _registry(*_ready_set(scope=scope, lifecycle=lifecycle))
        with pytest.raises(RetrievalUnavailableError):
            registry.select_active(scope, (RetrievalComponent.LEXICAL,))


def test_cross_tenant_and_mixed_active_sets_are_denied() -> None:
    scope = make_scope()
    other = make_scope(tenant_id=OTHER_TENANT_ID)
    registry, _store = _registry(*_active_set(scope=other))
    with pytest.raises(RetrievalUnavailableError):
        registry.select_active(scope, (RetrievalComponent.LEXICAL,))

    mixed = (
        _active_set()[:1]
        + _active_set(set_id=PRIOR_PROJECTION_SET_ID, idempotency_key="a9")[1:]
    )
    registry, _store = _registry(*mixed)
    with pytest.raises(RetrievalUnavailableError):
        registry.select_active(
            scope, (RetrievalComponent.LEXICAL, RetrievalComponent.VECTOR)
        )


def test_graph_from_a_nonselected_projection_set_is_denied() -> None:
    scope = make_scope()
    relational = _active_set(scope=scope, idempotency_key="a1")
    graph = _active_set(
        scope=scope,
        set_id=PRIOR_PROJECTION_SET_ID,
        with_graph=True,
        idempotency_key="a2",
    )[2:]
    registry, _store = _registry(*(relational + graph))
    with pytest.raises(RetrievalUnavailableError):
        registry.select_active(scope, (RetrievalComponent.GRAPH,))


def test_legacy_aliases_are_provenance_only_and_never_route() -> None:
    scope = make_scope()
    records = _active_set(scope=scope)
    aliased = tuple(
        make_record(
            record.projection,
            lifecycle=RegistryLifecycle.ACTIVE,
            idempotency_key="a1",
            legacy_qdrant_alias="qdrant-legacy-collection",
            legacy_falkordb_alias=(
                "falkordb-legacy-graph"
                if record.projection.component is RetrievalComponent.GRAPH
                else None
            ),
        )
        for record in records
    )
    registry, _store = _registry(*aliased)
    selected = registry.select_active(
        scope, (RetrievalComponent.LEXICAL, RetrievalComponent.VECTOR)
    )
    assert len(selected) == 2
    with pytest.raises(RetrievalRequestError):
        registry.resolve_legacy_alias("qdrant-legacy-collection")
    with pytest.raises(RetrievalRequestError):
        registry.resolve_legacy_alias("falkordb-legacy-graph")


def _retired_shared_partition_records() -> tuple[RegistryRecord, ...]:
    partition_suffix = "a" * 32
    first_scope = make_scope()
    second_scope = make_scope(matter_id=None, scope_kind=ScopeKind.TENANT_SHARED)
    records: list[RegistryRecord] = []
    for scope in (first_scope, second_scope):
        for component in (RetrievalComponent.LEXICAL, RetrievalComponent.VECTOR):
            records.append(
                make_record(
                    make_projection(
                        component,
                        scope,
                        partition_suffix=(
                            partition_suffix
                            if component is RetrievalComponent.LEXICAL
                            else "b" * 32
                        ),
                    ),
                    lifecycle=RegistryLifecycle.RETIRED,
                )
            )
    return tuple(records)


def test_shared_physical_generation_deletion_gate() -> None:
    records = _retired_shared_partition_records()
    registry, _store = _registry(*records)
    partition = records[0].projection.physical_partition_id
    referencing = tuple(
        record
        for record in records
        if record.projection.physical_partition_id == partition
    )
    full = tuple(RetirementGates(True, True, True, True) for _ in referencing)
    assert registry.deletion_decision(
        physical_partition_id=partition, gates=full
    ).allowed

    for bad in (
        RetirementGates(False, True, True, True),
        RetirementGates(True, False, True, True),
        RetirementGates(True, True, False, True),
        RetirementGates(True, True, True, False),
    ):
        gates = (bad,) + tuple(
            RetirementGates(True, True, True, True) for _ in referencing[1:]
        )
        decision = registry.deletion_decision(
            physical_partition_id=partition, gates=gates
        )
        assert not decision.allowed
        assert "scope_gates_unsatisfied" in decision.denied_reasons
    partial = registry.deletion_decision(
        physical_partition_id=partition, gates=full[:1]
    )
    assert not partial.allowed
    unknown = registry.deletion_decision(
        physical_partition_id="rp_" + "f" * 32, gates=()
    )
    assert not unknown.allowed


def test_deletion_blocked_while_any_referencing_scope_is_not_retired() -> None:
    records = _retired_shared_partition_records()
    partition = records[0].projection.physical_partition_id
    active_same_partition = make_record(
        records[0].projection, lifecycle=RegistryLifecycle.ACTIVE
    )
    registry, _store = _registry(active_same_partition, *records[1:])
    referencing = tuple(
        record
        for record in registry._store.snapshot()
        if record.projection.physical_partition_id == partition
    )
    decision = registry.deletion_decision(
        physical_partition_id=partition,
        gates=tuple(RetirementGates(True, True, True, True) for _ in referencing),
    )
    assert not decision.allowed
    assert "referencing_scope_not_retired" in decision.denied_reasons


def test_denial_shape_is_identical_for_missing_and_denied_selection() -> None:
    registry, _store = _registry()
    scope = make_scope()
    with pytest.raises(RetrievalUnavailableError) as missing:
        registry.select_active(scope, (RetrievalComponent.LEXICAL,))
    with pytest.raises(RetrievalUnavailableError) as denied:
        registry.select_active(
            make_scope(tenant_id=OTHER_TENANT_ID), (RetrievalComponent.LEXICAL,)
        )
    assert str(missing.value) == str(denied.value)
    assert missing.value.public_error() == denied.value.public_error()


def test_age_qualification_states_are_closed() -> None:
    assert set(AgeQualification) == {
        AgeQualification.QUALIFIED,
        AgeQualification.UNQUALIFIED,
    }
    assert SCAN_OBSERVED_AT.tzinfo is UTC
