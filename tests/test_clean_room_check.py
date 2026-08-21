from __future__ import annotations

import contextlib
import io
import json
import math
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import clean_room_check  # noqa: E402


class FakeProcess:
    def __init__(self, *outcomes: int | BaseException) -> None:
        self.pid = 4242
        self.outcomes = list(outcomes)
        self.wait_timeouts: list[float] = []
        self.return_code: int | None = None

    def poll(self) -> int | None:
        return self.return_code

    def wait(self, timeout: float) -> int:
        self.wait_timeouts.append(timeout)
        if not self.outcomes:
            raise subprocess.TimeoutExpired(["systemd-run"], timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        self.return_code = outcome
        return outcome


class FakeContainment:
    def __init__(
        self,
        process: FakeProcess,
        *,
        preflight_error: clean_room_check.CleanRoomError | None = None,
        extinction_available: bool = True,
        extinct_after_kill: bool = False,
        ensure_failures: int = 0,
        on_start: Callable[[Path], None] | None = None,
    ) -> None:
        self.process = process
        self.preflight_error = preflight_error
        self.extinction_available = extinction_available
        self.extinct_after_kill = extinct_after_kill
        self.ensure_failures = ensure_failures
        self.on_start = on_start
        self.signals: list[signal.Signals] = []
        self.ensure_calls = 0
        self.start_call: dict[str, Any] | None = None

    def preflight(self) -> None:
        if self.preflight_error is not None:
            raise self.preflight_error

    def start(
        self,
        *,
        target: Path,
        child_environment: dict[str, str],
        unit_name: str,
        runtime_seconds: float,
        register: Callable[[clean_room_check.ContainedUnit], None] | None = None,
    ) -> clean_room_check.ContainedUnit:
        self.start_call = {
            "target": target,
            "child_environment": dict(child_environment),
            "unit_name": unit_name,
            "runtime_seconds": runtime_seconds,
            "copied_payload": (target / "payload.txt").read_bytes()
            if (target / "payload.txt").exists()
            else None,
            "node_modules_exists": (target / "node_modules").exists(),
            "build_exists": (target / "build").exists(),
        }
        contained = clean_room_check.ContainedUnit(
            unit_name=unit_name,
            process=self.process,
            control_group=f"/synthetic/{unit_name}",
        )
        if register is not None:
            register(contained)
        if self.on_start is not None:
            self.on_start(target)
        return contained

    def signal(
        self,
        contained: clean_room_check.ContainedUnit,
        sent: signal.Signals,
    ) -> None:
        del contained
        self.signals.append(sent)

    def ensure_extinct(self, contained: clean_room_check.ContainedUnit) -> bool:
        del contained
        self.ensure_calls += 1
        if self.ensure_failures:
            self.ensure_failures -= 1
            raise RuntimeError("synthetic containment state outage")
        if self.extinct_after_kill and signal.SIGKILL in self.signals:
            return True
        return self.extinction_available and self.process.return_code is not None


class FakeBrokers:
    def __init__(self, workspace: Path, *, cleanup: bool = True) -> None:
        self.environment = {
            "SKLEGAL_BROKER_TOKEN": "synthetic-broker-token",
            "SKLEGAL_DOCKER_BROKER": "127.0.0.1:41001",
            "SKLEGAL_GPG_BROKER": "127.0.0.1:41002",
        }
        self.workspace = workspace
        self.cleanup = cleanup
        self.closed = False
        self.started = False

    def start(self) -> None:
        self.started = True

    @property
    def resource_counts(self) -> dict[str, int]:
        return {key: 0 for key in clean_room_check.BROKER_RESOURCE_KEYS}

    def close(self) -> bool:
        self.closed = True
        return self.cleanup


class CleanRoomCheckTests(unittest.TestCase):
    def _fixture(
        self,
        *,
        process: FakeProcess,
        source_entries: tuple[str, ...] = ("Makefile", "payload.txt"),
        environment_changes: dict[str, str] | None = None,
        progress_interval: float = 30.0,
        source_timeout: float = clean_room_check.SOURCE_TIMEOUT_SECONDS,
        containment_options: dict[str, Any] | None = None,
        after_copy_hook: Callable[[Path, Path], None] | None = None,
        source_runner_hook: Callable[[Path], None] | None = None,
        broker_cleanup: bool = True,
        broker_factory: Callable[[Path], clean_room_check.BrokerRuntime] | None = None,
    ) -> tuple[
        clean_room_check.CleanRoomReceipt,
        FakeContainment,
        list[dict[str, object]],
        Path,
        Path,
    ]:
        source_temporary = tempfile.TemporaryDirectory()
        scratch_temporary = tempfile.TemporaryDirectory()
        cache_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(source_temporary.cleanup)
        self.addCleanup(scratch_temporary.cleanup)
        self.addCleanup(cache_temporary.cleanup)
        source = Path(source_temporary.name) / "repository"
        source.mkdir()
        scratch = Path(scratch_temporary.name)
        caches = Path(cache_temporary.name)
        uv_cache = caches / "uv"
        npm_cache = caches / "npm"
        uv_cache.mkdir()
        npm_cache.mkdir()
        (source / "Makefile").write_text("check:\n\ttrue\n", encoding="utf-8")
        (source / "payload.txt").write_text("synthetic payload\n", encoding="utf-8")
        git_stdout = b"\0".join(item.encode() for item in source_entries) + b"\0"
        events: list[dict[str, object]] = []

        def source_runner(
            argv: list[str],
            *,
            cwd: Path,
            env: dict[str, str],
            check: bool,
            capture_output: bool,
            pass_fds: tuple[int, ...],
            timeout: float,
        ) -> subprocess.CompletedProcess[bytes]:
            self.assertEqual(
                [
                    "/usr/bin/git",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "core.untrackedCache=false",
                    "-c",
                    "core.preloadIndex=false",
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ],
                argv,
            )
            self.assertEqual(1, len(pass_fds))
            self.assertEqual(Path(f"/proc/self/fd/{pass_fds[0]}"), cwd)
            self.assertEqual(source, cwd.resolve(strict=True))
            self.assertEqual(
                {
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_OPTIONAL_LOCKS": "0",
                    "GIT_TERMINAL_PROMPT": "0",
                    "HOME": "/nonexistent",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                },
                env,
            )
            self.assertTrue(check)
            self.assertTrue(capture_output)
            self.assertEqual(source_timeout, timeout)
            self.assertTrue(math.isfinite(timeout))
            self.assertGreater(timeout, 0)
            if source_runner_hook is not None:
                source_runner_hook(source)
            return subprocess.CompletedProcess(argv, 0, stdout=git_stdout)

        environment = {
            "SKLEGAL_CLEAN_ROOM_ROOT": str(scratch),
            "UV_CACHE_DIR": str(uv_cache),
            "npm_config_cache": str(npm_cache),
        }
        environment.update(environment_changes or {})
        fake_containment = FakeContainment(
            process,
            **(containment_options or {}),
        )
        with (
            mock.patch.object(
                clean_room_check,
                "resolve_scratch_root",
                return_value=scratch,
            ),
            mock.patch.object(
                clean_room_check,
                "_validate_trusted_executables",
            ),
        ):
            receipt = clean_room_check.run(
                repo_root=source,
                environment=environment,
                source_runner=source_runner,
                containment=fake_containment,
                broker_factory=broker_factory
                or (
                    lambda workspace: FakeBrokers(
                        workspace,
                        cleanup=broker_cleanup,
                    )
                ),
                after_copy_hook=after_copy_hook,
                check_timeout=10.0,
                terminate_grace=2.0,
                kill_grace=1.0,
                progress_interval=progress_interval,
                source_timeout=source_timeout,
                emit=events.append,
            )
        return receipt, fake_containment, events, source, scratch

    def test_local_scratch_copy_and_gate_contract_are_exact(self) -> None:
        receipt, containment, events, _, scratch = self._fixture(process=FakeProcess(0))
        call = containment.start_call
        self.assertIsNotNone(call)
        assert call is not None
        target = call["target"]
        self.assertEqual("passed", receipt.status)
        self.assertEqual(0, receipt.exit_code)
        self.assertEqual(2, receipt.source_file_count)
        self.assertEqual(scratch, target.parent)
        self.assertFalse(target.exists())
        self.assertEqual(b"synthetic payload\n", call["copied_payload"])
        self.assertFalse(call["node_modules_exists"])
        self.assertFalse(call["build_exists"])
        self.assertEqual(10.0, call["runtime_seconds"])
        self.assertRegex(
            call["unit_name"],
            r"^sklegal-cleanroom-[0-9]+-[0-9a-f]+-[0-9a-f]+\.service$",
        )
        self.assertEqual("receipt", events[-1]["event"])
        self.assertEqual("sklegal-clean-room-receipt/v6", events[-1]["schema"])
        self.assertEqual("passed", events[-1]["status"])
        self.assertTrue(receipt.temporary_cleaned)
        self.assertTrue(receipt.containment_extinct)
        self.assertTrue(receipt.external_brokers_cleaned)
        self.assertGreaterEqual(containment.ensure_calls, 1)
        self.assertEqual(FakeBrokers(target).resource_counts, receipt.broker_resources)
        self.assertEqual(receipt.broker_resources, events[-1]["broker_resources"])
        self.assertNotIn("TOKEN", json.dumps(events[-1], sort_keys=True).upper())

    def test_ambient_credentials_configs_and_startup_controls_never_cross(
        self,
    ) -> None:
        hostile = {
            "PATH": "/tmp/hostile-bin",
            "BASH_ENV": "/tmp/hostile-bash-env",
            "ENV": "/tmp/hostile-sh-env",
            "MAKEFLAGS": "--eval=hostile",
            "NODE_OPTIONS": "--require=/tmp/hostile.js",
            "PYTHONPATH": "/tmp/hostile-python",
            "GIT_CONFIG_GLOBAL": "/tmp/hostile-gitconfig",
            "NPM_CONFIG_USERCONFIG": "/tmp/hostile-npmrc",
            "HTTP_PROXY": "http://credential.invalid",
            "HTTPS_PROXY": "http://credential.invalid",
            "SSH_AUTH_SOCK": "/tmp/hostile-agent",
            "DOCKER_CONFIG": "/tmp/hostile-docker",
            "XDG_CONFIG_HOME": "/tmp/hostile-config",
        }
        aws_sensitive_name = next(
            name
            for name in clean_room_check.DEFAULT_UNSET_ENVIRONMENT
            if name.startswith("AWS_") and name.endswith("_ACCESS_KEY")
        )
        hostile[aws_sensitive_name] = "synthetic-marker"
        receipt, containment, _, _, _ = self._fixture(
            process=FakeProcess(0),
            environment_changes=hostile,
        )
        self.assertEqual("passed", receipt.status)
        assert containment.start_call is not None
        child = containment.start_call["child_environment"]
        self.assertEqual(
            {
                "HOME",
                "LANG",
                "LC_ALL",
                "PATH",
                "SKLEGAL_BROKER_TOKEN",
                "SKLEGAL_DOCKER_BROKER",
                "SKLEGAL_GPG_BROKER",
                "TMPDIR",
                "UV_CACHE_DIR",
                "UV_LINK_MODE",
                "npm_config_cache",
            },
            set(child),
        )
        target = containment.start_call["target"]
        self.assertEqual(f"{target / '.clean-bin'}:/usr/bin", child["PATH"])
        self.assertTrue(Path(child["HOME"]).is_relative_to(target))
        self.assertTrue(Path(child["TMPDIR"]).is_relative_to(target))
        self.assertTrue(
            all(value not in json.dumps(child) for value in hostile.values())
        )
        self.assertNotIn("synthetic-broker-token", json.dumps(receipt.as_event()))

    def test_systemd_argv_is_absolute_closed_and_cgroup_bound(self) -> None:
        backend = object.__new__(clean_room_check.SystemdContainment)
        child = {
            "HOME": "/tmp/work/.clean-home",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/local/bin:/usr/bin",
            "TMPDIR": "/tmp/work/.clean-tmp",
            "UV_CACHE_DIR": "/tmp/cache/uv",
            "UV_LINK_MODE": "copy",
            "npm_config_cache": "/tmp/cache/npm",
        }
        argv = backend.build_argv(
            target=Path("/tmp/work"),
            child_environment=child,
            unit_name="sklegal-cleanroom-1-2-3.service",
            runtime_seconds=10,
        )
        self.assertEqual("/usr/bin/systemd-run", argv[0])
        self.assertIn("--property=ExitType=cgroup", argv)
        self.assertIn("--property=KillMode=control-group", argv)
        self.assertIn("--property=ExecStartPre=/usr/bin/sleep 0.2", argv)
        self.assertIn("--property=RuntimeMaxSec=10s", argv)
        self.assertIn("--property=NoNewPrivileges=yes", argv)
        self.assertIn("--property=PrivateUsers=yes", argv)
        self.assertIn(
            "--property=RestrictAddressFamilies=AF_INET AF_INET6",
            argv,
        )
        self.assertIn("--property=BindPaths=/tmp/work:/tmp/work", argv)
        self.assertIn("--property=ReadOnlyPaths=/tmp", argv)
        self.assertIn(
            "--property=ReadWritePaths=/tmp/work /tmp/cache/uv /tmp/cache/npm",
            argv,
        )
        unset = next(
            item for item in argv if item.startswith("--property=UnsetEnvironment=")
        )
        for name in ("BASH_ENV", "MAKEFLAGS", "NODE_OPTIONS", "SSH_AUTH_SOCK"):
            self.assertIn(name, unset)
        wrapper = [
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/tmp/work/scripts/clean_room_check.py",
            "--landlock-exec",
            "/tmp/work",
            "/tmp/cache/uv",
            "/tmp/cache/npm",
        ]
        self.assertEqual(wrapper, argv[-len(wrapper) :])
        env_index = argv.index("/usr/bin/env")
        self.assertEqual("-i", argv[env_index + 1])
        self.assertEqual(
            [f"{key}={child[key]}" for key in sorted(child)],
            argv[env_index + 2 : -len(wrapper)],
        )
        forbidden = (
            "BASH_ENV",
            "MAKEFLAGS",
            "NODE_OPTIONS",
            "PROXY",
            "SECRET",
            "SSH_AUTH_SOCK",
            "NPM_CONFIG_USERCONFIG",
        )
        self.assertFalse(any(item.startswith(forbidden) for item in argv))

    def test_external_manager_executables_are_absent_from_landlock_allowlist(
        self,
    ) -> None:
        blocked = {
            Path("/usr/bin/docker"),
            Path("/usr/bin/gpg"),
            Path("/usr/bin/gpg2"),
            Path("/usr/bin/gpgconf"),
            Path("/usr/bin/systemctl"),
            Path("/usr/bin/systemd-run"),
        }
        self.assertTrue(blocked.isdisjoint(clean_room_check.LANDLOCK_EXECUTABLES))

    def test_landlock_read_roots_exclude_broad_and_broker_private_paths(self) -> None:
        readable = set(clean_room_check.LANDLOCK_READ_PATHS)
        self.assertNotIn(Path("/"), readable)
        self.assertNotIn(Path("/tmp"), readable)
        self.assertTrue(all(path.is_absolute() for path in readable))

    def test_docker_broker_contract_is_exact_and_disposable_only(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            workspace = Path(raw)
            compose = workspace / "deploy" / "chiap01" / "compose.dev.yml"
            compose.parent.mkdir(parents=True)
            compose.write_text("services: {}\n", encoding="utf-8")
            container = "sklegal-s102-4242-deadbeef"
            new_container = "sklegal-s102-4242-feedface"
            tracked = {container}
            valid_run = [
                "run",
                "--detach",
                "--rm",
                "--name",
                new_container,
                "--label",
                "com.sklegal.test-card=SKL-S1-02",
                "--network",
                "none",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,noexec,nosuid,size=512m",
                "--env",
                "POSTGRES_DB=sklegal",
                "--env",
                "POSTGRES_USER=postgres",
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                clean_room_check.POSTGRES_IMAGE,
            ]

            allowed = (
                ["ps", "--quiet", "--filter", "name=^skmem-pg$"],
                [
                    "compose",
                    "--file",
                    str(compose),
                    "config",
                    "--format",
                    "json",
                ],
                ["exec", container, "pg_isready", "--username", "postgres"],
                [
                    "exec",
                    "-i",
                    container,
                    "psql",
                    "--no-psqlrc",
                    "--command",
                    "SELECT 1",
                ],
                ["inspect", container],
                ["rm", "--force", container],
                valid_run,
            )
            for arguments in allowed:
                with self.subTest(arguments=arguments):
                    clean_room_check._validate_docker_command(
                        arguments,
                        cwd=workspace,
                        workspace=workspace,
                        tracked_containers=tracked,
                    )

            denied = (
                ["image", "ls"],
                ["run", "alpine:latest"],
                [
                    "--mount",
                    "type=bind,source=/,target=/host",
                    *valid_run,
                ],
                ["host" if value == "none" else value for value in valid_run],
                ["exec", container, "sh", "-c", "true"],
                [
                    "compose",
                    "--file",
                    "/etc/passwd",
                    "config",
                    "--format",
                    "json",
                ],
                ["rm", "--force", "untracked"],
            )
            for arguments in denied:
                with (
                    self.subTest(arguments=arguments),
                    self.assertRaisesRegex(
                        clean_room_check.CleanRoomError,
                        "broker_request_denied",
                    ),
                ):
                    clean_room_check._validate_docker_command(
                        arguments,
                        cwd=workspace,
                        workspace=workspace,
                        tracked_containers=tracked,
                    )

    def test_broker_allows_only_one_container_ever_and_injects_hard_limits(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            workspace = Path(raw)
            compose = workspace / "deploy" / "chiap01" / "compose.dev.yml"
            compose.parent.mkdir(parents=True)
            compose.write_text("services: {}\n", encoding="utf-8")
            first = "sklegal-s102-4242-deadbeef"
            second = "sklegal-s102-4242-feedface"

            def run_arguments(name: str) -> list[str]:
                return [
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    name,
                    "--label",
                    "com.sklegal.test-card=SKL-S1-02",
                    "--network",
                    "none",
                    "--tmpfs",
                    "/var/lib/postgresql/data:rw,noexec,nosuid,size=512m",
                    "--env",
                    "POSTGRES_DB=sklegal",
                    "--env",
                    "POSTGRES_USER=postgres",
                    "--env",
                    "POSTGRES_HOST_AUTH_METHOD=trust",
                    clean_room_check.POSTGRES_IMAGE,
                ]

            broker = clean_room_check.ExternalBrokerSet(workspace)
            captured: list[list[str]] = []

            def external(
                argv: list[str],
                **kwargs: object,
            ) -> subprocess.CompletedProcess[bytes]:
                del kwargs
                captured.append(argv)
                return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

            try:
                with mock.patch.object(broker, "_run_external", side_effect=external):
                    broker._docker_request(
                        run_arguments(first), cwd=workspace, stdin=b""
                    )
                    broker._docker_request(
                        ["rm", "--force", first], cwd=workspace, stdin=b""
                    )
                    with self.assertRaisesRegex(
                        clean_room_check.CleanRoomError,
                        "broker_request_denied",
                    ):
                        broker._docker_request(
                            run_arguments(second), cwd=workspace, stdin=b""
                        )
                actual_run = next(
                    argv for argv in captured if argv[:2] == ["/usr/bin/docker", "run"]
                )
                for expected in (
                    "--cpus",
                    "--memory",
                    "--memory-swap",
                    "--pids-limit",
                    "--ulimit",
                    "--entrypoint",
                    "/usr/bin/timeout",
                    "-s",
                    "TERM",
                    "-k",
                    "30",
                    "/usr/local/bin/docker-entrypoint.sh",
                    "postgres",
                ):
                    self.assertIn(expected, actual_run)
                self.assertEqual(1, broker.resource_counts["containers_created"])
            finally:
                self.assertTrue(broker.close())

    def test_broker_budgets_requests_files_outputs_workers_and_one_keyring(
        self,
    ) -> None:
        limits = clean_room_check._BrokerLimits(
            max_requests=2,
            max_request_bytes=4096,
            max_response_bytes=4096,
            max_file_bytes=32,
            max_output_bytes=32,
            max_workers=1,
            max_external_processes=1,
        )
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            workspace = Path(raw)
            compose = workspace / "deploy" / "chiap01" / "compose.dev.yml"
            compose.parent.mkdir(parents=True)
            compose.write_text("services: {}\n", encoding="utf-8")
            first_home = workspace / "synthetic-gnupg"
            second_home = workspace / "other-gnupg"
            first_home.mkdir(mode=0o700)
            second_home.mkdir(mode=0o700)
            broker = clean_room_check.ExternalBrokerSet(workspace, limits=limits)

            def request() -> bytes:
                return json.dumps(
                    {
                        "argv": [
                            "compose",
                            "--file",
                            str(compose),
                            "config",
                            "--format",
                            "json",
                        ],
                        "cwd": str(workspace),
                        "gnupg_home": "",
                        "stdin": "",
                        "token": broker._token,
                        "tool": "docker",
                    },
                    separators=(",", ":"),
                ).encode()

            success = clean_room_check._completed_response(
                subprocess.CompletedProcess([], 0, stdout=b"{}", stderr=b"")
            )
            try:
                with mock.patch.object(broker, "_docker_request", return_value=success):
                    self.assertEqual(
                        0,
                        json.loads(broker._handle_request("docker", request()))[
                            "returncode"
                        ],
                    )
                    self.assertEqual(
                        0,
                        json.loads(broker._handle_request("docker", request()))[
                            "returncode"
                        ],
                    )
                    denied = json.loads(broker._handle_request("docker", request()))
                self.assertEqual(126, denied["returncode"])
                self.assertEqual(
                    b"broker budget exhausted\n",
                    __import__("base64").b64decode(denied["stderr"]),
                )
                self.assertGreaterEqual(broker.resource_counts["budget_denials"], 1)
                self.assertTrue(broker._budget.enter_external())
                self.assertFalse(broker._budget.enter_external())
                broker._budget.leave_external()

                broker._logical_keyring(first_home)
                with self.assertRaisesRegex(
                    clean_room_check.CleanRoomError,
                    "broker_request_denied",
                ):
                    broker._logical_keyring(second_home)

                oversized = workspace / "oversized.json"
                oversized.write_bytes(b"x" * 33)
                with self.assertRaisesRegex(
                    clean_room_check.CleanRoomError,
                    "broker_request_denied",
                ):
                    broker._snapshot_gpg_input(str(oversized), label="payload")
                self.assertFalse(
                    any(
                        entry.relative.name.endswith("payload")
                        for entry in broker._private_entries
                    )
                )
                self.assertIn(
                    "sys.stdin.buffer.read(4 * 1024 * 1024 + 1)",
                    clean_room_check._BROKER_SHIM,
                )

                oversized_process = subprocess.CompletedProcess(
                    [], 0, stdout=b"x" * 33, stderr=b""
                )
                with self.assertRaisesRegex(
                    clean_room_check.CleanRoomError,
                    "broker_request_denied",
                ):
                    clean_room_check._completed_response(
                        oversized_process,
                        max_output_bytes=limits.max_output_bytes,
                    )
            finally:
                self.assertTrue(broker.close())

        socket_module = __import__("socket")
        struct_module = __import__("struct")
        receiver, sender = socket_module.socketpair()
        try:
            sender.sendall(struct_module.pack("!I", 33))
            with self.assertRaisesRegex(
                clean_room_check.CleanRoomError,
                "broker_request_denied",
            ):
                clean_room_check._recv_frame(receiver, max_bytes=32)
        finally:
            receiver.close()
            sender.close()

        started = threading.Event()
        release = threading.Event()

        def blocking_handler(raw: bytes) -> bytes:
            del raw
            started.set()
            release.wait(timeout=2.0)
            return success

        concurrent_budget = clean_room_check._BrokerBudget(
            clean_room_check._BrokerLimits(
                max_workers=1,
                max_queued_connections=1,
            )
        )
        loopback = clean_room_check._LoopbackBroker(
            blocking_handler,
            budget=concurrent_budget,
        )
        loopback.start()
        clients: list[socket.socket] = []
        try:
            host, raw_port = loopback.endpoint.rsplit(":", 1)
            first = socket_module.create_connection((host, int(raw_port)))
            clients.append(first)
            first.sendall(struct_module.pack("!I", 1) + b"x")
            self.assertTrue(started.wait(timeout=1.0))
            second = socket_module.create_connection((host, int(raw_port)))
            clients.append(second)
            second.sendall(struct_module.pack("!I", 1) + b"x")
            deadline = time.monotonic() + 1.0
            while (
                concurrent_budget.snapshot()["connection_attempts"] < 2
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertEqual(2, concurrent_budget.snapshot()["connection_attempts"])
            third = socket_module.create_connection((host, int(raw_port)))
            clients.append(third)
            third.sendall(struct_module.pack("!I", 1) + b"x")
            size = struct_module.unpack("!I", third.recv(4))[0]
            denied = json.loads(third.recv(size))
            self.assertEqual(126, denied["returncode"])
            self.assertLessEqual(loopback.peak_workers, 1)
            release.set()
        finally:
            release.set()
            for client in clients:
                client.close()
            self.assertTrue(loopback.close())

    def test_wire_budget_counts_every_byte_and_uses_a_fixed_worker_pool(self) -> None:
        limits = clean_room_check._BrokerLimits(
            max_workers=2,
            max_queued_connections=2,
            max_external_processes=1,
            max_requests=512,
            max_request_bytes=4096,
            max_response_bytes=128 * 1024,
            max_frame_bytes=4096,
            max_stdin_bytes=32,
            max_file_bytes=32,
            max_output_bytes=4096,
        )
        budget = clean_room_check._BrokerBudget(limits)
        response = clean_room_check._completed_response(
            subprocess.CompletedProcess([], 0, stdout=b"ok", stderr=b"")
        )

        def handler(raw: bytes) -> bytes:
            if raw == b"e":
                raise RuntimeError("synthetic handler payload")
            return response if raw == b"x" else clean_room_check._denied_response()

        broker = clean_room_check._LoopbackBroker(
            handler,
            budget=budget,
        )
        broker.start()

        def exchange(payload: bytes, *, declared: int | None = None) -> bytes:
            host, raw_port = broker.endpoint.rsplit(":", 1)
            with socket.create_connection((host, int(raw_port)), timeout=2.0) as client:
                client.sendall(
                    struct.pack("!I", len(payload) if declared is None else declared)
                )
                if payload:
                    client.sendall(payload)
                size = struct.unpack("!I", clean_room_check._recv_exact(client, 4))[0]
                return clean_room_check._recv_exact(client, size)

        try:
            initial_workers = tuple(broker._workers)
            self.assertEqual(limits.max_workers, len(initial_workers))
            for _ in range(300):
                self.assertEqual(0, json.loads(exchange(b"x"))["returncode"])
            denied = exchange(b"", declared=limits.max_frame_bytes + 1)
            self.assertEqual(126, json.loads(denied)["returncode"])
            host, raw_port = broker.endpoint.rsplit(":", 1)
            with socket.create_connection((host, int(raw_port)), timeout=2.0) as client:
                client.sendall(struct.pack("!I", 3) + b"xy")
                client.shutdown(socket.SHUT_WR)
                size = struct.unpack("!I", clean_room_check._recv_exact(client, 4))[0]
                truncated_denial = clean_room_check._recv_exact(client, size)
            self.assertEqual(126, json.loads(truncated_denial)["returncode"])
            error_denial = exchange(b"e")
            self.assertEqual(126, json.loads(error_denial)["returncode"])
            self.assertEqual(initial_workers, tuple(broker._workers))
            deadline = time.monotonic() + 1.0
            while broker._connections and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(broker._connections)
            self.assertTrue(broker._queue.empty())
            counts = budget.snapshot()
            self.assertEqual(303, counts["connection_attempts"])
            self.assertEqual(301, counts["requests_accepted"])
            self.assertGreaterEqual(counts["requests_denied"], 3)
            self.assertEqual(300 * 5 + 4 + 6 + 5, counts["wire_bytes_received"])
            self.assertEqual(
                300 * (len(response) + 4)
                + 3 * (len(clean_room_check._denied_response()) + 4),
                counts["wire_bytes_sent"],
            )
        finally:
            self.assertTrue(broker.close())
            self.assertTrue(broker.close())

        exhausted_budget = clean_room_check._BrokerBudget(
            clean_room_check._BrokerLimits(
                max_workers=1,
                max_queued_connections=1,
                max_external_processes=1,
                max_requests=1,
                max_request_bytes=4,
                max_response_bytes=4096,
                max_frame_bytes=4096,
                max_stdin_bytes=32,
                max_file_bytes=32,
                max_output_bytes=4096,
            )
        )
        exhausted = clean_room_check._LoopbackBroker(
            lambda raw: response,
            budget=exhausted_budget,
        )
        exhausted.start()
        try:
            host, raw_port = exhausted.endpoint.rsplit(":", 1)
            with socket.create_connection((host, int(raw_port)), timeout=2.0) as client:
                sentinel = b"SECRET_WIRE_SENTINEL"
                client.sendall(struct.pack("!I", len(sentinel)) + sentinel)
                size = struct.unpack("!I", clean_room_check._recv_exact(client, 4))[0]
                denial = clean_room_check._recv_exact(client, size)
            self.assertEqual(126, json.loads(denial)["returncode"])
            self.assertNotIn(sentinel, denial)
            exhausted_counts = exhausted_budget.snapshot()
            self.assertEqual(4, exhausted_counts["wire_bytes_received"])
            self.assertLessEqual(
                exhausted_counts["wire_bytes_received"],
                exhausted_budget.limits.max_request_bytes,
            )
            self.assertGreaterEqual(exhausted_counts["budget_denials"], 1)
            self.assertGreaterEqual(exhausted_counts["requests_denied"], 1)
        finally:
            self.assertTrue(exhausted.close())

    def test_failed_private_stages_remove_partials_and_keep_ledger_exact(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            workspace = Path(raw)
            compose = workspace / "deploy" / "chiap01" / "compose.dev.yml"
            compose.parent.mkdir(parents=True)
            compose.write_text("services: {}\n", encoding="utf-8")
            source = workspace / "synthetic-input"
            source.write_bytes(b"synthetic")
            broker = clean_room_check.ExternalBrokerSet(workspace)
            private_root = broker._private_workspace.path
            baseline = broker.resource_counts
            self.assertEqual(1, baseline["private_files_current"])
            self.assertEqual(compose.stat().st_size, baseline["private_bytes_current"])
            real_open = os.open

            def fail_staged_reopen(
                path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                flags: int,
                *args: object,
                **kwargs: object,
            ) -> int:
                if (
                    isinstance(path, str)
                    and path.startswith("partial-")
                    and flags & os.O_ACCMODE == os.O_RDONLY
                ):
                    raise OSError("synthetic stage reopen failure")
                return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

            try:
                for index in range(3):
                    destination = Path("failed") / f"partial-{index}"
                    with (
                        mock.patch.object(os, "open", side_effect=fail_staged_reopen),
                        self.assertRaisesRegex(
                            clean_room_check.CleanRoomError,
                            "broker_request_denied",
                        ),
                    ):
                        broker._stage_workspace_file(
                            Path("synthetic-input"), destination=destination
                        )
                    self.assertFalse((private_root / destination).exists())
                    self.assertEqual(baseline, broker.resource_counts)

                def fail_keyring_open(
                    path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                    flags: int,
                    *args: object,
                    **kwargs: object,
                ) -> int:
                    if (
                        isinstance(path, str)
                        and path.startswith("gpg-")
                        and flags & os.O_DIRECTORY
                    ):
                        raise OSError("synthetic keyring open failure")
                    return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

                home = workspace / "synthetic-gnupg"
                home.mkdir(mode=0o700)
                with (
                    mock.patch.object(os, "open", side_effect=fail_keyring_open),
                    self.assertRaisesRegex(
                        clean_room_check.CleanRoomError,
                        "broker_request_denied",
                    ),
                ):
                    broker._logical_keyring(home)
                self.assertFalse(any(private_root.glob("gpg-*")))
                self.assertEqual(baseline, broker.resource_counts)
            finally:
                self.assertTrue(broker.close())

        with (
            tempfile.TemporaryDirectory(dir="/tmp") as source_raw,
            tempfile.TemporaryDirectory(dir="/tmp") as destination_raw,
        ):
            source_root = Path(source_raw)
            destination_root = Path(destination_raw)
            (source_root / "payload").write_bytes(b"synthetic")
            source_fd = os.open(source_root, os.O_RDONLY | os.O_DIRECTORY)
            destination_fd = os.open(destination_root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                for _ in range(3):
                    with self.assertRaisesRegex(RuntimeError, "copy fault"):
                        clean_room_check._copy_one(
                            Path("payload"),
                            source_root_fd=source_fd,
                            destination_root_fd=destination_fd,
                            repo_root=source_root,
                            target=destination_root,
                            after_copy_hook=lambda source, destination: (
                                _ for _ in ()
                            ).throw(RuntimeError("copy fault")),
                        )
                    self.assertFalse((destination_root / "payload").exists())
            finally:
                os.close(source_fd)
                os.close(destination_fd)

    def test_sigterm_enters_terminal_receipt_and_restores_handler(self) -> None:
        script = f"""
import json
import os
import signal
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, {str(REPO_ROOT / "scripts")!r})
import clean_room_check

previous = signal.getsignal(signal.SIGTERM)

def emit(event):
    print(json.dumps(event, sort_keys=True), flush=True)
    if event.get("event") == "phase" and event.get("phase") == "preflight-validated":
        os.kill(os.getpid(), signal.SIGTERM)

with mock.patch.object(clean_room_check, "_validate_trusted_executables"):
    receipt = clean_room_check.run(
        repo_root=Path({str(REPO_ROOT)!r}),
        environment={{}},
        emit=emit,
    )
assert receipt.status == "cancelled"
assert receipt.reason == "cancelled"
assert receipt.temporary_cleaned
assert receipt.containment_extinct
assert signal.getsignal(signal.SIGTERM) == previous
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        receipt = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual("receipt", receipt["event"])
        self.assertEqual("cancelled", receipt["status"])
        self.assertNotIn("signal", json.dumps(receipt).lower())

    def test_missing_trusted_executable_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            missing = Path(raw) / "missing-executable"
            with self.assertRaisesRegex(
                clean_room_check.CleanRoomError,
                "trusted_executable_unavailable",
            ):
                clean_room_check._validate_trusted_executable(missing)

    def test_gpg_broker_contract_requires_synthetic_workspace_keyring(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            workspace = Path(raw)
            keyring = workspace / "synthetic-gnupg"
            keyring.mkdir(mode=0o700)
            fingerprint = "A" * 40
            allowed = (
                [
                    "--batch",
                    "--passphrase",
                    "",
                    "--quick-generate-key",
                    "SKLegal Synthetic Test <synthetic@example.invalid>",
                    "ed25519",
                    "sign",
                    "1d",
                ],
                ["--batch", "--with-colons", "--list-secret-keys"],
                [
                    "--batch",
                    "--yes",
                    "--armor",
                    "--detach-sign",
                    "--local-user",
                    fingerprint,
                ],
            )
            for arguments in allowed:
                with self.subTest(arguments=arguments):
                    clean_room_check._validate_gpg_command(
                        arguments,
                        gnupg_home=keyring,
                        workspace=workspace,
                    )

            for arguments, home in (
                (["--version"], keyring),
                (["--batch", "--delete-secret-keys", fingerprint], keyring),
                (["--batch", "--with-colons", "--list-secret-keys"], Path("/tmp")),
            ):
                with (
                    self.subTest(arguments=arguments, home=home),
                    self.assertRaisesRegex(
                        clean_room_check.CleanRoomError,
                        "broker_request_denied",
                    ),
                ):
                    clean_room_check._validate_gpg_command(
                        arguments,
                        gnupg_home=home,
                        workspace=workspace,
                    )

    def test_gpg_broker_uses_private_keyring_and_snapshots_verify_inputs(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory(dir="/tmp") as workspace_raw,
            tempfile.TemporaryDirectory(dir="/tmp") as outside_raw,
        ):
            workspace = Path(workspace_raw)
            compose = workspace / "deploy" / "chiap01" / "compose.dev.yml"
            compose.parent.mkdir(parents=True)
            compose.write_text("services: {}\n", encoding="utf-8")
            logical_home = workspace / "synthetic-gnupg"
            logical_home.mkdir(mode=0o700)
            signature = workspace / "signature.asc"
            payload = workspace / "payload.json"
            signature.write_text("synthetic signature\n", encoding="utf-8")
            payload.write_text("synthetic payload\n", encoding="utf-8")
            outside = Path(outside_raw)
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("unchanged\n", encoding="utf-8")
            broker = clean_room_check.ExternalBrokerSet(workspace)
            private_root = broker._private_workspace.path
            captured: list[tuple[list[str], Path, dict[str, str]]] = []

            def external(
                argv: list[str],
                *,
                cwd: Path,
                environment: dict[str, str],
                stdin: bytes | None = None,
                timeout: float,
            ) -> subprocess.CompletedProcess[bytes]:
                del stdin, timeout
                captured.append((argv, cwd, environment))
                return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

            try:
                with mock.patch.object(broker, "_run_external", side_effect=external):
                    broker._gpg_request(
                        [
                            "--batch",
                            "--passphrase",
                            "",
                            "--quick-generate-key",
                            "SKLegal Synthetic Test <synthetic@example.invalid>",
                            "ed25519",
                            "sign",
                            "1d",
                        ],
                        cwd=workspace,
                        home=logical_home,
                        stdin=b"",
                    )
                    parked = workspace / "logical-home-before-swap"
                    logical_home.rename(parked)
                    logical_home.symlink_to(outside, target_is_directory=True)
                    broker._gpg_request(
                        ["--batch", "--with-colons", "--list-secret-keys"],
                        cwd=workspace,
                        home=logical_home,
                        stdin=b"",
                    )
                    broker._gpg_request(
                        [
                            "--batch",
                            "--verify",
                            str(signature),
                            str(payload),
                        ],
                        cwd=workspace,
                        home=logical_home,
                        stdin=b"",
                    )
                self.assertTrue(captured)
                for argv, cwd, environment in captured:
                    self.assertTrue(
                        Path(environment["GNUPGHOME"]).is_relative_to(private_root)
                    )
                    self.assertNotEqual(logical_home, Path(environment["GNUPGHOME"]))
                    self.assertTrue(cwd.is_relative_to(private_root))
                    self.assertNotIn(str(signature), argv)
                    self.assertNotIn(str(payload), argv)
                self.assertFalse(
                    any(
                        "gpg-inputs" in entry.path.parts
                        for entry in broker._private_entries
                    )
                )
                self.assertEqual(2, broker.resource_counts["verify_snapshots_created"])
                self.assertEqual("unchanged\n", sentinel.read_text(encoding="utf-8"))
                self.assertEqual([sentinel], list(outside.iterdir()))
            finally:
                cleaned = broker.close()
            self.assertTrue(cleaned)
            self.assertFalse(private_root.exists())
            self.assertEqual("unchanged\n", sentinel.read_text(encoding="utf-8"))
            self.assertEqual([sentinel], list(outside.iterdir()))

    def test_compose_broker_uses_prechecked_private_snapshot_after_path_swap(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory(dir="/tmp") as workspace_raw,
            tempfile.TemporaryDirectory(dir="/tmp") as outside_raw,
        ):
            workspace = Path(workspace_raw)
            compose = workspace / "deploy" / "chiap01" / "compose.dev.yml"
            compose.parent.mkdir(parents=True)
            compose.write_text("services: {}\n", encoding="utf-8")
            outside = Path(outside_raw)
            sentinel = outside / "sentinel.yml"
            sentinel.write_text("outside: unchanged\n", encoding="utf-8")
            broker = clean_room_check.ExternalBrokerSet(workspace)
            private_root = broker._private_workspace.path
            original = compose.with_name("compose.before-swap.yml")
            compose.rename(original)
            compose.symlink_to(sentinel)
            captured: list[tuple[list[str], Path]] = []

            def external(
                argv: list[str],
                *,
                cwd: Path,
                environment: dict[str, str],
                stdin: bytes | None = None,
                timeout: float,
            ) -> subprocess.CompletedProcess[bytes]:
                del environment, stdin, timeout
                captured.append((argv, cwd))
                return subprocess.CompletedProcess(argv, 0, stdout=b"{}", stderr=b"")

            try:
                with mock.patch.object(broker, "_run_external", side_effect=external):
                    broker._docker_request(
                        [
                            "compose",
                            "--file",
                            str(compose),
                            "config",
                            "--format",
                            "json",
                        ],
                        cwd=workspace,
                        stdin=b"",
                    )
                self.assertEqual(1, len(captured))
                argv, cwd = captured[0]
                self.assertNotIn(str(compose), argv)
                self.assertTrue(any(str(private_root) in item for item in argv))
                self.assertTrue(cwd.is_relative_to(private_root))
                self.assertEqual(
                    "outside: unchanged\n",
                    sentinel.read_text(encoding="utf-8"),
                )
            finally:
                cleaned = broker.close()
            self.assertTrue(cleaned)
            self.assertFalse(private_root.exists())
            self.assertEqual([sentinel], list(outside.iterdir()))

    def test_broker_rejects_initial_compose_and_gpg_links_without_side_effects(
        self,
    ) -> None:
        before_private = set(Path("/tmp").glob("sklegal-broker-*"))
        with (
            tempfile.TemporaryDirectory(dir="/tmp") as workspace_raw,
            tempfile.TemporaryDirectory(dir="/tmp") as outside_raw,
        ):
            workspace = Path(workspace_raw)
            outside = Path(outside_raw)
            sentinel = outside / "sentinel.yml"
            sentinel.write_text("outside: unchanged\n", encoding="utf-8")
            compose = workspace / "deploy" / "chiap01" / "compose.dev.yml"
            compose.parent.mkdir(parents=True)
            compose.symlink_to(sentinel)
            broker: clean_room_check.ExternalBrokerSet | None = None
            try:
                with self.assertRaisesRegex(
                    clean_room_check.CleanRoomError,
                    "broker_start_failed",
                ):
                    broker = clean_room_check.ExternalBrokerSet(workspace)
            finally:
                if broker is not None:
                    broker.close()
            self.assertEqual(
                "outside: unchanged\n",
                sentinel.read_text(encoding="utf-8"),
            )

            compose.unlink()
            compose.write_text("services: {}\n", encoding="utf-8")
            logical_home = workspace / "synthetic-gnupg"
            logical_home.symlink_to(outside, target_is_directory=True)
            broker = clean_room_check.ExternalBrokerSet(workspace)
            private_root = broker._private_workspace.path
            try:
                with self.assertRaisesRegex(
                    clean_room_check.CleanRoomError,
                    "broker_request_denied",
                ):
                    broker._gpg_request(
                        ["--batch", "--with-colons", "--list-secret-keys"],
                        cwd=workspace,
                        home=logical_home,
                        stdin=b"",
                    )
            finally:
                cleaned = broker.close()
            self.assertTrue(cleaned)
            self.assertFalse(private_root.exists())
            self.assertEqual([sentinel], list(outside.iterdir()))
        self.assertEqual(before_private, set(Path("/tmp").glob("sklegal-broker-*")))

    def test_private_keyring_cleanup_identity_swap_fails_closed(self) -> None:
        with (
            tempfile.TemporaryDirectory(dir="/tmp") as workspace_raw,
            tempfile.TemporaryDirectory(dir="/tmp") as outside_raw,
        ):
            workspace = Path(workspace_raw)
            compose = workspace / "deploy" / "chiap01" / "compose.dev.yml"
            compose.parent.mkdir(parents=True)
            compose.write_text("services: {}\n", encoding="utf-8")
            logical_home = workspace / "synthetic-gnupg"
            logical_home.mkdir(mode=0o700)
            outside = Path(outside_raw)
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("unchanged\n", encoding="utf-8")
            broker = clean_room_check.ExternalBrokerSet(workspace)
            private_root = broker._private_workspace.path
            fake = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
            with mock.patch.object(broker, "_run_external", return_value=fake):
                broker._gpg_request(
                    ["--batch", "--with-colons", "--list-secret-keys"],
                    cwd=workspace,
                    home=logical_home,
                    stdin=b"",
                )
            keyring = next(iter(broker._gpg_keyrings.values()))
            parked = keyring.path.with_name(f"{keyring.path.name}-before-swap")
            keyring.path.rename(parked)
            keyring.path.symlink_to(outside, target_is_directory=True)

            self.assertFalse(broker.close())
            self.assertFalse(private_root.exists())
            self.assertEqual("unchanged\n", sentinel.read_text(encoding="utf-8"))
            self.assertEqual([sentinel], list(outside.iterdir()))

    def test_systemd_launcher_uses_minimal_controller_environment(self) -> None:
        captured: dict[str, Any] = {}
        process = FakeProcess(0)

        def process_factory(
            argv: list[str],
            *,
            cwd: Path,
            env: dict[str, str],
            start_new_session: bool,
        ) -> FakeProcess:
            captured.update(
                argv=argv,
                cwd=cwd,
                env=env,
                start_new_session=start_new_session,
            )
            return process

        backend = clean_room_check.SystemdContainment(
            process_factory=process_factory,
        )
        unit_name = "sklegal-cleanroom-1-2-3.service"
        expected_control_group = backend._expected_control_group(unit_name)
        backend._show = lambda unit: subprocess.CompletedProcess(  # type: ignore[method-assign]
            [],
            0,
            stdout=(
                "ActiveState=active\nSubState=running\n"
                f"ControlGroup={expected_control_group}\n"
            ).encode(),
        )
        contained = backend.start(
            target=Path("/tmp"),
            child_environment={
                "PATH": "/usr/bin",
                "UV_CACHE_DIR": "/tmp/cache/uv",
                "npm_config_cache": "/tmp/cache/npm",
            },
            unit_name=unit_name,
            runtime_seconds=10,
            register=lambda value: None,
        )
        self.assertEqual(expected_control_group, contained.control_group)
        self.assertFalse(captured["start_new_session"])
        self.assertEqual(
            {
                "DBUS_SESSION_BUS_ADDRESS",
                "LANG",
                "LC_ALL",
                "XDG_RUNTIME_DIR",
            },
            set(captured["env"]),
        )
        self.assertNotIn("HOME", captured["env"])
        self.assertNotIn("PATH", captured["env"])

    def test_containment_registers_before_spawn_and_masks_interrupt_boundaries(
        self,
    ) -> None:
        unit_name = "sklegal-cleanroom-1-2-3.service"
        for boundary in ("before_spawn", "process_bound", "observed_bound"):
            with self.subTest(boundary=boundary):
                process = FakeProcess(-signal.SIGKILL)
                registered: list[clean_room_check.ContainedUnit] = []
                cleaned: list[clean_room_check.ContainedUnit] = []
                masks: list[tuple[int, set[signal.Signals]]] = []

                def probe(name: str) -> None:
                    if name == boundary:
                        raise KeyboardInterrupt

                backend = clean_room_check.SystemdContainment(
                    process_factory=lambda *args, **kwargs: process,
                    lifecycle_probe=probe,
                )
                expected = backend._expected_control_group(unit_name)
                backend._show = lambda unit: subprocess.CompletedProcess(  # type: ignore[method-assign]
                    [],
                    0,
                    stdout=(
                        "ActiveState=active\nSubState=running\n"
                        f"ControlGroup={expected}\n"
                    ).encode(),
                )
                backend._cleanup_failed_start = cleaned.append  # type: ignore[method-assign]

                def mask(
                    how: int,
                    values: set[signal.Signals],
                ) -> set[signal.Signals]:
                    masks.append((how, values))
                    return set()

                with (
                    mock.patch.object(signal, "pthread_sigmask", side_effect=mask),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    backend.start(
                        target=Path("/tmp"),
                        child_environment={
                            "PATH": "/usr/bin",
                            "UV_CACHE_DIR": "/tmp/cache/uv",
                            "npm_config_cache": "/tmp/cache/npm",
                        },
                        unit_name=unit_name,
                        runtime_seconds=10,
                        register=registered.append,
                    )
                self.assertTrue(registered)
                self.assertEqual(expected, registered[0].candidate_control_group)
                self.assertTrue(cleaned)
                self.assertEqual(signal.SIG_BLOCK, masks[0][0])
                self.assertEqual({signal.SIGINT, signal.SIGTERM}, masks[0][1])
                self.assertEqual(signal.SIG_SETMASK, masks[-1][0])

    def test_systemd_preflight_closes_manager_environment_and_outages(self) -> None:
        with tempfile.TemporaryDirectory() as cgroup_raw:
            cgroup_root = Path(cgroup_raw)
            (cgroup_root / "cgroup.controllers").write_text("cpu\n", encoding="ascii")
            backend = clean_room_check.SystemdContainment()
            backend._run_control = lambda argv, timeout=5.0: (
                subprocess.CompletedProcess(  # type: ignore[method-assign]
                    argv,
                    0,
                    stdout=(
                        b"AWS_PROFILE=synthetic\n"
                        b"BASH_ENV=/tmp/synthetic\n"
                        b"NEW_CREDENTIAL=synthetic\n"
                    ),
                )
            )
            with (
                mock.patch.object(clean_room_check, "CGROUP_ROOT", cgroup_root),
                mock.patch.object(clean_room_check, "_landlock_abi", return_value=8),
            ):
                backend.preflight()
            argv = backend.build_argv(
                target=Path("/tmp/work"),
                child_environment={
                    "PATH": "/usr/bin",
                    "UV_CACHE_DIR": "/tmp/cache/uv",
                    "npm_config_cache": "/tmp/cache/npm",
                },
                unit_name="sklegal-cleanroom-1-2-3.service",
                runtime_seconds=10,
            )
        unset = next(
            item for item in argv if item.startswith("--property=UnsetEnvironment=")
        )
        for name in ("AWS_PROFILE", "BASH_ENV", "NEW_CREDENTIAL"):
            self.assertIn(name, unset)

        unavailable = clean_room_check.SystemdContainment()
        unavailable._run_control = (  # type: ignore[method-assign]
            lambda argv, timeout=5.0: subprocess.CompletedProcess(
                argv,
                1,
                stdout=b"",
            )
        )
        with self.assertRaisesRegex(
            clean_room_check.CleanRoomError,
            "containment_unavailable",
        ):
            unavailable.preflight()

        with tempfile.TemporaryDirectory() as cgroup_raw:
            cgroup_root = Path(cgroup_raw)
            (cgroup_root / "cgroup.controllers").write_text("cpu\n", encoding="ascii")
            no_landlock = clean_room_check.SystemdContainment()
            no_landlock._run_control = (  # type: ignore[method-assign]
                lambda argv, timeout=5.0: subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout=b"LANG=C.UTF-8\n",
                )
            )
            with (
                mock.patch.object(clean_room_check, "CGROUP_ROOT", cgroup_root),
                mock.patch.object(clean_room_check, "_landlock_abi", return_value=4),
                self.assertRaisesRegex(
                    clean_room_check.CleanRoomError,
                    "containment_unavailable",
                ),
            ):
                no_landlock.preflight()

    def test_failed_cgroup_binding_is_killed_and_fails_closed(self) -> None:
        process = FakeProcess(-signal.SIGKILL)
        control_calls: list[list[str]] = []
        backend = clean_room_check.SystemdContainment(
            process_factory=lambda *args, **kwargs: process,
        )
        backend._run_control = (  # type: ignore[method-assign]
            lambda argv, timeout=5.0: (
                control_calls.append(argv)
                or subprocess.CompletedProcess(argv, 0, stdout=b"")
            )
        )
        shows = iter(
            (
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=(
                        b"ActiveState=active\n"
                        b"ControlGroup=/user.slice/unexpected.service\n"
                    ),
                ),
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=b"ActiveState=inactive\nControlGroup=\n",
                ),
            )
        )
        backend._show = lambda unit: next(shows)  # type: ignore[method-assign]

        with self.assertRaisesRegex(
            clean_room_check.CleanRoomError,
            "containment_binding_failed",
        ):
            backend.start(
                target=Path("/tmp"),
                child_environment={
                    "PATH": "/usr/bin",
                    "UV_CACHE_DIR": "/tmp/cache/uv",
                    "npm_config_cache": "/tmp/cache/npm",
                },
                unit_name="sklegal-cleanroom-1-2-3.service",
                runtime_seconds=10,
                register=lambda value: None,
            )
        self.assertEqual(-signal.SIGKILL, process.return_code)
        self.assertTrue(
            any(
                "kill" in call
                and "--kill-whom=all" in call
                and "--signal=SIGTERM" in call
                for call in control_calls
            )
        )

    def test_scratch_roots_are_beneath_only_trusted_local_anchors(self) -> None:
        with (
            tempfile.TemporaryDirectory(dir="/dev/shm") as source_raw,
            tempfile.TemporaryDirectory(dir="/dev/shm") as untrusted_raw,
        ):
            source = Path(source_raw) / "repository"
            source.mkdir()
            untrusted = Path(untrusted_raw)
            for override in (str(untrusted), "/", str(source.parent)):
                with (
                    self.subTest(override=override),
                    self.assertRaises(clean_room_check.CleanRoomError),
                ):
                    clean_room_check.resolve_scratch_root(
                        environment={"SKLEGAL_CLEAN_ROOM_ROOT": override},
                        repo_root=source,
                    )

            self.assertEqual(
                Path("/tmp"),
                clean_room_check.resolve_scratch_root(
                    environment={"SKLEGAL_CLEAN_ROOM_ROOT": "/tmp"},
                    repo_root=source,
                ),
            )

            arbitrary_xdg = Path(source_raw) / "arbitrary-xdg"
            arbitrary_xdg.mkdir()
            selected = clean_room_check.resolve_scratch_root(
                environment={"XDG_RUNTIME_DIR": str(arbitrary_xdg)},
                repo_root=source,
            )
            self.assertIn(
                selected,
                {Path("/tmp"), Path(f"/run/user/{os.geteuid()}")},
            )

            with mock.patch.object(
                clean_room_check,
                "_runtime_anchor",
                return_value=source.parent,
            ):
                self.assertEqual(
                    Path("/tmp"),
                    clean_room_check.resolve_scratch_root(
                        environment={},
                        repo_root=source,
                    ),
                )

            valid = Path(tempfile.mkdtemp(dir="/tmp"))
            self.addCleanup(lambda: valid.rmdir())
            with self.assertRaisesRegex(
                clean_room_check.CleanRoomError,
                "scratch_root_must_be_exact_anchor",
            ):
                clean_room_check.resolve_scratch_root(
                    environment={"SKLEGAL_CLEAN_ROOM_ROOT": str(valid)},
                    repo_root=source,
                )

        with tempfile.TemporaryDirectory(dir="/tmp") as source_raw:
            source = Path(source_raw) / "repository"
            source.mkdir()
            with (
                mock.patch.object(
                    clean_room_check,
                    "_runtime_anchor",
                    return_value=None,
                ),
                self.assertRaisesRegex(
                    clean_room_check.CleanRoomError,
                    "scratch_root_overlaps_repository",
                ),
            ):
                clean_room_check.resolve_scratch_root(
                    environment={},
                    repo_root=source,
                )

    def test_override_and_cache_links_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as source_raw:
            source = Path(source_raw) / "repository"
            source.mkdir()
            real = Path(tempfile.mkdtemp(dir="/tmp"))
            link = real.with_name(f"{real.name}-link")
            link.symlink_to(real, target_is_directory=True)
            self.addCleanup(lambda: link.unlink(missing_ok=True))
            self.addCleanup(lambda: real.rmdir())
            with self.assertRaises(clean_room_check.CleanRoomError):
                clean_room_check.resolve_scratch_root(
                    environment={"SKLEGAL_CLEAN_ROOM_ROOT": str(link)},
                    repo_root=source,
                )

            cache = Path(tempfile.mkdtemp(dir="/tmp"))
            cache_link = cache.with_name(f"{cache.name}-link")
            cache_link.symlink_to(cache, target_is_directory=True)
            self.addCleanup(lambda: cache_link.unlink(missing_ok=True))
            self.addCleanup(lambda: cache.rmdir())
            with self.assertRaisesRegex(
                clean_room_check.CleanRoomError,
                "uv_cache_untrusted",
            ):
                clean_room_check._cache_roots(
                    {
                        "UV_CACHE_DIR": str(cache_link),
                        "npm_config_cache": str(cache),
                    },
                    repo_root=source,
                )

    def test_source_and_destination_links_and_generated_outputs_are_denied(
        self,
    ) -> None:
        source_temporary = tempfile.TemporaryDirectory()
        scratch_temporary = tempfile.TemporaryDirectory()
        cache_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(source_temporary.cleanup)
        self.addCleanup(scratch_temporary.cleanup)
        self.addCleanup(cache_temporary.cleanup)
        source = Path(source_temporary.name) / "repository"
        source.mkdir()
        scratch = Path(scratch_temporary.name)
        cache = Path(cache_temporary.name)
        (cache / "uv").mkdir()
        (cache / "npm").mkdir()
        outside = scratch / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        (source / "linked.txt").symlink_to(outside)

        def source_runner(
            *args: object, **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            del args, kwargs
            return subprocess.CompletedProcess([], 0, stdout=b"linked.txt\0")

        with (
            mock.patch.object(
                clean_room_check,
                "resolve_scratch_root",
                return_value=scratch,
            ),
            mock.patch.object(
                clean_room_check,
                "_validate_trusted_executables",
            ),
        ):
            receipt = clean_room_check.run(
                repo_root=source,
                environment={
                    "SKLEGAL_CLEAN_ROOM_ROOT": str(scratch),
                    "UV_CACHE_DIR": str(cache / "uv"),
                    "npm_config_cache": str(cache / "npm"),
                },
                source_runner=source_runner,
                containment=FakeContainment(FakeProcess(0)),
                emit=lambda event: None,
            )
        self.assertEqual("failed", receipt.status)
        self.assertEqual("unsafe_source_type", receipt.reason)

        generated, containment, _, _, _ = self._fixture(
            process=FakeProcess(0),
            source_entries=("node_modules/generated.js",),
        )
        self.assertEqual("failed", generated.status)
        self.assertEqual("generated_source_path", generated.reason)
        self.assertIsNone(containment.start_call)

        def replace_destination(source_path: Path, destination: Path) -> None:
            del source_path
            destination.unlink()
            destination.symlink_to(outside)

        destination, containment, _, _, _ = self._fixture(
            process=FakeProcess(0),
            after_copy_hook=replace_destination,
        )
        self.assertEqual("failed", destination.status)
        self.assertEqual("destination_identity_changed", destination.reason)
        self.assertIsNone(containment.start_call)

    def test_descriptor_copy_rejects_hardlinks_and_inode_replacement(self) -> None:
        with (
            tempfile.TemporaryDirectory() as source_raw,
            tempfile.TemporaryDirectory() as target_raw,
        ):
            source = Path(source_raw)
            target = Path(target_raw)
            payload = source / "payload.txt"
            payload.write_text("same bytes\n", encoding="utf-8")
            hardlink = source / "hardlink.txt"
            os.link(payload, hardlink)
            with self.assertRaisesRegex(
                clean_room_check.CleanRoomError,
                "source_hardlink",
            ):
                clean_room_check._copy_sources(
                    [Path("payload.txt")],
                    repo_root=source,
                    target=target,
                )

        with (
            tempfile.TemporaryDirectory() as source_raw,
            tempfile.TemporaryDirectory() as target_raw,
        ):
            source = Path(source_raw)
            target = Path(target_raw)
            payload = source / "payload.txt"
            payload.write_text("same bytes\n", encoding="utf-8")
            replacement = source / "replacement.txt"
            replacement.write_text("same bytes\n", encoding="utf-8")

            def replace_source(source_path: Path, destination: Path) -> None:
                del destination
                os.replace(replacement, source_path)

            with self.assertRaisesRegex(
                clean_room_check.CleanRoomError,
                "source_identity_changed",
            ):
                clean_room_check._copy_sources(
                    [Path("payload.txt")],
                    repo_root=source,
                    target=target,
                    after_copy_hook=replace_source,
                )

    def test_descriptor_copy_is_exclusive_and_rejects_destination_hardlink(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as source_raw,
            tempfile.TemporaryDirectory() as target_raw,
        ):
            source = Path(source_raw)
            target = Path(target_raw)
            (source / "payload.txt").write_text("synthetic\n", encoding="utf-8")

            def link_destination(source_path: Path, destination: Path) -> None:
                del source_path
                os.link(destination, destination.with_suffix(".hardlink"))

            with self.assertRaisesRegex(
                clean_room_check.CleanRoomError,
                "destination_hardlink",
            ):
                clean_room_check._copy_sources(
                    [Path("payload.txt")],
                    repo_root=source,
                    target=target,
                    after_copy_hook=link_destination,
                )

        with (
            tempfile.TemporaryDirectory() as source_raw,
            tempfile.TemporaryDirectory() as target_raw,
        ):
            source = Path(source_raw)
            target = Path(target_raw)
            (source / "payload.txt").write_text("synthetic\n", encoding="utf-8")
            (target / "payload.txt").write_text("occupied\n", encoding="utf-8")
            with self.assertRaisesRegex(
                clean_room_check.CleanRoomError,
                "destination_already_exists",
            ):
                clean_room_check._copy_sources(
                    [Path("payload.txt")],
                    repo_root=source,
                    target=target,
                )

    def test_copy_drift_fails_before_gate_and_cleans(self) -> None:
        def drift(source: Path, destination: Path) -> None:
            del source
            destination.write_text("changed after copy\n", encoding="utf-8")

        receipt, containment, events, _, _ = self._fixture(
            process=FakeProcess(0),
            after_copy_hook=drift,
        )
        self.assertEqual("failed", receipt.status)
        self.assertEqual("copy_bytes_mismatch", receipt.reason)
        self.assertIsNone(containment.start_call)
        self.assertTrue(receipt.temporary_cleaned)
        self.assertTrue(events[-1]["temporary_cleaned"])

    def test_source_allowlist_timeout_fails_closed_with_terminal_receipt(
        self,
    ) -> None:
        def hang(source: Path) -> None:
            del source
            raise clean_room_check.CleanRoomTimeout("source_allowlist_timeout")

        receipt, containment, events, _, _ = self._fixture(
            process=FakeProcess(0),
            source_runner_hook=hang,
        )
        self.assertEqual("timed_out", receipt.status)
        self.assertEqual(124, receipt.exit_code)
        self.assertEqual("source_allowlist_timeout", receipt.reason)
        self.assertIsNone(containment.start_call)
        self.assertTrue(receipt.temporary_cleaned)
        self.assertEqual("receipt", events[-1]["event"])
        self.assertEqual("timed_out", events[-1]["status"])

        for invalid in (0.0, -1.0, math.inf, math.nan):
            with self.subTest(invalid=invalid):
                invalid_receipt, invalid_containment, _, _, _ = self._fixture(
                    process=FakeProcess(0),
                    source_timeout=invalid,
                )
                self.assertEqual("failed", invalid_receipt.status)
                self.assertEqual(
                    "invalid_timeout_configuration",
                    invalid_receipt.reason,
                )
                self.assertIsNone(invalid_containment.start_call)

    def test_source_allowlist_runner_escalates_term_to_bounded_group_kill(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            marker = Path(raw)
            script = (
                "trap 'touch \"$1\"/term-delivered' TERM; "
                '( trap "" TERM; sleep 2; touch "$1"/group-survived ) & '
                "while :; do sleep 1; done"
            )
            started = time.monotonic()
            with (
                mock.patch.object(
                    clean_room_check,
                    "SOURCE_TERMINATE_GRACE_SECONDS",
                    0.5,
                ),
                mock.patch.object(
                    clean_room_check,
                    "SOURCE_KILL_GRACE_SECONDS",
                    1.0,
                ),
                self.assertRaisesRegex(
                    clean_room_check.CleanRoomTimeout,
                    "source_allowlist_timeout",
                ),
            ):
                clean_room_check._run_source_command(
                    [
                        "/usr/bin/bash",
                        "-c",
                        script,
                        "clean-room-test",
                        str(marker),
                    ],
                    cwd=Path("/"),
                    env={"PATH": clean_room_check.SYSTEM_PATH},
                    check=True,
                    capture_output=True,
                    pass_fds=(),
                    timeout=0.5,
                )
            self.assertLess(time.monotonic() - started, 10.0)
            self.assertTrue((marker / "term-delivered").exists())
            time.sleep(2.5)
            self.assertFalse((marker / "group-survived").exists())

    def test_child_failure_is_normalized_and_tree_is_extinct_and_cleaned(
        self,
    ) -> None:
        receipt, containment, _, _, _ = self._fixture(process=FakeProcess(-9))
        assert containment.start_call is not None
        target = containment.start_call["target"]
        self.assertEqual("failed", receipt.status)
        self.assertEqual("gate_failed", receipt.reason)
        self.assertEqual(137, receipt.exit_code)
        self.assertFalse(target.exists())
        self.assertTrue(receipt.temporary_cleaned)
        self.assertTrue(receipt.containment_extinct)
        self.assertTrue(receipt.external_brokers_cleaned)

    def test_timeout_escalates_term_kill_extinguishes_and_cleans(self) -> None:
        process = FakeProcess(
            subprocess.TimeoutExpired(["make", "check"], 10.0),
            subprocess.TimeoutExpired(["make", "check"], 2.0),
            -signal.SIGKILL,
        )
        receipt, containment, events, _, _ = self._fixture(process=process)
        assert containment.start_call is not None
        self.assertEqual("timed_out", receipt.status)
        self.assertEqual(124, receipt.exit_code)
        self.assertEqual([signal.SIGTERM, signal.SIGKILL], containment.signals)
        self.assertEqual([10.0, 2.0, 1.0], process.wait_timeouts)
        self.assertFalse(containment.start_call["target"].exists())
        self.assertTrue(receipt.temporary_cleaned)
        self.assertTrue(receipt.containment_extinct)
        self.assertTrue(receipt.external_brokers_cleaned)
        self.assertTrue(events[-1]["temporary_cleaned"])

        kill_timeout_process = FakeProcess(
            subprocess.TimeoutExpired(["make", "check"], 10.0),
            subprocess.TimeoutExpired(["make", "check"], 2.0),
            subprocess.TimeoutExpired(["make", "check"], 1.0),
        )
        kill_timeout, containment, _, _, _ = self._fixture(
            process=kill_timeout_process,
            containment_options={"extinct_after_kill": True},
        )
        self.assertEqual("timed_out", kill_timeout.status)
        self.assertEqual("gate_kill_timeout", kill_timeout.reason)
        self.assertTrue(kill_timeout.temporary_cleaned)
        self.assertTrue(kill_timeout.containment_extinct)
        self.assertTrue(kill_timeout.external_brokers_cleaned)
        self.assertEqual([signal.SIGTERM, signal.SIGKILL], containment.signals)

    def test_running_gate_emits_bounded_elapsed_progress(self) -> None:
        process = FakeProcess(
            subprocess.TimeoutExpired(["make", "check"], 4.0),
            0,
        )
        receipt, _, events, _, _ = self._fixture(
            process=process,
            progress_interval=4.0,
        )
        self.assertEqual("passed", receipt.status)
        self.assertEqual([4.0, 4.0], process.wait_timeouts)
        self.assertIn(
            "gate-running",
            [event.get("phase") for event in events if event["event"] == "phase"],
        )

        for invalid in (0.0, -1.0, math.inf, math.nan):
            with self.subTest(invalid=invalid):
                invalid_receipt, containment, _, _, _ = self._fixture(
                    process=FakeProcess(0),
                    progress_interval=invalid,
                )
                self.assertEqual("failed", invalid_receipt.status)
                self.assertEqual(
                    "invalid_timeout_configuration",
                    invalid_receipt.reason,
                )
                self.assertIsNone(containment.start_call)

    def test_cancel_and_exception_extinguish_and_clean(self) -> None:
        cancelled, containment, _, _, _ = self._fixture(
            process=FakeProcess(KeyboardInterrupt(), -signal.SIGTERM)
        )
        self.assertEqual("cancelled", cancelled.status)
        self.assertEqual(130, cancelled.exit_code)
        self.assertEqual([signal.SIGTERM], containment.signals)
        self.assertTrue(cancelled.temporary_cleaned)
        self.assertTrue(cancelled.containment_extinct)
        self.assertTrue(cancelled.external_brokers_cleaned)

        failed, containment, _, _, _ = self._fixture(
            process=FakeProcess(RuntimeError("synthetic backend detail"), 0)
        )
        self.assertEqual("failed", failed.status)
        self.assertEqual("internal_error", failed.reason)
        self.assertEqual([signal.SIGTERM], containment.signals)
        self.assertTrue(failed.temporary_cleaned)
        self.assertTrue(failed.containment_extinct)
        self.assertTrue(failed.external_brokers_cleaned)

    def test_broker_cleanup_failure_overrides_a_successful_gate(self) -> None:
        receipt, _, events, _, _ = self._fixture(
            process=FakeProcess(0),
            broker_cleanup=False,
        )
        self.assertEqual("failed", receipt.status)
        self.assertEqual("broker_cleanup_failed", receipt.reason)
        self.assertFalse(receipt.external_brokers_cleaned)
        self.assertFalse(events[-1]["external_brokers_cleaned"])
        self.assertTrue(receipt.containment_extinct)
        self.assertTrue(receipt.temporary_cleaned)

    def test_real_broker_close_terminates_an_active_external_process(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            workspace = Path(raw)
            compose = workspace / "deploy" / "chiap01" / "compose.dev.yml"
            compose.parent.mkdir(parents=True)
            compose.write_text("services: {}\n", encoding="utf-8")
            broker = clean_room_check.ExternalBrokerSet(workspace)
            result: list[subprocess.CompletedProcess[bytes]] = []

            def run_sleep() -> None:
                result.append(
                    broker._run_external(
                        ["/usr/bin/sleep", "30"],
                        cwd=workspace,
                        environment=broker._process_environment(),
                        timeout=30.0,
                    )
                )

            worker = threading.Thread(target=run_sleep)
            worker.start()
            deadline = time.monotonic() + 2.0
            while not broker._processes and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(broker._processes)
            process_ids = [process.pid for process in broker._processes]
            self.assertTrue(broker.close())
            worker.join(timeout=2.0)
            self.assertFalse(worker.is_alive())
            self.assertTrue(result)
            self.assertTrue(
                all(not Path(f"/proc/{pid}").exists() for pid in process_ids)
            )

    def test_external_output_is_streamed_bounded_and_processes_are_killed(self) -> None:
        limits = clean_room_check._BrokerLimits(
            max_workers=1,
            max_queued_connections=1,
            max_external_processes=1,
            max_requests=32,
            max_request_bytes=64 * 1024,
            max_response_bytes=64 * 1024,
            max_frame_bytes=64 * 1024,
            max_stdin_bytes=1024,
            max_file_bytes=1024,
            max_output_bytes=1024,
            max_external_output_bytes=2300,
        )
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            workspace = Path(raw)
            compose = workspace / "deploy" / "chiap01" / "compose.dev.yml"
            compose.parent.mkdir(parents=True)
            compose.write_text("services: {}\n", encoding="utf-8")
            broker = clean_room_check.ExternalBrokerSet(workspace, limits=limits)
            environment = broker._process_environment()

            def invoke(
                source: str,
                *,
                timeout: float = 2.0,
            ) -> subprocess.CompletedProcess[bytes]:
                return broker._run_external(
                    [sys.executable, "-c", source],
                    cwd=workspace,
                    environment=environment,
                    stdin=b"bounded input",
                    timeout=timeout,
                )

            try:
                normal = invoke(
                    "import os,sys; data=sys.stdin.buffer.read(); os.write(1,data)"
                )
                self.assertEqual(0, normal.returncode)
                self.assertEqual(b"bounded input", normal.stdout)

                for source in (
                    "import os; os.write(1, b'x' * 4096)",
                    "import os; os.write(2, b'y' * 4096)",
                    "import os; os.write(1, b'x' * 700); os.write(2, b'y' * 700)",
                ):
                    with self.subTest(source=source):
                        denied = invoke(source)
                        self.assertEqual(126, denied.returncode)
                        self.assertEqual(b"", denied.stdout)
                        self.assertEqual(
                            b"broker output limit exceeded\n",
                            denied.stderr,
                        )

                pid_file = workspace / "ignored-term.pid"
                ignored = invoke(
                    "import os,signal,time; "
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    f"open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
                    "\nwhile True:\n os.write(1, b'z' * 512)\n time.sleep(0.001)\n",
                    timeout=2.0,
                )
                self.assertEqual(126, ignored.returncode)
                self.assertEqual(b"broker output limit exceeded\n", ignored.stderr)
                ignored_pid = int(pid_file.read_text(encoding="ascii"))
                self.assertFalse(Path(f"/proc/{ignored_pid}").exists())
                self.assertFalse(broker._processes)
                counts = broker.resource_counts
                self.assertGreater(counts["external_output_bytes"], 0)
                self.assertGreaterEqual(counts["external_output_denials"], 4)
            finally:
                self.assertTrue(broker.close())

    def test_loopback_broker_start_is_two_phase_and_partial_threads_join(self) -> None:
        broker = clean_room_check._LoopbackBroker(lambda raw: raw, max_workers=2)
        self.assertEqual((), broker._workers)
        self.assertIsNone(broker._thread)
        broker.start()
        self.assertEqual(2, len(broker._workers))
        self.assertIsNotNone(broker._thread)
        self.assertTrue(broker.close())
        self.assertEqual((), broker._workers)
        self.assertIsNone(broker._thread)

        for boundary in ("worker-0-started", "worker-1-started", "serve-started"):
            with self.subTest(boundary=boundary):

                def interrupt(phase: str, *, expected: str = boundary) -> None:
                    if phase == expected:
                        raise KeyboardInterrupt

                partial = clean_room_check._LoopbackBroker(
                    lambda raw: raw,
                    max_workers=2,
                    lifecycle_probe=interrupt,
                )
                with self.assertRaises(KeyboardInterrupt):
                    partial.start()
                self.assertTrue(partial.close())
                self.assertEqual((), partial._workers)
                self.assertIsNone(partial._thread)

    def test_external_output_cumulative_budget_and_input_are_bounded(self) -> None:
        limits = clean_room_check._BrokerLimits(
            max_workers=1,
            max_queued_connections=1,
            max_external_processes=1,
            max_requests=16,
            max_request_bytes=16 * 1024,
            max_response_bytes=16 * 1024,
            max_frame_bytes=16 * 1024,
            max_stdin_bytes=1024,
            max_file_bytes=1024,
            max_output_bytes=1024,
            max_external_output_bytes=1200,
        )
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            workspace = Path(raw)
            compose = workspace / "deploy" / "chiap01" / "compose.dev.yml"
            compose.parent.mkdir(parents=True)
            compose.write_text("services: {}\n", encoding="utf-8")
            broker = clean_room_check.ExternalBrokerSet(workspace, limits=limits)

            def invoke() -> subprocess.CompletedProcess[bytes]:
                return broker._run_external(
                    [sys.executable, "-c", "import os; os.write(1, b'x' * 700)"],
                    cwd=workspace,
                    environment=broker._process_environment(),
                    timeout=2.0,
                )

            try:
                self.assertEqual(0, invoke().returncode)
                denied = invoke()
                self.assertEqual(126, denied.returncode)
                self.assertEqual(b"", denied.stdout)
                self.assertEqual(b"broker output limit exceeded\n", denied.stderr)
                self.assertEqual(1201, broker.resource_counts["external_output_bytes"])
                self.assertEqual(
                    1,
                    broker.resource_counts["external_output_denials"],
                )
                with self.assertRaisesRegex(
                    clean_room_check.CleanRoomError,
                    "broker_request_denied",
                ):
                    broker._run_external(
                        [sys.executable, "-c", "pass"],
                        cwd=workspace,
                        environment=broker._process_environment(),
                        stdin=b"x" * 1025,
                        timeout=2.0,
                    )
                self.assertFalse(broker._processes)
            finally:
                self.assertTrue(broker.close())

    def test_run_registers_two_phase_broker_before_start_failure(self) -> None:
        instances: list[FakeBrokers] = []

        class InterruptingBrokers(FakeBrokers):
            def start(self) -> None:
                super().start()
                raise KeyboardInterrupt

        def factory(workspace: Path) -> FakeBrokers:
            broker = InterruptingBrokers(workspace)
            instances.append(broker)
            return broker

        receipt, containment, events, _, _ = self._fixture(
            process=FakeProcess(0),
            broker_factory=factory,
        )
        self.assertEqual("cancelled", receipt.status)
        self.assertIsNone(containment.start_call)
        self.assertEqual(1, len(instances))
        self.assertTrue(instances[0].started)
        self.assertTrue(instances[0].closed)
        self.assertTrue(receipt.external_brokers_cleaned)
        self.assertEqual("receipt", events[-1]["event"])

    def test_containment_unavailable_and_nonextinction_fail_closed(self) -> None:
        unavailable, containment, _, _, _ = self._fixture(
            process=FakeProcess(0),
            containment_options={
                "preflight_error": clean_room_check.CleanRoomError(
                    "containment_unavailable"
                )
            },
        )
        self.assertEqual("failed", unavailable.status)
        self.assertEqual("containment_unavailable", unavailable.reason)
        self.assertIsNone(containment.start_call)

        surviving, containment, _, _, _ = self._fixture(
            process=FakeProcess(0),
            containment_options={"extinction_available": False},
        )
        self.assertEqual("failed", surviving.status)
        self.assertEqual("containment_not_extinct", surviving.reason)
        self.assertFalse(surviving.containment_extinct)
        self.assertTrue(surviving.temporary_cleaned)
        self.assertIn(signal.SIGTERM, containment.signals)

        recovered, containment, _, _, _ = self._fixture(
            process=FakeProcess(0, 0),
            containment_options={"ensure_failures": 1},
        )
        self.assertEqual("passed", recovered.status)
        self.assertTrue(recovered.containment_extinct)
        self.assertEqual([signal.SIGTERM], containment.signals)

        residual, containment, _, _, _ = self._fixture(
            process=FakeProcess(0, 0, -signal.SIGKILL),
            containment_options={
                "extinction_available": False,
                "extinct_after_kill": True,
            },
        )
        self.assertEqual("passed", residual.status)
        self.assertTrue(residual.containment_extinct)
        self.assertEqual([signal.SIGTERM, signal.SIGKILL], containment.signals)

    def test_base_exception_after_launch_keeps_the_containment_handle(self) -> None:
        def interrupt_after_launch(target: Path) -> None:
            del target
            raise KeyboardInterrupt

        cancelled, containment, _, _, _ = self._fixture(
            process=FakeProcess(-signal.SIGTERM),
            containment_options={"on_start": interrupt_after_launch},
        )
        self.assertEqual("cancelled", cancelled.status)
        self.assertIsNotNone(cancelled.containment_unit)
        self.assertTrue(cancelled.containment_extinct)
        self.assertEqual([signal.SIGTERM], containment.signals)

    def test_cgroup_state_must_be_inactive_and_empty(self) -> None:
        backend = object.__new__(clean_room_check.SystemdContainment)
        process = FakeProcess(0)
        process.return_code = 0
        contained = clean_room_check.ContainedUnit(
            unit_name="synthetic.service",
            process=process,
            control_group="/synthetic.service",
        )
        with tempfile.TemporaryDirectory() as cgroup_raw:
            cgroup_root = Path(cgroup_raw)
            cgroup = cgroup_root / "synthetic.service"
            cgroup.mkdir()
            (cgroup / "cgroup.procs").write_text("", encoding="ascii")
            (cgroup / "cgroup.events").write_text("populated 0\n", encoding="ascii")
            backend._show = lambda unit: subprocess.CompletedProcess(  # type: ignore[method-assign]
                [], 0, stdout=b"ActiveState=inactive\nControlGroup=/synthetic.service\n"
            )
            with mock.patch.object(clean_room_check, "CGROUP_ROOT", cgroup_root):
                self.assertTrue(backend.ensure_extinct(contained))
                (cgroup / "cgroup.procs").write_text("999\n", encoding="ascii")
                self.assertFalse(backend.ensure_extinct(contained))
                (cgroup / "cgroup.procs").write_text("", encoding="ascii")
                backend._show = lambda unit: subprocess.CompletedProcess(  # type: ignore[method-assign]
                    [],
                    0,
                    stdout=b"ActiveState=active\nControlGroup=/synthetic.service\n",
                )
                self.assertFalse(backend.ensure_extinct(contained))

    def test_sigkill_uses_direct_cgroup_fallback_when_controller_is_down(
        self,
    ) -> None:
        backend = object.__new__(clean_room_check.SystemdContainment)
        backend._run_control = (  # type: ignore[method-assign]
            lambda argv, timeout=5.0: (_ for _ in ()).throw(
                clean_room_check.CleanRoomError("containment_control_failure")
            )
        )
        process = FakeProcess()
        contained = clean_room_check.ContainedUnit(
            unit_name="synthetic.service",
            process=process,
            control_group="/synthetic.service",
        )
        with tempfile.TemporaryDirectory() as cgroup_raw:
            cgroup_root = Path(cgroup_raw)
            cgroup = cgroup_root / "synthetic.service"
            cgroup.mkdir()
            kill_file = cgroup / "cgroup.kill"
            kill_file.write_text("", encoding="ascii")
            with mock.patch.object(clean_room_check, "CGROUP_ROOT", cgroup_root):
                backend.signal(contained, signal.SIGKILL)
            self.assertEqual("1", kill_file.read_text(encoding="ascii"))

    def test_preflight_failure_still_emits_terminal_receipt(self) -> None:
        events: list[dict[str, object]] = []
        receipt = clean_room_check.run(
            repo_root=Path("/definitely/missing/sklegal"),
            environment={},
            emit=events.append,
        )
        self.assertEqual("failed", receipt.status)
        self.assertEqual("source_root_unavailable", receipt.reason)
        self.assertEqual("receipt", events[-1]["event"])
        self.assertTrue(receipt.temporary_cleaned)

    def test_renamed_temporary_identity_cannot_produce_passed_receipt(self) -> None:
        moved: list[Path] = []

        def rename_target(target: Path) -> None:
            renamed = target.with_name(f"{target.name}-renamed")
            target.rename(renamed)
            moved.append(renamed)

        self.addCleanup(
            lambda: shutil.rmtree(moved[0]) if moved and moved[0].exists() else None
        )
        receipt, containment, _, _, _ = self._fixture(
            process=FakeProcess(0),
            containment_options={"on_start": rename_target},
        )
        self.assertEqual("failed", receipt.status)
        self.assertEqual("temporary_identity_changed", receipt.reason)
        self.assertFalse(receipt.temporary_cleaned)
        self.assertTrue(receipt.containment_extinct)
        assert containment.start_call is not None
        self.assertFalse(containment.start_call["target"].exists())
        self.assertTrue(moved[0].exists())

    def test_secure_workspace_cleanup_is_bound_to_created_inode(self) -> None:
        with tempfile.TemporaryDirectory() as scratch_raw:
            scratch = Path(scratch_raw)
            workspace = clean_room_check.SecureWorkspace.create(scratch)
            original_identity = workspace.identity
            renamed = workspace.path.with_name(f"{workspace.path.name}-renamed")
            workspace.path.rename(renamed)
            replacement = workspace.path
            replacement.mkdir()
            try:
                self.assertFalse(workspace.cleanup())
                self.assertTrue(renamed.exists())
                self.assertTrue(replacement.exists())
                self.assertEqual(
                    original_identity,
                    (renamed.stat().st_dev, renamed.stat().st_ino),
                )
            finally:
                shutil.rmtree(renamed)
                shutil.rmtree(replacement)

        with tempfile.TemporaryDirectory(dir="/tmp") as anchor_raw:
            anchor = Path(anchor_raw)
            scratch = anchor / "scratch"
            scratch.mkdir()
            workspace = clean_room_check.SecureWorkspace.create(scratch)
            moved_scratch = anchor / "scratch-moved"
            scratch.rename(moved_scratch)
            scratch.mkdir()
            created_under_moved_parent = moved_scratch / workspace.path.name
            self.assertFalse(workspace.cleanup())
            self.assertFalse(created_under_moved_parent.exists())

    def test_repository_identity_swap_between_allowlist_and_copy_is_denied(
        self,
    ) -> None:
        def replace_repository(source: Path) -> None:
            moved = source.with_name("repository-original")
            source.rename(moved)
            source.mkdir()
            (source / "Makefile").write_text("check:\n\ttrue\n", encoding="utf-8")
            (source / "payload.txt").write_text(
                "replacement payload\n",
                encoding="utf-8",
            )

        receipt, containment, _, _, _ = self._fixture(
            process=FakeProcess(0),
            source_runner_hook=replace_repository,
        )
        self.assertEqual("failed", receipt.status)
        self.assertEqual("source_root_identity_changed", receipt.reason)
        self.assertIsNone(containment.start_call)

    def test_local_git_fsmonitor_and_repository_hooks_never_execute(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            repository = Path(raw) / "repository"
            repository.mkdir()
            subprocess.run(
                ["/usr/bin/git", "init", "--quiet"],
                cwd=repository,
                check=True,
                env=clean_room_check._git_environment(),
            )
            (repository / "payload.txt").write_text("synthetic\n", encoding="utf-8")
            subprocess.run(
                ["/usr/bin/git", "add", "--", "payload.txt"],
                cwd=repository,
                check=True,
                env=clean_room_check._git_environment(),
            )
            marker = Path(raw) / "fsmonitor-executed"
            hook = Path(raw) / "fsmonitor-hook"
            hook.write_text(
                "#!/bin/sh\nprintf invoked > " + str(marker) + "\nprintf token\\n\n",
                encoding="utf-8",
            )
            hook.chmod(0o700)
            subprocess.run(
                ["/usr/bin/git", "config", "core.fsmonitor", str(hook)],
                cwd=repository,
                check=True,
                env=clean_room_check._git_environment(),
            )

            files = clean_room_check.source_files(repo_root=repository)

            self.assertEqual([Path("payload.txt")], files)
            self.assertFalse(marker.exists())

    def test_git_allowlist_uses_command_scope_config_neutralization(self) -> None:
        captured: dict[str, Any] = {}

        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            captured.update(argv=argv, kwargs=kwargs)
            return subprocess.CompletedProcess(argv, 0, stdout=b"payload.txt\0")

        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            repository = Path(raw)
            files = clean_room_check.source_files(
                repo_root=repository,
                runner=runner,  # type: ignore[arg-type]
            )

        self.assertEqual([Path("payload.txt")], files)
        self.assertEqual(
            [
                "/usr/bin/git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.untrackedCache=false",
                "-c",
                "core.preloadIndex=false",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            captured["argv"],
        )

    def test_stdout_events_are_flushed_json_and_bootstrap_is_kill_bounded(
        self,
    ) -> None:
        class RecordingStream(io.StringIO):
            flushed = False

            def flush(self) -> None:
                self.flushed = True
                super().flush()

        output = RecordingStream()
        with contextlib.redirect_stdout(output):
            clean_room_check.emit_event({"event": "phase", "phase": "synthetic"})
        self.assertTrue(output.flushed)
        self.assertEqual(
            {"event": "phase", "phase": "synthetic"},
            json.loads(output.getvalue()),
        )
        bootstrap = (REPO_ROOT / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")
        timeout_lines = [
            line.strip() for line in bootstrap.splitlines() if "timeout " in line
        ]
        self.assertTrue(timeout_lines)
        self.assertTrue(all("--kill-after=" in line for line in timeout_lines))
        self.assertIn("uv sync --locked --all-packages", bootstrap)
        self.assertIn("--ignore-scripts", bootstrap)
        self.assertIn("--no-audit", bootstrap)
        self.assertIn("--no-fund", bootstrap)
        self.assertIn("--prefer-offline", bootstrap)


if __name__ == "__main__":
    unittest.main()
