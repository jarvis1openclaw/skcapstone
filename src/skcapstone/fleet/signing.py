"""Signed desired state (Card 3.5, R6): the actuation trust boundary.

Spec and placement writes carry a detached capauth/PGP signature over
canonical payload bytes in the writer.signature slot the Phase 1 store
reserved. sknoded verifies before actuating (converge.py). Rollout is
permissive-then-enforce behind the SKFLEET_SIGNING env flag; off is the
default, so Phase 1/2 behavior is unchanged until the key ceremony.

capauth is a lazy soft dependency: every factory degrades to None instead
of raising, and callers treat None per mode (off ignores it, enforce
fails safe by refusing actuation, never by stopping running services).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

MODES = frozenset({"off", "permissive", "enforce"})
SIGNING_ENV = "SKFLEET_SIGNING"


def signing_mode() -> str:
    """The rollout mode: off (default) | permissive | enforce."""
    mode = os.environ.get(SIGNING_ENV, "off").strip().lower()
    return mode if mode in MODES else "off"


def canonical_bytes(payload: dict) -> bytes:
    """Deterministic bytes of a payload with its signature slot blanked.

    The signature covers everything else in the file, including
    generation and updatedAt, so replaying an old signed spec over a
    newer one is detectable as invalid.
    """
    body = json.loads(json.dumps(payload, sort_keys=True))
    writer = dict(body.get("writer") or {})
    writer["signature"] = None
    body["writer"] = writer
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_payload(payload: dict, verifier: Callable[[bytes, str], bool]) -> tuple[str, str]:
    """Classify one payload: verified, unsigned, or invalid (with detail)."""
    signature = (payload.get("writer") or {}).get("signature")
    if not signature:
        return ("unsigned", "no signature in writer block")
    try:
        ok = verifier(canonical_bytes(payload), signature)
    except Exception as exc:
        return ("invalid", f"verifier error: {exc}")
    if ok:
        return ("verified", "signature matches a trusted key")
    return ("invalid", "signature does not match any trusted key")


def _capauth_home() -> Path | None:
    """Resolve the local capauth home: the real resolver, env fallback.

    capauth.resolve_capauth_home() is the canonical source; the
    CAPAUTH_HOME env var is the fallback used when capauth is not
    installed, so the roster and key ceremony stay testable without it.
    """
    try:
        from capauth import resolve_capauth_home

        return resolve_capauth_home()
    except Exception:
        pass
    env = os.environ.get("CAPAUTH_HOME")
    return Path(env) if env else None


def capauth_signer() -> Callable[[bytes], str] | None:
    """A signer over this seat's capauth identity key, or None.

    Reads <capauth_home>/identity/private.asc; passphrase from the
    CAPAUTH_PASSPHRASE env var (empty default). Any failure returns None:
    signing is best-effort at write time, and enforcement lives at the
    actuation boundary, not here.
    """
    home = _capauth_home()
    if home is None:
        return None
    key_path = home / "identity" / "private.asc"
    if not key_path.exists():
        return None
    try:
        from capauth.crypto import get_backend

        armor = key_path.read_text(encoding="utf-8")
        passphrase = os.environ.get("CAPAUTH_PASSPHRASE", "")
        backend = get_backend()

        def _sign(data: bytes) -> str:
            return backend.sign(data, armor, passphrase)

        return _sign
    except Exception:
        return None


def default_signer() -> Callable[[bytes], str] | None:
    """The signer store writes use when none is passed: None while off."""
    if signing_mode() == "off":
        return None
    return capauth_signer()


def load_roster() -> list[str]:
    """Trusted writer public keys (armored) from the LOCAL capauth home.

    <capauth_home>/identity/public.asc (this seat) plus every *.asc under
    <capauth_home>/fleet-trust/ (installed by the key ceremony runbook).
    Never read from the synced fleet tree: the roster must not be
    writable by the thing it authenticates.
    """
    home = _capauth_home()
    if home is None:
        return []
    keys: list[str] = []
    own = home / "identity" / "public.asc"
    if own.exists():
        keys.append(own.read_text(encoding="utf-8"))
    trust_dir = home / "fleet-trust"
    if trust_dir.exists():
        for path in sorted(trust_dir.glob("*.asc")):
            keys.append(path.read_text(encoding="utf-8"))
    return keys


def capauth_verifier() -> Callable[[bytes, str], bool] | None:
    """A verifier over the local trust roster, or None when empty."""
    roster = load_roster()
    if not roster:
        return None
    try:
        from capauth.crypto import get_backend

        backend = get_backend()
    except Exception:
        return None

    def _verify(data: bytes, signature: str) -> bool:
        for key in roster:
            try:
                if backend.verify(data, signature, key):
                    return True
            except Exception:
                continue
        return False

    return _verify
