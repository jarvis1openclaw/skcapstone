"""Lossless planning for the Liberty Auto legacy matter pilot.

The importer deliberately produces proposals only.  It reads pinned records
from the HammerTime adapter, never constructs a HammerTime write path, and
does not advance any legal, approval, or execution state.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5


class PinnedRecord(Protocol):
    """Minimum read-only record surface required by the planner."""

    legacy_id: str
    relative_path: str
    frontmatter: Mapping[str, Any]
    body: str
    pin: Any
    registry_pin: Any


class PinnedPacket(Protocol):
    packet_version: int
    facts_pin: Any
    facts: Mapping[str, Any]
    review_pin: Any | None


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(value)


def _pin_path(pin: Any) -> str:
    return str(pin.relative_path)


def _pin_hash(pin: Any) -> str:
    return str(pin.content_sha256)


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _json_value(value: Any) -> Any:
    """Make a fact value JSON-safe without altering its source text form."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _json_pointer(parts: tuple[str, ...]) -> str:
    escaped = [part.replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(escaped)


def _walk_json(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk_json(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_json(child, path + (str(index),))
    else:
        yield _json_pointer(path), value


def _tension_class(predicate: str) -> str | None:
    normalized = predicate.lower().replace("-", "_")
    patterns = (
        ("party_or_trust_name", ("name", "trust")),
        ("timing_rule", ("response", "business_day", "calendar_day", "period")),
        ("date_or_deadline", ("date", "deadline", "due")),
        ("identity_assertion", ("identity", "person", "owner")),
        ("execution_state", ("execution", "executed", "completion")),
    )
    for group, needles in patterns:
        if any(needle in normalized for needle in needles):
            return group
    return None


@dataclass(frozen=True)
class LegacySourceFile:
    relative_path: str
    content_sha256: str
    observed_at: str
    byte_count: int | None = None


@dataclass(frozen=True)
class AtomicFactProposal:
    fact_assertion_id: str
    predicate: str
    value: Any
    value_type: str
    source_reference_id: str
    source_locator: str
    review_status: str = "source_asserted"
    tension_group_key: str | None = None


@dataclass(frozen=True)
class TensionProposal:
    key: str
    assertion_ids: tuple[str, ...]
    status: str = "unresolved"
    review_required: bool = True


@dataclass(frozen=True)
class VersionLineage:
    packet_version: int
    source_path: str
    source_sha256: str
    historical: bool
    current_review_baseline: bool


@dataclass(frozen=True)
class ImportRecord:
    target_type: str
    target_id: str
    source_reference_id: str
    mapping_rule: str
    mapping_status: str
    idempotency_key: str
    reviewed_by: str | None = None


@dataclass(frozen=True)
class PilotImportPlan:
    import_batch_id: str
    source_snapshot: str
    adapter_version: str
    source_files: tuple[LegacySourceFile, ...]
    records: tuple[ImportRecord, ...]
    facts: tuple[AtomicFactProposal, ...]
    tensions: tuple[TensionProposal, ...]
    version_lineage: tuple[VersionLineage, ...]
    review_required: bool
    write_operations: tuple[str, ...] = ()
    changed_sources: tuple[str, ...] = ()

    @property
    def hammer_time_mutation(self) -> bool:
        return bool(self.write_operations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "import_batch_id": self.import_batch_id,
            "source_snapshot": self.source_snapshot,
            "adapter_version": self.adapter_version,
            "source_files": [vars(item) for item in self.source_files],
            "records": [vars(item) for item in self.records],
            "facts": [
                {**vars(item), "value": _json_value(item.value)} for item in self.facts
            ],
            "tensions": [vars(item) for item in self.tensions],
            "version_lineage": [vars(item) for item in self.version_lineage],
            "review_required": self.review_required,
            "write_operations": list(self.write_operations),
            "changed_sources": list(self.changed_sources),
            "hammer_time_mutation": self.hammer_time_mutation,
        }

    def json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


class PilotImporter:
    """Build deterministic, idempotent proposals from pinned legacy records."""

    def __init__(self, *, adapter_version: str = "0.1.0") -> None:
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", adapter_version):
            raise ValueError("adapter_version must be semantic version text")
        self.adapter_version = adapter_version

    def build_plan(
        self,
        records: Iterable[PinnedRecord],
        *,
        source_snapshot: str,
        packet_references: Iterable[PinnedPacket] = (),
        metadata_files: Iterable[LegacySourceFile] = (),
        previous_source_hashes: Mapping[str, str] | None = None,
    ) -> PilotImportPlan:
        record_list = tuple(records)
        if not record_list:
            raise ValueError("pilot requires at least one pinned legacy record")
        if not source_snapshot.strip():
            raise ValueError("source_snapshot must be non-empty")

        source_files: dict[str, LegacySourceFile] = {}
        source_refs: dict[str, str] = {}
        for record in record_list:
            source_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"source:{record.relative_path}:{_pin_hash(record.pin)}",
                )
            )
            source_refs[record.relative_path] = source_id
            source_files[record.relative_path] = LegacySourceFile(
                relative_path=record.relative_path,
                content_sha256=_pin_hash(record.pin),
                observed_at=_utc(record.pin.observed_at),
            )
            registry_path = _pin_path(record.registry_pin)
            source_files.setdefault(
                registry_path,
                LegacySourceFile(
                    relative_path=registry_path,
                    content_sha256=_pin_hash(record.registry_pin),
                    observed_at=_utc(record.registry_pin.observed_at),
                ),
            )

        lineage: list[VersionLineage] = []
        for packet in sorted(packet_references, key=lambda item: item.packet_version):
            packet_path = _pin_path(packet.facts_pin)
            source_files[packet_path] = LegacySourceFile(
                relative_path=packet_path,
                content_sha256=_pin_hash(packet.facts_pin),
                observed_at=_utc(packet.facts_pin.observed_at),
            )
            lineage.append(
                VersionLineage(
                    packet_version=packet.packet_version,
                    source_path=packet_path,
                    source_sha256=_pin_hash(packet.facts_pin),
                    historical=False,
                    current_review_baseline=False,
                )
            )
            review_pin = packet.review_pin
            if review_pin is not None:
                source_files[_pin_path(review_pin)] = LegacySourceFile(
                    relative_path=_pin_path(review_pin),
                    content_sha256=_pin_hash(review_pin),
                    observed_at=_utc(review_pin.observed_at),
                )
        if lineage:
            baseline = max(item.packet_version for item in lineage)
            lineage = [
                VersionLineage(
                    packet_version=item.packet_version,
                    source_path=item.source_path,
                    source_sha256=item.source_sha256,
                    historical=item.packet_version != baseline,
                    current_review_baseline=item.packet_version == baseline,
                )
                for item in lineage
            ]
        for item in metadata_files:
            source_files[item.relative_path] = item

        facts: list[AtomicFactProposal] = []
        records_out: list[ImportRecord] = []
        fact_groups: dict[str, list[str]] = {}
        for record in record_list:
            source_id = source_refs[record.relative_path]
            target_type = (
                "matter" if record.legacy_id.startswith("PRB-") else "matter_event"
            )
            target_id = str(
                uuid5(NAMESPACE_URL, f"target:{record.legacy_id}:{source_snapshot}")
            )
            rule = (
                "problem.matter@1"
                if target_type == "matter"
                else "incident.transaction_review@1"
            )
            records_out.append(
                ImportRecord(
                    target_type=target_type,
                    target_id=target_id,
                    source_reference_id=source_id,
                    mapping_rule=rule,
                    mapping_status="proposed",
                    idempotency_key=self.idempotency_key(
                        record.relative_path, _pin_hash(record.pin), target_type, rule
                    ),
                )
            )
            for key, value in record.frontmatter.items():
                if isinstance(value, (dict, list)):
                    continue
                predicate = str(key)
                fact_id = str(
                    uuid5(NAMESPACE_URL, f"fact:{source_id}:{predicate}:{value}")
                )
                group_key = _tension_class(predicate)
                facts.append(
                    AtomicFactProposal(
                        fact_assertion_id=fact_id,
                        predicate=predicate,
                        value=value,
                        value_type=_value_type(value),
                        source_reference_id=source_id,
                        source_locator=f"frontmatter/{predicate}",
                        tension_group_key=group_key,
                    )
                )
                if group_key:
                    fact_groups.setdefault(group_key, []).append(fact_id)
            facts.append(
                AtomicFactProposal(
                    fact_assertion_id=str(
                        uuid5(NAMESPACE_URL, f"fact:{source_id}:legacy_id")
                    ),
                    predicate="legacy_id",
                    value=record.legacy_id,
                    value_type="string",
                    source_reference_id=source_id,
                    source_locator="legacy_id",
                )
            )

        tensions = tuple(
            TensionProposal(key=key, assertion_ids=tuple(ids))
            for key, ids in sorted(fact_groups.items())
            if len(ids) >= 2
        )
        batch_seed = "|".join(
            f"{item.relative_path}:{item.content_sha256}"
            for item in sorted(source_files.values(), key=lambda x: x.relative_path)
        )
        batch_id = str(uuid5(NAMESPACE_URL, f"batch:{source_snapshot}:{batch_seed}"))
        changed = tuple(
            sorted(
                path
                for path, item in source_files.items()
                if previous_source_hashes is not None
                and path in previous_source_hashes
                and previous_source_hashes[path] != item.content_sha256
            )
        )
        return PilotImportPlan(
            import_batch_id=batch_id,
            source_snapshot=source_snapshot,
            adapter_version=self.adapter_version,
            source_files=tuple(
                sorted(source_files.values(), key=lambda x: x.relative_path)
            ),
            records=tuple(records_out),
            facts=tuple(facts),
            tensions=tensions,
            version_lineage=tuple(lineage),
            review_required=True,
            changed_sources=changed,
        )

    @staticmethod
    def idempotency_key(
        legacy_path: str,
        content_sha256: str,
        target_type: str,
        mapping_rule: str,
        *,
        source_system: str = "hammertime",
        adapter_version: str = "0.1.0",
    ) -> str:
        value = "".join(
            (
                source_system,
                legacy_path,
                content_sha256,
                adapter_version,
                target_type,
                mapping_rule,
            )
        )
        return _sha256(value.encode("utf-8"))
