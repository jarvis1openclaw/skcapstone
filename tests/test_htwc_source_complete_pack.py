from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
from sklegal_model_gateway import (
    HTWC_PROPOSITIONS_SCHEMA_ID,
    HTWC_WORKFLOW_PACK_SCHEMA_ID,
    SchemaRegistry,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/generate_htwc_workflow_pack.py"
SPEC = importlib.util.spec_from_file_location("htwc_generator", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_pack_is_byte_identical_generated_output() -> None:
    target = ROOT / "config/processes/howtowinincourt/workflow-pack.v2.json"
    assert target.read_bytes() == MODULE.build()


def test_every_material_proposition_has_exact_source_spans() -> None:
    pack = json.loads(MODULE.build())
    assert pack["coverage"]["material_proposition_coverage_percent"] == 100
    assert pack["coverage"]["covered_workflows"] == sorted(MODULE.WORKFLOWS)
    assert all(item["source_refs"] for item in pack["propositions"])
    assert pack["coverage"]["proposition_referenced_spans"] == 28
    assert pack["coverage"]["represented_sources"] == 28
    assert pack["coverage"]["proposition_unreferenced_spans"] == []
    assert pack["coverage"]["contradiction_referenced_spans"] >= 1
    assert pack["coverage"]["unresolved_gap_referenced_spans"] >= 1


def test_qwen_only_and_external_actions_fail_closed() -> None:
    pack = json.loads(MODULE.build())
    assert pack["qwen_run"]["logical_route"] == "qwen.semantic-proposal.v1"
    assert pack["qwen_run"]["frontier_model_used"] is False
    assert pack["qwen_run"]["frontier_rewrite"] is False
    assert not any(pack["external_actions"].values())
    assert pack["jurisdiction_overlay"]["state"] == "unresolved"
    assert pack["model_boundary"] == {
        "corpus_semantics": "sk-qwen",
        "corpus_served_model": "qwen3.8-27b-huihui-abliterated-q4_k_m",
        "fable_5_1_dispatchable": False,
        "fable_5_1_eligible": False,
        "fable_5_1_fallback": False,
        "fable_5_1_state": "placeholder_metadata_only",
        "frontier_available": "astra",
        "frontier_invoked": False,
        "protected_matter_egress": False,
    }


def test_registered_schema_rejects_missing_source_binding() -> None:
    pack = json.loads(MODULE.build())
    schema = json.loads(
        (
            ROOT / "config/processes/howtowinincourt/workflow-pack.schema.json"
        ).read_text()
    )
    pack["propositions"][0]["source_refs"] = []
    try:
        jsonschema.validate(pack, schema, cls=jsonschema.Draft202012Validator)
    except jsonschema.ValidationError:
        return
    raise AssertionError("schema accepted a proposition without source spans")


def _replace_input(monkeypatch, name: str, mutate) -> None:
    original = MODULE.load

    def altered(requested: str):
        value = deepcopy(original(requested))
        if requested == name:
            mutate(value)
        return value

    monkeypatch.setattr(MODULE, "load", altered)


def _assert_generator_rejects(monkeypatch, name: str, mutate) -> None:
    _replace_input(monkeypatch, name, mutate)
    with pytest.raises((ValueError, KeyError, jsonschema.ValidationError)):
        MODULE.build()


def test_generator_rejects_frontier_rewrite(monkeypatch) -> None:
    _replace_input(
        monkeypatch,
        "qwen-run-receipt.v1.json",
        lambda value: value.__setitem__("frontier_rewrite", True),
    )
    try:
        MODULE.build()
    except ValueError as error:
        assert "frontier semantic rewrite" in str(error)
        return
    raise AssertionError("frontier rewrite was accepted")


def test_generator_rejects_invented_gateway_revision(monkeypatch) -> None:
    def mutate(value) -> None:
        value["gateway_revision"] = "not-authoritative"

    _replace_input(monkeypatch, "qwen-run-receipt.v1.json", mutate)
    try:
        MODULE.build()
    except ValueError as error:
        assert "must not invent" in str(error)
        return
    raise AssertionError("invented SKGateway revision was accepted")


def test_generator_rejects_response_hash_drift(monkeypatch) -> None:
    _replace_input(
        monkeypatch,
        "qwen-run-receipt.v1.json",
        lambda value: value.__setitem__("response_sha256", "0" * 64),
    )
    try:
        MODULE.build()
    except ValueError as error:
        assert "response hash mismatch" in str(error)
        return
    raise AssertionError("response hash drift was accepted")


def test_generator_rejects_mixed_legal_lane(monkeypatch) -> None:
    def mutate(value) -> None:
        value["propositions"][0]["lane"] = "official_authority"

    _replace_input(monkeypatch, "workflow-propositions.v1.json", mutate)
    try:
        MODULE.build()
    except ValueError as error:
        assert "mixed source lane" in str(error)
        return
    raise AssertionError("mixed legal lane was accepted")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("prompt_sha256", "invalid"),
        ("served_model_revision", ""),
        ("transport_profile", "unbound-profile"),
        ("requested_model", "unbound-model"),
        ("raw_provider_response_sha256", "invalid"),
    ),
)
def test_generator_rejects_unbound_execution_provenance(
    monkeypatch, field: str, value: object
) -> None:
    _assert_generator_rejects(
        monkeypatch,
        "qwen-run-receipt.v1.json",
        lambda receipt: receipt.__setitem__(field, value),
    )


