#!/usr/bin/env python3
"""SKL-S5-04B load and saturation driver: API, signing path, Temporal workers.

Three saturation scenarios against the pilot load envelope:

- ``api``: serve the SKL-S4-02 workspace read router over uvicorn on
  loopback with the real CapAuth protected-route boundary (stub signer) and
  a pilot-scale synthetic workspace, then drive concurrent HTTP load where
  every request presents a fresh one-use credential.
- ``signing``: measure the CapAuth signing-path ceiling (issue plus
  authorize, fresh one-use credential per operation) under threaded load,
  with either the real OpenPGP signer (throwaway ed25519 key in a temporary
  GNUPGHOME) or the deterministic stub signer.
- ``temporal``: run an in-process interactive-queue worker against the
  pinned development Temporal service and execute a burst of synthetic
  MatterTaskWorkflows with an auto-approving gate, measuring completion
  latency and throughput.

All identities, matters, credentials, and key material are synthetic. The
driver touches no live keyring, tenant, matter, or HammerTime path. The
Temporal scenario requires the disposable development stack
(./scripts/dev_dependencies.sh up); it never connects to any other service.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]

TENANT_ID = UUID("10000000-0000-4000-8000-0000000000b5")
CLIENT_ID = UUID("10000000-0000-4000-8000-0000000000c5")
MATTER_ID = UUID("20000000-0000-4000-8000-0000000000b5")
APPROVAL_ID = UUID("50000000-0000-4000-8000-0000000000b5")
OPERATOR = "skl-s5-04b-load"

# Pilot-scale read-model shape, derived from the SKL-S5-01A pilot dry run
# (112 source files, 54 fact assertions, 2 tension groups, 3 work-product
# versions) and the stated growth projection (25 matters per tenant).
PILOT_FACT_COUNT = 54
PILOT_SOURCE_FILE_COUNT = 112
PILOT_TENSION_COUNT = 2
PILOT_VERSION_COUNT = 3
PILOT_COMMUNICATION_COUNT = 10
GROWTH_MATTERS_PER_TENANT = 25


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Linear-interpolation percentile over an ascending list."""
    if not sorted_values:
        raise ValueError("percentile requires at least one value")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = fraction * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def latency_summary(latencies_ms: list[float]) -> dict[str, float]:
    """Rounded latency summary over millisecond samples."""
    if not latencies_ms:
        raise ValueError("latency summary requires at least one sample")
    ordered = sorted(latencies_ms)
    return {
        key: round(value, 3)
        for key, value in {
            "mean": statistics.fmean(ordered),
            "p50": _percentile(ordered, 0.50),
            "p95": _percentile(ordered, 0.95),
            "p99": _percentile(ordered, 0.99),
            "max": ordered[-1],
        }.items()
    }


def parse_levels(raw: str) -> list[int]:
    """Parse and validate a comma-separated concurrency-level list."""
    levels = [int(entry) for entry in raw.split(",") if entry.strip()]
    if not levels or any(level < 1 for level in levels):
        raise ValueError("levels must be a comma-separated list of integers >= 1")
    return levels


def _meta() -> dict[str, Any]:
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "gpg_available": shutil.which("gpg") is not None,
    }


@dataclass
class LoadLevelResult:
    """Aggregated outcome of one concurrency level."""

    workers: int
    operations: int
    wall_seconds: float
    ops_per_second: float
    latency_ms: dict[str, float]
    errors: int
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "workers": self.workers,
            "operations": self.operations,
            "wall_seconds": round(self.wall_seconds, 4),
            "ops_per_second": round(self.ops_per_second, 2),
            "latency_ms": self.latency_ms,
            "errors": self.errors,
            **self.extra,
        }


