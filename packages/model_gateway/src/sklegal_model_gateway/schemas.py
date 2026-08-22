"""Pinned output schemas and provider output validation."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, StringConstraints, ValidationError

from .errors import RouteIntegrityError, SchemaValidationError
from .models import GatewayValue

CORPUS_SUMMARY_SCHEMA_ID = "sklegal.corpus-summary/v1"


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
        return dict(parsed.model_dump(mode="json"))

    @classmethod
    def default(cls) -> SchemaRegistry:
        registry = cls()
        registry.register(CORPUS_SUMMARY_SCHEMA_ID, CorpusSummaryProposalPayload)
        return registry
