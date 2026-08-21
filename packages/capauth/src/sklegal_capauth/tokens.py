"""CapAuth signed credential construction and strict parsing."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Never, Protocol, Self

from capauth import (  # type: ignore[import-untyped]
    SignedToken,
    TokenPayload,
    TokenType,
    sign_manifest,
)
from pydantic import Field, StringConstraints, model_validator

from .models import (
    MAX_DELEGATION_DEPTH,
    MAX_TTL_SECONDS,
    CapabilityClaims,
    CapabilityGrant,
    DelegationClaims,
    Fingerprint,
    OpaqueName,
    PrincipalContext,
    Sha256,
    StrictValue,
)

MAX_CREDENTIAL_BYTES = 256 * 1024
MAX_AUTHORIZATION_BYTES = (MAX_DELEGATION_DEPTH + 1) * MAX_CREDENTIAL_BYTES + 4096
MAX_SIGNATURE_LENGTH = 128 * 1024
METADATA_KEY = "sklegal_capability"


class CredentialFormatError(ValueError):
    """The presented credential is not the exact SKLegal wire contract."""


class CredentialSigningError(RuntimeError):
    """A trusted signer did not produce a usable detached signature."""


def _reject_duplicate_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CredentialFormatError("JSON object contains a duplicate member")
        result[key] = value
    return result


def _strict_json_value(raw: str | bytes) -> object:
    try:
        return json.loads(raw, object_pairs_hook=_reject_duplicate_members)
    except Exception:
        raise CredentialFormatError("JSON input is malformed") from None


class CredentialSigner(Protocol):
    @property
    def issuer_fingerprint(self) -> str:
        """Return the full trusted issuer fingerprint."""

    def sign(self, payload_bytes: bytes) -> str:
        """Return a detached signature over exactly these bytes."""


class CapAuthManifestSigner:
    """Production signer using CapAuth's public detached-signature primitive.

    The issuer fingerprint is explicit. This adapter never reads a private key,
    passphrase, live agent profile, or synced token store. Key custody belongs to
    gpg-agent or a later narrow signing sidecar.
    """

    def __init__(self, issuer_fingerprint: str) -> None:
        normalized = issuer_fingerprint.strip().upper()
        if len(normalized) not in {40, 64}:
            raise ValueError("issuer fingerprint must be full length")
        if any(char not in "0123456789ABCDEF" for char in normalized):
            raise ValueError("issuer fingerprint must be uppercase hexadecimal")
        self._issuer_fingerprint = normalized

    @property
    def issuer_fingerprint(self) -> str:
        return self._issuer_fingerprint

    def sign(self, payload_bytes: bytes) -> str:
        try:
            signature = sign_manifest(
                payload_bytes,
                signer=self._issuer_fingerprint,
                passphrase=None,
            )
        except Exception:
            raise CredentialSigningError("CapAuth signing failed") from None
        if not signature.strip():
            raise CredentialSigningError("CapAuth returned an empty signature")
        return signature


class PresentedCapability:
    """Request-local raw leaf credential plus its complete ancestor chain.

    The type intentionally cannot serialize or reveal its value through string or
    repr. Boundary code constructs it from volatile transport input, passes it to
    the authorizer, and discards it.
    """

    __slots__ = ("__ancestors", "__leaf")

    def __init__(self, leaf: str, ancestors: tuple[str, ...] = ()) -> None:
        if not isinstance(leaf, str) or not leaf:
            raise CredentialFormatError("presented leaf credential is empty")
        if any(not isinstance(item, str) or not item for item in ancestors):
            raise CredentialFormatError("presented ancestor credential is empty")
        if len(ancestors) > MAX_DELEGATION_DEPTH:
            raise CredentialFormatError("presented credential chain is too deep")
        if len(set((*ancestors, leaf))) != len(ancestors) + 1:
            raise CredentialFormatError(
                "presented credential chain repeats a credential"
            )
        self.__leaf = leaf
        self.__ancestors = tuple(ancestors)

    @classmethod
    def single(cls, raw_credential: str) -> Self:
        return cls(raw_credential)

    @classmethod
    def delegated(cls, *, leaf: str, ancestors: tuple[str, ...]) -> Self:
        return cls(leaf, ancestors)

    def credentials_for_verification(self) -> tuple[str, ...]:
        """Return root through leaf for immediate request-local verification."""

        return (*self.__ancestors, self.__leaf)

    def with_child(self, child: str) -> PresentedCapability:
        return PresentedCapability(
            child,
            (*self.__ancestors, self.__leaf),
        )

    def __repr__(self) -> str:
        return "PresentedCapability(<redacted>)"

    def __str__(self) -> str:
        return "<redacted capability>"

    def __reduce__(self) -> Never:
        raise TypeError("presented capabilities cannot be serialized")


class WirePayload(StrictValue):
    token_id: Sha256
    token_type: Literal["capability"]
    issuer: Fingerprint
    subject: OpaqueName
    capabilities: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    not_before: datetime
    metadata: dict[str, object]
    audience: str


ArmoredSignature = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        max_length=MAX_SIGNATURE_LENGTH,
    ),
]


class WireEnvelope(StrictValue):
    skcapstone_token: Literal["1.0"]
    payload: WirePayload
    signature: ArmoredSignature


RawCredential = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=MAX_CREDENTIAL_BYTES,
    ),
]


class AuthorizationWireChain(StrictValue):
    leaf: RawCredential
    ancestors: tuple[RawCredential, ...] = Field(
        default=(),
        max_length=MAX_DELEGATION_DEPTH,
    )

    @model_validator(mode="after")
    def validate_unique_chain(self) -> Self:
        if len(set((*self.ancestors, self.leaf))) != len(self.ancestors) + 1:
            raise ValueError("presented chain repeats a credential")
        return self


class AuthorizationWireEnvelope(StrictValue):
    sklegal_presented_capability: Literal["1.0"]
    chain: AuthorizationWireChain


@dataclass(frozen=True, repr=False)
class ParsedCapability:
    """Request-local parsed credential with safe identifiers and signed claims."""

    token: SignedToken = field(repr=False)
    claims: CapabilityClaims
    credential_digest: str
    signed_payload_bytes: bytes = field(repr=False)

    def __repr__(self) -> str:
        return (
            "ParsedCapability(credential_digest="
            f"{self.credential_digest!r}, claims=<signed>)"
        )


def _require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise CredentialFormatError(f"{field_name} must be UTC with offset zero")
    return value.astimezone(UTC)


def _payload_identity(payload: TokenPayload) -> str:
    data = payload.model_dump(mode="json")
    data["token_id"] = ""
    encoded = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_signature(signature: str) -> str:
    return signature.replace("\r\n", "\n").strip() + "\n"


def credential_digest(token: SignedToken, policy_version: str) -> str:
    """Bind every signed byte, signer, and verifier policy into one opaque ID."""

    signature = _normalize_signature(token.signature or "")
    payload_bytes = token.payload.model_dump_json().encode("utf-8")
    material = b"\x00".join(
        (
            b"sklegal-credential-digest/v1",
            token.payload.issuer.strip().upper().encode("ascii"),
            policy_version.encode("ascii"),
            payload_bytes,
            signature.encode("utf-8"),
        )
    )
    return hashlib.sha256(material).hexdigest()


def export_presented_token(token: SignedToken) -> str:
    """Create the exact compact wire envelope without storing it."""

    if not token.signature:
        raise CredentialSigningError("refusing to export an unsigned credential")
    envelope = {
        "skcapstone_token": "1.0",
        "payload": token.payload.model_dump(mode="json"),
        "signature": _normalize_signature(token.signature),
    }
    return json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def parse_presented_token(raw_credential: str) -> ParsedCapability:
    """Parse an exact signed SKLegal token and reject all legacy flexibility."""

    if not isinstance(raw_credential, str):
        raise CredentialFormatError("credential must be text")
    encoded = raw_credential.encode("utf-8")
    if len(encoded) > MAX_CREDENTIAL_BYTES:
        raise CredentialFormatError("credential exceeds the size limit")
    try:
        raw_value = _strict_json_value(encoded)
        envelope = WireEnvelope.model_validate_json(
            json.dumps(
                raw_value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
        )
    except Exception:
        raise CredentialFormatError("credential envelope is malformed") from None

    raw_payload = envelope.payload
    issued_at = _require_utc(raw_payload.issued_at, "issued_at")
    expires_at = _require_utc(raw_payload.expires_at, "expires_at")
    not_before = _require_utc(raw_payload.not_before, "not_before")
    if set(raw_payload.metadata) != {METADATA_KEY}:
        raise CredentialFormatError("credential metadata is not the closed contract")
    try:
        claims = CapabilityClaims.model_validate_json(
            json.dumps(raw_payload.metadata[METADATA_KEY], separators=(",", ":"))
        )
    except Exception:
        raise CredentialFormatError("SKLegal capability claims are malformed") from None

    if raw_payload.subject != claims.principal.subject:
        raise CredentialFormatError("payload subject does not match principal")
    if raw_payload.audience != claims.grant.audience.value:
        raise CredentialFormatError("payload audience does not match signed grant")
    if raw_payload.capabilities != (claims.grant.capability.value,):
        raise CredentialFormatError(
            "payload must contain exactly the signed capability"
        )

    try:
        token_payload = TokenPayload(
            token_id=raw_payload.token_id,
            token_type=TokenType.CAPABILITY,
            issuer=raw_payload.issuer,
            subject=raw_payload.subject,
            capabilities=list(raw_payload.capabilities),
            issued_at=issued_at,
            expires_at=expires_at,
            not_before=not_before,
            metadata={METADATA_KEY: claims.model_dump(mode="json")},
            audience=raw_payload.audience,
        )
        if token_payload.token_id != _payload_identity(token_payload):
            raise ValueError("payload identity mismatch")
        signature = (
            _normalize_signature(envelope.signature)
            if envelope.signature.strip()
            else ""
        )
        token = SignedToken(
            payload=token_payload,
            signature=signature,
            verified=False,
        )
    except Exception:
        raise CredentialFormatError("credential payload is invalid") from None
    return ParsedCapability(
        token=token,
        claims=claims,
        credential_digest=credential_digest(
            token,
            claims.verifier_policy_version,
        ),
        signed_payload_bytes=token_payload.model_dump_json().encode("utf-8"),
    )


def export_authorization_bearer(presented: PresentedCapability) -> str:
    """Encode one request-local leaf and ordered ancestry for a Bearer value."""

    chain = presented.credentials_for_verification()
    envelope = AuthorizationWireEnvelope(
        sklegal_presented_capability="1.0",
        chain=AuthorizationWireChain(
            leaf=chain[-1],
            ancestors=chain[:-1],
        ),
    )
    encoded = envelope.model_dump_json()
    if len(encoded.encode("utf-8")) > MAX_AUTHORIZATION_BYTES:
        raise CredentialFormatError("authorization value exceeds the size limit")
    return encoded


def parse_authorization_bearer(raw_bearer: str) -> PresentedCapability:
    """Decode an unambiguous direct token or versioned request-local chain."""

    if not isinstance(raw_bearer, str) or not raw_bearer:
        raise CredentialFormatError("authorization value is empty")
    if len(raw_bearer.encode("utf-8")) > MAX_AUTHORIZATION_BYTES:
        raise CredentialFormatError("authorization value exceeds the size limit")
    value = _strict_json_value(raw_bearer)
    if not isinstance(value, dict):
        raise CredentialFormatError("authorization value is not an object")
    keys = set(value)
    direct_keys = {"skcapstone_token", "payload", "signature"}
    chain_keys = {"sklegal_presented_capability", "chain"}
    if keys == direct_keys:
        return PresentedCapability.single(raw_bearer)
    if keys != chain_keys:
        raise CredentialFormatError("authorization value is ambiguous")
    try:
        envelope = AuthorizationWireEnvelope.model_validate_json(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
        )
        return PresentedCapability.delegated(
            leaf=envelope.chain.leaf,
            ancestors=envelope.chain.ancestors,
        )
    except Exception:
        raise CredentialFormatError("authorization chain is malformed") from None


class CapabilityIssuer:
    """Issue one-use, one-capability tokens without persistent token storage."""

    def __init__(
        self,
        signer: CredentialSigner,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._signer = signer
        self._clock = clock or (lambda: datetime.now(UTC))

    def issue_root(
        self,
        *,
        principal: PrincipalContext,
        grant: CapabilityGrant,
        ttl_seconds: int = 300,
        max_delegation_depth: int = 0,
    ) -> PresentedCapability:
        delegation = DelegationClaims(
            depth=0,
            max_depth=max_delegation_depth,
            parent_credential_digest=None,
        )
        raw = self._issue(
            principal=principal,
            grant=grant,
            delegation=delegation,
            ttl_seconds=ttl_seconds,
            parent_expires_at=None,
        )
        return PresentedCapability.single(raw)

    def _issue_child(
        self,
        *,
        parent: ParsedCapability,
        principal: PrincipalContext,
        grant: CapabilityGrant,
        ttl_seconds: int,
        max_depth: int,
    ) -> str:
        delegation = DelegationClaims(
            depth=parent.claims.delegation.depth + 1,
            max_depth=max_depth,
            parent_credential_digest=parent.credential_digest,
        )
        return self._issue(
            principal=principal,
            grant=grant,
            delegation=delegation,
            ttl_seconds=ttl_seconds,
            parent_expires_at=parent.token.payload.expires_at,
        )

    def _issue(
        self,
        *,
        principal: PrincipalContext,
        grant: CapabilityGrant,
        delegation: DelegationClaims,
        ttl_seconds: int,
        parent_expires_at: datetime | None,
    ) -> str:
        if not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
            raise ValueError("capability TTL must be between 1 second and 1 hour")
        now = _require_utc(self._clock(), "issuer clock")
        expires_at = now + timedelta(seconds=ttl_seconds)
        if parent_expires_at is not None:
            expires_at = min(expires_at, parent_expires_at)
        if expires_at <= now:
            raise ValueError("delegated capability would already be expired")
        claims = CapabilityClaims(
            principal=principal,
            grant=grant,
            delegation=delegation,
        )
        payload = TokenPayload(
            token_id="",
            token_type=TokenType.CAPABILITY,
            issuer=self._signer.issuer_fingerprint.strip().upper(),
            subject=principal.subject,
            capabilities=[grant.capability.value],
            issued_at=now,
            expires_at=expires_at,
            not_before=now,
            metadata={METADATA_KEY: claims.model_dump(mode="json")},
            audience=grant.audience.value,
        )
        payload.token_id = _payload_identity(payload)
        payload_bytes = payload.model_dump_json().encode("utf-8")
        signature = self._signer.sign(payload_bytes)
        if not signature.strip():
            raise CredentialSigningError("trusted signer returned no signature")
        return export_presented_token(
            SignedToken(payload=payload, signature=signature, verified=True)
        )
