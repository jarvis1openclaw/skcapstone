from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

from sklegal_api.features.artifact_intake.contracts import (
    ArtifactIntakeCommand,
    ArtifactSourceInput,
    DerivationRequest,
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
    T0,
    TENANT_ID,
    original,
)

FIXTURE_PATH = Path(
    "tests/fixtures/mvp/fragments/artifact_intake/public-synthetic-artifact-intake.json"
)


def load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def build_service() -> ArtifactIntakeService:
    return ArtifactIntakeService(
        store=InMemoryArtifactStore(),
        policy=StaticArtifactPolicy(
            memberships={(TENANT_ID, MATTER_ID, PRINCIPAL_ID)},
            allowed_classifications={"public"},
            revision="a" * 64,
            valid_until=T0 + timedelta(hours=1),
        ),
        adapter=SyntheticArtifactAdapter(),
        clock=lambda: T0,
    )


def fixture_command(fixture: dict[str, object]) -> ArtifactIntakeCommand:
    source = fixture["source"]
    artifact = fixture["original"]
    governance = fixture["governance"]
    assert isinstance(source, dict)
    assert isinstance(artifact, dict)
    assert isinstance(governance, dict)
    content = str(artifact["content_utf8"]).encode("utf-8")
    return ArtifactIntakeCommand(
        source=ArtifactSourceInput.model_validate(source),
        original=original(
            content,
            filename=str(artifact["filename"]),
            media_type=str(artifact["media_type"]),
        ),
        acquisition_method="synthetic_adapter",
        classification="public",
        privilege_state="not_privileged",
        retention_policy_id=UUID(str(governance["retention_policy_id"])),
        legal_hold_ids=tuple(UUID(str(item)) for item in governance["legal_hold_ids"]),
        requested_derivations=tuple(
            DerivationRequest(
                kind=cast(Literal["text_extraction", "ocr", "transcript"], kind),
                tool_name=f"synthetic-{kind}",
                tool_version="1.0.0",
                output_media_type="text/plain",
            )
            for kind in cast(list[str], fixture["requested_derivations"])
        ),
    )


def access() -> ArtifactAccessContext:
    return ArtifactAccessContext(
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        principal_id=PRINCIPAL_ID,
        capability="evidence.manage",
        purpose="evidence_review",
        authorization_decision_id=UUID("60000000-0000-4000-8000-000000000001"),
        credential_expires_at=T0 + timedelta(hours=1),
    )


def test_fixture_is_executable_and_deterministically_resettable() -> None:
    fixture = load_fixture()
    command = fixture_command(fixture)
    first = build_service().intake(
        context=access(),
        matter_id=MATTER_ID,
        idempotency_key="fixture-artifact-intake-001",
        command=command,
    )
    reset = build_service().intake(
        context=access(),
        matter_id=MATTER_ID,
        idempotency_key="fixture-artifact-intake-001",
        command=command,
    )

    expected = fixture["expected"]
    assert isinstance(expected, dict)
    assert first == reset
    assert first.artifact.scan.state == expected["scan_state"]
    assert first.artifact.quarantine_state == expected["quarantine_state"]
    assert first.duplicate is expected["duplicate"]
    derivations = [item.derivation for item in first.derived_artifacts]
    assert all(item is not None for item in derivations)
    assert [item.kind for item in derivations if item is not None] == expected[
        "derived_kinds"
    ]
    assert [item.action for item in first.artifact.custody] == expected[
        "custody_actions"
    ]


def test_hammertime_contract_fixture_has_no_path_or_connector_access() -> None:
    fixture = load_fixture()
    adapter = SyntheticArtifactAdapter()
    assert fixture["truth_boundary"] == "public_synthetic"
    assert adapter.filesystem_access is False
    assert adapter.provider_access is False
    rendered = json.dumps(fixture)
    assert "content_base64" not in rendered
    assert "external_effect" in rendered
    expected = cast(dict[str, object], fixture["expected"])
    assert expected["external_effect"] is False
