"""Tests for the SKL-S5-04B Postgres saturation benchmark.

Unit tests cover the SQL input builders, the worker-output parser, the
pilot-scale seed constants, and argument defaults. The smoke test runs the
real benchmark at tiny scale against a disposable container and asserts the
result schema, chain verification, and zero deadlocks, mirroring the
SKL-S3-09 benchmark test pattern. All tenants, matters, and digests are
synthetic.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import benchmark_audit_chain_head as chain  # noqa: E402
from load_postgres_saturation import (  # noqa: E402
    BETA_FACTS,
    FUNCTIONS_SQL,
    GROWTH_MATTERS_PER_TENANT,
    PILOT_FACTS_PER_MATTER,
    SEED_SQL,
    _parse_worker_output,
    _principal_snapshot_input,
    _read_input,
    _replay_reserve_input,
    _revocation_snapshot_input,
    parse_arguments,
)

HEX64 = re.compile(r"^[0-9a-f]{64}$")


class SeedShapeTests(unittest.TestCase):
    def test_pilot_scale_matches_s5_01a_and_growth_projection(self) -> None:
        self.assertEqual(54, PILOT_FACTS_PER_MATTER)
        self.assertEqual(25, GROWTH_MATTERS_PER_TENANT)
        # The hot tenant carries 25 matters worth of pilot facts.
        self.assertIn(
            f"generate_series(1, {PILOT_FACTS_PER_MATTER * GROWTH_MATTERS_PER_TENANT})",
            SEED_SQL,
        )

    def test_seed_updates_principal_authentication_subjects(self) -> None:
        for scope in chain.SCOPES:
            self.assertIn(f"WHERE id = '{scope.principal_id}';", SEED_SQL)
        self.assertIn("authentication_subject", SEED_SQL)

    def test_functions_grant_runtime_profile_to_bench_roles(self) -> None:
        for role in (chain.BENCH_ROLE_ALPHA, chain.BENCH_ROLE_BETA):
            self.assertIn(role, FUNCTIONS_SQL)
        self.assertIn("sklegal_identity.capability_principal_snapshot", FUNCTIONS_SQL)
        self.assertIn("sklegal_identity.capability_revocation_snapshot", FUNCTIONS_SQL)
        self.assertIn("sklegal_identity.reserve_capability", FUNCTIONS_SQL)


class WorkerInputTests(unittest.TestCase):
    def test_read_input_repeats_one_timed_read_per_operation(self) -> None:
        payload = _read_input(chain.SCOPES[0], 4)
        lines = payload.strip().splitlines()
        self.assertEqual(4, len(lines))
        for line in lines:
            self.assertTrue(line.startswith("SELECT bench.timed_read('"))
            self.assertIn(chain.SCOPES[0].matter_id, line)

    def test_principal_snapshot_input_pins_tenant_and_principal(self) -> None:
        payload = _principal_snapshot_input(chain.SCOPES[0], 3)
        lines = payload.strip().splitlines()
        self.assertEqual(3, len(lines))
        for line in lines:
            self.assertIn("bench.timed_principal_snapshot(", line)
            self.assertIn(chain.SCOPES[0].tenant_id, line)
            self.assertIn(chain.SCOPES[0].principal_id, line)

    def test_revocation_snapshot_input_builds_digest_arrays(self) -> None:
        payload = _revocation_snapshot_input(chain.SCOPES[0], 5, random.Random(7))
        lines = payload.strip().splitlines()
        self.assertEqual(5, len(lines))
        digests: list[str] = []
        for line in lines:
            self.assertIn("bench.timed_revocation_snapshot(", line)
            self.assertIn("::sklegal_legal.sha256_digest[]", line)
            digests.extend(re.findall(r"'([0-9a-f]{64})'", line))
        self.assertEqual(15, len(digests))
        for digest in digests:
            self.assertRegex(digest, HEX64)

    def test_replay_reserve_input_builds_unique_uuid_digest_pairs(self) -> None:
        payload = _replay_reserve_input(chain.SCOPES[1], 6, random.Random(9))
        lines = payload.strip().splitlines()
        self.assertEqual(6, len(lines))
        call_shape = re.compile(
            r"^SELECT bench\.timed_replay_reserve\("
            r"'[0-9a-f-]{36}', '([0-9a-f]{64})', '([0-9a-f-]{36})'\);$"
        )
        digests: list[str] = []
        decisions: list[str] = []
        for line in lines:
            match = call_shape.match(line)
            self.assertIsNotNone(match, line)
            assert match is not None
            self.assertIn(chain.SCOPES[1].tenant_id, line)
            digests.append(match.group(1))
            decisions.append(match.group(2))
        self.assertEqual(6, len(set(digests)))
        self.assertEqual(6, len(set(decisions)))
        for digest in digests:
            self.assertRegex(digest, HEX64)


class WorkerOutputParserTests(unittest.TestCase):
    def test_parses_start_finish_epoch_pairs(self) -> None:
        started, finished = _parse_worker_output(
            ["1.5 1.6", " 2.0 2.25 ", "", "3.0 3.1"]
        )
        self.assertEqual([1.5, 2.0, 3.0], started)
        self.assertEqual([1.6, 2.25, 3.1], finished)

    def test_rejects_malformed_line(self) -> None:
        with self.assertRaises(ValueError):
            _parse_worker_output(["not-a-pair"])


class ArgumentTests(unittest.TestCase):
    def test_defaults(self) -> None:
        arguments = parse_arguments([])
        self.assertEqual("all", arguments.scenario)
        self.assertEqual("1,4,8,16", arguments.workers)
        self.assertEqual(200, arguments.reads_per_worker)
        self.assertEqual("1,4,8", arguments.capauth_workers)
        self.assertEqual(100, arguments.capauth_ops_per_worker)
        self.assertEqual(8, arguments.append_workers)
        self.assertEqual(16, arguments.read_workers)
        self.assertEqual(200, arguments.appends_per_worker)
        self.assertEqual(504, arguments.seed)
        self.assertFalse(arguments.keep_container)

    def test_output_default_lands_in_build_benchmarks(self) -> None:
        arguments = parse_arguments([])
        self.assertEqual(
            REPO_ROOT / "build" / "benchmarks" / "postgres-saturation.json",
            arguments.output,
        )


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

    def test_tiny_run_reports_all_scenarios_with_verified_chains(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sklegal-s504b-pg-test-") as directory:
            output = Path(directory) / "result.json"
            completed = subprocess.run(
                [
                    str(REPO_ROOT / ".tools" / "bin" / "uv"),
                    "run",
                    "--locked",
                    "python",
                    str(REPO_ROOT / "scripts" / "load_postgres_saturation.py"),
                    "--scenario",
                    "all",
                    "--workers",
                    "1,2",
                    "--reads-per-worker",
                    "3",
                    "--capauth-workers",
                    "1",
                    "--capauth-ops-per-worker",
                    "3",
                    "--append-workers",
                    "2",
                    "--read-workers",
                    "2",
                    "--appends-per-worker",
                    "10",
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

        self.assertEqual("sklegal-postgres-saturation", result["benchmark"])
        self.assertEqual("SKL-S5-04B", result["card"])
        environment = result["environment"]
        assert isinstance(environment, dict)
        self.assertTrue(environment["postgres_version"].startswith("17."))

        reads = result["reads"]
        assert isinstance(reads, dict)
        self.assertEqual(
            {
                "alpha_fact_assertions": PILOT_FACTS_PER_MATTER
                * GROWTH_MATTERS_PER_TENANT,
                "beta_fact_assertions": BETA_FACTS,
            },
            reads["data"],
        )
        read_levels = reads["levels"]
        assert isinstance(read_levels, list)
        self.assertEqual([1, 2], [level["workers"] for level in read_levels])
        for level in read_levels:
            self.assertGreater(level["ops_per_second"], 0.0)
            self.assertIn("lock_wait", level)
            kinds = level["kinds"]
            assert isinstance(kinds, dict)
            self.assertEqual({"read"}, set(kinds))

        capauth = result["capauth"]
        assert isinstance(capauth, dict)
        functions = capauth["functions"]
        assert isinstance(functions, dict)
        self.assertEqual(
            {"principal_snapshot", "revocation_snapshot", "replay_reserve"},
            set(functions),
        )
        for name, payload in functions.items():
            levels = payload["levels"]
            assert isinstance(levels, list)
            for level in levels:
                self.assertGreater(level["ops_per_second"], 0.0)
                kinds = level["kinds"]
                assert isinstance(kinds, dict)
                kind = kinds[name]
                assert isinstance(kind, dict)
                self.assertGreater(kind["latency_ms"]["p50"], 0.0)
        self.assertGreater(functions["replay_reserve"]["reservation_rows"], 0)

        audit = result["audit_under_load"]
        assert isinstance(audit, dict)
        self.assertTrue(audit["chain_verified_alpha"])
        self.assertTrue(audit["chain_verified_beta"])
        self.assertEqual(0, audit["deadlocks_delta"])
        self.assertGreater(audit["append_throughput_retained_ratio"], 0.0)
        for phase in ("idle_append", "mixed_append_and_read"):
            payload = audit[phase]
            assert isinstance(payload, dict)
            self.assertGreater(payload["ops_per_second"], 0.0)
        mixed = audit["mixed_append_and_read"]
        assert isinstance(mixed, dict)
        mixed_kinds = mixed["kinds"]
        assert isinstance(mixed_kinds, dict)
        self.assertEqual({"append", "read"}, set(mixed_kinds))


if __name__ == "__main__":
    unittest.main()
