"""Conservative party normalization and exact collision discovery."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from .models import (
    ConflictCheckResult,
    ConflictDisposition,
    ConflictMatch,
    KnownPartyAssociation,
    PartyCandidate,
    PartyRelationship,
    require_utc,
)

_COMPANY_SUFFIXES = frozenset(
    {
        "co",
        "company",
        "corp",
        "corporation",
        "inc",
        "incorporated",
        "llc",
        "llp",
        "lp",
        "limited",
        "pllc",
    }
)
_PUNCTUATION = re.compile(r"[^a-z0-9]+")


def normalize_party_name(display_name: str, party_kind: str) -> str:
    """Return a narrow exact-match key without making a fuzzy identity claim."""

    if not isinstance(display_name, str) or not display_name.strip():
        raise ValueError("party display name must be nonempty")
    decomposed = unicodedata.normalize("NFKD", display_name)
    ascii_text = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    tokens = _PUNCTUATION.sub(" ", ascii_text.casefold()).split()
    if len(tokens) > 1 and all(len(token) == 1 for token in tokens):
        tokens = ["".join(tokens)]
    elif party_kind == "company":
        prefix: list[str] = []
        while tokens and len(tokens[0]) == 1:
            prefix.append(tokens.pop(0))
        if len(prefix) > 1:
            tokens.insert(0, "".join(prefix))
        elif prefix:
            tokens.insert(0, prefix[0])
    if party_kind in {"company", "trust", "estate"}:
        while tokens and tokens[-1] in _COMPANY_SUFFIXES:
            tokens.pop()
    if not tokens:
        raise ValueError("party display name has no normalized identity material")
    return " ".join(tokens)


def _identity_digest(display_name: str, party_kind: str) -> str:
    canonical = (
        f"sklegal-party/v1\x00{party_kind}\x00"
        f"{normalize_party_name(display_name, party_kind)}"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_adverse_pair(left: PartyRelationship, right: PartyRelationship) -> bool:
    return {left, right} == {
        PartyRelationship.CLIENT,
        PartyRelationship.ADVERSE_PARTY,
    }


class ConflictService:
    """Discover exact normalized adverse-party collisions without deciding them."""

    def check(
        self,
        *,
        check_id: UUID,
        tenant_id: UUID,
        matter_id: UUID,
        candidates: Sequence[PartyCandidate],
        known_associations: Sequence[KnownPartyAssociation],
        checked_at: datetime,
    ) -> ConflictCheckResult:
        require_utc(checked_at)
        candidate_ids = tuple(candidate.party_id for candidate in candidates)
        if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("conflict check requires unique candidate parties")
        if any(
            candidate.tenant_id != tenant_id or candidate.matter_id != matter_id
            for candidate in candidates
        ):
            raise ValueError("candidate party crosses conflict-check scope")
        if any(item.tenant_id != tenant_id for item in known_associations):
            raise ValueError("known party association crosses tenant scope")

        matches: list[ConflictMatch] = []
        for candidate in candidates:
            candidate_digest = _identity_digest(
                candidate.display_name, candidate.party_kind
            )
            for association in known_associations:
                if not association.active or association.matter_id == matter_id:
                    continue
                if candidate.party_kind != association.party_kind:
                    continue
                if not _is_adverse_pair(
                    candidate.proposed_relationship, association.relationship
                ):
                    continue
                if candidate_digest != _identity_digest(
                    association.display_name, association.party_kind
                ):
                    continue
                matches.append(
                    ConflictMatch(
                        candidate_party_id=candidate.party_id,
                        association_id=association.association_id,
                        existing_matter_id=association.matter_id,
                        normalized_identity_digest=candidate_digest,
                    )
                )
        return ConflictCheckResult(
            check_id=check_id,
            tenant_id=tenant_id,
            matter_id=matter_id,
            candidate_party_ids=candidate_ids,
            matches=tuple(matches),
            recommended_disposition=(
                ConflictDisposition.HOLD if matches else ConflictDisposition.CLEAR
            ),
            checked_at=checked_at,
        )
