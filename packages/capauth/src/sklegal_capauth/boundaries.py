"""Reusable protected API, tool, model, and connector boundaries."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar
from uuid import UUID

from pydantic import model_validator

from .authorization import CapabilityAuthorizer
from .models import (
    CAPABILITY_RULES,
    Audience,
    AuthorizationRequest,
    AuthorizedContext,
    Capability,
    CapabilityGrant,
    ModelRoute,
    OpaqueName,
    PrincipalContext,
    Purpose,
    Sha256,
    StrictValue,
)
from .tokens import PresentedCapability

ResultT = TypeVar("ResultT")


class BoundaryScope(StrictValue):
    """Invocation-specific scope supplied by deterministic trusted code."""

    tenant_id: UUID
    matter_id: UUID | None = None
    resource_id: OpaqueName | None = None
    resource_version: int | None = None
    resource_sha256: Sha256 | None = None
    model_route: ModelRoute | None = None
    workflow_run_id: OpaqueName | None = None


class CapabilityRequirement(StrictValue):
    """Fixed authorization contract attached to one protected boundary."""

    audience: Audience
    target: OpaqueName
    capability: Capability
    purpose: Purpose

    @model_validator(mode="after")
    def validate_requirement(self) -> CapabilityRequirement:
        rule = CAPABILITY_RULES[self.capability]
        if self.purpose not in rule.purposes:
            raise ValueError("boundary purpose does not match capability")
        prefix = CapabilityGrant.TARGET_PREFIX[self.audience]
        if not self.target.startswith(prefix):
            raise ValueError("boundary target does not match audience")
        return self

    def bind(self, scope: BoundaryScope) -> CapabilityGrant:
        rule = CAPABILITY_RULES[self.capability]
        return CapabilityGrant(
            audience=self.audience,
            target=self.target,
            capability=self.capability,
            tenant_id=scope.tenant_id,
            matter_id=scope.matter_id,
            resource_type=rule.resource_type,
            resource_id=scope.resource_id,
            resource_version=scope.resource_version,
            resource_sha256=scope.resource_sha256,
            operation=rule.operation,
            purpose=self.purpose,
            model_route=scope.model_route,
            workflow_run_id=scope.workflow_run_id,
        )


class ProtectedBoundary[ResultT]:
    """Authorize before invoking a handler and never pass it a raw credential."""

    def __init__(
        self,
        *,
        authorizer: CapabilityAuthorizer,
        requirement: CapabilityRequirement,
    ) -> None:
        self._authorizer = authorizer
        self.requirement = requirement

    def authorize(
        self,
        *,
        principal: PrincipalContext,
        scope: BoundaryScope,
        correlation_id: UUID,
        presented: PresentedCapability | None,
    ) -> AuthorizedContext:
        request = AuthorizationRequest(
            principal=principal,
            grant=self.requirement.bind(scope),
            correlation_id=correlation_id,
        )
        return self._authorizer.authorize(presented, request)

    def invoke(
        self,
        *,
        principal: PrincipalContext,
        scope: BoundaryScope,
        correlation_id: UUID,
        presented: PresentedCapability | None,
        handler: Callable[[AuthorizedContext], ResultT],
    ) -> ResultT:
        authorized = self.authorize(
            principal=principal,
            scope=scope,
            correlation_id=correlation_id,
            presented=presented,
        )
        return handler(authorized)


class ApiCapabilityBoundary(ProtectedBoundary[ResultT]):
    def __init__(
        self,
        *,
        authorizer: CapabilityAuthorizer,
        route_name: OpaqueName,
        capability: Capability,
        purpose: Purpose,
    ) -> None:
        super().__init__(
            authorizer=authorizer,
            requirement=CapabilityRequirement(
                audience=Audience.API,
                target=f"api:{route_name}",
                capability=capability,
                purpose=purpose,
            ),
        )


class ToolCapabilityBoundary(ProtectedBoundary[ResultT]):
    def __init__(
        self,
        *,
        authorizer: CapabilityAuthorizer,
        tool_name: OpaqueName,
        capability: Capability,
        purpose: Purpose,
    ) -> None:
        super().__init__(
            authorizer=authorizer,
            requirement=CapabilityRequirement(
                audience=Audience.TOOL,
                target=f"tool:{tool_name}",
                capability=capability,
                purpose=purpose,
            ),
        )


class ModelCapabilityBoundary(ProtectedBoundary[ResultT]):
    def __init__(
        self,
        *,
        authorizer: CapabilityAuthorizer,
        model_target: OpaqueName,
        capability: Capability,
        purpose: Purpose,
    ) -> None:
        super().__init__(
            authorizer=authorizer,
            requirement=CapabilityRequirement(
                audience=Audience.MODEL,
                target=f"model:{model_target}",
                capability=capability,
                purpose=purpose,
            ),
        )


class ConnectorCapabilityBoundary(ProtectedBoundary[ResultT]):
    def __init__(
        self,
        *,
        authorizer: CapabilityAuthorizer,
        connector_name: OpaqueName,
        capability: Capability,
        purpose: Purpose,
    ) -> None:
        super().__init__(
            authorizer=authorizer,
            requirement=CapabilityRequirement(
                audience=Audience.CONNECTOR,
                target=f"connector:{connector_name}",
                capability=capability,
                purpose=purpose,
            ),
        )
