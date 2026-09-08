#!/usr/bin/env python3
"""Build the source-complete HowToWinInCourt workflow pack from pinned inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
PACK_DIR = ROOT / "config/processes/howtowinincourt"
CORPUS = Path(os.environ.get("HAMMERTIME_ROOT", ROOT.parent / "hammerTime"))
WORKFLOWS = (
    "planning",
    "elements-evidence",
    "pleadings",
    "discovery",
    "motions",
    "trial",
    "post-judgment",
)
SHA256 = set("0123456789abcdef")
EXPECTED_RIGHTS = {
    "classification": "confidential_personal_use_research",
    "private_rights_isolation": True,
    "public_redistribution": False,
}
EXPECTED_ROUTE = "qwen.semantic-proposal.v1"
EXPECTED_PROFILE = "chiap08.direct-qwen.v1"
EXPECTED_MODEL = "qwen3.8-27b-huihui-abliterated-q4_k_m"


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(name: str) -> Any:
    return json.loads((PACK_DIR / name).read_text(encoding="utf-8"))


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - SHA256:
        raise ValueError(f"invalid SHA256: {field}")
    return value


def verify_rights(manifest: dict[str, Any]) -> None:
    if manifest.get("rights") != EXPECTED_RIGHTS:
        raise ValueError("private source rights are absent, contradictory, or relaxed")


def verify_sources(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if manifest.get("schema") != "sklegal.htwc-source-manifest/v1":
        raise ValueError("unregistered source manifest schema")
    verify_rights(manifest)
    spans: dict[str, dict[str, Any]] = {}
    for source in manifest["sources"]:
        relative = source["path"]
        if (
            relative.startswith("/")
            or ".." in Path(relative).parts
            or "/Inbox/" in f"/{relative}/"
        ):
            raise ValueError(
                f"source path is outside the authorized boundary: {relative}"
            )
        raw = (CORPUS / relative).read_bytes()
        if (
            digest(raw) != source["sha256"]
            or source["version"] != f"sha256:{source['sha256']}"
        ):
            raise ValueError(f"source drift: {relative}")
        lines = raw.decode("utf-8").splitlines()
        for span in source["spans"]:
            if not 1 <= span["start_line"] <= span["end_line"] <= len(lines):
                raise ValueError(f"invalid span bounds: {span['id']}")
            text = "\n".join(lines[span["start_line"] - 1 : span["end_line"]])
            if digest(text.encode()) != span["sha256"]:
                raise ValueError(f"span drift: {span['id']}")
            if span["id"] in spans:
                raise ValueError(f"duplicate span: {span['id']}")
            spans[span["id"]] = {
                **span,
                "source_id": source["id"],
                "source_path": source["path"],
                "source_sha256": source["sha256"],
                "workflow": source["workflow"],
            }
    return spans


def validate_proposals(proposals: dict[str, Any], spans: dict[str, Any]) -> None:
    if proposals.get("schema") != "sklegal.htwc-propositions/v1":
        raise ValueError("unregistered proposition schema")
    proposition_ids = [item["id"] for item in proposals["propositions"]]
    if len(proposition_ids) != len(set(proposition_ids)):
        raise ValueError("duplicate proposition id")
    seen = {item["workflow"] for item in proposals["propositions"]}
    if seen != set(WORKFLOWS):
        raise ValueError("every workflow must have at least one proposition")
    for item in proposals["propositions"]:
        if not item["text"].strip() or item["uncertainty"] not in {
            "low",
            "medium",
            "high",
        }:
            raise ValueError(f"invalid proposition: {item.get('id')}")
        if not item["source_refs"] or any(
            ref not in spans for ref in item["source_refs"]
        ):
            raise ValueError(f"unbound proposition: {item['id']}")
        if len(item["source_refs"]) != 1:
            raise ValueError(f"proposition must bind exactly one span: {item['id']}")
        if spans[item["source_refs"][0]]["workflow"] != item["workflow"]:
            raise ValueError(
                f"proposition workflow disagrees with source: {item['id']}"
            )
        if item["lane"] != "course_instruction":
            raise ValueError(f"mixed source lane: {item['id']}")
    contradiction_ids = [item.get("id") for item in proposals["contradictions"]]
    if len(contradiction_ids) != len(set(contradiction_ids)):
        raise ValueError("duplicate contradiction id")
    for record in proposals["contradictions"]:
        if (
            not isinstance(record.get("id"), str)
            or not record["id"].strip()
            or not isinstance(record.get("description"), str)
            or not record["description"].strip()
            or record.get("resolution_state") != "unresolved"
            or record.get("human_review_required") is not True
        ):
            raise ValueError("contradiction must remain typed and unresolved")
        if not record["source_refs"] or any(
            ref not in spans for ref in record["source_refs"]
        ):
            raise ValueError(f"unbound contradiction: {record['id']}")
    for record in proposals["unresolved_gaps"]:
        if (
            not isinstance(record.get("id"), str)
            or not record["id"].strip()
            or not isinstance(record.get("description"), str)
            or not record["description"].strip()
            or record.get("resolution_state") != "unresolved"
            or record.get("human_review_required") is not True
        ):
            raise ValueError("gap must remain typed and unresolved")
        if not record["source_refs"] or any(
            ref not in spans for ref in record["source_refs"]
        ):
            raise ValueError(f"unbound unresolved gap: {record['id']}")


def verify_trace(trace: dict[str, Any], spans: dict[str, Any]) -> None:
    if trace.get("schema") != "sklegal.htwc-retrieval-trace/v1":
        raise ValueError("unregistered retrieval trace schema")
    rows = trace.get("sources")
    if not isinstance(rows, list) or len(rows) != len(spans):
        raise ValueError("retrieval trace must cover every manifest span exactly once")
    seen: set[str] = set()
    for row in rows:
        span_id = row.get("span_id")
        if span_id in seen or span_id not in spans:
            raise ValueError("retrieval trace span identity mismatch")
        expected = spans[span_id]
        fields = {
            "source_id": expected["source_id"],
            "source_path": expected["source_path"],
            "source_sha256": expected["source_sha256"],
            "span_id": span_id,
            "span_sha256": expected["sha256"],
            "start_line": expected["start_line"],
            "end_line": expected["end_line"],
        }
        if any(row.get(key) != value for key, value in fields.items()):
            raise ValueError(f"retrieval trace disagrees with manifest: {span_id}")
        seen.add(span_id)
    if seen != set(spans):
        raise ValueError("retrieval trace coverage mismatch")


def normalize_raw_response(
    raw: bytes, manifest: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    envelope = json.loads(raw)
    response_text = envelope["choices"][0]["message"]["content"].strip()
    if response_text.startswith("```json"):
        response_text = response_text[7:]
    elif response_text.startswith("```"):
        response_text = response_text[3:]
    if response_text.endswith("```"):
        response_text = response_text[:-3]
    proposal = json.loads(response_text.strip())
    source_to_span = {
        item["id"]: item["spans"][0]["id"] for item in manifest["sources"]
    }
    for collection in (proposal["propositions"], proposal["contradictions"]):
        for item in collection:
            item["source_refs"] = [
                source_to_span.get(ref, ref) for ref in item["source_refs"]
            ]
    all_span_ids = sorted(source_to_span.values())
    normalized_gaps = []
    for index, item in enumerate(proposal["unresolved_gaps"], 1):
        if isinstance(item, str):
            item = {
                "id": f"gap-{index}",
                "description": item,
                "source_refs": all_span_ids,
                "resolution_state": "unresolved",
                "human_review_required": True,
            }
        else:
            item["source_refs"] = [
                source_to_span.get(ref, ref) for ref in item["source_refs"]
            ]
        normalized_gaps.append(item)
    proposal["unresolved_gaps"] = normalized_gaps
    proposal["schema"] = "sklegal.htwc-propositions/v1"
    return proposal, source_to_span


def verify_receipt(
    receipt: dict[str, Any], proposals_bytes: bytes, trace_bytes: bytes
) -> None:
    required = {
        "agent_run_id",
        "logical_route",
        "transport_profile",
        "gateway_revision",
        "requested_model",
        "served_model",
        "served_model_revision",
        "prompt_sha256",
        "schema_sha256",
        "retrieval_trace_sha256",
        "response_sha256",
        "raw_provider_response_sha256",
        "normalization_record_sha256",
        "request_sha256",
        "transport_configuration_sha256",
        "dispatch_evidence_sha256",
    }
    if required - receipt.keys():
        raise ValueError("incomplete Qwen receipt")
    if receipt["logical_route"] != EXPECTED_ROUTE:
        raise ValueError("semantic derivation did not use the Qwen route")
    if receipt["frontier_model_used"] or receipt["frontier_rewrite"]:
        raise ValueError("frontier semantic rewrite is prohibited")
    attestation = (PACK_DIR / "local-qwen-server-attestation.v1.json").read_bytes()
    if receipt["server_attestation_sha256"] != digest(attestation):
        raise ValueError("local Qwen server attestation mismatch")
    server = json.loads(attestation)
    if (
        server["router_present"]
        or server["frontier_catalog_entries"]
        or server["frontier_dispatch_possible_on_this_server"]
    ):
        raise ValueError("server-side frontier dispatch was not excluded")
    if receipt["gateway_revision"] is not None or receipt["gateway_revision_required"]:
        raise ValueError("direct local transport must not invent an SKGateway revision")
    if receipt["transport_profile"] != EXPECTED_PROFILE:
        raise ValueError("Qwen receipt transport profile is not approved")
    if receipt["requested_model"] != EXPECTED_MODEL:
        raise ValueError("Qwen receipt requested model is not approved")
    if receipt["served_model"] != EXPECTED_MODEL:
        raise ValueError("Qwen receipt served model is not approved")
    if (
        not isinstance(receipt["served_model_revision"], str)
        or not receipt["served_model_revision"].strip()
    ):
        raise ValueError("Qwen served model revision is absent")
    for field in (
        "prompt_sha256",
        "raw_provider_response_sha256",
        "normalization_record_sha256",
        "request_sha256",
        "transport_configuration_sha256",
        "dispatch_evidence_sha256",
    ):
        require_sha256(receipt[field], field)
    prompt = (PACK_DIR / "qwen-prompt.v1.txt").read_bytes()
    raw_response = (PACK_DIR / "raw-qwen-response.v1.json").read_bytes()
    normalization = (PACK_DIR / "normalization-record.v1.json").read_bytes()
    dispatch = (PACK_DIR / "qwen-dispatch-evidence.v1.json").read_bytes()
    profiles = (
        ROOT / "config/model_gateway/deployment/transport-profiles.json"
    ).read_bytes()
    expected_hashes = {
        "prompt_sha256": digest(prompt),
        "raw_provider_response_sha256": digest(raw_response),
        "normalization_record_sha256": digest(normalization),
        "dispatch_evidence_sha256": digest(dispatch),
        "transport_configuration_sha256": digest(profiles),
    }
    if any(receipt[field] != value for field, value in expected_hashes.items()):
        raise ValueError("Qwen request-bound execution artifact mismatch")
    request_record = json.loads(dispatch)
    request_body = request_record.get("request_body")
    if receipt["request_sha256"] != digest(canonical(request_body)):
        raise ValueError("Qwen request hash mismatch")
    if receipt["prompt_sha256"] != digest(prompt):
        raise ValueError("Qwen prompt hash mismatch")
    if (
        request_record.get("card") != "a9e3c740"
        or request_record.get("logical_route") != EXPECTED_ROUTE
        or request_record.get("transport_profile") != EXPECTED_PROFILE
        or request_record.get("transport_kind") != "direct_qwen"
        or request_record.get("frontier_routes_invoked") != []
        or request_body.get("model") != EXPECTED_MODEL
    ):
        raise ValueError("Qwen dispatch evidence is not request-bound")
    normalization_record = json.loads(normalization)
    if normalization_record.get("raw_provider_response_sha256") != digest(
        raw_response
    ) or normalization_record.get("normalized_response_sha256") != digest(
        proposals_bytes
    ):
        raise ValueError("Qwen normalization record mismatch")
    if receipt["response_sha256"] != digest(proposals_bytes):
        raise ValueError("Qwen response hash mismatch")
    if receipt["retrieval_trace_sha256"] != digest(trace_bytes):
        raise ValueError("retrieval trace hash mismatch")


def build() -> bytes:
    source_manifest = load("source-manifest.v1.json")
    proposals = load("workflow-propositions.v1.json")
    receipt = load("qwen-run-receipt.v1.json")
    trace = load("retrieval-trace.v1.json")
    schema = load("workflow-pack.schema.json")
    spans = verify_sources(source_manifest)
    verify_trace(trace, spans)
    validate_proposals(proposals, spans)
    proposal_bytes = canonical(proposals)
    raw_response = (PACK_DIR / "raw-qwen-response.v1.json").read_bytes()
    replayed, source_to_span = normalize_raw_response(raw_response, source_manifest)
    if canonical(replayed) != proposal_bytes:
        raise ValueError("raw Qwen response normalization replay mismatch")
    normalization = load("normalization-record.v1.json")
    if normalization.get("source_to_span_map_sha256") != digest(
        canonical(source_to_span)
    ):
        raise ValueError("Qwen normalization source map mismatch")
    capture_source = ROOT / "scripts/capture_htwc_qwen_derivation.py"
    if normalization.get("normalizer_source_sha256") != digest(
        capture_source.read_bytes()
    ):
        raise ValueError("Qwen normalizer source hash mismatch")
    trace_bytes = canonical(trace)
    verify_receipt(receipt, proposal_bytes, trace_bytes)
    if receipt["schema_sha256"] != digest(canonical(schema)):
        raise ValueError("registered schema hash mismatch")
    proposition_refs = {
        ref for item in proposals["propositions"] for ref in item["source_refs"]
    }
    contradiction_refs = {
        ref for item in proposals["contradictions"] for ref in item["source_refs"]
    }
    gap_refs = {
        ref for item in proposals["unresolved_gaps"] for ref in item["source_refs"]
    }
    all_spans = set(spans)
    if (
        len(proposition_refs) != len(proposals["propositions"])
        or proposition_refs != all_spans
    ):
        raise ValueError("propositions must cover every required span exactly once")
    output = {
        "schema": "sklegal.htwc-workflow-pack/v2",
        "card": "a9e3c740",
        "source_manifest_sha256": digest(canonical(source_manifest)),
        "qwen_run_receipt_sha256": digest(canonical(receipt)),
        "qwen_run": receipt,
        "rights": source_manifest["rights"],
        "lanes": {
            "course_instruction": "source-derived proposals in this pack",
            "matter_facts_and_evidence": "not supplied",
            "official_authority": "not supplied; current jurisdiction-specific verification required",
            "model_inference": "Qwen proposal with per-item uncertainty",
            "human_decision": "not supplied; review is required",
        },
        "jurisdiction_overlay": {
            "included": False,
            "state": "unresolved",
            "rule": "No course proposition is controlling Authority or a Matter deadline.",
        },
        "external_actions": {
            "filing": False,
            "service": False,
            "mailing": False,
            "dispatch": False,
            "workflow_state_advance": False,
        },
        "model_boundary": {
            "corpus_semantics": "sk-qwen",
            "corpus_served_model": receipt["served_model"],
            "frontier_available": "astra",
            "frontier_invoked": False,
            "fable_5_1_state": "placeholder_metadata_only",
            "fable_5_1_eligible": False,
            "fable_5_1_dispatchable": False,
            "fable_5_1_fallback": False,
            "protected_matter_egress": False,
        },
        "propositions": proposals["propositions"],
        "contradictions": proposals["contradictions"],
        "unresolved_gaps": proposals["unresolved_gaps"],
        "coverage": {
            "required_workflows": list(WORKFLOWS),
            "covered_workflows": sorted(
                {p["workflow"] for p in proposals["propositions"]}
            ),
            "material_propositions": len(proposals["propositions"]),
            "source_bound_material_propositions": len(proposals["propositions"]),
            "material_proposition_coverage_percent": 100,
            "authorized_sources": len(source_manifest["sources"]),
            "represented_sources": len(
                {spans[ref]["source_id"] for ref in proposition_refs}
            ),
            "declared_spans": len(all_spans),
            "proposition_referenced_spans": len(proposition_refs),
            "proposition_unreferenced_spans": sorted(all_spans - proposition_refs),
            "contradiction_referenced_spans": len(contradiction_refs),
            "contradiction_unreferenced_spans": sorted(all_spans - contradiction_refs),
            "unresolved_gap_referenced_spans": len(gap_refs),
            "unresolved_gap_unreferenced_spans": sorted(all_spans - gap_refs),
        },
    }
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(output, schema, cls=jsonschema.Draft202012Validator)
    return canonical(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = build()
    target = PACK_DIR / "workflow-pack.v2.json"
    if args.check:
        if not target.exists() or target.read_bytes() != expected:
            raise SystemExit("workflow pack is not the byte-identical generated output")
    else:
        target.write_bytes(expected)
        print(digest(expected))


if __name__ == "__main__":
    main()