def _run_threaded_load(
    levels: list[int],
    operations_per_worker: int,
    operation_factory: Any,
) -> list[LoadLevelResult]:
    """Drive a thread-per-worker closed load at each concurrency level.

    operation_factory(worker_index) returns a callable that performs one
    operation and returns its latency in milliseconds, raising on failure.
    """
    results: list[LoadLevelResult] = []
    for level in levels:
        barrier = threading.Barrier(level)
        samples: list[float] = []
        samples_lock = threading.Lock()
        errors: list[str] = []

        def worker(worker_index: int) -> None:
            operation = operation_factory(worker_index)
            local: list[float] = []
            barrier.wait()
            for _ in range(operations_per_worker):
                try:
                    local.append(operation())
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {exc}")
            with samples_lock:
                samples.extend(local)

        threads = [
            threading.Thread(target=worker, args=(index,), daemon=True)
            for index in range(level)
        ]
        wall_start = time.perf_counter()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        wall_seconds = time.perf_counter() - wall_start
        if not samples:
            raise RuntimeError(
                f"level {level} produced no successful operations: {errors[:3]}"
            )
        results.append(
            LoadLevelResult(
                workers=level,
                operations=len(samples),
                wall_seconds=wall_seconds,
                ops_per_second=len(samples) / wall_seconds if wall_seconds else 0.0,
                latency_ms=latency_summary(samples),
                errors=len(errors),
            )
        )
    return results


# ---------------------------------------------------------------------------
# API scenario
# ---------------------------------------------------------------------------


def build_load_app(principal: Any) -> Any:
    """Compose the workspace read API with a pilot-scale synthetic store.

    The store mirrors the Liberty Auto pilot shape (54 facts, 112 source
    files, 2 tension groups, 3 work-product versions) at the growth
    projection of 25 matters for the tenant. The CapAuth boundary is the
    real 12-gate fail-closed authorizer with the deterministic stub signer.
    The given principal is both the resolved request identity and the only
    registered active principal, so client-issued credentials match.
    """
    from fastapi import FastAPI, Request
    from sklegal_api.workspace import (
        ClientSummaryRead,
        InMemoryWorkspaceReadStore,
        MatterDetailRead,
        build_workspace_router,
    )
    from sklegal_capauth import (
        BoundaryScope,
        CapabilityAuthorizer,
        InMemoryAuditSink,
        InMemoryPrincipalPolicyBackend,
        InMemoryReplayBackend,
        InMemoryRevocationBackend,
        PrincipalContext,
        SignatureVerificationCache,
        StaticTrustedIssuerBackend,
    )

    stamp = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    principals = InMemoryPrincipalPolicyBackend()
    principals.set(principal, active=True)
    authorizer = CapabilityAuthorizer(
        trusted_issuers=StaticTrustedIssuerBackend({_stub_issuer_fingerprint()}),
        principals=principals,
        revocations=InMemoryRevocationBackend(),
        replay=InMemoryReplayBackend(),
        audit=InMemoryAuditSink(),
        signature_cache=SignatureVerificationCache(),
    )

    store = InMemoryWorkspaceReadStore()
    store.add_client(
        TENANT_ID,
        ClientSummaryRead(
            id=CLIENT_ID,
            tenant_id=TENANT_ID,
            display_name="Synthetic Pilot Client",
            matter_count=GROWTH_MATTERS_PER_TENANT,
        ),
    )
    for index in range(GROWTH_MATTERS_PER_TENANT):
        matter_id = UUID(int=int(MATTER_ID) + index)
        store.add_matter(
            TENANT_ID,
            MatterDetailRead(
                id=matter_id,
                tenant_id=TENANT_ID,
                client_id=CLIENT_ID,
                client_display_name="Synthetic Pilot Client",
                title=f"Synthetic pilot matter {index}",
                status="open",
                summary="Synthetic pilot-scale matter for load qualification.",
                opened_on="2026-08-01",
                legacy_aliases=(f"synthetic-legacy-{index}",),
            ),
        )
        store.set_matter_members(
            TENANT_ID, matter_id, frozenset({principal.principal_id})
        )
    store.add_workspace(TENANT_ID, _pilot_workspace_view(stamp))

    def principal_resolver(_: Request) -> PrincipalContext:
        return principal

    def scope_resolver(request: Request) -> BoundaryScope:
        matter = request.path_params.get("matter_id")
        return BoundaryScope(
            tenant_id=TENANT_ID,
            matter_id=UUID(str(matter)) if matter is not None else None,
            resource_id=str(matter) if matter is not None else None,
        )

    app = FastAPI()
    app.include_router(
        build_workspace_router(
            store=store,
            authorizer=authorizer,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        )
    )
    return app


