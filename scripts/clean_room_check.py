#!/usr/bin/env python3
"""Run complete checks from a verified source copy in local scratch."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import math
import os
import queue
import re
import secrets
import selectors
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_OVERRIDE = "SKLEGAL_CLEAN_ROOM_ROOT"
CHECK_TIMEOUT_SECONDS = 900.0
TERMINATE_GRACE_SECONDS = 15.0
KILL_GRACE_SECONDS = 10.0
PROGRESS_INTERVAL_SECONDS = 30.0
SOURCE_TIMEOUT_SECONDS = 60.0
SOURCE_TERMINATE_GRACE_SECONDS = 5.0
SOURCE_KILL_GRACE_SECONDS = 5.0
CONTAINMENT_BIND_SECONDS = 5.0
SYSTEM_PATH = "/usr/bin"
TRUSTED_PATH_DIRECTORIES = (Path("/usr/bin"),)
SYSTEMD_RUN = Path("/usr/bin/systemd-run")
SYSTEMCTL = Path("/usr/bin/systemctl")
ENV = Path("/usr/bin/env")
GIT = Path("/usr/bin/git")
MAKE = Path("/usr/bin/make")
PYTHON = Path("/usr/bin/python3.12")
SLEEP = Path("/usr/bin/sleep")
DOCKER = Path("/usr/bin/docker")
GPG = Path("/usr/bin/gpg")
GPGCONF = Path("/usr/bin/gpgconf")
CGROUP_ROOT = Path("/sys/fs/cgroup")
POSTGRES_IMAGE = (
    "postgres:17.7-alpine@sha256:"
    "a6d31f85"
    "3205ce20"
    "d399df4e"
    "33a0b4c7"
    "15672f23"
    "2f4ee744"
    "0499747e"
    "6e02c126"
)
COMPOSE_RELATIVE = Path("deploy/chiap01/compose.dev.yml")
BROKER_REQUEST_LIMIT = 8 * 1024 * 1024
BROKER_IO_TIMEOUT = 120.0
LANDLOCK_EXECUTABLES = tuple(
    Path(path)
    for path in (
        "/usr/bin/bash",
        "/usr/bin/chmod",
        "/usr/bin/curl",
        "/usr/bin/cut",
        "/usr/bin/dash",
        "/usr/bin/dirname",
        "/usr/bin/env",
        "/usr/bin/false",
        "/usr/bin/find",
        "/usr/bin/git",
        "/usr/bin/grep",
        "/usr/bin/make",
        "/usr/bin/mkdir",
        "/usr/bin/mktemp",
        "/usr/bin/mv",
        "/usr/bin/node",
        "/usr/bin/python3.12",
        "/usr/bin/rg",
        "/usr/bin/rm",
        "/usr/bin/sed",
        "/usr/bin/sha256sum",
        "/usr/bin/sleep",
        "/usr/bin/sort",
        "/usr/bin/tail",
        "/usr/bin/tar",
        "/usr/bin/timeout",
        "/usr/bin/true",
        "/usr/bin/uname",
        "/usr/lib/node_modules/npm/bin/npm-cli.js",
        "/usr/lib/node_modules/npm/bin/npx-cli.js",
        "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
    )
)
LANDLOCK_READ_PATHS = tuple(
    Path(path)
    for path in (
        "/etc",
        "/proc",
        "/sys",
        "/usr",
        "/run/systemd/resolve",
    )
)
LANDLOCK_READ_DEVICES = tuple(Path(path) for path in ("/dev/random", "/dev/urandom"))
GENERATED_PARTS = frozenset(
    {
        ".clean-bin",
        ".clean-home",
        ".clean-tmp",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tools",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
    }
)
DEFAULT_UNSET_ENVIRONMENT = frozenset(
    {
        "ALL_PROXY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "BASH_ENV",
        "CLOUDSDK_CONFIG",
        "DOCKER_CONFIG",
        "ENV",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GPG_AGENT_INFO",
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "MAKEFLAGS",
        "NODE_OPTIONS",
        "NO_PROXY",
        "NPM_CONFIG_USERCONFIG",
        "PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "SSH_AUTH_SOCK",
        "XAUTHORITY",
        "XDG_CONFIG_HOME",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)


class SourceRunner(Protocol):
    def __call__(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        check: bool,
        capture_output: bool,
        pass_fds: tuple[int, ...],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]: ...


class WaitableProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float) -> int: ...


class ProcessFactory(Protocol):
    def __call__(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        start_new_session: bool,
    ) -> WaitableProcess: ...


class WorkspaceLease(Protocol):
    path: Path
    identity: tuple[int, int]

    def ensure_identity(self) -> None: ...

    def cleanup(self) -> bool: ...


class WorkspaceFactory(Protocol):
    def __call__(self, scratch_root: Path) -> WorkspaceLease: ...


class BrokerRuntime(Protocol):
    @property
    def environment(self) -> Mapping[str, str]: ...

    @property
    def resource_counts(self) -> Mapping[str, int]: ...

    def start(self) -> None: ...

    def close(self) -> bool: ...


class CleanRoomError(RuntimeError):
    """A bounded, sanitized clean-room contract failure."""

    def __init__(self, reason: str, *, exit_code: int = 1) -> None:
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code


class CleanRoomTimeout(CleanRoomError):
    """The nested gate exceeded its bounded runtime."""

    def __init__(self, reason: str = "gate_timeout") -> None:
        super().__init__(reason, exit_code=124)


class CleanRoomCancelled(CleanRoomError):
    """The caller cancelled qualification."""

    def __init__(self) -> None:
        super().__init__("cancelled", exit_code=130)


def _process_running(process: WaitableProcess) -> bool:
    try:
        return process.poll() is None
    except BaseException:
        return True


@dataclass(frozen=True)
class CleanRoomReceipt:
    """Machine-readable terminal result for one clean-room invocation."""

    status: str
    exit_code: int
    reason: str
    source_root: str
    scratch_root: str | None
    source_file_count: int
    source_inventory_sha256: str | None
    elapsed_seconds: float
    temporary_identity: str | None
    temporary_cleaned: bool
    containment_unit: str | None
    containment_extinct: bool
    external_brokers_cleaned: bool
    broker_resources: dict[str, int]

    def as_event(self) -> dict[str, object]:
        return {
            "event": "receipt",
            "schema": "sklegal-clean-room-receipt/v6",
            "status": self.status,
            "exit_code": self.exit_code,
            "reason": self.reason,
            "source_root": self.source_root,
            "scratch_root": self.scratch_root,
            "source_file_count": self.source_file_count,
            "source_inventory_sha256": self.source_inventory_sha256,
            "elapsed_seconds": self.elapsed_seconds,
            "temporary_identity": self.temporary_identity,
            "temporary_cleaned": self.temporary_cleaned,
            "containment_unit": self.containment_unit,
            "containment_extinct": self.containment_extinct,
            "external_brokers_cleaned": self.external_brokers_cleaned,
            "broker_resources": dict(self.broker_resources),
        }


def emit_event(event: dict[str, object]) -> None:
    """Emit one compact JSON event immediately for progress monitoring."""

    print(
        json.dumps(event, sort_keys=True, separators=(",", ":")),
        flush=True,
    )


def _phase(
    name: str,
    *,
    started: float,
    clock: Callable[[], float],
    emit: Callable[[dict[str, object]], None],
) -> None:
    emit(
        {
            "event": "phase",
            "phase": name,
            "elapsed_seconds": round(max(0.0, clock() - started), 3),
        }
    )


def _terminate_source_process(process: subprocess.Popen[bytes]) -> None:
    """Escalate a hung allowlist process group from TERM to a bounded KILL."""

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.communicate(timeout=SOURCE_TERMINATE_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.communicate(timeout=SOURCE_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        raise CleanRoomError("source_allowlist_cleanup_failed") from None


def _run_source_command(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    check: bool,
    capture_output: bool,
    pass_fds: tuple[int, ...],
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        pass_fds=pass_fds,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_source_process(process)
        raise CleanRoomTimeout("source_allowlist_timeout") from None
    completed = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    if check and completed.returncode:
        raise subprocess.CalledProcessError(
            completed.returncode,
            argv,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def _start_process(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    start_new_session: bool,
) -> WaitableProcess:
    return subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        start_new_session=start_new_session,
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_repo_root(repo_root: Path) -> Path:
    candidate = repo_root.absolute()
    try:
        metadata = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise CleanRoomError("source_root_unavailable") from None
    if candidate != resolved or stat.S_ISLNK(metadata.st_mode):
        raise CleanRoomError("source_root_is_link")
    if not stat.S_ISDIR(metadata.st_mode):
        raise CleanRoomError("source_root_not_directory")
    return resolved


def _validate_trusted_executable(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise CleanRoomError("trusted_executable_invalid")
    try:
        metadata = path.lstat()
        parent_metadata = path.parent.lstat()
    except OSError:
        raise CleanRoomError("trusted_executable_unavailable") from None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise CleanRoomError("trusted_executable_invalid")
    if metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise CleanRoomError("trusted_executable_untrusted")
    if not metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        raise CleanRoomError("trusted_executable_not_executable")
    if not stat.S_ISDIR(parent_metadata.st_mode) or parent_metadata.st_uid != 0:
        raise CleanRoomError("trusted_executable_parent_untrusted")
    if parent_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise CleanRoomError("trusted_executable_parent_untrusted")


def _validate_trusted_executables() -> None:
    for executable in (
        SYSTEMD_RUN,
        SYSTEMCTL,
        ENV,
        GIT,
        MAKE,
        PYTHON,
        SLEEP,
        DOCKER,
        GPG,
        GPGCONF,
        *LANDLOCK_EXECUTABLES,
    ):
        _validate_trusted_executable(executable)
    for directory in TRUSTED_PATH_DIRECTORIES:
        try:
            metadata = directory.lstat()
            resolved = directory.resolve(strict=True)
        except OSError:
            raise CleanRoomError("trusted_path_unavailable") from None
        if directory != resolved or not stat.S_ISDIR(metadata.st_mode):
            raise CleanRoomError("trusted_path_untrusted")
        if metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise CleanRoomError("trusted_path_untrusted")


def _runtime_anchor() -> Path | None:
    runtime = Path(f"/run/user/{os.geteuid()}")
    try:
        metadata = runtime.lstat()
        resolved = runtime.resolve(strict=True)
    except OSError:
        return None
    if runtime != resolved or not stat.S_ISDIR(metadata.st_mode):
        return None
    if metadata.st_uid != os.geteuid():
        return None
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return None
    return runtime


def _tmp_anchor() -> Path:
    anchor = Path("/tmp")
    try:
        metadata = anchor.lstat()
        resolved = anchor.resolve(strict=True)
    except OSError:
        raise CleanRoomError("local_scratch_unavailable") from None
    if anchor != resolved or not stat.S_ISDIR(metadata.st_mode):
        raise CleanRoomError("local_scratch_untrusted")
    if metadata.st_uid != 0 or not metadata.st_mode & stat.S_ISVTX:
        raise CleanRoomError("local_scratch_untrusted")
    return anchor


def _validate_scratch_override(raw: str, *, repo_root: Path) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise CleanRoomError("unsafe_scratch_path")
    try:
        metadata = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise CleanRoomError("scratch_root_unavailable") from None
    if candidate != resolved or stat.S_ISLNK(metadata.st_mode):
        raise CleanRoomError("unsafe_scratch_link")
    if not stat.S_ISDIR(metadata.st_mode):
        raise CleanRoomError("scratch_root_not_directory")

    anchors = [_tmp_anchor()]
    runtime = _runtime_anchor()
    if runtime is not None:
        anchors.append(runtime)
    if resolved not in anchors:
        raise CleanRoomError("scratch_root_must_be_exact_anchor")
    if _scratch_overlaps_repository(resolved, repo_root=repo_root):
        raise CleanRoomError("scratch_root_overlaps_repository")
    return resolved


def _scratch_overlaps_repository(scratch_root: Path, *, repo_root: Path) -> bool:
    """Return whether scratch could stage beside or inside the source tree."""

    repository = repo_root.resolve(strict=True)
    return scratch_root in {repository, *repository.parents} or _is_relative_to(
        scratch_root, repository.parent
    )


def resolve_scratch_root(
    *,
    environment: Mapping[str, str],
    repo_root: Path = REPO_ROOT,
) -> Path:
    """Choose only an exact trusted local anchor or a private descendant."""

    override = environment.get(SCRATCH_OVERRIDE)
    if override:
        return _validate_scratch_override(override, repo_root=repo_root)

    runtime = _runtime_anchor()
    expected_runtime = f"/run/user/{os.geteuid()}"
    if (
        runtime is not None
        and environment.get("XDG_RUNTIME_DIR")
        in {
            None,
            expected_runtime,
        }
        and not _scratch_overlaps_repository(runtime, repo_root=repo_root)
    ):
        return runtime
    temporary = _tmp_anchor()
    if _scratch_overlaps_repository(temporary, repo_root=repo_root):
        raise CleanRoomError("scratch_root_overlaps_repository")
    return temporary


def _validate_cache_root(path: Path, *, reason: str) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise CleanRoomError(reason)
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError:
        raise CleanRoomError(reason) from None
    if path != resolved or stat.S_ISLNK(metadata.st_mode):
        raise CleanRoomError(reason)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
        raise CleanRoomError(reason)
    if metadata.st_mode & stat.S_IWOTH:
        raise CleanRoomError(reason)
    if any(character.isspace() for character in str(resolved)):
        raise CleanRoomError(reason)
    if any(part in {"node_modules", "build", "dist", ".venv"} for part in path.parts):
        raise CleanRoomError(reason)
    return resolved


def _cache_roots(
    environment: Mapping[str, str], *, repo_root: Path
) -> tuple[Path, Path]:
    uv_raw = environment.get("UV_CACHE_DIR", str(repo_root / ".tools" / "uv-cache"))
    npm_raw = environment.get(
        "npm_config_cache",
        environment.get("NPM_CONFIG_CACHE", str(Path.home() / ".npm")),
    )
    return (
        _validate_cache_root(Path(uv_raw), reason="uv_cache_untrusted"),
        _validate_cache_root(Path(npm_raw), reason="npm_cache_untrusted"),
    )


def _validate_relative_path(path: Path) -> None:
    if path.is_absolute() or path == Path(".") or ".." in path.parts:
        raise CleanRoomError("unsafe_source_path")
    if any(part in GENERATED_PARTS for part in path.parts):
        raise CleanRoomError("generated_source_path")


def _git_environment() -> dict[str, str]:
    return {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def source_files(
    *,
    repo_root: Path = REPO_ROOT,
    runner: SourceRunner = _run_source_command,
    repo_root_fd: int | None = None,
    timeout: float = SOURCE_TIMEOUT_SECONDS,
) -> list[Path]:
    """Return the current Git-eligible source allowlist."""

    owned_descriptor = repo_root_fd is None
    descriptor = _open_directory(repo_root) if repo_root_fd is None else repo_root_fd
    try:
        _ensure_directory_identity(
            repo_root,
            descriptor,
            reason="source_root_identity_changed",
        )
        process = runner(
            [
                str(GIT),
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
            cwd=Path(f"/proc/self/fd/{descriptor}"),
            env=_git_environment(),
            check=True,
            capture_output=True,
            pass_fds=(descriptor,),
            timeout=timeout,
        )
        _ensure_directory_identity(
            repo_root,
            descriptor,
            reason="source_root_identity_changed",
        )
    except CleanRoomError:
        raise
    except Exception:
        raise CleanRoomError("source_allowlist_unavailable") from None
    finally:
        if owned_descriptor:
            os.close(descriptor)
    try:
        files = [
            Path(raw.decode("utf-8")) for raw in process.stdout.split(b"\0") if raw
        ]
    except UnicodeDecodeError:
        raise CleanRoomError("source_path_not_utf8") from None
    if not files:
        raise CleanRoomError("source_allowlist_empty")
    if len(files) != len(set(files)):
        raise CleanRoomError("source_allowlist_duplicate")
    for path in files:
        _validate_relative_path(path)
    return sorted(files)


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        return os.open(path, flags)
    except OSError:
        raise CleanRoomError("directory_descriptor_unavailable") from None


def _ensure_directory_identity(path: Path, descriptor: int, *, reason: str) -> None:
    try:
        descriptor_metadata = os.fstat(descriptor)
        path_metadata = path.stat(follow_symlinks=False)
    except OSError:
        raise CleanRoomError(reason) from None
    if not stat.S_ISDIR(descriptor_metadata.st_mode) or not stat.S_ISDIR(
        path_metadata.st_mode
    ):
        raise CleanRoomError(reason)
    if (
        descriptor_metadata.st_dev,
        descriptor_metadata.st_ino,
    ) != (
        path_metadata.st_dev,
        path_metadata.st_ino,
    ):
        raise CleanRoomError(reason)


def _open_parent(
    root_fd: int,
    parent_parts: Sequence[str],
    *,
    create: bool,
    reason: str,
) -> int:
    current = os.dup(root_fd)
    try:
        for part in parent_parts:
            if part in {"", ".", ".."}:
                raise CleanRoomError(reason)
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current)
                except FileExistsError:
                    pass
                except OSError:
                    raise CleanRoomError(reason) from None
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
            try:
                next_fd = os.open(part, flags, dir_fd=current)
            except OSError:
                raise CleanRoomError(reason) from None
            os.close(current)
            current = next_fd
        return current
    except Exception:
        os.close(current)
        raise


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise CleanRoomError("copy_write_failed")
        view = view[written:]


def _source_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _copy_one(
    relative: Path,
    *,
    source_root_fd: int,
    destination_root_fd: int,
    repo_root: Path,
    target: Path,
    after_copy_hook: Callable[[Path, Path], None] | None,
    destination_relative: Path | None = None,
    max_bytes: int | None = None,
) -> str:
    destination_path = destination_relative or relative
    source_parent = _open_parent(
        source_root_fd,
        relative.parts[:-1],
        create=False,
        reason="unsafe_source_path",
    )
    try:
        destination_parent = _open_parent(
            destination_root_fd,
            destination_path.parts[:-1],
            create=True,
            reason="unsafe_destination_path",
        )
    except Exception:
        os.close(source_parent)
        raise
    source_fd = -1
    destination_fd = -1
    destination_created = False
    try:
        try:
            source_fd = os.open(
                relative.name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=source_parent,
            )
        except OSError:
            raise CleanRoomError("unsafe_source_type") from None
        source_before = os.fstat(source_fd)
        if not stat.S_ISREG(source_before.st_mode):
            raise CleanRoomError("unsafe_source_type")
        if source_before.st_nlink != 1:
            raise CleanRoomError("source_hardlink")
        if max_bytes is not None and source_before.st_size > max_bytes:
            raise CleanRoomError("source_size_exceeded")

        try:
            destination_fd = os.open(
                destination_path.name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=destination_parent,
            )
            destination_created = True
        except FileExistsError:
            raise CleanRoomError("destination_already_exists") from None
        except OSError:
            raise CleanRoomError("unsafe_destination_type") from None

        source_digest = hashlib.sha256()
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            source_digest.update(chunk)
            _write_all(destination_fd, chunk)
        os.fchmod(destination_fd, stat.S_IMODE(source_before.st_mode))
        os.fsync(destination_fd)

        if after_copy_hook is not None:
            after_copy_hook(repo_root / relative, target / destination_path)

        source_after = os.fstat(source_fd)
        try:
            source_path_after = os.stat(
                relative.name,
                dir_fd=source_parent,
                follow_symlinks=False,
            )
        except OSError:
            raise CleanRoomError("source_identity_changed") from None
        if (
            _source_identity(source_before) != _source_identity(source_after)
            or source_path_after.st_dev != source_before.st_dev
            or source_path_after.st_ino != source_before.st_ino
        ):
            raise CleanRoomError("source_identity_changed")

        destination_after = os.fstat(destination_fd)
        try:
            destination_path_after = os.stat(
                destination_path.name,
                dir_fd=destination_parent,
                follow_symlinks=False,
            )
        except OSError:
            raise CleanRoomError("destination_identity_changed") from None
        if not stat.S_ISREG(destination_after.st_mode):
            raise CleanRoomError("unsafe_destination_type")
        if (
            destination_path_after.st_dev != destination_after.st_dev
            or destination_path_after.st_ino != destination_after.st_ino
        ):
            raise CleanRoomError("destination_identity_changed")
        if destination_after.st_nlink != 1 or destination_path_after.st_nlink != 1:
            raise CleanRoomError("destination_hardlink")

        os.lseek(source_fd, 0, os.SEEK_SET)
        os.lseek(destination_fd, 0, os.SEEK_SET)
        destination_digest = hashlib.sha256()
        while True:
            source_chunk = os.read(source_fd, 1024 * 1024)
            destination_chunk = os.read(destination_fd, 1024 * 1024)
            if source_chunk != destination_chunk:
                raise CleanRoomError("copy_bytes_mismatch")
            if not source_chunk:
                break
            destination_digest.update(destination_chunk)
        if source_digest.digest() != destination_digest.digest():
            raise CleanRoomError("copy_digest_mismatch")
        return source_digest.hexdigest()
    except BaseException:
        destination_identity: tuple[int, int] | None = None
        if destination_fd >= 0:
            try:
                metadata = os.fstat(destination_fd)
                destination_identity = (metadata.st_dev, metadata.st_ino)
            except OSError:
                pass
            try:
                os.close(destination_fd)
            except OSError:
                pass
            destination_fd = -1
        if destination_created and destination_identity is not None:
            try:
                path_metadata = os.stat(
                    destination_path.name,
                    dir_fd=destination_parent,
                    follow_symlinks=False,
                )
                if (
                    stat.S_ISREG(path_metadata.st_mode)
                    and (path_metadata.st_dev, path_metadata.st_ino)
                    == destination_identity
                ):
                    os.unlink(destination_path.name, dir_fd=destination_parent)
            except OSError:
                pass
        raise
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if destination_fd >= 0:
            os.close(destination_fd)
        os.close(source_parent)
        os.close(destination_parent)


def _copy_sources(
    files: Sequence[Path],
    *,
    repo_root: Path,
    target: Path,
    source_root_fd: int | None = None,
    after_copy_hook: Callable[[Path, Path], None] | None = None,
) -> str:
    inventory: list[tuple[str, str]] = []
    source_descriptor = (
        _open_directory(repo_root) if source_root_fd is None else os.dup(source_root_fd)
    )
    try:
        destination_root_fd = _open_directory(target)
    except BaseException:
        os.close(source_descriptor)
        raise
    try:
        _ensure_directory_identity(
            repo_root,
            source_descriptor,
            reason="source_root_identity_changed",
        )
        for relative in files:
            _validate_relative_path(relative)
            digest = _copy_one(
                relative,
                source_root_fd=source_descriptor,
                destination_root_fd=destination_root_fd,
                repo_root=repo_root,
                target=target,
                after_copy_hook=after_copy_hook,
            )
            inventory.append((relative.as_posix(), digest))
        _ensure_directory_identity(
            repo_root,
            source_descriptor,
            reason="source_root_identity_changed",
        )
    finally:
        os.close(source_descriptor)
        os.close(destination_root_fd)
    encoded = json.dumps(
        inventory,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SecureWorkspace:
    """A temporary tree bound to its parent entry and directory identity."""

    def __init__(self, path: Path, parent_fd: int, target_fd: int) -> None:
        self.path = path
        self._parent_fd = parent_fd
        self._target_fd = target_fd
        parent_metadata = os.fstat(parent_fd)
        self._parent_identity = (parent_metadata.st_dev, parent_metadata.st_ino)
        metadata = os.fstat(target_fd)
        self.identity = (metadata.st_dev, metadata.st_ino)
        self._closed = False

    @classmethod
    def create(
        cls,
        scratch_root: Path,
        *,
        prefix: str = "sklegal-cleanroom-",
    ) -> SecureWorkspace:
        raw = tempfile.mkdtemp(prefix=prefix, dir=scratch_root)
        path = Path(raw)
        os.chmod(path, 0o700)
        parent_fd = _open_directory(scratch_root)
        try:
            target_fd = os.open(
                path.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
        except Exception:
            os.close(parent_fd)
            shutil.rmtree(path)
            raise
        return cls(path, parent_fd, target_fd)

    def ensure_identity(self) -> None:
        if self._closed:
            raise CleanRoomError("temporary_identity_changed")
        try:
            parent_path_metadata = self.path.parent.stat(follow_symlinks=False)
            parent_descriptor_metadata = os.fstat(self._parent_fd)
        except OSError:
            raise CleanRoomError("temporary_identity_changed") from None
        if (
            parent_path_metadata.st_dev,
            parent_path_metadata.st_ino,
        ) != self._parent_identity or (
            parent_descriptor_metadata.st_dev,
            parent_descriptor_metadata.st_ino,
        ) != self._parent_identity:
            raise CleanRoomError("temporary_identity_changed")
        descriptor_metadata = os.fstat(self._target_fd)
        try:
            path_metadata = os.stat(
                self.path.name,
                dir_fd=self._parent_fd,
                follow_symlinks=False,
            )
        except OSError:
            raise CleanRoomError("temporary_identity_changed") from None
        if not stat.S_ISDIR(path_metadata.st_mode):
            raise CleanRoomError("temporary_identity_changed")
        if (
            descriptor_metadata.st_dev,
            descriptor_metadata.st_ino,
        ) != self.identity or (
            path_metadata.st_dev,
            path_metadata.st_ino,
        ) != self.identity:
            raise CleanRoomError("temporary_identity_changed")

    def cleanup(self) -> bool:
        if self._closed:
            return False
        identity_valid = True
        physically_removed = False
        try:
            try:
                self.ensure_identity()
            except CleanRoomError:
                identity_valid = False
            try:
                entry_metadata = os.stat(
                    self.path.name,
                    dir_fd=self._parent_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                entry_matches = False
            else:
                entry_matches = (
                    entry_metadata.st_dev,
                    entry_metadata.st_ino,
                ) == self.identity
            if entry_matches:
                shutil.rmtree(self.path.name, dir_fd=self._parent_fd)
            try:
                os.stat(
                    self.path.name,
                    dir_fd=self._parent_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                physically_removed = os.fstat(self._target_fd).st_nlink == 0
        except BaseException:
            physically_removed = False
        finally:
            os.close(self._target_fd)
            os.close(self._parent_fd)
            self._closed = True
        return identity_valid and physically_removed


def _create_workspace(scratch_root: Path) -> WorkspaceLease:
    return SecureWorkspace.create(scratch_root)


def _controller_environment() -> dict[str, str]:
    runtime = _runtime_anchor()
    if runtime is None:
        raise CleanRoomError("containment_unavailable")
    bus = runtime / "bus"
    try:
        metadata = bus.lstat()
    except OSError:
        raise CleanRoomError("containment_unavailable") from None
    if not stat.S_ISSOCK(metadata.st_mode):
        raise CleanRoomError("containment_unavailable")
    return {
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus}",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "XDG_RUNTIME_DIR": str(runtime),
    }


@dataclass(frozen=True)
class _BrokerLimits:
    max_workers: int = 4
    max_queued_connections: int = 8
    max_external_processes: int = 2
    max_requests: int = 1536
    max_request_bytes: int = 16 * 1024 * 1024
    max_response_bytes: int = 8 * 1024 * 1024
    max_frame_bytes: int = BROKER_REQUEST_LIMIT
    max_stdin_bytes: int = 4 * 1024 * 1024
    max_file_bytes: int = 4 * 1024 * 1024
    max_output_bytes: int = 4 * 1024 * 1024
    max_external_output_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        values = tuple(self.__dict__.values())
        if any(not isinstance(value, int) or value < 1 for value in values):
            raise CleanRoomError("broker_limits_invalid")
        if self.max_stdin_bytes > self.max_frame_bytes:
            raise CleanRoomError("broker_limits_invalid")


BROKER_LIMITS = _BrokerLimits()
BROKER_RESOURCE_KEYS = (
    "budget_denials",
    "connection_attempts",
    "connections_denied",
    "containers_created",
    "external_output_bytes",
    "external_output_denials",
    "keyrings_created",
    "peak_external_processes",
    "peak_workers",
    "private_bytes_created",
    "private_bytes_current",
    "private_bytes_peak",
    "private_bytes_removed",
    "private_files_created",
    "private_files_current",
    "private_files_peak",
    "private_files_removed",
    "request_bytes",
    "requests_accepted",
    "requests_denied",
    "response_bytes",
    "verify_snapshots_created",
    "wire_bytes_received",
    "wire_bytes_sent",
)


class _BrokerBudget:
    def __init__(self, limits: _BrokerLimits) -> None:
        self.limits = limits
        self._lock = threading.Lock()
        self._worker_slots = threading.BoundedSemaphore(limits.max_workers)
        self._external_slots = threading.BoundedSemaphore(limits.max_external_processes)
        self._counts = {key: 0 for key in BROKER_RESOURCE_KEYS}
        self._active_workers = 0
        self._active_external = 0
        self._reserved_request_bytes = 0
        self._reserved_requests = 0
        self._reserved_response_bytes = 0

    def _deny(self, *, request: bool = False) -> None:
        with self._lock:
            self._counts["budget_denials"] += 1
            if request:
                self._counts["requests_denied"] += 1

    def consume_request(self, size: int) -> bool:
        with self._lock:
            if (
                size < 1
                or size > self.limits.max_frame_bytes
                or self._counts["requests_accepted"] >= self.limits.max_requests
                or self._counts["request_bytes"] + size > self.limits.max_request_bytes
            ):
                self._counts["budget_denials"] += 1
                self._counts["requests_denied"] += 1
                return False
            self._counts["requests_accepted"] += 1
            self._counts["request_bytes"] += size
            return True

    def consume_response(self, size: int) -> bool:
        with self._lock:
            if (
                size < 1
                or size > self.limits.max_frame_bytes
                or self._counts["response_bytes"] + size
                > self.limits.max_response_bytes
            ):
                self._counts["budget_denials"] += 1
                return False
            self._counts["response_bytes"] += size
            return True

    def connection_attempted(self) -> None:
        with self._lock:
            self._counts["connection_attempts"] += 1

    def connection_denied(self) -> None:
        with self._lock:
            self._counts["budget_denials"] += 1
            self._counts["connections_denied"] += 1

    def reserve_request_bytes(self, size: int, *, request_slot: bool) -> bool:
        with self._lock:
            if (
                size < 1
                or self._counts["request_bytes"] + self._reserved_request_bytes + size
                > self.limits.max_request_bytes
                or (
                    request_slot
                    and self._counts["requests_accepted"] + self._reserved_requests
                    >= self.limits.max_requests
                )
            ):
                self._counts["budget_denials"] += 1
                return False
            self._reserved_request_bytes += size
            if request_slot:
                self._reserved_requests += 1
            return True

    def commit_request_bytes(
        self,
        reserved: int,
        received: int,
        *,
        request_complete: bool,
    ) -> None:
        if received < 0 or received > reserved:
            raise CleanRoomError("broker_resource_invalid")
        with self._lock:
            self._reserved_request_bytes -= reserved
            self._counts["request_bytes"] += received
            self._counts["wire_bytes_received"] += received
            if request_complete:
                self._reserved_requests -= 1
                self._counts["requests_accepted"] += 1

    def abandon_request_slot(self) -> None:
        with self._lock:
            if self._reserved_requests > 0:
                self._reserved_requests -= 1

    def reserve_response_bytes(self, size: int) -> bool:
        with self._lock:
            if (
                size < 1
                or size - 4 > self.limits.max_frame_bytes
                or self._counts["response_bytes"] + self._reserved_response_bytes + size
                > self.limits.max_response_bytes
            ):
                self._counts["budget_denials"] += 1
                return False
            self._reserved_response_bytes += size
            return True

    def commit_response_bytes(self, reserved: int, sent: int) -> None:
        if sent < 0 or sent > reserved:
            raise CleanRoomError("broker_resource_invalid")
        with self._lock:
            self._reserved_response_bytes -= reserved
            self._counts["response_bytes"] += sent
            self._counts["wire_bytes_sent"] += sent

    def private_file_created(self, size: int) -> None:
        if size < 0:
            raise CleanRoomError("broker_resource_invalid")
        with self._lock:
            self._counts["private_files_created"] += 1
            self._counts["private_files_current"] += 1
            self._counts["private_files_peak"] = max(
                self._counts["private_files_peak"],
                self._counts["private_files_current"],
            )
            self._counts["private_bytes_created"] += size
            self._counts["private_bytes_current"] += size
            self._counts["private_bytes_peak"] = max(
                self._counts["private_bytes_peak"],
                self._counts["private_bytes_current"],
            )

    def private_file_removed(self, size: int) -> None:
        if size < 0:
            raise CleanRoomError("broker_resource_invalid")
        with self._lock:
            if (
                self._counts["private_files_current"] < 1
                or self._counts["private_bytes_current"] < size
            ):
                raise CleanRoomError("broker_resource_invalid")
            self._counts["private_files_removed"] += 1
            self._counts["private_files_current"] -= 1
            self._counts["private_bytes_removed"] += size
            self._counts["private_bytes_current"] -= size

    def request_denied(self) -> None:
        with self._lock:
            self._counts["requests_denied"] += 1

    def enter_worker(self) -> bool:
        if not self._worker_slots.acquire(blocking=False):
            self._deny(request=True)
            return False
        with self._lock:
            self._active_workers += 1
            self._counts["peak_workers"] = max(
                self._counts["peak_workers"], self._active_workers
            )
        return True

    def leave_worker(self) -> None:
        with self._lock:
            self._active_workers -= 1
        self._worker_slots.release()

    def enter_external(self) -> bool:
        if not self._external_slots.acquire(blocking=False):
            self._deny()
            return False
        with self._lock:
            self._active_external += 1
            self._counts["peak_external_processes"] = max(
                self._counts["peak_external_processes"], self._active_external
            )
        return True

    def leave_external(self) -> None:
        with self._lock:
            self._active_external -= 1
        self._external_slots.release()

    def observe_external_output(self, size: int) -> bool:
        if size < 1:
            raise CleanRoomError("broker_resource_invalid")
        with self._lock:
            self._counts["external_output_bytes"] += size
            if (
                self._counts["external_output_bytes"]
                > self.limits.max_external_output_bytes
            ):
                self._counts["budget_denials"] += 1
                self._counts["external_output_denials"] += 1
                return False
            return True

    def external_output_remaining(self) -> int:
        with self._lock:
            return max(
                0,
                self.limits.max_external_output_bytes
                - self._counts["external_output_bytes"],
            )

    def deny_external_output(self) -> None:
        with self._lock:
            self._counts["budget_denials"] += 1
            self._counts["external_output_denials"] += 1

    def record(self, key: str) -> None:
        if key not in self._counts:
            raise CleanRoomError("broker_resource_invalid")
        with self._lock:
            self._counts[key] += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)


@dataclass(frozen=True)
class _BrokerCommand:
    operation: str
    container: str | None = None
    status_fd_index: int | None = None


@dataclass
class _BrokerPrivateEntry:
    path: Path
    relative: Path
    descriptor: int
    identity: tuple[int, int]
    is_directory: bool

    def ensure_identity(self) -> None:
        try:
            descriptor_metadata = os.fstat(self.descriptor)
            path_metadata = self.path.stat(follow_symlinks=False)
        except OSError:
            raise _broker_denied() from None
        expected = stat.S_ISDIR if self.is_directory else stat.S_ISREG
        if (
            not expected(descriptor_metadata.st_mode)
            or not expected(path_metadata.st_mode)
            or (
                descriptor_metadata.st_dev,
                descriptor_metadata.st_ino,
            )
            != self.identity
            or (path_metadata.st_dev, path_metadata.st_ino) != self.identity
        ):
            raise _broker_denied()

    def close(self) -> None:
        try:
            os.close(self.descriptor)
        except OSError:
            pass


def _broker_denied() -> CleanRoomError:
    return CleanRoomError("broker_request_denied", exit_code=126)


def _logical_workspace_relative(raw: Path, *, workspace: Path) -> Path:
    if not raw.is_absolute() or ".." in raw.parts:
        raise _broker_denied()
    try:
        relative = raw.relative_to(workspace)
    except ValueError:
        raise _broker_denied() from None
    if relative == Path(".") or any(part in {"", ".", ".."} for part in relative.parts):
        raise _broker_denied()
    return relative


def _validate_psql_arguments(arguments: Sequence[str]) -> None:
    single_value = {
        "--command",
        "--dbname",
        "--field-separator",
        "--set",
        "--username",
    }
    switches = {"--no-align", "--no-psqlrc", "--quiet", "--tuples-only"}
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in switches:
            index += 1
            continue
        if argument in single_value and index + 1 < len(arguments):
            value = arguments[index + 1]
            if not value or "\0" in value or len(value.encode("utf-8")) > 2_000_000:
                raise _broker_denied()
            index += 2
            continue
        if argument.startswith("--field-separator=") and len(argument) <= 32:
            index += 1
            continue
        raise _broker_denied()


def _validate_docker_command(
    arguments: Sequence[str],
    *,
    cwd: Path,
    workspace: Path,
    tracked_containers: set[str],
) -> _BrokerCommand:
    if cwd != workspace:
        _logical_workspace_relative(cwd, workspace=workspace)
    argv = list(arguments)
    compose = workspace / "deploy" / "chiap01" / "compose.dev.yml"
    compose_commands = (
        ["compose", "--file", str(compose), "config", "--format", "json"],
        [
            "compose",
            "--project-name",
            "sklegal-dev",
            "--file",
            str(compose),
            "config",
            "--quiet",
        ],
        [
            "compose",
            "--dry-run",
            "--project-name",
            "sklegal-dev",
            "--file",
            str(compose),
            "up",
            "--detach",
            "--wait",
        ],
        [
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
    )
    if argv in compose_commands:
        return _BrokerCommand("compose")
    if argv == ["ps", "--quiet", "--filter", "name=^skmem-pg$"]:
        return _BrokerCommand("skmemory-read")
    if len(argv) == 4 and argv[:3] == ["ps", "--all", "--quiet"]:
        raise _broker_denied()
    if (
        len(argv) == 5
        and argv[:4] == ["ps", "--all", "--quiet", "--filter"]
        and argv[4].startswith("name=^")
        and argv[4].endswith("$")
    ):
        container = argv[4][len("name=^") : -1]
        if container in tracked_containers:
            return _BrokerCommand("container-read", container)
        raise _broker_denied()
    if len(argv) >= 2 and argv[0] == "run":
        try:
            name = argv[argv.index("--name") + 1]
        except (ValueError, IndexError):
            raise _broker_denied() from None
        if re.fullmatch(r"sklegal-s102-[0-9]+-[0-9a-f]{8,32}", name) is None:
            raise _broker_denied()
        expected = [
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
            POSTGRES_IMAGE,
        ]
        if argv != expected or name in tracked_containers:
            raise _broker_denied()
        return _BrokerCommand("container-run", name)
    if argv[:1] == ["exec"]:
        index = 1
        if index < len(argv) and argv[index] == "-i":
            index += 1
        if index + 1 >= len(argv):
            raise _broker_denied()
        container = argv[index]
        program = argv[index + 1]
        program_arguments = argv[index + 2 :]
        if container not in tracked_containers:
            raise _broker_denied()
        if program == "pg_isready" and program_arguments in (
            ["--username", "postgres"],
            ["--username", "postgres", "--dbname", "sklegal"],
        ):
            return _BrokerCommand("container-exec", container)
        if program == "psql":
            _validate_psql_arguments(program_arguments)
            return _BrokerCommand("container-exec", container)
        raise _broker_denied()
    if len(argv) == 2 and argv[0] == "inspect" and argv[1] in tracked_containers:
        return _BrokerCommand("container-read", argv[1])
    if (
        len(argv) == 3
        and argv[:2] == ["rm", "--force"]
        and argv[2] in tracked_containers
    ):
        return _BrokerCommand("container-remove", argv[2])
    raise _broker_denied()


def _bounded_postgres_run(arguments: Sequence[str]) -> list[str]:
    argv = list(arguments)
    image_index = argv.index(POSTGRES_IMAGE)
    return [
        *argv[:image_index],
        "--cpus",
        "2.0",
        "--memory",
        "1024m",
        "--memory-swap",
        "1024m",
        "--pids-limit",
        "256",
        "--ulimit",
        "nofile=1024:1024",
        "--stop-timeout",
        "30",
        "--entrypoint",
        "/usr/bin/timeout",
        POSTGRES_IMAGE,
        "-s",
        "TERM",
        "-k",
        "30",
        "900",
        "/usr/local/bin/docker-entrypoint.sh",
        "postgres",
    ]


def _validate_gpg_command(
    arguments: Sequence[str],
    *,
    gnupg_home: Path,
    workspace: Path,
) -> _BrokerCommand:
    _logical_workspace_relative(gnupg_home, workspace=workspace)
    argv = list(arguments)
    if argv == [
        "--batch",
        "--passphrase",
        "",
        "--quick-generate-key",
        "SKLegal Synthetic Test <synthetic@example.invalid>",
        "ed25519",
        "sign",
        "1d",
    ]:
        return _BrokerCommand("gpg-generate")
    if argv == ["--batch", "--with-colons", "--list-secret-keys"]:
        return _BrokerCommand("gpg-list")
    sign_prefix = [
        "--batch",
        "--yes",
        "--armor",
        "--detach-sign",
        "--local-user",
    ]
    if len(argv) >= 6 and argv[:5] == sign_prefix:
        fingerprint = argv[5]
        suffix = argv[6:]
        if re.fullmatch(r"[0-9A-F]{40}|[0-9A-F]{64}", fingerprint) is None:
            raise _broker_denied()
        if suffix not in (
            [],
            ["--pinentry-mode", "loopback", "--passphrase", ""],
            ["--passphrase", "", "--pinentry-mode", "loopback"],
        ):
            raise _broker_denied()
        return _BrokerCommand("gpg-sign")
    if len(argv) == 4 and argv[:2] == ["--batch", "--verify"]:
        for raw in argv[2:]:
            _logical_workspace_relative(Path(raw), workspace=workspace)
        return _BrokerCommand("gpg-verify")
    if (
        len(argv) == 7
        and argv[:3] == ["--batch", "--quiet", "--status-fd"]
        and argv[3].isdigit()
        and argv[4] == "--verify"
    ):
        for raw in argv[5:]:
            _logical_workspace_relative(Path(raw), workspace=workspace)
        return _BrokerCommand("gpg-verify-status", status_fd_index=3)
    raise _broker_denied()


def _recv_exact(connection: socket.socket, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = connection.recv(length - len(result))
        if not chunk:
            raise _broker_denied()
        result.extend(chunk)
    return bytes(result)


def _recv_frame(
    connection: socket.socket,
    *,
    max_bytes: int = BROKER_REQUEST_LIMIT,
    budget: _BrokerBudget | None = None,
) -> bytes:
    if budget is None:
        size = struct.unpack("!I", _recv_exact(connection, 4))[0]
        if size == 0 or size > max_bytes:
            raise _broker_denied()
        return _recv_exact(connection, size)

    def receive_reserved(length: int, *, request_slot: bool) -> bytes:
        if not budget.reserve_request_bytes(length, request_slot=request_slot):
            raise CleanRoomError("broker_budget_exhausted", exit_code=126)
        value = bytearray()
        complete = False
        try:
            while len(value) < length:
                chunk = connection.recv(min(64 * 1024, length - len(value)))
                if not chunk:
                    raise _broker_denied()
                value.extend(chunk)
            complete = True
            return bytes(value)
        finally:
            budget.commit_request_bytes(
                length,
                len(value),
                request_complete=request_slot and complete,
            )
            if request_slot and not complete:
                budget.abandon_request_slot()

    try:
        size = struct.unpack("!I", receive_reserved(4, request_slot=False))[0]
        if size == 0 or size > max_bytes:
            raise _broker_denied()
        return receive_reserved(size, request_slot=True)
    except BaseException:
        budget.request_denied()
        raise


def _send_frame(
    connection: socket.socket,
    payload: bytes,
    *,
    budget: _BrokerBudget | None = None,
) -> bool:
    if len(payload) > BROKER_REQUEST_LIMIT:
        payload = json.dumps(
            {
                "returncode": 126,
                "stdout": "",
                "stderr": base64.b64encode(b"broker response too large\n").decode(),
                "status": "",
            },
            separators=(",", ":"),
        ).encode()
    framed = struct.pack("!I", len(payload)) + payload
    if budget is None:
        connection.sendall(framed)
        return True
    if not budget.reserve_response_bytes(len(framed)):
        return False
    sent = 0
    try:
        while sent < len(framed):
            written = connection.send(framed[sent:])
            if written <= 0:
                raise _broker_denied()
            sent += written
        return True
    finally:
        budget.commit_response_bytes(len(framed), sent)


def _completed_response(
    process: subprocess.CompletedProcess[bytes],
    *,
    status: bytes = b"",
    max_output_bytes: int = BROKER_LIMITS.max_output_bytes,
) -> bytes:
    if len(process.stdout) + len(process.stderr) + len(status) > max_output_bytes:
        raise _broker_denied()
    return json.dumps(
        {
            "returncode": process.returncode,
            "stdout": base64.b64encode(process.stdout).decode("ascii"),
            "stderr": base64.b64encode(process.stderr).decode("ascii"),
            "status": base64.b64encode(status).decode("ascii"),
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _denied_response(message: bytes = b"request denied\n") -> bytes:
    return _completed_response(
        subprocess.CompletedProcess([], 126, stdout=b"", stderr=message),
        max_output_bytes=max(BROKER_LIMITS.max_output_bytes, len(message)),
    )


def _budget_denied_response() -> bytes:
    return _denied_response(b"broker budget exhausted\n")


class _LoopbackBroker:
    def __init__(
        self,
        handler: Callable[[bytes], bytes],
        *,
        budget: _BrokerBudget | None = None,
        max_workers: int | None = None,
        lifecycle_probe: Callable[[str], None] | None = None,
    ) -> None:
        self._handler = handler
        if budget is not None and max_workers is not None:
            raise CleanRoomError("broker_limits_invalid")
        self._budget = budget or _BrokerBudget(
            _BrokerLimits(max_workers=max_workers or BROKER_LIMITS.max_workers)
        )
        self._lifecycle_probe = lifecycle_probe
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(16)
        self._socket.settimeout(0.2)
        self.endpoint = f"127.0.0.1:{self._socket.getsockname()[1]}"
        self._stopped = threading.Event()
        self._close_lock = threading.Lock()
        self._close_result: bool | None = None
        self._connections: set[socket.socket] = set()
        self._connections_lock = threading.Lock()
        self._queue: queue.Queue[socket.socket | None] = queue.Queue(
            maxsize=self._budget.limits.max_queued_connections
        )
        self._workers: tuple[threading.Thread, ...] = ()
        self._thread: threading.Thread | None = None
        self._started = False

    def _probe(self, phase: str) -> None:
        if self._lifecycle_probe is not None:
            self._lifecycle_probe(phase)

    def start(self) -> None:
        with self._close_lock:
            if self._started:
                return
            if self._close_result is not None or self._stopped.is_set():
                raise CleanRoomError("broker_start_failed")
            workers = tuple(
                threading.Thread(target=self._work, daemon=True)
                for _ in range(self._budget.limits.max_workers)
            )
            self._workers = workers
            try:
                for index, worker in enumerate(workers):
                    worker.start()
                    self._probe(f"worker-{index}-started")
                self._thread = threading.Thread(target=self._serve, daemon=True)
                self._thread.start()
                self._probe("serve-started")
                self._started = True
            except BaseException:
                self._close_unlocked()
                raise

    def _deny_connection(self, connection: socket.socket) -> None:
        self._budget.connection_denied()
        with connection:
            connection.settimeout(1.0)
            try:
                _send_frame(
                    connection,
                    _budget_denied_response(),
                    budget=self._budget,
                )
            except BaseException:
                pass

    def _work(self) -> None:
        while True:
            connection = self._queue.get()
            try:
                if connection is None:
                    return
                if not self._budget.enter_worker():
                    self._deny_connection(connection)
                    continue
                try:
                    self._handle(connection)
                finally:
                    self._budget.leave_worker()
            finally:
                if connection is not None:
                    with self._connections_lock:
                        self._connections.discard(connection)
                self._queue.task_done()

    def _serve(self) -> None:
        while not self._stopped.is_set():
            try:
                connection, _ = self._socket.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            self._budget.connection_attempted()
            with self._connections_lock:
                self._connections.add(connection)
            try:
                self._queue.put_nowait(connection)
            except queue.Full:
                with self._connections_lock:
                    self._connections.discard(connection)
                self._deny_connection(connection)

    def _handle(self, connection: socket.socket) -> None:
        with connection:
            connection.settimeout(BROKER_IO_TIMEOUT)
            try:
                raw = _recv_frame(
                    connection,
                    max_bytes=self._budget.limits.max_frame_bytes,
                    budget=self._budget,
                )
            except BaseException:
                response = _denied_response()
            else:
                try:
                    response = self._handler(raw)
                except BaseException:
                    self._budget.request_denied()
                    response = _denied_response()
            try:
                if not _send_frame(connection, response, budget=self._budget):
                    self._budget.request_denied()
                    _send_frame(
                        connection,
                        _budget_denied_response(),
                        budget=self._budget,
                    )
            except BaseException:
                pass

    @property
    def peak_workers(self) -> int:
        return self._budget.snapshot()["peak_workers"]

    @property
    def thread_count(self) -> int:
        return len(self._workers) + (self._thread is not None)

    def close(self) -> bool:
        with self._close_lock:
            if self._close_result is True:
                return self._close_result
            return self._close_unlocked()

    def _close_unlocked(self) -> bool:
        self._close_result = False
        self._stopped.set()
        try:
            self._socket.close()
        except OSError:
            pass
        serve_thread = self._thread
        if serve_thread is not None and serve_thread.ident is not None:
            serve_thread.join(timeout=2.0)
        with self._connections_lock:
            connections = tuple(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        while True:
            try:
                queued_connection = self._queue.get_nowait()
            except queue.Empty:
                break
            if queued_connection is not None:
                try:
                    queued_connection.close()
                except OSError:
                    pass
                with self._connections_lock:
                    self._connections.discard(queued_connection)
            self._queue.task_done()
        started_workers = tuple(
            worker for worker in self._workers if worker.ident is not None
        )
        sentinels_queued = True
        for _ in started_workers:
            try:
                self._queue.put(None, timeout=5.0)
            except queue.Full:
                sentinels_queued = False
                break
        for worker in started_workers:
            worker.join(timeout=5.0)
        cleaned = (
            sentinels_queued
            and (serve_thread is None or not serve_thread.is_alive())
            and not any(worker.is_alive() for worker in started_workers)
        )
        if cleaned:
            self._workers = ()
            self._thread = None
            self._started = False
        self._close_result = cleaned
        return self._close_result


_BROKER_SHIM = r"""#!/usr/bin/python3
import base64
import json
import os
import socket
import struct
import sys

