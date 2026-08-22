"""Synthetic issuer signing sidecar for isolated tests only.

This reference server speaks the narrow `sklegal-issuer-sidecar/v1`
protocol over a private unix socket and signs with throwaway synthetic
keys in a temporary GNUPGHOME. It never touches a live keyring, a synced
CapAuth home, or any production path. It is test scaffolding, not a
production sidecar.
"""

from __future__ import annotations

import base64
import json
import socket
import subprocess
import threading
from pathlib import Path

SIDECAR_SCHEMA = "sklegal-issuer-sidecar/v1"
MAX_REQUEST_BYTES = 300 * 1024
GPG_TIMEOUT_SECONDS = 30


def _recv_exactly(connection: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = connection.recv(remaining)
        if not chunk:
            raise ConnectionError("client closed the channel")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class SyntheticSidecarServer:
    """Minimal readiness and detached-sign server for synthetic keys."""

    def __init__(self, socket_path: Path, gnupg_home: Path) -> None:
        self._socket_path = Path(socket_path)
        self._gnupg_home = Path(gnupg_home)
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def __enter__(self) -> SyntheticSidecarServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def start(self) -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self._socket_path))
        listener.listen(8)
        listener.settimeout(0.1)
        self._listener = listener
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._listener is not None:
            self._listener.close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._socket_path.unlink(missing_ok=True)

    def _serve(self) -> None:
        assert self._listener is not None
        while not self._stopped.is_set():
            try:
                connection, _addr = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                self._handle(connection)
            except (ConnectionError, OSError, ValueError):
                pass
            finally:
                connection.close()

    def _handle(self, connection: socket.socket) -> None:
        header = _recv_exactly(connection, 4)
        length = int.from_bytes(header, "big")
        if length < 1 or length > MAX_REQUEST_BYTES:
            return
        request = json.loads(_recv_exactly(connection, length).decode("utf-8"))
        if request.get("schema") != SIDECAR_SCHEMA:
            self._reply(connection, {"ok": False})
            return
        fingerprint = request.get("fingerprint")
        operation = request.get("op")
        if not isinstance(fingerprint, str):
            self._reply(connection, {"ok": False})
            return
        if operation == "readiness":
            self._reply(
                connection,
                {"ok": True, "ready": self._holds_key(fingerprint)},
            )
            return
        if operation == "sign":
            payload_b64 = request.get("payload_b64")
            if not isinstance(payload_b64, str):
                self._reply(connection, {"ok": False})
                return
            signature = self._sign(fingerprint, base64.b64decode(payload_b64))
            if signature is None:
                self._reply(connection, {"ok": False})
            else:
                self._reply(connection, {"ok": True, "signature": signature})
            return
        self._reply(connection, {"ok": False})

    def _reply(self, connection: socket.socket, body: dict[str, object]) -> None:
        encoded = json.dumps(
            {**body, "schema": SIDECAR_SCHEMA},
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        connection.sendall(len(encoded).to_bytes(4, "big") + encoded)

    def _gpg(self, arguments: list[str], input_bytes: bytes | None) -> bytes | None:
        try:
            completed = subprocess.run(
                [
                    "gpg",
                    "--batch",
                    "--yes",
                    "--homedir",
                    str(self._gnupg_home),
                    *arguments,
                ],
                input=input_bytes,
                capture_output=True,
                timeout=GPG_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout

    def _holds_key(self, fingerprint: str) -> bool:
        listing = self._gpg(["--with-colons", "--list-secret-keys", fingerprint], None)
        if listing is None:
            return False
        held = {
            line.split(":")[9]
            for line in listing.decode("utf-8", errors="replace").splitlines()
            if line.startswith("fpr:") and len(line.split(":")) > 9
        }
        return fingerprint in held

    def _sign(self, fingerprint: str, payload: bytes) -> str | None:
        if not self._holds_key(fingerprint):
            return None
        signature = self._gpg(
            ["--armor", "--detach-sign", "--local-user", fingerprint, "--output", "-"],
            payload,
        )
        if signature is None:
            return None
        return signature.decode("utf-8", errors="replace")
