"""Aggregate persistence contract suite over the per-aggregate modules.

The contract tests live in the persistence_contract_* modules beside this
file. This module re-exports every contract class so the historical dotted
path tests.integration.test_persistence_contract keeps running the full
suite exactly as before the split. Import order below is deliberate: it
matches the historical alphabetical test execution order, which the shared
seeded database depends on.
"""

# ruff: noqa: I001

import unittest

from tests.integration.persistence_contract_migrations import (
    PersistenceContract00MigrationPreflightTests,
)
from tests.integration.persistence_contract_security_boundary import (
    PersistenceContract01SecurityBoundaryTests,
)
from tests.integration.persistence_contract_scope_privilege import (
    PersistenceContract02ScopePrivilegeTests,
)
from tests.integration.persistence_contract_matter_records import (
    PersistenceContract03MatterRecordTests,
)
from tests.integration.persistence_contract_matter_round_trips import (
    PersistenceContract04MatterRoundTripTests,
)
from tests.integration.persistence_contract_artifact_consumer import (
    PersistenceContract05ArtifactConsumerTests,
)
from tests.integration.persistence_contract_communication_work_product import (
    PersistenceContract06CommunicationWorkProductTests,
)
from tests.integration.persistence_contract_work_product_gates import (
    PersistenceContract07WorkProductGateTests,
)
from tests.integration.persistence_contract_work_product_drafting import (
    PersistenceContract07BWorkProductDraftingTests,
)
from tests.integration.persistence_contract_canonical_parity import (
    PersistenceContract08CanonicalParityTests,
)
from tests.integration.persistence_contract_audit_chain import (
    PersistenceContract09AuditChainTests,
)
from tests.integration.persistence_contract_outbox_watermark import (
    PersistenceContract10OutboxWatermarkTests,
)
from tests.integration.persistence_contract_migration_guards import (
    PersistenceContract11MigrationGuardTests,
)

__all__ = [
    "PersistenceContract00MigrationPreflightTests",
    "PersistenceContract01SecurityBoundaryTests",
    "PersistenceContract02ScopePrivilegeTests",
    "PersistenceContract03MatterRecordTests",
    "PersistenceContract04MatterRoundTripTests",
    "PersistenceContract05ArtifactConsumerTests",
    "PersistenceContract06CommunicationWorkProductTests",
    "PersistenceContract07WorkProductGateTests",
    "PersistenceContract07BWorkProductDraftingTests",
    "PersistenceContract08CanonicalParityTests",
    "PersistenceContract09AuditChainTests",
    "PersistenceContract10OutboxWatermarkTests",
    "PersistenceContract11MigrationGuardTests",
]


if __name__ == "__main__":
    unittest.main()
