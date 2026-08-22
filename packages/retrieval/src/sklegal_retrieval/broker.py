"""CapAuth-mediated credential broker and connection pool keying.

The broker resolves the database-owned credential binding only after the
orchestrator has authorized the request. It never returns a raw credential:
callers receive only the opaque binding pins already present on the request.
The pool keys authenticated connections by the exact contract pool key and
refuses any reuse across Principal, scope, projection set, or generation.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from .errors import RetrievalAuthorizationError
from .models import (
    CredentialBindingPins,
    OpaqueId,
    RetrievalRequest,
    RetrievalScope,
    RetrievalValue,
    ScopeKind,
)


class BrokerBindingStore(Protocol):
    """Database-owned binding mapping delivered from the core outbox.

    A revoked or absent binding is indistinguishable from a missing one: the
    store returns ``None`` and the broker fails closed.
    """

    def current_binding(self, database_principal: str) -> CredentialBindingPins | None:
        """Return the current binding for one opaque database Principal."""


class RetrievalPoolKey(RetrievalValue):
    """Exact connection-pool key from the partition contract."""

    database_principal: OpaqueId
    tenant_id: UUID
    scope_kind: ScopeKind
    matter_id: UUID | None
    projection_set_id: UUID
    projection_generation: int

    @classmethod
    def from_scope(
        cls,
        *,
        database_principal: str,
        scope: RetrievalScope,
        projection_set_id: UUID,
        projection_generation: int,
    ) -> RetrievalPoolKey:
        return cls(
            database_principal=database_principal,
            tenant_id=scope.tenant_id,
            scope_kind=scope.scope_kind,
            matter_id=scope.matter_id,
            projection_set_id=projection_set_id,
            projection_generation=projection_generation,
        )


class PooledConnection(Protocol):
    """One authenticated connection pinned to exactly one pool key."""

    @property
    def authenticated_key(self) -> RetrievalPoolKey:
        """Return the identity this connection was authenticated under."""


class RetrievalConnectionPool:
    """Lend authenticated connections without cross-boundary reuse."""

    def __init__(self) -> None:
        self._lent: dict[int, RetrievalPoolKey] = {}

    def lend(self, key: RetrievalPoolKey, connection: PooledConnection) -> None:
        """Register one connection for one exact key or fail closed.

        A connection authenticated under any other Principal, Tenant, scope
        kind, Matter, projection set, or generation can never serve this key.
        """

        if not isinstance(key, RetrievalPoolKey):
            raise RetrievalAuthorizationError("connection pool key is invalid")
        authenticated = connection.authenticated_key
        if not isinstance(authenticated, RetrievalPoolKey):
            raise RetrievalAuthorizationError(
                "connection has no authenticated pool identity"
            )
        if authenticated != key:
            raise RetrievalAuthorizationError(
                "connection reuse across Principal, scope, or generation is denied"
            )
        marker = id(connection)
        prior = self._lent.setdefault(marker, authenticated)
        if prior != key:
            raise RetrievalAuthorizationError(
                "connection was already lent under a different pool key"
            )


class RetrievalCredentialBroker:
    """Resolve database-owned bindings after policy approval, fail closed."""

    def __init__(self, store: BrokerBindingStore) -> None:
        self._store = store
        self._login_owners: dict[str, tuple[object, ...]] = {}

    def resolve(self, request: RetrievalRequest) -> CredentialBindingPins:
        """Return the current binding or deny without exposing a credential."""

        expected = request.credential_binding
        binding: CredentialBindingPins | None = None
        try:
            binding = self._store.current_binding(expected.database_principal)
        except Exception:
            binding = None
        if not isinstance(binding, CredentialBindingPins):
            raise RetrievalAuthorizationError(
                "current retrieval credential binding is unavailable",
                request_id=request.request_id,
            ) from None
        if binding != expected:
            raise RetrievalAuthorizationError(
                "current retrieval credential binding does not match the request",
                request_id=request.request_id,
            )
        self._reject_shared_login(binding, request=request)
        return binding

    def _reject_shared_login(
        self,
        binding: CredentialBindingPins,
        *,
        request: RetrievalRequest,
    ) -> None:
        owner = (
            binding.principal_id,
            binding.scope.tenant_id,
            binding.scope.scope_kind,
            binding.scope.matter_id,
            binding.projection_set_id,
            binding.projection_generation,
        )
        principal = binding.database_principal
        prior = self._login_owners.setdefault(principal, owner)
        if prior != owner:
            raise RetrievalAuthorizationError(
                "shared runtime login is rejected",
                request_id=request.request_id,
            )


__all__ = [
    "BrokerBindingStore",
    "PooledConnection",
    "RetrievalConnectionPool",
    "RetrievalCredentialBroker",
    "RetrievalPoolKey",
]
