from __future__ import annotations

import ast
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import sklegal_domain
from sklegal_domain import (
    Approval,
    ArtifactBinding,
    Authority,
    Claim,
    Client,
    Communication,
    CustodyEvent,
    Deadline,
    DeadlineCalculation,
    Defense,
    Element,
    Engagement,
    EvidenceItem,
    Execution,
    ExecutionEvent,
    ExecutionReceipt,
    ExecutionStep,
    FactAssertion,
    Forum,
    Issue,
    Matter,
    MatterEvent,
    Party,
    PartyRole,
    Proceeding,
    Remedy,
    SourceReference,
    Task,
    Tenant,
    TensionGroup,
    Transaction,
    TypedValue,
    ValidationOutcome,
    ValidationResult,
    WorkProduct,
    WorkProductVersion,
)

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def uid(value: int) -> UUID:
    return UUID(int=(1 << 127) | value)


def digest(character: str) -> str:
    return character * 64


class DomainContractIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tenant_id = uid(1)
        self.client_id = uid(2)
        self.engagement_id = uid(3)
        self.matter_id = uid(4)
        self.source = SourceReference(
            source_reference_id=uid(500),
            source_system="synthetic-fixture",
            source_version="v1",
            content_sha256=digest("a"),
            locator="fixture://domain/integration",
            observed_at=T0,
        )

    def audit(self, identifier: int) -> dict[str, object]:
        return {
            "id": uid(identifier),
            "created_at": T0,
            "updated_at": T0,
        }

    def protected(self, identifier: int) -> dict[str, object]:
        return {**self.audit(identifier), "tenant_id": self.tenant_id}

    def scoped(self, identifier: int) -> dict[str, object]:
        return {**self.protected(identifier), "matter_id": self.matter_id}

    def build_graph(self) -> tuple[object, ...]:
        tenant = Tenant(
            **self.protected(1),
            name="Synthetic Legal Tenant",
        )
        client = Client(
            **self.protected(2),
            display_name="Synthetic Client",
            client_kind="person",
        )
        engagement = Engagement(
            **self.protected(3),
            client_id=self.client_id,
            title="Synthetic Engagement",
            scope="Review a fixture-only legal question.",
        )
        matter = Matter(
            **self.protected(4),
            matter_id=self.matter_id,
            client_id=self.client_id,
            engagement_id=self.engagement_id,
            title="Synthetic Matter",
            summary="No person, production record, or protected corpus is used.",
        )
        party = Party(
            **self.scoped(10),
            display_name="Synthetic Party",
            party_kind="company",
            source_reference=self.source,
        )
        role = PartyRole(
            **self.scoped(11),
            party_id=party.id,
            role="counterparty",
            source_reference=self.source,
        )
        forum = Forum(
            **self.scoped(12),
            name="Synthetic Forum",
            jurisdiction="Fixture Jurisdiction",
            forum_kind="court",
        )
        proceeding = Proceeding(
            **self.scoped(13),
            title="Synthetic Proceeding Proposal",
            forum_id=forum.id,
        )
        matter_event = MatterEvent(
            **self.scoped(14),
            event_type="fixture_review",
            description="A synthetic review event.",
            observed_at=T0,
            source_reference=self.source,
        )
        transaction = Transaction(
            **self.scoped(15),
            title="Synthetic Transaction",
            description="A transaction represented only by fixtures.",
            party_role_ids=(role.id,),
            source_references=(self.source,),
        )
        fact_one = FactAssertion(
            **self.scoped(16),
            subject_ref=party.id,
            predicate="fixture.has_value",
            asserted_value=TypedValue(value_type="string", value="alpha"),
            source_reference=self.source,
            source_locator="/facts/0",
            observed_at=T0,
        )
        fact_two = FactAssertion(
            **self.scoped(17),
            subject_ref=party.id,
            predicate="fixture.has_value",
            asserted_value=TypedValue(value_type="string", value="beta"),
            source_reference=self.source,
            source_locator="/facts/1",
            observed_at=T0,
        )
        tension = TensionGroup(
            **self.scoped(18),
            title="Synthetic value tension",
            assertion_ids=(fact_one.id, fact_two.id),
        )
        evidence = EvidenceItem(
            **self.scoped(19),
            title="Synthetic Evidence",
            media_type="text/plain",
            content_sha256=digest("b"),
            source_reference=self.source,
        )
        custody = CustodyEvent(
            **self.scoped(20),
            evidence_item_id=evidence.id,
            action="acquired",
            custodian_id=uid(700),
            occurred_at=T0,
            source_reference=self.source,
        )
        issue = Issue(
            **self.scoped(21),
            question="What does the wholly synthetic fixture establish?",
        )
        authority = Authority(
            **self.scoped(22),
            title="Synthetic Authority",
            citation="Fixture Reporter 1",
            jurisdiction="Fixture Jurisdiction",
            authority_kind="case",
            source_reference=self.source,
        )
        element = Element(
            **self.scoped(23),
            claim_id=uid(24),
            description="A synthetic element.",
        )
        claim = Claim(
            **self.scoped(24),
            issue_id=issue.id,
            label="Synthetic Claim",
            statement="A fixture-only claim proposal.",
            element_ids=(element.id,),
            evidence_item_ids=(evidence.id,),
            authority_ids=(authority.id,),
        )
        defense = Defense(
            **self.scoped(25),
            issue_id=issue.id,
            label="Synthetic Defense",
            statement="A fixture-only defense proposal.",
        )
        remedy = Remedy(
            **self.scoped(26),
            claim_id=claim.id,
            description="A synthetic remedy proposal.",
            authority_ids=(authority.id,),
        )
        calculation = DeadlineCalculation(
            **self.scoped(27),
            trigger_fact_id=fact_one.id,
            calculation_rule="Add thirty fixture calendar days.",
            candidate_due_at=T0 + timedelta(days=30),
            calculated_at=T0,
            source_references=(self.source,),
            calculation_version="fixture-v1",
        )
        deadline = Deadline(
            **self.scoped(28),
            title="Synthetic deadline candidate",
            candidate_due_at=calculation.candidate_due_at,
            trigger_fact_id=fact_one.id,
            calculation_id=calculation.id,
        )
        task = Task(
            **self.scoped(29),
            title="Review fixture",
            description="Perform a fixture-only review.",
        )
        communication = Communication(
            **self.scoped(30),
            direction="internal",
            channel="other",
            subject="Synthetic internal communication",
            participant_ids=(party.id,),
        )
        version = WorkProductVersion(
            **self.scoped(31),
            work_product_id=uid(32),
            version_number=1,
            content_sha256=digest("c"),
            source_artifact_id=uid(600),
        )
        product = WorkProduct(
            **self.scoped(32),
            title="Synthetic Memo",
            work_product_kind="memo",
            current_version_id=version.id,
        )
        binding = ArtifactBinding(
            artifact_id=product.id,
            artifact_version=version.version_number,
            content_sha256=version.content_sha256,
        )
        validation = ValidationResult(
            **self.scoped(33),
            subject=binding,
            outcome=ValidationOutcome.PASSED,
            check_ids=("fixture.structure",),
            validator_principal_id=uid(700),
            validated_at=T0,
            rationale="The synthetic checks passed.",
        )
        approval = Approval(**self.scoped(34), subject=binding)
        execution = Execution(
            **self.scoped(35),
            subject=binding,
            destination_sha256=digest("d"),
            idempotency_key="synthetic-execution-1",
        )
        execution_event = ExecutionEvent(
            **self.scoped(36),
            execution_id=execution.id,
            step=ExecutionStep.VALIDATED,
            occurred_at=T0,
            correlation_id="synthetic-correlation-1",
            actor_principal_id=uid(700),
        )
        execution_receipt = ExecutionReceipt(
            **self.scoped(37),
            execution_id=execution.id,
            connector="synthetic-connector",
            external_receipt_id="synthetic-receipt-1",
            artifact_content_sha256=binding.content_sha256,
            destination_sha256=execution.destination_sha256,
            received_at=T0,
            verified_at=T0,
        )
        return (
            tenant,
            client,
            engagement,
            matter,
            party,
            role,
            forum,
            proceeding,
            matter_event,
            transaction,
            fact_one,
            fact_two,
            tension,
            evidence,
            custody,
            issue,
            authority,
            element,
            claim,
            defense,
            remedy,
            calculation,
            deadline,
            task,
            communication,
            version,
            product,
            validation,
            approval,
            execution,
            execution_event,
            execution_receipt,
        )

    def test_complete_synthetic_graph_round_trips_without_boundary_loss(self) -> None:
        graph = self.build_graph()
        self.assertEqual(32, len(graph))
        for record in graph:
            with self.subTest(record=type(record).__name__):
                restored = type(record).model_validate_json(record.model_dump_json())
                self.assertEqual(record, restored)
                if hasattr(record, "tenant_id"):
                    self.assertEqual(self.tenant_id, record.tenant_id)
                if hasattr(record, "matter_id"):
                    self.assertEqual(self.matter_id, record.matter_id)

    def test_representative_business_transitions_preserve_boundaries(self) -> None:
        graph = self.build_graph()
        tenant = next(record for record in graph if isinstance(record, Tenant))
        client = next(record for record in graph if isinstance(record, Client))
        matter = next(record for record in graph if isinstance(record, Matter))
        validation = next(
            record for record in graph if isinstance(record, ValidationResult)
        )
        approval = next(record for record in graph if isinstance(record, Approval))
        execution = next(record for record in graph if isinstance(record, Execution))
        active_tenant = tenant.transition_to(
            sklegal_domain.TenantStatus.ACTIVE,
            at=T0 + timedelta(seconds=1),
        )
        active_client = client.transition_to(
            sklegal_domain.ClientStatus.ACTIVE,
            at=T0 + timedelta(seconds=1),
        )
        open_matter = matter.transition_to(
            sklegal_domain.MatterStatus.OPEN,
            at=T0 + timedelta(seconds=1),
            opened_at=T0 + timedelta(seconds=1),
        )
        validation_event = ExecutionEvent(
            **{
                **self.scoped(38),
                "created_at": T0 + timedelta(seconds=1),
                "updated_at": T0 + timedelta(seconds=1),
            },
            execution_id=execution.id,
            step=ExecutionStep.VALIDATED,
            occurred_at=T0 + timedelta(seconds=1),
            correlation_id="synthetic-correlation-validated",
            actor_principal_id=uid(700),
        )
        validated_execution = execution.transition_to(
            sklegal_domain.ExecutionStatus.VALIDATED,
            at=T0 + timedelta(seconds=1),
            validation_result=validation,
            events=(validation_event,),
        )
        approved_decision = approval.transition_to(
            sklegal_domain.ApprovalStatus.APPROVED,
            at=T0 + timedelta(seconds=2),
            reviewer_principal_id=uid(700),
            decided_at=T0 + timedelta(seconds=2),
            rationale="The fixture artifact is approved exactly as hashed.",
        )
        approval_event = ExecutionEvent(
            **{
                **self.scoped(39),
                "created_at": T0 + timedelta(seconds=2),
                "updated_at": T0 + timedelta(seconds=2),
            },
            execution_id=execution.id,
            step=ExecutionStep.APPROVED,
            occurred_at=T0 + timedelta(seconds=2),
            correlation_id="synthetic-correlation-approved",
            actor_principal_id=uid(700),
        )
        approved_execution = validated_execution.transition_to(
            sklegal_domain.ExecutionStatus.APPROVED,
            at=T0 + timedelta(seconds=2),
            approval=approved_decision,
            events=(*validated_execution.events, approval_event),
        )
        for record in (active_tenant, active_client, open_matter):
            with self.subTest(record=type(record).__name__):
                self.assertEqual(self.tenant_id, record.tenant_id)
                self.assertEqual(2, record.version)
        self.assertEqual(validation, approved_execution.validation_result)
        self.assertEqual(approved_decision, approved_execution.approval)

    def test_package_has_no_database_web_or_ui_imports(self) -> None:
        package = ROOT / "packages" / "domain" / "src" / "sklegal_domain"
        prohibited = {
            "fastapi",
            "sqlalchemy",
            "psycopg",
            "asyncpg",
            "temporalio",
            "react",
        }
        seen: set[str] = set()
        for path in sorted(package.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    seen.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    if node.module:
                        seen.add(node.module.split(".", 1)[0])
        self.assertTrue(
            prohibited.isdisjoint(seen), sorted(prohibited.intersection(seen))
        )

    def test_public_entity_names_are_legal_domain_only(self) -> None:
        forbidden = {"Problem", "Incident"}
        entity_names = {
            value.__name__
            for name in sklegal_domain.__all__
            if isinstance((value := getattr(sklegal_domain, name)), type)
        }
        self.assertTrue(forbidden.isdisjoint(entity_names))


if __name__ == "__main__":
    unittest.main()
