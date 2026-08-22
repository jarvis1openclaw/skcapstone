#!/usr/bin/env python3
"""SKL-S5-04B Postgres saturation: reads, CapAuth SQL, audit under load.

Extends the SKL-S3-09 chain-head benchmark harness (imported, not copied)
with three saturation scenarios against one disposable PostgreSQL container
running the fully migrated SKLegal schema:

- ``reads``: pilot-scale workspace-shaped reads (fact assertion, matter,
  and matter event lookups through the RLS-bound runtime roles) at rising
  concurrency.
- ``capauth``: the three durable CapAuth SQL functions on the authorization
  hot path (principal snapshot, revocation snapshot, replay reserve)
  measured under concurrent load.
- ``audit``: the per-tenant audit chain-head append ceiling measured first
  idle and then while concurrent read load saturates the instance.

The benchmark is measurement tooling only. It creates no SKLegal schema
objects beyond the same throwaway ``bench`` schema the S3-09 harness uses,
edits no shared package code, and removes its container on exit unless
--keep-container is passed. All tenants, matters, and digests are synthetic.
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
from datetime import UTC, datetime
from pathlib import Path

import benchmark_audit_chain_head as chain

REPO_ROOT = Path(__file__).resolve().parents[1]

# Pilot scale from SKL-S5-01A: 54 fact assertions in the pilot matter.
# Growth projection: 25 matters per tenant, so the hot tenant carries
# 25 x 54 = 1350 fact assertions.
PILOT_FACTS_PER_MATTER = 54
GROWTH_MATTERS_PER_TENANT = 25
BETA_FACTS = 216

SEED_SQL = f"""
UPDATE sklegal_identity.principals
SET authentication_subject = 'synthetic:human:bench-alpha',
    version = version + 1
WHERE id = '{chain.SCOPES[0].principal_id}';
UPDATE sklegal_identity.principals
SET authentication_subject = 'synthetic:human:bench-beta',
    version = version + 1
WHERE id = '{chain.SCOPES[1].principal_id}';
INSERT INTO sklegal_legal.source_references (
    id, tenant_id, matter_id, source_system, source_version,
    content_sha256, locator, observed_at
)
SELECT md5('src-alpha-' || series)::uuid,
       '{chain.SCOPES[0].tenant_id}',
       '{chain.SCOPES[0].matter_id}',
       'hammertime',
       'synthetic-snapshot-1',
       md5('content-alpha-' || series) || md5('alpha-' || series),
       'synthetic/source-' || series || '.md',
       '2026-08-20T12:00:00Z'
FROM generate_series(1, {GROWTH_MATTERS_PER_TENANT}) AS series;
INSERT INTO sklegal_legal.fact_assertions (
    id, tenant_id, matter_id, subject_ref, predicate, value_type,
    asserted_value, source_reference_id, source_locator, observed_at
)
SELECT md5('fact-alpha-' || series)::uuid,
       '{chain.SCOPES[0].tenant_id}',
       '{chain.SCOPES[0].matter_id}',
       '{chain.SCOPES[0].principal_id}',
       'synthetic-predicate-' || (series % 12),
       'string',
       to_jsonb('synthetic value ' || series),
       md5('src-alpha-' || (series % {GROWTH_MATTERS_PER_TENANT} + 1))::uuid,
       '#/facts/' || series,
       '2026-08-20T12:00:00Z'
FROM generate_series(1, {PILOT_FACTS_PER_MATTER * GROWTH_MATTERS_PER_TENANT}) AS series;
INSERT INTO sklegal_legal.source_references (
    id, tenant_id, matter_id, source_system, source_version,
    content_sha256, locator, observed_at
)
SELECT md5('src-beta-' || series)::uuid,
       '{chain.SCOPES[1].tenant_id}',
       '{chain.SCOPES[1].matter_id}',
       'hammertime',
       'synthetic-snapshot-1',
       md5('content-beta-' || series) || md5('beta-' || series),
       'synthetic/source-' || series || '.md',
       '2026-08-20T12:00:00Z'
