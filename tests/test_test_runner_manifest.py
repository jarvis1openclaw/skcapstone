"""Manifest parity contract for the SKLegal quality gate.

Proves that every ``test_*.py`` module on disk is discovered and executed by
``scripts/run_checks.sh``. A module that is not executed by the gate fails
this contract, so no test module can be silently skipped or left unwired.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_CHECKS = REPO_ROOT / "scripts" / "run_checks.sh"

UNIT_DISCOVERY = [
    "find",
    "tests",
    "-type",
    "f",
    "-name",
    "test_*.py",
    "-not",
    "-path",
    "*/__pycache__/*",
    "-not",
    "-path",
    "tests/integration/*",
    "-print0",
]
INTEGRATION_DISCOVERY = [
    "find",
    "tests/integration",
    "-type",
    "f",
    "-name",
    "test_*.py",
    "-not",
    "-path",
    "*/__pycache__/*",
    "-print0",
]


def _run_discovery(argv: list[str]) -> set[str]:
    result = subprocess.run(
        argv,
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return {entry for entry in result.stdout.decode("utf-8").split("\0") if entry}


def _gate_discovered_modules() -> set[str]:
    return _run_discovery(UNIT_DISCOVERY) | _run_discovery(INTEGRATION_DISCOVERY)


def _filesystem_modules() -> set[str]:
    return {
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "tests").rglob("test_*.py")
        if "__pycache__" not in path.parts
    }


class RunnerDiscoveryParityTests(unittest.TestCase):
    """The gate discovery pattern must cover every test module on disk."""

    def test_gate_discovery_matches_filesystem_manifest(self) -> None:
        discovered = _gate_discovered_modules()
        on_disk = _filesystem_modules()
        self.assertGreater(len(on_disk), 0)
        self.assertEqual(
            discovered,
            on_disk,
            "gate discovery and filesystem manifest diverge: "
            f"missing from gate={sorted(on_disk - discovered)} "
            f"unexpected={sorted(discovered - on_disk)}",
        )

    def test_parity_fails_when_a_module_is_excluded(self) -> None:
        # Negative proof: narrowing discovery with an exclusion glob, the way
        # a silently skipping runner would, breaks the manifest comparison.
        unit_modules = sorted(
            module
            for module in _filesystem_modules()
            if not module.startswith("tests/integration/")
        )
        self.assertGreater(len(unit_modules), 0)
        probe = unit_modules[0]
        narrowed = _run_discovery(
            [*UNIT_DISCOVERY[:-1], "-not", "-path", probe, "-print0"]
        )
        narrowed |= _run_discovery(INTEGRATION_DISCOVERY)
        self.assertEqual(narrowed, _filesystem_modules() - {probe})
        self.assertNotEqual(narrowed, _filesystem_modules())


class RunnerWiringTests(unittest.TestCase):
    """The gate must execute every discovered module and fail closed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.script = RUN_CHECKS.read_text(encoding="utf-8")

    def test_gate_uses_discovery_not_a_handwritten_module_list(self) -> None:
        # Discovery must stay rg-free so the gate runs on hosts and CI
        # runners that do not ship ripgrep.
        self.assertIn("find tests", self.script)
        self.assertNotIn("rg --files", self.script)
        self.assertIn("test_*.py", self.script)
        self.assertNotIn("python -m unittest", self.script)
        self.assertNotRegex(self.script, r"tests\.test_[a-z0-9_]+")

    def test_gate_preflights_every_module_and_fails_on_zero_collection(
        self,
    ) -> None:
        self.assertIn("--collect-only", self.script)
        self.assertIn("collection count missing or zero", self.script)
        self.assertIn("discovery found zero eligible modules", self.script)

    def test_gate_executes_every_discovered_module_per_suite(self) -> None:
        self.assertIn('pytest -v "$module"', self.script)
        self.assertIn('run_pytest_modules unit "${modules[@]}"', self.script)
        self.assertIn('run_pytest_modules integration "${modules[@]}"', self.script)


if __name__ == "__main__":
    unittest.main()
