"""Model catalog, workload buckets, trust zones, and free routes (SKL-S3-10).

The catalog resolves the concrete Qwen request alias to the exact served
operator model and resolves S/M/L/XL workload buckets. Buckets allow model
substitution only where the task contract permits it: SKLegal ``public``
maps only to a public trust-zone bucket, ``internal`` maps only to an
internal-or-stricter bucket, and confidential, highly restricted,
privileged, and private-corpus contexts require an explicitly qualified
sovereign-local member plus SKLegal policy approval. A misspelled, empty,
stale, or ineligible bucket fails closed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sklegal_domain import DataClassification

from .errors import (
    BucketPolicyError,
    CatalogLookupError,
    FreeRouteDeniedError,
    SourceRightsDeniedError,
)
from .models import TrustZone, WorkloadClass
from .registry import sha256_text

CATALOG_SCHEMA = "sklegal-model-catalog/v1"

# Minimum trust zone required for each SKLegal classification. Classifications
# at or above confidential require a sovereign-local member and policy approval.
_ZONE_FLOOR: dict[DataClassification, TrustZone] = {
    DataClassification.PUBLIC: TrustZone.PUBLIC,
    DataClassification.INTERNAL: TrustZone.INTERNAL,
    DataClassification.CONFIDENTIAL: TrustZone.SOVEREIGN_LOCAL,
    DataClassification.PRIVILEGED_WORK_PRODUCT: TrustZone.SOVEREIGN_LOCAL,
    DataClassification.HIGHLY_RESTRICTED: TrustZone.SOVEREIGN_LOCAL,
}
_ZONE_RANK = {
    TrustZone.PUBLIC: 0,
    TrustZone.INTERNAL: 1,
    TrustZone.SOVEREIGN_LOCAL: 2,
}

_CLASSIFICATION_RANK = {
    DataClassification.PUBLIC: 0,
    DataClassification.INTERNAL: 1,
    DataClassification.CONFIDENTIAL: 2,
    DataClassification.PRIVILEGED_WORK_PRODUCT: 3,
    DataClassification.HIGHLY_RESTRICTED: 4,
}


class ServedModel(BaseModel):
    """One catalog member with exact served identity and trust zone."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    model_id: str = Field(min_length=1, max_length=160)
    served_model: str = Field(min_length=1, max_length=200)
    revision: str = Field(min_length=1, max_length=160)
    trust_zone: TrustZone
    workload_classes: tuple[WorkloadClass, ...] = Field(min_length=1)
    parameter_size: str | None = Field(default=None)
    qualified: bool = True
    notes: str | None = None


class Bucket(BaseModel):
    """One substitution bucket restricted to eligible members."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    bucket_id: str = Field(min_length=1, max_length=160)
    workload_class: WorkloadClass
    members: tuple[str, ...] = Field(min_length=1)
    enabled: bool = True


class FreeRouteRecord(BaseModel):
    """A policy-approved optimization route, never an entitlement."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    route_id: str = Field(min_length=1, max_length=160)
    model_id: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=160)
    approved: bool
    max_classification: DataClassification = DataClassification.PUBLIC
    evaluation_ref: str | None = None
    notes: str | None = None


class CatalogFile(BaseModel):
    """Validated on-disk shape of the model catalog."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    schema_marker: str = Field(
        alias="schema", pattern=f"^{CATALOG_SCHEMA}$"
    )
    purpose: str = Field(min_length=1)
    generation: str = Field(min_length=1, max_length=160)
    qwen_request_alias: str = Field(min_length=1, max_length=160)
    qwen_served_model: str = Field(min_length=1, max_length=200)
    models: list[ServedModel] = Field(min_length=1)
    buckets: list[Bucket] = Field(default_factory=list)
    free_routes: list[FreeRouteRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        model_ids = {model.model_id for model in self.models}
        if len(model_ids) != len(self.models):
            raise ValueError("catalog model identifiers must be unique")
        if self.qwen_served_model not in {
            model.served_model for model in self.models
        }:
            raise ValueError(
                "the qwen request alias must resolve to a catalog member's "
                "exact served model"
            )
        bucket_ids = [bucket.bucket_id for bucket in self.buckets]
        if len(bucket_ids) != len(set(bucket_ids)):
            raise ValueError("bucket identifiers must be unique")
        for bucket in self.buckets:
            for member in bucket.members:
                if member not in model_ids:
                    raise ValueError(
                        f"bucket {bucket.bucket_id} names an unknown member: "
                        f"{member}"
                    )
        for record in self.free_routes:
            if record.model_id not in model_ids:
                raise ValueError(
                    f"free route names an unknown model: {record.model_id}"
                )
        return self


class ResolvedTarget(BaseModel):
    """The exact target a request resolved to, with attribution fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_model: str
    served_model: str
    revision: str
    trust_zone: TrustZone
    bucket_id: str | None = None
    bucket_member_id: str | None = None