FROM generate_series(1, 4) AS series;
INSERT INTO sklegal_legal.fact_assertions (
    id, tenant_id, matter_id, subject_ref, predicate, value_type,
    asserted_value, source_reference_id, source_locator, observed_at
)
SELECT md5('fact-beta-' || series)::uuid,
       '{chain.SCOPES[1].tenant_id}',
       '{chain.SCOPES[1].matter_id}',
       '{chain.SCOPES[1].principal_id}',
       'synthetic-predicate-' || (series % 12),
       'string',
       to_jsonb('synthetic value ' || series),
       md5('src-beta-' || (series % 4 + 1))::uuid,
       '#/facts/' || series,
       '2026-08-20T12:00:00Z'
FROM generate_series(1, {BETA_FACTS}) AS series;
"""

FUNCTIONS_SQL = """
CREATE FUNCTION bench.timed_read(p_matter_id uuid)
RETURNS text
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    started timestamptz;
    finished timestamptz;
    sink bigint;
BEGIN
    started := clock_timestamp();
    SELECT count(*) INTO sink
    FROM sklegal_legal.fact_assertions
    WHERE matter_id = p_matter_id;
    SELECT count(*) INTO sink
    FROM sklegal_legal.matters
    WHERE matter_id = p_matter_id;
    SELECT count(*) INTO sink
    FROM sklegal_legal.matter_events
    WHERE matter_id = p_matter_id;
    finished := clock_timestamp();
    RETURN extract(epoch FROM started)::text || ' '
        || extract(epoch FROM finished)::text;
END;
$function$;

CREATE FUNCTION bench.timed_principal_snapshot(p_tenant_id uuid, p_principal_id uuid)
RETURNS text
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    started timestamptz;
    finished timestamptz;
    sink jsonb;
BEGIN
    started := clock_timestamp();
    sink := sklegal_identity.capability_principal_snapshot(
        p_tenant_id, p_principal_id
    );
    finished := clock_timestamp();
    RETURN extract(epoch FROM started)::text || ' '
        || extract(epoch FROM finished)::text;
END;
$function$;

CREATE FUNCTION bench.timed_revocation_snapshot(
    p_tenant_id uuid,
    p_digests sklegal_legal.sha256_digest[]
)
RETURNS text
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    started timestamptz;
    finished timestamptz;
    sink jsonb;
BEGIN
    started := clock_timestamp();
    sink := sklegal_identity.capability_revocation_snapshot(
        p_tenant_id, p_digests
    );
    finished := clock_timestamp();
    RETURN extract(epoch FROM started)::text || ' '
        || extract(epoch FROM finished)::text;
END;
$function$;