def _pilot_workspace_view(stamp: datetime) -> Any:
    """One pilot-scale workspace aggregate (Liberty Auto shape, synthetic)."""
    from sklegal_api.workspace import (
        CommunicationRead,
        FactAssertionRead,
        MatterWorkspaceRead,
        SourceFileRead,
        TensionGroupRead,
        TimelineEventRead,
        VersionLineageRead,
        WorkspaceMatterRead,
        WorkspaceProvenanceRead,
    )

    return MatterWorkspaceRead(
        matter=WorkspaceMatterRead(
            matter_id=MATTER_ID,
            client_id=CLIENT_ID,
            client_display_name="Synthetic Pilot Client",
            title="Synthetic pilot matter 0",
            summary="Synthetic pilot-scale matter for load qualification.",
            status="open",
            opened_at=stamp,
            legacy_aliases=("synthetic-legacy-0",),
        ),
        timeline=(
            TimelineEventRead(
                event_id=uuid4(),
                event_type="transaction_review",
                description="Synthetic transaction review event.",
                occurred_at=stamp,
                observed_at=stamp,
                status="proposed",
                source_path="synthetic/INCIDENT.md",
                legacy_aliases=("synthetic-legacy-event-0",),
            ),
        ),
        facts=tuple(
            FactAssertionRead(
                fact_assertion_id=uuid4(),
                predicate=f"synthetic-predicate-{index % 12}",
                asserted_value=f"synthetic value {index}",
                value_type="string",
                review_status="source_asserted",
                source_path=f"synthetic/source-{index % 20}.md",
                source_locator=f"#/facts/{index}",
                source_missing=False,
                tension_group_key=None,
            )
            for index in range(PILOT_FACT_COUNT)
        ),
        tensions=tuple(
            TensionGroupRead(
                tension_key=f"synthetic-tension-{index}",
                status="unresolved",
                assertion_ids=(uuid4(), uuid4()),
                review_required=True,
            )
            for index in range(PILOT_TENSION_COUNT)
        ),
        communications=tuple(
            CommunicationRead(
                communication_id=uuid4(),
                channel="correspondence",
                summary=f"synthetic-correspondence-{index}.md",
                occurred_at=stamp,
                status="recorded",
                source_path=f"synthetic/correspondence/{index}.md",
            )
            for index in range(PILOT_COMMUNICATION_COUNT)
        ),
        version_lineage=tuple(
            VersionLineageRead(
                packet_version=index + 2,
                source_path=f"synthetic/packet-v{index + 2}-facts.json",
                source_sha256=f"{index:064x}",
                historical=index < PILOT_VERSION_COUNT - 1,
                current_review_baseline=index == PILOT_VERSION_COUNT - 1,
            )
            for index in range(PILOT_VERSION_COUNT)
        ),
        provenance=WorkspaceProvenanceRead(
            source_snapshot="synthetic-snapshot-2026-08-22",
            current_source_snapshot="synthetic-snapshot-2026-08-22",
            adapter_version="0.1.0",
            observed_at=stamp,
            stale=False,
            source_files=tuple(
                SourceFileRead(
                    relative_path=f"synthetic/source-{index}.md",
                    content_sha256=f"{index:064x}",
                    observed_at=stamp,
                    byte_count=1024 + index,
                )
                for index in range(PILOT_SOURCE_FILE_COUNT)
            ),
        ),
    )


def _stub_issuer_fingerprint() -> str:
    from capauth.testing import STUB_ISSUER_FPR  # type: ignore[import-untyped]

    return STUB_ISSUER_FPR


class _StubSigner:
    @property
    def issuer_fingerprint(self) -> str:
        return _stub_issuer_fingerprint()

    def sign(self, payload_bytes: bytes) -> str:
        from capauth.testing import stub_signature_for  # type: ignore[import-untyped]

        return stub_signature_for(payload_bytes)


