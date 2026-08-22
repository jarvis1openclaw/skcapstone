"""Dedicated issuer custody, versioned issuer policy, and signing handles.

This module implements the S1-03A1 issuer boundary:

- a declared, dedicated SKLegal issuer identity that can never be the Casey
  human identity key or the Jarvis agent identity key;
- a versioned trusted issuer policy store with explicit rotation and
  rollback and no stale fallback;
- signing handles that expose the issuer key only through gpg-agent or a
  narrow sidecar and never carry a passphrase in arguments, environment,
  logs, or stored rows.

No production key is created here. Tests use synthetic temporary keys in
isolated directories only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, Self

from pydantic import Field, model_validator

from .backends import BackendUnavailable, TrustedIssuerGrant, TrustedIssuerSnapshot
from .models import Fingerprint, OpaqueName, StrictValue

CASEY_IDENTITY_FINGERPRINT = "AD80D077A047BABF29EEC97AF454FDBC3B1C37D9"
"""Casey human identity trust anchor. Never an application issuer."""

JARVIS_IDENTITY_FINGERPRINT = "C8D406A46F2DF4894E4FB41580A638570C9D41C4"
"""Jarvis agent identity trust anchor. Never an application issuer."""

FORBIDDEN_ISSUER_FINGERPRINTS: frozenset[str] = frozenset(
    {CASEY_IDENTITY_FINGERPRINT, JARVIS_IDENTITY_FINGERPRINT}
)
"""Identity trust anchors that must never sign SKLegal capabilities."""

CUSTODY_SCHEMA: Literal["sklegal-issuer-custody/v1"] = "sklegal-issuer-custody/v1"
POLICY_SCHEMA: Literal["sklegal-issuer-policy/v1"] = "sklegal-issuer-policy/v1"
REVOCATION_SCHEMA: Literal["sklegal-issuer-policy-revocation/v1"] = (
    "sklegal-issuer-policy-revocation/v1"
)

MAX_POLICY_BYTES = 64 * 1024
GPG_TIMEOUT_SECONDS = 30


class IssuerCustodyError(ValueError):
    """The issuer custody declaration is invalid or forbidden."""


class SigningUnavailable(RuntimeError):
    """The signing boundary has no trustworthy current answer."""


def _reject_duplicate_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("issuer policy input contains a duplicate member")
        result[key] = value
    return result


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _assert_outside_synced_homes(
    path: Path,
    synced_homes: tuple[str, ...],
    field_name: str,
) -> None:
    for synced in synced_homes:
        root = Path(synced).expanduser()
        if _path_within(path, root) or _path_within(root, path):
            raise IssuerCustodyError(
                f"{field_name} must not be inside a synced CapAuth home"
            )


class IssuerCustodyPolicy(StrictValue):
    """Declared dedicated issuer identity and its protected custody location.

    The declaration is deployment input. It names the exact full issuer
    fingerprint, the custody mechanism, and the protected home or socket
    location. Validation proves the identity is dedicated and the location
    is outside every declared synced CapAuth home.
    """

    schema_version: Literal["sklegal-issuer-custody/v1"] = CUSTODY_SCHEMA
    issuer_fingerprint: Fingerprint
    custody: Literal["gpg-agent", "sidecar"]
    key_home: str | None = None
    socket_path: str | None = None
    rotated_from: Fingerprint | None = None
    synced_home_paths: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_custody(self) -> Self:
        if self.issuer_fingerprint in FORBIDDEN_ISSUER_FINGERPRINTS:
            raise IssuerCustodyError(
                "identity trust anchors cannot be the application issuer"
            )
        if self.rotated_from is not None:
            if self.rotated_from in FORBIDDEN_ISSUER_FINGERPRINTS:
                raise IssuerCustodyError(
                    "rotation lineage cannot name an identity trust anchor"
                )
            if self.rotated_from == self.issuer_fingerprint:
                raise IssuerCustodyError(
                    "rotation lineage must name a different prior fingerprint"
                )
        synced = tuple(str(Path(item).expanduser()) for item in self.synced_home_paths)
        if self.custody == "gpg-agent":
            if self.key_home is None or self.socket_path is not None:
                raise IssuerCustodyError(
                    "gpg-agent custody requires key_home and forbids socket_path"
                )
            home = Path(self.key_home).expanduser()
            if not home.is_absolute():
                raise IssuerCustodyError("key_home must be an absolute path")
            _assert_outside_synced_homes(home, synced, "key_home")
        else:
            if self.socket_path is None or self.key_home is not None:
                raise IssuerCustodyError(
                    "sidecar custody requires socket_path and forbids key_home"
                )
            socket = Path(self.socket_path).expanduser()
            if not socket.is_absolute():
                raise IssuerCustodyError("socket_path must be an absolute path")
            _assert_outside_synced_homes(socket, synced, "socket_path")
        return self


class TrustedIssuerPolicyDocument(StrictValue):
    """One versioned trusted issuer policy revision.

    Fingerprint allowlists carry exact capability, audience, and principal
    kind ceilings through `TrustedIssuerGrant`. Identity trust anchors are
    never allowed as issuers. Rotation is a new revision with `supersedes`
    naming the prior one. A revoked revision always fails closed.
    """

    schema_version: Literal["sklegal-issuer-policy/v1"] = POLICY_SCHEMA
    policy_version: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1)
    status: Literal["active", "revoked"]
    supersedes: int | None = Field(default=None, ge=1)
    issuers: tuple[TrustedIssuerGrant, ...]

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        fingerprints = [issuer.fingerprint for issuer in self.issuers]
        if not fingerprints or len(fingerprints) != len(set(fingerprints)):
            raise IssuerCustodyError(
                "trusted issuer fingerprints must be nonempty and unique"
            )
        forbidden = FORBIDDEN_ISSUER_FINGERPRINTS.intersection(fingerprints)
        if forbidden:
            raise IssuerCustodyError("identity trust anchors cannot be trusted issuers")
        if self.supersedes is not None and self.supersedes >= self.revision:
            raise IssuerCustodyError("supersedes must name an earlier policy revision")
        return self


class IssuerPolicyRevocation(StrictValue):
    """Tombstone record that permanently retires one policy revision."""

    schema_version: Literal["sklegal-issuer-policy-revocation/v1"] = REVOCATION_SCHEMA
    revision: int = Field(ge=1)
    reason: OpaqueName


def _read_regular_file(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise BackendUnavailable("issuer policy input is unavailable")
        with os.fdopen(fd, "rb", closefd=True) as stream:
            fd = -1
            raw = stream.read(MAX_POLICY_BYTES + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(raw) > MAX_POLICY_BYTES:
        raise BackendUnavailable("issuer policy input exceeds the size limit")
    return raw


def _strict_document[DocumentT: StrictValue](
    raw: bytes, model: type[DocumentT]
) -> DocumentT:
    try:
        parsed = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_members
        )
        return model.model_validate_json(
            json.dumps(parsed, separators=(",", ":"), ensure_ascii=True)
        )
    except BackendUnavailable:
        raise
    except Exception as exc:
        raise BackendUnavailable("issuer policy input is unusable") from exc


class IssuerPolicyStore:
    """Directory of versioned issuer policy documents.

    Every load is a fresh strict read. There is no cache, no symlink
    following, and no stale fallback. Rotation writes a new revision.
    Rollback selects an earlier active revision explicitly. Revocation
    writes a separate tombstone that makes the revision fail closed even
    if the original document is later edited.
    """

    def __init__(self, root: Path) -> None:
        root = Path(root)
        try:
            metadata = root.stat()
        except OSError as exc:
            raise BackendUnavailable("issuer policy store is unavailable") from exc
        if root.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise BackendUnavailable("issuer policy store is not a real directory")
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def document_path(self, revision: int) -> Path:
        return self._root / f"issuer-policy-{revision}.json"

    def revocation_path(self, revision: int) -> Path:
        return self._root / f"issuer-policy-{revision}.revoked.json"

    def revisions(self) -> tuple[int, ...]:
        found: set[int] = set()
        prefix = "issuer-policy-"
        suffix = ".json"
        for entry in self._root.iterdir():
            name = entry.name
            if name.startswith(prefix) and name.endswith(suffix):
                number = name[len(prefix) : -len(suffix)]
                if number.isdigit():
                    found.add(int(number))
        return tuple(sorted(found))

    def is_revoked(self, revision: int) -> bool:
        """A revision is closed by any present tombstone.

        Only a cleanly absent marker means not revoked. An unreadable,
        corrupt, or mismatched marker is ambiguous and therefore closes
        the revision: a revocation artifact can never resurrect a policy.
        """

        marker = self.revocation_path(revision)
        try:
            raw = _read_regular_file(marker)
        except FileNotFoundError:
            return False
        except (OSError, BackendUnavailable):
            return True
        try:
            _strict_document(raw, IssuerPolicyRevocation)
        except BackendUnavailable:
            pass
        return True

    def load(self, revision: int) -> TrustedIssuerPolicyDocument:
        document, _raw = self.load_raw(revision)
        return document

    def load_raw(self, revision: int) -> tuple[TrustedIssuerPolicyDocument, bytes]:
        if revision < 1:
            raise BackendUnavailable("issuer policy revision is invalid")
        if self.is_revoked(revision):
            raise BackendUnavailable("issuer policy revision is revoked")
        try:
            raw = _read_regular_file(self.document_path(revision))
            document = _strict_document(raw, TrustedIssuerPolicyDocument)
        except BackendUnavailable:
            raise
        except Exception as exc:
            raise BackendUnavailable("issuer policy is unavailable") from exc
        if document.revision != revision:
            raise BackendUnavailable("issuer policy revision mismatch")
        if document.status != "active":
            raise BackendUnavailable("issuer policy revision is not active")
        return document, raw

    def write(self, document: TrustedIssuerPolicyDocument) -> Path:
        """Provision one new revision. Existing files are never overwritten."""

        path = self.document_path(document.revision)
        if self.is_revoked(document.revision):
            raise IssuerCustodyError("cannot write over a revoked policy revision")
        raw = document.model_dump_json().encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags, 0o644)
        except FileExistsError as exc:
            raise IssuerCustodyError(
                "issuer policy revisions are immutable; rotate to a new revision"
            ) from exc
        except OSError as exc:
            raise BackendUnavailable("issuer policy store is not writable") from exc
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return path

    def revoke(self, revision: int, *, reason: str) -> Path:
        """Retire one revision permanently with an explicit tombstone."""

        if revision < 1:
            raise IssuerCustodyError("issuer policy revision is invalid")
        record = IssuerPolicyRevocation(revision=revision, reason=reason)
        raw = record.model_dump_json().encode("utf-8")
        path = self.revocation_path(revision)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags, 0o644)
        except FileExistsError as exc:
            raise IssuerCustodyError(
                "issuer policy revision is already revoked"
            ) from exc
        except OSError as exc:
            raise BackendUnavailable("issuer policy store is not writable") from exc
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return path


class VersionedTrustedIssuerBackend:
    """Strict read-through backend over one explicit policy revision.

    Rotation and rollback are deployment decisions expressed by choosing a
    different revision. The backend re-reads the store on every snapshot,
    so a changed, removed, or revoked policy takes effect immediately and
    never silently falls back to stale state.
    """

    def __init__(self, store: IssuerPolicyStore, revision: int) -> None:
        self._store = store
        self._revision = revision

    @property
    def policy_revision(self) -> int:
        return self._revision

    def snapshot(self) -> TrustedIssuerSnapshot:
        document, raw = self._store.load_raw(self._revision)
        return TrustedIssuerSnapshot(
            policy_version=document.policy_version,
            revision=hashlib.sha256(raw).hexdigest(),
            issuers=document.issuers,
        )


@dataclass(frozen=True)
class SigningReadiness:
    """Sanitized signing boundary readiness report."""

    backend: str
    fingerprint: str
    ready: bool
    detail: str


class IssuerSigningHandle(Protocol):
    """Protected signing boundary contract.

    Implementations never accept, store, or forward a passphrase. Secret
    material stays inside gpg-agent or the narrow sidecar process.
    """

    @property
    def issuer_fingerprint(self) -> str:
        """Return the full dedicated issuer fingerprint."""

    def readiness(self) -> SigningReadiness:
        """Report sanitized readiness without signing anything."""

    def sign(self, payload_bytes: bytes) -> str:
        """Return a detached signature or raise SigningUnavailable."""


class GpgAgentSigningHandle:
    """Signing handle that delegates secret operations to gpg-agent.

    The handle invokes gpg in batch mode with no passphrase argument. Any
    passphrase prompt is answered only by the gpg-agent that owns the
    protected home. All failures raise `SigningUnavailable` with sanitized
    static messages.
    """

    def __init__(
        self,
        custody: IssuerCustodyPolicy,
        *,
        gpg_binary: str = "gpg",
    ) -> None:
        if custody.custody != "gpg-agent" or custody.key_home is None:
            raise IssuerCustodyError("GpgAgentSigningHandle requires gpg-agent custody")
        self._custody = custody
        self._gpg_binary = gpg_binary
        self._home = str(Path(custody.key_home).expanduser())

    @property
    def issuer_fingerprint(self) -> str:
        return self._custody.issuer_fingerprint

    def _run(self, arguments: list[str], input_bytes: bytes | None) -> bytes:
        command = [
            self._gpg_binary,
            "--batch",
            "--yes",
            "--homedir",
            self._home,
            *arguments,
        ]
        try:
            completed = subprocess.run(
                command,
                input=input_bytes,
                capture_output=True,
                timeout=GPG_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise SigningUnavailable("issuer signer is unavailable") from None
        if completed.returncode != 0:
            raise SigningUnavailable("issuer signing operation failed") from None
        return completed.stdout

    def readiness(self) -> SigningReadiness:
        fingerprint = self.issuer_fingerprint
        home = Path(self._home)
        if home.is_symlink() or not home.is_dir():
            return SigningReadiness(
                backend="gpg-agent",
                fingerprint=fingerprint,
                ready=False,
                detail="issuer key home is unavailable",
            )
        try:
            listing = self._run(
                ["--with-colons", "--list-secret-keys", fingerprint],
                None,
            )
        except SigningUnavailable:
            return SigningReadiness(
                backend="gpg-agent",
                fingerprint=fingerprint,
                ready=False,
                detail="issuer signer is unavailable",
            )
        held = {
            line.split(":")[9]
            for line in listing.decode("utf-8", errors="replace").splitlines()
            if line.startswith("fpr:") and len(line.split(":")) > 9
        }
        if fingerprint not in held:
            return SigningReadiness(
                backend="gpg-agent",
                fingerprint=fingerprint,
                ready=False,
                detail="issuer key is not held by the signer",
            )
        return SigningReadiness(
            backend="gpg-agent",
            fingerprint=fingerprint,
            ready=True,
            detail="issuer signer holds the dedicated key",
        )

    def sign(self, payload_bytes: bytes) -> str:
        signature = self._run(
            [
                "--armor",
                "--detach-sign",
                "--local-user",
                self.issuer_fingerprint,
                "--output",
                "-",
            ],
            payload_bytes,
        )
        text = signature.decode("utf-8", errors="replace")
        if not text.strip():
            raise SigningUnavailable("issuer signer returned no signature") from None
        return text


SIDECAR_SCHEMA: Literal["sklegal-issuer-sidecar/v1"] = "sklegal-issuer-sidecar/v1"
MAX_SIDECAR_PAYLOAD_BYTES = 256 * 1024
MAX_SIDECAR_RESPONSE_BYTES = 128 * 1024
DEFAULT_SIDECAR_TIMEOUT_SECONDS = 10.0


def _recv_exactly(sock: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise SigningUnavailable("issuer sidecar closed the channel") from None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class SidecarSigningHandle:
    """Signing handle that delegates secret operations to a narrow sidecar.

    The sidecar is a separate minimal process that owns the protected key
    home and answers exactly two operations over a private unix socket:
    readiness and detached signing. The request carries only the schema,
    operation, fingerprint, and payload bytes. No passphrase, key, or
    environment value ever crosses the channel. All failures raise
    `SigningUnavailable` with sanitized static messages.
    """

    def __init__(
        self,
        custody: IssuerCustodyPolicy,
        *,
        timeout_seconds: float = DEFAULT_SIDECAR_TIMEOUT_SECONDS,
    ) -> None:
        if custody.custody != "sidecar" or custody.socket_path is None:
            raise IssuerCustodyError("SidecarSigningHandle requires sidecar custody")
        if not 0.1 <= timeout_seconds <= 60.0:
            raise IssuerCustodyError(
                "sidecar timeout must be between 0.1 and 60 seconds"
            )
        self._custody = custody
        self._socket_path = str(Path(custody.socket_path).expanduser())
        self._timeout = timeout_seconds

    @property
    def issuer_fingerprint(self) -> str:
        return self._custody.issuer_fingerprint

    def _exchange(self, request: dict[str, object]) -> dict[str, object]:
        encoded = json.dumps(
            {**request, "schema": SIDECAR_SCHEMA},
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self._timeout)
            sock.connect(self._socket_path)
            sock.sendall(len(encoded).to_bytes(4, "big") + encoded)
            header = _recv_exactly(sock, 4)
            length = int.from_bytes(header, "big")
            if length < 1 or length > MAX_SIDECAR_RESPONSE_BYTES:
                raise SigningUnavailable(
                    "issuer sidecar response exceeds the size limit"
                ) from None
            body = _recv_exactly(sock, length)
            response = json.loads(body.decode("utf-8"))
        except SigningUnavailable:
            raise
        except Exception:
            raise SigningUnavailable("issuer signer is unavailable") from None
        finally:
            if sock is not None:
                sock.close()
        if not isinstance(response, dict) or response.get("schema") != SIDECAR_SCHEMA:
            raise SigningUnavailable("issuer sidecar response is unusable") from None
        if response.get("ok") is not True:
            raise SigningUnavailable("issuer signing operation failed") from None
        return response

    def readiness(self) -> SigningReadiness:
        fingerprint = self.issuer_fingerprint
        try:
            response = self._exchange({"op": "readiness", "fingerprint": fingerprint})
        except SigningUnavailable:
            return SigningReadiness(
                backend="sidecar",
                fingerprint=fingerprint,
                ready=False,
                detail="issuer signer is unavailable",
            )
        if response.get("ready") is not True:
            return SigningReadiness(
                backend="sidecar",
                fingerprint=fingerprint,
                ready=False,
                detail="issuer key is not held by the signer",
            )
        return SigningReadiness(
            backend="sidecar",
            fingerprint=fingerprint,
            ready=True,
            detail="issuer signer holds the dedicated key",
        )

    def sign(self, payload_bytes: bytes) -> str:
        if len(payload_bytes) > MAX_SIDECAR_PAYLOAD_BYTES:
            raise SigningUnavailable(
                "issuer signing payload exceeds the size limit"
            ) from None
        response = self._exchange(
            {
                "op": "sign",
                "fingerprint": self.issuer_fingerprint,
                "payload_b64": base64.b64encode(payload_bytes).decode("ascii"),
            }
        )
        signature = response.get("signature")
        if not isinstance(signature, str) or not signature.strip():
            raise SigningUnavailable("issuer signer returned no signature") from None
        return signature
