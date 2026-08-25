"""Provider-neutral, read-only secondary corpus review route.

The route carries logical attribution and accepts a transport adapter. The live
adapter is deliberately small and only reads an endpoint named by an
environment variable. It writes no workflow state and returns typed proposals.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

LOGICAL_ROUTE = "sklegal.local-corpus-secondary-review"
ENDPOINT_ENV = "SKLEGAL_QWEN_REVIEW_ENDPOINT"
EXPECTED_UNCERTAINTIES = (
    "EIA-STYLE-2020 currentness review required",
    "NIJ-STYLE-2022 currentness discrepancy requires review",
    "DOI-CORR-2014 currentness review required",
    "CA7-TYPOGRAPHY currentness review required",
    "EPA-STYLE-2009 supersession unknown",
)
DECISIONS = {"upheld", "challenged", "insufficient_evidence"}


class ReviewRouteError(ValueError):
    """A review route failed closed."""


@dataclass(frozen=True, slots=True)
class ReviewRoute:
    logical_route: str
    transport: str
    endpoint_env: str
    gateway_revision: str
    backend: str
    requested_model: str
    served_model: str
    timeout_seconds: float = 120.0


@dataclass(frozen=True, slots=True)
class ReviewResponse:
    output: dict[str, Any]
    served_model: str
    backend: str


class ReviewTransport(Protocol):
    def complete(
        self, *, route: ReviewRoute, prompt: str, schema: Mapping[str, Any]
    ) -> ReviewResponse: ...


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def schema_hash(schema: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(schema).encode())


def load_route(path: Path, *, gateway_revision: str) -> ReviewRoute:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("logical_route") != LOGICAL_ROUTE:
        raise ReviewRouteError("logical review route is not pinned")
    return ReviewRoute(
        logical_route=payload["logical_route"],
        transport=payload["transport"],
        endpoint_env=payload["endpoint_env"],
        gateway_revision=gateway_revision,
        backend=payload["backend"],
        requested_model=payload["requested_model"],
        served_model=payload["served_model"],
        timeout_seconds=float(payload.get("timeout_seconds", 120)),
    )


class LocalQwenOpenAITransport:
    """OpenAI-compatible local transport with no credential support."""

    def complete(
        self, *, route: ReviewRoute, prompt: str, schema: Mapping[str, Any]
    ) -> ReviewResponse:
        endpoint = os.environ.get(route.endpoint_env)
        if not endpoint:
            raise ReviewRouteError("review endpoint environment reference is unset")
        url = endpoint.rstrip("/") + "/v1/chat/completions"
        body = {
            "model": route.requested_model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return only one JSON object matching the supplied review schema.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 16384,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(body, separators=(",", ":")).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=route.timeout_seconds
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ReviewRouteError("local review transport failed") from exc
        try:
            content = payload["choices"][0]["message"]["content"]
            served_model = payload["model"]
            output = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ReviewRouteError("local review response was malformed") from exc
        if not isinstance(output, dict) or served_model != route.served_model:
            raise ReviewRouteError("served-model attribution did not match the route")
        return ReviewResponse(
            output=output, served_model=served_model, backend=route.backend
        )


def validate_verdict(
    output: Mapping[str, Any], *, proposition_ids: Sequence[str]
) -> dict[str, Any]:
    verdicts = output.get("verdicts")
    uncertainties = output.get("uncertainties_preserved")
    if not isinstance(verdicts, list) or not isinstance(uncertainties, list):
        raise ReviewRouteError("review output omitted verdicts or uncertainties")
    if set(uncertainties) != set(EXPECTED_UNCERTAINTIES) or len(uncertainties) != 5:
        raise ReviewRouteError("the five required uncertainties were not preserved")
    if len(verdicts) != len(proposition_ids):
        raise ReviewRouteError("review output does not cover all propositions")
    seen: set[str] = set()
    for verdict in verdicts:
        if not isinstance(verdict, dict):
            raise ReviewRouteError("review verdict is not an object")
        proposition_id = verdict.get("proposition_id")
        if proposition_id not in proposition_ids or proposition_id in seen:
            raise ReviewRouteError("review proposition coverage is invalid")
        if verdict.get("decision") not in DECISIONS:
            raise ReviewRouteError("review decision is invalid")
        if not isinstance(verdict.get("reasoning"), str) or not verdict["reasoning"]:
            raise ReviewRouteError("review reasoning is missing")
        if not isinstance(verdict.get("citations"), list) or not verdict["citations"]:
            raise ReviewRouteError("review citations are missing")
        seen.add(proposition_id)
    if seen != set(proposition_ids):
        raise ReviewRouteError("review proposition coverage is incomplete")
    if output.get("overall") not in {"pass", "pass_with_uncertainty"}:
        raise ReviewRouteError("review did not pass")
    return dict(output)


def review_challenge(
    challenge_path: Path,
    *,
    route: ReviewRoute,
    transport: ReviewTransport,
    evidence: str = "",
) -> tuple[ReviewResponse, str, str]:
    raw = challenge_path.read_bytes()
    challenge = json.loads(raw)
    schema = challenge["output_schema"]
    proposition_ids = [item["id"] for item in challenge["propositions"]]
    prompt_bytes = raw + (
        b"\n\nPINNED REVIEW EVIDENCE:\n" + evidence.encode() if evidence else b""
    )
    prompt_bytes += (
        b"\n\nMANDATORY OUTPUT CONSTRAINT: uncertainties_preserved MUST be exactly this "
        b'JSON array in this order: ["EIA-STYLE-2020 currentness review required",'
        b'"NIJ-STYLE-2022 currentness discrepancy requires review",'
        b'"DOI-CORR-2014 currentness review required",'
        b'"CA7-TYPOGRAPHY currentness review required",'
        b'"EPA-STYLE-2009 supersession unknown"]. Use these exact strings, no '
        b"paraphrases. Return only JSON."
    )
    response = transport.complete(
        route=route,
        prompt=prompt_bytes.decode("utf-8"),
        schema=schema,
    )
    if response.served_model != route.served_model:
        raise ReviewRouteError("served-model attribution did not match the route")
    response = ReviewResponse(
        output=validate_verdict(response.output, proposition_ids=proposition_ids),
        served_model=response.served_model,
        backend=response.backend,
    )
    return response, sha256_bytes(prompt_bytes), schema_hash(schema)


def write_qualification(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def rollback_qualification(path: Path) -> None:
    """Remove only a generated qualification file; route state is untouched."""

    if path.exists():
        path.unlink()
