#!/usr/bin/env python3
"""Measure per-tenant audit chain-head append serialization (SKL-S3-09).

The append-only audit design (migration 0007) serializes every append for a
tenant through one locked row in sklegal_audit.chain_heads (SELECT ... FOR
UPDATE inside sklegal_audit.append_event). This script quantifies the
resulting single-tenant throughput ceiling and lock-wait profile against a
disposable PostgreSQL container, plus a two-tenant control run to confirm the
serialization boundary is per tenant.

The benchmark is measurement tooling only. It creates no SKLegal schema
objects, edits no shared package code, and removes its container on exit
unless --keep-container is passed.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_RUNNER = REPO_ROOT / "scripts" / "manage_migrations.py"
RUNTIME_PROVISIONER = REPO_ROOT / "scripts" / "provision_postgres_runtime.py"
PRINCIPAL_PROVISIONER = REPO_ROOT / "scripts" / "provision_postgres_principal.py"
UV = REPO_ROOT / ".tools" / "bin" / "uv"
POSTGRES_IMAGE = (
    "postgres:17.7-alpine@sha256:"
    "a6d31f853205ce20d399df4e33a0b4c715672f232f4ee7440499747e6e02c126"  # pragma: allowlist secret
)
DATABASE = "sklegal"
BENCH_ROLE_ALPHA = "sklegal_bench_alpha"
BENCH_ROLE_BETA = "sklegal_bench_beta"
FIXED_OCCURRED_AT = "2026-08-20T12:00:00Z"
MONITOR_CADENCE_SECONDS = 0.01


@dataclass(frozen=True)
class TenantScope:
    """Synthetic tenant scope used by the benchmark."""

    name: str
    role: str
    tenant_id: str
    principal_id: str
    client_id: str
    engagement_id: str
    matter_id: str


SCOPES = (
    TenantScope(
        name="alpha",
        role=BENCH_ROLE_ALPHA,
        tenant_id="10000000-0000-4000-8000-000000000001",
        principal_id="10000000-0000-4000-8000-000000000011",
        client_id="10000000-0000-4000-8000-000000000101",
        engagement_id="10000000-0000-4000-8000-000000000201",
        matter_id="10000000-0000-4000-8000-000000000301",
    ),
    TenantScope(
        name="beta",
        role=BENCH_ROLE_BETA,
        tenant_id="20000000-0000-4000-8000-000000000001",
        principal_id="20000000-0000-4000-8000-000000000011",
        client_id="20000000-0000-4000-8000-000000000101",
        engagement_id="20000000-0000-4000-8000-000000000201",
        matter_id="20000000-0000-4000-8000-000000000301",
    ),
)

SETUP_SQL = """
CREATE SCHEMA bench;
CREATE TABLE bench.samples (
    sampled_at timestamptz NOT NULL,
    waiting_lock integer NOT NULL,
    active integer NOT NULL
);
CREATE FUNCTION bench.timed_append(
    p_event_id uuid,
    p_tenant_id uuid,
    p_matter_id uuid,
    p_principal_id uuid,
    p_run_id uuid,
    p_correlation_id uuid,
    p_trace_id text,
    p_span_id text
)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    started timestamptz;
    finished timestamptz;
BEGIN
    started := clock_timestamp();
    PERFORM sklegal_audit.append_event(
        p_event_id,
        p_tenant_id,
        p_matter_id,
        p_principal_id,
        p_run_id,
        p_correlation_id,
        p_trace_id,
        p_span_id,
        '01',
        'workflow',
        'bench.append',
        'matter',
        p_matter_id,
        NULL,
        NULL,
        'success',
        'bench.ok',
        '2026-08-20T12:00:00Z',
        '{}'::jsonb
    );
    finished := clock_timestamp();
    RETURN extract(epoch FROM started)::text || ' '
        || extract(epoch FROM finished)::text;
END;
$function$;
GRANT USAGE ON SCHEMA bench TO sklegal_bench_alpha, sklegal_bench_beta;
GRANT EXECUTE ON FUNCTION bench.timed_append(
    uuid, uuid, uuid, uuid, uuid, uuid, text, text
) TO sklegal_bench_alpha, sklegal_bench_beta;
"""

MONITOR_STATEMENT = """
INSERT INTO bench.samples
SELECT clock_timestamp(),
       count(*) FILTER (WHERE wait_event_type = 'Lock')::integer,
       count(*) FILTER (WHERE state = 'active')::integer
