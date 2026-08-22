"""Governed registry for official and free legal research sources.

This package is intentionally metadata and policy only.  It cannot perform a
network request, create an account, accept terms, or promote content into a
corpus.  Those effects remain behind separately approved workflows.
"""

from .registry import (
    ConnectorHealth,
    ConnectorRegistry,
    ConnectorUnavailable,
    LegalSourceConnector,
    RegistryValidationError,
    RightsState,
    SourceAccessDenied,
    SourceHealthStatus,
    SourceRegistry,
    load_registry,
    sha256_hex,
)

__all__ = [
    "ConnectorHealth",
    "ConnectorRegistry",
    "ConnectorUnavailable",
    "LegalSourceConnector",
    "RegistryValidationError",
    "RightsState",
    "SourceAccessDenied",
    "SourceHealthStatus",
    "SourceRegistry",
    "load_registry",
    "sha256_hex",
]