class ModelCatalog:
    """Immutable alias, bucket, and free-route lookup that fails closed."""

    def __init__(self, document: CatalogFile, *, revision: str = "") -> None:
        self._document = document
        self._revision = revision
        self._models = {model.model_id: model for model in document.models}
        self._served = {model.served_model: model for model in document.models}
        self._buckets = {bucket.bucket_id: bucket for bucket in document.buckets}

    @property
    def revision(self) -> str:
        return self._revision

    @property
    def generation(self) -> str:
        return self._document.generation

    @property
    def qwen_request_alias(self) -> str:
        return self._document.qwen_request_alias

    @property
    def qwen_served_model(self) -> str:
        return self._document.qwen_served_model

    def model(self, model_id: str) -> ServedModel:
        record = self._models.get(model_id)
        if record is None or not record.qualified:
            raise CatalogLookupError(f"model is absent or unqualified: {model_id}")
        return record

    def resolve_alias(self, alias: str) -> ResolvedTarget:
        """Resolve the exact Qwen alias; deny unknown or unqualified."""

        if alias != self._document.qwen_request_alias:
            raise CatalogLookupError(
                f"request model alias is not the pinned catalog alias: {alias}"
            )
        model = self._served.get(self._document.qwen_served_model)
        if model is None or not model.qualified:
            raise CatalogLookupError(
                "the pinned qwen served model is absent or unqualified"
            )
        return ResolvedTarget(
            requested_model=alias,
            served_model=model.served_model,
            revision=model.revision,
            trust_zone=model.trust_zone,
        )

    def resolve_bucket(
        self,
        bucket_id: str,
        *,
        workload_class: WorkloadClass | None,
        classification: DataClassification,
        human_approval_ref: str | None,
        source_rights_state: str | None,
    ) -> ResolvedTarget:
        """Resolve one eligible bucket member; deny ineligible or stale."""

        bucket = self._buckets.get(bucket_id)
        if bucket is None:
            raise CatalogLookupError(
                f"workload bucket is not pinned in the catalog: {bucket_id}"
            )
        if not bucket.enabled:
            raise CatalogLookupError(f"workload bucket is disabled: {bucket_id}")
        if workload_class is not None and bucket.workload_class != workload_class:
            raise BucketPolicyError(
                f"bucket {bucket_id} serves workload class {bucket.workload_class}, "
                f"not the requested {workload_class}"
            )
        floor = _ZONE_FLOOR[classification]
        candidates: list[ServedModel] = []
        conflicts: list[str] = []
        for member_id in bucket.members:
            model = self._models.get(member_id)
            if model is None or not model.qualified:
                conflicts.append(f"unqualified member {member_id}")
                continue
            if workload_class is not None:
                if workload_class not in model.workload_classes:
                    conflicts.append(
                        f"member {member_id} does not serve workload "
                        f"{workload_class}"
                    )
                    continue
            if _ZONE_RANK[model.trust_zone] < _ZONE_RANK[floor]:
                conflicts.append(
                    f"member {member_id} trust zone {model.trust_zone} is below "
                    f"the {floor} floor for {classification}"
                )
                continue
            if (
                floor is TrustZone.SOVEREIGN_LOCAL
                and model.trust_zone is not TrustZone.SOVEREIGN_LOCAL
            ):
                conflicts.append(
                    f"member {member_id} is not sovereign-local for {classification}"
                )
                continue
            candidates.append(model)
        if not candidates:
            detail = "; ".join(conflicts) or "no members"
            raise BucketPolicyError(
                f"bucket {bucket_id} has no eligible member for classification "
                f"{classification}: {detail}"
            )
        if floor is TrustZone.SOVEREIGN_LOCAL and not human_approval_ref:
            raise BucketPolicyError(
                f"bucket {bucket_id} requires human approval before any "
                f"sovereign-local member may serve {classification}"
            )
        if source_rights_state is not None:
            if source_rights_state in {
                "unknown",
                "unverified",
                "expired",
                "revoked",
            }:
                raise SourceRightsDeniedError(
                    f"source rights state {source_rights_state} quarantines "
                    "model egress"
                )
        chosen = candidates[0]
        return ResolvedTarget(
            requested_model=bucket_id,
            served_model=chosen.served_model,
            revision=chosen.revision,
            trust_zone=chosen.trust_zone,
            bucket_id=bucket.bucket_id,
            bucket_member_id=chosen.model_id,
        )

    def free_route(self, route_id: str) -> FreeRouteRecord:
        for record in self._document.free_routes:
            if record.route_id == route_id:
                if not record.approved:
                    raise FreeRouteDeniedError(
                        f"free route is not policy approved: {route_id}"
                    )
                return record
        raise CatalogLookupError(f"free route is not pinned in the catalog: {route_id}")

    def check_free_route(
        self,
        route_id: str,
        *,
        classification: DataClassification,
    ) -> FreeRouteRecord:
        record = self.free_route(route_id)
        if _CLASSIFICATION_RANK[classification] > _CLASSIFICATION_RANK[
            record.max_classification
        ]:
            raise FreeRouteDeniedError(
                f"free route {route_id} is limited to {record.max_classification} "
                f"synthetic content; requested {classification}"
            )
        return record

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, revision: str = "") -> Self:
        if payload.get("schema") != CATALOG_SCHEMA:
            raise ValueError("model catalog schema marker is missing or unknown")
        return cls(CatalogFile.model_validate(payload), revision=revision)

    @classmethod
    def from_file(cls, path: Path) -> Self:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("model catalog file must contain a JSON object")
        return cls.from_payload(payload, revision=sha256_text(text))