CREATE FUNCTION bench.timed_replay_reserve(
    p_tenant_id uuid,
    p_digest sklegal_legal.sha256_digest,
    p_decision_id uuid
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
    sink boolean;
BEGIN
    started := clock_timestamp();
    sink := sklegal_identity.reserve_capability(
        p_tenant_id, p_digest, p_decision_id, clock_timestamp() + interval '1 hour'
    );
    finished := clock_timestamp();
    RETURN extract(epoch FROM started)::text || ' '
        || extract(epoch FROM finished)::text;
END;
$function$;

GRANT EXECUTE ON FUNCTION bench.timed_read(uuid)
    TO sklegal_bench_alpha, sklegal_bench_beta;
GRANT EXECUTE ON FUNCTION bench.timed_principal_snapshot(uuid, uuid)
    TO sklegal_bench_alpha, sklegal_bench_beta;
GRANT EXECUTE ON FUNCTION bench.timed_revocation_snapshot(
    uuid, sklegal_legal.sha256_digest[]
) TO sklegal_bench_alpha, sklegal_bench_beta;
GRANT EXECUTE ON FUNCTION bench.timed_replay_reserve(
    uuid, sklegal_legal.sha256_digest, uuid
) TO sklegal_bench_alpha, sklegal_bench_beta;
-- Mirror the production sklegal_runtime function profile for the two
-- disposable benchmark roles so the CapAuth SQL path is measured through
-- the same grants the API composition receives.
GRANT EXECUTE ON FUNCTION sklegal_identity.capability_principal_snapshot(
    uuid, uuid
) TO sklegal_bench_alpha, sklegal_bench_beta;
GRANT EXECUTE ON FUNCTION sklegal_identity.capability_revocation_snapshot(
    uuid, sklegal_legal.sha256_digest[]
) TO sklegal_bench_alpha, sklegal_bench_beta;
GRANT EXECUTE ON FUNCTION sklegal_identity.reserve_capability(
    uuid, sklegal_legal.sha256_digest, uuid, timestamptz
) TO sklegal_bench_alpha, sklegal_bench_beta;
"""


class SaturationDatabase(chain.BenchDatabase):
    """S3-09 benchmark database extended with pilot-scale read data."""

    def __enter__(self) -> SaturationDatabase:
        super().__enter__()
        self._psql("postgres", SEED_SQL)
        self._psql("postgres", FUNCTIONS_SQL)
        return self


def _read_input(scope: chain.TenantScope, reads: int) -> str:
    return (f"SELECT bench.timed_read('{scope.matter_id}');\n") * reads


def _principal_snapshot_input(scope: chain.TenantScope, ops: int) -> str:
    return (
        "SELECT bench.timed_principal_snapshot("
        f"'{scope.tenant_id}', '{scope.principal_id}');\n"
    ) * ops


def _revocation_snapshot_input(
    scope: chain.TenantScope, ops: int, rng: random.Random
) -> str:
    lines = []
    for _ in range(ops):
        digests = ", ".join(f"'{rng.getrandbits(256):064x}'" for _ in range(3))
        lines.append(
            "SELECT bench.timed_revocation_snapshot("
            f"'{scope.tenant_id}', ARRAY[{digests}]::sklegal_legal.sha256_digest[]);"
        )
    return "\n".join(lines) + "\n"


def _replay_reserve_input(
    scope: chain.TenantScope, ops: int, rng: random.Random
) -> str:
    lines = []
    for _ in range(ops):
        digest = f"{rng.getrandbits(256):064x}"
        decision_id = f"{rng.getrandbits(128):032x}"
        lines.append(
            "SELECT bench.timed_replay_reserve("
            f"'{scope.tenant_id}', '{digest}', "
            f"'{decision_id[0:8]}-{decision_id[8:12]}-{decision_id[12:16]}-"
            f"{decision_id[16:20]}-{decision_id[20:32]}');"
        )
    return "\n".join(lines) + "\n"


def _parse_worker_output(buffer: list[str]) -> tuple[list[float], list[float]]:
    started: list[float] = []
    finished: list[float] = []
    for line in buffer:
        stripped = line.strip()
        if not stripped:
            continue
        started_text, finished_text = stripped.split(" ")
        started.append(float(started_text))
        finished.append(float(finished_text))
    return started, finished


def _execute_workers(
    database: SaturationDatabase,
    workloads: list[tuple[chain.TenantScope, str, str]],
    timeout_seconds: int,
    *,
    monitor: bool = False,
    reference_ops_per_second: float = 0.0,
) -> dict[str, object]:
    """Run persistent psql workers over prebuilt SQL inputs.

    workloads tuples are (tenant scope, kind label, SQL payload); the scope
    selects the login role and the kind label groups the per-kind aggregate
    in the result (so a mixed append-plus-read run reports an append
    throughput and a read latency separately). Returns the wall-time
    envelope, and optionally the lock-wait profile sampled while the workers
    ran.
    """
    with tempfile.TemporaryDirectory(prefix="sklegal-s504b-") as directory:
        client_start = time.monotonic()
        processes: list[tuple[chain.TenantScope, str, subprocess.Popen]] = []
        for index, (scope, kind, payload) in enumerate(workloads):
            input_path = Path(directory) / f"worker-{index}.sql"
            input_path.write_text(payload, encoding="utf-8")
            processes.append((scope, kind, database.spawn_worker(scope, input_path)))

        buffers: list[list[str]] = []
        readers: list[threading.Thread] = []
        for _scope, _kind, process in processes:
            assert process.stdout is not None
            buffer: list[str] = []
            reader = threading.Thread(target=buffer.extend, args=(process.stdout,))
            reader.start()
            buffers.append(buffer)
            readers.append(reader)

        monitor_process = None
        if monitor:
            statement_count = chain._monitor_statement_count(
                len(workloads), 200, reference_ops_per_second
            )
            monitor_process, monitor_payload = database.spawn_monitor(statement_count)
            assert monitor_process.stdin is not None
            monitor_process.stdin.write(monitor_payload)
            monitor_process.stdin.close()

        worker_results: list[dict[str, object]] = []
        errors: list[str] = []
        for (scope, kind, process), buffer, reader in zip(
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
            started, finished = _parse_worker_output(buffer)
            worker_results.append(
                {
                    "role": scope.role,
                    "kind": kind,
                    "operations": len(started),
                    "started": started,
                    "finished": finished,
                }
            )
        client_stop = time.monotonic()
        if monitor_process is not None:
            monitor_process.terminate()
            monitor_process.wait(timeout=10)

    if errors:
        raise RuntimeError("; ".join(errors))

    def _floats(worker: dict[str, object], key: str) -> list[float]:
        values = worker[key]
        assert isinstance(values, list)
        return values

    all_started = [
        value for worker in worker_results for value in _floats(worker, "started")
    ]
    all_finished = [
        value for worker in worker_results for value in _floats(worker, "finished")
    ]

    kinds: dict[str, object] = {}
    for kind in sorted({str(worker["kind"]) for worker in worker_results}):
        members = [worker for worker in worker_results if worker["kind"] == kind]
        kind_started = [
            value for worker in members for value in _floats(worker, "started")
        ]
        kind_finished = [
            value for worker in members for value in _floats(worker, "finished")
        ]
        latencies_ms = [
            (stop - start) * 1000.0
            for worker in members
            for start, stop in zip(
                _floats(worker, "started"), _floats(worker, "finished"), strict=True
            )
        ]
        kind_wall = max(kind_finished) - min(kind_started) if kind_started else 0.0
        kinds[kind] = {
            "workers": len(members),
            "operations": len(kind_started),
            "server_wall_seconds": round(kind_wall, 4),
            "ops_per_second": (
                round(len(kind_started) / kind_wall, 2) if kind_wall > 0 else 0.0
            ),
            "latency_ms": chain._latency_summary(latencies_ms) if latencies_ms else {},
        }

    events = len(all_started)
    server_wall = max(all_finished) - min(all_started) if all_started else 0.0
    result: dict[str, object] = {
        "workers": len(workloads),
        "operations": events,
        "server_wall_seconds": round(server_wall, 4),
        "client_wall_seconds": round(client_stop - client_start, 4),
        "ops_per_second": round(events / server_wall, 2) if server_wall > 0 else 0.0,
        "kinds": kinds,
    }
    if monitor and all_started:
        samples = database.sample_rows(min(all_started), max(all_finished))
        waiting_counts = [row[1] for row in samples]
        with_waiters = sum(1 for count in waiting_counts if count > 0)
        result["lock_wait"] = {
            "sample_count": len(samples),
            "samples_with_lock_waiters_pct": (
                round(100.0 * with_waiters / len(samples), 2) if samples else 0.0
            ),
            "mean_lock_waiters": (
                round(statistics.fmean(waiting_counts), 3) if samples else 0.0
            ),
            "max_lock_waiters": max(waiting_counts) if samples else 0,
        }
    return result


def run_reads(
    database: SaturationDatabase, arguments: argparse.Namespace
) -> dict[str, object]:
    levels = [int(entry) for entry in arguments.workers.split(",") if entry.strip()]
    measured: list[dict[str, object]] = []
    for level in levels:
        database.truncate_samples()
        workloads = []
        for index in range(level):
            scope = chain.SCOPES[index % 2]
            workloads.append(
                (scope, "read", _read_input(scope, arguments.reads_per_worker))
            )
        result = _execute_workers(
            database,
            workloads,
            arguments.timeout_seconds,
            monitor=True,
        )
        measured.append(result)
    return {
        "query": "fact_assertions count + matters count + matter_events count",
        "data": {
            "alpha_fact_assertions": PILOT_FACTS_PER_MATTER * GROWTH_MATTERS_PER_TENANT,
            "beta_fact_assertions": BETA_FACTS,
        },
        "levels": measured,
    }


def run_capauth(
    database: SaturationDatabase,
    arguments: argparse.Namespace,
    rng: random.Random,
) -> dict[str, object]:
    scope = chain.SCOPES[0]
    levels = [
        int(entry) for entry in arguments.capauth_workers.split(",") if entry.strip()
    ]
    builders = {
        "principal_snapshot": lambda ops: _principal_snapshot_input(scope, ops),
        "revocation_snapshot": lambda ops: _revocation_snapshot_input(scope, ops, rng),
        "replay_reserve": lambda ops: _replay_reserve_input(scope, ops, rng),
    }
    functions: dict[str, object] = {}
    for name, builder in builders.items():
        measured = []
        for level in levels:
            database.truncate_samples()
            workloads = [
                (scope, name, builder(arguments.capauth_ops_per_worker))
                for _ in range(level)
            ]
            measured.append(
                _execute_workers(database, workloads, arguments.timeout_seconds)
            )
        functions[name] = {"levels": measured}
    reserved = database._psql(
        "postgres",
        "SELECT count(*) FROM sklegal_identity.capability_replay_reservations;",
    ).strip()
    replay_payload = functions["replay_reserve"]
    assert isinstance(replay_payload, dict)
    replay_payload["reservation_rows"] = int(reserved)
    return {
        "tenant": scope.tenant_id,
        "functions": functions,
    }


def run_audit_under_load(
    database: SaturationDatabase,
    arguments: argparse.Namespace,
    rng: random.Random,
) -> dict[str, object]:
    append_scope = chain.SCOPES[0]
    deadlocks_before = database.deadlocks()

    database.truncate_samples()
    idle = _execute_workers(
        database,
        [
            (
                append_scope,
                "append",
                chain._worker_input(append_scope, arguments.appends_per_worker, rng),
            )
            for _ in range(arguments.append_workers)
        ],
        arguments.timeout_seconds,
        monitor=True,
    )

    database.truncate_samples()
    read_scope = chain.SCOPES[1]
    mixed_workloads: list[tuple[chain.TenantScope, str, str]] = [
        (
            append_scope,
            "append",
            chain._worker_input(append_scope, arguments.appends_per_worker, rng),
        )
        for _ in range(arguments.append_workers)
    ] + [
        (
            read_scope,
            "read",
            _read_input(read_scope, arguments.reads_per_worker),
        )
        for _ in range(arguments.read_workers)
    ]
    mixed = _execute_workers(
        database,
        mixed_workloads,
        arguments.timeout_seconds,
        monitor=True,
        reference_ops_per_second=float(idle["ops_per_second"]),
    )

    idle_kinds = idle["kinds"]
    mixed_kinds = mixed["kinds"]
    assert isinstance(idle_kinds, dict) and isinstance(mixed_kinds, dict)
    idle_append = idle_kinds["append"]
    mixed_append = mixed_kinds["append"]
    assert isinstance(idle_append, dict) and isinstance(mixed_append, dict)
    retained = (
        round(
            float(mixed_append["ops_per_second"])
            / float(idle_append["ops_per_second"]),
            3,
        )
        if float(idle_append["ops_per_second"]) > 0
        else 0.0
    )

    deadlocks_after = database.deadlocks()
    return {
        "tenant_id": append_scope.tenant_id,
        "read_tenant_id": read_scope.tenant_id,
        "append_workers": arguments.append_workers,
        "read_workers": arguments.read_workers,
        "appends_per_worker": arguments.appends_per_worker,
        "reads_per_worker": arguments.reads_per_worker,
        "idle_append": idle,
        "mixed_append_and_read": mixed,
        "append_throughput_retained_ratio": retained,
        "deadlocks_delta": deadlocks_after - deadlocks_before,
        "chain_verified_alpha": database.verify_chain(chain.SCOPES[0]),
        "chain_verified_beta": database.verify_chain(chain.SCOPES[1]),
    }


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=("all", "reads", "capauth", "audit"),
        default="all",
    )
    parser.add_argument("--workers", default="1,4,8,16")
    parser.add_argument("--reads-per-worker", type=int, default=200)
    parser.add_argument("--capauth-workers", default="1,4,8")
    parser.add_argument("--capauth-ops-per-worker", type=int, default=100)
    parser.add_argument("--append-workers", type=int, default=8)
    parser.add_argument("--read-workers", type=int, default=16)
    parser.add_argument("--appends-per-worker", type=int, default=200)
    parser.add_argument("--seed", type=int, default=504)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--label", default="local")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "build" / "benchmarks" / "postgres-saturation.json",
    )
    parser.add_argument("--keep-container", action="store_true")
    return parser.parse_args(argv)


def run_benchmark(arguments: argparse.Namespace) -> dict[str, object]:
    rng = random.Random(arguments.seed)
    result: dict[str, object] = {
        "benchmark": "sklegal-postgres-saturation",
        "schema_version": 1,
        "card": "SKL-S5-04B",
        "label": arguments.label,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "environment": {
            "postgres_image": chain.POSTGRES_IMAGE,
            "postgres_version": "",
            "host_cpus": os.cpu_count(),
            "platform": platform.platform(),
            "storage": "tmpfs (container data directory on RAM)",
            "client_transport": "psql over persistent docker exec pipes",
        },
        "config": {
            "scenario": arguments.scenario,
            "workers": arguments.workers,
            "reads_per_worker": arguments.reads_per_worker,
            "capauth_workers": arguments.capauth_workers,
            "capauth_ops_per_worker": arguments.capauth_ops_per_worker,
            "append_workers": arguments.append_workers,
            "read_workers": arguments.read_workers,
            "appends_per_worker": arguments.appends_per_worker,
            "seed": arguments.seed,
        },
    }
    with SaturationDatabase(keep_container=arguments.keep_container) as database:
        environment = result["environment"]
        assert isinstance(environment, dict)
        environment["postgres_version"] = database._psql(
            "postgres", "SHOW server_version;"
        ).strip()
        if arguments.scenario in ("all", "reads"):
            result["reads"] = run_reads(database, arguments)
        if arguments.scenario in ("all", "capauth"):
            result["capauth"] = run_capauth(database, arguments, rng)
        if arguments.scenario in ("all", "audit"):
            result["audit_under_load"] = run_audit_under_load(database, arguments, rng)
    return result


def main(argv: list[str] | None = None) -> int:
    if shutil.which("docker") is None:
        print(
            "docker is required for the postgres saturation benchmark", file=sys.stderr
        )
        return 2
    arguments = parse_arguments(argv)
    result = run_benchmark(arguments)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"results written to {arguments.output}")

    def print_levels(label: str, levels: list[dict[str, object]]) -> None:
        for level in levels:
            kinds = level["kinds"]
            assert isinstance(kinds, dict)
            for kind, payload in sorted(kinds.items()):
                assert isinstance(payload, dict)
                latency = payload["latency_ms"]
                assert isinstance(latency, dict)
                print(
                    f"  {label}/{kind} workers={payload['workers']:>3} "
                    f"ops/s={payload['ops_per_second']:>9} "
                    f"p50_ms={latency['p50']:>8} p99_ms={latency['p99']:>8}"
                )

    reads = result.get("reads")
    if isinstance(reads, dict):
        print_levels("reads", reads["levels"])  # type: ignore[arg-type]
    capauth = result.get("capauth")
    if isinstance(capauth, dict):
        functions = capauth["functions"]
        assert isinstance(functions, dict)
        for name, payload in functions.items():
            if not isinstance(payload, dict) or "levels" not in payload:
                continue
            print_levels(name, payload["levels"])  # type: ignore[arg-type]
    audit = result.get("audit_under_load")
    if isinstance(audit, dict):
        idle = audit["idle_append"]
        mixed = audit["mixed_append_and_read"]
        assert isinstance(idle, dict) and isinstance(mixed, dict)
        idle_kinds = idle["kinds"]
        mixed_kinds = mixed["kinds"]
        assert isinstance(idle_kinds, dict) and isinstance(mixed_kinds, dict)
        idle_append = idle_kinds["append"]
        mixed_append = mixed_kinds["append"]
        mixed_read = mixed_kinds["read"]
        assert isinstance(idle_append, dict)
        assert isinstance(mixed_append, dict)
        assert isinstance(mixed_read, dict)
        print(
            f"  audit idle: {idle_append['ops_per_second']} appends/s; "
            f"under read load: {mixed_append['ops_per_second']} appends/s "
            f"(retained {audit['append_throughput_retained_ratio']}), "
            f"read p99={mixed_read['latency_ms']['p99']}ms"  # type: ignore[index]
        )
        print(
            f"  chains verified: alpha={audit['chain_verified_alpha']} "
            f"beta={audit['chain_verified_beta']} "
            f"deadlocks_delta={audit['deadlocks_delta']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
