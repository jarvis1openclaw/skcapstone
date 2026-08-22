"""Claim-grounded drafting: sentence bindings, rendering, compare, invalidation.

Pure domain helpers for the matter Documents tab editor (SKL-S4-04). No I/O
and no model calls: models produce typed proposals, and these helpers reduce
typed records into editor projections and readiness decisions.

Three contracts live here:

1. Sentence grounding. A draft is split into deterministic sentences; every
   factual sentence binds to exactly one claim ledger entry through a
   ``SentenceGrounding`` pinned to the exact work product version triple. A
   sentence with no applicable binding renders an ungrounded warning, a
   sentence bound to a withdrawn or absent claim renders a warning, and a
   sentence carrying a bracketed unknown defers grounding until the unknown
   is resolved. Grounding defects block ``DRAFT_READY`` alongside unresolved
   unknowns.
2. Editor rendering. ``render_draft_segments`` turns draft content into
   ordered text and unknown segments so the editor shows bracketed unknowns
   as first-class chips, and ``compare_draft_versions`` produces the
   sentence-level version compare rows for the v(n-1) to v(n) diff preview.
3. Changed-after-approval invalidation. ``invalidate_approval_after_edit``
   applies the deterministic consequence of editing an approved work
   product: the approval can no longer name the changed bytes, so the work
   product resets to in review with cleared gate evidence and the approved
   version is superseded, forcing new validation and approval.
"""

from __future__ import annotations

import difflib
import hashlib
from datetime import datetime
from typing import Annotated, ClassVar, Literal

from pydantic import Field, model_validator

from .base import MatterEntity
from .drafting import (
    UNKNOWN_PLACEHOLDER_PATTERN,
    UnknownOccurrence,
    assert_approval_matches_version,
    supersede_version,
)
from .entities.claim_ledger import LedgerClaim
from .entities.work_product import (
    WorkProduct,
    WorkProductVersion,
)
from .exceptions import DomainTransitionError
from .states import (
    DraftGroundingStatus,
    LedgerClaimStatus,
    WorkProductStatus,
    WorkProductVersionStatus,
)
from .value_objects import (
    ArtifactBinding,
    DomainId,
    FrozenValue,
    NonEmptyText,
    Sha256,
    ShortText,
)

_SENTENCE_TERMINATORS = ".!?"


def normalize_sentence_text(text: str) -> str:
    """Collapse all whitespace runs so equal sentences compare equal."""

    if not isinstance(text, str):
        raise TypeError("sentence text must be text")
    return " ".join(text.split())


def sentence_key(text: str) -> Sha256:
    """Derive the stable identity key of one sentence.

    The key is the SHA-256 of the whitespace-normalized sentence text, so an
    unchanged sentence keeps its binding and its compare row even when
    surrounding content shifts position.
    """

    digest = hashlib.sha256(normalize_sentence_text(text).encode("utf-8")).hexdigest()
    return digest  # type: ignore[return-value]


class DraftSentence(FrozenValue):
    """One deterministic sentence of a draft body.

    ``start`` and ``end`` are half-open offsets into the original content and
    always satisfy ``content[start:end].strip() == text``.
    """

    key: Sha256
    text: NonEmptyText
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def validate_span(self) -> DraftSentence:
        if self.end <= self.start:
            raise ValueError("sentence span end must be later than start")
        if self.text != self.text.strip():
            raise ValueError("sentence text must carry no outer whitespace")
        return self

    def contains_unknown(self) -> bool:
        """Whether this sentence carries at least one bracketed unknown."""

        return UNKNOWN_PLACEHOLDER_PATTERN.search(self.text) is not None


def _sentence_cuts(content: str) -> list[int]:
    """Cut offsets where one sentence may end, skipping unknown markers."""

    cuts = [0]
    length = len(content)
    index = 0
    while index < length:
        char = content[index]
        if char == "[":
            marker = UNKNOWN_PLACEHOLDER_PATTERN.match(content, index)
            if marker is not None:
                index = marker.end()
                continue
        if char in _SENTENCE_TERMINATORS:
            following = index + 1
            if following == length or content[following].isspace():
                cuts.append(following)
        elif char == "\n":
            cuts.append(index)
        index += 1
    if cuts[-1] != length:
        cuts.append(length)
    return cuts


