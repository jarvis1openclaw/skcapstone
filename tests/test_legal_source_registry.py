"""Contract tests for the governed legal-source connector registry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sklegal_legal_sources import (
    ConnectorHealth,
    ConnectorRegistry,
    ConnectorUnavailable,
    LegalSourceConnector,
    RightsState,
    SourceAccessDenied,
    SourceHealthStatus,
    load_registry,
    sha256_hex,
)

REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "legal-sources"
    / "connector-registry.json"
)
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def enabled_connector(**changes: object) -> LegalSourceConnector:
    values: dict[str, object] = {
        "connector_id": "fixture-source",
        "display_name": "Fixture Official Source",
        "canonical_locator": "https://example.test/source",
        "health_locator": "https://example.test/health",
        "source_role": "official_court",
        "jurisdiction": "US-IL",
        "publisher": "Fixture Court",
        "terms_locator": "https://example.test/terms",
        "license_summary": "Reviewed fixture terms.",
        "attribution_notice": "Fixture attribution.",
        "terms_evidence_sha256": "a" * 64,
        "rights_state": RightsState.VERIFIED_RESTRICTED,
        "rights_reviewed_by": "qualified-reviewer",
        "rights_reviewed_at": NOW,
        "allowed_purposes": frozenset({"read", "retrieve"}),
        "account_owner": "records-owner",
        "account_required": True,
        "credential_reference": "secret://sklegal/fixture-source/v2",
        "rate_limit_requests": 10,
        "rate_limit_window_seconds": 60,
        "freshness_seconds": 300,
        "enabled": True,
    }
    values.update(changes)
    return LegalSourceConnector.model_validate(values)


def healthy(connector_id: str = "fixture-source", **changes: object) -> ConnectorHealth:
    values: dict[str, object] = {
        "connector_id": connector_id,
        "observed_at": NOW,
        "last_success_at": NOW - timedelta(seconds=1),
        "credential_reference": "secret://sklegal/fixture-source/v2",
    }
    values.update(changes)
    return ConnectorHealth.model_validate(values)


def test_initial_inventory_covers_required_source_families_and_quarantines_them() -> (
    None
):
    registry = load_registry(REGISTRY_PATH)
    connectors = ConnectorRegistry(registry)
    assert {
        "govinfo",
        "congress-gov",
        "supreme-court",
        "uscourts",
        "illinois-general-assembly",
        "illinois-courts",
        "illinois-register",
        "courtlistener",
    } <= set(item.connector_id for item in registry.connectors)
    with pytest.raises(SourceAccessDenied, match="rights_quarantined"):
        connectors.require_purpose("govinfo", "read")


def test_enabled_connector_requires_terms_evidence_owner_and_secret_reference() -> None:
    with pytest.raises(ValueError, match="terms evidence hash"):
        enabled_connector(terms_evidence_sha256=None)
    with pytest.raises(ValueError, match="secret-store reference"):
        enabled_connector(credential_reference=None)
    with pytest.raises(ValueError, match="account-required connector"):
        enabled_connector(enabled=False, account_owner=None)


def test_rate_limit_backoff_blocks_preflight() -> None:
    registry = ConnectorRegistry(
        load_registry(REGISTRY_PATH).model_copy(
            update={"connectors": (enabled_connector(),)}
        )
    )
    status = registry.health_status(
        "fixture-source", healthy(retry_after=NOW + timedelta(seconds=30))
    )
    assert status == SourceHealthStatus.RATE_LIMITED
    with pytest.raises(ConnectorUnavailable, match="rate_limited"):
        registry.require_available(
            "fixture-source",
            healthy(retry_after=NOW + timedelta(seconds=30)),
            "read",
        )


def test_stale_outage_and_revoked_account_fail_closed() -> None:
    registry = ConnectorRegistry(
        load_registry(REGISTRY_PATH).model_copy(
            update={"connectors": (enabled_connector(),)}
        )
    )
    assert (
        registry.health_status(
            "fixture-source", healthy(last_success_at=NOW - timedelta(seconds=301))
        )
        == SourceHealthStatus.STALE
    )
    assert (
        registry.health_status("fixture-source", healthy(consecutive_failures=1))
        == SourceHealthStatus.OUTAGE
    )
    assert (
        registry.health_status("fixture-source", healthy(account_active=False))
        == SourceHealthStatus.REVOKED
    )
    assert (
        registry.health_status(
            "fixture-source",
            healthy(credential_reference="secret://sklegal/fixture-source/v1"),
        )
        == SourceHealthStatus.REVOKED
    )


def test_rights_revocation_and_hash_capture_are_exact() -> None:
    revoked = enabled_connector(rights_state=RightsState.REVOKED, enabled=False)
    registry = ConnectorRegistry(
        load_registry(REGISTRY_PATH).model_copy(update={"connectors": (revoked,)})
    )
    with pytest.raises(SourceAccessDenied, match="rights_revoked"):
        registry.require_purpose("fixture-source", "read")
    expected = "".join(
        (
            "a2d594c1",
            "8b0995c3",
            "686d04d2",
            "a4768310",
            "6f28dbe5",
            "80d2ec90",
            "441fbe44",
            "4d6d30b0",
        )
    )
    assert sha256_hex(b"official source bytes") == expected
