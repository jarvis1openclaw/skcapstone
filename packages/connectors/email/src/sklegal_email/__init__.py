"""Simulation-first email connector."""

from .connector import (
    EmailAction,
    EmailReceipt,
    EmailSimulation,
    EmailValidationError,
    ReceiptStatus,
)

__all__ = [
    "EmailAction",
    "EmailReceipt",
    "EmailSimulation",
    "EmailValidationError",
    "ReceiptStatus",
]
