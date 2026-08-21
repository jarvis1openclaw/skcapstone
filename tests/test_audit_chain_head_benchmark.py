"""Tests for the SKL-S3-09 audit chain-head serialization benchmark."""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from benchmark_audit_chain_head import (  # noqa: E402
    SCOPES,
    _latency_summary,
    _monitor_statement_count,
    _percentile,
    _worker_input,
    parse_arguments,
)


class PercentileTests(unittest.TestCase):
    def test_single_value(self) -> None:
        self.assertEqual(4.0, _percentile([4.0], 0.99))

    def test_median_of_odd_list(self) -> None:
        self.assertEqual(2.0, _percentile([1.0, 2.0, 3.0], 0.50))

    def test_interpolates_between_neighbours(self) -> None:
        self.assertEqual(2.5, _percentile([1.0, 2.0, 3.0, 4.0], 0.50))

    def test_rejects_empty_input(self) -> None:
        with self.assertRaises(ValueError):
            _percentile([], 0.50)

    def test_latency_summary_orders_extremes(self) -> None:
        summary = _latency_summary([30.0, 10.0, 20.0])
        self.assertEqual(20.0, summary["p50"])
        self.assertEqual(30.0, summary["max"])
        self.assertEqual(20.0, summary["mean"])


class MonitorEstimateTests(unittest.TestCase):
    def test_without_reference_assumes_slow_serial(self) -> None:
        samples = _monitor_statement_count(4, 100, 0.0)
        self.assertGreaterEqual(samples, 2000)

    def test_scales_with_workload_and_is_capped(self) -> None:
        small = _monitor_statement_count(1, 10, 100.0)
        large = _monitor_statement_count(64, 5000, 100.0)
        self.assertLess(small, large)
        self.assertLessEqual(large, 60000)


class WorkerInputTests(unittest.TestCase):
    def test_generates_one_timed_append_per_event(self) -> None:
        payload = _worker_input(SCOPES[0], 3, random.Random(1))
        lines = payload.strip().splitlines()
        self.assertEqual(3, len(lines))
        for line in lines:
            self.assertTrue(line.startswith("SELECT bench.timed_append('"))
            self.assertTrue(line.endswith("');"))
            self.assertIn(SCOPES[0].tenant_id, line)
            self.assertIn(SCOPES[0].matter_id, line)
            self.assertIn(SCOPES[0].principal_id, line)

    def test_generated_identifiers_are_unique(self) -> None:
        payload = _worker_input(SCOPES[1], 50, random.Random(2))
        self.assertEqual(50, len(set(payload.strip().splitlines())))


class ArgumentTests(unittest.TestCase):
    def test_defaults(self) -> None:
        arguments = parse_arguments([])
        self.assertEqual("1,2,4,8,16,32", arguments.workers)
        self.assertEqual(200, arguments.events_per_worker)
        self.assertFalse(arguments.no_control)
        self.assertFalse(arguments.keep_container)


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "info"], capture_output=True, text=True, check=False
    )
    return probe.returncode == 0


@unittest.skipUnless(_docker_available(), "docker daemon is unavailable")
class BenchmarkSmokeTests(unittest.TestCase):
    """Run the real benchmark with a tiny workload against a container."""

    def test_tiny_run_produces_verified_chain_and_measurements(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sklegal-s309-test-") as directory:
            output = Path(directory) / "result.json"
            completed = subprocess.run(
                [
                    str(REPO_ROOT / ".tools" / "bin" / "uv"),
                    "run",
                    "--locked",
                    "python",
                    str(REPO_ROOT / "scripts" / "benchmark_audit_chain_head.py"),
                    "--workers",
                    "1,2",
                    "--events-per-worker",
                    "10",
                    "--control-workers-per-tenant",
                    "2",
                    "--control-events-per-worker",
                    "5",
                    "--output",
                    str(output),
                    "--label",
                    "unittest-smoke",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=900,
            )
            self.assertEqual(
                0,
                completed.returncode,
                f"benchmark failed:\n{completed.stdout}\n{completed.stderr}",
            )
            result = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual("sklegal-audit-chain-head-serialization", result["benchmark"])
        single = result["single_tenant"]
        self.assertTrue(single["chain_verified"])
        self.assertEqual(30, single["event_count"])
        self.assertEqual(30, single["expected_event_count"])
        self.assertEqual(0, single["deadlocks_delta"])
        levels = {level["workers"]: level for level in single["levels"]}
        self.assertEqual({1, 2}, set(levels))
        for level in levels.values():
            self.assertGreater(level["ops_per_second"], 0.0)
            self.assertGreater(level["latency_ms"]["p50"], 0.0)
            self.assertGreaterEqual(
                level["latency_ms"]["max"], level["latency_ms"]["p50"]
            )
            self.assertIn("samples_with_lock_waiters_pct", level["lock_wait"])
        control = result["two_tenant_control"]
        self.assertTrue(control["chain_verified_alpha"])
        self.assertTrue(control["chain_verified_beta"])
        self.assertEqual(20, control["events"])
        self.assertGreater(control["ops_per_second"], 0.0)
        self.assertIn("speedup_vs_single_tenant_same_workers", control)


if __name__ == "__main__":
    unittest.main()
