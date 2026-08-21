"""Tests for the CapAuth signing hot path benchmark harness."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import benchmark_capauth_hotpath as bench  # noqa: E402


class BenchmarkStatsTest(unittest.TestCase):
    def test_empty_samples_report_zero_count(self) -> None:
        self.assertEqual(bench._stats([]), {"count": 0})

    def test_percentiles_are_nearest_rank_and_monotonic(self) -> None:
        samples = list(range(1, 101))
        stats = bench._stats(samples)
        self.assertEqual(stats["count"], 100)
        self.assertAlmostEqual(stats["min_ms"], 1 / 1_000_000)
        self.assertAlmostEqual(stats["max_ms"], 100 / 1_000_000)
        self.assertAlmostEqual(stats["p50_ms"], 50 / 1_000_000)
        self.assertAlmostEqual(stats["p95_ms"], 95 / 1_000_000)
        self.assertLessEqual(stats["min_ms"], stats["p50_ms"])
        self.assertLessEqual(stats["p50_ms"], stats["mean_ms"])
        self.assertLessEqual(stats["mean_ms"], stats["p95_ms"])
        self.assertLessEqual(stats["p95_ms"], stats["p99_ms"])
        self.assertLessEqual(stats["p99_ms"], stats["max_ms"])


class BenchmarkReportTest(unittest.TestCase):
    def test_stub_report_shape_and_invariants(self) -> None:
        report = bench.run_benchmark(
            iterations=8,
            gpg_iterations=0,
            load_workers=(2,),
            load_per_worker=4,
            include_gpg=False,
        )
        scenarios = report["scenarios"]
        expected = {
            "issue_only_stub",
            "authorize_fresh_one_use_stub",
            "hot_path_total_stub",
            "parse_only_stub",
            "deny_replayed_stub",
        }
        self.assertEqual(set(scenarios), expected)
        for name, stats in scenarios.items():
            with self.subTest(scenario=name):
                self.assertEqual(stats["count"], 8)
                self.assertGreater(stats["mean_ms"], 0)
                self.assertLessEqual(stats["min_ms"], stats["p50_ms"])
                self.assertLessEqual(stats["p50_ms"], stats["p95_ms"])
                self.assertLessEqual(stats["p95_ms"], stats["max_ms"])

        invariants = report["invariants"]
        self.assertTrue(invariants["one_use_replay_denied"])
        self.assertTrue(invariants["allow_count_matches"])
        self.assertEqual(invariants["clock_reads_per_authorize"], 3)
        self.assertEqual(invariants["signature_cache_hits_on_fresh_path"], 0)
        self.assertEqual(
            invariants["signature_cache_lookups_on_fresh_path"],
            8,
        )
        self.assertEqual(invariants["load_errors"], 0)

        load = report["load_stub"]["2"]
        self.assertEqual(load["count"], 8)
        self.assertEqual(load["errors"], 0)
        self.assertGreater(load["ops_per_second"], 0)

        self.assertEqual(report["openpgp_scenarios"], {})
        phases = report["phases_stub"]
        for phase in (
            bench.PHASE_TRUSTED,
            bench.PHASE_PRINCIPALS,
            bench.PHASE_REVOCATIONS,
            bench.PHASE_REPLAY,
            bench.PHASE_AUDIT,
            bench.PHASE_CACHE_CONTAINS,
            bench.PHASE_CACHE_ADD,
        ):
            with self.subTest(phase=phase):
                self.assertGreater(phases[phase]["count"], 0)
                self.assertGreaterEqual(phases[phase]["total_ms"], 0)

        json.dumps(report)

    def test_invalid_iteration_counts_rejected(self) -> None:
        with self.assertRaises(ValueError):
            bench.run_benchmark(iterations=0, include_gpg=False)
        with self.assertRaises(ValueError):
            bench.run_benchmark(iterations=1, load_workers=(0,), include_gpg=False)

    def test_main_writes_stamped_and_latest_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exit_code = bench.main(
                [
                    "--iterations",
                    "4",
                    "--gpg-iterations",
                    "0",
                    "--load-workers",
                    "2",
                    "--load-per-worker",
                    "2",
                    "--no-gpg",
                    "--output-dir",
                    temp,
                ]
            )
            self.assertEqual(exit_code, 0)
            latest = Path(temp) / "capauth-hotpath-latest.json"
            self.assertTrue(latest.exists())
            report = json.loads(latest.read_text(encoding="utf-8"))
            self.assertIn("scenarios", report)
            self.assertEqual(report["parameters"]["iterations"], 4)
            self.assertFalse(report["parameters"]["include_gpg"])
            stamped = [
                item for item in Path(temp).iterdir() if item.name != latest.name
            ]
            self.assertEqual(len(stamped), 1)
            self.assertTrue(stamped[0].name.startswith("capauth-hotpath-"))
            self.assertEqual(
                stamped[0].read_text(encoding="utf-8"),
                latest.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
