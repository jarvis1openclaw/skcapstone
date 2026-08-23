from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _development_container_inventory() -> tuple[str, ...]:
    compose = REPO_ROOT / "deploy" / "chiap01" / "compose.dev.yml"
    process = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "sklegal-dev",
            "--file",
            str(compose),
            "ps",
            "--status",
            "running",
            "--quiet",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    container_ids = tuple(sorted(process.stdout.split()))
    if not container_ids:
        return ()
    inspected = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .Id}} {{json .Config.Image}} {{json .Config.Labels}} "
            "{{json .Mounts}} {{json .State.StartedAt}}",
            *container_ids,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    identities = tuple(sorted(inspected.stdout.splitlines()))
    if len(identities) != len(container_ids):
        raise RuntimeError("development container inventory is incomplete")
    return identities


DEVELOPMENT_CONTAINER_BASELINE = _development_container_inventory()


class FoundationContractTests(unittest.TestCase):
    def test_approved_workspace_shape_exists(self) -> None:
        required = (
            "apps/web",
            "services/api",
            "services/worker",
            "packages/domain",
            "packages/policies",
            "packages/audit",
            "packages/model_gateway",
            "packages/retrieval",
            "packages/connectors/hammertime",
            "workflows",
            "agents/specs",
            "agents/schemas",
            "jurisdiction_packs",
            "evals",
            "migrations",
            "deploy/chiap01",
        )
        for relative in required:
            with self.subTest(relative=relative):
                self.assertTrue((REPO_ROOT / relative).is_dir())

    def test_python_workspace_packages_import(self) -> None:
        import sklegal_api
        import sklegal_audit
        import sklegal_domain
        import sklegal_model_gateway
        import sklegal_policies
        import sklegal_retrieval
        import sklegal_worker

        names = {
            sklegal_api.PACKAGE_NAME,
            sklegal_audit.PACKAGE_NAME,
            sklegal_domain.PACKAGE_NAME,
            sklegal_model_gateway.PACKAGE_NAME,
            sklegal_policies.PACKAGE_NAME,
            sklegal_retrieval.PACKAGE_NAME,
            sklegal_worker.PACKAGE_NAME,
        }
        self.assertEqual(7, len(names))

    def test_compose_contract_is_valid_and_loopback_only(self) -> None:
        compose = REPO_ROOT / "deploy" / "chiap01" / "compose.dev.yml"
        process = subprocess.run(
            ["docker", "compose", "--file", str(compose), "config", "--format", "json"],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(process.stdout)
        self.assertEqual({"postgres", "temporal"}, set(payload["services"]))
        for service in payload["services"].values():
            for port in service.get("ports", []):
                self.assertEqual("127.0.0.1", port["host_ip"])

    def test_foundation_checks_preserve_development_container_inventory(self) -> None:
        self.assertEqual(
            DEVELOPMENT_CONTAINER_BASELINE,
            _development_container_inventory(),
            "foundation checks changed pre-existing development container ownership",
        )

    def test_development_container_inventory_detects_added_or_restarted_fixture(
        self,
    ) -> None:
        synthetic_identity = '"synthetic" "image" {} [] "2099-01-01T00:00:00Z"'
        self.assertNotEqual(
            DEVELOPMENT_CONTAINER_BASELINE,
            (*DEVELOPMENT_CONTAINER_BASELINE, synthetic_identity),
        )

    def test_reproducible_lockfiles_exist(self) -> None:
        self.assertTrue((REPO_ROOT / "uv.lock").is_file())
        self.assertTrue((REPO_ROOT / "package-lock.json").is_file())
        lock = json.loads((REPO_ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(3, lock["lockfileVersion"])


if __name__ == "__main__":
    unittest.main()
