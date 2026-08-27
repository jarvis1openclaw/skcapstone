"""Exact request capability contracts for the frozen V2 API."""

from __future__ import annotations

from dataclasses import dataclass
from re import Pattern
from typing import Self
from uuid import UUID

from sklegal_capauth import Capability, Purpose
from starlette.routing import compile_path


@dataclass(frozen=True, slots=True)
class ReviewedRequestCapability:
    operation_id: str
    method: str
    path_template: str
    target: str
    capability: Capability
    purpose: Purpose
    resource_parameter: str
    uuid_parameters: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedRequestCapability:
    reviewed: ReviewedRequestCapability
    matter_id: UUID
    resource_id: str
    path_parameters: tuple[tuple[str, str], ...]
    resource_version: int | None = None
    resource_sha256: str | None = None

    def parameter(self, name: str) -> str:
        try:
            return dict(self.path_parameters)[name]
        except KeyError:
            raise ValueError(f"reviewed route parameter is absent: {name}") from None

    def bind_exact_version(self, version: int, content_sha256: str) -> Self:
        if self.reviewed.capability is not Capability.WORK_PRODUCT_APPROVE:
            raise ValueError("exact version binding is reserved for Approval")
        if (
            version < 1
            or len(content_sha256) != 64
            or any(item not in "0123456789abcdef" for item in content_sha256)
        ):
            raise ValueError("exact version binding is invalid")
        return type(self)(
            reviewed=self.reviewed,
            matter_id=self.matter_id,
            resource_id=self.resource_id,
            path_parameters=self.path_parameters,
            resource_version=version,
            resource_sha256=content_sha256,
        )


class ReviewedRequestCapabilityRegistry:
    """Resolve only complete, reviewed method and route-template matches."""

    def __init__(self, reviewed: tuple[ReviewedRequestCapability, ...]) -> None:
        operation_ids = {item.operation_id for item in reviewed}
        request_pairs = {(item.method, item.path_template) for item in reviewed}
        if len(operation_ids) != len(reviewed) or len(request_pairs) != len(reviewed):
            raise ValueError("reviewed request capability contracts must be unique")
        self.reviewed = reviewed
        self._compiled: tuple[tuple[ReviewedRequestCapability, Pattern[str]], ...] = (
            tuple((item, compile_path(item.path_template)[0]) for item in reviewed)
        )

    def resolve(self, method: str, path: str) -> ResolvedRequestCapability | None:
        for reviewed, pattern in self._compiled:
            if method != reviewed.method:
                continue
            match = pattern.fullmatch(path)
            if match is None:
                continue
            parameters = match.groupdict()
            try:
                for name in reviewed.uuid_parameters:
                    parsed = UUID(parameters[name])
                    if str(parsed) != parameters[name].lower():
                        return None
                    parameters[name] = str(parsed)
                matter_id = UUID(parameters["matter_id"])
                resource_id = parameters[reviewed.resource_parameter]
            except (KeyError, ValueError):
                return None
            return ResolvedRequestCapability(
                reviewed=reviewed,
                matter_id=matter_id,
                resource_id=resource_id,
                path_parameters=tuple(sorted(parameters.items())),
            )
        return None


def _reviewed(
    operation_id: str,
    method: str,
    path_template: str,
    capability: Capability,
    purpose: Purpose,
    resource_parameter: str = "matter_id",
) -> ReviewedRequestCapability:
    parameters = tuple(
        name
        for name in (
            "matter_id",
            "agent_run_id",
            "claim_id",
            "recommendation_id",
            "artifact_id",
            "work_product_id",
            "version_id",
        )
        if f"{{{name}}}" in path_template
    )
    return ReviewedRequestCapability(
        operation_id=operation_id,
        method=method,
        path_template=path_template,
        target=f"api:{operation_id}",
        capability=capability,
        purpose=purpose,
        resource_parameter=resource_parameter,
        uuid_parameters=parameters,
    )


