"""Human-approved structured import for the Liberty Auto pilot.

This module executes pilot TDD phase B on top of the S5-01A dry run. It
imports a :class:`~sklegal_migration.pilot.PilotImportPlan` into the
isolated SKLegal pilot store only when an explicit human mapping approval
covers the exact import batch and every proposed idempotency key. Without
that approval the import fails closed before any write.

Guarantees enforced here and in migration ``0016_pilot_import.sql``:

- every import write carries a deterministic idempotency key, so rerunning
  the same source snapshot creates no duplicates
- changed source content produces a new batch, a new source version, and a
  new proposed mapping revision while unchanged records are suppressed
- approval and execution states import as negative states only
  (``pending_review`` / ``not_started``); no state is advanced
- tension groups import as unresolved, review-required records and are
  never silently harmonized
- rollback marks the batch withdrawn, records withdrawn targets so their
  UUIDs are never reused, and permits physical row deletion only through
  a disposable-store reset of an already-withdrawn batch
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from .dry_run import SOURCE_SYSTEM, SourceInventoryEntry
from .pilot import (
    AtomicFactProposal,
    ImportRecord,
    LegacySourceFile,
    PilotImportPlan,
    TensionProposal,
    _json_value,
)

NIL_UUID = "00000000-0000-0000-0000-000000000000"
APPROVAL_STATE = "pending_review"
EXECUTION_STATE = "not_started"


class ImportGateError(ValueError):
    """Raised when an import gate fails closed before or during writes."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def state_idempotency_key(target_type: str, target_id: str, state_kind: str) -> str:
    """Deterministic key for one imported negative state row."""
    material = "|".join((SOURCE_SYSTEM, target_type, target_id, state_kind))
    return _sha256(material.encode("utf-8"))


@dataclass(frozen=True)
class MappingApproval:
    """Explicit human approval of one dry-run mapping review.

    ``approved_idempotency_keys`` must exactly match the plan's record
    keys: each key embeds the source content hash, so approval binds the
    exact reviewed source version.
    """

    import_batch_id: str
    reviewer: str
    decided_at: str
    decision: str
    approved_idempotency_keys: tuple[str, ...]
    review_artifact: str


def require_import_approval(
    plan: PilotImportPlan,
    approval: MappingApproval | None,
) -> MappingApproval:
    """Fail closed unless the approval exactly covers the plan."""
    if approval is None:
        raise ImportGateError(
            "pilot import requires an explicit human mapping approval"
        )
    if approval.decision != "approved":
        raise ImportGateError(
            f"mapping review decision is {approval.decision!r}, not 'approved'"
        )
    if not approval.reviewer.strip():
        raise ImportGateError("mapping approval reviewer must be non-empty")
    if not approval.review_artifact.strip():
        raise ImportGateError("mapping approval review artifact must be non-empty")
    if not approval.decided_at.strip():
        raise ImportGateError("mapping approval decision time must be non-empty")
    if approval.import_batch_id != plan.import_batch_id:
        raise ImportGateError(
            "mapping approval batch does not match the import plan batch"
        )
    required = {record.idempotency_key for record in plan.records}
    approved = set(approval.approved_idempotency_keys)
    if approved != required:
        missing = sorted(required - approved)
        extra = sorted(approved - required)
        raise ImportGateError(
            "mapping approval does not exactly cover the proposed "
            f"idempotency keys (missing={missing}, unexpected={extra})"
        )
    return approval


@dataclass(frozen=True)
class ImportStateRow:
    """One negative approval or execution state for an imported target."""

    idempotency_key: str
    batch_id: str
    target_type: str
    target_id: str
    state_kind: str
    state_value: str


