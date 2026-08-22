"""SKL-S5-04D backup manifest, restore authorization, and recovery tests."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

from sklegal_persistence import (
    RPO_TARGET_SECONDS,
    RTO_TARGET_SECONDS,
    BackupContractError,
    BackupManifest,
    RecoveryMeasurement,
    RecoveryTargets,
    RestoreRequest,
    authorize_restore,
    collect_secret_references,
    evaluate_recovery,
    hold_wall_digest_rows,
    parse_manifest,
    verify_secret_recovery,
)

TENANT_ALPHA = UUID("10000000-0000-4000-8000-0000000005d1")
TENANT_BETA = UUID("10000000-0000-4000-8000-0000000005d2")
OTHER_TENANT = UUID("10000000-0000-4000-8000-0000000005e1")


def _manifest(
    *,
    encrypted: bool = False,
    tenant_ids: frozenset[UUID] = frozenset({TENANT_ALPHA}),
) -> BackupManifest:
    return BackupManifest(
        backup_id="skl-backup-s504d-logical-001",
        kind="logical_full",
        source_identity="disposable-source-1",
        database="sklegal",
        started_at=datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC),
        completed_at=datetime(2026, 8, 22, 12, 0, 41, tzinfo=UTC),
        content_sha256="a" * 64,
        content_bytes=615913,
        tenant_ids=tenant_ids,
        hold_wall_digest="b" * 64,
        hold_wall_rows=2,
        encrypted=encrypted,
        key_custody_reference=("vault:sklegal/backup/key-s504d" if encrypted else None),
        retention_class="qualification-scratch",
    )


def _request(
    *,
    backup_id: str = "skl-backup-s504d-logical-001",
    tenant_ids: frozenset[UUID] = frozenset({TENANT_ALPHA}),
    hold_acknowledged: bool = True,
    approval: str = "approval-9549c3be-restore-001",
    key_reference: str | None = None,
    operator: str = "skl-s5-04d",
    target: str = "disposable-restore-1",
) -> RestoreRequest:
    return RestoreRequest(
        backup_id=backup_id,
        target_identity=target,
        operator=operator,
        approval_reference=approval,
        requested_tenant_ids=tenant_ids,
        hold_state_acknowledged=hold_acknowledged,
        decryption_key_reference=key_reference,
    )


class BackupManifestTests(unittest.TestCase):
    def test_manifest_round_trips_through_canonical_json(self) -> None:
        manifest = _manifest()
        record = json.loads(manifest.canonical_bytes().decode("utf-8"))
        rebuilt = parse_manifest(record)
        self.assertEqual(manifest, rebuilt)
        self.assertEqual(manifest.manifest_sha256(), rebuilt.manifest_sha256())

    def test_canonical_bytes_are_scope_order_independent(self) -> None:
        one = _manifest(tenant_ids=frozenset({TENANT_ALPHA, TENANT_BETA}))
        two = _manifest(tenant_ids=frozenset({TENANT_BETA, TENANT_ALPHA}))
        self.assertEqual(one.canonical_bytes(), two.canonical_bytes())

    def test_manifest_rejects_naive_and_non_utc_timestamps(self) -> None:
        with self.assertRaises(BackupContractError):
            _manifest().__class__(
                **{
                    **_manifest().__dict__,
                    "completed_at": datetime(2026, 8, 22, 12, 0, 41),
                }
            )
        with self.assertRaises(BackupContractError):
            _manifest().__class__(
                **{
                    **_manifest().__dict__,
                    "completed_at": datetime(
                        2026,
                        8,
                        22,
                        7,
                        0,
                        41,
                        tzinfo=timezone(timedelta(hours=-5)),
                    ),
                }
            )

    def test_manifest_rejects_completion_before_start(self) -> None:
        manifest = _manifest()
        with self.assertRaises(BackupContractError):
            BackupManifest(
                **{
                    **manifest.__dict__,
                    "started_at": manifest.completed_at + timedelta(seconds=1),
                }
            )

    def test_encrypted_manifest_requires_key_custody_reference(self) -> None:
        manifest = _manifest(encrypted=True)
        with self.assertRaises(BackupContractError):
            BackupManifest(
                **{
                    **manifest.__dict__,
                    "key_custody_reference": None,
                }
            )

    def test_manifest_requires_scope_and_digest_shapes(self) -> None:
        base = _manifest()
        for field, value in (
            ("tenant_ids", frozenset()),
            ("content_sha256", "XYZ"),
            ("hold_wall_digest", "b" * 63),
            ("content_bytes", 0),
            ("backup_id", "not-a-valid-id"),
            ("kind", "snapshot"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(BackupContractError):
                    BackupManifest(**{**base.__dict__, field: value})

    def test_parse_manifest_rejects_unknown_fields(self) -> None:
        record = json.loads(_manifest().canonical_bytes().decode("utf-8"))
        record["extra_field"] = "unrecognized"
        with self.assertRaises(BackupContractError):
            parse_manifest(record)


class RestoreAuthorizationTests(unittest.TestCase):
    def test_matching_scope_with_approval_is_allowed(self) -> None:
        manifest = _manifest()
        decision = authorize_restore(
            json.loads(manifest.canonical_bytes().decode("utf-8")),
            recorded_manifest_sha256=manifest.manifest_sha256(),
            request=_request(),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(manifest, decision.manifest)
        self.assertEqual((), decision.reasons)

    def test_partial_scope_within_backup_scope_is_allowed(self) -> None:
        manifest = _manifest(tenant_ids=frozenset({TENANT_ALPHA, TENANT_BETA}))
        decision = authorize_restore(
            json.loads(manifest.canonical_bytes().decode("utf-8")),
            recorded_manifest_sha256=manifest.manifest_sha256(),
            request=_request(tenant_ids=frozenset({TENANT_BETA})),
        )
        self.assertTrue(decision.allowed)

    def test_cross_tenant_restore_is_denied(self) -> None:
        manifest = _manifest()
        decision = authorize_restore(
            json.loads(manifest.canonical_bytes().decode("utf-8")),
            recorded_manifest_sha256=manifest.manifest_sha256(),
            request=_request(tenant_ids=frozenset({OTHER_TENANT})),
        )
        self.assertFalse(decision.allowed)
        self.assertIn("restore_scope_crosses_backup_scope", decision.reasons)
        self.assertIn(str(OTHER_TENANT), decision.reasons)
        self.assertIsNone(decision.manifest)

    def test_tampered_manifest_is_denied(self) -> None:
        manifest = _manifest()
        record = json.loads(manifest.canonical_bytes().decode("utf-8"))
        record["content_sha256"] = "c" * 64
        decision = authorize_restore(
            record,
            recorded_manifest_sha256=manifest.manifest_sha256(),
            request=_request(),
        )
        self.assertFalse(decision.allowed)
        self.assertIn("manifest_digest_mismatch", decision.reasons)

    def test_unreadable_manifest_fails_closed(self) -> None:
        for record in (None, {"backup_id": 7}):
            with self.subTest(record=record):
                decision = authorize_restore(
                    record,
                    recorded_manifest_sha256="d" * 64,
                    request=_request(),
                )
                self.assertFalse(decision.allowed)
                self.assertIn(
                    "manifest_unavailable" if record is None else "manifest_invalid",
                    decision.reasons,
                )

    def test_missing_approval_operator_target_or_scope_is_denied(self) -> None:
        manifest = _manifest()
        record = json.loads(manifest.canonical_bytes().decode("utf-8"))
        cases = (
            ("restore_approval_missing", _request(approval="")),
            ("restore_operator_unattributed", _request(operator="")),
            ("restore_target_unidentified", _request(target="")),
            (
                "restore_scope_empty",
                _request(tenant_ids=frozenset()),
            ),
        )
        for expected_reason, request in cases:
            with self.subTest(reason=expected_reason):
                decision = authorize_restore(
                    record,
                    recorded_manifest_sha256=manifest.manifest_sha256(),
                    request=request,
                )
                self.assertFalse(decision.allowed)
                self.assertIn(expected_reason, decision.reasons)

    def test_unacknowledged_hold_state_is_denied(self) -> None:
        manifest = _manifest()
        decision = authorize_restore(
            json.loads(manifest.canonical_bytes().decode("utf-8")),
            recorded_manifest_sha256=manifest.manifest_sha256(),
            request=_request(hold_acknowledged=False),
        )
        self.assertFalse(decision.allowed)
        self.assertIn("hold_state_not_acknowledged", decision.reasons)

    def test_encrypted_backup_requires_matching_key_custody(self) -> None:
        manifest = _manifest(encrypted=True)
        record = json.loads(manifest.canonical_bytes().decode("utf-8"))
        missing = authorize_restore(
            record,
            recorded_manifest_sha256=manifest.manifest_sha256(),
            request=_request(key_reference=None),
        )
        wrong = authorize_restore(
            record,
            recorded_manifest_sha256=manifest.manifest_sha256(),
            request=_request(key_reference="vault:sklegal/backup/other"),
        )
        matching = authorize_restore(
            record,
            recorded_manifest_sha256=manifest.manifest_sha256(),
            request=_request(key_reference="vault:sklegal/backup/key-s504d"),
        )
        self.assertFalse(missing.allowed)
        self.assertIn("key_custody_unavailable", missing.reasons)
        self.assertFalse(wrong.allowed)
        self.assertIn("key_custody_unavailable", wrong.reasons)
        self.assertTrue(matching.allowed)

    def test_backup_id_mismatch_is_denied(self) -> None:
        manifest = _manifest()
        decision = authorize_restore(
            json.loads(manifest.canonical_bytes().decode("utf-8")),
            recorded_manifest_sha256=manifest.manifest_sha256(),
            request=_request(backup_id="skl-backup-s504d-logical-999"),
        )
        self.assertFalse(decision.allowed)
        self.assertIn("backup_id_mismatch", decision.reasons)

    def test_unrecorded_manifest_digest_fails_closed(self) -> None:
        manifest = _manifest()
        decision = authorize_restore(
            json.loads(manifest.canonical_bytes().decode("utf-8")),
            recorded_manifest_sha256="not-a-digest",
            request=_request(),
        )
        self.assertFalse(decision.allowed)
        self.assertIn("manifest_digest_unrecorded", decision.reasons)


class RecoveryMeasurementTests(unittest.TestCase):
    def test_rpo_and_rto_are_computed_from_the_contract_points(self) -> None:
        failure = datetime(2026, 8, 22, 16, 26, 49, tzinfo=UTC)
        measurement = RecoveryMeasurement(
            scenario="logical_full_restore",
            failure_observed_at=failure,
            last_durable_point=failure - timedelta(seconds=15),
            service_healthy_at=failure + timedelta(seconds=26),
        )
        self.assertEqual(15, measurement.rpo_seconds)
        self.assertEqual(26, measurement.rto_seconds)
        evaluation = evaluate_recovery(measurement)
        self.assertEqual("logical_full_restore", evaluation.scenario)
        self.assertTrue(evaluation.rpo_within_target)
        self.assertTrue(evaluation.rto_within_target)
        self.assertTrue(evaluation.passed)

    def test_targets_match_the_smoke_contract_numbers(self) -> None:
        self.assertEqual(300, RPO_TARGET_SECONDS)
        self.assertEqual(900, RTO_TARGET_SECONDS)
        targets = RecoveryTargets()
        self.assertEqual(300, targets.rpo_seconds)
        self.assertEqual(900, targets.rto_seconds)

    def test_breaching_either_target_fails_the_evaluation(self) -> None:
        failure = datetime(2026, 8, 22, 16, 26, 49, tzinfo=UTC)
        rpo_breach = evaluate_recovery(
            RecoveryMeasurement(
                scenario="rpo_breach",
                failure_observed_at=failure,
                last_durable_point=failure - timedelta(seconds=301),
                service_healthy_at=failure + timedelta(seconds=60),
            )
        )
        rto_breach = evaluate_recovery(
            RecoveryMeasurement(
                scenario="rto_breach",
                failure_observed_at=failure,
                last_durable_point=failure,
                service_healthy_at=failure + timedelta(seconds=901),
            )
        )
        self.assertFalse(rpo_breach.passed)
        self.assertFalse(rpo_breach.rpo_within_target)
        self.assertTrue(rpo_breach.rto_within_target)
        self.assertFalse(rto_breach.passed)
        self.assertTrue(rto_breach.rpo_within_target)
        self.assertFalse(rto_breach.rto_within_target)

    def test_exact_target_boundary_passes(self) -> None:
        failure = datetime(2026, 8, 22, 16, 26, 49, tzinfo=UTC)
        boundary = evaluate_recovery(
            RecoveryMeasurement(
                scenario="boundary",
                failure_observed_at=failure,
                last_durable_point=failure - timedelta(seconds=300),
                service_healthy_at=failure + timedelta(seconds=900),
            )
        )
        self.assertTrue(boundary.passed)

    def test_recovery_point_after_failure_clamps_to_zero(self) -> None:
        failure = datetime(2026, 8, 22, 16, 26, 49, tzinfo=UTC)
        measurement = RecoveryMeasurement(
            scenario="clock_skew",
            failure_observed_at=failure,
            last_durable_point=failure + timedelta(seconds=2),
            service_healthy_at=failure + timedelta(seconds=10),
        )
        self.assertEqual(0, measurement.rpo_seconds)

    def test_measurement_requires_utc_and_ordered_points(self) -> None:
        failure = datetime(2026, 8, 22, 16, 26, 49, tzinfo=UTC)
        with self.assertRaises(BackupContractError):
            RecoveryMeasurement(
                scenario="naive",
                failure_observed_at=datetime(2026, 8, 22, 16, 26, 49),
                last_durable_point=failure,
                service_healthy_at=failure,
            )
        with self.assertRaises(BackupContractError):
            RecoveryMeasurement(
                scenario="reordered",
                failure_observed_at=failure,
                last_durable_point=failure,
                service_healthy_at=failure - timedelta(seconds=1),
            )
        with self.assertRaises(BackupContractError):
            RecoveryMeasurement(
                scenario="",
                failure_observed_at=failure,
                last_durable_point=failure,
                service_healthy_at=failure,
            )


class HoldWallDigestTests(unittest.TestCase):
    def test_digest_is_row_order_independent_and_counts_rows(self) -> None:
        rows = (
            ("ethical_wall", str(TENANT_ALPHA), "wall-one"),
            ("conflict_hold", str(TENANT_ALPHA), "hold-one"),
        )
        reversed_rows = tuple(reversed(rows))
        digest, count = hold_wall_digest_rows(rows)
        other_digest, other_count = hold_wall_digest_rows(reversed_rows)
        self.assertEqual(digest, other_digest)
        self.assertEqual(2, count)
        self.assertEqual(2, other_count)

    def test_empty_scope_is_the_explicit_negative_hold_state(self) -> None:
        digest, count = hold_wall_digest_rows(())
        self.assertEqual(0, count)
        empty_again, _ = hold_wall_digest_rows(())
        self.assertEqual(digest, empty_again)
        changed, _ = hold_wall_digest_rows(
            ("conflict_hold", "t", "h"),
        )
        self.assertNotEqual(digest, changed)

    def test_scope_change_changes_the_digest(self) -> None:
        base, _ = hold_wall_digest_rows((("ethical_wall", str(TENANT_ALPHA), "w"),))
        scoped, _ = hold_wall_digest_rows((("ethical_wall", str(TENANT_BETA), "w"),))
        self.assertNotEqual(base, scoped)


class SecretRecoveryTests(unittest.TestCase):
    ROUTE_REGISTRY = {
        "schema_version": "model-gateway-route-registry/v1",
        "routes": {
            "openai": {
                "provider": "openai",
                "secret_reference": "vault:sklegal/openai/platform-api-key",
                "nested": [{"secret_reference": "vault:sklegal/internal/marker"}],
            },
            "qwen-local": {"provider": "local"},
        },
    }

    def test_collect_secret_references_walks_the_config_tree(self) -> None:
        references = collect_secret_references(self.ROUTE_REGISTRY)
        self.assertEqual(
            (
                "vault:sklegal/openai/platform-api-key",
                "vault:sklegal/internal/marker",
            ),
            references,
        )
        self.assertEqual((), collect_secret_references({"routes": {}}))

    def test_recovery_verifies_only_when_every_reference_resolves(self) -> None:
        references = collect_secret_references(self.ROUTE_REGISTRY)
        resolved = verify_secret_recovery(references, lambda reference: True)
        self.assertTrue(resolved.verified)
        self.assertEqual(references, resolved.references)
        self.assertEqual((), resolved.unresolved)

        partially = verify_secret_recovery(
            references,
            lambda reference: reference != "vault:sklegal/internal/marker",
        )
        self.assertFalse(partially.verified)
        self.assertEqual(("vault:sklegal/internal/marker",), partially.unresolved)

    def test_empty_scope_and_malformed_references_fail_closed(self) -> None:
        empty = verify_secret_recovery((), lambda reference: True)
        self.assertFalse(empty.verified)
        self.assertEqual(("<no-references-in-scope>",), empty.invalid)

        malformed = verify_secret_recovery(("raw-key-value",), lambda reference: True)
        self.assertFalse(malformed.verified)
        self.assertEqual(("raw-key-value",), malformed.invalid)
        self.assertEqual((), malformed.unresolved)


if __name__ == "__main__":
    unittest.main()