V2_REQUEST_CAPABILITIES = ReviewedRequestCapabilityRegistry(
    (
        _reviewed(
            "get_workspace",
            "GET",
            "/v1/matters/{matter_id}/workspace",
            Capability.MATTER_READ,
            Purpose.MATTER_MANAGEMENT,
        ),
        _reviewed(
            "get_claim_ledger",
            "GET",
            "/v1/matters/{matter_id}/claims",
            Capability.CLAIM_REVIEW,
            Purpose.CLAIM_REVIEW,
        ),
        _reviewed(
            "get_joined_analysis",
            "GET",
            "/v1/matters/{matter_id}/analysis",
            Capability.CLAIM_REVIEW,
            Purpose.CLAIM_REVIEW,
        ),
        _reviewed(
            "create_analysis_run",
            "POST",
            "/v1/matters/{matter_id}/agent-runs",
            Capability.CLAIM_PROPOSE,
            Purpose.CLAIM_DEVELOPMENT,
        ),
        _reviewed(
            "get_agent_run",
            "GET",
            "/v1/matters/{matter_id}/agent-runs/{agent_run_id}",
            Capability.CLAIM_REVIEW,
            Purpose.CLAIM_REVIEW,
            "agent_run_id",
        ),
        _reviewed(
            "create_challenge",
            "POST",
            "/v1/matters/{matter_id}/claims/{claim_id}/challenges",
            Capability.CLAIM_REVIEW,
            Purpose.CLAIM_REVIEW,
            "claim_id",
        ),
        _reviewed(
            "list_recommendations",
            "GET",
            "/v1/matters/{matter_id}/recommendations",
            Capability.CLAIM_REVIEW,
            Purpose.CLAIM_REVIEW,
        ),
        _reviewed(
            "decide_recommendation",
            "POST",
            "/v1/matters/{matter_id}/recommendations/{recommendation_id}/decisions",
            Capability.CLAIM_REVIEW,
            Purpose.CLAIM_REVIEW,
            "recommendation_id",
        ),
        _reviewed(
            "create_artifact_intake",
            "POST",
            "/v1/matters/{matter_id}/artifacts",
            Capability.EVIDENCE_MANAGE,
            Purpose.EVIDENCE_REVIEW,
        ),
        _reviewed(
            "get_artifact",
            "GET",
            "/v1/matters/{matter_id}/artifacts/{artifact_id}",
            Capability.EVIDENCE_READ,
            Purpose.EVIDENCE_REVIEW,
            "artifact_id",
        ),
        _reviewed(
            "get_work_product",
            "GET",
            "/v1/matters/{matter_id}/work-products/{work_product_id}",
            Capability.MATTER_READ,
            Purpose.MATTER_MANAGEMENT,
            "work_product_id",
        ),
        _reviewed(
            "create_work_product_version",
            "POST",
            "/v1/matters/{matter_id}/work-products/{work_product_id}/versions",
            Capability.WORK_PRODUCT_DRAFT,
            Purpose.WORK_PRODUCT_PREPARATION,
            "work_product_id",
        ),
        _reviewed(
            "validate_work_product",
            "POST",
            "/v1/matters/{matter_id}/work-products/{work_product_id}/versions/{version_id}/validations",
            Capability.WORK_PRODUCT_DRAFT,
            Purpose.WORK_PRODUCT_PREPARATION,
            "version_id",
        ),
        _reviewed(
            "decide_approval",
            "POST",
            "/v1/matters/{matter_id}/work-products/{work_product_id}/versions/{version_id}/approval-decisions",
            Capability.WORK_PRODUCT_APPROVE,
            Purpose.HUMAN_APPROVAL,
            "version_id",
        ),
        _reviewed(
            "upsert_task",
            "POST",
            "/v1/matters/{matter_id}/tasks",
            Capability.MATTER_MANAGE,
            Purpose.MATTER_MANAGEMENT,
        ),
        _reviewed(
            "compute_deadline",
            "POST",
            "/v1/matters/{matter_id}/deadlines",
            Capability.MATTER_MANAGE,
            Purpose.MATTER_MANAGEMENT,
        ),
        _reviewed(
            "create_simulation_handoff",
            "POST",
            "/v1/matters/{matter_id}/action-simulations",
            Capability.ACTION_EMAIL_PREPARE,
            Purpose.EXTERNAL_ACTION_PREPARATION,
        ),
        _reviewed(
            "list_activity",
            "GET",
            "/v1/matters/{matter_id}/activity",
            Capability.AUDIT_READ,
            Purpose.AUDIT_REVIEW,
        ),
        _reviewed(
            "create_activity_export",
            "POST",
            "/v1/matters/{matter_id}/activity-exports",
            Capability.AUDIT_READ,
            Purpose.AUDIT_REVIEW,
        ),
        _reviewed(
            "search_corpus",
            "POST",
            "/v1/matters/{matter_id}/corpus/search",
            Capability.CORPUS_SEARCH,
            Purpose.LEGAL_RESEARCH,
        ),
        _reviewed(
            "get_corpus_span",
            "GET",
            "/v1/matters/{matter_id}/corpus/sources/{source_id}/span",
            Capability.CORPUS_ARTIFACT_READ,
            Purpose.LEGAL_RESEARCH,
            "source_id",
        ),
    )
)


__all__ = [
    "ResolvedRequestCapability",
    "ReviewedRequestCapability",
    "ReviewedRequestCapabilityRegistry",
    "V2_REQUEST_CAPABILITIES",
]