@dataclass(frozen=True)
class ApprovedImportResult:
    """Counts and reconciliation evidence for one approved import run."""

    import_batch_id: str
    tenant_id: str
    batch_created: bool
    rerun: bool
    records_created: int
    records_suppressed: int
    facts_created: int
    facts_suppressed: int
    tensions_created: int
    tensions_suppressed: int
    states_created: int
    states_suppressed: int
    source_files_recorded: int
    source_files_suppressed: int
    reconciliation: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "import_batch_id": self.import_batch_id,
            "tenant_id": self.tenant_id,
            "batch_created": self.batch_created,
            "rerun": self.rerun,
            "records_created": self.records_created,
            "records_suppressed": self.records_suppressed,
            "facts_created": self.facts_created,
            "facts_suppressed": self.facts_suppressed,
            "tensions_created": self.tensions_created,
            "tensions_suppressed": self.tensions_suppressed,
            "states_created": self.states_created,
            "states_suppressed": self.states_suppressed,
            "source_files_recorded": self.source_files_recorded,
            "source_files_suppressed": self.source_files_suppressed,
            "reconciliation": dict(self.reconciliation),
        }

    def json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


class PilotImportStore(Protocol):
    """Storage contract for the isolated pilot import.

    Every ``add_*`` method is insert-if-absent on the row idempotency key
    and returns True only when the row was actually inserted.
    """

    disposable: bool

    def batch_status(self, batch_id: str) -> str | None: ...

    def create_batch(
        self,
        *,
        plan: PilotImportPlan,
        approval: MappingApproval,
        tenant_id: str,
    ) -> bool: ...

    def add_source_file(self, batch_id: str, item: LegacySourceFile) -> bool: ...

    def max_revision(self, target_type: str, target_id: str) -> int: ...

    def add_record(
        self,
        record: ImportRecord,
        *,
        batch_id: str,
        tenant_id: str,
        revision: int,
        reviewed_by: str,
    ) -> bool: ...

    def add_fact(self, batch_id: str, fact: AtomicFactProposal) -> bool: ...

    def add_tension(self, batch_id: str, tension: TensionProposal) -> bool: ...

    def add_state(self, row: ImportStateRow) -> bool: ...

    def withdrawn_targets(self) -> frozenset[tuple[str, str]]: ...

    def withdraw_batch(self, batch_id: str, *, withdrawn_at: str) -> bool: ...

    def delete_batch_children(self, batch_id: str) -> Mapping[str, int]: ...

    def counts(self) -> Mapping[str, int]: ...


