"""Frozen request contracts for the standalone Work Product feature router."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sklegal_persistence.features.work_products.models import (
    SourceBinding,
    VersionBinding,
)


class WorkProductCommand(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class CreateWorkProductBody(WorkProductCommand):
    schema_version: Literal["sklegal.work-product-create-command/v1"] = (
        "sklegal.work-product-create-command/v1"
    )
    title: str = Field(min_length=1, max_length=512)
    work_product_kind: Literal[
        "memo", "letter", "pleading", "contract", "packet", "report", "other"
    ]
    content: str = Field(min_length=1, max_length=1_000_000)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: SourceBinding


class CreateVersionBody(WorkProductCommand):
    schema_version: Literal["sklegal.work-product-version-command/v1"] = (
        "sklegal.work-product-version-command/v1"
    )
    expected_aggregate_version: int = Field(ge=1)
    expected_current: VersionBinding
    content: str = Field(min_length=1, max_length=1_000_000)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: SourceBinding


class GroundSentenceBody(WorkProductCommand):
    schema_version: Literal["sklegal.work-product-grounding-command/v1"] = (
        "sklegal.work-product-grounding-command/v1"
    )
    expected_aggregate_version: int = Field(ge=1)
    binding: VersionBinding
    sentence_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    claim_id: UUID


class ValidateWorkProductBody(WorkProductCommand):
    schema_version: Literal["sklegal.work-product-validation-command/v1"] = (
        "sklegal.work-product-validation-command/v1"
    )
    expected_aggregate_version: int = Field(ge=1)
    binding: VersionBinding
    check_ids: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=4096)


class RequestApprovalBody(WorkProductCommand):
    schema_version: Literal["sklegal.approval-request-command/v1"] = (
        "sklegal.approval-request-command/v1"
    )
    expected_aggregate_version: int = Field(ge=1)
    binding: VersionBinding
    validation_id: UUID


class DecideApprovalBody(WorkProductCommand):
    schema_version: Literal["sklegal.approval-decision-command/v1"] = (
        "sklegal.approval-decision-command/v1"
    )
    expected_aggregate_version: int = Field(ge=1)
    binding: VersionBinding
    decision: Literal["approved", "rejected"]
    rationale: str = Field(min_length=1, max_length=4096)


class RevokeApprovalBody(WorkProductCommand):
    schema_version: Literal["sklegal.approval-revocation-command/v1"] = (
        "sklegal.approval-revocation-command/v1"
    )
    expected_aggregate_version: int = Field(ge=1)
    binding: VersionBinding
    rationale: str = Field(min_length=1, max_length=4096)


class SupersedeApprovalBody(WorkProductCommand):
    schema_version: Literal["sklegal.approval-supersession-command/v1"] = (
        "sklegal.approval-supersession-command/v1"
    )
    expected_aggregate_version: int = Field(ge=1)
    superseding_approval_id: UUID
    superseding_binding: VersionBinding
