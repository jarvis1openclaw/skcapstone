"""SKL-S5-03A email connector simulation qualification.

Exercises the complete simulation path for the email connector end to end:
exact-version approval, destination verification, duplicate suppression,
synthetic receipts, receipt reconciliation, and the structural guarantee
that the connector code contains no live transport.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest
from sklegal_connectors import (
    Action,
    ActionStatus,
    ApprovalBinding,
    ConnectorInvariantError,
    SimulationRegistry,
)
from sklegal_email import (
    EmailAction,
    EmailReceipt,
    EmailSimulation,
    EmailValidationError,
    ReceiptStatus,
)

ARTIFACT_V2 = hashlib.sha256(b"email-artifact-v2").hexdigest()
ARTIFACT_V3 = hashlib.sha256(b"email-artifact-v3").hexdigest()
REPO_ROOT = Path(__file__).resolve().parents[2]

BANNED_MODULES = {
    "socket",
    "smtplib",
    "ssl",
    "urllib",
    "urllib3",
    "http",
    "httpx",
    "requests",
    "aiohttp",
    "ftplib",
    "imaplib",
    "poplib",
    "telnetlib",
    "subprocess",
}
BANNED_CALLS = {"open", "eval", "exec", "compile", "__import__", "input"}
BANNED_ATTR_CALLS = {("os", "system"), ("os", "popen")}


class Capability:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def verify(self, *, action: Action, capability_ref: str) -> bool:
        return self.allowed and capability_ref == "cap:email"


def approved_email(
    simulation: EmailSimulation,
    *,
    action_id: str = "email-1",
    recipients: tuple[str, ...] = ("Counsel@Example.com",),
) -> EmailAction:
    draft = simulation.draft(
        tenant_id="tenant-1",
        matter_id="matter-1",
        action_id=action_id,
        artifact_id="work-product-1",
        artifact_version=2,
        artifact_sha256=ARTIFACT_V2,
        recipients=recipients,
        subject="Review",
    )
    validated = simulation.validate(draft)
    return simulation.approve(
        validated,
        ApprovalBinding("approval-1", "work-product-1", 2, ARTIFACT_V2),
    )


def queued_email(
    simulation: EmailSimulation, *, action_id: str = "email-1"
) -> EmailAction:
    return simulation.queue(
        approved_email(simulation, action_id=action_id),
        capability_ref="cap:email",
        capability_verifier=Capability(),
    )


def assert_no_live_transport(*packages: str) -> None:
    for package in packages:
        root = REPO_ROOT / "packages" / "connectors" / package / "src"
        assert root.is_dir(), f"missing connector source tree: {root}"
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root_module = alias.name.split(".")[0]
                        assert root_module not in BANNED_MODULES, (
                            f"{path}: {alias.name}"
                        )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root_module = node.module.split(".")[0]
                    assert root_module not in BANNED_MODULES, f"{path}: {node.module}"
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        assert node.func.id not in BANNED_CALLS, (
                            f"{path}: {node.func.id}"
                        )
                    elif isinstance(node.func, ast.Attribute) and isinstance(
                        node.func.value, ast.Name
                    ):
                        pair = (node.func.value.id, node.func.attr)
                        assert pair not in BANNED_ATTR_CALLS, f"{path}: {pair}"


def test_end_to_end_simulation_reaches_receipt_verified() -> None:
    simulation = EmailSimulation()
    dispatched, receipt = simulation.dispatch(queued_email(simulation))
    verified = simulation.reconcile(dispatched, receipt)

    assert verified.action.status is ActionStatus.RECEIPT_VERIFIED
    assert receipt.status is ReceiptStatus.DELIVERED
    assert receipt.recipient_count == 1
    assert receipt.simulation_receipt.simulated is True
    assert receipt.simulation_receipt.artifact_sha256 == ARTIFACT_V2
    assert (
        receipt.simulation_receipt.destination_sha256
        == dispatched.action.destination_sha256
    )
    assert receipt.simulation_receipt.idempotency_key == (
        dispatched.action.idempotency_key
    )
    assert simulation.dispatch_count == 1


def test_exact_version_approval_binds_artifact_version_and_digest() -> None:
    simulation = EmailSimulation()
    draft = simulation.draft(
        tenant_id="tenant-1",
        matter_id="matter-1",
        action_id="email-1",
        artifact_id="work-product-1",
        artifact_version=2,
        artifact_sha256=ARTIFACT_V2,
        recipients=("counsel@example.com",),
        subject="Review",
    )
    validated = simulation.validate(draft)

    with pytest.raises(ConnectorInvariantError, match="exact artifact version"):
        simulation.approve(
            validated, ApprovalBinding("approval-1", "work-product-1", 3, ARTIFACT_V2)
        )
    with pytest.raises(ConnectorInvariantError, match="exact artifact version"):
        simulation.approve(
            validated, ApprovalBinding("approval-1", "work-product-1", 2, ARTIFACT_V3)
        )
    with pytest.raises(ConnectorInvariantError, match="exact artifact version"):
        simulation.approve(
            validated, ApprovalBinding("approval-1", "work-product-9", 2, ARTIFACT_V2)
        )

    changed_draft = simulation.draft(
        tenant_id="tenant-1",
        matter_id="matter-1",
        action_id="email-1",
        artifact_id="work-product-1",
        artifact_version=3,
        artifact_sha256=ARTIFACT_V3,
        recipients=("counsel@example.com",),
        subject="Review",
    )
    with pytest.raises(ConnectorInvariantError, match="exact artifact digest"):
        changed_draft.action.validate(artifact_sha256=ARTIFACT_V2)
    with pytest.raises(ConnectorInvariantError, match="exact artifact version"):
        simulation.approve(
            simulation.validate(changed_draft),
            ApprovalBinding("approval-1", "work-product-1", 2, ARTIFACT_V2),
        )


def test_destination_verification_binds_exact_recipients() -> None:
    simulation = EmailSimulation()
    approved = approved_email(simulation)

    expected = hashlib.sha256(
        "\0".join(("email-destination-v1", "counsel@example.com")).encode("utf-8")
    ).hexdigest()
    assert approved.action.destination_sha256 == expected

    same_mailbox = approved_email(simulation, recipients=("counsel@example.com",))
    assert same_mailbox.action.destination_sha256 == expected

    other = approved_email(simulation, recipients=("opposing@example.com",))
    assert other.action.destination_sha256 != expected
    assert other.action.idempotency_key != approved.action.idempotency_key

    with pytest.raises(ConnectorInvariantError, match="destination digest changed"):
        approved.action.queue(
            destination_sha256=other.action.destination_sha256,
            capability_ref="cap:email",
            capability_verifier=Capability(),
        )


def test_dispatch_requires_queued_gate() -> None:
    simulation = EmailSimulation()
    with pytest.raises(EmailValidationError, match="queued"):
        simulation.dispatch(approved_email(simulation))
    assert simulation.dispatch_count == 0


def test_duplicate_dispatch_suppression_across_registries() -> None:
    shared = SimulationRegistry()
    first_simulation = EmailSimulation(registry=shared)
    second_simulation = EmailSimulation(registry=shared)

    dispatched_one, receipt_one = first_simulation.dispatch(
        queued_email(first_simulation)
    )
    dispatched_two, receipt_two = second_simulation.dispatch(
        queued_email(second_simulation)
    )

    assert receipt_one == receipt_two
    assert dispatched_one.action == dispatched_two.action
    assert shared.receipt_count == 1
    assert first_simulation.dispatch_count == 1
    assert second_simulation.dispatch_count == 1


def test_receipt_reconciliation_rejects_foreign_and_mismatched_receipts() -> None:
    simulation = EmailSimulation()
    dispatched, receipt = simulation.dispatch(queued_email(simulation))
    foreign_dispatched, foreign_receipt = simulation.dispatch(
        queued_email(simulation, action_id="email-2")
    )
    assert simulation.dispatch_count == 2

    with pytest.raises(ConnectorInvariantError, match="receipt does not bind"):
        simulation.reconcile(dispatched, foreign_receipt)
    with pytest.raises(EmailValidationError, match="recipient count"):
        simulation.reconcile(
            dispatched,
            EmailReceipt(receipt.simulation_receipt, ReceiptStatus.DELIVERED, 2),
        )

    bounced = simulation.reconcile(
        dispatched,
        EmailReceipt(receipt.simulation_receipt, ReceiptStatus.BOUNCED, 1),
    )
    assert bounced.action.status is ActionStatus.FAILED
    assert bounced.action.failure_reason == "email provider reported a bounce"

    requeued = EmailAction(
        bounced.action.retry(), bounced.recipients, bounced.subject_digest
    )
    assert requeued.action.status is ActionStatus.QUEUED
    redispatched, redelivered = simulation.dispatch(requeued)
    assert redelivered.simulation_receipt == receipt.simulation_receipt

    recovered = simulation.reconcile(redispatched, redelivered)
    assert recovered.action.status is ActionStatus.RECEIPT_VERIFIED
    assert simulation.dispatch_count == 2
    assert foreign_dispatched.action.status is ActionStatus.DISPATCHED


def test_connector_code_has_no_live_transport() -> None:
    assert_no_live_transport("email", "base")
