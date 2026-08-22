"""Driver-neutral projection registry: activation, rollback, and retirement.

The registry is owned by ``sklegal-core-pg``. This module implements the
activation compare-and-swap, supply-chain evidence gate, atomic rollback, and
retirement deletion gates from the tenant partition contract without any
database driver. Stores supply atomic snapshots and compare-and-swap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, Self
from uuid import UUID

from pydantic import Field, model_validator

from .errors import (
    RetrievalRequestError,
    RetrievalUnavailableError,
)
from .models import (
    OpaqueId,
    ProjectionPins,
    RetrievalComponent,
    RetrievalScope,
    RetrievalValue,
    SafeVersion,
    ScopeKind,
    Sha256,
)


class RegistryLifecycle(StrEnum):
    BUILDING = "building"
    READY = "ready"
    ACTIVE = "active"
    RETIRING = "retiring"
    RETIRED = "retired"
    REJECTED = "rejected"
    FAILED = "failed"


class AgeQualification(StrEnum):
    UNQUALIFIED = "unqualified"
    QUALIFIED = "qualified"


class ExtensionEvidence(RetrievalValue):
    """Pinned source revision, checksum, and runtime readback for one extension."""

    source_revision: SafeVersion
    source_sha256: Sha256
    runtime_version_readback: SafeVersion


class SupplyChainEvidence(RetrievalValue):
    """Exact image, extension, and scan pins required before activation."""

    retrieval_image_digest: Sha256
    postgresql_version: SafeVersion
    postgresql_version_readback: SafeVersion
    sbom_sha256: Sha256
    vulnerability_scan_sha256: Sha256
    vulnerability_scan_observed_at: datetime
    vulnerability_advisory_database_revision: SafeVersion
    license_inventory_sha256: Sha256
    pgvector: ExtensionEvidence
    apache_age: ExtensionEvidence | None = None
    apache_age_qualification_status: AgeQualification = AgeQualification.UNQUALIFIED
    apache_age_qualification_evidence_sha256: Sha256 | None = None
    graph_gateway_definition_sha256: Sha256 | None = None
    high_risk_acceptance_ref: OpaqueId | None = None
    high_risk_acceptance_expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_evidence_consistency(self) -> Self:
        for value in (
            self.vulnerability_scan_observed_at,
            self.high_risk_acceptance_expires_at,
        ):
            if value is not None and value.tzinfo is None:
                raise ValueError("supply-chain evidence times must be timezone aware")
        qualified = self.apache_age_qualification_status is AgeQualification.QUALIFIED
        if qualified != (
            self.apache_age is not None
            and self.apache_age_qualification_evidence_sha256 is not None
        ):
            raise ValueError(
                "qualified AGE evidence requires the extension pins and evidence hash"
            )
        if (self.high_risk_acceptance_ref is None) != (
            self.high_risk_acceptance_expires_at is None
        ):
            raise ValueError(
                "high-risk acceptance requires both a reference and an expiry"
            )
        return self


class RegistryRecord(RetrievalValue):
    """One immutable registry record for one projection component."""

    projection: ProjectionPins
    lifecycle: RegistryLifecycle
    source_snapshot_sha256: Sha256
    source_event_sequence: int = Field(ge=1)
    source_event_sha256: Sha256
    policy_revision: Sha256
    rights_revision: Sha256
    supply_chain: SupplyChainEvidence
    activation_idempotency_key: OpaqueId
    content_digest: Sha256
    prior_projection_set_id: UUID | None = None
    legacy_qdrant_collection_alias: OpaqueId | None = None
    legacy_falkordb_graph_alias: OpaqueId | None = None

    @model_validator(mode="after")
    def validate_graph_alias_shape(self) -> Self:
        if self.legacy_falkordb_graph_alias is not None and (
            self.projection.component is not RetrievalComponent.GRAPH
        ):
            raise ValueError("a legacy graph alias requires a graph projection")
        return self


@dataclass(frozen=True, slots=True)
class ActivationKey:
    """Unique active-set key: Tenant, scope kind, and Matter or null."""

    tenant_id: UUID
    scope_kind: ScopeKind
    matter_id: UUID | None

    @classmethod
    def from_scope(cls, scope: RetrievalScope) -> ActivationKey:
        return cls(
            tenant_id=scope.tenant_id,
            scope_kind=scope.scope_kind,
            matter_id=scope.matter_id,
        )


@dataclass(frozen=True, slots=True)
class RetirementGates:
    """Per-scope human and policy gates required before physical deletion."""

    retention_clear: bool
    legal_hold_clear: bool
    backup_and_restore_evidence: bool
    human_approval: bool

    @property
    def satisfied(self) -> bool:
        return (
            self.retention_clear
            and self.legal_hold_clear
            and self.backup_and_restore_evidence
            and self.human_approval
        )


@dataclass(frozen=True, slots=True)
class RetirementDecision:
    """Outcome of one physical-generation deletion evaluation."""

    allowed: bool
    denied_reasons: tuple[str, ...]


class RegistryStore(Protocol):
    """Atomic registry persistence owned by the core cluster."""

    def snapshot(self) -> tuple[RegistryRecord, ...]:
        """Return one consistent immutable view of every record."""

    def compare_and_swap(
        self,
        *,
        key: ActivationKey,
        expected_active: tuple[RegistryRecord, ...],
        next_records: tuple[RegistryRecord, ...],
        idempotency_key: str,
    ) -> tuple[RegistryRecord, ...] | None:
        """Replace the key's records iff the active set matches.

        Returns the new full snapshot on success. Returns ``None`` when the
        current active set differs from ``expected_active`` so the caller can
        deny while the previous active set stays untouched. Replaying an
        already-applied idempotency key returns the current snapshot without
        changing anything.
        """


_REQUIRED_COMPONENTS = (
    RetrievalComponent.LEXICAL,
    RetrievalComponent.VECTOR,
)


def _with_lifecycle(
    record: RegistryRecord, lifecycle: RegistryLifecycle
) -> RegistryRecord:
    """Return a revalidated copy of one record in a new lifecycle state."""

    return RegistryRecord.model_validate(
        {**record.model_dump(), "lifecycle": lifecycle}
    )


class ProjectionRegistry:
    """Select active projections and gate activation, rollback, deletion."""

    def __init__(self, store: RegistryStore) -> None:
        self._store = store

    def select_active(
        self,
        scope: RetrievalScope,
        components: tuple[RetrievalComponent, ...],
        *,
        request_id: object | None = None,
    ) -> tuple[ProjectionPins, ...]:
        """Select one atomic active set; missing and denied share one shape."""

        del request_id
        try:
            snapshot = self._store.snapshot()
        except Exception:
            raise RetrievalUnavailableError(
                "active projection selection is unavailable"
            ) from None
        active = tuple(
            record
            for record in snapshot
            if record.lifecycle is RegistryLifecycle.ACTIVE
            and record.projection.scope == scope
        )
        selected: list[RegistryRecord] = []
        for component in components:
            matches = tuple(
                record for record in active if record.projection.component is component
            )
            if len(matches) != 1:
                raise RetrievalUnavailableError(
                    "active projection selection is unavailable"
                )
            selected.append(matches[0])
        first = selected[0].projection
        for record in selected:
            projection = record.projection
            if (
                projection.projection_set_id != first.projection_set_id
                or projection.projection_generation != first.projection_generation
                or projection.release_id != first.release_id
            ):
                raise RetrievalUnavailableError(
                    "active projection selection is unavailable"
                )
        if RetrievalComponent.GRAPH in components:
            relational_sets = {
                record.projection.projection_set_id
                for record in active
                if record.projection.component is not RetrievalComponent.GRAPH
            }
            if relational_sets and relational_sets != {first.projection_set_id}:
                raise RetrievalUnavailableError(
                    "active projection selection is unavailable"
                )
        return tuple(record.projection for record in selected)

    def activate(
        self,
        candidate: tuple[RegistryRecord, ...],
        *,
        running: SupplyChainEvidence,
        now: datetime,
    ) -> tuple[RegistryRecord, ...]:
        """Atomically activate one ready projection set or deny unchanged."""

        self._validate_candidate(candidate)
        for record in candidate:
            self._verify_supply_chain(record, running=running, now=now)
        key = ActivationKey.from_scope(candidate[0].projection.scope)
        snapshot = self._snapshot()
        current_active = tuple(
            record
            for record in snapshot
            if self._key(record) == key and record.lifecycle is RegistryLifecycle.ACTIVE
        )
        candidate_set_id = candidate[0].projection.projection_set_id
        candidate_by_component = {
            record.projection.component: record for record in candidate
        }
        existing_components = {
            record.projection.component
            for record in snapshot
            if self._key(record) == key
            and record.projection.projection_set_id == candidate_set_id
        }
        if existing_components and existing_components != set(candidate_by_component):
            raise RetrievalRequestError("active projection set manifest is immutable")
        for existing in snapshot:
            if (
                self._key(existing) != key
                or existing.projection.projection_set_id != candidate_set_id
            ):
                continue
            prior = candidate_by_component.get(existing.projection.component)
            if prior is None or prior != _with_lifecycle(
                existing, RegistryLifecycle.READY
            ):
                raise RetrievalRequestError(
                    "active projection set manifest is immutable"
                )
        retiring = tuple(
            _with_lifecycle(record, RegistryLifecycle.RETIRING)
            for record in current_active
        )
        installing = tuple(
            _with_lifecycle(record, RegistryLifecycle.ACTIVE) for record in candidate
        )
        next_records = installing + retiring
        result = self._compare_and_swap(
            key=key,
            expected_active=current_active,
            next_records=next_records,
            idempotency_key=candidate[0].activation_idempotency_key,
        )
        if result is None:
            raise RetrievalUnavailableError(
                "active projection selection is unavailable"
            )
        return result

    def rollback(self, scope: RetrievalScope) -> tuple[RegistryRecord, ...]:
        """Atomically restore the prior projection set for one scope."""

        key = ActivationKey.from_scope(scope)
        snapshot = self._snapshot()
        active = tuple(
            record
            for record in snapshot
            if self._key(record) == key and record.lifecycle is RegistryLifecycle.ACTIVE
        )
        if not active:
            raise RetrievalUnavailableError(
                "active projection selection is unavailable"
            )
        prior_set_id = active[0].prior_projection_set_id
        if prior_set_id is None:
            raise RetrievalUnavailableError(
                "active projection selection is unavailable"
            )
        prior = tuple(
            record
            for record in snapshot
            if self._key(record) == key
            and record.projection.projection_set_id == prior_set_id
            and record.lifecycle is RegistryLifecycle.RETIRING
        )
        self._validate_complete_set(prior)
        restored = tuple(
            _with_lifecycle(record, RegistryLifecycle.ACTIVE) for record in prior
        )
        superseded = tuple(
            _with_lifecycle(record, RegistryLifecycle.RETIRING) for record in active
        )
        result = self._compare_and_swap(
            key=key,
            expected_active=active,
            next_records=restored + superseded,
            idempotency_key=f"{active[0].activation_idempotency_key}:rollback",
        )
        if result is None:
            raise RetrievalUnavailableError(
                "active projection selection is unavailable"
            )
        return result

    def deletion_decision(
        self,
        *,
        physical_partition_id: str,
        gates: tuple[RetirementGates, ...],
    ) -> RetirementDecision:
        """Evaluate the deletion gate for one shared physical generation."""

        snapshot = self._snapshot()
        referencing = tuple(
            record
            for record in snapshot
            if record.projection.physical_partition_id == physical_partition_id
        )
        if not referencing:
            return RetirementDecision(
                allowed=False,
                denied_reasons=("no_retired_referencing_scope",),
            )
        denied: list[str] = []
        not_retired = sum(
            1
            for record in referencing
            if record.lifecycle is not RegistryLifecycle.RETIRED
        )
        if not_retired:
            denied.append("referencing_scope_not_retired")
        if len(gates) != len(referencing):
            denied.append("missing_scope_gates")
        elif any(not gate.satisfied for gate in gates):
            denied.append("scope_gates_unsatisfied")
        return RetirementDecision(allowed=not denied, denied_reasons=tuple(denied))

    def resolve_legacy_alias(self, alias: str) -> None:
        """Legacy Qdrant and FalkorDB aliases are provenance only."""

        del alias
        raise RetrievalRequestError(
            "legacy retrieval aliases are never routing eligible"
        )

    def _snapshot(self) -> tuple[RegistryRecord, ...]:
        try:
            return self._store.snapshot()
        except Exception:
            raise RetrievalUnavailableError(
                "active projection selection is unavailable"
            ) from None

    def _compare_and_swap(
        self,
        *,
        key: ActivationKey,
        expected_active: tuple[RegistryRecord, ...],
        next_records: tuple[RegistryRecord, ...],
        idempotency_key: str,
    ) -> tuple[RegistryRecord, ...] | None:
        try:
            return self._store.compare_and_swap(
                key=key,
                expected_active=expected_active,
                next_records=next_records,
                idempotency_key=idempotency_key,
            )
        except Exception:
            return None

    @staticmethod
    def _key(record: RegistryRecord) -> ActivationKey:
        return ActivationKey.from_scope(record.projection.scope)

    def _validate_candidate(self, candidate: tuple[RegistryRecord, ...]) -> None:
        if not isinstance(candidate, tuple) or not candidate:
            raise RetrievalRequestError("activation candidate set is invalid")
        if any(not isinstance(record, RegistryRecord) for record in candidate):
            raise RetrievalRequestError("activation candidate set is invalid")
        if any(record.lifecycle is not RegistryLifecycle.READY for record in candidate):
            raise RetrievalUnavailableError(
                "active projection selection is unavailable"
            )
        self._validate_complete_set(candidate)
        first = candidate[0]
        for record in candidate:
            if record.activation_idempotency_key != first.activation_idempotency_key:
                raise RetrievalRequestError(
                    "activation candidate set shares no idempotency key"
                )
            if record.supply_chain != first.supply_chain:
                raise RetrievalRequestError(
                    "activation candidate set has mixed supply-chain evidence"
                )

    def _validate_complete_set(self, records: tuple[RegistryRecord, ...]) -> None:
        if not records:
            raise RetrievalUnavailableError(
                "active projection selection is unavailable"
            )
        components = tuple(record.projection.component for record in records)
        for required in _REQUIRED_COMPONENTS:
            if components.count(required) != 1:
                raise RetrievalUnavailableError(
                    "active projection selection is unavailable"
                )
        extras = set(components) - set(_REQUIRED_COMPONENTS)
        if extras != set() and extras != {RetrievalComponent.GRAPH}:
            raise RetrievalRequestError("activation candidate set is invalid")
        if components.count(RetrievalComponent.GRAPH) > 1:
            raise RetrievalRequestError("activation candidate set is invalid")
        first = records[0].projection
        key = ActivationKey.from_scope(first.scope)
        for record in records:
            projection = record.projection
            if (
                self._key(record) != key
                or projection.projection_set_id != first.projection_set_id
                or projection.projection_generation != first.projection_generation
                or projection.release_id != first.release_id
            ):
                raise RetrievalRequestError(
                    "activation candidate set mixes scopes, sets, releases, or generations"
                )

    def _verify_supply_chain(
        self,
        record: RegistryRecord,
        *,
        running: SupplyChainEvidence,
        now: datetime,
    ) -> None:
        evidence = record.supply_chain
        if evidence != running:
            raise RetrievalUnavailableError(
                "active projection selection is unavailable"
            )
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        if evidence.high_risk_acceptance_expires_at is not None and (
            evidence.high_risk_acceptance_expires_at <= now
        ):
            raise RetrievalUnavailableError(
                "active projection selection is unavailable"
            )
        graph = record.projection.graph
        if graph is not None:
            if (
                running.apache_age_qualification_status
                is not AgeQualification.QUALIFIED
                or running.graph_gateway_definition_sha256 is None
                or running.graph_gateway_definition_sha256
                != graph.graph_gateway_function_definition_sha256
            ):
                raise RetrievalUnavailableError(
                    "active projection selection is unavailable"
                )


__all__ = [
    "ActivationKey",
    "AgeQualification",
    "ExtensionEvidence",
    "ProjectionRegistry",
    "RegistryLifecycle",
    "RegistryRecord",
    "RegistryStore",
    "RetirementDecision",
    "RetirementGates",
    "SupplyChainEvidence",
]
