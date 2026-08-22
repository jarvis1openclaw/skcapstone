"""Build the strict synthetic 36-entity write-first persistence contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sklegal_domain
from sklegal_domain.base import DomainEntity
from sklegal_persistence import PersistenceMetadata


@dataclass(frozen=True)
class FreshPersistenceContract:
    entities: dict[str, DomainEntity]
    metadata: dict[str, PersistenceMetadata]
    auxiliary_entities: tuple[DomainEntity, ...]
    auxiliary_metadata: tuple[PersistenceMetadata, ...]
    tenant_id: UUID
    principal_id: UUID
    matter_id: UUID
    runtime_role: str


def _uid(value: int) -> str:
    return f"a1000000-0000-4000-8000-{value:012d}"


def _entity(name: str, payload: dict[str, Any]) -> DomainEntity:
    entity_type = getattr(sklegal_domain, name)
    return entity_type.model_validate_json(json.dumps(payload, sort_keys=True))


def build_fresh_contract() -> FreshPersistenceContract:
    at = datetime(2026, 8, 20, 5, 0, tzinfo=UTC)
    times = tuple(at + timedelta(minutes=index) for index in range(7))
    iso = tuple(value.isoformat().replace("+00:00", "Z") for value in times)
    tenant = _uid(1)
    principal = _uid(2)
    client = _uid(3)
    engagement = _uid(4)
    matter = _uid(5)
    source_id = _uid(6)
    forum = _uid(7)
    proceeding = _uid(8)
    party = _uid(9)
    party_role = _uid(10)
    matter_event = _uid(11)
    transaction = _uid(12)
    fact_one = _uid(13)
    fact_two = _uid(14)
    tension = _uid(15)
    evidence = _uid(16)
    custody = _uid(17)
    authority = _uid(18)
    issue = _uid(19)
    claim = _uid(20)
    defense = _uid(21)
    claim_element = _uid(22)
    defense_element = _uid(23)
    remedy = _uid(24)
    calculation = _uid(25)
    deadline = _uid(26)
    task = _uid(27)
    communication = _uid(28)
    work_product = _uid(29)
    work_product_version = _uid(30)
    source_artifact = _uid(31)
    validation = _uid(32)
    approval = _uid(33)
    execution = _uid(34)
    receipt = _uid(35)
    event_ids = tuple(_uid(value) for value in range(36, 41))
    template = _uid(41)
    template_version = _uid(42)
    unknown = _uid(43)
    ledger_claim = _uid(44)
    claim_support = _uid(45)
    digest = "a" * 64
    destination_digest = "b" * 64

    def audited(
        identifier: str, *, version: int = 1, updated: int = 6
    ) -> dict[str, Any]:
        return {
            "id": identifier,
            "version": version,
            "created_at": iso[0],
            "updated_at": iso[updated],
        }

    def protected(
        identifier: str, *, version: int = 1, updated: int = 6
    ) -> dict[str, Any]:
        return {
            **audited(identifier, version=version, updated=updated),
            "tenant_id": tenant,
        }

    def scoped(
        identifier: str, *, version: int = 1, updated: int = 6
    ) -> dict[str, Any]:
        return {
            **protected(identifier, version=version, updated=updated),
            "matter_id": matter,
        }

    source = {
        "source_reference_id": source_id,
        "source_system": "synthetic",
        "source_version": "v1",
        "content_sha256": "c" * 64,
        "locator": "synthetic:fresh-contract",
        "observed_at": iso[1],
    }
    subject = {
        "artifact_id": work_product_version,
        "artifact_version": 1,
        "content_sha256": digest,
    }
    validation_payload = {
        **scoped(validation, version=2, updated=2),
        "subject": subject,
        "outcome": "passed",
        "check_ids": ["synthetic.fresh-contract"],
        "validator_principal_id": principal,
        "validated_at": iso[2],
        "rationale": "Synthetic write-first validation.",
    }
    approval_payload = {
        **scoped(approval, version=2, updated=3),
        "subject": subject,
        "reviewer_principal_id": principal,
        "decided_at": iso[3],
        "rationale": "Synthetic write-first approval.",
        "status": "approved",
    }
    event_steps = ("validated", "approved", "queued", "dispatched", "receipt_verified")
    event_payloads = tuple(
        {
            **scoped(event_id, updated=index + 2),
            "execution_id": execution,
            "step": step,
            "occurred_at": iso[index + 2],
            "correlation_id": f"synthetic-fresh-{step}",
            "actor_principal_id": principal,
            "receipt_id": receipt if step == "receipt_verified" else None,
        }
        for index, (event_id, step) in enumerate(
            zip(event_ids, event_steps, strict=True)
        )
    )
    receipt_payload = {
        **scoped(receipt, updated=6),
        "execution_id": execution,
        "connector": "synthetic-connector",
        "external_receipt_id": "synthetic-fresh-receipt",
        "artifact_content_sha256": digest,
        "destination_sha256": destination_digest,
        "received_at": iso[5],
        "verified_at": iso[6],
    }

    payloads: dict[str, dict[str, Any]] = {
        "Tenant": {
            **protected(tenant),
            "name": "Synthetic Fresh Tenant",
            "status": "active",
        },
        "Client": {
            **protected(client),
            "display_name": "Synthetic Fresh Client",
            "client_kind": "person",
            "status": "proposed",
        },
        "Engagement": {
            **protected(engagement),
            "client_id": client,
            "title": "Synthetic Fresh Engagement",
            "scope": "Synthetic write-first persistence acceptance.",
            "effective_interval": {},
            "status": "proposed",
        },
        "Matter": {
            **scoped(matter),
            "client_id": client,
            "engagement_id": engagement,
            "title": "Synthetic Fresh Matter",
            "summary": "Synthetic-only write-first persistence acceptance.",
            "aliases": [],
            "status": "proposed",
        },
        "Party": {
            **scoped(party),
            "display_name": "Synthetic Fresh Party",
            "party_kind": "person",
            "source_reference": source,
            "status": "proposed",
        },
        "PartyRole": {
            **scoped(party_role),
            "party_id": party,
            "role": "client",
            "effective_interval": {},
            "source_reference": source,
            "status": "proposed",
        },
        "Forum": {
            **scoped(forum),
            "name": "Synthetic Fresh Forum",
            "jurisdiction": "Synthetic",
            "forum_kind": "court",
            "source_reference": source,
        },
        "Proceeding": {
            **scoped(proceeding),
            "title": "Synthetic Fresh Proceeding",
            "forum_id": forum,
            "effective_interval": {},
            "status": "proposed",
        },
        "MatterEvent": {
            **scoped(matter_event),
            "event_type": "synthetic_fresh_event",
            "description": "Synthetic fresh matter event.",
            "occurred_at": iso[2],
            "observed_at": iso[2],
            "source_reference": source,
            "aliases": [],
            "status": "proposed",
        },
        "Transaction": {
            **scoped(transaction, version=3),
            "title": "Synthetic Fresh Transaction",
            "description": "Synthetic fresh legal transaction.",
            "party_role_ids": [party_role],
            "source_references": [source],
            "status": "proposed",
        },
        "FactAssertion": {
            **scoped(fact_one),
            "subject_ref": transaction,
            "predicate": "synthetic_amount",
            "asserted_value": {"value_type": "integer", "value": 7},
            "source_reference": source,
            "source_locator": "synthetic:line:7",
            "effective_interval": {},
            "observed_at": iso[2],
            "status": "source_asserted",
        },
        "TensionGroup": {
            **scoped(tension, version=3),
            "title": "Synthetic Fresh Tension",
            "assertion_ids": [fact_one, fact_two],
            "status": "unresolved",
        },
        "EvidenceItem": {
            **scoped(evidence),
            "title": "Synthetic Fresh Evidence",
            "media_type": "text/plain",
            "content_sha256": "d" * 64,
            "source_reference": source,
            "status": "proposed",
        },
        "CustodyEvent": {
            **scoped(custody),
            "evidence_item_id": evidence,
            "action": "acquired",
            "custodian_id": principal,
            "occurred_at": iso[2],
            "source_reference": source,
        },
        "Authority": {
            **scoped(authority),
            "title": "Synthetic Fresh Authority",
            "citation": "Synthetic A.1",
            "jurisdiction": "Synthetic",
            "authority_kind": "administrative_material",
            "source_reference": source,
            "effective_interval": {},
            "status": "proposed",
        },
        "Issue": {
            **scoped(issue),
            "question": "What is the synthetic fresh issue?",
            "status": "identified",
        },
        "Claim": {
            **scoped(claim, version=4),
            "issue_id": issue,
            "label": "Synthetic Fresh Claim",
            "statement": "Synthetic fresh claim statement.",
            "element_ids": [claim_element],
            "evidence_item_ids": [evidence],
            "authority_ids": [authority],
            "status": "proposed",
        },
        "Defense": {
            **scoped(defense, version=3),
            "issue_id": issue,
            "label": "Synthetic Fresh Defense",
            "statement": "Synthetic fresh defense statement.",
            "element_ids": [defense_element],
            "evidence_item_ids": [evidence],
            "status": "proposed",
        },
        "Element": {
            **scoped(claim_element, version=2),
            "theory_kind": "claim",
            "claim_id": claim,
            "description": "Synthetic fresh claim element.",
            "evidence_item_ids": [evidence],
            "status": "alleged",
        },
        "Remedy": {
            **scoped(remedy, version=2),
            "claim_id": claim,
            "description": "Synthetic fresh remedy.",
            "authority_ids": [authority],
            "status": "proposed",
        },
        "ClaimSupport": {
            **scoped(claim_support),
            "claim_id": ledger_claim,
            "kind": "support",
            "source_reference": source,
            "span_start": 0,
            "span_end": 12,
            "excerpt_sha256": "e" * 64,
            "note": None,
            "recorded_by_principal_id": principal,
            "policy_revision": digest,
        },
        "LedgerClaim": {
            **scoped(ledger_claim),
            "statement": "Synthetic fresh ledger claim statement.",
            "policy_revision": digest,
            "support": [
                {
                    **scoped(claim_support),
                    "claim_id": ledger_claim,
                    "kind": "support",
                    "source_reference": source,
                    "span_start": 0,
                    "span_end": 12,
                    "excerpt_sha256": "e" * 64,
                    "note": None,
                    "recorded_by_principal_id": principal,
                    "policy_revision": digest,
                }
            ],
            "status": "proposed",
        },
        "DeadlineCalculation": {
            **scoped(calculation, version=2),
            "trigger_fact_id": fact_one,
            "calculation_rule": "Synthetic plus seven days.",
            "candidate_due_at": iso[6],
            "calculated_at": iso[2],
            "source_references": [source],
            "calculation_version": "v1",
        },
        "Deadline": {
            **scoped(deadline),
            "title": "Synthetic Fresh Deadline",
            "status": "candidate",
        },
        "Task": {
            **scoped(task),
            "title": "Synthetic Fresh Task",
            "description": "Synthetic fresh task description.",
            "status": "draft",
        },
        "Communication": {
            **scoped(communication, version=2),
            "direction": "internal",
            "channel": "calendar",
            "subject": "Synthetic fresh communication",
            "participant_ids": [party],
            "status": "draft",
        },
        "WorkProduct": {
            **scoped(work_product),
            "title": "Synthetic Fresh Work Product",
            "work_product_kind": "memo",
            "current_version_id": work_product_version,
            "status": "draft",
        },
        "WorkProductVersion": {
            **scoped(work_product_version, version=2),
            "work_product_id": work_product,
            "version_number": 1,
            "content_sha256": digest,
            "source_artifact_id": source_artifact,
            "status": "frozen",
        },
        "WorkProductTemplate": {
            **protected(template),
            "name": "Synthetic Fresh Template",
            "work_product_kind": "memo",
            "current_version_id": template_version,
            "status": "draft",
        },
        "WorkProductTemplateVersion": {
            **protected(template_version),
            "template_id": template,
            "version_number": 1,
            "content_sha256": digest,
            "status": "draft",
        },
        "WorkProductUnknown": {
            **scoped(unknown, version=2),
            "version_binding": subject,
            "placeholder_key": "client_name",
            "hint": "Full legal name",
            "resolved_by_principal_id": principal,
            "resolved_at": iso[4],
            "status": "resolved",
        },
        "ValidationResult": validation_payload,
        "Approval": approval_payload,
        "Execution": {
            **scoped(execution, version=6, updated=6),
            "subject": subject,
            "destination_sha256": destination_digest,
            "idempotency_key": "synthetic-fresh-execution",
            "validation_result": validation_payload,
            "approval": approval_payload,
            "events": list(event_payloads),
            "receipt": receipt_payload,
            "status": "receipt_verified",
        },
        "ExecutionEvent": event_payloads[0],
        "ExecutionReceipt": receipt_payload,
    }
    entities = {name: _entity(name, payload) for name, payload in payloads.items()}
    fact_two_entity = _entity(
        "FactAssertion",
        {
            **scoped(fact_two),
            "subject_ref": transaction,
            "predicate": "synthetic_amount",
            "asserted_value": {"value_type": "integer", "value": 8},
            "source_reference": source,
            "source_locator": "synthetic:line:8",
            "effective_interval": {},
            "observed_at": iso[2],
            "status": "source_asserted",
        },
    )
    defense_element_entity = _entity(
        "Element",
        {
            **scoped(defense_element),
            "theory_kind": "defense",
            "claim_id": defense,
            "description": "Synthetic fresh defense element.",
            "status": "alleged",
        },
    )

    source_metadata = {
        "tenant_id": UUID(tenant),
        "matter_id": UUID(matter),
        "classification": "confidential",
        "completeness": "incomplete",
        "version": 1,
        "created_at": times[0],
        "updated_at": times[6],
    }

    def relation(**values: Any) -> dict[str, Any]:
        return {
            "tenant_id": UUID(tenant),
            "matter_id": UUID(matter),
            "created_at": times[0],
            **values,
        }

    def source_relation(**values: Any) -> dict[str, Any]:
        return {**source_metadata, **values}

    empty = PersistenceMetadata()
    metadata: dict[str, PersistenceMetadata] = {name: empty for name in entities}
    metadata["Tenant"] = PersistenceMetadata(scalar={"slug": "synthetic-fresh"})
    metadata["Matter"] = PersistenceMetadata(relations={"aliases": ()})
    for name in (
        "Party",
        "Forum",
        "FactAssertion",
        "EvidenceItem",
        "CustodyEvent",
    ):
        metadata[name] = PersistenceMetadata(
            relations={"source_reference": (source_relation(),)}
        )
    metadata["Authority"] = PersistenceMetadata(
        scalar={"system_from": times[0], "system_to": None},
        relations={"source_reference": (source_relation(),)},
    )
    metadata["PartyRole"] = PersistenceMetadata(
        scalar={"proceeding_id": UUID(proceeding)},
        relations={"source_reference": (source_relation(),)},
    )
    metadata["MatterEvent"] = PersistenceMetadata(
        relations={"source_reference": (source_relation(),), "aliases": ()}
    )
    metadata["Transaction"] = PersistenceMetadata(
        relations={
            "party_roles": (relation(transaction_id=UUID(transaction)),),
            "source_references": (
                source_relation(
                    transaction_id=UUID(transaction), relation_created_at=times[0]
                ),
            ),
        }
    )
    metadata["TensionGroup"] = PersistenceMetadata(
        relations={
            "assertions": (
                relation(tension_group_id=UUID(tension)),
                relation(tension_group_id=UUID(tension)),
            )
        }
    )
    metadata["Claim"] = PersistenceMetadata(
        relations={
            "elements": (
                relation(
                    theory_kind="claim",
                    claim_id=UUID(claim),
                ),
            ),
            "evidence": (relation(theory_kind="claim", theory_id=UUID(claim)),),
            "authorities": (relation(theory_kind="claim", theory_id=UUID(claim)),),
        }
    )
    metadata["Defense"] = PersistenceMetadata(
        relations={
            "elements": (relation(theory_kind="defense", claim_id=UUID(defense)),),
            "evidence": (relation(theory_kind="defense", theory_id=UUID(defense)),),
            "authorities": (),
        }
    )
    metadata["Element"] = PersistenceMetadata(
        relations={"evidence": (relation(element_id=UUID(claim_element)),)}
    )
    defense_element_metadata = PersistenceMetadata(relations={"evidence": ()})
    metadata["Remedy"] = PersistenceMetadata(
        relations={"authorities": (relation(remedy_id=UUID(remedy)),)}
    )
    metadata["ClaimSupport"] = PersistenceMetadata(
        relations={"source_reference": (source_relation(),)}
    )
    metadata["LedgerClaim"] = PersistenceMetadata(
        scalar={"system_from": times[0], "system_to": None},
        relations={
            "support": ({"nested_metadata": metadata["ClaimSupport"]},),
        },
    )
    metadata["DeadlineCalculation"] = PersistenceMetadata(
        relations={
            "source_references": (
                source_relation(
                    calculation_id=UUID(calculation),
                    relation_created_at=times[0],
                ),
            )
        }
    )
    metadata["Communication"] = PersistenceMetadata(
        scalar={
            "work_product_version_number": None,
            "work_product_content_sha256": None,
        },
        relations={"participants": (relation(communication_id=UUID(communication)),)},
    )
    metadata["WorkProduct"] = PersistenceMetadata(
        scalar={"current_version_number": 1, "current_content_sha256": digest}
    )
    metadata["WorkProductVersion"] = PersistenceMetadata(
        scalar={
            "encrypted_content": None,
            "encryption_key_ref": None,
            "encryption_algorithm": None,
            "encrypted_at": None,
        }
    )
    metadata["WorkProductTemplate"] = PersistenceMetadata(
        scalar={"current_version_number": 1, "current_content_sha256": digest}
    )
    metadata["WorkProductTemplateVersion"] = PersistenceMetadata(
        scalar={
            "encrypted_content": None,
            "encryption_key_ref": None,
            "encryption_algorithm": None,
            "encrypted_at": None,
        }
    )
    metadata["ValidationResult"] = PersistenceMetadata(
        relations={
            "checks": (relation(validation_id=UUID(validation)),),
        }
    )
    event_metadata = tuple(
        PersistenceMetadata(scalar={"sequence_no": index + 1}) for index in range(5)
    )
    metadata["ExecutionEvent"] = event_metadata[0]
    metadata["Execution"] = PersistenceMetadata(
        scalar={
            "validation_result_id": UUID(validation),
            "approval_id": UUID(approval),
            "approval_version": 2,
        },
        relations={
            "validation_result": ({"nested_metadata": metadata["ValidationResult"]},),
            "approval": ({"nested_metadata": metadata["Approval"]},),
            "events": tuple({"nested_metadata": item} for item in event_metadata),
            "receipt": ({"nested_metadata": metadata["ExecutionReceipt"]},),
        },
    )
    return FreshPersistenceContract(
        entities=entities,
        metadata=metadata,
        auxiliary_entities=(fact_two_entity, defense_element_entity),
        auxiliary_metadata=(
            PersistenceMetadata(relations={"source_reference": (source_relation(),)}),
            defense_element_metadata,
        ),
        tenant_id=UUID(tenant),
        principal_id=UUID(principal),
        matter_id=UUID(matter),
        runtime_role="sklegal_test_fresh_writer",
    )
