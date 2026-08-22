"""Fail-closed legal-source registry and deterministic connector health logic."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class RightsState(StrEnum):
    VERIFIED_PERMISSIVE = "verified_permissive"
    VERIFIED_RESTRICTED = "verified_restricted"
    OWNER_AUTHORIZED = "owner_authorized"
    PUBLIC_DOMAIN_VERIFIED = "public_domain_verified"
    UNKNOWN = "unknown"
    UNVERIFIED = "unverified"
    EXPIRED = "expired"
    REVOKED = "revoked"


class SourceHealthStatus(StrEnum):
    HEALTHY = "healthy"
    STALE = "stale"
    RATE_LIMITED = "rate_limited"
    OUTAGE = "outage"
    QUARANTINED = "quarantined"
    REVOKED = "revoked"


class RegistryValidationError(ValueError):
    """Raised when a source record cannot satisfy its claimed status."""


class SourceAccessDenied(PermissionError):
    """Raised when a source is not eligible for the requested purpose."""


class ConnectorUnavailable(RuntimeError):
    """Raised when connector health prevents an acquisition attempt."""


_USABLE_RIGHTS = frozenset(
    {
        RightsState.VERIFIED_PERMISSIVE,
        RightsState.VERIFIED_RESTRICTED,
        RightsState.OWNER_AUTHORIZED,
        RightsState.PUBLIC_DOMAIN_VERIFIED,
    }
)


class LegalSourceConnector(BaseModel):
    """A versioned, non-secret connector record for one legal source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    connector_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,79}$")
    display_name: str = Field(min_length=3, max_length=240)
    canonical_locator: HttpUrl
    health_locator: HttpUrl | None = None
    source_role: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    jurisdiction: str = Field(min_length=2, max_length=80)
    publisher: str = Field(min_length=2, max_length=240)
    terms_locator: HttpUrl | None = None
    license_summary: str = Field(min_length=3, max_length=500)
    attribution_notice: str = Field(min_length=1, max_length=500)
    terms_evidence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rights_state: RightsState
    rights_reviewed_by: str | None = None
    rights_reviewed_at: datetime | None = None
    allowed_purposes: frozenset[str] = frozenset()
    account_owner: str | None = None
    account_required: bool = False
    credential_reference: str | None = None
    rate_limit_requests: int = Field(ge=1, le=1_000_000)
    rate_limit_window_seconds: int = Field(ge=1, le=86_400)
    freshness_seconds: int = Field(ge=60, le=31_536_000)
    enabled: bool = False

    @model_validator(mode="after")
    def validate_governed_enablement(self) -> LegalSourceConnector:
        reviewed = self.rights_reviewed_by and self.rights_reviewed_at
        if self.rights_reviewed_at is not None and (
            self.rights_reviewed_at.tzinfo is None
            or self.rights_reviewed_at.utcoffset() != timedelta(0)
        ):
            raise RegistryValidationError("rights review timestamp must be UTC")
        if self.enabled and self.rights_state not in _USABLE_RIGHTS:
            raise RegistryValidationError("enabled connector requires reviewed rights")
        if self.enabled and not (reviewed and self.terms_evidence_sha256):
            raise RegistryValidationError(
                "enabled connector requires reviewer and terms evidence hash"
            )
        if self.enabled and not self.allowed_purposes:
            raise RegistryValidationError("enabled connector requires allowed purposes")
        if self.account_required and not self.account_owner:
            raise RegistryValidationError("account-required connector needs an owner")
        if self.enabled and self.account_required and not self.credential_reference:
            raise RegistryValidationError(
                "enabled account-required connector needs a secret-store reference"
            )
        return self


class ConnectorHealth(BaseModel):
    """Non-secret observed connector state, suitable for health projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    connector_id: str
    observed_at: datetime
    last_success_at: datetime | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    retry_after: datetime | None = None
    account_active: bool = True
    credential_reference: str | None = None

    @model_validator(mode="after")
    def validate_timestamps(self) -> ConnectorHealth:
        for value in (self.observed_at, self.last_success_at, self.retry_after):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() != timedelta(0)
            ):
                raise RegistryValidationError("health timestamps must be UTC")
        return self


class SourceRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    registry_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    connectors: tuple[LegalSourceConnector, ...]

    @model_validator(mode="after")
    def require_unique_connectors(self) -> SourceRegistry:
        identifiers = [item.connector_id for item in self.connectors]
        if len(identifiers) != len(set(identifiers)):
            raise RegistryValidationError("connector identifiers must be unique")
        return self


def load_registry(path: Path) -> SourceRegistry:
    """Load a local reviewed registry, rejecting malformed records."""

    return SourceRegistry.model_validate_json(path.read_text(encoding="utf-8"))


def sha256_hex(content: bytes) -> str:
    """Return the exact source hash captured by a caller at acquisition time."""

    return hashlib.sha256(content).hexdigest()


class ConnectorRegistry:
    """Pure access and health decisions for source connector metadata."""

    def __init__(self, registry: SourceRegistry) -> None:
        self._connectors = {item.connector_id: item for item in registry.connectors}

    def get(self, connector_id: str) -> LegalSourceConnector:
        try:
            return self._connectors[connector_id]
        except KeyError as error:
            raise SourceAccessDenied("unknown_connector") from error

    def require_purpose(self, connector_id: str, purpose: str) -> LegalSourceConnector:
        connector = self.get(connector_id)
        if connector.rights_state == RightsState.REVOKED:
            raise SourceAccessDenied("rights_revoked")
        if not connector.enabled or connector.rights_state not in _USABLE_RIGHTS:
            raise SourceAccessDenied("rights_quarantined")
        if purpose not in connector.allowed_purposes:
            raise SourceAccessDenied("purpose_not_authorized")
        return connector

    def health_status(
        self, connector_id: str, health: ConnectorHealth
    ) -> SourceHealthStatus:
        connector = self.get(connector_id)
        if connector.rights_state == RightsState.REVOKED or not health.account_active:
            return SourceHealthStatus.REVOKED
        if connector.account_required and (
            health.credential_reference != connector.credential_reference
        ):
            return SourceHealthStatus.REVOKED
        if not connector.enabled or connector.rights_state not in _USABLE_RIGHTS:
            return SourceHealthStatus.QUARANTINED
        if health.retry_after is not None and health.retry_after > health.observed_at:
            return SourceHealthStatus.RATE_LIMITED
        if health.consecutive_failures:
            return SourceHealthStatus.OUTAGE
        if health.last_success_at is None or (
            health.observed_at - health.last_success_at
            > timedelta(seconds=connector.freshness_seconds)
        ):
            return SourceHealthStatus.STALE
        return SourceHealthStatus.HEALTHY

    def require_available(
        self, connector_id: str, health: ConnectorHealth, purpose: str
    ) -> LegalSourceConnector:
        connector = self.require_purpose(connector_id, purpose)
        status = self.health_status(connector_id, health)
        if status != SourceHealthStatus.HEALTHY:
            raise ConnectorUnavailable(status.value)
        return connector


def registry_json(registry: SourceRegistry) -> str:
    """Produce stable JSON for evidence hashing without writing to disk."""

    return json.dumps(
        registry.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
