"""Versioned model-route registry loading and lookup."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Self

from .errors import RouteNotFoundError
from .models import ModelRouteRecord

REGISTRY_SCHEMA = "sklegal-model-route-registry/v1"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RouteRegistry:
    """Immutable pinned route lookup built from a validated payload."""

    def __init__(
        self,
        routes: tuple[ModelRouteRecord, ...],
        *,
        registry_revision: str,
    ) -> None:
        if not routes:
            raise ValueError("route registry cannot be empty")
        route_ids = [route.route_id for route in routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("route identifiers must be unique")
        self._routes = {route.route_id: route for route in routes}
        self._registry_revision = registry_revision

    @property
    def registry_revision(self) -> str:
        return self._registry_revision

    @property
    def routes(self) -> tuple[ModelRouteRecord, ...]:
        return tuple(self._routes[route_id] for route_id in sorted(self._routes))

    def route(self, route_id: str) -> ModelRouteRecord:
        record = self._routes.get(route_id)
        if record is None:
            raise RouteNotFoundError(f"route is not pinned in the registry: {route_id}")
        return record

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        registry_revision: str,
    ) -> Self:
        if payload.get("schema") != REGISTRY_SCHEMA:
            raise ValueError("route registry schema marker is missing or unknown")
        raw_routes = payload.get("routes")
        if not isinstance(raw_routes, list) or not raw_routes:
            raise ValueError("route registry requires a nonempty routes list")
        routes = tuple(
            ModelRouteRecord.model_validate(raw_route) for raw_route in raw_routes
        )
        return cls(routes, registry_revision=registry_revision)

    @classmethod
    def from_file(cls, path: Path) -> Self:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("route registry file must contain a JSON object")
        return cls.from_payload(payload, registry_revision=sha256_text(text))
