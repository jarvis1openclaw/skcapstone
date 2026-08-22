"""Versioned, hash-pinned agent specification registry.

The registry loads spec documents, validates them against the schema and
the bounded-authority rules, and pins every version by the SHA-256 of its
source file. Versions are immutable: a different document claiming an
existing (spec_id, version) pair is rejected. The registry holds no
runtime handles and performs no tool or model calls.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Self

from pydantic import ValidationError
from sklegal_model_gateway import RouteNotFoundError, RouteRegistry

from .errors import (
    ModelRouteNotEnabledError,
    SpecIntegrityError,
    SpecNotFoundError,
    SpecValidationError,
    SpecVersionImmutableError,
    UnboundedAuthorityError,
    UnknownModelRouteError,
    UnknownToolError,
)
from .models import (
    HUMAN_ESCALATION_PREFIX,
    SPEC_SCHEMA,
    AgentSpec,
    AgentSpecRecord,
    classification_rank,
)
from .tools import KNOWN_TOOL_IDS, WILDCARD_MARKERS


def _load_record(
    path: Path,
    *,
    route_registry: RouteRegistry,
    expected_hash: str | None = None,
) -> AgentSpecRecord:
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if expected_hash is not None and digest != expected_hash:
        raise SpecIntegrityError(
            f"spec file hash does not match its pinned hash: {path.name}"
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SpecValidationError(f"spec file is not valid JSON: {path.name}") from exc
    if not isinstance(payload, dict):
        raise SpecValidationError("spec file must contain a JSON object")
    if payload.get("schema") != SPEC_SCHEMA:
        raise SpecValidationError("spec schema marker is missing or unknown")
    _reject_wildcard_tools(payload, source=path.name)
    document = {key: value for key, value in payload.items() if key != "schema"}
    try:
        spec = AgentSpec.model_validate(document)
    except ValidationError as exc:
        raise SpecValidationError(
            f"spec failed schema validation: {path.name}: {exc.error_count()} error(s)"
        ) from exc
    _enforce_bounded_authority(spec, route_registry=route_registry, source=path.name)
    return AgentSpecRecord(spec=spec, spec_sha256=digest, source_name=path.name)


def _reject_wildcard_tools(payload: dict[str, Any], *, source: str) -> None:
    raw_tools = payload.get("tool_allowlist", [])
    if not isinstance(raw_tools, list):
        return
    for entry in raw_tools:
        if not isinstance(entry, str):
            continue
        if any(marker in entry for marker in WILDCARD_MARKERS):
            raise UnboundedAuthorityError(
                f"tool allowlist entries must name exact known tools, "
                f"never wildcard patterns: {source}: {entry!r}"
            )


def _enforce_bounded_authority(
    spec: AgentSpec,
    *,
    route_registry: RouteRegistry,
    source: str,
) -> None:
    if not spec.allowed_context.matter_scoped:
        raise UnboundedAuthorityError(
            f"allowed context must be scoped to one matter: {source}"
        )
    if not spec.escalation_target.startswith(HUMAN_ESCALATION_PREFIX):
        raise UnboundedAuthorityError(
            f"escalation target must name a human review queue "
            f"({HUMAN_ESCALATION_PREFIX}<queue>): {source}"
        )
    for tool_id in spec.tool_allowlist:
        if tool_id not in KNOWN_TOOL_IDS:
            raise UnknownToolError(
                f"tool is not in the known domain-tool catalog: {source}: {tool_id}"
            )
    for route_id in spec.model_routes:
        try:
            route = route_registry.route(route_id)
        except RouteNotFoundError as exc:
            raise UnknownModelRouteError(
                f"model route is not pinned in the gateway registry: "
                f"{source}: {route_id}"
            ) from exc
        if not route.enabled:
            raise ModelRouteNotEnabledError(
                f"model route is pinned but disabled: {source}: {route_id}"
            )
        if classification_rank(spec.allowed_context.classification_ceiling) > (
            classification_rank(route.egress_classification_ceiling)
        ):
            raise UnboundedAuthorityError(
                f"allowed-context ceiling exceeds the route egress ceiling: "
                f"{source}: {route_id}"
            )
        if spec.retry_class != route.retry_class:
            raise SpecValidationError(
                f"spec retry class must match the pinned route retry class: "
                f"{source}: {route_id}"
            )


class AgentSpecRegistry:
    """Immutable lookup over validated, hash-pinned agent spec versions."""

    def __init__(self, records: tuple[AgentSpecRecord, ...]) -> None:
        seen: dict[tuple[str, int], AgentSpecRecord] = {}
        for record in records:
            key = (record.spec.spec_id, record.spec.version)
            existing = seen.get(key)
            if existing is not None:
                if existing.spec_sha256 != record.spec_sha256:
                    raise SpecVersionImmutableError(
                        f"spec version is immutable and already pinned: "
                        f"{key[0]} version {key[1]}"
                    )
                continue
            seen[key] = record
        self._records = seen

    @property
    def records(self) -> tuple[AgentSpecRecord, ...]:
        return tuple(
            self._records[key]
            for key in sorted(self._records, key=lambda item: (item[0], item[1]))
        )

    def versions(self, spec_id: str) -> tuple[int, ...]:
        versions = sorted(
            version for (known_id, version) in self._records if known_id == spec_id
        )
        if not versions:
            raise SpecNotFoundError(f"no versions pinned for spec: {spec_id}")
        return tuple(versions)

    def spec(self, spec_id: str, version: int | None = None) -> AgentSpecRecord:
        if version is None:
            version = self.versions(spec_id)[-1]
        record = self._records.get((spec_id, version))
        if record is None:
            raise SpecNotFoundError(
                f"spec version is not pinned in the registry: "
                f"{spec_id} version {version}"
            )
        return record

    def with_spec_file(
        self,
        path: Path,
        *,
        route_registry: RouteRegistry,
        expected_hash: str | None = None,
    ) -> Self:
        """Return a registry with one more version; never mutate in place.

        Re-registering the identical document is an idempotent no-op. A
        different document claiming an existing version is rejected.
        """

        record = _load_record(
            path, route_registry=route_registry, expected_hash=expected_hash
        )
        key = (record.spec.spec_id, record.spec.version)
        existing = self._records.get(key)
        if existing is not None:
            if existing.spec_sha256 == record.spec_sha256:
                return self
            raise SpecVersionImmutableError(
                f"spec version is immutable and already pinned: "
                f"{key[0]} version {key[1]}"
            )
        return type(self)((*self.records, record))

    @classmethod
    def from_files(
        cls,
        paths: tuple[Path, ...] | list[Path],
        *,
        route_registry: RouteRegistry,
        expected_hashes: Mapping[str, str] | None = None,
    ) -> Self:
        if not paths:
            raise SpecValidationError("spec registry requires at least one spec file")
        records = []
        for path in paths:
            expected = None
            if expected_hashes is not None:
                expected = expected_hashes.get(path.name)
                if expected is None:
                    raise SpecIntegrityError(
                        f"spec file is not covered by the pinned manifest: {path.name}"
                    )
            records.append(
                _load_record(
                    path, route_registry=route_registry, expected_hash=expected
                )
            )
        return cls(tuple(records))

    @classmethod
    def from_directory(
        cls,
        directory: Path,
        *,
        route_registry: RouteRegistry,
        expected_hashes: Mapping[str, str] | None = None,
    ) -> Self:
        paths = sorted(directory.glob("*.json"))
        if expected_hashes is not None:
            pinned = set(expected_hashes)
            listed = {path.name for path in paths}
            if pinned != listed:
                raise SpecIntegrityError(
                    "pinned manifest must cover exactly the spec files present"
                )
        return cls.from_files(
            paths, route_registry=route_registry, expected_hashes=expected_hashes
        )
