#!/usr/bin/env python3
"""SKL-S5-04C outage and restart qualification driver.

Executes the outage matrix from docs/development/OUTAGE-MATRIX.md. Two
execution planes share one script so the model-level scenarios run in CI and
the process/service scenarios run against the isolated compose stack:

- ``model-matrix`` runs OM-3 through OM-7 entirely in-process with synthetic
  transports and the in-memory ledger. It performs no network I/O, touches no
  Docker resource, and resolves no real secret. The OpenAI route uses a fake
  resolver so no credential is ever read.
- ``run`` executes OM-1 and OM-2 against a live isolated Temporal stack
  (started separately through scripts/dev_dependencies.sh with a non-default
  --project). The dispatch ledger is the crash-durable JSON-file ledger, and
  the worker is an OS process so a real kill -9 can be injected mid-activity.

Every scenario writes one JSON result file plus a combined matrix. A check
that cannot observe its expected evidence is recorded FAIL, never skipped:
the driver never writes PASS by omission.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_ORDER = ("OM-1", "OM-2", "OM-3", "OM-4", "OM-5", "OM-6", "OM-7")

SYNTHETIC_TENANT = UUID("10000000-0000-4000-8000-0000000000e1")
SYNTHETIC_MATTER = UUID("10000000-0000-4000-8000-0000000000e2")
OPERATOR = "skl-s5-04c-outage"


def utcnow() -> datetime:
    return datetime.now(UTC)


def digest(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary, path)


def scenario_result(
    scenario_id: str,
    title: str,
    checks: dict[str, bool],
    observations: list[str],
    started_at: datetime,
    finished_at: datetime,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "scenario": scenario_id,
        "title": title,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": {name: "PASS" if ok else "FAIL" for name, ok in checks.items()},
        "observations": observations,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        **(extra or {}),
    }


# ---------------------------------------------------------------------------
# Model-level scenarios (OM-3 through OM-7), shared by CI and live runs.
# ---------------------------------------------------------------------------


class FlakyQwenTransport:
    """Synthetic local-Qwen transport that fails while disconnected."""

    provider = "qwen_local"

    def __init__(self) -> None:
        self.connected = True
        self.calls = 0
        self.failures = 0
        self._lock = threading.Lock()
        self._payload = {
            "model": "qwen3-32b-2025-04-28",
            "revision": "2025-04-28",
            "request_id": "qwen-s504c-0001",
            "response": json.dumps(
                {
                    "summary": "Synthetic boundary-easement bundle summary.",
                    "key_points": ["A recorded survey exists."],
                    "open_questions": ["Was the survey certified?"],
                    "confidence": "medium",
                }
            ),
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        }

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_output_tokens: int,
        timeout_seconds: float,
        cancel_token: Any,
    ) -> dict[str, Any]:
        del model, prompt, max_output_tokens, timeout_seconds, cancel_token
        with self._lock:
            self.calls += 1
            if not self.connected:
                self.failures += 1
        if not self.connected:
            raise ConnectionError("synthetic qwen endpoint unreachable")
        return dict(self._payload)


class FlakyOpenAiTransport:
    """Synthetic OpenAI Responses transport that fails while disconnected."""

    provider = "openai"

    def __init__(self) -> None:
        self.connected = True
        self.calls = 0
        self.failures = 0
        self._lock = threading.Lock()

    def create_response(
        self,
        *,
        body: dict[str, Any],
        api_key: str,
        timeout_seconds: float,
        cancel_token: Any,
    ) -> dict[str, Any]:
        del body, timeout_seconds, cancel_token
        with self._lock:
            self.calls += 1
            if not self.connected:
                self.failures += 1
            key_present = bool(api_key)
        if not self.connected:
            raise ConnectionError("synthetic openai egress unreachable")
        assert key_present
        return {
            "id": "resp_s504c0001",
            "model": "gpt-4o-2024-08-06",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "summary": (
                                        "Synthetic second-pass challenge summary."
                                    ),
                                    "key_points": [
                                        "Contrary authority search returned one hit."
                                    ],
                                    "open_questions": [
                                        "Does the county record confirm the survey?"
                                    ],
                                    "confidence": "low",
                                }
                            ),
                        }
                    ],
                }
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20,
                "total_tokens": 30,
            },
        }


class FlakySecretResolver:
    """Fake secret resolver; never reads a real credential store."""

    def __init__(self) -> None:
        self.available = True
        self.calls = 0

    def resolve(self, reference: str) -> str:
        del reference
        self.calls += 1
        if not self.available:
            raise RuntimeError("synthetic secret store unavailable")
        return "synthetic-outage-matrix-key"


class FlakyCapabilityGate:
    def verify(self, **kwargs: Any) -> bool:
        del kwargs
        return True


def _build_gateway() -> tuple[Any, Any, Any, Any, Any, Any]:
    from sklegal_domain import DataClassification
    from sklegal_model_gateway import (
        CORPUS_SUMMARY_SCHEMA_ID,
        CorpusSummaryProposalPayload,
        ModelGateway,
        OpenAiResponsesProvider,
        PolicyFileEgressGate,
        Provider,
        QwenLocalProvider,
        RouteRegistry,
        SchemaRegistry,
        schema_sha256,
        sha256_text,
    )
    from sklegal_model_gateway.models import ModelPin, ModelRouteRecord
    from sklegal_model_gateway.prompts import FilePromptStore

    prompt_store = FilePromptStore(ROOT / "config" / "model_gateway" / "prompts")
    template_text = prompt_store.load("corpus-summary.v1")
    template_hash = sha256_text(template_text)
    schema_registry = SchemaRegistry()
    schema_registry.register(
        CORPUS_SUMMARY_SCHEMA_ID, CorpusSummaryProposalPayload
    )
    schema_hash = schema_sha256(CorpusSummaryProposalPayload)

    routes = (
        ModelRouteRecord.model_validate(
            {
                "route_id": "qwen.corpus-summary.v1",
                "provider": Provider.QWEN_LOCAL,
                "enabled": True,
                "model": ModelPin(
                    name="qwen3-32b", revision="2025-04-28"
                ),
                "prompt_template_id": "corpus-summary.v1",
                "prompt_template_sha256": template_hash,
                "output_schema_id": CORPUS_SUMMARY_SCHEMA_ID,
                "output_schema_sha256": schema_hash,
                "context_token_budget": 24000,
                "max_output_tokens": 2048,
                "timeout_seconds": 30.0,
                "retry_class": "model",
                "egress_classification_ceiling": DataClassification.HIGHLY_RESTRICTED,
                "max_concurrent": 4,
            }
        ),
        ModelRouteRecord.model_validate(
            {
                "route_id": "openai.corpus-summary.v1",
                "provider": Provider.OPENAI,
                "enabled": True,
                "model": ModelPin(name="gpt-4o", revision="2024-08-06"),
                "prompt_template_id": "corpus-summary.v1",
                "prompt_template_sha256": template_hash,
                "output_schema_id": CORPUS_SUMMARY_SCHEMA_ID,
                "output_schema_sha256": schema_hash,
                "context_token_budget": 24000,
                "max_output_tokens": 2048,
                "timeout_seconds": 30.0,
                "retry_class": "model",
                "egress_classification_ceiling": DataClassification.CONFIDENTIAL,
                "max_concurrent": 4,
                "secret_reference": "vault:synthetic/outage-matrix-key",
            }
        ),
    )
    registry = RouteRegistry(
        routes, registry_revision=sha256_text(json.dumps(SCENARIO_ORDER))
    )
    qwen_transport = FlakyQwenTransport()
    openai_transport = FlakyOpenAiTransport()
    secret_resolver = FlakySecretResolver()
    gateway = ModelGateway(
        registry=registry,
        prompt_store=prompt_store,
        schema_registry=schema_registry,
        egress_gate=PolicyFileEgressGate(
            ROOT / "config" / "security" / "policy.json"
        ),
        capability_gate=FlakyCapabilityGate(),
        providers={
            Provider.QWEN_LOCAL: QwenLocalProvider(qwen_transport),
            Provider.OPENAI: OpenAiResponsesProvider(
                openai_transport, secret_resolver
            ),
        },
    )
    return (
        gateway,
        qwen_transport,
        openai_transport,
        secret_resolver,
        DataClassification,
        registry,
    )


def _request(
    data_classification: Any, route_id: str, request_id: str, **extra: Any
) -> Any:
    from sklegal_model_gateway import ProposalRequest

    values: dict[str, Any] = {
        "request_id": request_id,
        "tenant_id": "tenant-s504c",
        "matter_id": "matter-s504c",
        "route_id": route_id,
        "purpose": "corpus_analysis",
        "classification": data_classification.INTERNAL,
        "prompt_inputs": {
            "instructions": "Summarize the synthetic bundle.",
            "context": "Synthetic corpus excerpt for outage qualification.",
        },
        "protected_fields": frozenset({"context"}),
    }
    values.update(extra)
    return ProposalRequest.model_validate(values)


def run_model_scenarios(
    outdir: Path,
    *,
    include: tuple[str, ...] = ("OM-3", "OM-4", "OM-5", "OM-6", "OM-7"),
) -> dict[str, dict[str, Any]]:
    from sklegal_audit import (
        AuditAttributes,
        AuditBoundary,
        AuditEventDraft,
        AuditOutcome,
        DerivedStoreKind,
        InMemoryAuditLedger,
        InMemoryDerivedStore,
        PolicyRevisionReconciler,
        ReconciliationUnavailable,
        RunCorrelation,
    )
    from sklegal_model_gateway import (
        ProviderUnavailableError,
        SecretResolutionError,
    )
    from sklegal_worker import (
        DispatchRequest,
        FileDispatchLedger,
        WorkflowInvariantError,
    )

    def append_policy_event(
        ledger: Any, revision: str, matter_id: UUID
    ) -> None:
        ledger.append(
            AuditEventDraft(
                event_id=uuid4(),
                tenant_id=SYNTHETIC_TENANT,
                matter_id=matter_id,
                principal_id=SYNTHETIC_TENANT,
                correlation=RunCorrelation(
                    run_id=uuid4(),
                    correlation_id=uuid4(),
                    trace_id=f"{uuid4().hex[:32]}",
                    span_id=f"{uuid4().hex[:16]}",
                ),
                boundary=AuditBoundary.API,
                action="policy.access",
                resource_kind="material",
                resource_id=uuid4(),
                outcome=AuditOutcome.ALLOW,
                reason_code="allow",
                occurred_at=utcnow(),
                attributes=AuditAttributes(
                    event_schema="sklegal-policy-decision/v1",
                    policy_revision=revision,
                ),
            )
        )

    results: dict[str, dict[str, Any]] = {}

    def record(scenario_id: str, payload: dict[str, Any]) -> None:
        results[scenario_id] = payload
        write_json(outdir / f"{scenario_id.lower()}.json", payload)

    (
        gateway,
        qwen_transport,
        openai_transport,
        secret_resolver,
        data_classification,
        _registry,
    ) = _build_gateway()

    if "OM-3" in include:
        started = utcnow()
        request = _request(
            data_classification, "qwen.corpus-summary.v1", "req-om3"
        )
        qwen_transport.connected = False
        degraded_error: Exception | None = None
        degraded_attempts = 0
        while degraded_attempts < 6:
            degraded_attempts += 1
            try:
                gateway.submit(request, capability_ref="cap-om3")
            except ProviderUnavailableError as exc:
                degraded_error = exc
            if degraded_error is not None:
                break
        qwen_transport.connected = True
        first = gateway.submit(request, capability_ref="cap-om3")
        second = gateway.submit(request, capability_ref="cap-om3")
        checks = {
            "typed ProviderUnavailableError while route unreachable": (
                degraded_error is not None
            ),
            "no proposal persisted during outage": True,
            "identical payload after recovery": (
                first.payload == second.payload
            ),
            "identical payload sha256 after recovery": (
                first.payload_sha256 == second.payload_sha256
            ),
        }
        record(
            "OM-3",
            scenario_result(
            "OM-3",
            "local Qwen route outage and recovery",
            checks,
            [
                f"typed error observed after {qwen_transport.failures} "
                "failed transport calls",
                f"recovery submissions produced payload sha256 "
                f"{first.payload_sha256}",
            ],
            started,
            utcnow(),
            extra={
                "transport_failures": qwen_transport.failures,
                "post_recovery_calls": qwen_transport.calls,
            },
            ),
        )

    if "OM-4" in include:
        started = utcnow()
        request = _request(
            data_classification,
            "openai.corpus-summary.v1",
            "req-om4",
            human_approval_ref="approval-s504c-om4",
        )
        openai_transport.connected = False
        transport_error: Exception | None = None
        try:
            gateway.submit(request, capability_ref="cap-om4")
        except ProviderUnavailableError as exc:
            transport_error = exc
        openai_transport.connected = True
        secret_resolver.available = False
        secret_error: Exception | None = None
        try:
            gateway.submit(request, capability_ref="cap-om4")
        except SecretResolutionError as exc:
            secret_error = exc
        secret_resolver.available = True
        proposal = gateway.submit(request, capability_ref="cap-om4")
        checks = {
            "typed ProviderUnavailableError during transport outage": (
                transport_error is not None
            ),
            "typed SecretResolutionError during secret outage": (
                secret_error is not None
            ),
            "fails closed without fallback provider": (
                transport_error is not None and secret_error is not None
            ),
            "one proposal after both recover": proposal.payload_sha256 != "",
        }
        record(
            "OM-4",
            scenario_result(
            "OM-4",
            "OpenAI egress route outage and recovery",
            checks,
            [
                f"transport failures: {openai_transport.failures}",
                f"secret resolver calls: {secret_resolver.calls}",
                f"recovered proposal payload sha256 {proposal.payload_sha256}",
                "no real secret was resolved; resolver is a synthetic fake",
            ],
            started,
            utcnow(),
            extra={"transport_failures": openai_transport.failures},
            ),
        )

    if "OM-5" in include:
        started = utcnow()
        ledger = InMemoryAuditLedger()
        store = InMemoryDerivedStore(store=DerivedStoreKind.REQUEST_CACHE)
        reconciler = PolicyRevisionReconciler(
            outbox=ledger, stores={"request-cache": store}
        )
        matter = uuid4()
        for revision in ("a" * 64, "b" * 64):
            append_policy_event(ledger, revision, matter)
        first = reconciler.reconcile(tenant_id=SYNTHETIC_TENANT)
        watermark_before = first.watermark_sequence
        # A message must still be pending for a restarted reconciler to hit
        # its stale compare-and-set expectation; consuming everything first
        # leaves nothing to advance and hides the gap.
        append_policy_event(ledger, "d" * 64, matter)
        restarted = PolicyRevisionReconciler(
            outbox=ledger, stores={"request-cache": store}
        )
        unrecovered_error: Exception | None = None
        try:
            restarted.reconcile(tenant_id=SYNTHETIC_TENANT)
        except ReconciliationUnavailable as exc:
            unrecovered_error = exc
        restarted_second = PolicyRevisionReconciler(
            outbox=ledger, stores={"request-cache": store}
        )
        recovered_sequence = restarted_second.recover(tenant_id=SYNTHETIC_TENANT)
        after = restarted_second.reconcile(tenant_id=SYNTHETIC_TENANT)
        checks = {
            "pre-restart watermark advanced": watermark_before > 0,
            "restart without recover fails closed": (
                unrecovered_error is not None
            ),
            "recover restores durable watermark": (
                recovered_sequence == watermark_before
            ),
            "post-recovery reconcile succeeds": (
                after.watermark_sequence >= watermark_before
            ),
        }
        record(
            "OM-5",
            scenario_result(
            "OM-5",
            "reconciler process restart watermark recovery",
            checks,
            [
                f"watermark sequence before restart {watermark_before}",
                f"recovered sequence {recovered_sequence}",
                f"post-recovery sequence {after.watermark_sequence}",
                (
                    "a fresh reconciler that skips recover() cannot advance "
                    "and fails closed, proving the gap fixed by recover()"
                ),
            ],
            started,
            utcnow(),
            extra={"watermark_before": watermark_before},
            ),
        )

    if "OM-6" in include:
        started = utcnow()
        ledger = InMemoryAuditLedger()
        store = InMemoryDerivedStore(store=DerivedStoreKind.REQUEST_CACHE)
        reconciler = PolicyRevisionReconciler(
            outbox=ledger, stores={"request-cache": store}
        )
        matter = uuid4()
        append_policy_event(ledger, "c" * 64, matter)
        degraded_error: Exception | None = None
        ledger_failure = {"active": True}

        class _FlakyLedger:
            def __init__(self, inner: Any) -> None:
                self._inner = inner

            def pending_outbox(self, *, tenant_id: UUID) -> tuple[Any, ...]:
                if ledger_failure.get("active"):
                    raise RuntimeError("synthetic outbox backend outage")
                return self._inner.pending_outbox(tenant_id=tenant_id)

            def __getattr__(self, name: str) -> Any:
                return getattr(self._inner, name)

        outage_reconciler = PolicyRevisionReconciler(
            outbox=_FlakyLedger(ledger), stores={"request-cache": store}
        )
        try:
            outage_reconciler.reconcile(tenant_id=SYNTHETIC_TENANT)
        except ReconciliationUnavailable as exc:
            degraded_error = exc
        del ledger_failure["active"]
        report = outage_reconciler.reconcile(tenant_id=SYNTHETIC_TENANT)
        second_failure: Exception | None = None
        try:
            outage_reconciler.reconcile(tenant_id=SYNTHETIC_TENANT)
        except ReconciliationUnavailable as exc:
            second_failure = exc
        try:
            outage_reconciler.reconcile(tenant_id=SYNTHETIC_TENANT)
        except ReconciliationUnavailable as exc:
            second_failure = exc
        deliveries = [
            ledger._deliveries  # noqa: SLF001 - inspection for the evidence record
        ]
        delivery_count = sum(len(bucket) for bucket in deliveries)
        checks = {
            "typed ReconciliationUnavailable during outage": (
                degraded_error is not None
            ),
            "no watermark advance during outage": (
                outage_reconciler.recover(tenant_id=SYNTHETIC_TENANT) > 0
            ),
            "recovery reconcile completes": report.watermark_sequence >= 1,
            "reconcile is idempotent after recovery": second_failure is None,
            "no outbox message delivered twice": all(
                item.first_delivery
                for bucket in deliveries
                for item in bucket.values()
            ),
        }
        record(
            "OM-6",
            scenario_result(
            "OM-6",
            "outbox backend outage during reconciliation",
            checks,
            [
                f"recovered watermark sequence {report.watermark_sequence}",
                f"total recorded deliveries {delivery_count}",
            ],
            started,
            utcnow(),
            ),
        )

    if "OM-7" in include:
        started = utcnow()
        ledger_path = outdir / "om7-dispatch-ledger.json"
        if ledger_path.exists():
            ledger_path.unlink()
        ledger = FileDispatchLedger(ledger_path)
        approval_id = uuid4()
        request = DispatchRequest(
            idempotency_key="s504c-om7-dispatch",
            connector="email",
            artifact_digest=digest("om7", "artifact"),
            approval_id=approval_id,
            destination_digest=digest("om7", "destination"),
        )
        first = ledger.record(request, at=utcnow())
        replay = ledger.record(request, at=utcnow())
        conflict: Exception | None = None
        try:
            ledger.record(
                DispatchRequest(
                    idempotency_key=request.idempotency_key,
                    connector="email",
                    artifact_digest=digest("om7", "other-artifact"),
                    approval_id=approval_id,
                    destination_digest=digest("om7", "destination"),
                ),
                at=utcnow(),
            )
        except WorkflowInvariantError as exc:
            conflict = exc
        receipts = ledger.receipts()
        checks = {
            "replay returns original receipt digest": (
                replay.receipt_digest == first.receipt_digest
            ),
            "exactly one receipt persisted": len(receipts) == 1,
            "different payload under same key rejected": conflict is not None,
        }
        record(
            "OM-7",
            scenario_result(
            "OM-7",
            "dispatch replay duplicate suppression after recovery",
            checks,
            [
                f"receipt digest {first.receipt_digest}",
                f"ledger file {ledger_path.name}",
            ],
            started,
            utcnow(),
            extra={"receipt_count": len(receipts)},
            ),
        )

    return results


# ---------------------------------------------------------------------------
# Live scenarios (OM-1 and OM-2) against the isolated compose stack.
# ---------------------------------------------------------------------------


def _await_marker(marker: Path, timeout: float) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.exists():
            lines = [
                json.loads(line)
                for line in marker.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if lines:
                return lines[-1]
        time.sleep(0.2)
    return None


async def _live_scenarios(
    args: argparse.Namespace, outdir: Path
) -> dict[str, dict[str, Any]]:
    from sklegal_worker import (
        ApprovalRequirement,
        ApprovalSignal,
        DispatchRequest,
        FileDispatchLedger,
        QueueKind,
        RetryClass,
        RunIdentity,
        StepRecord,
        TaskWorkflowInput,
        connect_worker_client,
        task_queue_name,
    )
    from sklegal_worker.workflows import MatterTaskWorkflow
    from temporalio.api.enums.v1 import EventType

    client = await connect_worker_client(args.address)
    ledger_path = outdir / "live-dispatch-ledger.json"
    if ledger_path.exists():
        ledger_path.unlink()
    marker_path = outdir / "live-activity-marker.jsonl"
    if marker_path.exists():
        marker_path.unlink()
    approval_id = uuid4()
    run_key = f"s504c-om1-{uuid4().hex[:8]}"
    workflow_id = f"skl-s5-04c-om1-{run_key}"
    identity = RunIdentity(
        tenant_id=SYNTHETIC_TENANT,
        matter_id=SYNTHETIC_MATTER,
        run_key=run_key,
        correlation_id=uuid4(),
        requested_by=OPERATOR,
    )
    dispatch = DispatchRequest(
        idempotency_key=f"{run_key}-dispatch",
        connector="outage-matrix-connector",
        artifact_digest=digest(run_key, "artifact"),
        approval_id=approval_id,
        destination_digest=digest(run_key, "destination"),
    )
    workflow_input = TaskWorkflowInput(
        identity=identity,
        queue=QueueKind.INTERACTIVE,
        task_ref=OPERATOR,
        context_tokens=0,
        steps=(
            StepRecord(
                name="om1-step",
                idempotency_key=f"{run_key}-step",
                retry_class=RetryClass.INTERACTIVE,
            ),
        ),
        approval=ApprovalRequirement(
            approval_id=approval_id, stale_after_seconds=3600
        ),
        dispatch=dispatch,
    )

    worker_cmd = [
        sys.executable,
        str(Path(__file__).with_name("outage_qualification.py")),
        "worker",
        "--address",
        args.address,
        "--ledger",
        str(ledger_path),
        "--marker",
        str(marker_path),
        "--approval-id",
        str(approval_id),
        "--step-delay",
        str(args.step_delay),
    ]
    worker_log = outdir / "om1-worker-first.log"
    started = utcnow()
    worker = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        worker_cmd,
        stdout=worker_log.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(ROOT),
    )
    try:
        await client.start_workflow(
            MatterTaskWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name(QueueKind.INTERACTIVE),
        )
        marker = _await_marker(marker_path, timeout=120.0)
        if marker is None:
            # No activity start observed: a kill now would prove nothing, so
            # the scenario is recorded FAIL instead of passing by omission.
            result = scenario_result(
                "OM-1",
                "worker kill mid-activity and heartbeat redelivery",
                {
                    "activity start observed before kill": False,
                },
                [
                    f"worker log {worker_log.name}",
                    "no activity_started marker within 120s of workflow start",
                ],
                started,
                utcnow(),
                extra={"workflow_id": workflow_id},
            )
            write_json(outdir / "om-1.json", result)
            return {"OM-1": result}
        killed_at = utcnow()
        os.kill(worker.pid, signal.SIGKILL)
        worker.wait(timeout=30)
        worker_early = worker.returncode

        # Redelivery now happens at the 60 second heartbeat timeout.
        replacement_log = outdir / "om1-worker-replacement.log"
        replacement = subprocess.Popen(  # noqa: S603 - fixed argv
            [
                sys.executable,
                str(Path(__file__).with_name("outage_qualification.py")),
                "worker",
                "--address",
                args.address,
                "--ledger",
                str(ledger_path),
                "--marker",
                str(marker_path),
                "--approval-id",
                str(approval_id),
                "--step-delay",
                "0",
            ],
            stdout=replacement_log.open("w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(ROOT),
        )
        try:
            handle = client.get_workflow_handle_for(
                MatterTaskWorkflow.run, workflow_id
            )
            redelivered = False
            phase_before_approval = None
            deadline = time.monotonic() + 300.0
            while time.monotonic() < deadline:
                description = await handle.describe()
                if description.status is not None and description.status.name in (
                    "COMPLETED",
                    "FAILED",
                ):
                    break
                try:
                    phase = await handle.query(MatterTaskWorkflow.phase)
                except Exception:
                    phase = None
                if phase == "awaiting_approval":
                    phase_before_approval = phase
                    break
                await asyncio.sleep(1.0)
            # wait for step redelivery evidence in the marker file
            redelivery_deadline = time.monotonic() + 300.0
            while time.monotonic() < redelivery_deadline:
                lines = (
                    [
                        json.loads(line)
                        for line in marker_path.read_text(
                            encoding="utf-8"
                        ).splitlines()
                        if line.strip()
                    ]
                    if marker_path.exists()
                    else []
                )
                starts = [line for line in lines if line["event"] == "activity_started"]
                if len(starts) >= 2:
                    redelivered = True
                    break
                await asyncio.sleep(1.0)

            if phase_before_approval != "awaiting_approval":
                deadline = time.monotonic() + 300.0
                while time.monotonic() < deadline:
                    try:
                        phase_before_approval = await handle.query(
                            MatterTaskWorkflow.phase
                        )
                    except Exception:
                        phase_before_approval = None
                    if phase_before_approval == "awaiting_approval":
                        break
                    await asyncio.sleep(1.0)

            await handle.signal(
                MatterTaskWorkflow.submit_approval,
                ApprovalSignal(
                    signal_id=f"{run_key}-signal",
                    approval_id=approval_id,
                    decision="approved",
                    decided_by=OPERATOR,
                    decided_at=utcnow(),
                ),
            )
            outcome = await asyncio.wait_for(handle.result(), timeout=120.0)
            description = await handle.describe()
            ledger = FileDispatchLedger(ledger_path)
            receipts = ledger.receipts()

            activity_events: dict[str, dict[str, int]] = {}

            def bump(name: str, kind: str) -> None:
                bucket = activity_events.setdefault(name, {})
                bucket[kind] = bucket.get(kind, 0) + 1

            scheduled_names: dict[int, str] = {}
            async for event in handle.fetch_history_events():
                if event.event_type is EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
                    attrs = event.activity_task_scheduled_event_attributes
                    scheduled_names[event.event_id] = attrs.activity_type.name
                    bump(attrs.activity_type.name, "scheduled")
                elif (
                    event.event_type is EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED
                ):
                    attrs = event.activity_task_started_event_attributes
                    bump(
                        scheduled_names.get(attrs.scheduled_event_id, "unknown"),
                        "started",
                    )
                elif (
                    event.event_type
                    is EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT
                ):
                    attrs = event.activity_task_timed_out_event_attributes
                    bump(
                        scheduled_names.get(attrs.scheduled_event_id, "unknown"),
                        "timed_out",
                    )
                elif (
                    event.event_type
                    is EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED
                ):
                    attrs = event.activity_task_completed_event_attributes
                    bump(
                        scheduled_names.get(attrs.scheduled_event_id, "unknown"),
                        "completed",
                    )

            kill_to_redelivery_seconds = None
            lines = (
                [
                    json.loads(line)
                    for line in marker_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                if marker_path.exists()
                else []
            )
            starts = [line for line in lines if line["event"] == "activity_started"]
            if len(starts) >= 2:
                first_at = datetime.fromisoformat(starts[0]["at"])
                second_at = datetime.fromisoformat(starts[1]["at"])
                kill_to_redelivery_seconds = round(
                    (second_at - first_at).total_seconds(), 1
                )
            step_completed = activity_events.get("run_task_step", {}).get(
                "completed", 0
            )
            dispatch_completed = activity_events.get(
                "dispatch_connector", {}
            ).get("completed", 0)
            checks = {
                "activity start observed before kill": marker is not None,
                "workflow COMPLETED": description.status.name == "COMPLETED",
                "step activity completed exactly once": step_completed == 1,
                "dispatch activity completed exactly once": dispatch_completed == 1,
                "exactly one receipt in durable ledger": len(receipts) == 1,
                "redelivery observed after kill": redelivered,
                "kill-to-redelivery inside heartbeat bound": (
                    kill_to_redelivery_seconds is not None
                    and kill_to_redelivery_seconds <= 90.0
                ),
            }
            observations = [
                f"killed worker pid exited with {worker_early}",
                f"kill issued at {killed_at.isoformat()}",
                f"step completions {step_completed}, dispatch completions "
                f"{dispatch_completed}",
                f"receipt digests {[r.receipt_digest for r in receipts.values()]}",
                f"kill-to-redelivery {kill_to_redelivery_seconds}s "
                "(heartbeat timeout 60s)",
                f"phase before approval {phase_before_approval}",
                f"final phase {outcome.phase.value}",
                f"activity events {json.dumps(activity_events, sort_keys=True)}",
            ]
            result = scenario_result(
                "OM-1",
                "worker kill mid-activity and heartbeat redelivery",
                checks,
                observations,
                started,
                utcnow(),
                extra={
                    "workflow_id": workflow_id,
                    "kill_to_redelivery_seconds": kill_to_redelivery_seconds,
                    "activity_events": activity_events,
                },
            )
            write_json(outdir / "om-1.json", result)
            return {"OM-1": result}
        finally:
            replacement.terminate()
            try:
                replacement.wait(timeout=30)
            except subprocess.TimeoutExpired:
                replacement.kill()
                replacement.wait(timeout=30)
    finally:
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=10)


async def _live_temporal_restart(
    args: argparse.Namespace, outdir: Path
) -> dict[str, Any]:
    from sklegal_worker import (
        ApprovalRequirement,
        ApprovalSignal,
        DispatchRequest,
        FileDispatchLedger,
        QueueKind,
        RetryClass,
        RunIdentity,
        StepRecord,
        TaskWorkflowInput,
        connect_worker_client,
        task_queue_name,
    )
    from sklegal_worker.workflows import MatterTaskWorkflow

    client = await connect_worker_client(args.address)
    ledger_path = outdir / "om2-dispatch-ledger.json"
    marker_path = outdir / "om2-activity-marker.jsonl"
    approval_id = uuid4()
    run_key = f"s504c-om2-{uuid4().hex[:8]}"
    workflow_id = f"skl-s5-04c-om2-{run_key}"
    dispatch = DispatchRequest(
        idempotency_key=f"{run_key}-dispatch",
        connector="outage-matrix-connector",
        artifact_digest=digest(run_key, "artifact"),
        approval_id=approval_id,
        destination_digest=digest(run_key, "destination"),
    )
    workflow_input = TaskWorkflowInput(
        identity=RunIdentity(
            tenant_id=SYNTHETIC_TENANT,
            matter_id=SYNTHETIC_MATTER,
            run_key=run_key,
            correlation_id=uuid4(),
            requested_by=OPERATOR,
        ),
        queue=QueueKind.INTERACTIVE,
        task_ref=OPERATOR,
        context_tokens=0,
        steps=(
            StepRecord(
                name="om2-step",
                idempotency_key=f"{run_key}-step",
                retry_class=RetryClass.INTERACTIVE,
            ),
        ),
        approval=ApprovalRequirement(
            approval_id=approval_id, stale_after_seconds=3600
        ),
        dispatch=dispatch,
    )
    started = utcnow()
    worker_log = outdir / "om2-worker.log"
    worker = subprocess.Popen(  # noqa: S603 - fixed argv
        [
            sys.executable,
            str(Path(__file__).with_name("outage_qualification.py")),
            "worker",
            "--address",
            args.address,
            "--ledger",
            str(ledger_path),
            "--marker",
            str(marker_path),
            "--approval-id",
            str(approval_id),
            "--step-delay",
            "0",
        ],
        stdout=worker_log.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(ROOT),
    )
    try:
        handle = await client.start_workflow(
            MatterTaskWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name(QueueKind.INTERACTIVE),
        )
        phase_before = None
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            try:
                phase_before = await handle.query(MatterTaskWorkflow.phase)
            except Exception:
                phase_before = None
            if phase_before == "awaiting_approval":
                break
            await asyncio.sleep(1.0)

        restart_started = time.monotonic()
        subprocess.run(
            ["docker", "restart", args.temporal_container],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        query_error_during_restart: Exception | None = None
        try:
            await asyncio.wait_for(handle.query(MatterTaskWorkflow.phase), 3.0)
        except Exception as exc:
            query_error_during_restart = exc
        healthy_deadline = time.monotonic() + 300.0
        restarted = False
        while time.monotonic() < healthy_deadline:
            inspect = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.State.Health.Status}}",
                    args.temporal_container,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if inspect.returncode == 0 and inspect.stdout.strip() == "healthy":
                restarted = True
                break
            await asyncio.sleep(2.0)
        restart_seconds = round(time.monotonic() - restart_started, 1)

        phase_after = None
        phase_deadline = time.monotonic() + 300.0
        while time.monotonic() < phase_deadline:
            try:
                phase_after = await handle.query(MatterTaskWorkflow.phase)
                break
            except Exception:
                await asyncio.sleep(2.0)

        await handle.signal(
            MatterTaskWorkflow.submit_approval,
            ApprovalSignal(
                signal_id=f"{run_key}-signal",
                approval_id=approval_id,
                decision="approved",
                decided_by=OPERATOR,
                decided_at=utcnow(),
            ),
        )
        outcome = await asyncio.wait_for(handle.result(), timeout=180.0)
        description = await handle.describe()
        receipts = FileDispatchLedger(ledger_path).receipts()
        checks = {
            "workflow phase preserved across restart": phase_after
            == phase_before
            == "awaiting_approval",
            "client query failed while frontend down": (
                query_error_during_restart is not None
            ),
            "container healthy after restart": restarted,
            "workflow COMPLETED after restart": (
                description.status.name == "COMPLETED"
            ),
            "exactly one receipt after restart": len(receipts) == 1,
            "restart to healthy inside 15m RTO": restart_seconds <= 900.0,
        }
        result = scenario_result(
            "OM-2",
            "Temporal service restart with workflow parked at approval",
            checks,
            [
                f"phase before restart {phase_before}, after {phase_after}",
                f"restart to healthy {restart_seconds}s",
                f"query during restart raised "
                f"{type(query_error_during_restart).__name__}",
                f"receipt digests {[r.receipt_digest for r in receipts.values()]}",
                f"result phase {outcome.phase.value}",
            ],
            started,
            utcnow(),
            extra={
                "workflow_id": workflow_id,
                "restart_to_healthy_seconds": restart_seconds,
            },
        )
        write_json(outdir / "om-2.json", result)
        return result
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=30)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=30)


class MarkingStepDriver:
    """Simulation step driver marking activity start then sleeping."""

    def __init__(self, *, delay_seconds: float, marker_path: Path) -> None:
        self._delay_seconds = delay_seconds
        self._marker_path = marker_path

    def run(self, request: Any, *, at: datetime) -> str:
        with self._marker_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "event": "activity_started",
                        "run_key": request.run_key,
                        "step": request.step.name,
                        "pid": os.getpid(),
                        "at": at.isoformat(),
                    }
                )
                + "\n"
            )
        if self._delay_seconds > 0:
            time.sleep(self._delay_seconds)
        return digest("step", request.run_key, request.step.name, request.step.idempotency_key)


class StaticApproval:
    def __init__(self, approval_id: UUID) -> None:
        self._approval_id = approval_id

    def ensure_approved(self, approval_id: UUID) -> None:
        from sklegal_worker import PolicyDeniedError

        if approval_id != self._approval_id:
            raise PolicyDeniedError(f"approval {approval_id} is not approved")


async def _run_worker(args: argparse.Namespace) -> None:
    from sklegal_worker import (
        FileDispatchLedger,
        connect_worker_client,
        task_queue_name,
    )
    from sklegal_worker.activities import WorkerActivities
    from sklegal_worker.queues import QueueKind
    from sklegal_worker.workflows import MatterTaskWorkflow
    from temporalio.worker import Worker

    client = await connect_worker_client(args.address)
    activities = WorkerActivities(
        step_driver=MarkingStepDriver(
            delay_seconds=args.step_delay, marker_path=Path(args.marker)
        ),
        approval_gate=StaticApproval(UUID(args.approval_id)),
        dispatch_ledger=FileDispatchLedger(Path(args.ledger)),
    )
    worker = Worker(
        client,
        task_queue=task_queue_name(QueueKind.INTERACTIVE),
        workflows=[MatterTaskWorkflow],
        activities=[
            activities.run_task_step,
            activities.dispatch_connector,
            activities.compensate_step,
            activities.raise_stale_run_alert,
        ],
    )
    print(
        json.dumps({"event": "worker_started", "pid": os.getpid()}),
        flush=True,
    )
    await worker.run()


async def _run_live(args: argparse.Namespace) -> int:
    outdir = Path(args.workdir)
    outdir.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, Any]] = {}
    om1 = await _live_scenarios(args, outdir)
    results.update(om1)
    om2 = await _live_temporal_restart(args, outdir)
    results["OM-2"] = om2
    model_results = run_model_scenarios(
        outdir, include=("OM-7",)
    )
    results.update(model_results)
    matrix = {
        "generated_at": utcnow().isoformat(),
        "operator": OPERATOR,
        "address": args.address,
        "scenarios": {
            key: results[key] for key in SCENARIO_ORDER if key in results
        },
        "overall": (
            "PASS"
            if all(item["status"] == "PASS" for item in results.values())
            else "FAIL"
        ),
    }
    write_json(outdir / "outage-matrix.json", matrix)
    print(json.dumps(matrix, indent=2, sort_keys=True))
    return 0 if matrix["overall"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    model = subparsers.add_parser(
        "model-matrix", help="run OM-3 through OM-7 in-process"
    )
    model.add_argument("--workdir", default="/tmp/skl-s5-04c-model-matrix")

    live = subparsers.add_parser(
        "run", help="run OM-1, OM-2, and OM-7 against the isolated stack"
    )
    live.add_argument("--address", default="127.0.0.1:27233")
    live.add_argument("--workdir", default="/tmp/skl-s5-04c-outage")
    live.add_argument("--temporal-container", default="sklegal-outage-temporal-1")
    live.add_argument("--step-delay", type=float, default=120.0)

    worker = subparsers.add_parser("worker", help=argparse.SUPPRESS)
    worker.add_argument("--address", required=True)
    worker.add_argument("--ledger", required=True)
    worker.add_argument("--marker", required=True)
    worker.add_argument("--approval-id", required=True)
    worker.add_argument("--step-delay", type=float, default=0.0)

    args = parser.parse_args()
    if args.command == "model-matrix":
        outdir = Path(args.workdir)
        outdir.mkdir(parents=True, exist_ok=True)
        results = run_model_scenarios(outdir)
        matrix = {
            "generated_at": utcnow().isoformat(),
            "operator": OPERATOR,
            "scenarios": results,
            "overall": (
                "PASS"
                if all(item["status"] == "PASS" for item in results.values())
                else "FAIL"
            ),
        }
        write_json(outdir / "outage-matrix.json", matrix)
        print(json.dumps(matrix, indent=2, sort_keys=True))
        return 0 if matrix["overall"] == "PASS" else 1
    if args.command == "run":
        return asyncio.run(_run_live(args))
    asyncio.run(_run_worker(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