def split_draft_sentences(content: str) -> tuple[DraftSentence, ...]:
    """Split draft content into deterministic sentences in document order.

    A sentence ends after ``.``, ``!``, or ``?`` followed by whitespace or
    the end of the content, or at a newline, so headings and list lines stay
    separate segments. Terminators inside bracketed unknown markers never
    end a sentence. Spans that normalize to empty are skipped. Every
    returned sentence satisfies ``content[start:end].strip() == text``.
    """

    if not isinstance(content, str):
        raise TypeError("content must be text")
    sentences: list[DraftSentence] = []
    cuts = _sentence_cuts(content)
    for begin, end in zip(cuts, cuts[1:], strict=False):
        span = content[begin:end]
        text = span.strip()
        if not text:
            continue
        start = begin + (len(span) - len(span.lstrip()))
        sentences.append(
            DraftSentence(
                key=sentence_key(text),
                text=text,
                start=start,
                end=start + len(text),
            )
        )
    return tuple(sentences)


class SentenceGrounding(MatterEntity):
    """One binding of a draft sentence to a claim ledger entry.

    The binding is pinned to the exact work product version triple (version
    id, version number, content digest) inside one tenant and matter, so an
    edit that changes the draft cannot silently reuse a grounding recorded
    for the previous bytes.
    """

    tenant_id: DomainId
    matter_id: DomainId
    version_binding: ArtifactBinding
    sentence_key: Sha256
    claim_id: DomainId

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = MatterEntity.IMMUTABLE_FIELDS | {
        "version_binding",
        "sentence_key",
        "claim_id",
    }

    def binds_version(self, version: WorkProductVersion) -> bool:
        """Whether this grounding names the exact version and scope."""

        binding = self.version_binding
        return (
            self.tenant_id == version.tenant_id
            and self.matter_id == version.matter_id
            and binding.artifact_id == version.id
            and binding.artifact_version == version.version_number
            and binding.content_sha256 == version.content_sha256
        )


def applicable_groundings(
    version: WorkProductVersion,
    groundings: tuple[SentenceGrounding, ...],
) -> tuple[SentenceGrounding, ...]:
    """Return only the groundings pinned to this exact version and scope."""

    return tuple(
        grounding for grounding in groundings if grounding.binds_version(version)
    )


class SentenceGroundingEvaluation(FrozenValue):
    """The grounding state of one sentence, with its editor warning.

    Warnings are first-class output: an ungrounded, withdrawn, or missing
    claim state always carries its exact warning text so the editor and the
    audit trail show the defect instead of hiding it.
    """

    sentence: DraftSentence
    status: DraftGroundingStatus
    grounding: SentenceGrounding | None = None
    claim_status: LedgerClaimStatus | None = None
    warning: ShortText | None = None

    @model_validator(mode="after")
    def validate_warning_presence(self) -> SentenceGroundingEvaluation:
        warning_states = {
            DraftGroundingStatus.UNGROUNDED,
            DraftGroundingStatus.CLAIM_WITHDRAWN,
            DraftGroundingStatus.CLAIM_MISSING,
            DraftGroundingStatus.DEFERRED_UNKNOWN,
        }
        if self.status in warning_states and self.warning is None:
            raise ValueError("a non-grounded sentence carries its warning text")
        if self.status is DraftGroundingStatus.GROUNDED and self.warning is not None:
            raise ValueError("a grounded sentence carries no warning")
        return self

    @property
    def is_defect(self) -> bool:
        """Whether this evaluation blocks DRAFT_READY."""

        return self.status in {
            DraftGroundingStatus.UNGROUNDED,
            DraftGroundingStatus.CLAIM_WITHDRAWN,
            DraftGroundingStatus.CLAIM_MISSING,
        }


def _ledger_by_id(
    ledger_claims: tuple[LedgerClaim, ...],
) -> dict[DomainId, LedgerClaim]:
    ledger: dict[DomainId, LedgerClaim] = {}
    for claim in ledger_claims:
        if claim.id in ledger:
            raise ValueError("duplicate ledger claim records for one claim id")
        ledger[claim.id] = claim
    return ledger


