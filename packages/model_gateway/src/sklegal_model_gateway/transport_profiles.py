"""Deployment-only transport profile store (SKL-S3-10).

Profiles are environment records, not domain state. They resolve base
addresses and secret references behind the logical route so switching the
deployment binding between direct Qwen and SKGateway never touches agent
specs, prompt or output schema pins, or legal gates. Every profile carries a
pinned configuration hash: a loaded record that no longer matches its hash is
stale and fails closed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

from .errors import (
    TransportBindingError,
    TransportProfileDisabledError,
    TransportProfileNotFoundError,
    TransportProfileStaleError,
)
from .models import ModelRouteRecord, Provider, TransportKind, TransportProfile
from .registry import sha256_text

PROFILE_STORE_SCHEMA = "sklegal-transport-profile-store/v1"

# Which logical provider each transport kind may bind. A profile that claims
# the wrong provider kind is a binding error, not a silent rewrite.
_KIND_BINDINGS: dict[TransportKind, frozenset[Provider]] = {
    TransportKind.DIRECT_QWEN: frozenset({Provider.QWEN_LOCAL}),
    TransportKind.SKGATEWAY_CHAT: frozenset({Provider.QWEN_LOCAL, Provider.OPENAI}),
    TransportKind.OPENAI_RESPONSES: frozenset({Provider.OPENAI}),
}


def profile_content_sha256(profile: TransportProfile) -> str:
    """Hash of the canonical profile content without the hash itself."""

    content = profile.content_sha256_fields()
    return sha256_text(json.dumps(content, sort_keys=True, separators=(",", ":")))


class ProfileStoreFile(BaseModel):
    """Validated on-disk shape of a transport profile store."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    schema_marker: str = Field(
        alias="schema", pattern=f"^{PROFILE_STORE_SCHEMA}$"
    )
    purpose: str = Field(min_length=1)
    profiles: list[TransportProfile] = Field(min_length=1)


class TransportProfileStore:
    """Immutable lookup of deployment transport profiles by identifier."""

    def __init__(
        self,
        profiles: tuple[TransportProfile, ...],
        *,
        store_revision: str,
    ) -> None:
        if not profiles:
            raise ValueError("transport profile store cannot be empty")
        ids = [profile.profile_id for profile in profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("transport profile identifiers must be unique")
        for profile in profiles:
            if profile_content_sha256(profile) != profile.config_sha256:
                raise TransportProfileStaleError(
                    "transport profile no longer matches its pinned hash: "
                    f"{profile.profile_id}"
                )
        self._profiles = {profile.profile_id: profile for profile in profiles}
        self._store_revision = store_revision

    @property
    def store_revision(self) -> str:
        return self._store_revision

    @property
    def profiles(self) -> tuple[TransportProfile, ...]:
        return tuple(self._profiles[pid] for pid in sorted(self._profiles))

    def profile(self, profile_id: str) -> TransportProfile:
        record = self._profiles.get(profile_id)
        if record is None:
            raise TransportProfileNotFoundError(
                f"transport profile is not pinned in the store: {profile_id}"
            )
        return record

    def resolve(self, route: ModelRouteRecord) -> TransportProfile:
        """Resolve the binding for one route; deny unknown, disabled, stale."""

        profile = self.profile(route.transport_profile_id)
        if not profile.enabled:
            raise TransportProfileDisabledError(
                f"transport profile is disabled: {profile.profile_id}"
            )
        if profile_content_sha256(profile) != profile.config_sha256:
            raise TransportProfileStaleError(
                f"transport profile no longer matches its pinned hash: "
                f"{profile.profile_id}"
            )
        allowed = _KIND_BINDINGS.get(profile.kind, frozenset())
        if route.provider not in allowed:
            raise TransportBindingError(
                f"transport kind {profile.kind} cannot bind provider "
                f"{route.provider} on route {route.route_id}"
            )
        return profile

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        store_revision: str,
    ) -> Self:
        if payload.get("schema") != PROFILE_STORE_SCHEMA:
            raise ValueError("profile store schema marker is missing or unknown")
        document = ProfileStoreFile.model_validate(payload)
        return cls(tuple(document.profiles), store_revision=store_revision)

    @classmethod
    def from_file(cls, path: Path) -> Self:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("transport profile store must contain a JSON object")
        return cls.from_payload(payload, store_revision=sha256_text(text))