@pytest.mark.parametrize(
    "rights",
    (
        {},
        {
            "classification": "confidential_personal_use_research",
            "private_rights_isolation": True,
            "public_redistribution": True,
        },
    ),
)
def test_generator_rejects_absent_or_relaxed_private_rights(
    monkeypatch, rights: dict[str, object]
) -> None:
    _assert_generator_rejects(
        monkeypatch,
        "source-manifest.v1.json",
        lambda manifest: manifest.__setitem__("rights", rights),
    )


def test_generator_rejects_duplicate_proposition_id(monkeypatch) -> None:
    def mutate(proposals) -> None:
        proposals["propositions"][1]["id"] = proposals["propositions"][0]["id"]

    _assert_generator_rejects(monkeypatch, "workflow-propositions.v1.json", mutate)


def test_generator_rejects_trace_manifest_disagreement(monkeypatch) -> None:
    def mutate(trace) -> None:
        trace["sources"][0]["source_path"] = "synthetic-wrong-path"

    _assert_generator_rejects(monkeypatch, "retrieval-trace.v1.json", mutate)


def test_generator_rejects_resolved_contradiction(monkeypatch) -> None:
    def mutate(proposals) -> None:
        proposals["contradictions"][0]["human_review_required"] = False

    _assert_generator_rejects(monkeypatch, "workflow-propositions.v1.json", mutate)


def test_contradiction_cannot_hide_missing_proposition_span(monkeypatch) -> None:
    def mutate(proposals) -> None:
        missing = proposals["propositions"][0]["source_refs"][0]
        proposals["propositions"][0]["source_refs"] = proposals["propositions"][1][
            "source_refs"
        ][:]
        proposals["contradictions"][0]["source_refs"].append(missing)

    _assert_generator_rejects(monkeypatch, "workflow-propositions.v1.json", mutate)


def test_application_registry_rejects_invalid_proposal_and_pack() -> None:
    registry = SchemaRegistry.default()
    proposals = MODULE.load("workflow-propositions.v1.json")
    pack = json.loads(MODULE.build())
    assert registry.validate(HTWC_PROPOSITIONS_SCHEMA_ID, json.dumps(proposals))
    assert registry.validate(HTWC_WORKFLOW_PACK_SCHEMA_ID, json.dumps(pack))

    proposals["contradictions"][0]["human_review_required"] = False
    with pytest.raises(Exception):
        registry.validate(HTWC_PROPOSITIONS_SCHEMA_ID, json.dumps(proposals))
    pack["rights"]["public_redistribution"] = True
    with pytest.raises(Exception):
        registry.validate(HTWC_WORKFLOW_PACK_SCHEMA_ID, json.dumps(pack))