def evaluate_sentence_grounding(
    *,
    version: WorkProductVersion,
    content: str,
    groundings: tuple[SentenceGrounding, ...],
    ledger_claims: tuple[LedgerClaim, ...],
) -> tuple[SentenceGroundingEvaluation, ...]:
    """Evaluate every sentence of one exact draft version, fail closed.

    A sentence is grounded when one grounding pinned to this exact version
    binds it to a ledger claim in the same tenant and matter whose status is
    not withdrawn. A sentence carrying a bracketed unknown defers grounding
    until the unknown is resolved; every other unbound sentence renders an
    ungrounded warning. Two applicable groundings for one sentence must name
    the same claim, otherwise the input is contradictory and rejected.
    """

    ledger = _ledger_by_id(ledger_claims)
    bound: dict[Sha256, SentenceGrounding] = {}
    for grounding in applicable_groundings(version, groundings):
        existing = bound.get(grounding.sentence_key)
        if existing is not None and existing.claim_id != grounding.claim_id:
            raise ValueError(
                "one sentence carries contradictory claim groundings: "
                f"{existing.claim_id} and {grounding.claim_id}"
            )
        if existing is None:
            bound[grounding.sentence_key] = grounding

    evaluations: list[SentenceGroundingEvaluation] = []
    for sentence in split_draft_sentences(content):
        # Headings and list labels without terminal punctuation are structural
        # draft text, not factual sentences that require a claim binding.
        if sentence.text[-1] not in _SENTENCE_TERMINATORS:
            continue
        grounding = bound.get(sentence.key)
        if grounding is None:
            if sentence.contains_unknown():
                evaluations.append(
                    SentenceGroundingEvaluation(
                        sentence=sentence,
                        status=DraftGroundingStatus.DEFERRED_UNKNOWN,
                        warning=(
                            "Grounding deferred until the bracketed unknown is "
                            "resolved."
                        ),
                    )
                )
            else:
                evaluations.append(
                    SentenceGroundingEvaluation(
                        sentence=sentence,
                        status=DraftGroundingStatus.UNGROUNDED,
                        warning=(
                            "Factual sentence is not grounded in a claim ledger entry."
                        ),
                    )
                )
            continue
        claim = ledger.get(grounding.claim_id)
        if claim is None or (
            claim.tenant_id != version.tenant_id or claim.matter_id != version.matter_id
        ):
            evaluations.append(
                SentenceGroundingEvaluation(
                    sentence=sentence,
                    status=DraftGroundingStatus.CLAIM_MISSING,
                    grounding=grounding,
                    warning=(
                        "Bound claim is not present in the provided claim ledger."
                    ),
                )
            )
        elif claim.status is LedgerClaimStatus.WITHDRAWN:
            evaluations.append(
                SentenceGroundingEvaluation(
                    sentence=sentence,
                    status=DraftGroundingStatus.CLAIM_WITHDRAWN,
                    grounding=grounding,
                    claim_status=claim.status,
                    warning="Bound claim was withdrawn from the claim ledger.",
                )
            )
        else:
            evaluations.append(
                SentenceGroundingEvaluation(
                    sentence=sentence,
                    status=DraftGroundingStatus.GROUNDED,
                    grounding=grounding,
                    claim_status=claim.status,
                )
            )
    return tuple(evaluations)


def grounding_defects(
    evaluations: tuple[SentenceGroundingEvaluation, ...],
) -> tuple[SentenceGroundingEvaluation, ...]:
    """Return the evaluations that block DRAFT_READY for the draft."""

    return tuple(evaluation for evaluation in evaluations if evaluation.is_defect)


def stale_groundings(
    *,
    version: WorkProductVersion,
    content: str,
    groundings: tuple[SentenceGrounding, ...],
) -> tuple[SentenceGrounding, ...]:
    """Groundings pinned to this version whose sentence no longer exists.

    An edit can remove a sentence while leaving its binding behind; the
    stale binding still claims provenance over bytes that are gone, so it is
    surfaced instead of silently dropped.
    """

    present = {sentence.key for sentence in split_draft_sentences(content)}
    return tuple(
        grounding
        for grounding in applicable_groundings(version, groundings)
        if grounding.sentence_key not in present
    )


