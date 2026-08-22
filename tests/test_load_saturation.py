"""Tests for the SKL-S5-04B load and saturation driver.

Unit tests cover the statistics helpers, argument parsing, the synthetic
pilot workspace composition, and the workflow input shape. Smoke tests run
the real driver at tiny scale: the API scenario over loopback uvicorn, the
signing scenario in stub and real OpenPGP modes, and the Temporal scenario
when the disposable development stack is reachable. All identities,
matters, credentials, and key material are synthetic.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from load_saturation import (  # noqa: E402
    APPROVAL_ID,
    MATTER_ID,
    PILOT_FACT_COUNT,
    PILOT_SOURCE_FILE_COUNT,
    PILOT_TENSION_COUNT,
    TENANT_ID,
    _api_grant,
    _api_principal,
    _percentile,
    _process_snapshot,
    build_load_app,
    build_workflow_input,
    latency_summary,
    parse_arguments,
    parse_levels,
)


class PercentileTests(unittest.TestCase):
    def test_single_value(self) -> None:
        self.assertEqual(7.0, _percentile([7.0], 0.99))

    def test_median_of_odd_list(self) -> None:
        self.assertEqual(3.0, _percentile([1.0, 3.0, 5.0], 0.50))

    def test_interpolates_between_neighbours(self) -> None:
        self.assertEqual(4.0, _percentile([1.0, 3.0, 5.0, 7.0], 0.50))

    def test_rejects_empty_input(self) -> None:
        with self.assertRaises(ValueError):
            _percentile([], 0.50)

    def test_latency_summary_orders_extremes(self) -> None:
        summary = latency_summary([90.0, 10.0, 50.0])
        self.assertEqual(50.0, summary["p50"])
        self.assertEqual(90.0, summary["max"])
        self.assertEqual(50.0, summary["mean"])
        self.assertEqual(set(summary), {"mean", "p50", "p95", "p99", "max"})


class ParseLevelsTests(unittest.TestCase):
    def test_parses_comma_separated_levels(self) -> None:
        self.assertEqual([1, 4, 16], parse_levels("1,4,16"))

    def test_ignores_blank_entries(self) -> None:
        self.assertEqual([2], parse_levels(" 2 ,"))

    def test_rejects_empty_list(self) -> None:
        with self.assertRaises(ValueError):
            parse_levels(" , ")

    def test_rejects_non_positive_level(self) -> None:
        with self.assertRaises(ValueError):
            parse_levels("1,0")

    def test_rejects_non_integer_level(self) -> None:
        with self.assertRaises(ValueError):
            parse_levels("1,two")


class ArgumentTests(unittest.TestCase):
    def test_api_defaults(self) -> None:
        arguments = parse_arguments(["api"])
        self.assertEqual("1,4,8,16,32", arguments.workers)
        self.assertEqual(200, arguments.requests_per_worker)
        self.assertEqual(30.0, arguments.startup_timeout_seconds)

    def test_signing_defaults_to_real_openpgp(self) -> None:
        arguments = parse_arguments(["signing"])
        self.assertEqual("1,2,4,8", arguments.workers)
        self.assertEqual(40, arguments.ops_per_worker)
        self.assertEqual("openpgp", arguments.mode)
        self.assertEqual(8, arguments.warmup_operations)

    def test_temporal_defaults_pin_the_dev_stack(self) -> None:
        arguments = parse_arguments(["temporal"])
        self.assertEqual("127.0.0.1:17233", arguments.address)
        self.assertEqual(40, arguments.workflows)
        self.assertEqual(10, arguments.max_concurrency)
        self.assertEqual(120.0, arguments.run_timeout_seconds)

    def test_output_is_a_main_parser_option(self) -> None:
        target = Path("somewhere/else.json")
        arguments = parse_arguments(["--output", str(target), "signing"])
        self.assertEqual(target, arguments.output)


class LoadAppTests(unittest.TestCase):
    """Compose the pilot-scale app and drive it through one real request."""

    def test_workspace_read_serves_pilot_scale_through_capauth(self) -> None:
        from capauth.testing import signing_stub
        from fastapi.testclient import TestClient
        from sklegal_capauth import CapabilityIssuer

        class _StubSigner:
            @property
            def issuer_fingerprint(self) -> str:
                from capauth.testing import STUB_ISSUER_FPR

                return STUB_ISSUER_FPR

            def sign(self, payload_bytes: bytes) -> str:
                from capauth.testing import stub_signature_for

                return stub_signature_for(payload_bytes)

        with signing_stub():
            principal = _api_principal()
            app = build_load_app(principal)
            issuer = CapabilityIssuer(_StubSigner())
            presented = issuer.issue_root(principal=principal, grant=_api_grant())
            raw = presented.credentials_for_verification()[-1]

            client = TestClient(app)
            response = client.get(
                f"/v1/matters/{MATTER_ID}/workspace",
                headers={"Authorization": f"Bearer {raw}"},
            )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual(str(MATTER_ID), payload["matter"]["matterId"])
        self.assertEqual(PILOT_FACT_COUNT, len(payload["facts"]))
        self.assertEqual(PILOT_TENSION_COUNT, len(payload["tensions"]))
        self.assertEqual(
            PILOT_SOURCE_FILE_COUNT, len(payload["provenance"]["sourceFiles"])
        )

    def test_workspace_read_denies_without_credential(self) -> None:
        from capauth.testing import signing_stub
        from fastapi.testclient import TestClient

        with signing_stub():
            app = build_load_app(_api_principal())
            client = TestClient(app)
            response = client.get(f"/v1/matters/{MATTER_ID}/workspace")

        self.assertNotEqual(200, response.status_code)

    def test_process_snapshot_reports_positive_usage(self) -> None:
        snapshot = _process_snapshot()
        self.assertGreaterEqual(snapshot["cpu_seconds_total"], 0.0)
        self.assertGreater(snapshot["rss_mib"], 0.0)


class WorkflowInputTests(unittest.TestCase):
    def test_input_shape_is_one_interactive_gated_run(self) -> None:
        from sklegal_worker.models import QueueKind

        payload = build_workflow_input("run-key-alpha")
        self.assertEqual(QueueKind.INTERACTIVE, payload.queue)
        self.assertEqual(TENANT_ID, payload.identity.tenant_id)
        self.assertEqual(MATTER_ID, payload.identity.matter_id)
        self.assertEqual("run-key-alpha", payload.identity.run_key)
        self.assertEqual(1, len(payload.steps))
        self.assertEqual(APPROVAL_ID, payload.approval.approval_id)
        self.assertEqual(APPROVAL_ID, payload.dispatch.approval_id)

    def test_dispatch_digests_are_valid_sha256(self) -> None:
        """Regression guard: DispatchRequest digests must be 64-char hex."""
        import re

        payload = build_workflow_input("run-key-digests")
        self.assertRegex(payload.dispatch.artifact_digest, r"^[0-9a-f]{64}$")
        self.assertRegex(payload.dispatch.destination_digest, r"^[0-9a-f]{64}$")
        self.assertIsNone(re.match(r"[^0-9a-f]", payload.dispatch.artifact_digest))

    def test_idempotency_keys_are_unique_per_run_key(self) -> None:
        first = build_workflow_input("run-key-one")
        second = build_workflow_input("run-key-two")
        keys = {
            first.steps[0].idempotency_key,
            first.dispatch.idempotency_key,
            second.steps[0].idempotency_key,
            second.dispatch.idempotency_key,
        }
        self.assertEqual(4, len(keys))


def _run_driver(arguments: list[str]) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="sklegal-s504b-test-") as directory:
        output = Path(directory) / "result.json"
        completed = subprocess.run(
            [
                str(REPO_ROOT / ".tools" / "bin" / "uv"),
                "run",
                "--locked",
                "python",
                str(REPO_ROOT / "scripts" / "load_saturation.py"),
                "--output",
                str(output),
                *arguments,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"driver failed:\n{completed.stdout}\n{completed.stderr}"
            )
        report = json.loads(output.read_text(encoding="utf-8"))
    assert isinstance(report, dict)
    return report


class DriverSmokeTests(unittest.TestCase):
    """Run the real driver at tiny scale and validate the report schema."""

    def test_api_scenario_reports_levels_without_errors(self) -> None:
        result = _run_driver(["api", "--workers", "1,2", "--requests-per-worker", "6"])
        self.assertEqual("sklegal-load-saturation", result["benchmark"])
        self.assertEqual("SKL-S5-04B", result["card"])
        scenario = result["result"]
        assert isinstance(scenario, dict)
        self.assertEqual("api-workspace-read", scenario["scenario"])
        levels = scenario["levels"]
        assert isinstance(levels, list)
        self.assertEqual([1, 2], [level["workers"] for level in levels])
        for level in levels:
            self.assertEqual(0, level["errors"])
            self.assertGreater(level["ops_per_second"], 0.0)
            self.assertGreater(level["latency_ms"]["p50"], 0.0)
        self.assertIn("p95", scenario["token_issue_ms"])
        self.assertIn("rss_mib", scenario["server_process"])

    def test_signing_scenario_stub_mode(self) -> None:
        result = _run_driver(
            ["signing", "--mode", "stub", "--workers", "1,2", "--ops-per-worker", "5"]
        )
        scenario = result["result"]
        assert isinstance(scenario, dict)
        self.assertEqual("capauth-signing-path", scenario["scenario"])
        self.assertEqual("stub", scenario["mode"])
        for level in scenario["levels"]:
            self.assertEqual(0, level["errors"])
            self.assertGreater(level["ops_per_second"], 0.0)
        self.assertIn("issue_ms", scenario)
        self.assertIn("authorize_ms", scenario)

    @unittest.skipUnless(shutil.which("gpg"), "gpg is required")
    def test_signing_scenario_real_openpgp_mode(self) -> None:
        stub = _run_driver(
            ["signing", "--mode", "stub", "--workers", "1", "--ops-per-worker", "3"]
        )
        real = _run_driver(
            ["signing", "--mode", "openpgp", "--workers", "1", "--ops-per-worker", "3"]
        )
        stub_scenario = stub["result"]
        real_scenario = real["result"]
        assert isinstance(stub_scenario, dict) and isinstance(real_scenario, dict)
        self.assertEqual("openpgp", real_scenario["mode"])
        for level in real_scenario["levels"]:
            self.assertEqual(0, level["errors"])
            self.assertGreater(level["ops_per_second"], 0.0)
        # Real gpg signing dominates issuance: milliseconds versus the stub's
        # microseconds, so the full signed operation is bound by cryptography.
        self.assertGreater(
            real_scenario["issue_ms"]["p50"], 20.0 * stub_scenario["issue_ms"]["p50"]
        )
        self.assertGreater(real_scenario["issue_ms"]["p50"], 1.0)


def _dev_temporal_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 17233), timeout=0.5):
            return True
    except OSError:
        return False


@unittest.skipUnless(_dev_temporal_reachable(), "dev Temporal stack is not running")
class TemporalSmokeTests(unittest.TestCase):
    def test_temporal_scenario_completes_workflows(self) -> None:
        result = _run_driver(["temporal", "--workflows", "4", "--max-concurrency", "2"])
        scenario = result["result"]
        assert isinstance(scenario, dict)
        self.assertEqual("temporal-interactive-worker", scenario["scenario"])
        self.assertEqual(4, scenario["workflows_requested"])
        self.assertEqual(4, scenario["workflows_completed"])
        self.assertEqual([], scenario["failures"])
        self.assertEqual(4, scenario["dispatch_receipts"])
        self.assertGreater(scenario["workflows_per_second"], 0.0)
        self.assertGreater(scenario["completion_latency_ms"]["p50"], 0.0)


if __name__ == "__main__":
    unittest.main()
