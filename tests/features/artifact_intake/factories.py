from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from sklegal_api.features.artifact_intake.contracts import (
    ArtifactIntakeCommand,
    ArtifactSourceInput,
    DerivationRequest,
    OriginalArtifactInput,
    ProposedRecordLinkInput,
)

T0 = datetime(2099, 1, 2, 3, 4, 5, tzinfo=UTC)
TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("20000000-0000-4000-8000-000000000001")
OTHER_MATTER_ID = UUID("20000000-0000-4000-8000-000000000002")
PRINCIPAL_ID = UUID("30000000-0000-4000-8000-000000000001")
RETENTION_ID = UUID("40000000-0000-4000-8000-000000000001")


def original(
    content: bytes = b"Public synthetic exhibit bytes.",
    *,
    filename: str = "synthetic-exhibit.txt",
    media_type: str = "text/plain",
) -> OriginalArtifactInput:
    return OriginalArtifactInput(
        filename=filename,
        media_type=media_type,
        byte_count=len(content),
        content_sha256=hashlib.sha256(content).hexdigest(),
        content_base64=base64.b64encode(content).decode("ascii"),
    )


def command(
    *,
    content: bytes = b"Public synthetic exhibit bytes.",
    source_identity: str = "synthetic-source-001",
    derivations: tuple[str, ...] = ("text_extraction",),
) -> ArtifactIntakeCommand:
    return ArtifactIntakeCommand(
        source=ArtifactSourceInput(
            source_system="public_synthetic",
            source_identity=source_identity,
            source_version="v1",
            observed_at=T0,
        ),
        original=original(content),
        acquisition_method="synthetic_adapter",
        classification="public",
        privilege_state="not_privileged",
        retention_policy_id=RETENTION_ID,
        requested_derivations=tuple(
            DerivationRequest(
                kind=cast(Literal["text_extraction", "ocr", "transcript"], kind),
                tool_name=f"synthetic-{kind}",
                tool_version="1.0.0",
                output_media_type="text/plain",
            )
            for kind in derivations
        ),
        proposed_links=(
            ProposedRecordLinkInput(
                target_type="fact_assertion",
                target_id=UUID("50000000-0000-4000-8000-000000000001"),
                rationale="The artifact may support this synthetic assertion.",
            ),
        ),
    )
