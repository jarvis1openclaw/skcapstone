"""Qualify the brokered host-execution boundary of the clean-room gate.

Covers card 0e436d61: the parent-owned broker authenticates every request
with its token, binds loopback-only endpoints, starts atomically, and is the
only Docker/GPG entry point visible to the contained gate.
"""

from __future__ import annotations

import base64
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import clean_room_check  # noqa: E402


class BrokerHostBoundaryTests(unittest.TestCase):
    def _workspace(self, raw: str) -> Path:
        workspace = Path(raw)
        compose = workspace / "deploy" / "chiap01" / "compose.dev.yml"
        compose.parent.mkdir(parents=True)
        compose.write_text("services: {}\n", encoding="utf-8")
        return workspace

    @staticmethod
    def _request_payload(
        broker: clean_room_check.ExternalBrokerSet,
        *,
        omit: tuple[str, ...] = (),
        **overrides: object,
    ) -> bytes:
        payload: dict[str, object] = {
            "argv": ["ps", "--all"],
            "cwd": str(broker._workspace),
            "gnupg_home": "",
            "stdin": "",
            "token": broker._token,
            "tool": "docker",
        }
        for key in omit:
            payload.pop(key, None)
        payload.update(overrides)
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    def _assert_denied_without_leak(
        self,
        broker: clean_room_check.ExternalBrokerSet,
        raw: bytes,
        *,
        tool: str = "docker",
    ) -> None:
        response = broker._handle_request(tool, raw)
        decoded = json.loads(response)
        self.assertEqual(126, decoded["returncode"])
        self.assertEqual(
            b"request denied\n",
            base64.b64decode(decoded["stderr"]),
        )
        self.assertNotIn(broker._token.encode("utf-8"), response)

    def test_wrong_missing_or_swapped_tokens_are_denied_without_leak(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            broker = clean_room_check.ExternalBrokerSet(self._workspace(raw))
            try:
                baseline = broker.resource_counts["requests_denied"]
                self._assert_denied_without_leak(
                    broker,
                    self._request_payload(broker, token="forged-token"),
                )
                self._assert_denied_without_leak(
                    broker,
                    self._request_payload(broker, omit=("token",)),
                )
                self._assert_denied_without_leak(
                    broker,
                    self._request_payload(broker, unexpected="extra"),
                )
                self._assert_denied_without_leak(
                    broker,
                    self._request_payload(broker),
                    tool="gpg",
                )
                self.assertEqual(
                    baseline + 4,
                    broker.resource_counts["requests_denied"],
                )
            finally:
                self.assertTrue(broker.close())

    def test_broker_endpoints_are_loopback_only_and_distinct(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            broker = clean_room_check.ExternalBrokerSet(self._workspace(raw))
            try:
                environment = broker.environment
                self.assertEqual(
                    {
                        "SKLEGAL_BROKER_TOKEN",
                        "SKLEGAL_DOCKER_BROKER",
                        "SKLEGAL_GPG_BROKER",
                    },
                    set(environment),
                )
                endpoints = {
                    name: environment[name].rsplit(":", 1)
                    for name in ("SKLEGAL_DOCKER_BROKER", "SKLEGAL_GPG_BROKER")
                }
                for host, raw_port in endpoints.values():
                    self.assertEqual("127.0.0.1", host)
                    self.assertGreater(int(raw_port), 0)
                self.assertNotEqual(
                    endpoints["SKLEGAL_DOCKER_BROKER"][1],
                    endpoints["SKLEGAL_GPG_BROKER"][1],
                )
                token = environment["SKLEGAL_BROKER_TOKEN"]
                self.assertGreaterEqual(len(token), 32)
                for endpoint in environment.values():
                    if endpoint != token:
                        self.assertNotIn(token, endpoint)
            finally:
                self.assertTrue(broker.close())

    def test_broker_start_is_atomic_and_never_resumes_after_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            broker = clean_room_check.ExternalBrokerSet(self._workspace(raw))
            self.assertIsNotNone(broker._docker)
            self.assertTrue(broker._docker.close())
            with self.assertRaisesRegex(
                clean_room_check.CleanRoomError,
                "broker_start_failed",
            ):
                broker.start()
            self.assertFalse(broker._started)
            with self.assertRaisesRegex(
                clean_room_check.CleanRoomError,
                "broker_start_failed",
            ):
                broker.start()
            self.assertTrue(broker.close())

    def test_shims_are_the_only_docker_and_gpg_entry_points(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            workspace = self._workspace(raw)
            broker = clean_room_check.ExternalBrokerSet(workspace)
            try:
                shim_directory = workspace / ".clean-bin"
                for name in ("docker", "gpg"):
                    shim = shim_directory / name
                    self.assertTrue(shim.is_file())
                    self.assertFalse(shim.is_symlink())
                    self.assertEqual(0o700, stat.S_IMODE(shim.stat().st_mode))
                    self.assertEqual(
                        clean_room_check._BROKER_SHIM.encode("utf-8"),
                        shim.read_bytes(),
                    )
                with tempfile.TemporaryDirectory(dir="/tmp") as cache:
                    child_environment = clean_room_check._child_environment(
                        target=workspace,
                        uv_cache=Path(cache) / "uv",
                        npm_cache=Path(cache) / "npm",
                        broker_environment=broker.environment,
                    )
                self.assertTrue(
                    child_environment["PATH"].startswith(f"{shim_directory}:"),
                )
                self.assertEqual(
                    broker.environment["SKLEGAL_BROKER_TOKEN"],
                    child_environment["SKLEGAL_BROKER_TOKEN"],
                )
            finally:
                self.assertTrue(broker.close())


if __name__ == "__main__":
    unittest.main()
