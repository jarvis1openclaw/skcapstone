from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError
from sklegal_api.features.artifact_intake.contracts import (
    ArtifactIntakeCommand,
    ArtifactLineageRead,
    DerivationRequest,
    OriginalArtifactInput,
    ProposedRecordLinkInput,
)
from sklegal_api.features.artifact_intake.service import (
    ArtifactAccessContext,
    ArtifactIntakeService,
    StaticArtifactPolicy,
)
from sklegal_api.features.artifact_intake.store import InMemoryArtifactStore
from sklegal_api.features.artifact_intake.synthetic import SyntheticArtifactAdapter

from tests.features.artifact_intake.factories import (
    MATTER_ID,
    PRINCIPAL_ID,
    TENANT_ID,
    command,
    original,
)

T0 = datetime(2099, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_command_round_trips_with_camel_case_wire_names() -> None:
    body = command().model_dump(mode="json", by_alias=True)

    assert body["source"]["sourceSystem"] == "public_synthetic"
    assert body["original"]["contentSha256"] == original().content_sha256
    assert body["requestedDerivations"][0]["kind"] == "text_extraction"
    assert body["proposedLinks"][0]["targetType"] == "fact_assertion"
    assert ArtifactIntakeCommand.model_validate(body) == command()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"content_sha256": "0" * 64}, "content sha256 does not match"),
        ({"byte_count": 2}, "byte count does not match"),
        ({"filename": "../unsafe.txt"}, "filename must not contain a path"),
        ({"media_type": "text/plain\r\nx-unsafe: yes"}, "media type is invalid"),
    ],
)
def test_original_bytes_are_hash_and_shape_bound(
    change: dict[str, object], message: str
) -> None:
    values = original().model_dump()
    values.update(change)
    with pytest.raises(ValidationError, match=message):
        OriginalArtifactInput.model_validate(values)


def test_command_requires_declared_retention_and_public_synthetic_source() -> None:
    values = command().model_dump()
    values["retention_policy_id"] = None
    with pytest.raises(ValidationError):
        ArtifactIntakeCommand.model_validate(values)

    values = command().model_dump()
    values["source"]["source_system"] = "protected_hammertime"
    with pytest.raises(ValidationError):
        ArtifactIntakeCommand.model_validate(values)

    values = command().model_dump()
    values["classification"] = "confidential"
    with pytest.raises(ValidationError):
        ArtifactIntakeCommand.model_validate(values)


def test_derivation_contract_has_closed_kind_and_tool_evidence() -> None:
    with pytest.raises(ValidationError):
        DerivationRequest(
            kind="model_guess",  # type: ignore[arg-type]
            tool_name="synthetic",
            tool_version="1.0.0",
            output_media_type="text/plain",
        )
    with pytest.raises(ValidationError):
        DerivationRequest(
            kind="ocr",
            tool_name="",
            tool_version="1.0.0",
            output_media_type="text/plain",
        )


def test_proposed_links_are_inert_and_use_closed_legal_record_types() -> None:
    link = command().proposed_links[0]
    assert link.review_state == "proposed"
    assert link.target_type == "fact_assertion"
    with pytest.raises(ValidationError):
        ProposedRecordLinkInput(
            target_type="external_action",  # type: ignore[arg-type]
            target_id=UUID("50000000-0000-4000-8000-000000000001"),
            rationale="Not an artifact relationship.",
        )


@pytest.mark.parametrize(
    "mutation",
    ["original_hash", "input_hash", "output_hash", "missing_edge"],
)
def test_lineage_is_connected_by_exact_content_hashes(mutation: str) -> None:
    store = InMemoryArtifactStore()
    service = ArtifactIntakeService(
        store=store,
        policy=StaticArtifactPolicy(
            memberships={(TENANT_ID, MATTER_ID, PRINCIPAL_ID)},
            allowed_classifications={"public"},
            revision="9" * 64,
            valid_until=T0.replace(year=2100),
        ),
        adapter=SyntheticArtifactAdapter(),
        clock=lambda: T0,
    )
    receipt = service.intake(
        context=ArtifactAccessContext(
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            principal_id=PRINCIPAL_ID,
            capability="evidence.manage",
            purpose="evidence_review",
            authorization_decision_id=UUID("60000000-0000-4000-8000-000000000001"),
            credential_expires_at=T0.replace(year=2100),
        ),
        matter_id=MATTER_ID,
        idempotency_key="artifact-lineage-contract",
        command=command(derivations=("ocr",)),
    )
    values = receipt.lineage.model_dump()
    if mutation == "missing_edge":
        values["edges"] = ()
    else:
        child = values["artifacts"][1]
        if mutation == "original_hash":
            child["original_sha256"] = "a" * 64
        elif mutation == "output_hash":
            child["derivation"]["tool_evidence"]["output_sha256"] = "a" * 64
        else:
            child["derivation"]["tool_evidence"]["input_sha256"] = "a" * 64
        values["edges"] = (child["derivation"],)
    with pytest.raises(ValidationError):
        ArtifactLineageRead.model_validate(values)
