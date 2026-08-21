#!/usr/bin/env python3
"""Deterministic reference evaluator for SKLegal Sprint 0 security policy.

This is a narrow executable specification. Production authorization remains a
later CapAuth and policy-gateway task.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = REPO_ROOT / "config" / "security" / "policy.json"

OPERATIONS = {
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

INPUT_KINDS = {
    "attacker_controlled",
    "tenant_user_controlled",
    "operator_controlled",
    "developer_controlled",
    "model_produced",
    "connector_returned",
    "corpus_derived",
}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    effective_classification: str
    obligations: tuple[str, ...] = ()


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        policy = json.load(stream)
    if policy.get("schema_version") != 1:
        raise ValueError("unsupported policy schema")
    return policy


def effective_classification(
    labels: list[str] | tuple[str, ...], policy: Mapping[str, Any]
) -> str:
    order = list(policy["classification_order"])
    if not labels:
        return str(policy["default_classification"])
    unknown = sorted(set(labels) - set(order))
    if unknown:
        raise ValueError(f"unknown classification: {', '.join(unknown)}")
    return max(labels, key=order.index)


def _deny(reason: str, classification: str) -> Decision:
    return Decision(False, reason, classification)


def _is_public_read(request: Mapping[str, Any], classification: str) -> bool:
    return (
        request.get("operation") == "read"
        and classification == "public"
        and request.get("public_route") is True
    )


def evaluate(
    request: Mapping[str, Any], policy: Mapping[str, Any] | None = None
) -> Decision:
    """Evaluate a typed policy request and return the first fail-closed result."""

    active_policy = dict(policy or load_policy())
    try:
        classification = effective_classification(
            list(request.get("classification_labels", [])), active_policy
        )
    except (TypeError, ValueError) as exc:
        return _deny(f"classification_invalid:{exc}", "confidential")

    operation = request.get("operation")
    if operation not in OPERATIONS:
        return _deny("operation_missing_or_unknown", classification)

    input_kind = request.get("input_kind")
    if input_kind not in INPUT_KINDS:
        return _deny("input_kind_missing_or_unknown", classification)

    public_read = _is_public_read(request, classification)
    if not public_read:
        if request.get("authn_valid") is not True:
            return _deny("authentication_required", classification)
        if request.get("capauth_valid") is not True:
            return _deny("capability_required", classification)
        if request.get("capability_matches") is not True:
            return _deny("capability_scope_mismatch", classification)

    resource_tenant = request.get("resource_tenant_id")
    principal_tenant = request.get("principal_tenant_id")
    if resource_tenant is not None or not public_read:
        if not resource_tenant or not principal_tenant:
            return _deny("tenant_scope_missing", classification)
        if resource_tenant != principal_tenant:
            return _deny("cross_tenant_denied", classification)

    resource_matter = request.get("resource_matter_id")
    if resource_matter is not None:
        if request.get("principal_matter_id") != resource_matter:
            return _deny("cross_matter_denied", classification)
        if request.get("matter_membership") is not True:
            return _deny("matter_membership_required", classification)
        conflict_status = request.get("conflict_status")
        if conflict_status == "waived":
            if request.get("waiver_valid") is not True:
                return _deny("conflict_waiver_invalid", classification)
        elif conflict_status != "clear":
            return _deny("conflict_denied_or_unresolved", classification)
        if request.get("ethical_wall_allowed") is not True:
            return _deny("ethical_wall_denied", classification)

    if any(
        request.get(field) is True
        for field in (
            "contains_secret",
            "contains_raw_capability",
            "contains_credential_material",
        )
    ):
        return _deny("raw_secret_or_capability_denied", classification)
    if request.get("credential_required") is True:
        if request.get("secret_store_reference_valid") is not True:
            return _deny("secret_store_reference_required", classification)

    protected_operations = set(active_policy["protected_access_operations"])
    if operation in protected_operations:
        if classification == "privileged_work_product":
            if request.get("privileged_access_authorized") is not True:
                return _deny("privileged_access_required", classification)
        if classification == "highly_restricted":
            if request.get("highly_restricted_access_authorized") is not True:
                return _deny("highly_restricted_access_required", classification)

    rights_state = request.get("source_rights")
    rights_rule = active_policy["rights_states"].get(rights_state)
    quarantined = rights_rule == "quarantine"
    rights_operations = set(active_policy["rights_controlled_operations"])
    quarantine_review_operations = set(active_policy["quarantine_review_operations"])
    rights_required = operation in rights_operations and (
        not public_read
        or rights_state is not None
        or request.get("source_governed") is True
    )
    isolated_quarantine_review = (
        operation in quarantine_review_operations
        and request.get("quarantine_review") is True
        and request.get("purpose") == active_policy["quarantine_review_purpose"]
    )
    if rights_required:
        if rights_rule is None:
            return _deny("source_rights_missing_or_unknown", classification)
        if quarantined:
            if not isolated_quarantine_review:
                return _deny("source_rights_quarantine", classification)
        elif operation not in set(request.get("rights_allowed_purposes", [])):
            return _deny("source_rights_purpose_denied", classification)

    if operation == "model_egress":
        route = request.get("model_route")
        if route not in {"local", "external"}:
            return _deny("model_route_missing_or_unknown", classification)
        if route == "external":
            egress_rule = active_policy["external_model_egress"][classification]
            if egress_rule == "deny":
                return _deny("classification_egress_denied", classification)
            if rights_rule is None or quarantined:
                return _deny("source_rights_egress_denied", classification)
            if "model_egress" not in set(request.get("rights_allowed_purposes", [])):
                return _deny("source_rights_purpose_denied", classification)
            if request.get("tenant_egress_allowed") is not True:
                return _deny("tenant_egress_denied", classification)
            if request.get("provider_route_approved") is not True:
                return _deny("provider_route_not_approved", classification)
            if request.get("purpose_approved") is not True:
                return _deny("egress_purpose_denied", classification)
            if request.get("context_minimized") is not True:
                return _deny("egress_minimization_required", classification)
            if egress_rule == "conditional_human_approval":
                if request.get("human_approval") is not True:
                    return _deny("human_egress_approval_required", classification)

    if operation == "tool_invoke":
        if request.get("tool_authority_source") != active_policy["tool_authority_source"]:
            return _deny("tool_authority_must_come_from_capauth", classification)
        if request.get("requested_authority_expansion") is True:
            return _deny("untrusted_authority_expansion_denied", classification)
        if request.get("arguments_validated") is not True:
            return _deny("tool_arguments_invalid", classification)

    if operation == "connector_dispatch":
        if request.get("exact_version_approved") is not True:
            return _deny("exact_version_approval_required", classification)
        if request.get("destination_verified") is not True:
            return _deny("destination_verification_required", classification)
        if not request.get("idempotency_key"):
            return _deny("idempotency_key_required", classification)
        mode = request.get("connector_mode")
        if mode not in {"simulation", "production"}:
            return _deny("connector_mode_missing_or_unknown", classification)
        if mode == "production":
            if request.get("connector_approved") is not True:
                return _deny("production_connector_not_approved", classification)
            if request.get("human_approval") is not True:
                return _deny("human_dispatch_approval_required", classification)

    if operation == "log":
        protected = set(active_policy["protected_logging_requires_redaction"])
        if classification in protected and request.get("redacted") is not True:
            return _deny("logging_redaction_required", classification)

    if operation == "retention_delete":
        if request.get("legal_hold") is True:
            return _deny("legal_hold_overrides_retention", classification)
        if request.get("legal_hold") is not False:
            return _deny("legal_hold_state_required", classification)
        if request.get("retention_expired") is not True:
            return _deny("retention_not_expired", classification)
        if request.get("deletion_approved") is not True:
            return _deny("deletion_approval_required", classification)

    if operation == "backup_restore":
        if request.get("encrypted") is not True:
            return _deny("backup_encryption_required", classification)
        if request.get("approved_destination") is not True:
            return _deny("backup_destination_denied", classification)
        if request.get("hold_metadata_preserved") is not True:
            return _deny("backup_hold_metadata_required", classification)

    if operation == "workflow_mutation":
        if request.get("workflow_replay") is True:
            if request.get("idempotency_receipt_verified") is not True:
                return _deny("workflow_replay_mutation_denied", classification)

    obligations = ["audit_policy_decision"]
    if classification in {"confidential", "privileged_work_product", "highly_restricted"}:
        obligations.append("minimize_protected_data")
    if quarantined:
        obligations.append("retain_quarantine")
    return Decision(True, "allowed", classification, tuple(obligations))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path, help="JSON policy request")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    args = parser.parse_args()
    with args.request.open("r", encoding="utf-8") as stream:
        request = json.load(stream)
    result = evaluate(request, load_policy(args.policy))
    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.allowed else 2


if __name__ == "__main__":
    raise SystemExit(main())
