"""Pinned output schemas and provider output validation."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from .errors import RouteIntegrityError, SchemaValidationError
from .models import GatewayValue

CORPUS_SUMMARY_SCHEMA_ID = "sklegal.corpus-summary/v1"
HTWC_PROPOSITIONS_SCHEMA_ID = "sklegal.htwc-propositions/v1"
HTWC_WORKFLOW_PACK_SCHEMA_ID = "sklegal.htwc-workflow-pack/v2"
HTWC_WORKFLOWS = frozenset(
    {
        "planning",
        "elements-evidence",
        "pleadings",
        "discovery",
        "motions",
        "trial",
        "post-judgment",
    }
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def schema_sha256(model: type[BaseModel]) -> str:
    """Content hash of the canonical JSON Schema for one payload model."""

    return hashlib.sha256(
        canonical_json(model.model_json_schema()).encode("utf-8")
    ).hexdigest()


class CorpusSummaryProposalPayload(GatewayValue):
    """Typed corpus summary proposal shared by the pinned initial routes."""

    summary: Annotated[str, StringConstraints(min_length=1, max_length=20000)]
    key_points: tuple[Annotated[str, StringConstraints(min_length=1)], ...]
    open_questions: tuple[Annotated[str, StringConstraints(min_length=1)], ...]
    confidence: Literal["low", "medium", "high"]


class HTWCProposition(GatewayValue):
    id: Annotated[str, StringConstraints(min_length=1)]
    workflow: Literal[
        "planning",
        "elements-evidence",
        "pleadings",
        "discovery",
        "motions",
        "trial",
        "post-judgment",
    ]
    text: Annotated[str, StringConstraints(min_length=1)]
    lane: Literal["course_instruction"]
    source_refs: tuple[Annotated[str, StringConstraints(min_length=1)], ...] = Field(
        min_length=1, max_length=1
    )
    uncertainty: Literal["low", "medium", "high"]
    counter_support: str
    human_review_required: Literal[True]


class HTWCUnresolvedRecord(GatewayValue):
    id: Annotated[str, StringConstraints(min_length=1)]
    description: Annotated[str, StringConstraints(min_length=1)]
    source_refs: tuple[Annotated[str, StringConstraints(min_length=1)], ...] = Field(
        min_length=1
    )
    resolution_state: Literal["unresolved"]
    human_review_required: Literal[True]


class HTWCPropositionsPayload(GatewayValue):
    schema_id: Literal["sklegal.htwc-propositions/v1"] = Field(
        alias="schema", serialization_alias="schema"
    )
    propositions: tuple[HTWCProposition, ...] = Field(min_length=28, max_length=28)
    contradictions: tuple[HTWCUnresolvedRecord, ...] = Field(min_length=1)
    unresolved_gaps: tuple[HTWCUnresolvedRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_identity_and_coverage(self) -> HTWCPropositionsPayload:
        ids = [item.id for item in self.propositions]
        refs = [item.source_refs[0] for item in self.propositions]
        if len(ids) != len(set(ids)):
            raise ValueError("proposition ids must be unique")
        if len(refs) != len(set(refs)):
            raise ValueError("proposition source spans must be unique")
        if {item.workflow for item in self.propositions} != HTWC_WORKFLOWS:
            raise ValueError("every HowToWinInCourt workflow must be represented")
        contradiction_ids = [item.id for item in self.contradictions]
        if len(contradiction_ids) != len(set(contradiction_ids)):
            raise ValueError("contradiction ids must be unique")
        return self


class HTWCWorkflowPackPayload(GatewayValue):
    schema_id: Literal["sklegal.htwc-workflow-pack/v2"] = Field(
        alias="schema", serialization_alias="schema"
    )
    card: Literal["a9e3c740"]
    source_manifest_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    qwen_run_receipt_sha256: Annotated[
        str, StringConstraints(pattern=r"^[0-9a-f]{64}$")
    ]
    qwen_run: dict[str, Any]
    rights: dict[str, Any]
    lanes: dict[str, str]
    jurisdiction_overlay: dict[str, Any]
    external_actions: dict[str, bool]
    model_boundary: dict[str, Any]
    propositions: tuple[HTWCProposition, ...] = Field(min_length=28, max_length=28)
    contradictions: tuple[HTWCUnresolvedRecord, ...] = Field(min_length=1)
    unresolved_gaps: tuple[HTWCUnresolvedRecord, ...] = Field(min_length=1)
    coverage: dict[str, Any]

    @model_validator(mode="after")
    def validate_consumer_boundary(self) -> HTWCWorkflowPackPayload:
        if self.rights != {
            "classification": "confidential_personal_use_research",
            "private_rights_isolation": True,
            "public_redistribution": False,
        }:
            raise ValueError("private source rights are not exact")
        ids = [item.id for item in self.propositions]
        refs = [item.source_refs[0] for item in self.propositions]
        if len(ids) != len(set(ids)) or len(refs) != len(set(refs)):
            raise ValueError("proposition identity or coverage is not unique")
        if {item.workflow for item in self.propositions} != HTWC_WORKFLOWS:
            raise ValueError("workflow coverage is incomplete")
        if self.coverage.get("proposition_referenced_spans") != 28:
            raise ValueError("proposition coverage is incomplete")
        if self.coverage.get("proposition_unreferenced_spans") != []:
            raise ValueError("a required proposition span is missing")
        # The consumer, rather than lifecycle state or links, owns these
        # structural joins. Keep the lane and action boundary closed here so
        # callers cannot silently widen the pack's meaning.
        expected_lanes = {
            "course_instruction": "source-derived proposals in this pack",
            "matter_facts_and_evidence": "not supplied",
            "official_authority": "not supplied; current jurisdiction-specific verification required",
            "model_inference": "Qwen proposal with per-item uncertainty",
            "human_decision": "not supplied; review is required",
        }
        if self.lanes != expected_lanes:
            raise ValueError("pack lanes are not the governed consumer lanes")
        if self.jurisdiction_overlay != {
            "included": False,
            "state": "unresolved",
            "rule": "No course proposition is controlling Authority or a Matter deadline.",
        }:
            raise ValueError("jurisdiction overlay is not closed")
        if self.external_actions != {
            "dispatch": False,
            "filing": False,
            "mailing": False,
            "service": False,
            "workflow_state_advance": False,
        }:
            raise ValueError("external action boundary is not closed")
        expected_boundary = {
            "corpus_semantics": "sk-qwen",
            "corpus_served_model": self.qwen_run.get("served_model"),
            "frontier_available": "astra",
            "frontier_invoked": False,
            "fable_5_1_state": "placeholder_metadata_only",
            "fable_5_1_eligible": False,
            "fable_5_1_dispatchable": False,
            "fable_5_1_fallback": False,
        }
        if self.model_boundary != expected_boundary:
            raise ValueError("model boundary is not governed")
        if self.coverage.get("represented_sources") != 28:
            raise ValueError("derived source coverage is incomplete")
        if self.coverage.get("covered_workflows") != sorted(HTWC_WORKFLOWS):
            raise ValueError("derived workflow coverage is incomplete")
        qwen = self.qwen_run
        sha_fields = (
            "request_sha256",
            "prompt_sha256",
            "raw_provider_response_sha256",
            "normalization_record_sha256",
            "dispatch_evidence_sha256",
            "transport_configuration_sha256",
        )
        if any(
            not isinstance(qwen.get(field), str)
            or len(qwen[field]) != 64
            or any(character not in "0123456789abcdef" for character in qwen[field])
            for field in sha_fields
        ):
            raise ValueError("Qwen execution provenance is incomplete")
        if (
            qwen.get("logical_route") != "qwen.semantic-proposal.v1"
            or qwen.get("transport_profile") != "chiap08.direct-qwen.v1"
            or qwen.get("requested_model") != "qwen3.8-27b-huihui-abliterated-q4_k_m"
            or not qwen.get("served_model_revision")
        ):
            raise ValueError("Qwen execution identity is not pinned")
        if any(self.external_actions.values()):
            raise ValueError("HowToWinInCourt pack cannot execute external actions")
        return self


class SchemaRegistry:
    """In-code registry binding schema identifiers to payload models."""

    def __init__(self) -> None:
        self._schemas: dict[str, type[BaseModel]] = {}

    def register(self, schema_id: str, model: type[BaseModel]) -> None:
        if not schema_id:
            raise ValueError("schema id cannot be empty")
        self._schemas[schema_id] = model

    def require(self, schema_id: str) -> type[BaseModel]:
        model = self._schemas.get(schema_id)
        if model is None:
            raise RouteIntegrityError(f"output schema is not registered: {schema_id}")
        return model

    def json_schema(self, schema_id: str) -> dict[str, Any]:
        return dict(self.require(schema_id).model_json_schema())

    def validate(self, schema_id: str, raw_text: str) -> dict[str, Any]:
        """Parse and validate provider output; never returns partial data."""

        model = self.require(schema_id)
        try:
            data: Any = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise SchemaValidationError("provider output is not valid JSON") from exc
        if not isinstance(data, dict):
            raise SchemaValidationError("provider output must be a JSON object")
        try:
            parsed = model.model_validate(data)
        except ValidationError as exc:
            raise SchemaValidationError(
                "provider output failed the pinned output schema"
            ) from exc
        return dict(parsed.model_dump(mode="json", by_alias=True))

    @classmethod
    def default(cls) -> SchemaRegistry:
        registry = cls()
        registry.register(CORPUS_SUMMARY_SCHEMA_ID, CorpusSummaryProposalPayload)
        registry.register(HTWC_PROPOSITIONS_SCHEMA_ID, HTWCPropositionsPayload)
        registry.register(HTWC_WORKFLOW_PACK_SCHEMA_ID, HTWCWorkflowPackPayload)
        return registry