def _api_grant() -> Any:
    from sklegal_capauth import (
        CAPABILITY_RULES,
        Audience,
        Capability,
        CapabilityGrant,
    )

    rule = CAPABILITY_RULES[Capability.MATTER_READ]
    return CapabilityGrant(
        audience=Audience.API,
        target="api:workspace.matters.workspace",
        capability=Capability.MATTER_READ,
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        resource_type=rule.resource_type,
        resource_id=str(MATTER_ID),
        operation=rule.operation,
        purpose=next(iter(rule.purposes)),
    )


def _api_principal() -> Any:
    from sklegal_capauth import PrincipalContext, PrincipalType

    return PrincipalContext(
        principal_id=uuid4(),
        principal_type=PrincipalType.HUMAN,
        subject="synthetic:human:load-client",
        tenant_id=TENANT_ID,
    )


def run_api_scenario(arguments: argparse.Namespace) -> dict[str, Any]:
    """Serve the workspace API on loopback and drive concurrent HTTP load."""
    import uvicorn
    from capauth.testing import signing_stub  # type: ignore[import-untyped]
    from sklegal_capauth import CapabilityIssuer

    levels = parse_levels(arguments.workers)
    if arguments.requests_per_worker < 1:
        raise ValueError("requests-per-worker must be >= 1")

    with signing_stub():
        issuer = CapabilityIssuer(_StubSigner())
        client_principal = _api_principal()
        grant = _api_grant()
        app = build_load_app(client_principal)

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
        )
        server = uvicorn.Server(config)
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        try:
            _wait_for_server(port, arguments.startup_timeout_seconds)
            url = f"http://127.0.0.1:{port}/v1/matters/{MATTER_ID}/workspace"
            issue_latencies: list[float] = []
            issue_lock = threading.Lock()

            def make_operation(_worker_index: int) -> Any:
                import httpx

                client = httpx.Client(timeout=30.0)

                def operation() -> float:
                    issue_start = time.perf_counter()
                    presented = issuer.issue_root(
                        principal=client_principal, grant=grant
                    )
                    raw = presented.credentials_for_verification()[-1]
                    issue_ms = (time.perf_counter() - issue_start) * 1000.0
                    with issue_lock:
                        issue_latencies.append(issue_ms)
                    start = time.perf_counter()
                    response = client.get(
                        url, headers={"Authorization": f"Bearer {raw}"}
                    )
                    elapsed_ms = (time.perf_counter() - start) * 1000.0
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"unexpected status {response.status_code}: "
                            f"{response.text[:200]}"
                        )
                    return elapsed_ms

                return operation

            levels_results = _run_threaded_load(
                levels, arguments.requests_per_worker, make_operation
            )
        finally:
            server.should_exit = True
            server_thread.join(timeout=15)

    process = _process_snapshot()
    return {
        "scenario": "api-workspace-read",
        "endpoint": "/v1/matters/{matter_id}/workspace",
        "auth": "fresh one-use stub-signed credential per request",
        "store": "in-memory pilot-scale workspace (54 facts, 112 sources, 25 matters)",
        "levels": [result.as_dict() for result in levels_results],
        "token_issue_ms": latency_summary(issue_latencies),
        "server_process": process,
    }


def _wait_for_server(port: int, timeout_seconds: float) -> None:
    import httpx

    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/v1/clients"
    while time.monotonic() < deadline:
        try:
            httpx.get(url, timeout=1.0)
            return
        except httpx.TransportError:
            time.sleep(0.05)
    raise RuntimeError("uvicorn test server did not start in time")


def _process_snapshot() -> dict[str, Any]:
    """Own-process CPU and RSS snapshot for ceiling comparison."""
    with open("/proc/self/stat", encoding="utf-8") as handle:
        fields = handle.read().split()
    ticks = os.sysconf("SC_CLK_TCK")
    cpu_seconds = (int(fields[13]) + int(fields[14])) / ticks
    with open("/proc/self/status", encoding="utf-8") as handle:
        status = handle.read()
    rss_kib = next(
        int(line.split()[1]) for line in status.splitlines() if line.startswith("VmRSS")
    )
    return {"cpu_seconds_total": round(cpu_seconds, 2), "rss_mib": round(rss_kib / 1024, 1)}


