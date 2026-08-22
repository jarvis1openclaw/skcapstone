"""Fail-closed tests for the narrow issuer signing sidecar handle."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from sklegal_capauth import (
    IssuerCustodyError,
    IssuerCustodyPolicy,
    SidecarSigningHandle,
    SigningUnavailable,
)

DEDICATED_FPR = "5" * 40
SIDECAR_SCHEMA = "sklegal-issuer-sidecar/v1"


def _custody(socket_path: Path) -> IssuerCustodyPolicy:
    return IssuerCustodyPolicy(
        issuer_fingerprint=DEDICATED_FPR,
        custody="sidecar",
        socket_path=str(socket_path),
    )


class _FakeServer:
    """Scriptable raw socket server for protocol chaos cases."""

    def __init__(self, socket_path: Path, behavior) -> None:
        self._socket_path = socket_path
        self._behavior = behavior
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def __enter__(self) -> _FakeServer:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self._socket_path))
        listener.listen(8)
        listener.settimeout(0.1)
        self._listener = listener
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
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
                self._behavior(connection)
            except OSError:
                pass
            finally:
                connection.close()


def _reply(
    connection: socket.socket, body: object, *, raw: bytes | None = None
) -> None:
    connection.recv(4096)
    _send(connection, body, raw=raw)


def _send(connection: socket.socket, body: object, *, raw: bytes | None = None) -> None:
    encoded = raw if raw is not None else json.dumps(body).encode("utf-8")
    connection.sendall(len(encoded).to_bytes(4, "big") + encoded)


def _ok_response(**extra: object) -> dict[str, object]:
    return {"schema": SIDECAR_SCHEMA, "ok": True, **extra}


class SidecarHandleContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.socket_path = self.root / "issuer-sidecar.sock"

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_requires_sidecar_custody_and_bounded_timeout(self) -> None:
        with self.assertRaises(IssuerCustodyError):
            SidecarSigningHandle(
                IssuerCustodyPolicy(
                    issuer_fingerprint=DEDICATED_FPR,
                    custody="gpg-agent",
                    key_home=str(self.root / "gpg"),
                )
            )
        with self.assertRaises(IssuerCustodyError):
            SidecarSigningHandle(_custody(self.socket_path), timeout_seconds=0.01)
        with self.assertRaises(IssuerCustodyError):
            SidecarSigningHandle(_custody(self.socket_path), timeout_seconds=3600.0)

    def test_unavailable_signer_fails_sanitized(self) -> None:
        handle = SidecarSigningHandle(_custody(self.socket_path))
        report = handle.readiness()
        self.assertFalse(report.ready)
        self.assertEqual(report.backend, "sidecar")
        self.assertEqual(report.fingerprint, DEDICATED_FPR)
        self.assertEqual(report.detail, "issuer signer is unavailable")
        with self.assertRaises(SigningUnavailable) as raised:
            handle.sign(b"payload")
        self.assertEqual(str(raised.exception), "issuer signer is unavailable")
        self.assertNotIn(str(self.socket_path), str(raised.exception))

    def test_readiness_and_sign_success(self) -> None:
        armor = (
            "-----BEGIN PGP SIGNATURE-----\nsynthetic\n-----END PGP SIGNATURE-----\n"
        )

        def behavior(connection: socket.socket) -> None:
            _reply(connection, _ok_response(ready=True, signature=armor))

        with _FakeServer(self.socket_path, behavior):
            handle = SidecarSigningHandle(_custody(self.socket_path))
            report = handle.readiness()
            self.assertTrue(report.ready)
            self.assertEqual(report.detail, "issuer signer holds the dedicated key")
            self.assertEqual(handle.sign(b"payload"), armor)

    def test_wrong_fingerprint_reports_not_ready_and_sign_fails(self) -> None:
        requests: list[dict[str, object]] = []

        def behavior(connection: socket.socket) -> None:
            length = int.from_bytes(_recv(connection, 4), "big")
            requests.append(json.loads(_recv(connection, length)))
            _send(connection, {"schema": SIDECAR_SCHEMA, "ok": True, "ready": False})

        def failing_behavior(connection: socket.socket) -> None:
            _reply(connection, {"schema": SIDECAR_SCHEMA, "ok": False})

        with _FakeServer(self.socket_path, behavior):
            handle = SidecarSigningHandle(_custody(self.socket_path))
            report = handle.readiness()
            self.assertFalse(report.ready)
            self.assertEqual(report.detail, "issuer key is not held by the signer")
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(set(request), {"schema", "op", "fingerprint"})
        self.assertNotIn("passphrase", json.dumps(request).lower())

        with _FakeServer(self.socket_path, failing_behavior):
            with self.assertRaises(SigningUnavailable) as raised:
                handle.sign(b"payload")
        self.assertEqual(str(raised.exception), "issuer signing operation failed")

    def test_malformed_sidecar_responses_fail_sanitized(self) -> None:
        cases = (
            lambda c: _reply(c, None, raw=b"this is not json"),
            lambda c: _reply(c, {"schema": "other/v9", "ok": True}),
            lambda c: _reply(c, ["not", "an", "object"]),
            lambda c: _reply(c, _ok_response(signature="")),
            lambda c: _reply(c, _ok_response(signature=None)),
        )
        for behavior in cases:
            with self.subTest(behavior=behavior):
                with _FakeServer(self.socket_path, behavior):
                    handle = SidecarSigningHandle(_custody(self.socket_path))
                    with self.assertRaises(SigningUnavailable):
                        handle.sign(b"payload")

    def test_oversized_response_fails_sanitized(self) -> None:
        def behavior(connection: socket.socket) -> None:
            connection.recv(4096)
            connection.sendall((200 * 1024).to_bytes(4, "big"))

        with _FakeServer(self.socket_path, behavior):
            handle = SidecarSigningHandle(_custody(self.socket_path))
            with self.assertRaises(SigningUnavailable) as raised:
                handle.sign(b"payload")
            self.assertIn("size limit", str(raised.exception))

    def test_socket_outage_mid_sign_fails_sanitized(self) -> None:
        def behavior(connection: socket.socket) -> None:
            connection.recv(4096)
            connection.shutdown(socket.SHUT_RDWR)

        with _FakeServer(self.socket_path, behavior):
            handle = SidecarSigningHandle(_custody(self.socket_path))
            with self.assertRaises(SigningUnavailable):
                handle.sign(b"payload")

    def test_slow_sidecar_times_out_sanitized(self) -> None:
        def behavior(connection: socket.socket) -> None:
            connection.recv(4096)
            time.sleep(2)

        with _FakeServer(self.socket_path, behavior):
            handle = SidecarSigningHandle(
                _custody(self.socket_path), timeout_seconds=0.2
            )
            with self.assertRaises(SigningUnavailable) as raised:
                handle.sign(b"payload")
            self.assertEqual(str(raised.exception), "issuer signer is unavailable")

    def test_oversized_payload_fails_before_connect(self) -> None:
        handle = SidecarSigningHandle(_custody(self.socket_path))
        with self.assertRaises(SigningUnavailable) as raised:
            handle.sign(b"x" * (300 * 1024))
        self.assertIn("size limit", str(raised.exception))

    def test_handle_never_touches_environment_or_passphrases(self) -> None:
        captured: list[dict[str, object]] = []

        def behavior(connection: socket.socket) -> None:
            length = int.from_bytes(_recv(connection, 4), "big")
            captured.append(json.loads(_recv(connection, length)))
            _send(connection, _ok_response(signature="sig"))

        before = dict(os.environ)
        with _FakeServer(self.socket_path, behavior):
            handle = SidecarSigningHandle(_custody(self.socket_path))
            handle.sign(b"payload")
        self.assertEqual(dict(os.environ), before)
        request = captured[0]
        self.assertEqual(set(request), {"schema", "op", "fingerprint", "payload_b64"})
        self.assertNotIn("passphrase", json.dumps(request).lower())


def _recv(connection: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = connection.recv(remaining)
        if not chunk:
            raise ConnectionError("closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


if __name__ == "__main__":
    unittest.main()
