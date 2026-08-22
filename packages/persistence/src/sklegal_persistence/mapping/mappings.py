"""Aggregate entity mapping registry combined from every aggregate module."""

from .claim_ledger import CLAIM_LEDGER_MAPPINGS
from .claims import CLAIM_MAPPINGS
from .contract import EntityMapping
from .facts import FACT_MAPPINGS
from .integration import INTEGRATION_MAPPINGS
from .security import SECURITY_MAPPINGS
from .structure import STRUCTURE_MAPPINGS
from .work import WORK_MAPPINGS
from .work_product import WORK_PRODUCT_MAPPINGS

MAPPINGS: dict[str, EntityMapping] = {
    item.entity_name: item
    for item in (
        *SECURITY_MAPPINGS,
        *STRUCTURE_MAPPINGS,
        *FACT_MAPPINGS,
        *CLAIM_MAPPINGS,
        *CLAIM_LEDGER_MAPPINGS,
        *WORK_MAPPINGS,
        *WORK_PRODUCT_MAPPINGS,
        *INTEGRATION_MAPPINGS,
    )
}
