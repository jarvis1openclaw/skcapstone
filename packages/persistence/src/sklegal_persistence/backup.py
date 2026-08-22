"""Backup manifest, restore authorization, and recovery-target contract.

This module owns the SKL-S5-04D qualification surface as pure decision
logic: durable backup manifests with content digests, the operator restore
authorization path demanded by the threat model boundary B11 (backup copied
to an unapproved target, operator restores across tenants, missing hold
state, lost key custody), numeric RPO/RTO evaluation against the targets
recorded in docs/development/RESILIENCE-SMOKE.md and proposed for the
capacity baseline by docs/approval/AMENDMENT-SKL-S3-07.md, and fail-closed
secret-reference recovery verification.

Everything here is deterministic and free of I/O. Secret values never enter
this module: the recovery verifier accepts a resolver that answers whether a
reference resolves, never what it resolves to.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
BACKUP_ID = re.compile(r"^skl-backup-[0-9a-z-]{1,80}$")
SECRET_REFERENCE = re.compile(r"^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9/._:-]{0,200}$")

BackupKind = Literal["logical_full", "pitr_base"]

#: Recovery targets from docs/development/RESILIENCE-SMOKE.md. The RPO applies
#: to the canonical SKLegal PostgreSQL database; Temporal history must simply
#: not be lost. These numbers stay proposed for the capacity baseline until
#: the human owner records the decision in AMENDMENT-SKL-S3-07.
RPO_TARGET_SECONDS = 300
RTO_TARGET_SECONDS = 900


class BackupContractError(ValueError):
    """Raised when backup or restore input is structurally invalid."""


def _require_utc(name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise BackupContractError(f"{name} requires a UTC offset-zero datetime")
    return value


@dataclass(frozen=True)
class BackupManifest:
    """Durable snapshot descriptor stored beside the backup it describes.

    ``content_sha256`` digests the backup artifact bytes (logical dump file,
    or the deterministic archive of a base-backup directory set). ``tenant_ids``
    is the tenant scope observed at backup time and bounds every restore of
    this backup. ``hold_wall_digest`` digests the legal-hold and ethical-wall
    rows in scope so a restore can prove barriers survived recovery.
    """

    backup_id: str
    kind: BackupKind
    source_identity: str
    database: str
    started_at: datetime
    completed_at: datetime
    content_sha256: str
    content_bytes: int
    tenant_ids: frozenset[UUID]
    hold_wall_digest: str
    hold_wall_rows: int
    encrypted: bool
    key_custody_reference: str | None
    retention_class: str

    def __post_init__(self) -> None:
        if not BACKUP_ID.match(self.backup_id):
            raise BackupContractError("backup_id must match skl-backup-<slug>")
        if self.kind not in ("logical_full", "pitr_base"):
            raise BackupContractError("kind must be logical_full or pitr_base")
        _require_utc("started_at", self.started_at)
        _require_utc("completed_at", self.completed_at)
        if self.completed_at < self.started_at:
            raise BackupContractError("completed_at precedes started_at")
        if not HEX_DIGEST.match(self.content_sha256):
            raise BackupContractError("content_sha256 must be a lowercase sha256")
        if not HEX_DIGEST.match(self.hold_wall_digest):
            raise BackupContractError("hold_wall_digest must be a lowercase sha256")
        if self.content_bytes <= 0:
            raise BackupContractError("content_bytes must be positive")
        if self.hold_wall_rows < 0:
            raise BackupContractError("hold_wall_rows must not be negative")
        if not self.tenant_ids:
            raise BackupContractError("tenant_ids must not be empty")
        if not self.source_identity or not self.database:
            raise BackupContractError("source_identity and database are required")
        if self.encrypted and not self.key_custody_reference:
            raise BackupContractError(
                "encrypted backups require a key_custody_reference"
            )
        if not self.retention_class:
            raise BackupContractError("retention_class is required")

    def canonical_bytes(self) -> bytes:
        """Render the manifest as canonical JSON bytes for digesting.

        Tenant scope is sorted so the bytes are identical for equal scopes,
        and timestamps render with fixed microseconds like the audit chain.
        """

        payload: dict[str, Any] = {
            "backup_id": self.backup_id,
            "kind": self.kind,
            "source_identity": self.source_identity,
            "database": self.database,
            "started_at": _canonical_timestamp(self.started_at),
            "completed_at": _canonical_timestamp(self.completed_at),
            "content_sha256": self.content_sha256,
            "content_bytes": self.content_bytes,
            "tenant_ids": sorted(str(value) for value in self.tenant_ids),
            "hold_wall_digest": self.hold_wall_digest,
            "hold_wall_rows": self.hold_wall_rows,
            "encrypted": self.encrypted,
            "key_custody_reference": self.key_custody_reference,
            "retention_class": self.retention_class,
        }
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")

    def manifest_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def _canonical_timestamp(value: datetime) -> str:
    return (
        f"{value.year:04d}-{value.month:02d}-{value.day:02d}T"
        f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}."
        f"{value.microsecond:06d}Z"
    )


def parse_manifest(record: Mapping[str, Any]) -> BackupManifest:
    """Rebuild one manifest from decoded JSON, failing closed on drift."""

    unknown = set(record) - {field for field in BackupManifest.__dataclass_fields__}
    if unknown:
        raise BackupContractError(
            f"manifest record carries unknown fields: {sorted(unknown)}"
        )
    try:
        return BackupManifest(
            backup_id=record["backup_id"],
            kind=record["kind"],
            source_identity=record["source_identity"],
            database=record["database"],
            started_at=_parse_timestamp(record["started_at"]),
            completed_at=_parse_timestamp(record["completed_at"]),
            content_sha256=record["content_sha256"],
            content_bytes=record["content_bytes"],
            tenant_ids=frozenset(UUID(item) for item in record["tenant_ids"]),
            hold_wall_digest=record["hold_wall_digest"],
            hold_wall_rows=record["hold_wall_rows"],
            encrypted=record["encrypted"],
            key_custody_reference=record.get("key_custody_reference"),
            retention_class=record["retention_class"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise BackupContractError(f"manifest record is invalid: {error}") from None


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise BackupContractError("manifest timestamps must be strings")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _require_utc("manifest timestamp", parsed)


@dataclass(frozen=True)
class RestoreRequest:
    """One operator request to restore a backup into a scratch instance."""

    backup_id: str
    target_identity: str
    operator: str
    approval_reference: str
    requested_tenant_ids: frozenset[UUID]
    hold_state_acknowledged: bool
    decryption_key_reference: str | None = None


@dataclass(frozen=True)
class RestoreAuthorization:
    """Fail-closed decision over one restore request.

    ``reasons`` is empty exactly when ``decision`` is allow. Any missing
    input, tampered manifest, cross-tenant scope, unacknowledged hold state,
    or unavailable key custody denies the restore.
    """

    decision: Literal["allow", "deny"]
    reasons: tuple[str, ...]
    manifest: BackupManifest | None

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


def authorize_restore(
    manifest_record: Mapping[str, Any] | None,
    *,
    recorded_manifest_sha256: str,
    request: RestoreRequest,
) -> RestoreAuthorization:
    """Authorize one restore against the recorded manifest digest.

    ``recorded_manifest_sha256`` is the digest captured in the backup
    catalog at backup time, supplied independently of the manifest record
    that travels with the backup bytes. A mismatch denies the restore as a
    tampered or unreadable manifest. The function never raises for
    authorization-relevant input: every failure mode is a denial reason.
    """

    def deny(*reasons: str) -> RestoreAuthorization:
        return RestoreAuthorization(
            decision="deny",
            reasons=reasons,
            manifest=None,
        )

    if not request.approval_reference:
        return deny("restore_approval_missing")
    if not request.operator:
        return deny("restore_operator_unattributed")
    if not request.target_identity:
        return deny("restore_target_unidentified")
    if manifest_record is None:
        return deny("manifest_unavailable")
    try:
        manifest = parse_manifest(manifest_record)
    except BackupContractError as error:
        return deny("manifest_invalid", str(error))
    if not HEX_DIGEST.match(recorded_manifest_sha256):
        return deny("manifest_digest_unrecorded")
    if manifest.manifest_sha256() != recorded_manifest_sha256:
        return deny("manifest_digest_mismatch")
    if request.backup_id != manifest.backup_id:
        return deny("backup_id_mismatch")
    crossed = sorted(
        str(value) for value in request.requested_tenant_ids - manifest.tenant_ids
    )
    if crossed:
        return deny("restore_scope_crosses_backup_scope", ",".join(crossed))
    if not request.requested_tenant_ids:
        return deny("restore_scope_empty")
    if not request.hold_state_acknowledged:
        return deny("hold_state_not_acknowledged")
    if manifest.encrypted and (
        request.decryption_key_reference is None
        or request.decryption_key_reference != manifest.key_custody_reference
    ):
        return deny("key_custody_unavailable")
    return RestoreAuthorization(
        decision="allow",
        reasons=(),
        manifest=manifest,
    )


@dataclass(frozen=True)
class RecoveryTargets:
    """Numeric recovery targets under evaluation."""

    rpo_seconds: int = RPO_TARGET_SECONDS
    rto_seconds: int = RTO_TARGET_SECONDS


@dataclass(frozen=True)
class RecoveryMeasurement:
    """One measured failure and recovery.

    ``last_durable_point`` is the latest durable backup timestamp before the
    failure for a logical restore, or the point-in-time recovery target for
    a PITR restore. ``service_healthy_at`` is the first probe the restored
    service answered. All timestamps are UTC.
    """

    scenario: str
    failure_observed_at: datetime
    last_durable_point: datetime
    service_healthy_at: datetime

    def __post_init__(self) -> None:
        _require_utc("failure_observed_at", self.failure_observed_at)
        _require_utc("last_durable_point", self.last_durable_point)
        _require_utc("service_healthy_at", self.service_healthy_at)
        if self.service_healthy_at < self.failure_observed_at:
            raise BackupContractError("service_healthy_at precedes failure_observed_at")
        if not self.scenario:
            raise BackupContractError("scenario is required")

    @property
    def rpo_seconds(self) -> int:
        lost = self.failure_observed_at - self.last_durable_point
        return max(0, round(lost.total_seconds()))

    @property
    def rto_seconds(self) -> int:
        return round(
            (self.service_healthy_at - self.failure_observed_at).total_seconds()
        )


@dataclass(frozen=True)
class RecoveryEvaluation:
    """Pass or fail of one measurement against the numeric targets."""

    scenario: str
    rpo_seconds: int
    rto_seconds: int
    rpo_within_target: bool
    rto_within_target: bool

    @property
    def passed(self) -> bool:
        return self.rpo_within_target and self.rto_within_target


def evaluate_recovery(
    measurement: RecoveryMeasurement,
    targets: RecoveryTargets | None = None,
) -> RecoveryEvaluation:
    """Evaluate one measurement against the recovery targets."""

    bounds = targets or RecoveryTargets()
    return RecoveryEvaluation(
        scenario=measurement.scenario,
        rpo_seconds=measurement.rpo_seconds,
        rto_seconds=measurement.rto_seconds,
        rpo_within_target=measurement.rpo_seconds <= bounds.rpo_seconds,
        rto_within_target=measurement.rto_seconds <= bounds.rto_seconds,
    )


def hold_wall_digest_rows(
    rows: tuple[tuple[str, ...], ...],
) -> tuple[str, int]:
    """Digest the hold and ethical-wall rows of one backup scope.

    Rows are joined in sorted order so the digest is stable across dump and
    restore. The row count is returned alongside so the manifest can record
    it and a restore can prove no wall rows silently vanished. An empty
    scope digests to a fixed constant and a count of zero, which is the
    explicit negative hold state the threat model requires.
    """

    canonical = "\n".join("\x1f".join(row) for row in sorted(rows)).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest(), len(rows)


@dataclass(frozen=True)
class SecretRecoveryReport:
    """Fail-closed result of verifying secret references after recovery."""

    references: tuple[str, ...]
    unresolved: tuple[str, ...]
    invalid: tuple[str, ...]

    @property
    def verified(self) -> bool:
        return bool(self.references) and not self.unresolved and not self.invalid


def collect_secret_references(config: Mapping[str, Any]) -> tuple[str, ...]:
    """Collect every secret_reference value from a decoded config tree.

    The route registry stores secret references under a
    ``secret_reference`` key. Values are references (scheme-prefixed
    pointers into a secret store), never secret material. The walk is
    recursive and deduplicates while preserving first-seen order.
    """

    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                if key == "secret_reference" and isinstance(value, str):
                    if value not in found:
                        found.append(value)
                else:
                    walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(config)
    return tuple(found)


def verify_secret_recovery(
    references: tuple[str, ...],
    resolver: Callable[[str], bool],
) -> SecretRecoveryReport:
    """Verify that every secret reference resolves after recovery.

    The resolver answers only whether a reference resolves in the recovered
    secret store; it must never return secret material. An empty reference
    set is a failed verification: nothing was proven recoverable, and the
    procedure fails closed rather than passing vacuously.
    """

    if not references:
        return SecretRecoveryReport(
            references=(),
            unresolved=(),
            invalid=("<no-references-in-scope>",),
        )
    unresolved: list[str] = []
    invalid: list[str] = []
    for reference in references:
        if not SECRET_REFERENCE.match(reference):
            invalid.append(reference)
            continue
        if not resolver(reference):
            unresolved.append(reference)
    return SecretRecoveryReport(
        references=references,
        unresolved=tuple(unresolved),
        invalid=tuple(invalid),
    )
