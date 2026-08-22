"""Leak tests for the CapAuth-mediated credential broker and pool."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sklegal_retrieval.broker import (
    RetrievalConnectionPool,
    RetrievalCredentialBroker,
    RetrievalPoolKey,
)
from sklegal_retrieval.errors import RetrievalAuthorizationError
from sklegal_retrieval.fake import (
    FakeActiveProjectionRegistry,
    FakeAuthorizer,
    FakeBrokerBindingStore,
    FakeRetrievalExecutor,
    FakeRetrievalRecord,
)
from sklegal_retrieval.models import (
    LexicalSearchParameters,
    QueryTemplateId,
    RetrievalComponent,
    RetrievalMode,
    RetrievalRequest,
)
from sklegal_retrieval.orchestrator import RetrievalOrchestrator

from tests.support.retrieval_pins import (
    MATTER_ID,
    OTHER_MATTER_ID,
    OTHER_PRINCIPAL_ID,
    OTHER_TENANT_ID,
    PRINCIPAL_ID,
    PRIOR_PROJECTION_SET_ID,
    PROJECTION_SET_ID,
    TENANT_ID,
    make_authorization,
    make_binding,
    make_projection,
    make_request,
    make_scope,
    make_source,
)


def _lexical_request() -> RetrievalRequest:
    scope = make_scope()
    projection = make_projection(RetrievalComponent.LEXICAL, scope)
    return make_request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="liberty mutual"),
        projections=(projection,),
    )


@dataclass(frozen=True, slots=True)
class _Connection:
    key: RetrievalPoolKey

    @property
    def authenticated_key(self) -> RetrievalPoolKey:
        return self.key


@dataclass(frozen=True, slots=True)
class _AnonymousConnection:
    @property
    def authenticated_key(self) -> object:
        return "not-a-pool-key"


def _pool_key(**overrides: object) -> RetrievalPoolKey:
    values: dict[str, object] = {
        "database_principal": "principal-db-1",
        "tenant_id": TENANT_ID,
        "scope_kind": make_scope().scope_kind,
        "matter_id": MATTER_ID,
        "projection_set_id": PROJECTION_SET_ID,
        "projection_generation": 3,
    }
    values.update(overrides)
    return RetrievalPoolKey(**values)  # type: ignore[arg-type]


def test_broker_resolves_current_binding_without_exposing_a_credential() -> None:
    request = _lexical_request()
    store = FakeBrokerBindingStore(
        {request.credential_binding.database_principal: request.credential_binding}
    )
    broker = RetrievalCredentialBroker(store)
    resolved = broker.resolve(request)
    assert resolved == request.credential_binding
    assert store.call_count == 1


def test_broker_slots_into_orchestrator_after_authorization() -> None:
    request = _lexical_request()
    event_log: list[str] = []
    store = FakeBrokerBindingStore(
        {request.credential_binding.database_principal: request.credential_binding}
    )
    broker = RetrievalCredentialBroker(store)

    class _LoggedBroker:
        def resolve(self, inner: RetrievalRequest) -> object:
            event_log.append("credential_binding")
            return broker.resolve(inner)

    class _LoggedRegistry:
        def __init__(self, projections: tuple[object, ...]) -> None:
            self._inner = FakeActiveProjectionRegistry(projections)  # type: ignore[arg-type]

        def select_active(
            self, scope: object, components: object, **kw: object
        ) -> object:
            event_log.append("registry")
            return self._inner.select_active(scope, components, **kw)  # type: ignore[arg-type]

    executor = FakeRetrievalExecutor(
        [
            FakeRetrievalRecord(
                projection=request.projections[0],
                source=make_source("record-1"),
                content="liberty mutual settlement packet",
            )
        ]
    )
    orchestrator = RetrievalOrchestrator(
        authorizer=FakeAuthorizer(request.authorization, event_log=event_log),
        credential_resolver=_LoggedBroker(),
        registry=_LoggedRegistry(request.projections),
        executor=executor,
    )
    result = orchestrator.retrieve(request)
    assert len(result.hits) == 1
    assert event_log[0] == "authorization"
    assert event_log.index("credential_binding") < event_log.index("registry")


def test_broker_denies_a_binding_for_the_wrong_principal() -> None:
    request = _lexical_request()
    wrong = make_binding(
        request.scope,
        request.projections[0],
        principal_id=OTHER_PRINCIPAL_ID,
    )
    broker = RetrievalCredentialBroker(
        FakeBrokerBindingStore({wrong.database_principal: wrong})
    )
    with pytest.raises(RetrievalAuthorizationError):
        broker.resolve(request)


def test_broker_denies_a_revoked_or_missing_binding() -> None:
    request = _lexical_request()
    broker = RetrievalCredentialBroker(FakeBrokerBindingStore({}))
    with pytest.raises(RetrievalAuthorizationError):
        broker.resolve(request)


def test_broker_fails_closed_when_the_store_is_unavailable() -> None:
    request = _lexical_request()
    broker = RetrievalCredentialBroker(FakeBrokerBindingStore({}, available=False))
    with pytest.raises(RetrievalAuthorizationError):
        broker.resolve(request)


def test_broker_denies_a_stale_policy_binding() -> None:
    request = _lexical_request()
    stale = make_binding(
        request.scope,
        request.projections[0],
        policy_revision="9" * 64,
    )
    broker = RetrievalCredentialBroker(
        FakeBrokerBindingStore({stale.database_principal: stale})
    )
    with pytest.raises(RetrievalAuthorizationError):
        broker.resolve(request)


def test_broker_denies_a_binding_for_the_wrong_matter_or_generation() -> None:
    request = _lexical_request()
    wrong_matter = make_binding(
        make_scope(matter_id=OTHER_MATTER_ID),
        request.projections[0],
    )
    broker = RetrievalCredentialBroker(
        FakeBrokerBindingStore({wrong_matter.database_principal: wrong_matter})
    )
    with pytest.raises(RetrievalAuthorizationError):
        broker.resolve(request)

    wrong_generation = make_binding(
        request.scope,
        request.projections[0],
        generation=4,
    )
    broker = RetrievalCredentialBroker(
        FakeBrokerBindingStore({wrong_generation.database_principal: wrong_generation})
    )
    with pytest.raises(RetrievalAuthorizationError):
        broker.resolve(request)


def test_shared_runtime_login_is_rejected() -> None:
    scope = make_scope()
    projection = make_projection(RetrievalComponent.LEXICAL, scope)
    first = make_request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="liberty mutual"),
        projections=(projection,),
    )
    second = make_request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="liberty mutual"),
        projections=(projection,),
        authorization=make_authorization(scope, principal_id=OTHER_PRINCIPAL_ID),
        binding=make_binding(scope, projection, principal_id=OTHER_PRINCIPAL_ID),
    )
    store = FakeBrokerBindingStore({"principal-db-1": first.credential_binding})
    broker = RetrievalCredentialBroker(store)
    broker.resolve(first)
    store.update("principal-db-1", second.credential_binding)
    with pytest.raises(RetrievalAuthorizationError):
        broker.resolve(second)


def test_pool_lends_only_the_exactly_matching_connection() -> None:
    pool = RetrievalConnectionPool()
    key = _pool_key()
    pool.lend(key, _Connection(key))
    pool.lend(key, _Connection(key))


def test_pool_rejects_an_unauthenticated_connection() -> None:
    pool = RetrievalConnectionPool()
    with pytest.raises(RetrievalAuthorizationError):
        pool.lend(_pool_key(), _AnonymousConnection())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "override",
    [
        {"database_principal": "principal-db-2"},
        {"tenant_id": OTHER_TENANT_ID},
        {"matter_id": OTHER_MATTER_ID},
        {"projection_set_id": PRIOR_PROJECTION_SET_ID},
        {"projection_generation": 4},
    ],
)
def test_pool_denies_reuse_across_principal_scope_set_or_generation(
    override: dict[str, object],
) -> None:
    pool = RetrievalConnectionPool()
    key = _pool_key()
    pool.lend(key, _Connection(key))
    with pytest.raises(RetrievalAuthorizationError):
        pool.lend(_pool_key(**override), _Connection(key))
    with pytest.raises(RetrievalAuthorizationError):
        pool.lend(key, _Connection(_pool_key(**override)))


def test_pool_key_matches_the_contract_fields() -> None:
    key = _pool_key()
    assert set(key.model_dump()) == {
        "database_principal",
        "tenant_id",
        "scope_kind",
        "matter_id",
        "projection_set_id",
        "projection_generation",
    }
    assert key.matter_id == MATTER_ID
    assert key.projection_set_id == PROJECTION_SET_ID
    assert PRINCIPAL_ID != OTHER_PRINCIPAL_ID