def _validate_tenant_id(tenant_id: str) -> str:
    try:
        parsed = UUID(tenant_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ImportGateError("tenant_id must be a UUID") from exc
    if str(parsed) == NIL_UUID:
        raise ImportGateError("tenant_id cannot be the nil UUID")
    return str(parsed)


def _reconcile_inventory(
    plan: PilotImportPlan,
    inventory: tuple[SourceInventoryEntry, ...],
) -> dict[str, Any]:
    """Prove the plan covers the source inventory before any write."""
    plan_by_path = {item.relative_path: item for item in plan.source_files}
    missing = sorted(
        entry.relative_path
        for entry in inventory
        if entry.relative_path not in plan_by_path
    )
    mismatched = sorted(
        entry.relative_path
        for entry in inventory
        if entry.relative_path in plan_by_path
        and plan_by_path[entry.relative_path].content_sha256 != entry.content_sha256
    )
    inventory_paths = {entry.relative_path for entry in inventory}
    outside = sorted(set(plan_by_path) - inventory_paths)
    reconciliation: dict[str, Any] = {
        "inventoried_files": len(inventory),
        "plan_source_files": len(plan.source_files),
        "missing_from_plan": missing,
        "hash_mismatches": mismatched,
        "plan_sources_outside_inventory": outside,
        "changed_sources": list(plan.changed_sources),
    }
    if missing or mismatched:
        raise ImportGateError(
            "source inventory reconciliation failed closed: "
            f"missing_from_plan={missing} hash_mismatches={mismatched}"
        )
    return reconciliation


def run_approved_import(
    plan: PilotImportPlan,
    *,
    approval: MappingApproval | None,
    inventory: Iterable[SourceInventoryEntry],
    store: PilotImportStore,
    tenant_id: str,
) -> ApprovedImportResult:
    """Import an approved pilot plan into the isolated store.

    Every gate runs before the first write: human approval, zero
    HammerTime mutations, inventory reconciliation, withdrawn-batch and
    withdrawn-target reuse checks. All writes are insert-if-absent on
    deterministic idempotency keys, so a rerun suppresses every row.
    """
    resolved = require_import_approval(plan, approval)
    if plan.hammer_time_mutation:
        raise ImportGateError("import plan carries HammerTime write operations")
    if not plan.records:
        raise ImportGateError("import plan has no records")
    tenant = _validate_tenant_id(tenant_id)
    entries = tuple(inventory)
    if not entries:
        raise ImportGateError("source inventory must be non-empty")
    reconciliation = _reconcile_inventory(plan, entries)

    status = store.batch_status(plan.import_batch_id)
    if status == "withdrawn":
        raise ImportGateError("import batch is withdrawn; refusing to reuse it")
    withdrawn = store.withdrawn_targets()
    reused = sorted(
        f"{record.target_type}:{record.target_id}"
        for record in plan.records
        if (record.target_type, record.target_id) in withdrawn
    )
    if reused:
        raise ImportGateError(
            f"import targets were withdrawn and cannot be reused: {reused}"
        )

    batch_created = store.create_batch(
        plan=plan,
        approval=resolved,
        tenant_id=tenant,
    )

    files_recorded = 0
    files_suppressed = 0
    for item in plan.source_files:
        if store.add_source_file(plan.import_batch_id, item):
            files_recorded += 1
        else:
            files_suppressed += 1

    records_created = 0
    records_suppressed = 0
    states_created = 0
    states_suppressed = 0
    for record in plan.records:
        revision = store.max_revision(record.target_type, record.target_id) + 1
        if store.add_record(
            record,
            batch_id=plan.import_batch_id,
            tenant_id=tenant,
            revision=revision,
            reviewed_by=resolved.reviewer,
        ):
            records_created += 1
        else:
            records_suppressed += 1
        for kind, value in (
            ("approval", APPROVAL_STATE),
            ("execution", EXECUTION_STATE),
        ):
            row = ImportStateRow(
                idempotency_key=state_idempotency_key(
                    record.target_type, record.target_id, kind
                ),
                batch_id=plan.import_batch_id,
                target_type=record.target_type,
                target_id=record.target_id,
                state_kind=kind,
                state_value=value,
            )
            if store.add_state(row):
                states_created += 1
            else:
                states_suppressed += 1

    facts_created = 0
    facts_suppressed = 0
    for fact in plan.facts:
        if store.add_fact(plan.import_batch_id, fact):
            facts_created += 1
        else:
            facts_suppressed += 1

    tensions_created = 0
    tensions_suppressed = 0
    for tension in plan.tensions:
        if store.add_tension(plan.import_batch_id, tension):
            tensions_created += 1
        else:
            tensions_suppressed += 1

    reconciliation.update(
        {
            "records_created": records_created,
            "records_suppressed": records_suppressed,
            "fact_assertions_created": facts_created,
            "fact_assertions_suppressed": facts_suppressed,
            "tension_groups_created": tensions_created,
            "tension_groups_suppressed": tensions_suppressed,
            "states_created": states_created,
            "states_suppressed": states_suppressed,
            "source_files_recorded": files_recorded,
            "source_files_suppressed": files_suppressed,
            "approval_states_advanced": 0,
            "execution_states_advanced": 0,
        }
    )
    return ApprovedImportResult(
        import_batch_id=plan.import_batch_id,
        tenant_id=tenant,
        batch_created=batch_created,
        rerun=not batch_created,
        records_created=records_created,
        records_suppressed=records_suppressed,
        facts_created=facts_created,
        facts_suppressed=facts_suppressed,
        tensions_created=tensions_created,
        tensions_suppressed=tensions_suppressed,
        states_created=states_created,
        states_suppressed=states_suppressed,
        source_files_recorded=files_recorded,
        source_files_suppressed=files_suppressed,
        reconciliation=reconciliation,
    )


def withdraw_batch(
    store: PilotImportStore,
    batch_id: str,
    *,
    withdrawn_at: str | None = None,
) -> bool:
    """Mark an import batch withdrawn and pin its targets as non-reusable.

    Child rows (mappings, facts, tensions, and the recorded human
    approval) are preserved. Returns True only when this call performed
    the withdrawal transition.
    """
    stamp = withdrawn_at or datetime.now(UTC).isoformat()
    return store.withdraw_batch(batch_id, withdrawn_at=stamp)


def reset_disposable_batch(
    store: PilotImportStore,
    batch_id: str,
) -> Mapping[str, int]:
    """Physically delete a withdrawn batch's child rows in a disposable store.

    Permitted only for disposable development stores and only after the
    batch is withdrawn. The batch tombstone and the withdrawn-target pins
    survive so withdrawn target UUIDs are never reused.
    """
    if not store.disposable:
        raise ImportGateError(
            "physical pilot import deletion requires a disposable store"
        )
    if store.batch_status(batch_id) != "withdrawn":
        raise ImportGateError(
            "disposable reset requires a withdrawn import batch; failing closed"
        )
    return store.delete_batch_children(batch_id)


class InMemoryPilotImportStore:
    """Deterministic in-memory store for unit tests and dry evidence runs."""

    def __init__(self, *, disposable: bool = True) -> None:
        self.disposable = disposable
        self.batches: dict[str, dict[str, Any]] = {}
        self.source_files: dict[tuple[str, str], LegacySourceFile] = {}
        self.records: dict[str, dict[str, Any]] = {}
        self.facts: dict[str, tuple[str, AtomicFactProposal]] = {}
        self.tensions: dict[tuple[str, str], TensionProposal] = {}
        self.states: dict[str, ImportStateRow] = {}
        self.withdrawn: dict[tuple[str, str], tuple[str, str]] = {}

    def batch_status(self, batch_id: str) -> str | None:
        batch = self.batches.get(batch_id)
        return None if batch is None else str(batch["status"])

    def create_batch(
        self,
        *,
        plan: PilotImportPlan,
        approval: MappingApproval,
        tenant_id: str,
    ) -> bool:
        if plan.import_batch_id in self.batches:
            return False
        self.batches[plan.import_batch_id] = {
            "tenant_id": tenant_id,
            "source_snapshot": plan.source_snapshot,
            "adapter_version": plan.adapter_version,
            "source_file_count": len(plan.source_files),
            "approved_by": approval.reviewer,
            "approved_at": approval.decided_at,
            "review_artifact": approval.review_artifact,
            "status": "imported",
            "withdrawn_at": None,
        }
        return True

    def add_source_file(self, batch_id: str, item: LegacySourceFile) -> bool:
        key = (batch_id, item.relative_path)
        if key in self.source_files:
            return False
        self.source_files[key] = item
        return True

    def max_revision(self, target_type: str, target_id: str) -> int:
        revisions = [
            int(row["revision"])
            for row in self.records.values()
            if row["target_type"] == target_type and row["target_id"] == target_id
        ]
        return max(revisions, default=0)

    def add_record(
        self,
        record: ImportRecord,
        *,
        batch_id: str,
        tenant_id: str,
        revision: int,
        reviewed_by: str,
    ) -> bool:
        if record.idempotency_key in self.records:
            return False
        self.records[record.idempotency_key] = {
            "batch_id": batch_id,
            "tenant_id": tenant_id,
            "target_type": record.target_type,
            "target_id": record.target_id,
            "source_reference_id": record.source_reference_id,
            "mapping_rule": record.mapping_rule,
            "mapping_status": record.mapping_status,
            "revision": revision,
            "reviewed_by": reviewed_by,
        }
        return True

    def add_fact(self, batch_id: str, fact: AtomicFactProposal) -> bool:
        if fact.fact_assertion_id in self.facts:
            return False
        self.facts[fact.fact_assertion_id] = (batch_id, fact)
        return True

    def add_tension(self, batch_id: str, tension: TensionProposal) -> bool:
        key = (tension.key, batch_id)
        if key in self.tensions:
            return False
        self.tensions[key] = tension
        return True

    def add_state(self, row: ImportStateRow) -> bool:
        if row.idempotency_key in self.states:
            return False
        self.states[row.idempotency_key] = row
        return True

    def withdrawn_targets(self) -> frozenset[tuple[str, str]]:
        return frozenset(self.withdrawn)

    def withdraw_batch(self, batch_id: str, *, withdrawn_at: str) -> bool:
        batch = self.batches.get(batch_id)
        if batch is None or batch["status"] != "imported":
            return False
        for row in self.records.values():
            if row["batch_id"] == batch_id:
                self.withdrawn.setdefault(
                    (str(row["target_type"]), str(row["target_id"])),
                    (batch_id, withdrawn_at),
                )
        batch["status"] = "withdrawn"
        batch["withdrawn_at"] = withdrawn_at
        return True

    def delete_batch_children(self, batch_id: str) -> Mapping[str, int]:
        file_keys = [key for key in self.source_files if key[0] == batch_id]
        for file_key in file_keys:
            del self.source_files[file_key]
        record_keys = [
            key for key, row in self.records.items() if row["batch_id"] == batch_id
        ]
        for record_key in record_keys:
            del self.records[record_key]
        fact_keys = [key for key, row in self.facts.items() if row[0] == batch_id]
        for fact_key in fact_keys:
            del self.facts[fact_key]
        tension_keys = [key for key in self.tensions if key[1] == batch_id]
        for tension_key in tension_keys:
            del self.tensions[tension_key]
        state_keys = [
            key for key, row in self.states.items() if row.batch_id == batch_id
        ]
        for state_key in state_keys:
            del self.states[state_key]
        return {
            "source_files": len(file_keys),
            "records": len(record_keys),
            "facts": len(fact_keys),
            "tension_groups": len(tension_keys),
            "states": len(state_keys),
        }

    def counts(self) -> Mapping[str, int]:
        return {
            "batches": len(self.batches),
            "source_files": len(self.source_files),
            "records": len(self.records),
            "facts": len(self.facts),
            "tension_groups": len(self.tensions),
            "states": len(self.states),
            "withdrawn_targets": len(self.withdrawn),
        }


def _unwrap(value: object) -> object:
    if isinstance(value, (list, tuple)) and len(value) == 1:
        return value[0]
    return value


def _inserted(value: object) -> bool:
    text = _text(value)
    if text is None:
        return False
    try:
        return int(text) > 0
    except ValueError:
        return True


def _text(value: object) -> str | None:
    value = _unwrap(value)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_BATCH_INSERT_SQL = """
WITH inserted AS (
    INSERT INTO sklegal_migrations.pilot_import_batches
        (batch_id, tenant_id, source_snapshot, adapter_version,
         source_file_count, approved_by, approved_at, review_artifact)
    VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s::timestamptz, %s)
    ON CONFLICT (batch_id) DO NOTHING
    RETURNING 1
)
SELECT count(*) FROM inserted;
""".strip()

_BATCH_STATUS_SQL = """
SELECT status FROM sklegal_migrations.pilot_import_batches
WHERE batch_id = %s::uuid;
""".strip()

_SOURCE_FILE_INSERT_SQL = """
WITH inserted AS (
    INSERT INTO sklegal_migrations.pilot_import_source_files
        (batch_id, relative_path, content_sha256, byte_count, observed_at)
    VALUES (%s::uuid, %s, %s, %s, %s::timestamptz)
    ON CONFLICT (batch_id, relative_path) DO NOTHING
    RETURNING 1
)
SELECT count(*) FROM inserted;
""".strip()

_MAX_REVISION_SQL = """
SELECT COALESCE(MAX(revision), 0)
FROM sklegal_migrations.pilot_import_records
WHERE target_type = %s AND target_id = %s::uuid;
""".strip()

_RECORD_INSERT_SQL = """
WITH inserted AS (
    INSERT INTO sklegal_migrations.pilot_import_records
        (idempotency_key, batch_id, tenant_id, target_type, target_id,
         source_reference_id, mapping_rule, mapping_status, revision,
         reviewed_by)
    VALUES (%s, %s::uuid, %s::uuid, %s, %s::uuid, %s::uuid, %s, %s, %s, %s)
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING 1
)
SELECT count(*) FROM inserted;
""".strip()

_FACT_INSERT_SQL = """
WITH inserted AS (
    INSERT INTO sklegal_migrations.pilot_import_facts
        (fact_assertion_id, batch_id, predicate, asserted_value, value_type,
         source_reference_id, source_locator, review_status, tension_group_key)
    VALUES (%s::uuid, %s::uuid, %s, %s::jsonb, %s, %s::uuid, %s, %s, %s)
    ON CONFLICT (fact_assertion_id) DO NOTHING
    RETURNING 1
)
SELECT count(*) FROM inserted;
""".strip()

_TENSION_INSERT_SQL = """
WITH inserted AS (
    INSERT INTO sklegal_migrations.pilot_import_tension_groups
        (tension_key, batch_id, assertion_ids, status, review_required)
    VALUES (%s, %s::uuid, %s::uuid[], %s, %s)
    ON CONFLICT (tension_key, batch_id) DO NOTHING
    RETURNING 1
)
SELECT count(*) FROM inserted;
""".strip()

_STATE_INSERT_SQL = """
WITH inserted AS (
    INSERT INTO sklegal_migrations.pilot_import_states
        (idempotency_key, batch_id, target_type, target_id, state_kind,
         state_value)
    VALUES (%s, %s::uuid, %s, %s::uuid, %s, %s)
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING 1
)
SELECT count(*) FROM inserted;
""".strip()

_WITHDRAWN_TARGETS_SQL = """
SELECT COALESCE(
    jsonb_agg(jsonb_build_array(target_type, target_id::text)), '[]'
)::text
FROM sklegal_migrations.pilot_import_withdrawn_targets;
""".strip()

_WITHDRAW_SQL = """
WITH pins AS (
    INSERT INTO sklegal_migrations.pilot_import_withdrawn_targets
        (target_type, target_id, batch_id, withdrawn_at)
    SELECT target_type, target_id, batch_id, %s::timestamptz
    FROM sklegal_migrations.pilot_import_records
    WHERE batch_id = %s::uuid
    ON CONFLICT (target_type, target_id) DO NOTHING
),
updated AS (
    UPDATE sklegal_migrations.pilot_import_batches
    SET status = 'withdrawn', withdrawn_at = %s::timestamptz
    WHERE batch_id = %s::uuid AND status = 'imported'
    RETURNING 1
)
SELECT count(*) FROM updated;
""".strip()

_COUNTS_SQL = """
SELECT jsonb_build_object(
    'batches', (SELECT count(*) FROM sklegal_migrations.pilot_import_batches),
    'source_files',
        (SELECT count(*) FROM sklegal_migrations.pilot_import_source_files),
    'records', (SELECT count(*) FROM sklegal_migrations.pilot_import_records),
    'facts', (SELECT count(*) FROM sklegal_migrations.pilot_import_facts),
    'tension_groups',
        (SELECT count(*) FROM sklegal_migrations.pilot_import_tension_groups),
    'states', (SELECT count(*) FROM sklegal_migrations.pilot_import_states),
    'withdrawn_targets',
        (SELECT count(*) FROM sklegal_migrations.pilot_import_withdrawn_targets)
)::text;
""".strip()

_CHILD_TABLES = (
    "pilot_import_states",
    "pilot_import_tension_groups",
    "pilot_import_facts",
    "pilot_import_records",
    "pilot_import_source_files",
)


class PostgresPilotImportStore:
    """Driver-neutral pilot import store over an injected SQL executor."""

    def __init__(
        self,
        executor: Any,
        *,
        disposable: bool = False,
    ) -> None:
        self._executor = executor
        self.disposable = disposable

    def _run(self, sql: str, params: tuple[object, ...]) -> object:
        return self._executor(sql, params)

    def batch_status(self, batch_id: str) -> str | None:
        return _text(self._run(_BATCH_STATUS_SQL, (batch_id,)))

    def create_batch(
        self,
        *,
        plan: PilotImportPlan,
        approval: MappingApproval,
        tenant_id: str,
    ) -> bool:
        return _inserted(
            self._run(
                _BATCH_INSERT_SQL,
                (
                    plan.import_batch_id,
                    tenant_id,
                    plan.source_snapshot,
                    plan.adapter_version,
                    len(plan.source_files),
                    approval.reviewer,
                    approval.decided_at,
                    approval.review_artifact,
                ),
            )
        )

    def add_source_file(self, batch_id: str, item: LegacySourceFile) -> bool:
        return _inserted(
            self._run(
                _SOURCE_FILE_INSERT_SQL,
                (
                    batch_id,
                    item.relative_path,
                    item.content_sha256,
                    item.byte_count,
                    item.observed_at,
                ),
            )
        )

    def max_revision(self, target_type: str, target_id: str) -> int:
        value = _text(self._run(_MAX_REVISION_SQL, (target_type, target_id)))
        return int(value) if value is not None else 0

    def add_record(
        self,
        record: ImportRecord,
        *,
        batch_id: str,
        tenant_id: str,
        revision: int,
        reviewed_by: str,
    ) -> bool:
        return _inserted(
            self._run(
                _RECORD_INSERT_SQL,
                (
                    record.idempotency_key,
                    batch_id,
                    tenant_id,
                    record.target_type,
                    record.target_id,
                    record.source_reference_id,
                    record.mapping_rule,
                    record.mapping_status,
                    revision,
                    reviewed_by,
                ),
            )
        )

    def add_fact(self, batch_id: str, fact: AtomicFactProposal) -> bool:
        payload = json.dumps(
            _json_value(fact.value), sort_keys=True, separators=(",", ":")
        )
        return _inserted(
            self._run(
                _FACT_INSERT_SQL,
                (
                    fact.fact_assertion_id,
                    batch_id,
                    fact.predicate,
                    payload,
                    fact.value_type,
                    fact.source_reference_id,
                    fact.source_locator,
                    fact.review_status,
                    fact.tension_group_key,
                ),
            )
        )

    def add_tension(self, batch_id: str, tension: TensionProposal) -> bool:
        return _inserted(
            self._run(
                _TENSION_INSERT_SQL,
                (
                    tension.key,
                    batch_id,
                    list(tension.assertion_ids),
                    tension.status,
                    tension.review_required,
                ),
            )
        )

    def add_state(self, row: ImportStateRow) -> bool:
        return _inserted(
            self._run(
                _STATE_INSERT_SQL,
                (
                    row.idempotency_key,
                    row.batch_id,
                    row.target_type,
                    row.target_id,
                    row.state_kind,
                    row.state_value,
                ),
            )
        )

    def withdrawn_targets(self) -> frozenset[tuple[str, str]]:
        raw = _text(self._run(_WITHDRAWN_TARGETS_SQL, ()))
        if raw is None:
            return frozenset()
        payload = json.loads(raw)
        return frozenset((str(item[0]), str(item[1])) for item in payload)

    def withdraw_batch(self, batch_id: str, *, withdrawn_at: str) -> bool:
        return _inserted(
            self._run(
                _WITHDRAW_SQL,
                (withdrawn_at, batch_id, withdrawn_at, batch_id),
            )
        )

    def delete_batch_children(self, batch_id: str) -> Mapping[str, int]:
        deleted: dict[str, int] = {}
        for table in _CHILD_TABLES:
            sql = (
                f"WITH deleted AS (DELETE FROM sklegal_migrations.{table} "
                "WHERE batch_id = %s::uuid RETURNING 1) "
                "SELECT count(*) FROM deleted;"
            )
            value = _text(self._run(sql, (batch_id,)))
            name = table.removeprefix("pilot_import_")
            deleted[name] = int(value) if value is not None else 0
        return deleted

    def counts(self) -> Mapping[str, int]:
        raw = _text(self._run(_COUNTS_SQL, ()))
        if raw is None:
            return {}
        payload = json.loads(raw)
        return {str(key): int(value) for key, value in payload.items()}
