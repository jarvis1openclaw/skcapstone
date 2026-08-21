from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import clean_room_check  # noqa: E402


class _InjectedWaitProcess:
    def __init__(
        self,
        process: clean_room_check.WaitableProcess,
        failure: BaseException,
    ) -> None:
        self.pid = process.pid
        self._process = process
        self._failure: BaseException | None = failure

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float) -> int:
        if self._failure is not None:
            time.sleep(0.3)
            failure = self._failure
            self._failure = None
            raise failure
        return self._process.wait(timeout=timeout)


class _InjectedWaitContainment:
    def __init__(self, failure: BaseException) -> None:
        self._backend = clean_room_check.SystemdContainment()
        self._failure = failure

    def preflight(self) -> None:
        self._backend.preflight()

    def start(self, **kwargs: Any) -> clean_room_check.ContainedUnit:
        contained = self._backend.start(**kwargs)
        return clean_room_check.ContainedUnit(
            unit_name=contained.unit_name,
            process=_InjectedWaitProcess(contained.process, self._failure),
            control_group=contained.control_group,
        )

    def signal(
        self,
        contained: clean_room_check.ContainedUnit,
        sent: clean_room_check.signal.Signals,
    ) -> None:
        self._backend.signal(contained, sent)

    def ensure_extinct(self, contained: clean_room_check.ContainedUnit) -> bool:
        return self._backend.ensure_extinct(contained)


class _BindingFaultContainment(clean_room_check.SystemdContainment):
    def __init__(self, fault: str) -> None:
        super().__init__()
        self._fault = fault
        self._fault_injected = False
        self._controller_outage = False
        self.direct_kill_used = False

    def _run_control(
        self,
        argv: list[str],
        *,
        timeout: float = 5.0,
    ) -> subprocess.CompletedProcess[bytes]:
        if self._controller_outage:
            raise clean_room_check.CleanRoomError("containment_control_failure")
        return super()._run_control(argv, timeout=timeout)

    def _show(self, unit_name: str) -> subprocess.CompletedProcess[bytes]:
        if not self._fault_injected:
            self._fault_injected = True
            if self._fault == "interrupt":
                raise KeyboardInterrupt
            self._controller_outage = True
            raise clean_room_check.CleanRoomError("containment_control_failure")
        return super()._show(unit_name)

    def _direct_cgroup_kill(
        self,
        contained: clean_room_check.ContainedUnit,
    ) -> bool:
        self.direct_kill_used = True
        return super()._direct_cgroup_kill(contained)


class _LifecycleFaultContainment(clean_room_check.SystemdContainment):
    def __init__(self, boundary: str) -> None:
        self._boundary = boundary
        self._injected = False
        self.spawned_process: clean_room_check.WaitableProcess | None = None
        process_factory = (
            self._spawn_then_interrupt
            if boundary == "factory_after_spawn"
            else clean_room_check._start_process
        )
        super().__init__(
            lifecycle_probe=self._probe,
            process_factory=process_factory,
        )

    def _spawn_then_interrupt(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        start_new_session: bool,
    ) -> clean_room_check.WaitableProcess:
        self.spawned_process = clean_room_check._start_process(
            argv,
            cwd=cwd,
            env=env,
            start_new_session=start_new_session,
        )
        raise KeyboardInterrupt

    def _probe(self, boundary: str) -> None:
        if boundary == self._boundary and not self._injected:
            self._injected = True
            raise KeyboardInterrupt


