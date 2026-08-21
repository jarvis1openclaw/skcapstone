from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from evaluate_security_policy import effective_classification, evaluate, load_policy


class SecurityPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_policy()

    def base_request(self, operation: str = "read") -> dict[str, object]:
        return {
            "operation": operation,
            "input_kind": "tenant_user_controlled",
            "classification_labels": ["confidential"],
            "authn_valid": True,
            "capauth_valid": True,
            "capability_matches": True,
            "principal_tenant_id": "tenant-a",
            "resource_tenant_id": "tenant-a",
            "principal_matter_id": "matter-1",
            "resource_matter_id": "matter-1",
            "matter_membership": True,
            "conflict_status": "clear",
            "ethical_wall_allowed": True,
            "source_rights": "owner_authorized",
            "rights_allowed_purposes": [operation],
        }

    def assert_denied(self, request: dict[str, object], reason: str) -> None:
        decision = evaluate(request, self.policy)
        self.assertFalse(decision.allowed)
        self.assertEqual(reason, decision.reason)

    def test_classification_inherits_most_restrictive_label(self) -> None:
        result = effective_classification(
            ["internal", "privileged_work_product", "confidential"], self.policy
        )
        self.assertEqual("privileged_work_product", result)

    def test_missing_classification_defaults_to_confidential(self) -> None:
        request = self.base_request()
        request["classification_labels"] = []
        decision = evaluate(request, self.policy)
        self.assertTrue(decision.allowed)
        self.assertEqual("confidential", decision.effective_classification)

    def test_unknown_rights_quarantine_restricted_uses(self) -> None:
        for operation in (
            "corpus_promote",
            "model_train",
            "redistribute",
            "derived_publish",
        ):
            with self.subTest(operation=operation):
                request = self.base_request(operation)
                request["source_rights"] = "unknown"
                self.assert_denied(request, "source_rights_quarantine")

    def test_unverified_rights_quarantine_restricted_uses(self) -> None:
        request = self.base_request("corpus_promote")
        request["source_rights"] = "unverified"
        self.assert_denied(request, "source_rights_quarantine")

    def test_rights_must_authorize_exact_use(self) -> None:
        request = self.base_request("derived_publish")
        request["rights_allowed_purposes"] = ["read"]
        self.assert_denied(request, "source_rights_purpose_denied")

    def test_verified_rights_must_authorize_ordinary_source_read(self) -> None:
        request = self.base_request("read")
        request["rights_allowed_purposes"] = ["retrieve"]
        self.assert_denied(request, "source_rights_purpose_denied")

    def test_verified_rights_must_authorize_ordinary_source_retrieval(self) -> None:
        request = self.base_request("retrieve")
        request["rights_allowed_purposes"] = ["read"]
        self.assert_denied(request, "source_rights_purpose_denied")

    def test_quarantined_source_denies_ordinary_read(self) -> None:
        request = self.base_request("read")
        request["source_rights"] = "unknown"
        self.assert_denied(request, "source_rights_quarantine")

    def test_quarantined_source_denies_ordinary_retrieval(self) -> None:
        request = self.base_request("retrieve")
        request["source_rights"] = "unverified"
        self.assert_denied(request, "source_rights_quarantine")

    def test_public_route_cannot_bypass_source_quarantine(self) -> None:
        request = {
            "operation": "read",
            "input_kind": "attacker_controlled",
            "classification_labels": ["public"],
            "public_route": True,
            "source_governed": True,
            "source_rights": "unknown",
        }
        self.assert_denied(request, "source_rights_quarantine")

    def test_source_governed_public_read_requires_rights_record(self) -> None:
        request = {
            "operation": "read",
            "input_kind": "attacker_controlled",
            "classification_labels": ["public"],
            "public_route": True,
            "source_governed": True,
        }
        self.assert_denied(request, "source_rights_missing_or_unknown")

    def test_explicit_isolated_quarantine_review_is_allowed(self) -> None:
        request = self.base_request("retrieve")
        request.update(
            {
                "source_rights": "unknown",
                "rights_allowed_purposes": [],
                "quarantine_review": True,
                "purpose": "isolated_quarantine_review",
            }
        )
        decision = evaluate(request, self.policy)
        self.assertTrue(decision.allowed)
        self.assertIn("retain_quarantine", decision.obligations)

    def test_cross_tenant_is_denied(self) -> None:
        request = self.base_request()
        request["principal_tenant_id"] = "tenant-b"
        self.assert_denied(request, "cross_tenant_denied")

    def test_cross_matter_is_denied(self) -> None:
        request = self.base_request()
        request["principal_matter_id"] = "matter-2"
        self.assert_denied(request, "cross_matter_denied")

    def test_conflict_hold_is_denied(self) -> None:
        request = self.base_request()
        request["conflict_status"] = "hold"
        self.assert_denied(request, "conflict_denied_or_unresolved")

    def test_unknown_conflict_state_is_denied(self) -> None:
        request = self.base_request()
        request["conflict_status"] = "unknown"
        self.assert_denied(request, "conflict_denied_or_unresolved")

    def test_waived_conflict_requires_valid_waiver(self) -> None:
        request = self.base_request()
        request["conflict_status"] = "waived"
        self.assert_denied(request, "conflict_waiver_invalid")

    def test_valid_conflict_waiver_is_allowed(self) -> None:
        request = self.base_request()
        request.update({"conflict_status": "waived", "waiver_valid": True})
        self.assertTrue(evaluate(request, self.policy).allowed)

    def test_ethical_wall_exclusion_is_denied(self) -> None:
        request = self.base_request()
        request["ethical_wall_allowed"] = False
        self.assert_denied(request, "ethical_wall_denied")

    def test_protected_content_external_egress_is_denied(self) -> None:
        request = self.base_request("model_egress")
        request.update(
            {
                "classification_labels": ["privileged_work_product"],
                "model_route": "external",
                "tenant_egress_allowed": True,
                "purpose_approved": True,
                "context_minimized": True,
                "human_approval": True,
                "privileged_access_authorized": True,
                "provider_route_approved": True,
            }
        )
        self.assert_denied(request, "classification_egress_denied")

    def test_privileged_access_requires_independent_grant(self) -> None:
        request = self.base_request("read")
        request["classification_labels"] = ["privileged_work_product"]
        self.assert_denied(request, "privileged_access_required")

    def test_highly_restricted_access_requires_independent_grant(self) -> None:
        request = self.base_request("read")
        request["classification_labels"] = ["highly_restricted"]
        self.assert_denied(request, "highly_restricted_access_required")

    def test_privileged_read_can_pass_need_to_know_and_rights_gates(self) -> None:
        request = self.base_request("read")
        request.update(
            {
                "classification_labels": ["privileged_work_product"],
                "privileged_access_authorized": True,
            }
        )
        self.assertTrue(evaluate(request, self.policy).allowed)

    def test_confidential_egress_requires_human_approval(self) -> None:
        request = self.base_request("model_egress")
        request.update(
            {
                "model_route": "external",
                "tenant_egress_allowed": True,
                "provider_route_approved": True,
                "purpose_approved": True,
                "context_minimized": True,
                "human_approval": False,
            }
        )
        self.assert_denied(request, "human_egress_approval_required")

    def test_confidential_egress_can_pass_all_independent_gates(self) -> None:
        request = self.base_request("model_egress")
        request.update(
            {
                "model_route": "external",
                "tenant_egress_allowed": True,
                "provider_route_approved": True,
                "purpose_approved": True,
                "context_minimized": True,
                "human_approval": True,
            }
        )
        self.assertTrue(evaluate(request, self.policy).allowed)

    def test_external_egress_requires_approved_provider_route(self) -> None:
        request = self.base_request("model_egress")
        request.update(
            {
                "model_route": "external",
                "tenant_egress_allowed": True,
                "provider_route_approved": False,
                "purpose_approved": True,
                "context_minimized": True,
                "human_approval": True,
            }
        )
        self.assert_denied(request, "provider_route_not_approved")

    def test_document_or_model_cannot_expand_tool_authority(self) -> None:
        for input_kind in ("tenant_user_controlled", "model_produced", "corpus_derived"):
            with self.subTest(input_kind=input_kind):
                request = self.base_request("tool_invoke")
                request.update(
                    {
                        "input_kind": input_kind,
                        "tool_authority_source": "capauth",
                        "requested_authority_expansion": True,
                        "arguments_validated": True,
                    }
                )
                self.assert_denied(request, "untrusted_authority_expansion_denied")

    def test_connector_returned_tool_arguments_need_validation(self) -> None:
        request = self.base_request("tool_invoke")
        request.update(
            {
                "input_kind": "connector_returned",
                "tool_authority_source": "capauth",
                "requested_authority_expansion": False,
                "arguments_validated": False,
            }
        )
        self.assert_denied(request, "tool_arguments_invalid")

    def test_capauth_can_authorize_validated_low_risk_tool_call(self) -> None:
        request = self.base_request("tool_invoke")
        request.update(
            {
                "input_kind": "model_produced",
                "tool_authority_source": "capauth",
                "requested_authority_expansion": False,
                "arguments_validated": True,
            }
        )
        self.assertTrue(evaluate(request, self.policy).allowed)

    def test_production_connector_requires_approval(self) -> None:
        request = self.base_request("connector_dispatch")
        request.update(
            {
                "connector_mode": "production",
                "connector_approved": False,
                "human_approval": True,
                "exact_version_approved": True,
                "destination_verified": True,
                "idempotency_key": "dispatch-1",
            }
        )
        self.assert_denied(request, "production_connector_not_approved")

    def test_simulated_connector_is_allowed_after_safe_gates(self) -> None:
        request = self.base_request("connector_dispatch")
        request.update(
            {
                "connector_mode": "simulation",
                "exact_version_approved": True,
                "destination_verified": True,
                "idempotency_key": "simulation-1",
            }
        )
        self.assertTrue(evaluate(request, self.policy).allowed)

    def test_protected_logging_requires_redaction(self) -> None:
        request = self.base_request("log")
        request["redacted"] = False
        self.assert_denied(request, "logging_redaction_required")

    def test_redacted_protected_logging_is_allowed(self) -> None:
        request = self.base_request("log")
        request["redacted"] = True
        self.assertTrue(evaluate(request, self.policy).allowed)

    def test_raw_secrets_are_denied_across_every_operation(self) -> None:
        for operation in sorted(
            {
                "backup_restore",
                "connector_dispatch",
                "corpus_promote",
                "derived_publish",
                "log",
                "model_egress",
                "model_train",
                "read",
                "redistribute",
                "retention_delete",
                "retrieve",
                "tool_invoke",
                "workflow_mutation",
            }
        ):
            with self.subTest(operation=operation):
                request = self.base_request(operation)
                request["contains_secret"] = True
                self.assert_denied(request, "raw_secret_or_capability_denied")

    def test_raw_capabilities_and_credentials_are_always_denied(self) -> None:
        for field in ("contains_raw_capability", "contains_credential_material"):
            with self.subTest(field=field):
                request = self.base_request("read")
                request[field] = True
                self.assert_denied(request, "raw_secret_or_capability_denied")

    def test_required_credential_uses_secret_store_reference(self) -> None:
        request = self.base_request("connector_dispatch")
        request.update({"credential_required": True, "secret_store_reference_valid": False})
        self.assert_denied(request, "secret_store_reference_required")

    def test_secret_store_reference_is_not_raw_secret_material(self) -> None:
        request = self.base_request("read")
        request.update({"credential_required": True, "secret_store_reference_valid": True})
        self.assertTrue(evaluate(request, self.policy).allowed)

    def test_secrets_are_denied_even_when_marked_redacted(self) -> None:
        request = self.base_request("log")
        request.update({"redacted": True, "contains_secret": True})
        self.assert_denied(request, "raw_secret_or_capability_denied")

    def test_legal_hold_overrides_expired_retention(self) -> None:
        request = self.base_request("retention_delete")
        request.update(
            {
                "legal_hold": True,
                "retention_expired": True,
                "deletion_approved": True,
            }
        )
        self.assert_denied(request, "legal_hold_overrides_retention")

    def test_missing_legal_hold_state_denies_retention_delete(self) -> None:
        request = self.base_request("retention_delete")
        request.update({"retention_expired": True, "deletion_approved": True})
        self.assert_denied(request, "legal_hold_state_required")

    def test_expired_retention_can_delete_after_approval_without_hold(self) -> None:
        request = self.base_request("retention_delete")
        request.update(
            {
                "legal_hold": False,
                "retention_expired": True,
                "deletion_approved": True,
            }
        )
        self.assertTrue(evaluate(request, self.policy).allowed)

    def test_backup_restore_preserves_encryption_destination_and_holds(self) -> None:
        request = self.base_request("backup_restore")
        request.update(
            {
                "encrypted": True,
                "approved_destination": True,
                "hold_metadata_preserved": False,
            }
        )
        self.assert_denied(request, "backup_hold_metadata_required")

    def test_workflow_replay_cannot_repeat_mutation_without_receipt(self) -> None:
        request = self.base_request("workflow_mutation")
        request.update(
            {"workflow_replay": True, "idempotency_receipt_verified": False}
        )
        self.assert_denied(request, "workflow_replay_mutation_denied")

    def test_public_read_is_allowed_on_explicit_public_route(self) -> None:
        request = {
            "operation": "read",
            "input_kind": "attacker_controlled",
            "classification_labels": ["public"],
            "public_route": True,
        }
        self.assertTrue(evaluate(request, self.policy).allowed)

    def test_missing_policy_attributes_fail_closed(self) -> None:
        request = self.base_request()
        del request["capauth_valid"]
        self.assert_denied(request, "capability_required")

    def test_request_evaluation_does_not_mutate_input(self) -> None:
        request = self.base_request()
        original = copy.deepcopy(request)
        evaluate(request, self.policy)
        self.assertEqual(original, request)


if __name__ == "__main__":
    unittest.main()
