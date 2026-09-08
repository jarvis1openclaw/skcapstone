#!/usr/bin/env python3
"""Capture the one governed local Qwen derivation authorized by a9e3c740."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sklegal_model_gateway import HTWC_PROPOSITIONS_SCHEMA_ID, SchemaRegistry

ROOT = Path(__file__).resolve().parents[1]
CORPUS = Path(os.environ.get("HAMMERTIME_ROOT", ROOT.parent / "hammerTime"))
OUT = ROOT / "config/processes/howtowinincourt"
ENDPOINT_ENV = "SKLEGAL_QWEN_DIRECT_ENDPOINT"
MODEL = "qwen3.8-27b-huihui-abliterated-q4_k_m"
ROUTES = {
    "planning": (
        "media/transcripts/qstart-here-whiteboard-a8638878baed.md",
        "normalized/course-pages/juris-dictionary-definitions-a-978784982b15.md",
        "normalized/course-pages/maxims-cc947dc07569.md",
        "normalized/course-pages/natural-law-14a29724ce45.md",
        "media/transcripts/audio-classroom-2-e6de13f0f411.md",
        "normalized/course-pages/easy-guide-cf12d3e93439.md",
        "normalized/course-pages/key-to-winning-be5a76c87f69.md",
        "normalized/course-pages/planning-c977606e03a2.md",
    ),
    "elements-evidence": (
        "normalized/course-pages/contracts-c7c76d7dd588.md",
        "normalized/course-pages/qcontracts-de49db88f796.md",
        "normalized/course-pages/evidence-34db47fcb0d2.md",
    ),
    "pleadings": (
        "media/transcripts/audio-classroom-7-ac41faf1ba08.md",
        "normalized/course-pages/answers-299e26ac4b83.md",
        "normalized/course-pages/complaints-5813abe98a50.md",
    ),
    "discovery": (
        "media/transcripts/audio-classroom-11-731e4bd815c5.md",
        "media/transcripts/floridasupremecourt-0c3187445682.md",
        "normalized/course-pages/compelling-5e637c126a9b.md",
        "normalized/course-pages/discovery-95223cc4300f.md",
    ),
    "motions": (
        "media/transcripts/audio-classroom-9-8cd21ee06f59.md",
        "normalized/course-pages/motions-ecb60bd2268a.md",
    ),
    "trial": (
        "media/transcripts/audio-classroom-18-e85281746a6c.md",
        "normalized/course-pages/judges-49b0a7b4e737.md",
        "normalized/course-pages/objections-fa90ff67967a.md",
        "normalized/course-pages/trial-f135bffef111.md",
    ),
    "post-judgment": (
        "media/transcripts/audio-classroom-21-4b03e4cc7605.md",
        "media/transcripts/audio-classroom-22-167431b2f5c1.md",
        "normalized/course-pages/appeals-7c3bc8f2c9d7.md",
        "normalized/course-pages/collecting-9d74986765fa.md",
    ),
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode()


def main() -> None:
    endpoint = os.environ.get(ENDPOINT_ENV)
    if not endpoint:
        raise RuntimeError(f"{ENDPOINT_ENV} must resolve the approved direct profile")
    endpoint = endpoint.rstrip("/") + "/v1/chat/completions"
    sources: list[dict[str, Any]] = []
    blocks: list[str] = []
    trace: list[dict[str, Any]] = []
    for workflow, paths in ROUTES.items():
        for index, suffix in enumerate(paths, 1):
            relative = f"private-corpus/how-to-win-in-court/{suffix}"
            raw = (CORPUS / relative).read_bytes()
            lines = raw.decode("utf-8").splitlines()
            start = 30 if "/transcripts/" in relative else 34
            end = min(len(lines), start + 46)
            text = "\n".join(lines[start - 1 : end])
            source_id = f"{workflow}-{index}"
            span_id = f"{source_id}:L{start}-L{end}"
            source = {
                "id": source_id,
                "path": relative,
                "sha256": sha(raw),
                "version": f"sha256:{sha(raw)}",
                "workflow": workflow,
                "spans": [
                    {
                        "id": span_id,
                        "start_line": start,
                        "end_line": end,
                        "sha256": sha(text.encode()),
                    }
                ],
            }
            sources.append(source)
            trace.append(
                {
                    "source_id": source_id,
                    "source_path": relative,
                    "source_sha256": sha(raw),
                    "span_id": span_id,
                    "start_line": start,
                    "end_line": end,
                    "span_sha256": sha(text.encode()),
                }
            )
            blocks.append(f"WORKFLOW {workflow} SPAN {span_id}\n{text}")

    source_manifest = {
        "schema": "sklegal.htwc-source-manifest/v1",
        "corpus_release": "how-to-win-in-court/private-source-files-by-content-hash/2026-09-08",
        "rights": {
            "classification": "confidential_personal_use_research",
            "private_rights_isolation": True,
            "public_redistribution": False,
        },
        "sources": sources,
    }
    retrieval_trace = {
        "schema": "sklegal.htwc-retrieval-trace/v1",
        "card": "a9e3c740",
        "sources": trace,
    }
    schema_bytes = canonical(
        json.loads((OUT / "workflow-pack.schema.json").read_text())
    )
    server_attestation = json.loads(
        (OUT / "local-qwen-server-attestation.v1.json").read_text()
    )
    instruction = """Analyze only the supplied 28 authorized HowToWinInCourt spans. Return JSON only with keys propositions, contradictions, unresolved_gaps. Produce exactly one material proposition for each supplied span, exactly 28 propositions total. Use every supplied span ID exactly once as the sole source_refs entry of its proposition. Each proposition requires id, workflow, text, lane equal to course_instruction, source_refs using the exact supplied span ID, uncertainty low or medium or high, counter_support, and human_review_required true. Preserve the source wording and intent without silently reconciling tensions. Contradictions require id, description, source_refs, resolution_state equal unresolved, and human_review_required true. Keep course instruction, Matter Fact Assertions and Evidence Items, current jurisdiction-specific official Authority, model inference, and human decisions separate. Treat every jurisdiction rule, deadline, admissibility conclusion, remedy, filing requirement, and outcome as unresolved until verified from current official Authority and Matter facts. Do not invent facts or law. Do not file, serve, mail, dispatch, approve, execute, or advance workflow state. This is a source-derived research proposal only. Do not use or request a frontier model."""
    prompt = instruction + "\n\n" + "\n\n".join(blocks)
    request_body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 12000,
        "temperature": 0,
        "stream": False,
    }
    profile_store_path = (
        ROOT / "config/model_gateway/deployment/transport-profiles.json"
    )
    profile_store = json.loads(profile_store_path.read_text(encoding="utf-8"))
    profile = next(
        item
        for item in profile_store["profiles"]
        if item["profile_id"] == "chiap08.direct-qwen.v1"
    )
    if (
        not profile["enabled"]
        or profile["kind"] != "direct_qwen"
        or profile["endpoint_reference"] != ENDPOINT_ENV
    ):
        raise RuntimeError("approved direct Qwen transport profile is unavailable")
    started = datetime.now(UTC).isoformat()
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(request_body, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        raw = response.read()
    finished = datetime.now(UTC).isoformat()
    envelope = json.loads(raw)
    response_text = envelope["choices"][0]["message"]["content"].strip()
    if response_text.startswith("```json"):
        response_text = response_text[7:]
    elif response_text.startswith("```"):
        response_text = response_text[3:]
    if response_text.endswith("```"):
        response_text = response_text[:-3]
    proposal = json.loads(response_text.strip())
    source_to_span = {item["id"]: item["spans"][0]["id"] for item in sources}
    for collection in (proposal["propositions"], proposal["contradictions"]):
        for item in collection:
            item["source_refs"] = [
                source_to_span.get(ref, ref) for ref in item["source_refs"]
            ]
    normalized_gaps = []
    all_span_ids = sorted(source_to_span.values())
    for index, item in enumerate(proposal["unresolved_gaps"], 1):
        if isinstance(item, str):
            normalized_gaps.append(
                {
                    "id": f"gap-{index}",
                    "description": item,
                    "source_refs": all_span_ids,
                    "resolution_state": "unresolved",
                    "human_review_required": True,
                }
            )
        else:
            item["source_refs"] = [
                source_to_span.get(ref, ref) for ref in item["source_refs"]
            ]
            normalized_gaps.append(item)
    proposal["unresolved_gaps"] = normalized_gaps
    proposal["schema"] = "sklegal.htwc-propositions/v1"
    proposal = SchemaRegistry.default().validate(
        HTWC_PROPOSITIONS_SCHEMA_ID, canonical(proposal).decode()
    )
    expected_spans = {span["id"] for source in sources for span in source["spans"]}
    observed_spans = [
        ref for item in proposal["propositions"] for ref in item["source_refs"]
    ]
    if (
        len(proposal["propositions"]) != 28
        or set(observed_spans) != expected_spans
        or len(observed_spans) != len(set(observed_spans))
    ):
        raise RuntimeError(
            "Qwen response did not cover every authorized source span exactly once"
        )
    proposal_bytes = canonical(proposal)
    trace_bytes = canonical(retrieval_trace)
    prompt_bytes = prompt.encode()
    normalization = {
        "schema": "sklegal.htwc-normalization-record/v1",
        "card": "a9e3c740",
        "algorithm": "extract-message-content;strip-one-markdown-fence;parse-json;map-source-ids-to-pinned-span-ids;type-string-gaps;canonical-json-v1",
        "raw_provider_response_sha256": sha(raw),
        "normalized_response_sha256": sha(proposal_bytes),
        "source_to_span_map_sha256": sha(canonical(source_to_span)),
        "normalizer_source_sha256": sha(Path(__file__).read_bytes()),
    }
    normalization_bytes = canonical(normalization)
    dispatch_evidence = {
        "schema": "sklegal.htwc-qwen-dispatch-evidence/v1",
        "card": "a9e3c740",
        "authorization": "SKCapstone card a9e3c740 exactly-one local-Qwen derivation",
        "logical_route": "qwen.semantic-proposal.v1",
        "transport_profile": profile["profile_id"],
        "transport_kind": profile["kind"],
        "transport_configuration_sha256": sha(profile_store_path.read_bytes()),
        "endpoint_reference": ENDPOINT_ENV,
        "request_body": request_body,
        "request_started_at": started,
        "request_finished_at": finished,
        "provider_request_id": envelope.get("id"),
        "served_model": envelope.get("model"),
        "frontier_routes_invoked": [],
        "protected_matter_egress": False,
    }
    dispatch_bytes = canonical(dispatch_evidence)
    receipt = {
        "schema": "sklegal.qwen-run-receipt/v2",
        "card": "a9e3c740",
        "agent_run_id": envelope.get("id"),
        "logical_route": "qwen.semantic-proposal.v1",
        "transport_profile": "chiap08.direct-qwen.v1",
        "gateway_revision": None,
        "gateway_revision_required": False,
        "gateway_revision_reason": "Direct local vLLM transport. No SKGateway process participated.",
        "server_attestation_sha256": sha(
            (OUT / "local-qwen-server-attestation.v1.json").read_bytes()
        ),
        "backend": "chiap08-local-qwen",
        "requested_model": MODEL,
        "served_model": envelope["model"],
        "served_model_revision": server_attestation["model_revision"],
        "prompt_sha256": sha(prompt_bytes),
        "request_sha256": sha(canonical(request_body)),
        "transport_configuration_sha256": sha(profile_store_path.read_bytes()),
        "dispatch_evidence_sha256": sha(dispatch_bytes),
        "schema_sha256": sha(schema_bytes),
        "retrieval_trace_sha256": sha(trace_bytes),
        "response_sha256": sha(proposal_bytes),
        "raw_provider_response_sha256": sha(raw),
        "normalization_record_sha256": sha(normalization_bytes),
        "started_at": started,
        "finished_at": finished,
        "frontier_model_used": False,
        "frontier_rewrite": False,
        "frontier_invocation_count": 0,
        "protected_egress": False,
        "source_bytes_changed": False,
        "usage": envelope.get("usage"),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "source-manifest.v1.json").write_bytes(canonical(source_manifest))
    (OUT / "retrieval-trace.v1.json").write_bytes(trace_bytes)
    (OUT / "workflow-propositions.v1.json").write_bytes(proposal_bytes)
    (OUT / "qwen-prompt.v1.txt").write_bytes(prompt_bytes)
    (OUT / "raw-qwen-response.v1.json").write_bytes(raw)
    (OUT / "normalization-record.v1.json").write_bytes(normalization_bytes)
    (OUT / "qwen-dispatch-evidence.v1.json").write_bytes(dispatch_bytes)
    (OUT / "qwen-run-receipt.v1.json").write_bytes(canonical(receipt))


if __name__ == "__main__":
    main()
