"""Authorization-aware service for the joined Matter analysis projection."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Protocol
from uuid import UUID

from sklegal_capauth import AuthorizedContext, Capability, Purpose

from .contract import (
    JoinedAnalysisSnapshotProjection,
    MatterAnalysisRead,
    PageRead,
    PolicyIdentityRead,
)

_DIGEST_PLACEHOLDER = "0" * 64


class JoinedAnalysisStoreUnavailable(RuntimeError):
    """The joined analysis store cannot prove a current answer."""


class JoinedAnalysisCursorInvalid(ValueError):
    """The cursor is malformed, stale, or belongs to another snapshot."""


class JoinedAnalysisStore(Protocol):
    """Fail-closed store contract keyed only by authenticated scope."""

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool: ...

    def projection(
        self, tenant_id: UUID, matter_id: UUID
    ) -> JoinedAnalysisSnapshotProjection | None: ...


def projection_sha256(projection: JoinedAnalysisSnapshotProjection) -> str:
    """Digest canonical projection bytes with the digest slot zeroed.

    This removes self-reference while binding every other field and the fact
    that the slot exists at the exact contract location.
    """

    payload = projection.model_dump(mode="json", by_alias=False)
    payload["snapshot"]["projection_sha256"] = _DIGEST_PLACEHOLDER
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def projection_provenance_sha256(
    projection: JoinedAnalysisSnapshotProjection,
) -> dict[str, str]:
    """Recompute each declared provenance partition from typed content."""

    dumped = projection.model_dump(mode="json", by_alias=False)
    return {
        "matter_snapshot_sha256": _canonical_sha256(
            {
                "classification": dumped["classification"],
                "forum": dumped["forum"],
                "proceedings": dumped["proceedings"],
                "issues": dumped["issues"],
                "fact_assertions": dumped["fact_assertions"],
                "evidence_items": dumped["evidence_items"],
            }
        ),
        "claim_projection_revision": _canonical_sha256(
            {
                "theories": dumped["theories"],
                "elements": dumped["elements"],
            }
        ),
        "authority_snapshot": _canonical_sha256(dumped["authorities"]),
        "projection_revision": hashlib.sha256(
            projection.schema_revision.encode("ascii")
        ).hexdigest(),
    }


def verify_projection_digest(projection: JoinedAnalysisSnapshotProjection) -> None:
    expected = projection_provenance_sha256(projection)
    if any(
        getattr(projection.snapshot, name) != digest
        for name, digest in expected.items()
    ):
        raise JoinedAnalysisStoreUnavailable("joined analysis provenance digest drift")
    if projection_sha256(projection) != projection.snapshot.projection_sha256:
        raise JoinedAnalysisStoreUnavailable("joined analysis projection digest drift")


def _policy_identity(
    authorized: AuthorizedContext, matter_id: UUID
) -> PolicyIdentityRead:
    decision = authorized.decision
    if (
        not decision.allow
        or decision.tenant_id != authorized.principal.tenant_id
        or decision.matter_id != matter_id
        or decision.principal_id != authorized.principal.principal_id
        or decision.capability != Capability.CLAIM_REVIEW
        or decision.purpose != Purpose.CLAIM_REVIEW
        or decision.trusted_issuer_policy_revision is None
        or decision.revocation_revision is None
        or not decision.principal_policy_revisions
    ):
        raise JoinedAnalysisStoreUnavailable("authorization evidence is incomplete")
    return PolicyIdentityRead(
        authorization_decision_id=decision.decision_id,
        principal_id=decision.principal_id,
        verifier_policy_version=decision.verifier_policy_version,
        principal_policy_revisions=tuple(
            item.revision for item in decision.principal_policy_revisions
        ),
        trusted_issuer_policy_revision=decision.trusted_issuer_policy_revision,
        revocation_revision=decision.revocation_revision,
    )


def _encode_cursor(projection: JoinedAnalysisSnapshotProjection, offset: int) -> str:
    raw = (
        f"ja1:{projection.snapshot.snapshot_id}:{projection.snapshot.projection_sha256}:"
        f"{offset}"
    ).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(
    projection: JoinedAnalysisSnapshotProjection, cursor: str | None
) -> int:
    if cursor is None:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True).decode("ascii")
        prefix, snapshot_id, digest, offset_text = raw.split(":")
        offset = int(offset_text)
    except (UnicodeError, ValueError):
        raise JoinedAnalysisCursorInvalid("joined analysis cursor is invalid") from None
    if (
        prefix != "ja1"
        or snapshot_id != str(projection.snapshot.snapshot_id)
        or digest != projection.snapshot.projection_sha256
        or offset < 0
        or offset > len(projection.theories)
    ):
        raise JoinedAnalysisCursorInvalid("joined analysis cursor is stale")
    return offset


def build_matter_analysis(
    *,
    projection: JoinedAnalysisSnapshotProjection,
    authorized: AuthorizedContext,
    limit: int,
    cursor: str | None,
) -> MatterAnalysisRead:
    """Validate provenance and create a stable page over Claims and Defenses."""

    verify_projection_digest(projection)
    if (
        projection.tenant_id != authorized.principal.tenant_id
        or projection.matter_id != authorized.decision.matter_id
    ):
        raise JoinedAnalysisStoreUnavailable("joined analysis scope drift")
    if not 1 <= limit <= 100:
        raise JoinedAnalysisCursorInvalid("joined analysis page limit is invalid")
    offset = _decode_cursor(projection, cursor)
    theories = projection.theories[offset : offset + limit]
    theory_ids = {item.theory_id for item in theories}
    elements = tuple(
        item for item in projection.elements if item.theory_id in theory_ids
    )
    fact_ids = {
        identifier for item in theories for identifier in item.fact_assertion_ids
    } | {identifier for item in elements for identifier in item.fact_assertion_ids}
    evidence_ids = {
        link.evidence_item_id for item in theories for link in item.evidence
    } | {identifier for item in elements for identifier in item.evidence_item_ids}
    authority_ids = {
        link.authority_id for item in theories for link in item.authorities
    } | {
        item.burden.authority_id
        for item in elements
        if item.burden.authority_id is not None
    }
    issues = tuple(
        item.model_copy(
            update={
                "theory_ids": tuple(
                    identifier
                    for identifier in item.theory_ids
                    if identifier in theory_ids
                )
            }
        )
        for item in projection.issues
        if any(identifier in theory_ids for identifier in item.theory_ids)
    )
    next_offset = offset + len(theories)
    has_more = next_offset < len(projection.theories)
    next_cursor = _encode_cursor(projection, next_offset) if has_more else None
    page = PageRead(
        limit=limit,
        returned=len(theories),
        has_more=has_more,
        next_cursor=next_cursor,
    )
    return MatterAnalysisRead(
        tenant_id=projection.tenant_id,
        matter_id=projection.matter_id,
        snapshot=projection.snapshot,
        classification=projection.classification,
        policy_identity=_policy_identity(authorized, projection.matter_id),
        forum=projection.forum,
        proceedings=projection.proceedings,
        issues=issues,
        theories=theories,
        elements=elements,
        fact_assertions=tuple(
            item
            for item in projection.fact_assertions
            if item.fact_assertion_id in fact_ids
        ),
        evidence_items=tuple(
            item
            for item in projection.evidence_items
            if item.evidence_item_id in evidence_ids
        ),
        authorities=tuple(
            item
            for item in projection.authorities
            if item.authority_id in authority_ids
        ),
        page=page,
    )
