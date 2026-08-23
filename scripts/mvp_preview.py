#!/usr/bin/env python3
"""Start and stop the loopback-only SKLegal public-synthetic MVP preview."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import http.server
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

LOOPBACK = "127.0.0.1"
MODE = "public-synthetic"
MAX_BODY_BYTES = 1_048_576
MAX_PROXY_RESPONSE_BYTES = 16_777_216
MAX_LOG_BYTES = 1_048_576
INSTANCE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
STATE_SCHEMA = "sklegal.mvp-preview-state/v1"


class PreviewError(RuntimeError):
    """A bounded preview lifecycle operation failed closed."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(131_072), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _assert_preview_bundle(dist: Path) -> None:
    assets = tuple(sorted((dist / "assets").glob("*.js")))
    if not assets:
        raise PreviewError("web production bundle has no JavaScript assets")
    rendered = b"".join(path.read_bytes() for path in assets)
    if b"Start public-synthetic session" not in rendered:
        raise PreviewError("web bundle does not enable public-synthetic bootstrap")
    if b"Internal authentication is unavailable in this build" in rendered:
        raise PreviewError("web bundle retains the disabled authentication branch")


def _run(root: Path, *command: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise PreviewError(f"command failed: {command[0]}")
    return result.stdout.strip()


def _validate_candidate(root: Path, expected_commit: str) -> dict[str, str]:
    if not root.is_dir() or root.is_symlink():
        raise PreviewError("candidate root must be a real directory")
    commit = _run(root, "git", "rev-parse", "HEAD")
    if commit != expected_commit:
        raise PreviewError("candidate commit does not match the required revision")
    if _run(root, "git", "status", "--porcelain", "--untracked-files=no"):
        raise PreviewError("candidate tracked files are dirty")
    required = (root / "uv.lock", root / "package-lock.json", root / "apps/web")
    if any(not path.exists() for path in required):
        raise PreviewError("candidate source or lock files are incomplete")
    return {
        "commit": commit,
        "tree": _run(root, "git", "rev-parse", "HEAD^{tree}"),
        "uv_lock_sha256": _sha256(root / "uv.lock"),
        "package_lock_sha256": _sha256(root / "package-lock.json"),
    }


def _runtime_dir(raw: str, instance: str) -> Path:
    if not INSTANCE_PATTERN.fullmatch(instance):
        raise PreviewError("instance name is invalid")
    root = Path(raw).expanduser().resolve(strict=False)
    path = root / instance
    if path.exists() and path.is_symlink():
        raise PreviewError("runtime directory cannot be a symbolic link")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _state_path(runtime: Path) -> Path:
    return runtime / "state.json"


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _read_state(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise PreviewError("preview state custody is invalid")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PreviewError("preview instance is not running") from None
    if payload.get("schema") != STATE_SCHEMA:
        raise PreviewError("preview state schema is invalid")
    return payload


def _port_available(port: int) -> bool:
    if not 1024 <= port <= 65535:
        raise PreviewError("preview ports must be between 1024 and 65535")
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        probe.bind((LOOPBACK, port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def _process_identity(pid: int) -> dict[str, Any]:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    command = Path(f"/proc/{pid}/cmdline").read_bytes()
    return {
        "pid": pid,
        "start_ticks": stat.rsplit(")", 1)[1].split()[19],
        "cmdline_sha256": hashlib.sha256(command).hexdigest(),
    }


def _identity_matches(record: dict[str, Any]) -> bool:
    try:
        current = _process_identity(int(record["pid"]))
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return False
    return current == {
        "pid": int(record["pid"]),
        "start_ticks": str(record["start_ticks"]),
        "cmdline_sha256": str(record["cmdline_sha256"]),
    }


def _wait_url(url: str, *, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise PreviewError("preview readiness deadline expired")


def _open_log(path: Path):
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
        with path.open("rb") as source:
            source.seek(-MAX_LOG_BYTES, os.SEEK_END)
            tail = source.read()
        path.write_bytes(tail)
    return path.open("ab", buffering=0)


def _start_process(
    command: list[str], root: Path, log_path: Path
) -> subprocess.Popen[bytes]:
    log = _open_log(log_path)
    try:
        return subprocess.Popen(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log.close()


def _terminate(record: dict[str, Any]) -> str:
    pid = int(record["pid"])
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "already-stopped"
    if not _identity_matches(record):
        raise PreviewError("recorded PID identity changed; refusing to signal it")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return "stopped"
        time.sleep(0.05)
    if not _identity_matches(record):
        raise PreviewError("recorded PID identity changed during teardown")
    os.kill(pid, signal.SIGKILL)
    return "killed-after-timeout"


def _stop_state(path: Path) -> dict[str, str]:
    state = _read_state(path)
    results: dict[str, str] = {}
    for name in ("web", "api"):
        record = state.get("processes", {}).get(name)
        if not isinstance(record, dict):
            raise PreviewError("preview process record is incomplete")
        results[name] = _terminate(record)
    path.unlink()
    return results


class _PreviewHandler(http.server.SimpleHTTPRequestHandler):
    server_version = "SKLegalPublicSyntheticPreview/1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _preview_health(self) -> None:
        body = b'{"status":"ready","mode":"public-synthetic"}\n'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY_BYTES:
            self.send_error(413)
            return
        body = self.rfile.read(length) if length else None
        backend = f"{LOOPBACK}:{self.server.api_port}"  # type: ignore[attr-defined]
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"connection", "host", "content-length"}
        }
        if "Origin" in headers:
            headers["Origin"] = f"http://{backend}"
        connection = http.client.HTTPConnection(
            LOOPBACK, self.server.api_port, timeout=3
        )  # type: ignore[attr-defined]
        try:
            connection.request(
                self.command,
                self.path.removeprefix("/api") or "/",
                body=body,
                headers=headers,
            )
            response = connection.getresponse()
            payload = response.read(MAX_PROXY_RESPONSE_BYTES + 1)
            if len(payload) > MAX_PROXY_RESPONSE_BYTES:
                raise PreviewError("API response exceeds preview bound")
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in {
                    "connection",
                    "content-length",
                    "content-security-policy",
                }:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except Exception:
            body = b'{"detail":{"code":"preview_api_unavailable"}}\n'
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        finally:
            connection.close()

    def _static(self, *, head_only: bool = False) -> None:
        translated = Path(self.translate_path(self.path))
        if not translated.is_file():
            self.path = "/index.html"
        if head_only:
            super().do_HEAD()
        else:
            super().do_GET()

    def do_GET(self) -> None:
        if self.path == "/__preview/healthz":
            self._preview_health()
        elif self.path.startswith("/api/"):
            self._proxy()
        else:
            self._static()

    def do_HEAD(self) -> None:
        if self.path == "/__preview/healthz":
            self._preview_health()
        elif self.path.startswith("/api/"):
            self._proxy()
        else:
            self._static(head_only=True)

    def do_POST(self) -> None:
        self._proxy() if self.path.startswith("/api/") else self.send_error(405)

    def do_DELETE(self) -> None:
        self._proxy() if self.path.startswith("/api/") else self.send_error(405)


class _ReusableLoopbackServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True


def _web_server(
    dist: Path, web_port: int, api_port: int
) -> http.server.ThreadingHTTPServer:
    if not dist.is_dir() or not (dist / "index.html").is_file():
        raise PreviewError("web production build is absent")
    handler = lambda *args, **kwargs: _PreviewHandler(  # noqa: E731
        *args, directory=str(dist), **kwargs
    )
    server = _ReusableLoopbackServer((LOOPBACK, web_port), handler)
    server.api_port = api_port  # type: ignore[attr-defined]
    return server


def _serve_web(dist: Path, web_port: int, api_port: int) -> int:
    server = _web_server(dist, web_port, api_port)
    server.serve_forever()
    return 0


def _start(args: argparse.Namespace) -> int:
    if args.mode != MODE:
        raise PreviewError("only explicit public-synthetic mode is accepted")
    root = Path(args.candidate_root).expanduser().resolve(strict=True)
    runtime = _runtime_dir(args.runtime_root, args.instance)
    state_path = _state_path(runtime)
    if state_path.exists():
        raise PreviewError("preview state already exists; stop or reset it first")
    if args.api_port == args.web_port:
        raise PreviewError("API and web ports must be distinct")
    for port in (args.api_port, args.web_port):
        if not _port_available(port):
            raise PreviewError(f"loopback port {port} is unavailable")
    identity = _validate_candidate(root, args.candidate_commit)
    python = root / ".venv/bin/python"
    if not python.is_file():
        raise PreviewError(
            "candidate virtual environment is absent; run bootstrap first"
        )
    build_environment = dict(os.environ)
    build_environment.update(
        {
            "NODE_ENV": "development",
            "VITE_SKLEGAL_API_BASE": "/api",
            "VITE_SKLEGAL_PUBLIC_SYNTHETIC_PREVIEW": "1",
        }
    )
    _run(
        root,
        "npm",
        "run",
        "build",
        "--workspace",
        "@sklegal/web",
        env=build_environment,
    )
    dist = root / "apps/web/dist"
    _assert_preview_bundle(dist)
    identity["web_dist_sha256"] = _tree_sha256(dist)
    identity["browser_bootstrap"] = "enabled-public-synthetic-only"
    api = _start_process(
        [
            str(python),
            "-m",
            "uvicorn",
            "sklegal_api.public_synthetic_preview:app",
            "--host",
            LOOPBACK,
            "--port",
            str(args.api_port),
            "--no-access-log",
        ],
        root,
        runtime / "api.log",
    )
    web: subprocess.Popen[bytes] | None = None
    try:
        _wait_url(f"http://{LOOPBACK}:{args.api_port}/healthz")
        web = _start_process(
            [
                str(python),
                str(root / "scripts/mvp_preview.py"),
                "serve-web",
                "--dist",
                str(dist),
                "--web-port",
                str(args.web_port),
                "--api-port",
                str(args.api_port),
            ],
            root,
            runtime / "web.log",
        )
        _wait_url(f"http://{LOOPBACK}:{args.web_port}/__preview/healthz")
        state = {
            "schema": STATE_SCHEMA,
            "mode": MODE,
            "instance": args.instance,
            "candidate_root": str(root),
            "identity": identity,
            "endpoints": {
                "api": f"http://{LOOPBACK}:{args.api_port}",
                "web": f"http://{LOOPBACK}:{args.web_port}",
            },
            "processes": {
                "api": _process_identity(api.pid),
                "web": _process_identity(web.pid),
            },
        }
        _write_state(state_path, state)
    except Exception:
        for process in (web, api):
            if process is not None and process.poll() is None:
                process.terminate()
        raise
    print(json.dumps(state, sort_keys=True))
    return 0


def _stop(args: argparse.Namespace) -> int:
    runtime = _runtime_dir(args.runtime_root, args.instance)
    print(json.dumps(_stop_state(_state_path(runtime)), sort_keys=True))
    return 0


def _status(args: argparse.Namespace) -> int:
    runtime = _runtime_dir(args.runtime_root, args.instance)
    state = _read_state(_state_path(runtime))
    state["running"] = {
        name: _identity_matches(record) for name, record in state["processes"].items()
    }
    print(json.dumps(state, sort_keys=True))
    return 0 if all(state["running"].values()) else 1


def _reset(args: argparse.Namespace) -> int:
    runtime = _runtime_dir(args.runtime_root, args.instance)
    state_path = _state_path(runtime)
    if state_path.exists():
        _stop_state(state_path)
    for name in ("api.log", "web.log", "state.tmp"):
        try:
            (runtime / name).unlink()
        except FileNotFoundError:
            pass
    try:
        runtime.rmdir()
    except OSError:
        raise PreviewError("runtime directory contains unowned files") from None
    print(json.dumps({"instance": args.instance, "status": "reset"}))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    lifecycle = argparse.ArgumentParser(add_help=False)
    lifecycle.add_argument("--instance", default="default")
    lifecycle.add_argument("--runtime-root", default="/tmp/sklegal-mvp-preview")

    start = subparsers.add_parser("start", parents=[lifecycle])
    start.add_argument("--candidate-root", required=True)
    start.add_argument("--candidate-commit", required=True)
    start.add_argument("--mode", required=True)
    start.add_argument("--api-port", type=int, default=15172)
    start.add_argument("--web-port", type=int, default=15173)
    start.set_defaults(handler=_start)
    for name, handler in (("stop", _stop), ("status", _status), ("reset", _reset)):
        command = subparsers.add_parser(name, parents=[lifecycle])
        command.set_defaults(handler=handler)
    serve = subparsers.add_parser("serve-web")
    serve.add_argument("--dist", required=True, type=Path)
    serve.add_argument("--web-port", required=True, type=int)
    serve.add_argument("--api-port", required=True, type=int)
    serve.set_defaults(
        handler=lambda args: _serve_web(args.dist, args.web_port, args.api_port)
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        return int(args.handler(args))
    except (OSError, PreviewError, ValueError) as exc:
        print(f"preview denied: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
