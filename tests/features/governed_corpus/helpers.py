from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sklegal_api.features.governed_corpus.contracts import RecordCorpusSourceCommand
from sklegal_api.features.governed_corpus.service import (
    GovernedCorpusAccessContext,
    GovernedCorpusService,
    StaticGovernedCorpusPolicy,
)
from sklegal_persistence.features.governed_corpus.models import (
    Classification,
    CorpusProjectionCommand,
    CorpusSourceVersion,
    ProjectionState,
)
from sklegal_persistence.features.governed_corpus.repository import (
    InMemoryGovernedCorpusRepository,
)

TENANT = UUID("10000000-0000-4000-8000-000000000001")
OTHER_TENANT = UUID("10000000-0000-4000-8000-000000000002")
MATTER = UUID("20000000-0000-4000-8000-000000000001")
OTHER_MATTER = UUID("20000000-0000-4000-8000-000000000002")
PRINCIPAL = UUID("40000000-0000-4000-8000-000000000001")
OTHER_PRINCIPAL = UUID("40000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
RIGHTS = "1" * 64
POLICY = "2" * 64
CURSOR_KEY = hashlib.sha256(b"public-synthetic-cursor-fixture").digest()

FIXTURE = (
    Path(__file__).parents[2]
    / "fixtures/mvp/fragments/governed_corpus/public-synthetic-governed-corpus-v1.json"
)


def fixture() -> tuple[ProjectionState, tuple[CorpusSourceVersion, ...]]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for source in data["sources"]:
        source["chunkSha256"] = hashlib.sha256(
            source["exactSpan"].encode("utf-8")
        ).hexdigest()
    return (
        ProjectionState.model_validate(data["projection"]),
        tuple(CorpusSourceVersion.model_validate(item) for item in data["sources"]),
    )


def access(
    capability: str = "corpus.search",
    purpose: str = "legal_research",
    *,
    matter_id: UUID = MATTER,
    principal_id: UUID = PRINCIPAL,
    revoked: bool = False,
    expires_at: datetime | None = None,
) -> GovernedCorpusAccessContext:
    return GovernedCorpusAccessContext.model_validate(
        {
            "tenant_id": TENANT,
            "matter_id": matter_id,
            "principal_id": principal_id,
            "capability": capability,
            "purpose": purpose,
            "authorization_decision_id": UUID("50000000-0000-4000-8000-000000000001"),
            "authorization_context_sha256": "3" * 64,
            "credential_expires_at": expires_at or NOW + timedelta(hours=1),
            "revoked": revoked,
        }
    )


def source_command(source: CorpusSourceVersion) -> RecordCorpusSourceCommand:
    return RecordCorpusSourceCommand.model_validate(
        source.model_dump(
            mode="python",
            exclude={
                "schema_version",
                "tenant_id",
                "matter_id",
                "rights_revision",
                "recorded_at",
                "recorded_by_principal_id",
                "authorization_decision_id",
                "policy_decision_id",
                "policy_revision",
            },
        )
    )


def composition(
    *,
    seed: bool = True,
    clock: Callable[[], datetime] | None = None,
) -> tuple[
    GovernedCorpusService,
    InMemoryGovernedCorpusRepository,
    StaticGovernedCorpusPolicy,
]:
    projection, sources = fixture()
    repository = InMemoryGovernedCorpusRepository(
        policy_revision=POLICY,
        rights_revision=RIGHTS,
    )
    repository.set_matter_members(TENANT, MATTER, {PRINCIPAL})
    repository.set_projection(TENANT, MATTER, projection)
    grants = {
        (TENANT, MATTER, PRINCIPAL, "corpus.search", "legal_research"),
        (TENANT, MATTER, PRINCIPAL, "corpus.artifact.read", "legal_research"),
        (TENANT, MATTER, PRINCIPAL, "corpus.ingest.submit", "corpus_ingestion"),
    }
    policy = StaticGovernedCorpusPolicy(
        grants=grants,
        classification_ceiling=Classification.PUBLIC,
        rights_revision=RIGHTS,
        revision=POLICY,
        valid_until=NOW + timedelta(hours=1),
    )
    service = GovernedCorpusService(
        core_repository=repository,
        retrieval_repository=repository,
        policy=policy,
        clock=clock or (lambda: NOW),
        cursor_signing_key=CURSOR_KEY,
    )
    if seed:
        for index, source in enumerate(sources, start=1):
            receipt = service.record_source(
                context=access("corpus.ingest.submit", "corpus_ingestion"),
                matter_id=MATTER,
                idempotency_key=f"seed-{index}",
                command=source_command(source),
            )
            repository.apply_projection(
                CorpusProjectionCommand(
                    command_id=receipt.outbox_id,
                    operation=(
                        "supersede"
                        if receipt.source.supersedes_source_version_id is not None
                        else "create"
                    ),
                    source=receipt.source,
                    payload_sha256=repository.outbox_records[-1].payload_sha256,
                    core_watermark=projection.core_watermark,
                    projected_at=receipt.source.recorded_at,
                )
            )
    return service, repository, policy
