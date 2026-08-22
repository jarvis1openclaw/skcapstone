"""Fail-closed file and key capability checks for issuer custody."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

MAX_PASSPHRASE_BYTES = 16 * 1024


class CustodyValidationError(ValueError):
    """Raised without including credential material in the error text."""


def read_owner_passphrase(path: Path) -> bytes:
    """Read one owner-only passphrase file without broad whitespace stripping."""

    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise CustodyValidationError("passphrase file is not owner-only")
        raw = os.read(fd, MAX_PASSPHRASE_BYTES + 1)
    except (OSError, CustodyValidationError) as exc:
        if isinstance(exc, CustodyValidationError):
            raise
        raise CustodyValidationError("passphrase file is unavailable") from None
    finally:
        try:
            os.close(fd)
        except (UnboundLocalError, OSError):
            pass

    if len(raw) > MAX_PASSPHRASE_BYTES:
        raise CustodyValidationError("passphrase file exceeds the size limit")
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    if not raw or b"\x00" in raw or b"\r" in raw or b"\n" in raw:
        raise CustodyValidationError("passphrase file has invalid contents")
    return raw


@dataclass(frozen=True, slots=True)
class KeyCapability:
    fingerprint: str
    record_type: str
    capabilities: frozenset[str]

    @property
    def can_sign(self) -> bool:
        return "s" in self.capabilities

    @property
    def can_encrypt(self) -> bool:
        return "e" in self.capabilities


def parse_gpg_capabilities(lines: Iterable[str]) -> tuple[KeyCapability, ...]:
    """Parse only public/secret key records from GnuPG colon metadata."""

    records: list[KeyCapability] = []
    pending: tuple[str, str, frozenset[str]] | None = None
    for line in lines:
        fields = line.rstrip("\n").split(":")
        if not fields:
            continue
        kind = fields[0]
        if kind not in {"pub", "sec", "sub", "ssb"}:
            if kind == "fpr" and pending is not None and len(fields) > 9:
                record_type, _, capabilities = pending
                records.append(KeyCapability(fields[9], record_type, capabilities))
                pending = None
            continue
        if len(fields) <= 11:
            raise CustodyValidationError("key capability metadata is incomplete")
        key_id = fields[4]
        capabilities = frozenset(char for char in fields[11] if char in "sSenacd")
        pending = (kind, key_id, capabilities)
    if pending is not None:
        record_type, key_id, capabilities = pending
        records.append(KeyCapability(key_id, record_type, capabilities))
    if not records:
        raise CustodyValidationError("key capability metadata is empty")
    return tuple(records)


def validate_declared_capabilities(
    records: Iterable[KeyCapability],
    declared_signing_subkeys: Iterable[str] = (),
) -> tuple[KeyCapability, ...]:
    """Reject a declared signing subkey that lacks cryptographic signing use."""

    values = tuple(records)
    primary = tuple(record for record in values if record.record_type in {"pub", "sec"})
    if len(primary) != 1 or not primary[0].can_sign:
        raise CustodyValidationError("issuer primary key lacks signing capability")
    declared = set(declared_signing_subkeys)
    for record in values:
        if record.fingerprint in declared and not record.can_sign:
            raise CustodyValidationError(
                "declared signing subkey lacks signing capability"
            )
    return values


@dataclass(frozen=True, slots=True)
class CustodyCoherence:
    fingerprint: str
    passphrase_accepted: bool


def validate_key_coherence(
    *,
    private_fingerprint: str,
    public_fingerprint: str,
    passphrase: bytes,
    unlock_probe: Callable[[bytes], bool],
) -> CustodyCoherence:
    """Prove key identity and unlock agreement without retaining the secret."""

    if not private_fingerprint or private_fingerprint != public_fingerprint:
        raise CustodyValidationError("private and public fingerprints do not match")
    try:
        accepted = bool(unlock_probe(passphrase))
    except Exception:
        accepted = False
    if not accepted:
        raise CustodyValidationError("key unlock probe failed")
    return CustodyCoherence(private_fingerprint, True)