FROM pg_stat_activity
WHERE backend_type = 'client backend'
  AND datname = 'sklegal'
  AND usename IN ('sklegal_bench_alpha', 'sklegal_bench_beta');
SELECT pg_sleep(%s);
"""


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


def _latency_summary(latencies_ms: list[float]) -> dict[str, float]:
    ordered = sorted(latencies_ms)
    return {
        "mean": statistics.fmean(ordered),
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "p99": _percentile(ordered, 0.99),
        "max": ordered[-1],
    }


@dataclass
class WorkerResult:
    scope: TenantScope
    started: list[float] = field(default_factory=list)
    finished: list[float] = field(default_factory=list)

    @property
    def latencies_ms(self) -> list[float]:
        return [
            (stop - start) * 1000.0
            for start, stop in zip(self.started, self.finished, strict=True)
        ]


class BenchDatabase:
    """Disposable PostgreSQL container with the migrated SKLegal schema."""

    def __init__(self, *, keep_container: bool = False) -> None:
        self.keep_container = keep_container
        self.container = f"sklegal-s309-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    def __enter__(self) -> BenchDatabase:
        self._run_host(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                self.container,
                "--label",
                "com.sklegal.test-card=SKL-S3-09",
                "--network",
                "none",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,noexec,nosuid,size=512m",
                "--env",
                f"POSTGRES_DB={DATABASE}",
                "--env",
                "POSTGRES_USER=postgres",
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                POSTGRES_IMAGE,
            ]
        )
        self._wait_ready()
        self._psql(
            "postgres",
            """
            CREATE ROLE sklegal_migrator LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            GRANT CREATE ON DATABASE sklegal TO sklegal_migrator;
            """,
        )
        self._run_tool(RUNTIME_PROVISIONER)
        self._migrate("up")
        self._seed()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.keep_container:
            return
        subprocess.run(
            ["docker", "rm", "--force", self.container],
            check=False,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def _run_host(command: list[str]) -> None:
        subprocess.run(command, check=True, capture_output=True, text=True)

    def _run_tool(self, script: Path, *arguments: str) -> None:
        subprocess.run(
            [
                str(UV),
                "run",
                "--locked",
                "python",
                str(script),
                *arguments,
                "--docker-container",
                self.container,
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def _migrate(self, *arguments: str) -> None:
        self._run_tool(MIGRATION_RUNNER, *arguments, "--user", "sklegal_migrator")

    def _wait_ready(self) -> None:
        for attempt in range(120):
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    self.container,
                    "pg_isready",
                    "--username",
                    "postgres",
                    "--dbname",
                    DATABASE,
                ],
                capture_output=True,
                text=True,
            )
            if ready.returncode == 0:
                return
            if attempt == 119:
                raise RuntimeError("disposable PostgreSQL readiness timeout")
            time.sleep(0.25)

    def _psql_command(self, user: str) -> list[str]:
        return [
            "docker",
            "exec",
            "-i",
            self.container,
            "psql",
            "--no-psqlrc",
            "--quiet",
            "--set",
            "ON_ERROR_STOP=1",
            "--username",
            user,
            "--dbname",
            DATABASE,
            "--tuples-only",
            "--no-align",
        ]

    def _psql(self, user: str, sql: str) -> str:
        result = subprocess.run(
            self._psql_command(user),
            input=sql,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"psql failed for role {user}: {result.stderr}")
        return result.stdout

    def _seed(self) -> None:
        for scope in SCOPES:
            self._psql(
                "postgres",
                f"""
                CREATE ROLE {scope.role} LOGIN NOSUPERUSER NOCREATEDB
                    NOCREATEROLE NOINHERIT NOBYPASSRLS;
                INSERT INTO sklegal_identity.tenants
                    (id, tenant_id, slug, name, status)
                VALUES ('{scope.tenant_id}', '{scope.tenant_id}',
                        'bench-{scope.name}', 'Bench {scope.name}', 'active');
                INSERT INTO sklegal_identity.principals
                    (id, tenant_id, principal_kind, display_name)
                VALUES ('{scope.principal_id}', '{scope.tenant_id}',
                        'human', 'Bench Principal {scope.name}');
                INSERT INTO sklegal_identity.tenant_memberships
                    (tenant_id, principal_id, membership_role)
                VALUES ('{scope.tenant_id}', '{scope.principal_id}', 'member');
                INSERT INTO sklegal_legal.clients
                    (id, tenant_id, display_name, client_kind, status)
                VALUES ('{scope.client_id}', '{scope.tenant_id}',
                        'Bench Client {scope.name}', 'company', 'active');
                INSERT INTO sklegal_legal.engagements
                    (id, tenant_id, client_id, title, scope, status, valid_from)
                VALUES ('{scope.engagement_id}', '{scope.tenant_id}',
                        '{scope.client_id}', 'Bench Engagement {scope.name}',
                        'Benchmark scope', 'active', '2026-01-01T00:00:00Z');
                INSERT INTO sklegal_legal.matters
                    (id, tenant_id, matter_id, client_id, engagement_id,
                     title, summary, status, opened_at)
                VALUES ('{scope.matter_id}', '{scope.tenant_id}',
                        '{scope.matter_id}', '{scope.client_id}',
                        '{scope.engagement_id}', 'Bench Matter {scope.name}',
                        'Benchmark matter only.', 'open', '2026-01-02T00:00:00Z');
                INSERT INTO sklegal_legal.matter_memberships
                    (tenant_id, matter_id, principal_id, membership_role)
                VALUES ('{scope.tenant_id}', '{scope.matter_id}',
                        '{scope.principal_id}', 'member');
                """,
            )
            self._run_tool(
                PRINCIPAL_PROVISIONER,
                "--runtime-role",
                scope.role,
                "--tenant-id",
                scope.tenant_id,
                "--principal-id",
                scope.principal_id,
            )
        self._psql("postgres", SETUP_SQL)

    def spawn_worker(self, scope: TenantScope, input_path: Path) -> subprocess.Popen:
        stdin = open(input_path, encoding="utf-8")  # noqa: SIM115
        return subprocess.Popen(  # noqa: S603
            self._psql_command(scope.role),
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def spawn_monitor(self, statement_count: int) -> tuple[subprocess.Popen, str]:
        payload = (MONITOR_STATEMENT % MONITOR_CADENCE_SECONDS) * statement_count
        return subprocess.Popen(  # noqa: S603
            self._psql_command("postgres"),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        ), payload

    def sample_rows(self, start: float, stop: float) -> list[tuple[float, int, int]]:
        output = self._psql(
            "postgres",
            f"""
            SELECT extract(epoch FROM sampled_at), waiting_lock, active
            FROM bench.samples
            WHERE sampled_at >= to_timestamp({start})
              AND sampled_at <= to_timestamp({stop})
            ORDER BY sampled_at;
            """,
        )
        rows: list[tuple[float, int, int]] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            epoch_text, waiting, active = line.split("|")
            rows.append((float(epoch_text), int(waiting), int(active)))
        return rows

    def deadlocks(self) -> int:
        output = self._psql(
            "postgres",
            "SELECT deadlocks FROM pg_stat_database WHERE datname = 'sklegal';",
        )
        return int(output.strip())

    def event_count(self, tenant_id: str) -> int:
        output = self._psql(
            "postgres",
            f"SELECT count(*) FROM sklegal_audit.events "
            f"WHERE tenant_id = '{tenant_id}';",
        )
        return int(output.strip())

    def verify_chain(self, scope: TenantScope) -> bool:
        output = self._psql(
            scope.role, "SELECT sklegal_audit.verify_current_tenant_chain();"
        )
        return output.strip() == "t"

    def truncate_samples(self) -> None:
        self._psql("postgres", "TRUNCATE bench.samples;")


def _worker_input(scope: TenantScope, events: int, rng: random.Random) -> str:
    lines = []
    for _ in range(events):
        event_id = uuid.UUID(int=rng.getrandbits(128), version=4)
        run_id = uuid.UUID(int=rng.getrandbits(128), version=4)
        correlation_id = uuid.UUID(int=rng.getrandbits(128), version=4)
        trace_id = f"{rng.getrandbits(128):032x}"
        span_id = f"{rng.getrandbits(64):016x}"
        lines.append(
            "SELECT bench.timed_append("
            f"'{event_id}', '{scope.tenant_id}', '{scope.matter_id}', "
            f"'{scope.principal_id}', '{run_id}', '{correlation_id}', "
            f"'{trace_id}', '{span_id}');"
        )
    return "\n".join(lines) + "\n"


def _monitor_statement_count(
    workers: int, events_per_worker: int, reference_ops_per_second: float
) -> int:
    """Estimate how many monitor samples a level needs, with headroom."""
    total_events = workers * events_per_worker
    if reference_ops_per_second > 0:
        expected_seconds = total_events / reference_ops_per_second
    else:
        expected_seconds = total_events * 0.05
    samples = int(expected_seconds * 1.5 / MONITOR_CADENCE_SECONDS) + 500
    return min(max(samples, 2000), 60000)


def run_benchmark(arguments: argparse.Namespace) -> dict[str, object]:
    seed = arguments.seed
    rng = random.Random(seed)
    started_utc = datetime.now(UTC).isoformat()
    levels = [int(entry) for entry in arguments.workers.split(",")]
    if not levels or any(level < 1 for level in levels):
        raise ValueError("workers must be a comma-separated list of integers >= 1")
    if arguments.events_per_worker < 1:
        raise ValueError("events-per-worker must be >= 1")

    result: dict[str, object] = {
        "benchmark": "sklegal-audit-chain-head-serialization",
        "schema_version": 1,
        "card": "SKL-S3-09",
        "label": arguments.label,
        "started_at_utc": started_utc,
        "environment": {
            "postgres_image": POSTGRES_IMAGE,
            "postgres_version": "",
            "host_cpus": os.cpu_count(),
            "platform": platform.platform(),
            "storage": "tmpfs (container data directory on RAM)",
            "client_transport": "psql over persistent docker exec pipes",
        },
        "config": {
            "workers": levels,
            "events_per_worker": arguments.events_per_worker,
            "control_workers_per_tenant": arguments.control_workers_per_tenant,
            "control_events_per_worker": arguments.control_events_per_worker,
            "seed": seed,
        },
    }

    with BenchDatabase(keep_container=arguments.keep_container) as database:
        environment = result["environment"]
        assert isinstance(environment, dict)
        environment["postgres_version"] = database._psql(
            "postgres", "SHOW server_version;"
        ).strip()

        single = _measure_single_tenant(database, levels, arguments, rng)
        result["single_tenant"] = single
        if not arguments.no_control:
            result["two_tenant_control"] = _measure_two_tenant_control(
                database, arguments, rng, single
            )

    return result


def _execute_level(
    database: BenchDatabase,
    *,
    worker_specs: list[TenantScope],
    events_per_worker: int,
    rng: random.Random,
    reference_ops_per_second: float,
    timeout_seconds: int,
) -> dict[str, object]:
    """Execute one level: spawn workers plus a lock-wait monitor.

    worker_specs lists the tenant scope each worker appends to. Returns wall
    time, throughput, latency percentiles, and the sampled lock-wait profile.
    """
    total_workers = len(worker_specs)
    with tempfile.TemporaryDirectory(prefix="sklegal-s309-") as directory:
        client_start = time.monotonic()
        processes: list[tuple[TenantScope, subprocess.Popen]] = []
        for index, scope in enumerate(worker_specs):
            input_path = Path(directory) / f"worker-{index}.sql"
            input_path.write_text(
                _worker_input(scope, events_per_worker, rng), encoding="utf-8"
            )
            processes.append((scope, database.spawn_worker(scope, input_path)))

        # Drain every worker stdout on its own thread so a full pipe buffer
        # can never stall a worker while the monitor payload is written.
        buffers: list[list[str]] = []
        readers: list[threading.Thread] = []
        for _scope, process in processes:
            assert process.stdout is not None
            buffer: list[str] = []
            reader = threading.Thread(target=buffer.extend, args=(process.stdout,))
            reader.start()
            buffers.append(buffer)
            readers.append(reader)

        monitor_count = _monitor_statement_count(
            total_workers, events_per_worker, reference_ops_per_second
        )
        monitor, monitor_payload = database.spawn_monitor(monitor_count)
        assert monitor.stdin is not None
        monitor.stdin.write(monitor_payload)
        monitor.stdin.close()

        results: list[WorkerResult] = []
        errors: list[str] = []
        for (scope, process), buffer, reader in zip(
            processes, buffers, readers, strict=True
        ):
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                reader.join(timeout=10)
                errors.append(f"worker for {scope.name} timed out")
                continue
            reader.join(timeout=10)
            if process.returncode != 0:
                assert process.stderr is not None
                errors.append(
                    f"worker for {scope.name} failed: {process.stderr.read().strip()}"
                )
                continue
            worker = WorkerResult(scope=scope)
            for line in buffer:
                stripped = line.strip()
                if not stripped:
                    continue
                started_text, finished_text = stripped.split(" ")
                worker.started.append(float(started_text))
                worker.finished.append(float(finished_text))
            results.append(worker)
        client_stop = time.monotonic()
        monitor.terminate()
        monitor.wait(timeout=10)

    if errors:
        raise RuntimeError("; ".join(errors))
    for worker in results:
        if len(worker.started) != events_per_worker:
            raise RuntimeError(
                f"worker for {worker.scope.name} produced "
                f"{len(worker.started)} of {events_per_worker} events"
            )

    all_started = [value for worker in results for value in worker.started]
    all_finished = [value for worker in results for value in worker.finished]
    latencies_ms = [value for worker in results for value in worker.latencies_ms]
    events = len(all_started)
    server_wall = max(all_finished) - min(all_started)
    client_wall = client_stop - client_start
    ops_per_second = events / server_wall if server_wall > 0 else 0.0

    # Window the lock-wait samples to the interval in which workers were
    # actually appending; the monitor stream can outlive the workers and
    # would otherwise dilute the profile with idle samples.
    samples = database.sample_rows(min(all_started), max(all_finished))
    waiting_counts = [row[1] for row in samples]
    with_waiters = sum(1 for count in waiting_counts if count > 0)
    lock_wait = {
        "sample_count": len(samples),
        "samples_with_lock_waiters_pct": (
            round(100.0 * with_waiters / len(samples), 2) if samples else 0.0
        ),
        "mean_lock_waiters": (
            round(statistics.fmean(waiting_counts), 3) if samples else 0.0
        ),
        "max_lock_waiters": max(waiting_counts) if samples else 0,
    }
    return {
        "workers": total_workers,
        "events": events,
        "server_wall_seconds": round(server_wall, 4),
        "client_wall_seconds": round(client_wall, 4),
        "ops_per_second": round(ops_per_second, 2),
        "latency_ms": {
            key: round(value, 3)
            for key, value in _latency_summary(latencies_ms).items()
        },
        "lock_wait": lock_wait,
    }


def _measure_single_tenant(
    database: BenchDatabase,
    levels: list[int],
    arguments: argparse.Namespace,
    rng: random.Random,
) -> dict[str, object]:
    scope = SCOPES[0]
    deadlocks_before = database.deadlocks()
    measured_levels: list[dict[str, object]] = []
    reference_ops = 0.0
    for level in levels:
        database.truncate_samples()
        specs = [scope for _ in range(level)]
        level_result = _execute_level(
            database,
            worker_specs=specs,
            events_per_worker=arguments.events_per_worker,
            rng=rng,
            reference_ops_per_second=reference_ops,
            timeout_seconds=arguments.timeout_seconds,
        )
        reference_ops = float(level_result["ops_per_second"])
        measured_levels.append(level_result)
    baseline = measured_levels[0] if levels[0] == 1 else None
    service_ms = (
        float(baseline["latency_ms"]["mean"]) if isinstance(baseline, dict) else None
    )
    for level_result in measured_levels:
        latency = level_result["latency_ms"]
        assert isinstance(latency, dict)
        if service_ms:
            level_result["estimated_lock_wait_fraction"] = round(
                max(0.0, 1.0 - service_ms / float(latency["mean"])), 3
            )
    deadlocks_after = database.deadlocks()
    expected_events = sum(levels) * arguments.events_per_worker
    return {
        "tenant_id": scope.tenant_id,
        "role": scope.role,
        "levels": measured_levels,
        "single_worker_service_ms": service_ms,
        "deadlocks_delta": deadlocks_after - deadlocks_before,
        "event_count": database.event_count(scope.tenant_id),
        "expected_event_count": expected_events,
        "chain_verified": database.verify_chain(scope),
    }


def _measure_two_tenant_control(
    database: BenchDatabase,
    arguments: argparse.Namespace,
    rng: random.Random,
    single: dict[str, object],
) -> dict[str, object]:
    workers = arguments.control_workers_per_tenant
    specs = [scope for scope in SCOPES[:2] for _ in range(workers)]
    database.truncate_samples()
    control = _execute_level(
        database,
        worker_specs=specs,
        events_per_worker=arguments.control_events_per_worker,
        rng=rng,
        reference_ops_per_second=0.0,
        timeout_seconds=arguments.timeout_seconds,
    )
    single_levels = single["levels"]
    assert isinstance(single_levels, list)
    match = next(
        (
            level
            for level in single_levels
            if isinstance(level, dict) and level["workers"] == workers
        ),
        None,
    )
    if match is not None:
        control["speedup_vs_single_tenant_same_workers"] = round(
            float(control["ops_per_second"]) / float(match["ops_per_second"]), 3
        )
    control["tenants"] = [scope.tenant_id for scope in SCOPES[:2]]
    control["workers_per_tenant"] = workers
    control["events_per_worker"] = arguments.control_events_per_worker
    control["chain_verified_alpha"] = database.verify_chain(SCOPES[0])
    control["chain_verified_beta"] = database.verify_chain(SCOPES[1])
    return control


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workers",
        default="1,2,4,8,16,32",
        help="comma-separated single-tenant concurrency levels (default %(default)s)",
    )
    parser.add_argument(
        "--events-per-worker",
        type=int,
        default=200,
        help="appends each worker performs per level (default %(default)s)",
    )
    parser.add_argument(
        "--control-workers-per-tenant",
        type=int,
        default=8,
        help="workers per tenant in the two-tenant control (default %(default)s)",
    )
    parser.add_argument(
        "--control-events-per-worker",
        type=int,
        default=200,
        help="appends per worker in the two-tenant control (default %(default)s)",
    )
    parser.add_argument(
        "--no-control",
        action="store_true",
        help="skip the two-tenant control run",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "build" / "audit" / "chain-head-benchmark.json",
        help="JSON output path (default %(default)s)",
    )
    parser.add_argument("--label", default="local", help="run label")
    parser.add_argument("--seed", type=int, default=309, help="benchmark data RNG seed")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=900,
        help="per-worker timeout in seconds (default %(default)s)",
    )
    parser.add_argument(
        "--keep-container",
        action="store_true",
        help="keep the disposable container after the run",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if shutil.which("docker") is None:
        print("docker is required for the chain-head benchmark", file=sys.stderr)
        return 2
    arguments = parse_arguments(argv)
    result = run_benchmark(arguments)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    single = result["single_tenant"]
    assert isinstance(single, dict)
    levels = single["levels"]
    assert isinstance(levels, list)
    print(f"results written to {arguments.output}")
    print("single-tenant append levels:")
    for level in levels:
        assert isinstance(level, dict)
        latency = level["latency_ms"]
        lock_wait = level["lock_wait"]
        assert isinstance(latency, dict)
        assert isinstance(lock_wait, dict)
        print(
            f"  workers={level['workers']:>3} ops/s={level['ops_per_second']:>9} "
            f"p50_ms={latency['p50']:>8} p99_ms={latency['p99']:>8} "
            f"lock_wait_samples_pct={lock_wait['samples_with_lock_waiters_pct']}"
        )
    print(f"chain verified: {single['chain_verified']}")
    if "two_tenant_control" in result:
        control = result["two_tenant_control"]
        assert isinstance(control, dict)
        print(
            f"two-tenant control: ops/s={control['ops_per_second']} "
            f"speedup={control.get('speedup_vs_single_tenant_same_workers')}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