@unittest.skipUnless(
    os.environ.get("SKLEGAL_SYSTEMD_CONTAINMENT_TEST") == "1",
    "set SKLEGAL_SYSTEMD_CONTAINMENT_TEST=1 for the disposable host proof",
)
class SystemdContainmentIntegrationTests(unittest.TestCase):
    def _run_case(
        self,
        makefile: str,
        *,
        containment: clean_room_check.ContainmentBackend | None = None,
        extra_files: dict[str, str] | None = None,
        check_timeout: float = 5.0,
        terminate_grace: float = 0.5,
        kill_grace: float = 0.5,
        inspect_cache: Callable[[Path], None] | None = None,
        expected_extinction: bool = True,
        expected_containment_unit: bool = True,
        emit_hook: Callable[[dict[str, object]], None] | None = None,
        broker_factory: Callable[[Path], clean_room_check.BrokerRuntime] | None = None,
    ) -> tuple[clean_room_check.CleanRoomReceipt, list[dict[str, object]]]:
        with (
            tempfile.TemporaryDirectory(dir="/dev/shm") as source_raw,
            tempfile.TemporaryDirectory(dir="/tmp") as cache_raw,
        ):
            source = Path(source_raw) / "repository"
            source.mkdir()
            (source / "Makefile").write_text(makefile, encoding="utf-8")
            (source / "scripts").mkdir()
            shutil.copy2(
                REPO_ROOT / "scripts" / "clean_room_check.py",
                source / "scripts" / "clean_room_check.py",
            )
            fixture_files = {
                "deploy/chiap01/compose.dev.yml": "services: {}\n",
                **(extra_files or {}),
            }
            for name, value in fixture_files.items():
                destination = source / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(value, encoding="utf-8")
            entries = [
                "Makefile",
                "scripts/clean_room_check.py",
                *fixture_files,
            ]
            git_stdout = b"\0".join(item.encode() for item in entries) + b"\0"
            cache = Path(cache_raw)
            uv_cache = cache / "uv"
            npm_cache = cache / "npm"
            uv_cache.mkdir()
            npm_cache.mkdir()
            events: list[dict[str, object]] = []

            def emit(event: dict[str, object]) -> None:
                events.append(event)
                if emit_hook is not None:
                    emit_hook(event)

            receipt = clean_room_check.run(
                repo_root=source,
                environment={
                    "SKLEGAL_CLEAN_ROOM_ROOT": "/tmp",
                    "UV_CACHE_DIR": str(uv_cache),
                    "npm_config_cache": str(npm_cache),
                },
                source_runner=lambda *args, **kwargs: subprocess.CompletedProcess(
                    [], 0, stdout=git_stdout
                ),
                containment=containment,
                broker_factory=broker_factory or clean_room_check._create_brokers,
                check_timeout=check_timeout,
                terminate_grace=terminate_grace,
                kill_grace=kill_grace,
                progress_interval=0.1,
                emit=emit,
            )
            if inspect_cache is not None:
                inspect_cache(npm_cache)
            self.assertTrue(receipt.temporary_cleaned)
            self.assertEqual(expected_extinction, receipt.containment_extinct)
            if expected_containment_unit:
                self.assertIsNotNone(receipt.containment_unit)
            else:
                self.assertIsNone(receipt.containment_unit)
            self.assertEqual("receipt", events[-1]["event"])
            return receipt, events

    def test_sigterm_child_boundary(self) -> None:
        boundary = os.environ.get("SKLEGAL_TEST_SIGTERM_BOUNDARY")
        if boundary is None:
            self.skipTest("subprocess-only SIGTERM helper")
        containment_boundaries = {
            "before_spawn",
            "process_bound",
            "observed_bound",
            "run",
        }
        broker_boundaries = {
            *(f"docker-worker-{index}-started" for index in range(4)),
            "docker-serve-started",
            *(f"gpg-worker-{index}-started" for index in range(4)),
            "gpg-serve-started",
            "gpg-serve-started-long-callers",
        }
        self.assertIn(boundary, containment_boundaries | broker_boundaries)
        injected = False
        brokers: list[clean_room_check.ExternalBrokerSet] = []
        callers: list[clean_room_check.socket.socket] = []

        def terminate_at_lifecycle(phase: str) -> None:
            nonlocal injected
            target = (
                "gpg-serve-started"
                if boundary == "gpg-serve-started-long-callers"
                else boundary
            )
            if phase == target and not injected:
                injected = True
                if boundary == "gpg-serve-started-long-callers":
                    self.assertTrue(brokers)
                    for endpoint in brokers[0].environment.values():
                        if not isinstance(endpoint, str) or not endpoint.startswith(
                            "127.0.0.1:"
                        ):
                            continue
                        host, raw_port = endpoint.rsplit(":", 1)
                        for _ in range(16):
                            caller = clean_room_check.socket.create_connection(
                                (host, int(raw_port)),
                                timeout=2.0,
                            )
                            caller.sendall(clean_room_check.struct.pack("!I", 1024))
                            callers.append(caller)
                os.kill(os.getpid(), clean_room_check.signal.SIGTERM)

        def terminate_while_running(event: dict[str, object]) -> None:
            nonlocal injected
            if (
                boundary == "run"
                and event.get("event") == "phase"
                and event.get("phase") == "gate-started"
                and not injected
            ):
                injected = True
                os.kill(os.getpid(), clean_room_check.signal.SIGTERM)

        containment = clean_room_check.SystemdContainment(
            lifecycle_probe=terminate_at_lifecycle,
        )

        def broker_factory(workspace: Path) -> clean_room_check.ExternalBrokerSet:
            broker = clean_room_check.ExternalBrokerSet(
                workspace,
                lifecycle_probe=terminate_at_lifecycle,
            )
            brokers.append(broker)
            return broker

        try:
            receipt, _ = self._run_case(
                "check:\n\t/usr/bin/sleep 30\n",
                containment=containment,
                emit_hook=terminate_while_running,
                broker_factory=broker_factory,
                expected_containment_unit=boundary in containment_boundaries,
            )
        finally:
            for caller in callers:
                caller.close()
        self.assertTrue(injected)
        self.assertEqual("cancelled", receipt.status)
        self.assertEqual("cancelled", receipt.reason)
        self.assertTrue(receipt.external_brokers_cleaned)
        self.assertTrue(all(broker.thread_count == 0 for broker in brokers))
        if boundary in containment_boundaries:
            assert receipt.containment_unit is not None
            self._assert_no_unit_residue(receipt.containment_unit)
        else:
            self.assertIsNone(receipt.containment_unit)

    def test_real_sigterm_at_every_launch_and_run_boundary_cleans(self) -> None:
        helper = (
            "tests.integration.test_clean_room_containment."
            "SystemdContainmentIntegrationTests.test_sigterm_child_boundary"
        )
        for boundary in ("before_spawn", "process_bound", "observed_bound", "run"):
            with self.subTest(boundary=boundary):
                environment = dict(os.environ)
                environment["SKLEGAL_TEST_SIGTERM_BOUNDARY"] = boundary
                process = subprocess.run(
                    [
                        sys.executable,
                        "-W",
                        "error::ResourceWarning",
                        "-m",
                        "unittest",
                        "-v",
                        helper,
                    ],
                    cwd=REPO_ROOT,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30.0,
                )
                self.assertEqual(
                    0,
                    process.returncode,
                    (process.stdout + process.stderr)[-4000:],
                )

    def test_real_sigterm_after_every_broker_thread_start_cleans(self) -> None:
        helper = (
            "tests.integration.test_clean_room_containment."
            "SystemdContainmentIntegrationTests.test_sigterm_child_boundary"
        )
        boundaries = [
            *(f"docker-worker-{index}-started" for index in range(4)),
            "docker-serve-started",
            *(f"gpg-worker-{index}-started" for index in range(4)),
            "gpg-serve-started",
        ]
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                environment = dict(os.environ)
                environment["SKLEGAL_TEST_SIGTERM_BOUNDARY"] = boundary
                process = subprocess.run(
                    [
                        sys.executable,
                        "-W",
                        "error::ResourceWarning",
                        "-m",
                        "unittest",
                        "-v",
                        helper,
                    ],
                    cwd=REPO_ROOT,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30.0,
                )
                self.assertEqual(
                    0,
                    process.returncode,
                    (process.stdout + process.stderr)[-4000:],
                )

    def test_real_sigterm_with_repeated_long_lived_broker_callers_cleans(self) -> None:
        helper = (
            "tests.integration.test_clean_room_containment."
            "SystemdContainmentIntegrationTests.test_sigterm_child_boundary"
        )
        environment = dict(os.environ)
        environment["SKLEGAL_TEST_SIGTERM_BOUNDARY"] = "gpg-serve-started-long-callers"
        process = subprocess.run(
            [
                sys.executable,
                "-W",
                "error::ResourceWarning",
                "-m",
                "unittest",
                "-v",
                helper,
            ],
            cwd=REPO_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30.0,
        )
        self.assertEqual(
            0,
            process.returncode,
            (process.stdout + process.stderr)[-4000:],
        )

    def _assert_no_unit_residue(self, unit_name: str) -> None:
        result = subprocess.run(
            [
                "/usr/bin/systemctl",
                "--user",
                "show",
                unit_name,
                "--property=ActiveState",
            ],
            check=False,
            capture_output=True,
            env=clean_room_check._controller_environment(),
        )
        if result.returncode == 0:
            self.assertNotIn(b"ActiveState=active", result.stdout)
        expected = clean_room_check.SystemdContainment._expected_control_group(
            unit_name
        )
        self.assertFalse((clean_room_check.CGROUP_ROOT / expected.lstrip("/")).exists())

    def test_all_terminal_paths_extinguish_the_transient_cgroup(self) -> None:
        passed, _ = self._run_case("check:\n\t/usr/bin/true\n")
        self.assertEqual("passed", passed.status)

        failed, _ = self._run_case("check:\n\t/usr/bin/false\n")
        self.assertEqual("failed", failed.status)
        self.assertEqual("gate_failed", failed.reason)

        cancelled, _ = self._run_case(
            "check:\n\t/usr/bin/sleep 30\n",
            containment=_InjectedWaitContainment(KeyboardInterrupt()),
        )
        self.assertEqual("cancelled", cancelled.status)

        exception, _ = self._run_case(
            "check:\n\t/usr/bin/sleep 30\n",
            containment=_InjectedWaitContainment(RuntimeError("synthetic")),
        )
        self.assertEqual("failed", exception.status)
        self.assertEqual("internal_error", exception.reason)

    def test_binding_interrupt_and_controller_outage_leave_zero_residue(
        self,
    ) -> None:
        for fault in ("interrupt", "outage"):
            with self.subTest(fault=fault):
                containment = _BindingFaultContainment(fault)
                receipt, _ = self._run_case(
                    "check:\n\t/usr/bin/sleep 30\n",
                    containment=containment,
                    expected_extinction=fault == "interrupt",
                )
                assert receipt.containment_unit is not None
                self._assert_no_unit_residue(receipt.containment_unit)
                if fault == "interrupt":
                    self.assertEqual("cancelled", receipt.status)
                else:
                    self.assertEqual("failed", receipt.status)
                    self.assertEqual("containment_not_extinct", receipt.reason)
                    self.assertTrue(containment.direct_kill_used)

    def test_atomic_registration_owns_every_spawn_interrupt_boundary(self) -> None:
        for boundary in (
            "before_spawn",
            "factory_after_spawn",
            "process_bound",
            "observed_bound",
        ):
            with self.subTest(boundary=boundary):
                containment = _LifecycleFaultContainment(boundary)
                receipt, _ = self._run_case(
                    "check:\n\t/usr/bin/sleep 30\n",
                    containment=containment,
                )
                if containment.spawned_process is not None:
                    containment.spawned_process.wait(timeout=5.0)
                assert receipt.containment_unit is not None
                self._assert_no_unit_residue(receipt.containment_unit)
                self.assertEqual("cancelled", receipt.status)

    def test_payload_receives_only_the_closed_environment(self) -> None:
        observed: dict[str, str] = {}
        helper = """import json
import os
from pathlib import Path

environment = dict(os.environ)
for name in (
    "SKLEGAL_BROKER_TOKEN",
    "SKLEGAL_DOCKER_BROKER",
    "SKLEGAL_GPG_BROKER",
):
    environment[name] = "<present>" if environment.get(name) else "<missing>"
Path(os.environ["npm_config_cache"], "environment.json").write_text(
    json.dumps(environment, sort_keys=True),
    encoding="utf-8",
)
"""
        receipt, _ = self._run_case(
            "check:\n\t/usr/bin/python3 environment.py\n",
            extra_files={"environment.py": helper},
            inspect_cache=lambda cache: observed.update(
                json.loads((cache / "environment.json").read_text(encoding="utf-8"))
            ),
        )
        self.assertEqual("passed", receipt.status)
        child = observed
        self.assertEqual("", child.pop("MAKEFLAGS"))
        self.assertEqual("", child.pop("MFLAGS"))
        self.assertEqual("1", child.pop("MAKELEVEL"))
        self.assertEqual("<present>", child.pop("SKLEGAL_BROKER_TOKEN"))
        self.assertEqual("<present>", child.pop("SKLEGAL_DOCKER_BROKER"))
        self.assertEqual("<present>", child.pop("SKLEGAL_GPG_BROKER"))
        self.assertEqual(
            {
                "HOME",
                "LANG",
                "LC_ALL",
                "PATH",
                "TMPDIR",
                "UV_CACHE_DIR",
                "UV_LINK_MODE",
                "npm_config_cache",
            },
            set(child),
        )

    def test_filesystem_sandbox_denies_workspace_and_scratch_root_rename(self) -> None:
        observed: dict[str, object] = {}
        helper = """import json
import os
from pathlib import Path

workspace = Path.cwd()
scratch = workspace.parent
results = {}
for label, source in (("workspace", workspace), ("scratch", scratch)):
    destination = source.with_name(source.name + "-rename-attempt")
    try:
        source.rename(destination)
    except OSError:
        results[label] = "denied"
    else:
        results[label] = "allowed"
        destination.rename(source)

result_path = Path(os.environ["npm_config_cache"]) / "rename-results.json"
result_path.write_text(json.dumps(results, sort_keys=True), encoding="utf-8")
if {results["workspace"], results["scratch"]} != {"denied"}:
    raise SystemExit(1)
"""
        receipt, _ = self._run_case(
            "check:\n\t/usr/bin/python3 rename_check.py\n",
            extra_files={"rename_check.py": helper},
            inspect_cache=lambda cache: observed.update(
                json.loads((cache / "rename-results.json").read_text(encoding="utf-8"))
            ),
        )
        self.assertEqual(
            {"workspace": "denied", "scratch": "denied"},
            {key: observed[key] for key in ("workspace", "scratch")},
            observed,
        )
        self.assertEqual("passed", receipt.status)

    def test_landlock_allows_bootstrap_io_but_denies_raw_docker_socket(
        self,
    ) -> None:
        observed: dict[str, object] = {}
        helper = """import json
import os
import socket
import subprocess
from pathlib import Path

workspace = Path.cwd()
uv_cache = Path(os.environ["UV_CACHE_DIR"])
npm_cache = Path(os.environ["npm_config_cache"])
for path in (
    workspace / ".tools" / "bootstrap-probe",
    workspace / "node_modules" / "bootstrap-probe",
    uv_cache / "bootstrap-probe",
    npm_cache / "bootstrap-probe",
):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ok\\n", encoding="ascii")

node = subprocess.run(
    ["/usr/bin/timeout", "--kill-after=1", "2", "/usr/bin/node", "--version"],
    check=True,
    capture_output=True,
    text=True,
)
try:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect("/run/docker.sock")
    finally:
        sock.close()
except OSError:
    docker_socket = "denied"
else:
    docker_socket = "connected"

(npm_cache / "bootstrap-results.json").write_text(
    json.dumps(
        {
            "docker_socket": docker_socket,
            "node_version": node.stdout.strip(),
            "workspace_tools": (workspace / ".tools" / "bootstrap-probe").read_text(
                encoding="ascii"
            ).strip(),
            "workspace_node_modules": (
                workspace / "node_modules" / "bootstrap-probe"
            ).read_text(encoding="ascii").strip(),
            "uv_cache": (uv_cache / "bootstrap-probe").read_text(
                encoding="ascii"
            ).strip(),
            "npm_cache": (npm_cache / "bootstrap-probe").read_text(
                encoding="ascii"
            ).strip(),
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
"""
        receipt, _ = self._run_case(
            "check:\n\t/usr/bin/python3 bootstrap_probe.py\n",
            extra_files={"bootstrap_probe.py": helper},
            inspect_cache=lambda cache: observed.update(
                json.loads(
                    (cache / "bootstrap-results.json").read_text(encoding="utf-8")
                )
            ),
        )
        self.assertEqual("passed", receipt.status)
        self.assertEqual("denied", observed["docker_socket"])
        self.assertRegex(str(observed["node_version"]), r"^v22\.")
        self.assertEqual(
            {"ok"},
            {
                observed["workspace_tools"],
                observed["workspace_node_modules"],
                observed["uv_cache"],
                observed["npm_cache"],
            },
        )

    def test_external_manager_paths_and_nested_user_service_are_denied(self) -> None:
        observed: dict[str, object] = {}
        unit_name = f"sklegal-escape-probe-{os.getpid()}-{time.monotonic_ns()}"
        helper = f"""import json
import os
import socket
import subprocess
from pathlib import Path

results = {{}}
for executable in (
    "/usr/bin/docker",
    "/usr/bin/gpg",
    "/usr/bin/gpgconf",
    "/usr/bin/systemctl",
    "/usr/bin/systemd-run",
):
    try:
        process = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
        )
    except OSError:
        results[executable] = "denied"
    else:
        results[executable] = f"allowed:{{process.returncode}}"

try:
    nested = subprocess.run(
        [
            "/usr/bin/systemd-run",
            "--user",
            "--quiet",
            "--unit={unit_name}",
            "--property=ExitType=cgroup",
            "/usr/bin/sleep",
            "30",
        ],
        check=False,
        capture_output=True,
    )
except OSError:
    results["nested_systemd"] = "denied"
else:
    results["nested_systemd"] = f"allowed:{{nested.returncode}}"

for label, path in (
    ("docker_socket", "/run/docker.sock"),
    ("user_bus_socket", f"/run/user/{{os.getuid()}}/bus"),
):
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(path)
        finally:
            sock.close()
    except OSError:
        results[label] = "denied"
    else:
        results[label] = "connected"

Path(os.environ["npm_config_cache"], "manager-results.json").write_text(
    json.dumps(results, sort_keys=True),
    encoding="utf-8",
)
"""
        try:
            receipt, _ = self._run_case(
                "check:\n\t/usr/bin/python3 manager_probe.py\n",
                extra_files={"manager_probe.py": helper},
                inspect_cache=lambda cache: observed.update(
                    json.loads(
                        (cache / "manager-results.json").read_text(encoding="utf-8")
                    )
                ),
            )
        finally:
            subprocess.run(
                ["/usr/bin/systemctl", "--user", "stop", f"{unit_name}.service"],
                check=False,
                capture_output=True,
                env=clean_room_check._controller_environment(),
            )
            subprocess.run(
                [
                    "/usr/bin/systemctl",
                    "--user",
                    "reset-failed",
                    f"{unit_name}.service",
                ],
                check=False,
                capture_output=True,
                env=clean_room_check._controller_environment(),
            )
        self.assertEqual("passed", receipt.status)
        self.assertEqual(
            {
                "/usr/bin/docker": "denied",
                "/usr/bin/gpg": "denied",
                "/usr/bin/gpgconf": "denied",
                "/usr/bin/systemctl": "denied",
                "/usr/bin/systemd-run": "denied",
                "docker_socket": "denied",
                "nested_systemd": "denied",
                "user_bus_socket": "denied",
            },
            observed,
        )
        self._assert_no_unit_residue(f"{unit_name}.service")

    def test_closed_brokers_allow_only_disposable_postgres_and_synthetic_gpg(
        self,
    ) -> None:
        observed: dict[str, object] = {}
        container = f"sklegal-s102-{os.getpid()}-{time.monotonic_ns():x}"[-63:]
        second_container = f"sklegal-s102-{os.getpid()}-{time.monotonic_ns() + 1:x}"[
            -63:
        ]
        compose = """services:
  postgres:
    image: postgres:17.7-alpine@sha256:a6d31f853205ce20d399df4e33a0b4c715672f232f4ee7440499747e6e02c126
"""
        helper = f'''import json
import os
import shutil
import subprocess
from pathlib import Path

workspace = Path.cwd()
results = {{
    "docker_path": shutil.which("docker"),
    "gpg_path": shutil.which("gpg"),
}}
try:
    results["private_state"] = (
        "visible"
        if any(name.startswith("sklegal-broker-") for name in os.listdir("/tmp"))
        else "missing"
    )
except OSError:
    results["private_state"] = "denied"
compose = workspace / "deploy" / "chiap01" / "compose.dev.yml"
configured = subprocess.run(
    ["docker", "compose", "--file", str(compose), "config", "--format", "json"],
    check=True,
    capture_output=True,
    text=True,
)
results["compose"] = "postgres" if "postgres" in configured.stdout else "missing"

run_argv = [
        "docker", "run", "--detach", "--rm", "--name", "{container}",
        "--label", "com.sklegal.test-card=SKL-S1-02",
        "--network", "none",
        "--tmpfs", "/var/lib/postgresql/data:rw,noexec,nosuid,size=512m",
        "--env", "POSTGRES_DB=sklegal",
        "--env", "POSTGRES_USER=postgres",
        "--env", "POSTGRES_HOST_AUTH_METHOD=trust",
        "postgres:17.7-alpine@sha256:a6d31f853205ce20d399df4e33a0b4c715672f232f4ee7440499747e6e02c126",
]
subprocess.run(
    run_argv,
    check=True,
    capture_output=True,
)
try:
    inspection = json.loads(subprocess.run(
        ["docker", "inspect", "{container}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout)[0]
    host_config = inspection["HostConfig"]
    results["container_limits"] = {{
        "cmd": inspection["Config"]["Cmd"],
        "entrypoint": inspection["Config"]["Entrypoint"],
        "memory": host_config["Memory"],
        "memory_swap": host_config["MemorySwap"],
        "nano_cpus": host_config["NanoCpus"],
        "pids_limit": host_config["PidsLimit"],
        "stop_timeout": inspection["Config"].get("StopTimeout"),
        "ulimits": host_config["Ulimits"],
    }}
    for _ in range(40):
        ready = subprocess.run(
            ["docker", "exec", "{container}", "pg_isready", "--username", "postgres"],
            check=False,
            capture_output=True,
        )
        if ready.returncode == 0:
            break
        __import__("time").sleep(0.1)
    else:
        raise RuntimeError("postgres did not become ready")
    results["postgres"] = "ready"
    for _ in range(40):
        database_ready = subprocess.run(
            [
                "docker", "exec", "-i", "{container}", "psql",
                "--username", "postgres", "--dbname", "sklegal",
                "--no-align", "--tuples-only", "--no-psqlrc",
            ],
            input=b"SELECT 1;",
            check=False,
            capture_output=True,
        )
        if database_ready.returncode == 0:
            break
        __import__("time").sleep(0.1)
    else:
        raise RuntimeError("postgres database did not become ready")
    oversized = subprocess.run(
        [
            "docker", "exec", "-i", "{container}", "psql",
            "--username", "postgres", "--dbname", "sklegal",
            "--no-align", "--tuples-only", "--no-psqlrc",
        ],
        input=b"SELECT repeat('x', 5 * 1024 * 1024);",
        check=False,
        capture_output=True,
    )
    results["oversized_psql"] = {{
        "returncode": oversized.returncode,
        "stdout": oversized.stdout.decode("utf-8", errors="replace"),
        "stderr": oversized.stderr.decode("utf-8", errors="replace"),
    }}
    ready_after_denial = subprocess.run(
        ["docker", "exec", "{container}", "pg_isready", "--username", "postgres"],
        check=False,
        capture_output=True,
    )
    results["postgres_after_output_denial"] = ready_after_denial.returncode
finally:
    subprocess.run(
        ["docker", "rm", "--force", "{container}"],
        check=False,
        capture_output=True,
    )
second_run = list(run_argv)
second_run[second_run.index("{container}")] = "{second_container}"
results["second_container"] = subprocess.run(
    second_run,
    check=False,
    capture_output=True,
).returncode

keyring = workspace / "synthetic-gnupg"
keyring.mkdir(mode=0o700)
gpg_environment = {{**os.environ, "GNUPGHOME": str(keyring)}}
subprocess.run(
    [
        "gpg", "--batch", "--passphrase", "", "--quick-generate-key",
        "SKLegal Synthetic Test <synthetic@example.invalid>",
        "ed25519", "sign", "1d",
    ],
    env=gpg_environment,
    check=True,
    capture_output=True,
)
listing = subprocess.run(
    ["gpg", "--batch", "--with-colons", "--list-secret-keys"],
    env=gpg_environment,
    check=True,
    capture_output=True,
    text=True,
)
results["gpg"] = "synthetic@example.invalid" if "synthetic@example.invalid" in listing.stdout else "missing"
results["docker_malformed"] = subprocess.run(
    ["docker", "image", "ls"],
    check=False,
    capture_output=True,
).returncode
outside = {{**os.environ, "GNUPGHOME": "/tmp"}}
results["gpg_malformed"] = subprocess.run(
    ["gpg", "--batch", "--with-colons", "--list-secret-keys"],
    env=outside,
    check=False,
    capture_output=True,
).returncode
Path(os.environ["npm_config_cache"], "broker-results.json").write_text(
    json.dumps(results, sort_keys=True),
    encoding="utf-8",
)
'''
        try:
            receipt, events = self._run_case(
                "check:\n\t/usr/bin/python3 broker_probe.py\n",
                extra_files={
                    "broker_probe.py": helper,
                    "deploy/chiap01/compose.dev.yml": compose,
                },
                check_timeout=20.0,
                inspect_cache=lambda cache: observed.update(
                    json.loads(
                        (cache / "broker-results.json").read_text(encoding="utf-8")
                    )
                ),
            )
        finally:
            subprocess.run(
                ["/usr/bin/docker", "rm", "--force", container],
                check=False,
                capture_output=True,
            )
            subprocess.run(
                ["/usr/bin/docker", "rm", "--force", second_container],
                check=False,
                capture_output=True,
            )
        self.assertEqual("passed", receipt.status)
        self.assertEqual("postgres", observed["compose"])
        self.assertEqual("ready", observed["postgres"])
        self.assertEqual(
            {
                "returncode": 126,
                "stdout": "",
                "stderr": "broker output limit exceeded\n",
            },
            observed["oversized_psql"],
        )
        self.assertEqual(0, observed["postgres_after_output_denial"])
        self.assertEqual("synthetic@example.invalid", observed["gpg"])
        self.assertEqual(126, observed["second_container"])
        self.assertEqual(
            {
                "cmd": [
                    "-s",
                    "TERM",
                    "-k",
                    "30",
                    "900",
                    "/usr/local/bin/docker-entrypoint.sh",
                    "postgres",
                ],
                "entrypoint": ["/usr/bin/timeout"],
                "memory": 1024 * 1024 * 1024,
                "memory_swap": 1024 * 1024 * 1024,
                "nano_cpus": 2_000_000_000,
                "pids_limit": 256,
                "stop_timeout": 30,
                "ulimits": [
                    {
                        "Hard": 1024,
                        "Name": "nofile",
                        "Soft": 1024,
                    }
                ],
            },
            observed["container_limits"],
        )
        self.assertEqual("denied", observed["private_state"])
        self.assertNotEqual(0, observed["docker_malformed"])
        self.assertNotEqual(0, observed["gpg_malformed"])
        self.assertTrue(str(observed["docker_path"]).endswith("/.clean-bin/docker"))
        self.assertTrue(str(observed["gpg_path"]).endswith("/.clean-bin/gpg"))
        self.assertNotIn("TOKEN", json.dumps(events[-1], sort_keys=True).upper())
        self.assertEqual(1, receipt.broker_resources["containers_created"])
        self.assertEqual(1, receipt.broker_resources["keyrings_created"])
        self.assertGreater(receipt.broker_resources["requests_denied"], 0)
        self.assertGreater(receipt.broker_resources["connection_attempts"], 0)
        self.assertEqual(
            receipt.broker_resources["request_bytes"],
            receipt.broker_resources["wire_bytes_received"],
        )
        self.assertEqual(
            receipt.broker_resources["response_bytes"],
            receipt.broker_resources["wire_bytes_sent"],
        )
        self.assertEqual(0, receipt.broker_resources["private_files_current"])
        self.assertEqual(0, receipt.broker_resources["private_bytes_current"])
        remaining = subprocess.run(
            [
                "/usr/bin/docker",
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"name=^{container}$",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual("", remaining.stdout.strip())

    def test_real_brokers_resist_gpg_home_and_compose_path_swaps(self) -> None:
        with (
            tempfile.TemporaryDirectory(
                prefix="sklegal-broker-swap-",
                dir="/tmp",
            ) as workspace_raw,
            tempfile.TemporaryDirectory(
                prefix="sklegal-broker-outside-",
                dir="/tmp",
            ) as outside_raw,
        ):
            workspace = Path(workspace_raw)
            outside = Path(outside_raw)
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("unchanged\n", encoding="utf-8")
            compose = workspace / "deploy" / "chiap01" / "compose.dev.yml"
            compose.parent.mkdir(parents=True)
            compose.write_text("services: {}\n", encoding="utf-8")
            logical_home = workspace / "synthetic-gnupg"
            logical_home.mkdir(mode=0o700)
            broker = clean_room_check.ExternalBrokerSet(workspace)
            broker.start()
            private_root = broker._private_workspace.path
            environment = {
                "GNUPGHOME": str(logical_home),
                "HOME": str(workspace),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": f"{workspace / '.clean-bin'}:/usr/bin",
                **broker.environment,
            }
            agent_sockets: tuple[Path, ...] = ()
            try:
                subprocess.run(
                    [
                        "gpg",
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
                    env=environment,
                    check=True,
                    capture_output=True,
                )
                listing = subprocess.run(
                    ["gpg", "--batch", "--with-colons", "--list-secret-keys"],
                    cwd=workspace,
                    env=environment,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
                fingerprint = next(
                    line.split(":")[9]
                    for line in listing.splitlines()
                    if line.startswith("fpr:")
                )

                parked_home = workspace / "logical-home-before-swap"
                logical_home.rename(parked_home)
                logical_home.symlink_to(outside, target_is_directory=True)
                compose_before_swap = compose.with_name("compose.before-swap.yml")
                compose.rename(compose_before_swap)
                compose.symlink_to(sentinel)

                payload = workspace / "payload.json"
                payload.write_text('{"synthetic":true}\n', encoding="utf-8")
                signature = workspace / "payload.json.asc"
                signed = subprocess.run(
                    [
                        "gpg",
                        "--batch",
                        "--yes",
                        "--armor",
                        "--detach-sign",
                        "--local-user",
                        fingerprint,
                    ],
                    cwd=workspace,
                    env=environment,
                    input=payload.read_bytes(),
                    check=True,
                    capture_output=True,
                )
                signature.write_bytes(signed.stdout)
                subprocess.run(
                    ["gpg", "--batch", "--verify", str(signature), str(payload)],
                    cwd=workspace,
                    env=environment,
                    check=True,
                    capture_output=True,
                )
                configured = subprocess.run(
                    [
                        "docker",
                        "compose",
                        "--file",
                        str(compose),
                        "config",
                        "--format",
                        "json",
                    ],
                    cwd=workspace,
                    env=environment,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual({}, json.loads(configured.stdout)["services"])
                self.assertEqual([sentinel], list(outside.iterdir()))
                agent_sockets = tuple(
                    Path(
                        subprocess.run(
                            [
                                "/usr/bin/gpgconf",
                                "--homedir",
                                str(keyring.path),
                                "--list-dirs",
                                "agent-socket",
                            ],
                            check=True,
                            capture_output=True,
                            text=True,
                            timeout=5.0,
                        ).stdout.strip()
                    )
                    for keyring in broker._gpg_keyrings.values()
                )
            finally:
                cleaned = broker.close()
            self.assertTrue(cleaned)
            self.assertFalse(private_root.exists())
            self.assertFalse(any(path.is_socket() for path in agent_sockets))
            self.assertEqual("unchanged\n", sentinel.read_text(encoding="utf-8"))
            self.assertEqual([sentinel], list(outside.iterdir()))

    def test_timeout_kills_setsid_and_double_fork_descendants(self) -> None:
        observed_rows: list[list[str]] = []
        helper = """import os
import signal
import time
from pathlib import Path

pid_file = Path(os.environ["npm_config_cache"], "pids.txt")
signal.signal(signal.SIGTERM, signal.SIG_IGN)

def record(label: str) -> None:
    with pid_file.open("a", encoding="ascii") as stream:
        stream.write(f"{label} {os.getpid()}\\n")
        stream.flush()
        os.fsync(stream.fileno())

record("parent")
if os.fork() == 0:
    os.setsid()
    record("setsid")
    if os.fork() != 0:
        os._exit(0)
    record("double-fork")
    time.sleep(30)
time.sleep(30)
"""
        receipt, events = self._run_case(
            "check:\n\t/usr/bin/python3 descendants.py\n",
            extra_files={"descendants.py": helper},
            check_timeout=0.75,
            terminate_grace=0.25,
            kill_grace=1.0,
            inspect_cache=lambda cache: observed_rows.extend(
                line.split()
                for line in (cache / "pids.txt")
                .read_text(encoding="ascii")
                .splitlines()
            ),
        )
        self.assertEqual("timed_out", receipt.status)
        self.assertIn(
            "timeout-kill",
            [event.get("phase") for event in events if event["event"] == "phase"],
        )
        rows = observed_rows
        self.assertEqual({"parent", "setsid", "double-fork"}, {row[0] for row in rows})
        for _, raw_pid in rows:
            self.assertFalse(Path(f"/proc/{raw_pid}").exists())

    def test_brokers_run_exact_foundation_capauth_and_persistence_contracts(
        self,
    ) -> None:
        shim_root = REPO_ROOT / ".clean-bin"
        self.assertFalse(shim_root.exists())
        broker = clean_room_check.ExternalBrokerSet(REPO_ROOT)
        broker.start()
        private_root = broker._private_workspace.path
        temporary_context = tempfile.TemporaryDirectory(
            prefix=".clean-broker-contract-",
            dir=REPO_ROOT,
        )
        cleaned = False
        agent_sockets: tuple[Path, ...] = ()
        resources: dict[str, int] = {}
        try:
            temporary = Path(temporary_context.name)
            home = temporary / "home"
            scratch = temporary / "tmp"
            home.mkdir(mode=0o700)
            scratch.mkdir(mode=0o700)
            environment = {
                "HOME": str(home),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": f"{shim_root}:/usr/bin",
                "TMPDIR": str(scratch),
                "UV_CACHE_DIR": str(REPO_ROOT / ".tools" / "uv-cache"),
                "UV_LINK_MODE": "copy",
                "npm_config_cache": str(REPO_ROOT / ".tools" / "npm-cache"),
                **broker.environment,
            }
            process = subprocess.run(
                [
                    str(REPO_ROOT / ".tools" / "bin" / "uv"),
                    "run",
                    "--locked",
                    "python",
                    "-m",
                    "unittest",
                    "-v",
                    "tests.integration.test_foundation_contract",
                    "tests.integration.test_capauth_contract",
                    "tests.integration.test_persistence_contract",
                ],
                cwd=REPO_ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=240.0,
            )
            tracked_homes = tuple(
                sorted(keyring.path for keyring in broker._gpg_keyrings.values())
            )
            self.assertTrue(tracked_homes)
            agent_sockets = tuple(
                Path(
                    subprocess.run(
                        [
                            "/usr/bin/gpgconf",
                            "--homedir",
                            str(tracked_home),
                            "--list-dirs",
                            "agent-socket",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=5.0,
                    ).stdout.strip()
                )
                for tracked_home in tracked_homes
            )
            resources = broker.resource_counts
            self.assertFalse(
                any(
                    "gpg-inputs" in entry.path.parts
                    for entry in broker._private_entries
                )
            )
        finally:
            cleaned = broker.close()
            resources = broker.resource_counts
            temporary_context.cleanup()
            if shim_root.is_dir() and not shim_root.is_symlink():
                shutil.rmtree(shim_root)
        self.assertTrue(cleaned)
        self.assertFalse(private_root.exists())
        self.assertFalse(any(path.is_socket() for path in agent_sockets))
        self.assertFalse(shim_root.exists())
        self.assertEqual(
            0,
            process.returncode,
            (process.stdout + process.stderr)[-8000:],
        )
        self.assertIn("Ran 40 tests", process.stderr)
        self.assertIn("OK", process.stderr)
        self.assertEqual(1, resources["containers_created"])
        self.assertEqual(1, resources["keyrings_created"])
        self.assertEqual(0, resources["private_files_current"])
        self.assertEqual(0, resources["private_bytes_current"])
        self.assertEqual(
            resources["private_files_created"], resources["private_files_removed"]
        )
        self.assertEqual(
            resources["private_bytes_created"], resources["private_bytes_removed"]
        )
        self.assertLess(
            resources["requests_accepted"], clean_room_check.BROKER_LIMITS.max_requests
        )
        self.assertLess(
            resources["request_bytes"], clean_room_check.BROKER_LIMITS.max_request_bytes
        )
        self.assertLess(
            resources["response_bytes"],
            clean_room_check.BROKER_LIMITS.max_response_bytes,
        )
        self.assertLessEqual(
            resources["peak_workers"], clean_room_check.BROKER_LIMITS.max_workers
        )
        self.assertLessEqual(
            resources["peak_external_processes"],
            clean_room_check.BROKER_LIMITS.max_external_processes,
        )
        print(
            "broker-resource-counts=" + json.dumps(resources, sort_keys=True),
            file=sys.stderr,
        )


if __name__ == "__main__":
    unittest.main()