def grounding_claim_ids(
    *,
    version: WorkProductVersion,
    content: str,
    groundings: tuple[SentenceGrounding, ...],
) -> tuple[DomainId, ...]:
    """Deduplicated claim ids bound by this exact draft version.

    Feeds ``evaluate_draft_ready_gate`` so the gate checks CLAIM_READY for
    exactly the claims the current draft bytes rely on.
    """

    present = {sentence.key for sentence in split_draft_sentences(content)}
    claim_ids: list[DomainId] = []
    for grounding in applicable_groundings(version, groundings):
        if grounding.sentence_key in present and grounding.claim_id not in claim_ids:
            claim_ids.append(grounding.claim_id)
    return tuple(claim_ids)


def assert_draft_grounded(
    *,
    version: WorkProductVersion,
    content: str,
    groundings: tuple[SentenceGrounding, ...],
    ledger_claims: tuple[LedgerClaim, ...],
) -> None:
    """Fail closed when any grounding defect or stale binding remains."""

    evaluations = evaluate_sentence_grounding(
        version=version,
        content=content,
        groundings=groundings,
        ledger_claims=ledger_claims,
    )
    defects = grounding_defects(evaluations)
    stale = stale_groundings(version=version, content=content, groundings=groundings)
    if defects or stale:
        parts: list[str] = []
        if defects:
            keys = ", ".join(evaluation.sentence.key[:12] for evaluation in defects)
            parts.append(f"grounding defects on sentences: {keys}")
        if stale:
            stale_keys = ", ".join(grounding.sentence_key[:12] for grounding in stale)
            parts.append(f"stale groundings: {stale_keys}")
        raise DomainTransitionError("; ".join(parts))


class DraftSegment(FrozenValue):
    """One rendered run of the editor body.

    ``text`` runs carry plain draft text; ``unknown`` runs carry one located
    bracketed unknown whose text is the exact marker.
    """

    kind: Literal["text", "unknown"]
    text: NonEmptyText
    unknown: UnknownOccurrence | None = None

    @model_validator(mode="after")
    def validate_kind(self) -> DraftSegment:
        if self.kind == "unknown":
            if self.unknown is None:
                raise ValueError("an unknown segment carries its occurrence")
            if self.text != self.unknown.marker:
                raise ValueError("an unknown segment renders its exact marker")
        elif self.unknown is not None:
            raise ValueError("a text segment carries no unknown occurrence")
        return self


def render_draft_segments(content: str) -> tuple[DraftSegment, ...]:
    """Render draft content as ordered text and bracketed-unknown segments.

    The editor renders unknown segments as first-class chips so bracketed
    unknowns stay visible exactly where they occur; plain square brackets in
    legal text never become chips because only the explicit ``[?key]``
    marker syntax matches.
    """

    if not isinstance(content, str):
        raise TypeError("content must be text")
    segments: list[DraftSegment] = []
    cursor = 0
    for match in UNKNOWN_PLACEHOLDER_PATTERN.finditer(content):
        if match.start() > cursor:
            run = content[cursor : match.start()]
            segments.append(DraftSegment(kind="text", text=run))
            cursor = match.start()
        hint = match.group("hint")
        occurrence = UnknownOccurrence(
            placeholder_key=match.group("key"),
            hint=hint.strip() if hint is not None else None,
            start=match.start(),
            end=match.end(),
        )
        segments.append(
            DraftSegment(kind="unknown", text=occurrence.marker, unknown=occurrence)
        )
        cursor = match.end()
    if cursor < len(content):
        run = content[cursor:]
        segments.append(DraftSegment(kind="text", text=run))
    return tuple(segments)


class DraftCompareRow(FrozenValue):
    """One row of the version compare view.

    ``unchanged`` rows carry the current sentence, ``added`` rows the
    current sentence, and ``removed`` rows the previous sentence, so each
    row always carries its exact source text.
    """

    change: Literal["unchanged", "added", "removed"]
    sentence: DraftSentence

    @property
    def previous_text(self) -> str | None:
        return self.sentence.text if self.change == "removed" else None

    @property
    def current_text(self) -> str | None:
        return self.sentence.text if self.change != "removed" else None