tool = os.path.basename(sys.argv[0])
endpoint_name = "SKLEGAL_DOCKER_BROKER" if tool == "docker" else "SKLEGAL_GPG_BROKER"
try:
    host, raw_port = os.environ[endpoint_name].rsplit(":", 1)
    token = os.environ["SKLEGAL_BROKER_TOKEN"]
    arguments = sys.argv[1:]
    reads_stdin = (
        (tool == "docker" and arguments[:1] == ["exec"] and "-i" in arguments[:3])
        or (tool == "gpg" and "--detach-sign" in arguments)
    )
    stdin = sys.stdin.buffer.read(4 * 1024 * 1024 + 1) if reads_stdin else b""
    if len(stdin) > 4 * 1024 * 1024:
        raise RuntimeError
    request = json.dumps(
        {
            "token": token,
            "tool": tool,
            "argv": arguments,
            "cwd": os.getcwd(),
            "gnupg_home": os.environ.get("GNUPGHOME", ""),
            "stdin": base64.b64encode(stdin).decode("ascii"),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connection.settimeout(120.0)
    connection.connect((host, int(raw_port)))
    connection.sendall(struct.pack("!I", len(request)) + request)

    def receive(length):
        value = bytearray()
        while len(value) < length:
            chunk = connection.recv(length - len(value))
            if not chunk:
                raise RuntimeError
            value.extend(chunk)
        return bytes(value)

    size = struct.unpack("!I", receive(4))[0]
    if size < 1 or size > 8 * 1024 * 1024:
        raise RuntimeError
    response = json.loads(receive(size))
    connection.close()
    stdout = base64.b64decode(response["stdout"], validate=True)
    stderr = base64.b64decode(response["stderr"], validate=True)
    status = base64.b64decode(response["status"], validate=True)
    if status and "--status-fd" in arguments:
        os.write(int(arguments[arguments.index("--status-fd") + 1]), status)
    sys.stdout.buffer.write(stdout)
    sys.stderr.buffer.write(stderr)
    raise SystemExit(int(response["returncode"]))
except SystemExit:
    raise
except BaseException:
    sys.stderr.write("broker unavailable\n")
    raise SystemExit(126)
"""


class ExternalBrokerSet:
    """Parent-owned closed adapters for Docker and isolated synthetic GPG."""

    def __init__(
        self,
        workspace: Path,
        *,
        limits: _BrokerLimits = BROKER_LIMITS,
        lifecycle_probe: Callable[[str], None] | None = None,
    ) -> None:
        self._workspace = workspace.resolve(strict=True)
        self._limits = limits
        self._lifecycle_probe = lifecycle_probe
        self._budget = _BrokerBudget(limits)
        self._workspace_fd = -1
        self._private_workspace: SecureWorkspace | None = None
        self._private_root_fd = -1
        self._private_entries: list[_BrokerPrivateEntry] = []
        self._compose_snapshot: _BrokerPrivateEntry | None = None
        self._gpg_keyrings: dict[tuple[str, ...], _BrokerPrivateEntry] = {}
        self._docker: _LoopbackBroker | None = None
        self._gpg: _LoopbackBroker | None = None
        self._token = secrets.token_urlsafe(32)
        self._containers: set[str] = set()
        self._container_created: str | None = None
        self._processes: set[subprocess.Popen[bytes]] = set()
        self._closing = threading.Event()
        self._started = False
        self._lock = threading.RLock()
        self.environment: dict[str, str] = {}
        try:
            self._workspace_fd = _open_directory(self._workspace)
            _ensure_directory_identity(
                self._workspace,
                self._workspace_fd,
                reason="broker_start_failed",
            )
            self._private_workspace = SecureWorkspace.create(
                _tmp_anchor(),
                prefix="sklegal-broker-",
            )
            self._private_root_fd = _open_directory(self._private_workspace.path)
            self._compose_snapshot = self._stage_workspace_file(
                COMPOSE_RELATIVE,
                destination=Path("compose.dev.yml"),
            )
            self._docker = _LoopbackBroker(
                lambda raw: self._handle_request("docker", raw, wire_accounted=True),
                budget=self._budget,
                lifecycle_probe=lambda phase: self._probe("docker", phase),
            )
            self._gpg = _LoopbackBroker(
                lambda raw: self._handle_request("gpg", raw, wire_accounted=True),
                budget=self._budget,
                lifecycle_probe=lambda phase: self._probe("gpg", phase),
            )
            self.environment = {
                "SKLEGAL_BROKER_TOKEN": self._token,
                "SKLEGAL_DOCKER_BROKER": self._docker.endpoint,
                "SKLEGAL_GPG_BROKER": self._gpg.endpoint,
            }
            self._install_shims()
        except BaseException:
            self.close()
            raise CleanRoomError("broker_start_failed") from None

    @property
    def resource_counts(self) -> dict[str, int]:
        return self._budget.snapshot()

    @property
    def thread_count(self) -> int:
        return sum(
            broker.thread_count
            for broker in (self._docker, self._gpg)
            if broker is not None
        )

    def _probe(self, broker: str, phase: str) -> None:
        if self._lifecycle_probe is not None:
            self._lifecycle_probe(f"{broker}-{phase}")

    def start(self) -> None:
        if self._closing.is_set() or self._docker is None or self._gpg is None:
            raise CleanRoomError("broker_start_failed")
        if self._started:
            return
        blocked = {signal.SIGINT, signal.SIGTERM}
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
        try:
            self._docker.start()
            self._gpg.start()
            self._started = True
        except BaseException as exc:
            self.close()
            if isinstance(exc, (CleanRoomCancelled, KeyboardInterrupt)):
                raise
            raise CleanRoomError("broker_start_failed") from None
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

    def _ensure_private_state(self) -> Path:
        if self._private_workspace is None or self._private_root_fd < 0:
            raise _broker_denied()
        try:
            self._private_workspace.ensure_identity()
            root_metadata = os.fstat(self._private_root_fd)
        except BaseException:
            raise _broker_denied() from None
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or (root_metadata.st_dev, root_metadata.st_ino)
            != self._private_workspace.identity
        ):
            raise _broker_denied()
        return self._private_workspace.path

    def _stage_workspace_file(
        self,
        relative: Path,
        *,
        destination: Path,
    ) -> _BrokerPrivateEntry:
        private_root = self._ensure_private_state()
        if self._workspace_fd < 0:
            raise _broker_denied()
        _validate_relative_path(relative)
        _validate_relative_path(destination)
        copied = False
        descriptor = -1
        try:
            _copy_one(
                relative,
                source_root_fd=self._workspace_fd,
                destination_root_fd=self._private_root_fd,
                repo_root=self._workspace,
                target=private_root,
                after_copy_hook=None,
                destination_relative=destination,
                max_bytes=self._limits.max_file_bytes,
            )
            copied = True
            parent_fd = _open_parent(
                self._private_root_fd,
                destination.parts[:-1],
                create=False,
                reason="broker_request_denied",
            )
            try:
                descriptor = os.open(
                    destination.name,
                    os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
            finally:
                os.close(parent_fd)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size > self._limits.max_file_bytes
            ):
                raise _broker_denied()
            entry = _BrokerPrivateEntry(
                path=private_root / destination,
                relative=destination,
                descriptor=descriptor,
                identity=(metadata.st_dev, metadata.st_ino),
                is_directory=False,
            )
            self._private_entries.append(entry)
            self._budget.private_file_created(metadata.st_size)
            return entry
        except BaseException:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if copied:
                self._remove_untracked_private_file(destination)
            raise _broker_denied() from None

    def _remove_untracked_private_file(self, relative: Path) -> bool:
        descriptor = -1
        parent_fd = -1
        try:
            parent_fd = _open_parent(
                self._private_root_fd,
                relative.parts[:-1],
                create=False,
                reason="broker_request_denied",
            )
            descriptor = os.open(
                relative.name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            descriptor_metadata = os.fstat(descriptor)
            path_metadata = os.stat(
                relative.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(descriptor_metadata.st_mode)
                or not stat.S_ISREG(path_metadata.st_mode)
                or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
                != (path_metadata.st_dev, path_metadata.st_ino)
            ):
                return False
            os.unlink(relative.name, dir_fd=parent_fd)
            removed = os.fstat(descriptor).st_nlink == 0
        except FileNotFoundError:
            removed = True
        except OSError:
            try:
                path_metadata = os.stat(
                    relative.name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                if stat.S_ISREG(path_metadata.st_mode) and path_metadata.st_nlink == 1:
                    os.unlink(relative.name, dir_fd=parent_fd)
                    removed = True
                else:
                    removed = False
            except OSError:
                removed = False
        except BaseException:
            removed = False
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if parent_fd >= 0:
                os.close(parent_fd)
        if relative.parts[:-1]:
            try:
                os.rmdir(
                    str(Path(*relative.parts[:-1])),
                    dir_fd=self._private_root_fd,
                )
            except OSError:
                pass
        return removed

    def _discard_private_entry(self, entry: _BrokerPrivateEntry) -> bool:
        with self._lock:
            try:
                entry_size = os.fstat(entry.descriptor).st_size
            except OSError:
                entry_size = -1
            try:
                entry.ensure_identity()
                parent_fd = _open_parent(
                    self._private_root_fd,
                    entry.relative.parts[:-1],
                    create=False,
                    reason="broker_request_denied",
                )
                try:
                    os.unlink(entry.relative.name, dir_fd=parent_fd)
                finally:
                    os.close(parent_fd)
                removed = os.fstat(entry.descriptor).st_nlink == 0
            except BaseException:
                removed = False
            entry.close()
            try:
                self._private_entries.remove(entry)
            except ValueError:
                removed = False
            if removed and not entry.is_directory and entry_size >= 0:
                try:
                    self._budget.private_file_removed(entry_size)
                except CleanRoomError:
                    removed = False
            if entry.relative.parts[:-1]:
                try:
                    os.rmdir(
                        str(Path(*entry.relative.parts[:-1])),
                        dir_fd=self._private_root_fd,
                    )
                except OSError:
                    pass
            return removed

    def _logical_keyring(self, home: Path) -> _BrokerPrivateEntry:
        relative = _logical_workspace_relative(home, workspace=self._workspace)
        key = tuple(relative.parts)
        with self._lock:
            existing = self._gpg_keyrings.get(key)
            if existing is not None:
                existing.ensure_identity()
                return existing
            if self._gpg_keyrings:
                raise _broker_denied()
            if self._workspace_fd < 0:
                raise _broker_denied()
            logical_fd = _open_parent(
                self._workspace_fd,
                relative.parts,
                create=False,
                reason="broker_request_denied",
            )
            try:
                logical_metadata = os.fstat(logical_fd)
                if (
                    not stat.S_ISDIR(logical_metadata.st_mode)
                    or logical_metadata.st_uid != os.geteuid()
                    or logical_metadata.st_mode & 0o077
                ):
                    raise _broker_denied()
            finally:
                os.close(logical_fd)

            private_root = self._ensure_private_state()
            name = f"gpg-{secrets.token_hex(16)}"
            descriptor = -1
            created = False
            try:
                os.mkdir(name, mode=0o700, dir_fd=self._private_root_fd)
                created = True
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=self._private_root_fd,
                )
                metadata = os.fstat(descriptor)
                if not stat.S_ISDIR(metadata.st_mode) or metadata.st_nlink < 1:
                    raise _broker_denied()
            except BaseException:
                if descriptor >= 0:
                    os.close(descriptor)
                if created:
                    try:
                        os.rmdir(name, dir_fd=self._private_root_fd)
                    except OSError:
                        pass
                raise _broker_denied() from None
            entry = _BrokerPrivateEntry(
                path=private_root / name,
                relative=Path(name),
                descriptor=descriptor,
                identity=(metadata.st_dev, metadata.st_ino),
                is_directory=True,
            )
            self._private_entries.append(entry)
            self._gpg_keyrings[key] = entry
            self._budget.record("keyrings_created")
            return entry

    def _snapshot_gpg_input(self, raw: str, *, label: str) -> _BrokerPrivateEntry:
        relative = _logical_workspace_relative(Path(raw), workspace=self._workspace)
        destination = Path("gpg-inputs") / (f"{secrets.token_hex(16)}-{label}")
        snapshot = self._stage_workspace_file(relative, destination=destination)
        self._budget.record("verify_snapshots_created")
        return snapshot

    def _install_shims(self) -> None:
        directory = self._workspace / ".clean-bin"
        directory.mkdir(mode=0o700)
        for name in ("docker", "gpg"):
            path = directory / name
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o700,
            )
            try:
                encoded = _BROKER_SHIM.encode("utf-8")
                written = 0
                while written < len(encoded):
                    written += os.write(descriptor, encoded[written:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _decode_request(
        self, expected_tool: str, raw: bytes
    ) -> tuple[list[str], Path, Path | None, bytes]:
        try:
            payload = json.loads(raw)
            if set(payload) != {
                "argv",
                "cwd",
                "gnupg_home",
                "stdin",
                "token",
                "tool",
            }:
                raise ValueError
            if payload["tool"] != expected_tool or not hmac.compare_digest(
                str(payload["token"]), self._token
            ):
                raise ValueError
            arguments = payload["argv"]
            if (
                not isinstance(arguments, list)
                or len(arguments) > 64
                or any(
                    not isinstance(item, str)
                    or "\0" in item
                    or len(item.encode("utf-8")) > self._limits.max_frame_bytes
                    for item in arguments
                )
            ):
                raise ValueError
            cwd = Path(payload["cwd"])
            home = Path(payload["gnupg_home"]) if payload["gnupg_home"] else None
            stdin = base64.b64decode(payload["stdin"], validate=True)
            if len(stdin) > self._limits.max_stdin_bytes:
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise _broker_denied() from None
        return arguments, cwd, home, stdin

    @staticmethod
    def _process_environment(home: Path | None = None) -> dict[str, str]:
        environment = {
            "HOME": "/nonexistent",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": SYSTEM_PATH,
        }
        if home is not None:
            environment["GNUPGHOME"] = str(home)
            environment["HOME"] = str(home.parent / ".clean-home")
        return environment

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes]) -> bool:
        try:
            if process.poll() is not None:
                return True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2.0)
            return process.poll() is not None
        except BaseException:
            return False

    def _run_external(
        self,
        argv: list[str],
        *,
        cwd: Path,
        environment: dict[str, str],
        stdin: bytes | None = None,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        if stdin is not None and len(stdin) > self._limits.max_stdin_bytes:
            raise _broker_denied()
        if not self._budget.enter_external():
            raise CleanRoomError("broker_budget_exhausted", exit_code=126)
        process: subprocess.Popen[bytes] | None = None
        with self._lock:
            if self._closing.is_set():
                self._budget.leave_external()
                raise _broker_denied()
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env=environment,
                    stdin=(
                        subprocess.PIPE if stdin is not None else subprocess.DEVNULL
                    ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                self._processes.add(process)
            except BaseException:
                self._budget.leave_external()
                raise
        try:
            assert process is not None
            try:
                return self._capture_external(
                    process,
                    argv=argv,
                    stdin=stdin,
                    timeout=timeout,
                )
            except BaseException:
                self._stop_process(process)
                raise
        finally:
            with self._lock:
                if process is not None and process.poll() is not None:
                    self._processes.discard(process)
            self._budget.leave_external()

    def _capture_external(
        self,
        process: subprocess.Popen[bytes],
        *,
        argv: list[str],
        stdin: bytes | None,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        stdout = bytearray()
        stderr = bytearray()
        selector = selectors.DefaultSelector()
        streams: list[object] = []
        input_view = memoryview(stdin or b"")
        input_offset = 0
        started = time.monotonic()
        denied = False
        timed_out = False

        def close_stream(stream: object) -> None:
            try:
                descriptor = stream.fileno()  # type: ignore[attr-defined]
            except (AttributeError, OSError, ValueError):
                return
            try:
                selector.unregister(descriptor)
            except (KeyError, ValueError):
                pass
            try:
                stream.close()  # type: ignore[attr-defined]
            except OSError:
                pass

        try:
            assert process.stdout is not None
            assert process.stderr is not None
            for name, stream, target in (
                ("stdout", process.stdout, stdout),
                ("stderr", process.stderr, stderr),
            ):
                descriptor = stream.fileno()
                os.set_blocking(descriptor, False)
                selector.register(
                    descriptor,
                    selectors.EVENT_READ,
                    (name, stream, target),
                )
                streams.append(stream)
            if process.stdin is not None:
                streams.append(process.stdin)
                if input_view:
                    descriptor = process.stdin.fileno()
                    os.set_blocking(descriptor, False)
                    selector.register(
                        descriptor,
                        selectors.EVENT_WRITE,
                        ("stdin", process.stdin, None),
                    )
                else:
                    close_stream(process.stdin)

            while selector.get_map() or process.poll() is None:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    timed_out = True
                    break
                if not selector.get_map():
                    try:
                        process.wait(timeout=min(0.05, remaining))
                    except subprocess.TimeoutExpired:
                        continue
                    break
                events = selector.select(timeout=min(0.1, remaining))
                for key, mask in events:
                    name, stream, target = key.data
                    if name == "stdin" and mask & selectors.EVENT_WRITE:
                        try:
                            written = os.write(
                                key.fd,
                                input_view[input_offset : input_offset + 64 * 1024],
                            )
                        except BrokenPipeError:
                            close_stream(stream)
                            continue
                        input_offset += written
                        if input_offset >= len(input_view):
                            close_stream(stream)
                        continue
                    if not mask & selectors.EVENT_READ:
                        continue
                    command_remaining = max(
                        0,
                        self._limits.max_output_bytes - len(stdout) - len(stderr),
                    )
                    cumulative_remaining = self._budget.external_output_remaining()
                    read_size = max(
                        1,
                        min(64 * 1024, command_remaining + 1, cumulative_remaining + 1),
                    )
                    try:
                        chunk = os.read(key.fd, read_size)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        close_stream(stream)
                        continue
                    cumulative_allowed = self._budget.observe_external_output(
                        len(chunk)
                    )
                    command_allowed = (
                        len(stdout) + len(stderr) + len(chunk)
                        <= self._limits.max_output_bytes
                    )
                    if not cumulative_allowed or not command_allowed:
                        if cumulative_allowed:
                            self._budget.deny_external_output()
                        denied = True
                        break
                    assert target is not None
                    target.extend(chunk)
                if denied:
                    break

            if denied:
                if not self._stop_process(process):
                    self._closing.set()
                    raise CleanRoomError("broker_process_cleanup_failed")
                stdout.clear()
                stderr.clear()
                return subprocess.CompletedProcess(
                    argv,
                    126,
                    b"",
                    b"broker output limit exceeded\n",
                )
            if timed_out:
                if not self._stop_process(process):
                    self._closing.set()
                    raise CleanRoomError("broker_process_cleanup_failed")
                return subprocess.CompletedProcess(
                    argv, 124, bytes(stdout), bytes(stderr)
                )
            if process.poll() is None:
                process.wait(timeout=max(0.1, timeout - (time.monotonic() - started)))
            return subprocess.CompletedProcess(
                argv,
                process.returncode if process.returncode is not None else 1,
                bytes(stdout),
                bytes(stderr),
            )
        finally:
            input_view.release()
            for cleanup_stream in streams:
                close_stream(cleanup_stream)
            selector.close()

    def _terminate_active_processes(self) -> bool:
        with self._lock:
            processes = tuple(self._processes)
        success = True
        for process in processes:
            if not self._stop_process(process):
                success = False
        with self._lock:
            return success and not any(
                process.poll() is None for process in self._processes
            )

    def _docker_request(
        self, arguments: list[str], *, cwd: Path, stdin: bytes
    ) -> bytes:
        private_root = self._ensure_private_state()
        actual = list(arguments)
        with self._lock:
            command = _validate_docker_command(
                arguments,
                cwd=cwd,
                workspace=self._workspace,
                tracked_containers=set(self._containers),
            )
            if command.operation == "compose":
                if self._compose_snapshot is None:
                    raise _broker_denied()
                self._compose_snapshot.ensure_identity()
                actual[actual.index(str(self._workspace / COMPOSE_RELATIVE))] = str(
                    self._compose_snapshot.path
                )
            if command.operation == "container-run":
                assert command.container is not None
                if self._container_created is not None:
                    raise _broker_denied()
                collision = self._run_external(
                    [
                        str(DOCKER),
                        "ps",
                        "--all",
                        "--quiet",
                        "--filter",
                        f"name=^{command.container}$",
                    ],
                    cwd=private_root,
                    environment=self._process_environment(),
                    timeout=10.0,
                )
                if collision.returncode != 0 or collision.stdout.strip():
                    raise _broker_denied()
                self._container_created = command.container
                self._containers.add(command.container)
                self._budget.record("containers_created")
                actual = _bounded_postgres_run(actual)
        process = self._run_external(
            [str(DOCKER), *actual],
            cwd=private_root,
            environment=self._process_environment(),
            stdin=(
                stdin if arguments[:1] == ["exec"] and "-i" in arguments[:3] else None
            ),
            timeout=BROKER_IO_TIMEOUT,
        )
        return _completed_response(
            process,
            max_output_bytes=self._limits.max_output_bytes,
        )

    def _gpg_request(
        self,
        arguments: list[str],
        *,
        cwd: Path,
        home: Path | None,
        stdin: bytes,
    ) -> bytes:
        if home is None:
            raise _broker_denied()
        if cwd != self._workspace:
            _logical_workspace_relative(cwd, workspace=self._workspace)
        command = _validate_gpg_command(
            arguments,
            gnupg_home=home,
            workspace=self._workspace,
        )
        private_root = self._ensure_private_state()
        keyring = self._logical_keyring(home)
        keyring.ensure_identity()
        actual = list(arguments)
        status = b""
        snapshots: list[_BrokerPrivateEntry] = []
        try:
            if command.operation in {"gpg-verify", "gpg-verify-status"}:
                input_indexes = (2, 3) if command.operation == "gpg-verify" else (5, 6)
                for index, label in zip(
                    input_indexes, ("signature", "payload"), strict=True
                ):
                    snapshot = self._snapshot_gpg_input(actual[index], label=label)
                    snapshots.append(snapshot)
                    snapshot.ensure_identity()
                    actual[index] = str(snapshot.path)
            if command.status_fd_index is not None:
                actual[command.status_fd_index] = "1"
            process = self._run_external(
                [str(GPG), *actual],
                cwd=private_root,
                environment=self._process_environment(keyring.path),
                stdin=stdin if command.operation == "gpg-sign" else None,
                timeout=30.0,
            )
        finally:
            snapshots_cleaned = [
                self._discard_private_entry(snapshot) for snapshot in snapshots
            ]
            if not all(snapshots_cleaned):
                raise _broker_denied()
        if command.status_fd_index is not None:
            status = process.stdout
            process = subprocess.CompletedProcess(
                process.args,
                process.returncode,
                stdout=b"",
                stderr=process.stderr,
            )
        return _completed_response(
            process,
            status=status,
            max_output_bytes=self._limits.max_output_bytes,
        )

    def _handle_request(
        self,
        expected_tool: str,
        raw: bytes,
        *,
        wire_accounted: bool = False,
    ) -> bytes:
        if not wire_accounted and not self._budget.consume_request(len(raw)):
            return _budget_denied_response()
        try:
            arguments, cwd, home, stdin = self._decode_request(expected_tool, raw)
            if expected_tool == "docker":
                response = self._docker_request(arguments, cwd=cwd, stdin=stdin)
            else:
                response = self._gpg_request(
                    arguments,
                    cwd=cwd,
                    home=home,
                    stdin=stdin,
                )
            if not wire_accounted and not self._budget.consume_response(len(response)):
                return _budget_denied_response()
            return response
        except CleanRoomError as exc:
            self._budget.request_denied()
            if exc.reason == "broker_budget_exhausted":
                return _budget_denied_response()
            return _denied_response()
        except BaseException:
            self._budget.request_denied()
            return _denied_response()

    def _cleanup_containers(self) -> bool:
        success = True
        with self._lock:
            containers = tuple(sorted(self._containers))
        for container in containers:
            try:
                subprocess.run(
                    [str(DOCKER), "rm", "--force", container],
                    check=False,
                    capture_output=True,
                    env=self._process_environment(),
                    timeout=15.0,
                )
                remaining = subprocess.run(
                    [
                        str(DOCKER),
                        "ps",
                        "--all",
                        "--quiet",
                        "--filter",
                        f"name=^{container}$",
                    ],
                    check=False,
                    capture_output=True,
                    env=self._process_environment(),
                    timeout=10.0,
                )
                if remaining.returncode != 0 or remaining.stdout.strip():
                    success = False
            except BaseException:
                success = False
        return success

    def _cleanup_gpg(self) -> bool:
        success = True
        with self._lock:
            keyrings = tuple(self._gpg_keyrings.values())
        for keyring in keyrings:
            try:
                self._ensure_private_state()
                keyring.ensure_identity()
                socket_result = subprocess.run(
                    [
                        str(GPGCONF),
                        "--homedir",
                        str(keyring.path),
                        "--list-dirs",
                        "agent-socket",
                    ],
                    check=False,
                    capture_output=True,
                    env=self._process_environment(keyring.path),
                    timeout=5.0,
                )
                agent_socket = Path(socket_result.stdout.decode().strip())
                killed = subprocess.run(
                    [
                        str(GPGCONF),
                        "--homedir",
                        str(keyring.path),
                        "--kill",
                        "all",
                    ],
                    check=False,
                    capture_output=True,
                    env=self._process_environment(keyring.path),
                    timeout=5.0,
                )
                deadline = time.monotonic() + 2.0
                while agent_socket.is_socket() and time.monotonic() < deadline:
                    time.sleep(0.05)
                if killed.returncode != 0 or agent_socket.is_socket():
                    success = False
            except BaseException:
                success = False
        return success

    def close(self) -> bool:
        self._closing.set()
        if self._docker is not None:
            self._docker.close()
        if self._gpg is not None:
            self._gpg.close()
        processes_stopped = self._terminate_active_processes()
        docker_closed = self._docker is None or self._docker.close()
        gpg_closed = self._gpg is None or self._gpg.close()
        containers_cleaned = self._cleanup_containers()
        gpg_cleaned = self._cleanup_gpg()
        files_cleaned = True
        for entry in tuple(self._private_entries):
            if not entry.is_directory:
                files_cleaned = self._discard_private_entry(entry) and files_cleaned
        for entry in self._private_entries:
            entry.close()
        self._private_entries.clear()
        self._gpg_keyrings.clear()
        if self._private_root_fd >= 0:
            os.close(self._private_root_fd)
            self._private_root_fd = -1
        if self._workspace_fd >= 0:
            os.close(self._workspace_fd)
            self._workspace_fd = -1
        private_cleaned = True
        if self._private_workspace is not None:
            private_cleaned = self._private_workspace.cleanup()
            self._private_workspace = None
        return (
            docker_closed
            and gpg_closed
            and processes_stopped
            and containers_cleaned
            and gpg_cleaned
            and files_cleaned
            and private_cleaned
        )


def _create_brokers(workspace: Path) -> BrokerRuntime:
    return ExternalBrokerSet(workspace)


def _child_environment(
    *,
    target: Path,
    uv_cache: Path,
    npm_cache: Path,
    broker_environment: Mapping[str, str],
) -> dict[str, str]:
    home = target / ".clean-home"
    temporary = target / ".clean-tmp"
    home.mkdir(mode=0o700)
    temporary.mkdir(mode=0o700)
    environment = {
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": f"{target / '.clean-bin'}:{SYSTEM_PATH}",
        "TMPDIR": str(temporary),
        "UV_CACHE_DIR": str(uv_cache),
        "UV_LINK_MODE": "copy",
        "npm_config_cache": str(npm_cache),
    }
    environment.update(broker_environment)
    return environment


LANDLOCK_CREATE_RULESET = 444
LANDLOCK_ADD_RULE = 445
LANDLOCK_RESTRICT_SELF = 446
LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38
LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12
LANDLOCK_ACCESS_FS_REFER = 1 << 13
LANDLOCK_ACCESS_FS_TRUNCATE = 1 << 14
LANDLOCK_ACCESS_FS_IOCTL_DEV = 1 << 15


class _LandlockRulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _LandlockPathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
        ("reserved", ctypes.c_uint32),
    ]


def _landlock_syscall(number: int, *arguments: object) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    result = int(libc.syscall(number, *arguments))
    if result < 0:
        raise CleanRoomError("landlock_unavailable")
    return result


def _landlock_abi() -> int:
    return _landlock_syscall(
        LANDLOCK_CREATE_RULESET,
        0,
        0,
        LANDLOCK_CREATE_RULESET_VERSION,
    )


def _landlock_add_path_rule(
    ruleset_fd: int,
    path: Path,
    allowed_access: int,
) -> None:
    try:
        path_fd = os.open(path, os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        raise CleanRoomError("landlock_path_invalid") from None
    try:
        attribute = _LandlockPathBeneathAttr(
            allowed_access=allowed_access,
            parent_fd=path_fd,
            reserved=0,
        )
        _landlock_syscall(
            LANDLOCK_ADD_RULE,
            ruleset_fd,
            LANDLOCK_RULE_PATH_BENEATH,
            ctypes.byref(attribute),
            0,
        )
    finally:
        os.close(path_fd)


def _landlock_directory(raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise CleanRoomError("landlock_path_invalid")
    try:
        metadata = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise CleanRoomError("landlock_path_invalid") from None
    if candidate != resolved or not stat.S_ISDIR(metadata.st_mode):
        raise CleanRoomError("landlock_path_invalid")
    return resolved


def _landlock_exec(arguments: Sequence[str]) -> int:
    if len(arguments) != 3:
        return 125
    try:
        target, uv_cache, npm_cache = (_landlock_directory(raw) for raw in arguments)
        abi = _landlock_abi()
        if abi < 5:
            raise CleanRoomError("landlock_unavailable")
        handled_access = (
            LANDLOCK_ACCESS_FS_EXECUTE
            | LANDLOCK_ACCESS_FS_WRITE_FILE
            | LANDLOCK_ACCESS_FS_READ_FILE
            | LANDLOCK_ACCESS_FS_READ_DIR
            | LANDLOCK_ACCESS_FS_REMOVE_DIR
            | LANDLOCK_ACCESS_FS_REMOVE_FILE
            | LANDLOCK_ACCESS_FS_MAKE_CHAR
            | LANDLOCK_ACCESS_FS_MAKE_DIR
            | LANDLOCK_ACCESS_FS_MAKE_REG
            | LANDLOCK_ACCESS_FS_MAKE_SOCK
            | LANDLOCK_ACCESS_FS_MAKE_FIFO
            | LANDLOCK_ACCESS_FS_MAKE_BLOCK
            | LANDLOCK_ACCESS_FS_MAKE_SYM
            | LANDLOCK_ACCESS_FS_REFER
            | LANDLOCK_ACCESS_FS_TRUNCATE
            | LANDLOCK_ACCESS_FS_IOCTL_DEV
        )
        ruleset_attribute = _LandlockRulesetAttr(handled_access_fs=handled_access)
        ruleset_fd = _landlock_syscall(
            LANDLOCK_CREATE_RULESET,
            ctypes.byref(ruleset_attribute),
            ctypes.sizeof(ruleset_attribute),
            0,
        )
        read_access = LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_READ_DIR
        write_access = (
            read_access
            | LANDLOCK_ACCESS_FS_WRITE_FILE
            | LANDLOCK_ACCESS_FS_REMOVE_DIR
            | LANDLOCK_ACCESS_FS_REMOVE_FILE
            | LANDLOCK_ACCESS_FS_MAKE_DIR
            | LANDLOCK_ACCESS_FS_MAKE_REG
            | LANDLOCK_ACCESS_FS_MAKE_SOCK
            | LANDLOCK_ACCESS_FS_MAKE_FIFO
            | LANDLOCK_ACCESS_FS_MAKE_SYM
            | LANDLOCK_ACCESS_FS_REFER
            | LANDLOCK_ACCESS_FS_TRUNCATE
        )
        workspace_access = write_access | LANDLOCK_ACCESS_FS_EXECUTE
        try:
            for readable in LANDLOCK_READ_PATHS:
                _landlock_add_path_rule(ruleset_fd, readable, read_access)
            _landlock_add_path_rule(ruleset_fd, target, workspace_access)
            for writable in (uv_cache, npm_cache):
                _landlock_add_path_rule(ruleset_fd, writable, write_access)
            for executable in LANDLOCK_EXECUTABLES:
                _landlock_add_path_rule(
                    ruleset_fd,
                    executable,
                    LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_EXECUTE,
                )
            _landlock_add_path_rule(
                ruleset_fd,
                Path("/dev/null"),
                LANDLOCK_ACCESS_FS_READ_FILE
                | LANDLOCK_ACCESS_FS_WRITE_FILE
                | LANDLOCK_ACCESS_FS_IOCTL_DEV,
            )
            for device in LANDLOCK_READ_DEVICES:
                _landlock_add_path_rule(
                    ruleset_fd,
                    device,
                    LANDLOCK_ACCESS_FS_READ_FILE,
                )
            libc = ctypes.CDLL(None, use_errno=True)
            if int(libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) != 0:
                raise CleanRoomError("landlock_unavailable")
            _landlock_syscall(LANDLOCK_RESTRICT_SELF, ruleset_fd, 0)
        finally:
            os.close(ruleset_fd)
    except BaseException:
        return 125
    os.execve(str(MAKE), [str(MAKE), "check"], dict(os.environ))
    return 125


class _ProcessSlot:
    def __init__(self) -> None:
        self.pid = 0
        self._process: WaitableProcess | None = None
        self._lock = threading.Lock()

    def bind(self, process: WaitableProcess) -> None:
        with self._lock:
            if self._process is not None or process.pid <= 0:
                raise CleanRoomError("containment_process_invalid")
            self._process = process
            self.pid = process.pid

    def poll(self) -> int | None:
        with self._lock:
            process = self._process
        return 0 if process is None else process.poll()

    def wait(self, timeout: float) -> int:
        with self._lock:
            process = self._process
        return 0 if process is None else process.wait(timeout=timeout)


@dataclass(frozen=True)
class ContainedUnit:
    unit_name: str
    process: WaitableProcess
    control_group: str | None
    candidate_control_group: str | None = None
    binding_confirmed: bool = True


class ContainmentBackend(Protocol):
    def preflight(self) -> None: ...

    def start(
        self,
        *,
        target: Path,
        child_environment: dict[str, str],
        unit_name: str,
        runtime_seconds: float,
        register: Callable[[ContainedUnit], None],
    ) -> ContainedUnit: ...

    def signal(self, contained: ContainedUnit, sent: signal.Signals) -> None: ...

    def ensure_extinct(self, contained: ContainedUnit) -> bool: ...


class SystemdContainment:
    """Own the complete nested gate process tree in one user service cgroup."""

    def __init__(
        self,
        *,
        process_factory: ProcessFactory = _start_process,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        lifecycle_probe: Callable[[str], None] | None = None,
    ) -> None:
        self._process_factory = process_factory
        self._clock = clock
        self._sleeper = sleeper
        self._lifecycle_probe = lifecycle_probe or (lambda phase: None)
        self._environment = _controller_environment()
        self._unset_environment = set(DEFAULT_UNSET_ENVIRONMENT)

    def _run_control(
        self, argv: list[str], *, timeout: float = 5.0
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                argv,
                cwd=Path("/"),
                env=self._environment,
                check=False,
                capture_output=True,
                timeout=timeout,
            )
        except Exception:
            raise CleanRoomError("containment_control_failure") from None

    def preflight(self) -> None:
        result = self._run_control([str(SYSTEMCTL), "--user", "show-environment"])
        if result.returncode != 0 or not (CGROUP_ROOT / "cgroup.controllers").is_file():
            raise CleanRoomError("containment_unavailable")
        if _landlock_abi() < 5:
            raise CleanRoomError("containment_unavailable")
        try:
            decoded = result.stdout.decode("utf-8")
        except UnicodeDecodeError:
            raise CleanRoomError("containment_environment_invalid") from None
        for line in decoded.splitlines():
            name, separator, _ = line.partition("=")
            if (
                not separator
                or not name.isascii()
                or not name.replace("_", "a").isalnum()
                or name[0].isdigit()
            ):
                raise CleanRoomError("containment_environment_invalid")
            self._unset_environment.add(name)

    def build_argv(
        self,
        *,
        target: Path,
        child_environment: dict[str, str],
        unit_name: str,
        runtime_seconds: float,
    ) -> list[str]:
        runtime = max(1, math.ceil(runtime_seconds))
        unset_environment = getattr(
            self,
            "_unset_environment",
            set(DEFAULT_UNSET_ENVIRONMENT),
        )
        argv = [
            str(SYSTEMD_RUN),
            "--user",
            "--wait",
            "--collect",
            "--quiet",
            "--pipe",
            f"--unit={unit_name}",
            "--service-type=exec",
            "--property=ExitType=cgroup",
            "--property=KillMode=control-group",
            "--property=SendSIGKILL=yes",
            "--property=FinalKillSignal=SIGKILL",
            "--property=Slice=app.slice",
            "--property=ExecStartPre=/usr/bin/sleep 0.2",
            "--property=TimeoutStopSec=15s",
            f"--property=RuntimeMaxSec={runtime}s",
            "--property=UMask=0077",
            "--property=NoNewPrivileges=yes",
            "--property=PrivateUsers=yes",
            "--property=RestrictAddressFamilies=AF_INET AF_INET6",
            f"--property=BindPaths={target}:{target}",
            f"--property=ReadOnlyPaths={target.parent}",
            "--property=ReadWritePaths="
            + " ".join(
                (
                    str(target),
                    child_environment["UV_CACHE_DIR"],
                    child_environment["npm_config_cache"],
                )
            ),
            "--property=UnsetEnvironment=" + " ".join(sorted(unset_environment)),
            f"--working-directory={target}",
            str(ENV),
            "-i",
        ]
        argv.extend(
            f"{key}={child_environment[key]}" for key in sorted(child_environment)
        )
        argv.extend(
            [
                str(PYTHON),
                "-I",
                "-S",
                str(target / "scripts" / "clean_room_check.py"),
                "--landlock-exec",
                str(target),
                child_environment["UV_CACHE_DIR"],
                child_environment["npm_config_cache"],
            ]
        )
        return argv

    def _show(self, unit_name: str) -> subprocess.CompletedProcess[bytes]:
        return self._run_control(
            [
                str(SYSTEMCTL),
                "--user",
                "show",
                unit_name,
                "--property=ActiveState",
                "--property=SubState",
                "--property=ControlGroup",
            ]
        )

    @staticmethod
    def _properties(output: bytes) -> dict[str, str]:
        values: dict[str, str] = {}
        try:
            decoded = output.decode("utf-8")
        except UnicodeDecodeError:
            raise CleanRoomError("containment_control_failure") from None
        for line in decoded.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value
        return values

    @staticmethod
    def _validate_control_group(raw: str) -> str:
        relative = Path(raw.lstrip("/"))
        if not raw.startswith("/") or ".." in relative.parts or not relative.parts:
            raise CleanRoomError("containment_binding_invalid")
        return raw

    @staticmethod
    def _expected_control_group(unit_name: str) -> str:
        uid = os.geteuid()
        return f"/user.slice/user-{uid}.slice/user@{uid}.service/app.slice/{unit_name}"

    @staticmethod
    def _cgroup_name(contained: ContainedUnit) -> str | None:
        return contained.control_group or contained.candidate_control_group

    def _cgroup_path(self, contained: ContainedUnit) -> Path | None:
        raw = self._cgroup_name(contained)
        if raw is None:
            return None
        validated = self._validate_control_group(raw)
        return CGROUP_ROOT / validated.lstrip("/")

    def _cgroup_population(self, contained: ContainedUnit) -> bool | None:
        cgroup_path = self._cgroup_path(contained)
        if cgroup_path is None:
            return None
        try:
            if not cgroup_path.exists():
                return False
            for process_file in cgroup_path.rglob("cgroup.procs"):
                if process_file.read_text(encoding="ascii").strip():
                    return True
            events = cgroup_path / "cgroup.events"
            if events.is_file():
                values = dict(
                    line.split(maxsplit=1)
                    for line in events.read_text(encoding="ascii").splitlines()
                    if " " in line
                )
                if values.get("populated") not in {None, "0"}:
                    return True
            return False
        except OSError:
            return None

    def _direct_cgroup_kill(self, contained: ContainedUnit) -> bool:
        cgroup_path = self._cgroup_path(contained)
        if cgroup_path is None:
            return False
        kill_path = cgroup_path / "cgroup.kill"
        try:
            if not cgroup_path.exists():
                return True
            descriptor = os.open(
                kill_path,
                os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
        except FileNotFoundError:
            return not cgroup_path.exists()
        except OSError:
            return False
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                return False
            return os.write(descriptor, b"1") == 1
        except OSError:
            return False
        finally:
            os.close(descriptor)

    def _cleanup_failed_start(self, contained: ContainedUnit) -> None:
        try:
            self.signal(contained, signal.SIGTERM)
        except BaseException:
            pass
        try:
            contained.process.wait(timeout=1.0)
        except BaseException:
            pass
        population = self._cgroup_population(contained)
        if population is not False or _process_running(contained.process):
            try:
                self.signal(contained, signal.SIGKILL)
            except BaseException:
                pass
            try:
                if _process_running(contained.process):
                    contained.process.wait(timeout=5.0)
            except BaseException:
                pass

    def start(
        self,
        *,
        target: Path,
        child_environment: dict[str, str],
        unit_name: str,
        runtime_seconds: float,
        register: Callable[[ContainedUnit], None],
    ) -> ContainedUnit:
        argv = self.build_argv(
            target=target,
            child_environment=child_environment,
            unit_name=unit_name,
            runtime_seconds=runtime_seconds,
        )
        expected_control_group = self._expected_control_group(unit_name)
        process_slot = _ProcessSlot()
        provisional = ContainedUnit(
            unit_name=unit_name,
            process=process_slot,
            control_group=None,
            candidate_control_group=expected_control_group,
            binding_confirmed=False,
        )
        active_handle = provisional
        process_bound = False
        try:
            blocked = {signal.SIGINT, signal.SIGTERM}
            previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
            try:
                register(provisional)
                self._lifecycle_probe("before_spawn")
                process = self._process_factory(
                    argv,
                    cwd=target,
                    env=dict(self._environment),
                    start_new_session=False,
                )
                process_slot.bind(process)
                process_bound = True
                self._lifecycle_probe("process_bound")
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

            deadline = self._clock() + CONTAINMENT_BIND_SECONDS
            while self._clock() < deadline:
                result = self._show(unit_name)
                if result.returncode == 0:
                    properties = self._properties(result.stdout)
                    control_group = properties.get("ControlGroup", "")
                    if control_group:
                        validated = self._validate_control_group(control_group)
                        observed = ContainedUnit(
                            unit_name=unit_name,
                            process=process_slot,
                            control_group=validated,
                            candidate_control_group=expected_control_group,
                            binding_confirmed=True,
                        )
                        active_handle = observed
                        register(observed)
                        if validated != expected_control_group:
                            raise CleanRoomError("containment_binding_invalid")
                        self._lifecycle_probe("observed_bound")
                        return observed
                return_code = process_slot.poll()
                if return_code is not None:
                    break
                self._sleeper(0.05)
        except BaseException as exc:
            self._cleanup_failed_start(active_handle)
            if isinstance(exc, CleanRoomCancelled) or not isinstance(exc, Exception):
                raise
            reason = (
                "containment_binding_failed"
                if process_bound
                else "containment_start_failed"
            )
            raise CleanRoomError(reason) from None
        self._cleanup_failed_start(active_handle)
        raise CleanRoomError("containment_binding_failed")

    def signal(self, contained: ContainedUnit, sent: signal.Signals) -> None:
        controller_succeeded = False
        try:
            result = self._run_control(
                [
                    str(SYSTEMCTL),
                    "--user",
                    "kill",
                    "--kill-whom=all",
                    f"--signal={sent.name}",
                    contained.unit_name,
                ]
            )
            controller_succeeded = result.returncode == 0
        except BaseException:
            controller_succeeded = False

        direct_succeeded = False
        if sent == signal.SIGKILL:
            direct_succeeded = self._direct_cgroup_kill(contained)
        if not controller_succeeded and not direct_succeeded:
            raise CleanRoomError("containment_signal_failed")

    def ensure_extinct(self, contained: ContainedUnit) -> bool:
        if not contained.binding_confirmed:
            try:
                result = self._show(contained.unit_name)
            except BaseException:
                return False
            if result.returncode != 0:
                return False
            properties = self._properties(result.stdout)
            observed_group = properties.get("ControlGroup", "")
            if observed_group:
                validated = self._validate_control_group(observed_group)
                if validated != contained.candidate_control_group:
                    return False
            active = properties.get("ActiveState", "unknown")
            if active in {"active", "activating", "deactivating", "reloading"}:
                return False
            population = self._cgroup_population(contained)
            return (
                population is False
                and not _process_running(contained.process)
                and contained.candidate_control_group is not None
            )
        if contained.control_group is None:
            return False
        population = self._cgroup_population(contained)
        if population is None or population:
            return False
        if _process_running(contained.process):
            return False
        try:
            result = self._show(contained.unit_name)
        except BaseException:
            return True
        if result.returncode != 0:
            return True
        active = self._properties(result.stdout).get("ActiveState", "unknown")
        return active not in {"active", "activating", "deactivating", "reloading"}


def normalize_exit_code(return_code: int) -> int:
    """Map signal and invalid child statuses to portable shell exit codes."""

    if return_code < 0:
        return min(255, 128 + abs(return_code))
    if return_code > 255:
        return 1
    return return_code


def _wait_for_gate(
    contained: ContainedUnit,
    containment: ContainmentBackend,
    *,
    check_timeout: float,
    terminate_grace: float,
    kill_grace: float,
    progress_interval: float,
    phase: Callable[[str], None],
) -> int:
    remaining = check_timeout
    while remaining > 0:
        interval = min(progress_interval, remaining)
        try:
            return contained.process.wait(timeout=interval)
        except KeyboardInterrupt:
            phase("cancel-term")
            containment.signal(contained, signal.SIGTERM)
            try:
                contained.process.wait(timeout=terminate_grace)
            except subprocess.TimeoutExpired:
                phase("cancel-kill")
                containment.signal(contained, signal.SIGKILL)
                try:
                    contained.process.wait(timeout=kill_grace)
                except subprocess.TimeoutExpired:
                    pass
            raise CleanRoomCancelled from None
        except subprocess.TimeoutExpired:
            remaining -= interval
            if remaining > 0:
                phase("gate-running")

    phase("timeout-term")
    containment.signal(contained, signal.SIGTERM)
    try:
        contained.process.wait(timeout=terminate_grace)
    except subprocess.TimeoutExpired:
        phase("timeout-kill")
        containment.signal(contained, signal.SIGKILL)
        try:
            contained.process.wait(timeout=kill_grace)
        except subprocess.TimeoutExpired:
            raise CleanRoomTimeout("gate_kill_timeout") from None
    raise CleanRoomTimeout


def _assure_extinction(
    contained: ContainedUnit,
    containment: ContainmentBackend,
    *,
    terminate_grace: float,
    kill_grace: float,
    phase: Callable[[str], None],
) -> bool:
    def is_extinct() -> bool:
        try:
            return containment.ensure_extinct(contained)
        except BaseException:
            return False

    if is_extinct():
        return True

    phase("containment-term")
    try:
        containment.signal(contained, signal.SIGTERM)
    except BaseException:
        pass
    if _process_running(contained.process):
        try:
            contained.process.wait(timeout=terminate_grace)
        except BaseException:
            pass
    if is_extinct():
        return True

    phase("containment-kill")
    try:
        containment.signal(contained, signal.SIGKILL)
    except BaseException:
        pass
    if _process_running(contained.process):
        try:
            contained.process.wait(timeout=kill_grace)
        except BaseException:
            return False
    return is_extinct()


def _unit_name(identity: tuple[int, int]) -> str:
    device, inode = identity
    return f"sklegal-cleanroom-{os.getpid()}-{device:x}-{inode:x}.service"


def run(
    *,
    repo_root: Path = REPO_ROOT,
    environment: Mapping[str, str] | None = None,
    source_runner: SourceRunner = _run_source_command,
    containment: ContainmentBackend | None = None,
    broker_factory: Callable[[Path], BrokerRuntime] = _create_brokers,
    workspace_factory: WorkspaceFactory = _create_workspace,
    after_copy_hook: Callable[[Path, Path], None] | None = None,
    check_timeout: float = CHECK_TIMEOUT_SECONDS,
    terminate_grace: float = TERMINATE_GRACE_SECONDS,
    kill_grace: float = KILL_GRACE_SECONDS,
    progress_interval: float = PROGRESS_INTERVAL_SECONDS,
    source_timeout: float = SOURCE_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    emit: Callable[[dict[str, object]], None] = emit_event,
) -> CleanRoomReceipt:
    """Run one bounded clean-room gate and always return a terminal receipt."""

    started = clock()
    source_root_display = str(repo_root)
    active_environment = dict(os.environ if environment is None else environment)
    source_root: Path | None = None
    source_root_fd: int | None = None
    scratch_root: Path | None = None
    workspace: WorkspaceLease | None = None
    contained: ContainedUnit | None = None
    brokers: BrokerRuntime | None = None
    active_containment: ContainmentBackend | None = containment
    file_count = 0
    inventory_sha256: str | None = None
    status = "failed"
    reason = "internal_error"
    exit_code = 1
    containment_extinct = False
    external_brokers_cleaned = True
    broker_resources = {key: 0 for key in BROKER_RESOURCE_KEYS}
    temporary_cleaned = True
    termination_requested = False
    cleanup_started = False
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    sigterm_installed = False

    def request_termination(
        signum: int,
        frame: object,
    ) -> None:
        del signum, frame
        nonlocal termination_requested
        termination_requested = True
        if not cleanup_started:
            raise CleanRoomCancelled

    def phase(name: str) -> None:
        _phase(name, started=started, clock=clock, emit=emit)

    try:
        signal.signal(signal.SIGTERM, request_termination)
        sigterm_installed = True
        durations = (
            check_timeout,
            terminate_grace,
            kill_grace,
            progress_interval,
            source_timeout,
        )
        if any(not math.isfinite(value) or value <= 0 for value in durations):
            raise CleanRoomError("invalid_timeout_configuration")

        source_root = _validate_repo_root(repo_root)
        source_root_display = str(source_root)
        source_root_fd = _open_directory(source_root)
        _ensure_directory_identity(
            source_root,
            source_root_fd,
            reason="source_root_identity_changed",
        )
        _validate_trusted_executables()
        phase("preflight-validated")
        scratch_root = resolve_scratch_root(
            environment=active_environment,
            repo_root=source_root,
        )
        uv_cache, npm_cache = _cache_roots(
            active_environment,
            repo_root=source_root,
        )
        phase("scratch-validated")
        files = source_files(
            repo_root=source_root,
            runner=source_runner,
            repo_root_fd=source_root_fd,
            timeout=source_timeout,
        )
        file_count = len(files)
        phase("source-allowlist")

        active_containment = containment or SystemdContainment()
        active_containment.preflight()
        phase("containment-validated")
        workspace = workspace_factory(scratch_root)
        workspace.ensure_identity()
        temporary_cleaned = False
        phase("temporary-created")
        inventory_sha256 = _copy_sources(
            files,
            repo_root=source_root,
            target=workspace.path,
            source_root_fd=source_root_fd,
            after_copy_hook=after_copy_hook,
        )
        _ensure_directory_identity(
            source_root,
            source_root_fd,
            reason="source_root_identity_changed",
        )
        os.close(source_root_fd)
        source_root_fd = None
        workspace.ensure_identity()
        phase("copy-verified")
        broker_signals = {signal.SIGINT, signal.SIGTERM}
        broker_previous_mask = signal.pthread_sigmask(
            signal.SIG_BLOCK,
            broker_signals,
        )
        try:
            brokers = broker_factory(workspace.path)
            external_brokers_cleaned = False
            brokers.start()
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, broker_previous_mask)
        phase("brokers-started")
        child_environment = _child_environment(
            target=workspace.path,
            uv_cache=uv_cache,
            npm_cache=npm_cache,
            broker_environment=brokers.environment,
        )
        workspace.ensure_identity()

        def register_containment(value: ContainedUnit) -> None:
            nonlocal contained
            contained = value

        contained = active_containment.start(
            target=workspace.path,
            child_environment=child_environment,
            unit_name=_unit_name(workspace.identity),
            runtime_seconds=check_timeout,
            register=register_containment,
        )
        phase("gate-started")
        child_exit = normalize_exit_code(
            _wait_for_gate(
                contained,
                active_containment,
                check_timeout=check_timeout,
                terminate_grace=terminate_grace,
                kill_grace=kill_grace,
                progress_interval=progress_interval,
                phase=phase,
            )
        )
        workspace.ensure_identity()
        if child_exit != 0:
            raise CleanRoomError("gate_failed", exit_code=child_exit)
        status = "passed"
        reason = "ok"
        exit_code = 0
    except CleanRoomTimeout as exc:
        status = "timed_out"
        reason = exc.reason
        exit_code = exc.exit_code
    except CleanRoomCancelled as exc:
        status = "cancelled"
        reason = exc.reason
        exit_code = exc.exit_code
    except CleanRoomError as exc:
        reason = exc.reason
        exit_code = exc.exit_code
    except KeyboardInterrupt:
        status = "cancelled"
        reason = "cancelled"
        exit_code = 130
    except Exception:
        reason = "internal_error"
    finally:
        cleanup_started = True
        if source_root_fd is not None:
            os.close(source_root_fd)
            source_root_fd = None
        if contained is not None and active_containment is not None:
            containment_extinct = _assure_extinction(
                contained,
                active_containment,
                terminate_grace=terminate_grace,
                kill_grace=kill_grace,
                phase=phase,
            )
        else:
            containment_extinct = True

        if brokers is not None:
            try:
                external_brokers_cleaned = brokers.close()
                counts = dict(brokers.resource_counts)
                if set(counts) != set(BROKER_RESOURCE_KEYS) or any(
                    not isinstance(value, int) or value < 0 for value in counts.values()
                ):
                    raise CleanRoomError("broker_resource_invalid")
                broker_resources = {key: counts[key] for key in BROKER_RESOURCE_KEYS}
            except BaseException:
                external_brokers_cleaned = False
            if external_brokers_cleaned:
                phase("brokers-cleaned")

        identity_changed = False
        if workspace is not None:
            try:
                workspace.ensure_identity()
            except CleanRoomError:
                identity_changed = True
            temporary_cleaned = workspace.cleanup()
            if temporary_cleaned:
                phase("temporary-cleaned")

        if identity_changed:
            status = "failed"
            reason = "temporary_identity_changed"
            exit_code = 1
        elif workspace is not None and not temporary_cleaned:
            status = "failed"
            reason = "temporary_cleanup_failed"
            exit_code = 1
        if contained is not None and not containment_extinct:
            status = "failed"
            reason = "containment_not_extinct"
            exit_code = 1
        if brokers is not None and not external_brokers_cleaned:
            status = "failed"
            reason = "broker_cleanup_failed"
            exit_code = 1
        cleanup_failed = status == "failed" and reason in {
            "temporary_identity_changed",
            "temporary_cleanup_failed",
            "containment_not_extinct",
            "broker_cleanup_failed",
        }
        if sigterm_installed and previous_sigterm is not None:
            try:
                signal.signal(signal.SIGTERM, previous_sigterm)
            except BaseException:
                status = "failed"
                reason = "signal_handler_restore_failed"
                exit_code = 1
                cleanup_failed = True
        if termination_requested and not cleanup_failed:
            status = "cancelled"
            reason = "cancelled"
            exit_code = 130

    elapsed = round(max(0.0, clock() - started), 3)
    receipt = CleanRoomReceipt(
        status=status,
        exit_code=exit_code,
        reason=reason,
        source_root=source_root_display,
        scratch_root=str(scratch_root) if scratch_root is not None else None,
        source_file_count=file_count,
        source_inventory_sha256=inventory_sha256,
        elapsed_seconds=elapsed,
        temporary_identity=(
            f"{workspace.identity[0]}:{workspace.identity[1]}"
            if workspace is not None
            else None
        ),
        temporary_cleaned=temporary_cleaned,
        containment_unit=contained.unit_name if contained is not None else None,
        containment_extinct=containment_extinct,
        external_brokers_cleaned=external_brokers_cleaned,
        broker_resources=broker_resources,
    )
    emit(receipt.as_event())
    return receipt


def main() -> int:
    if len(sys.argv) > 1:
        if sys.argv[1] != "--landlock-exec":
            return 125
        return _landlock_exec(sys.argv[2:])
    return run().exit_code


if __name__ == "__main__":
    raise SystemExit(main())
