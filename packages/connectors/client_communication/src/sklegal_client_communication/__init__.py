"""Matter-scoped, privilege-labeled client communication simulation."""

from .connector import (
    ClientCommunicationConnector,
    CommunicationAction,
    CommunicationProviderResponse,
    CommunicationReceipt,
    CommunicationReceiptStatus,
)

__all__ = [
    "ClientCommunicationConnector",
    "CommunicationAction",
    "CommunicationProviderResponse",
    "CommunicationReceipt",
    "CommunicationReceiptStatus",
]