def compare_draft_versions(
    previous_content: str, current_content: str
) -> tuple[DraftCompareRow, ...]:
    """Build the sentence-level version compare rows, in reading order.

    Sentences are matched by their stable identity key, not by position, so
    an insertion at the top of the document does not mark every later
    sentence changed. A replaced span emits its removed rows before its
    added rows.
    """

    previous = split_draft_sentences(previous_content)
    current = split_draft_sentences(current_content)
    matcher = difflib.SequenceMatcher(
        a=[sentence.key for sentence in previous],
        b=[sentence.key for sentence in current],
        autojunk=False,
    )
    rows: list[DraftCompareRow] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            rows.extend(
                DraftCompareRow(change="unchanged", sentence=sentence)
                for sentence in current[j1:j2]
            )
        else:
            rows.extend(
                DraftCompareRow(change="removed", sentence=sentence)
                for sentence in previous[i1:i2]
            )
            rows.extend(
                DraftCompareRow(change="added", sentence=sentence)
                for sentence in current[j1:j2]
            )
    return tuple(rows)


def invalidate_approval_after_edit(
    *,
    work_product: WorkProduct,
    approved_version: WorkProductVersion,
    approval_subject: ArtifactBinding,
    successor: WorkProductVersion,
    at: datetime,
) -> tuple[WorkProduct, WorkProductVersion]:
    """Apply the changed-after-approval invalidation for one edited draft.

    Editing an approved work product requires a successor draft version
    with changed bytes. The approval names the approved exact version and
    can never cover the successor, so this helper resets the work product
    to in review with cleared validation and approval evidence, points the
    current version at the successor, and supersedes the approved version.
    The next validation and approval must be earned again from scratch.

    Fails closed when the work product is not approved, the approval never
    matched the approved version bytes, or the successor is not the next
    changed draft version of the same chain.
    """

    if work_product.status is not WorkProductStatus.APPROVED:
        raise DomainTransitionError(
            "only an approved work product has an approval to invalidate"
        )
    if work_product.current_version_id != approved_version.id:
        raise DomainTransitionError(
            "invalidation applies to the current approved version"
        )
    assert_approval_matches_version(approval_subject, approved_version)
    if (
        successor.tenant_id != approved_version.tenant_id
        or successor.matter_id != approved_version.matter_id
        or successor.work_product_id != approved_version.work_product_id
    ):
        raise DomainTransitionError("successor version must stay in the same chain")
    if successor.version_number != approved_version.version_number + 1:
        raise DomainTransitionError(
            "successor version must be the next version of the chain"
        )
    if successor.content_sha256 == approved_version.content_sha256:
        raise DomainTransitionError(
            "an edit after approval must change the exact content digest"
        )
    if successor.status is not WorkProductVersionStatus.DRAFT:
        raise DomainTransitionError("successor version must be a draft")
    reset = work_product.transition_to(
        WorkProductStatus.IN_REVIEW,
        at=at,
        validation_result_id=None,
        approval_id=None,
        current_version_id=successor.id,
    )
    superseded = supersede_version(approved_version, at=at)
    return reset, superseded


def approval_invalidation_state(
    *,
    approval_subject: ArtifactBinding | None,
    current_version: WorkProductVersion,
) -> bool:
    """Whether a recorded approval no longer names the current draft bytes.

    Read-side detection for the editor: when an approval exists but its
    binding does not match the current version triple, the edit happened
    after the approval and the approval is invalidated. Absence of an
    approval is not an invalidation.
    """

    if approval_subject is None:
        return False
    return (
        approval_subject.artifact_id != current_version.id
        or approval_subject.artifact_version != current_version.version_number
        or approval_subject.content_sha256 != current_version.content_sha256
    )


__all__ = [
    "DraftCompareRow",
    "DraftSegment",
    "DraftSentence",
    "SentenceGrounding",
    "SentenceGroundingEvaluation",
    "applicable_groundings",
    "approval_invalidation_state",
    "assert_draft_grounded",
    "compare_draft_versions",
    "evaluate_sentence_grounding",
    "grounding_claim_ids",
    "grounding_defects",
    "invalidate_approval_after_edit",
    "normalize_sentence_text",
    "render_draft_segments",
    "sentence_key",
    "split_draft_sentences",
    "stale_groundings",
]
