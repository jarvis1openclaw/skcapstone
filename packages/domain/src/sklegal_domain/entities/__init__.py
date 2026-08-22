"""Typed SKLegal entities and legal-domain state machines."""

from .claim_ledger import ClaimSupport, LedgerClaim
from .claims import Claim, Defense, Element, Issue, Remedy
from .facts import (
    Authority,
    CustodyEvent,
    EvidenceItem,
    FactAssertion,
    TensionGroup,
    effective_at,
)
from .integration import (
    Approval,
    Execution,
    ExecutionEvent,
    ExecutionReceipt,
    ValidationResult,
)
from .security import Client, Engagement, Tenant
from .structure import (
    Forum,
    Matter,
    MatterEvent,
    Party,
    PartyRole,
    Proceeding,
    Transaction,
)
from .work import Communication, Deadline, DeadlineCalculation, Task
from .work_product import WorkProduct, WorkProductVersion

__all__ = [
    "Approval",
    "Authority",
    "Claim",
    "ClaimSupport",
    "Client",
    "Communication",
    "CustodyEvent",
    "Deadline",
    "DeadlineCalculation",
    "Defense",
    "Element",
    "Engagement",
    "EvidenceItem",
    "Execution",
    "ExecutionEvent",
    "ExecutionReceipt",
    "FactAssertion",
    "Forum",
    "Issue",
    "LedgerClaim",
    "Matter",
    "MatterEvent",
    "Party",
    "PartyRole",
    "Proceeding",
    "Remedy",
    "Task",
    "Tenant",
    "TensionGroup",
    "Transaction",
    "ValidationResult",
    "WorkProduct",
    "WorkProductVersion",
    "effective_at",
]