# ---------------------------------------------------------------------------
# Signing-path scenario
# ---------------------------------------------------------------------------


def _generate_synthetic_key(keyring: Path) -> str:
    environment = {**os.environ, "GNUPGHOME": str(keyring)}
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--passphrase",
            "",
            "--quick-generate-key",
            "SKLegal Synthetic Load <synthetic-load@example.invalid>",
            "ed25519",
            "sign",
            "1d",
        ],
        env=environment,
        check=True,
        capture_output=True,
    )
    listing = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--list-secret-keys"],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return next(
        line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")
    )


def _write_issuer_policy(root: Path, fingerprint: str) -> Path:
    from sklegal_capauth import (
        Audience,
        Capability,
        PrincipalType,
        VERIFIER_POLICY_VERSION,
    )

    path = root / "trusted-issuers.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "sklegal-trusted-issuers/v1",
                "policy_version": VERIFIER_POLICY_VERSION,
                "issuers": [
                    {
                        "fingerprint": fingerprint,
                        "capabilities": [Capability.MATTER_READ.value],
                        "audiences": [Audience.API.value],
                        "principal_types": [PrincipalType.HUMAN.value],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def run_signing_scenario(arguments: argparse.Namespace) -> dict[str, Any]:
    """Measure the signing-path ceiling under threaded load."""
    from sklegal_capauth import (
        AuthorizationRequest,
        CapabilityAuthorizer,
        CapabilityIssuer,
        CapAuthManifestSigner,
        FileTrustedIssuerBackend,
        InMemoryAuditSink,
        InMemoryPrincipalPolicyBackend,
        InMemoryReplayBackend,
        InMemoryRevocationBackend,
        SignatureVerificationCache,
    )

    levels = parse_levels(arguments.workers)
    if arguments.ops_per_worker < 1:
        raise ValueError("ops-per-worker must be >= 1")
    if arguments.mode == "openpgp" and shutil.which("gpg") is None:
        raise RuntimeError("gpg is required for the openpgp signing scenario")

    with tempfile.TemporaryDirectory(prefix="sklegal-s504b-sign-") as temp:
        root = Path(temp)
        principal = _api_principal()
        grant = _api_grant()
        if arguments.mode == "openpgp":
            keyring = root / "gnupg"
            keyring.mkdir(mode=0o700)
            fingerprint = _generate_synthetic_key(keyring)
            previous_home = os.environ.get("GNUPGHOME")
            os.environ["GNUPGHOME"] = str(keyring)
            signer: Any = CapAuthManifestSigner(fingerprint)
        else:
            fingerprint = _stub_issuer_fingerprint()
            previous_home = None
            signer = _StubSigner()
        try:
            policy_path = _write_issuer_policy(root, fingerprint)
            principals = InMemoryPrincipalPolicyBackend()
            principals.set(principal, active=True)
            authorizer = CapabilityAuthorizer(
                trusted_issuers=FileTrustedIssuerBackend(policy_path),
                principals=principals,
                revocations=InMemoryRevocationBackend(),
                replay=InMemoryReplayBackend(),
                audit=InMemoryAuditSink(),
                signature_cache=SignatureVerificationCache(),
            )
            issuer = CapabilityIssuer(signer)
            issue_samples: list[float] = []
            authorize_samples: list[float] = []
            share_lock = threading.Lock()

            def make_operation(_worker_index: int) -> Any:
                def operation() -> float:
                    start = time.perf_counter()
                    presented = issuer.issue_root(principal=principal, grant=grant)
                    issued_at = time.perf_counter()
                    authorizer.authorize(
                        presented,
                        AuthorizationRequest(
                            principal=principal,
                            grant=grant,
                            correlation_id=uuid4(),
                        ),
                    )
                    stop = time.perf_counter()
                    with share_lock:
                        issue_samples.append((issued_at - start) * 1000.0)
                        authorize_samples.append((stop - issued_at) * 1000.0)
                    return (stop - start) * 1000.0

                return operation

            if arguments.mode == "openpgp":
                level_results = _run_threaded_load(
                    levels, arguments.ops_per_worker, make_operation
                )
            else:
                from capauth.testing import signing_stub  # type: ignore[import-untyped]

                with signing_stub():
                    level_results = _run_threaded_load(
                        levels, arguments.ops_per_worker, make_operation
                    )
        finally:
            if arguments.mode == "openpgp":
                if previous_home is None:
                    os.environ.pop("GNUPGHOME", None)
                else:
                    os.environ["GNUPGHOME"] = previous_home

    return {
        "scenario": "capauth-signing-path",
        "mode": arguments.mode,
        "operation": "issue fresh one-use credential plus full authorize",
        "levels": [result.as_dict() for result in level_results],
        "issue_ms": latency_summary(issue_samples),
        "authorize_ms": latency_summary(authorize_samples),
    }


# ---------------------------------------------------------------------------
# Temporal worker scenario
# ---------------------------------------------------------------------------


def build_workflow_input(run_key: str) -> Any:
    """One synthetic one-step interactive run with an approval gate."""
    from sklegal_worker.models import (
        ApprovalRequirement,
        DispatchRequest,
        QueueKind,
        RetryClass,
        RunIdentity,
        StepRecord,
        TaskWorkflowInput,
    )

    digest = f"{run_key}:artifact"
    return TaskWorkflowInput(
        identity=RunIdentity(
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            run_key=run_key,
            correlation_id=uuid4(),
            requested_by=OPERATOR,
        ),
        queue=QueueKind.INTERACTIVE,
        task_ref=OPERATOR,
        context_tokens=0,
        steps=(
            StepRecord(
                name="load-step",
                idempotency_key=f"{run_key}-step",
                retry_class=RetryClass.INTERACTIVE,
            ),
        ),
        approval=ApprovalRequirement(
            approval_id=APPROVAL_ID,
            stale_after_seconds=3600,
        ),
        dispatch=DispatchRequest(
            idempotency_key=f"{run_key}-dispatch",
            connector="load-connector",
            artifact_digest=digest,
            approval_id=APPROVAL_ID,
            destination_digest=f"{run_key}:destination",
        ),
    )


async def _temporal_run(arguments: argparse.Namespace) -> dict[str, Any]:
    import asyncio

    from sklegal_worker.activities import (
        SimulatedDispatchLedger,
        StaticApprovalGate,
        WorkerActivities,
    )
    from sklegal_worker.models import ApprovalSignal, QueueKind, TaskWorkflowResult
    from sklegal_worker.queues import task_queue_name
    from sklegal_worker.workflows import MatterTaskWorkflow
    from temporalio.client import Client
    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.worker import Worker

    client = await Client.connect(
        arguments.address,
        namespace="default",
        data_converter=pydantic_data_converter,
    )
    ledger = SimulatedDispatchLedger()
    activities = WorkerActivities(
        approval_gate=StaticApprovalGate({APPROVAL_ID: "approved"}),
        dispatch_ledger=ledger,
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

    semaphore = asyncio.Semaphore(arguments.max_concurrency)
    latencies_ms: list[float] = []
    failures: list[str] = []

    async def execute_run(index: int) -> None:
        run_key = f"skl-s5-04b-load-{index:05d}-{uuid4().hex[:8]}"
        async with semaphore:
            started = time.perf_counter()
            try:
                handle = await client.start_workflow(
                    MatterTaskWorkflow.run,
                    build_workflow_input(run_key),
                    id=run_key,
                    task_queue=task_queue_name(QueueKind.INTERACTIVE),
                )
                deadline = time.monotonic() + arguments.run_timeout_seconds
                while True:
                    phase = await handle.query(MatterTaskWorkflow.phase)
                    if phase == "awaiting_approval":
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"{run_key} never awaited approval")
                    await asyncio.sleep(0.1)
                await handle.signal(
                    MatterTaskWorkflow.submit_approval,
                    ApprovalSignal(
                        signal_id=f"{run_key}-approval",
                        approval_id=APPROVAL_ID,
                        decision="approved",
                        decided_by=OPERATOR,
                        decided_at=datetime.now(UTC),
                    ),
                )
                result: TaskWorkflowResult = await handle.result()
                if result.dispatch_receipt is None:
                    raise RuntimeError(f"{run_key} completed without a dispatch receipt")
                latencies_ms.append((time.perf_counter() - started) * 1000.0)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{run_key}: {type(exc).__name__}: {exc}")

    wall_start = time.perf_counter()
    async with worker:
        await asyncio.gather(
            *(execute_run(index) for index in range(arguments.workflows))
        )
    wall_seconds = time.perf_counter() - wall_start
    if not latencies_ms:
        raise RuntimeError(f"no workflow completed: {failures[:3]}")
    return {
        "scenario": "temporal-interactive-worker",
        "address": arguments.address,
        "task_queue": task_queue_name(QueueKind.INTERACTIVE),
        "workflow": "MatterTaskWorkflow (1 step, auto approval, 1 dispatch)",
        "workflows_requested": arguments.workflows,
        "workflows_completed": len(latencies_ms),
        "max_concurrency": arguments.max_concurrency,
        "wall_seconds": round(wall_seconds, 3),
        "workflows_per_second": round(len(latencies_ms) / wall_seconds, 3),
        "completion_latency_ms": latency_summary(latencies_ms),
        "failures": failures,
        "dispatch_receipts": ledger.recorded_count,
    }


def run_temporal_scenario(arguments: argparse.Namespace) -> dict[str, Any]:
    import asyncio

    if arguments.workflows < 1 or arguments.max_concurrency < 1:
        raise ValueError("workflows and max-concurrency must be >= 1")
    return asyncio.run(_temporal_run(arguments))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="JSON output path (default build/benchmarks/load-<scenario>.json)",
    )
    subparsers = parser.add_subparsers(dest="scenario", required=True)

    api = subparsers.add_parser("api", help="workspace API HTTP load")
    api.add_argument("--workers", default="1,4,8,16,32")
    api.add_argument("--requests-per-worker", type=int, default=200)
    api.add_argument("--startup-timeout-seconds", type=float, default=30.0)

    signing = subparsers.add_parser("signing", help="CapAuth signing-path load")
    signing.add_argument("--workers", default="1,2,4,8")
    signing.add_argument("--ops-per-worker", type=int, default=40)
    signing.add_argument("--mode", choices=("openpgp", "stub"), default="openpgp")

    temporal = subparsers.add_parser("temporal", help="Temporal worker burst")
    temporal.add_argument("--address", default="127.0.0.1:17233")
    temporal.add_argument("--workflows", type=int, default=40)
    temporal.add_argument("--max-concurrency", type=int, default=10)
    temporal.add_argument("--run-timeout-seconds", type=float, default=120.0)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    if arguments.scenario == "api":
        result = run_api_scenario(arguments)
    elif arguments.scenario == "signing":
        result = run_signing_scenario(arguments)
    else:
        result = run_temporal_scenario(arguments)
    report = {
        "benchmark": "sklegal-load-saturation",
        "schema_version": 1,
        "card": "SKL-S5-04B",
        "meta": _meta(),
        "result": result,
    }
    output = arguments.output
    if output is None:
        output = (
            REPO_ROOT / "build" / "benchmarks" / f"load-{arguments.scenario}.json"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"results written to {output}")
    if arguments.scenario == "temporal":
        print(
            f"temporal: {result['workflows_completed']}/{result['workflows_requested']}"
            f" workflows, {result['workflows_per_second']} wf/s,"
            f" p95={result['completion_latency_ms']['p95']}ms,"
            f" failures={len(result['failures'])}"
        )
    else:
        for level in result["levels"]:
            latency = level["latency_ms"]
            print(
                f"  workers={level['workers']:>3} ops/s={level['ops_per_second']:>9}"
                f" p50_ms={latency['p50']:>8} p95_ms={latency['p95']:>8}"
                f" errors={level['errors']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
