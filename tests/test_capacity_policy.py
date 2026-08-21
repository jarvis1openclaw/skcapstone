from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "check_capacity.py"
SPEC = importlib.util.spec_from_file_location("check_capacity", MODULE_PATH)
assert SPEC and SPEC.loader
check_capacity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_capacity)


class CapacityPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = json.loads(
            (ROOT / "config/platform/chiap01-capacity-policy.json").read_text()
        )
        self.snapshot = json.loads(
            (ROOT / "tests/fixtures/platform/chiap01-2026-08-19.json").read_text()
        )

    def test_current_snapshot_passes(self) -> None:
        result = check_capacity.evaluate(self.policy, self.snapshot)
        self.assertEqual("PASS", result["status"])
        self.assertEqual("PASS", result["ports"]["status"])
        self.assertEqual("PASS", result["docker_volumes"]["status"])

    def test_warning_threshold_without_consuming_disk(self) -> None:
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["mounts"][0]["used_percent"] = 82
        result = check_capacity.evaluate(self.policy, snapshot)
        self.assertEqual("WARNING", result["status"])
        self.assertEqual("WARNING", result["mounts"][0]["status"])

    def test_critical_threshold_without_consuming_disk(self) -> None:
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["mounts"][0]["used_percent"] = 92
        result = check_capacity.evaluate(self.policy, snapshot)
        self.assertEqual("CRITICAL", result["status"])
        self.assertEqual("CRITICAL", result["mounts"][0]["status"])

    def test_warning_free_space_floor_without_high_used_percent(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["mount_checks"][0]["warning_available_after_reserve_gib"] = 370
        policy["mount_checks"][0]["critical_available_after_reserve_gib"] = 350
        result = check_capacity.evaluate(policy, self.snapshot)
        self.assertLess(
            self.snapshot["mounts"][0]["used_percent"],
            policy["mount_checks"][0]["warning_used_percent"],
        )
        self.assertEqual("WARNING", result["mounts"][0]["status"])

    def test_critical_free_space_floor_without_high_used_percent(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["mount_checks"][0]["warning_available_after_reserve_gib"] = 400
        policy["mount_checks"][0]["critical_available_after_reserve_gib"] = 370
        result = check_capacity.evaluate(policy, self.snapshot)
        self.assertLess(
            self.snapshot["mounts"][0]["used_percent"],
            policy["mount_checks"][0]["warning_used_percent"],
        )
        self.assertEqual("CRITICAL", result["mounts"][0]["status"])

    def test_malformed_snapshot_is_rejected(self) -> None:
        snapshot = copy.deepcopy(self.snapshot)
        del snapshot["mounts"][0]["available_bytes"]
        with self.assertRaises(check_capacity.InputError):
            check_capacity.evaluate(self.policy, snapshot)

    def test_unsupported_snapshot_schema_is_rejected(self) -> None:
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["schema_version"] = 2
        with self.assertRaises(check_capacity.InputError):
            check_capacity.evaluate(self.policy, snapshot)

    def test_most_specific_relevant_mount_is_selected(self) -> None:
        nfs_mount = check_capacity.select_mount(
            "/mnt/cloud/onedrive/projects/DAVE-AI/sklegal", self.snapshot["mounts"]
        )
        root_mount = check_capacity.select_mount("/mnt/cloud", self.snapshot["mounts"])
        self.assertEqual("/mnt/cloud/onedrive", nfs_mount["target"])
        self.assertEqual("/", root_mount["target"])

    def test_port_conflict_is_critical(self) -> None:
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["listening_tcp_ports"].append(18080)
        result = check_capacity.evaluate(self.policy, snapshot)
        self.assertEqual("CRITICAL", result["status"])
        self.assertEqual("sklegal-api", result["ports"]["conflicts"][0]["service"])

    def test_orphan_docker_volume_is_warning(self) -> None:
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["docker"]["volumes"].append("unreferenced-test-volume")
        result = check_capacity.evaluate(self.policy, snapshot)
        self.assertEqual("WARNING", result["status"])
        self.assertEqual(
            ["unreferenced-test-volume"],
            result["docker_volumes"]["orphan_volumes"],
        )

    def test_active_reference_to_missing_docker_volume_is_critical(self) -> None:
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["docker"]["active_containers"][0]["named_volumes"] = [
            "missing-test-volume"
        ]
        result = check_capacity.evaluate(self.policy, snapshot)
        self.assertEqual("CRITICAL", result["status"])
        self.assertEqual(
            ["missing-test-volume"],
            result["docker_volumes"]["missing_volumes"],
        )

    def test_malformed_docker_volume_entry_is_rejected(self) -> None:
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["docker"]["volumes"] = [{"name": "not-a-string"}]
        with self.assertRaises(check_capacity.InputError):
            check_capacity.evaluate(self.policy, snapshot)


if __name__ == "__main__":
    unittest.main()
