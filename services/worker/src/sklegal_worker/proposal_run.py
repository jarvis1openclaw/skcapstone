"""Governed end-to-end Qwen proposal run over pinned matter context.

One runner resolves a pinned retrieval context, retrieves it through the
SKL-S2-04 orchestrator, composes the pinned prompt, submits it to local
Qwen through the SKL-S3-02 model gateway, validates the typed proposal
and its source links, re-verifies the source inventory after the provider
call to catch a mid-run source change, and records one immutable,
content-free proposal record in an idempotent ledger.

Provider output stays a proposal: nothing here mutates domain, workflow,
or connector state, and no external action occurs. Acceptance or
rejection happens only through a separate human decision.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import Field
from sklegal_model_gateway.errors import (
    AuditUnavailableError,
    CapabilityDeniedError,
    CapacityQueueTimeoutError,
    EgressDeniedError,
    FreeRouteDeniedError,
    ModelCancelledError,
    ModelGatewayError,
    ModelTimeoutError,
    PolicyUnavailableError,
    ProviderContractError,
    ProviderSaturationError,
    ProviderUnavailableError,
    SchemaValidationError,
    SourceRightsDeniedError,
)
from sklegal_model_gateway.gateway import ModelGateway
from sklegal_model_gateway.models import Proposal, ProposalRequest
from sklegal_retrieval.errors import (
    RetrievalAuthorizationError,
    RetrievalError,
)
from sklegal_retrieval.models import (
    RetrievalRequest,
    RetrievalResult,
    ScopeKind,
)
from sklegal_retrieval.orchestrator import RetrievalOrchestrator
from temporalio import activity

from .errors import (
    ContextRetrievalUnavailableError,
    ModelUnavailableError,
    PolicyDeniedError,
    ProposalOutputInvalidError,
    SourceLinkValidationError,
    WorkflowInvariantError,
)
from .models import (
    ProposalRunInput,
    ProposalRunOutcome,
    SourcePin,
    WorkflowPayload,
)

_PROMPT_INSTRUCTION_KEY = "instructions"
_PROMPT_CONTEXT_KEY = "context"


class PinnedProposalContext(WorkflowPayload):
    """One registered pinned retrieval context for governed proposal runs.

    Holds the typed SKL-S2-04 retrieval request built from the approved
    pilot import pins and the exact source inventory the run must observe.
    The request itself lives only inside the worker process: it never
    enters workflow history.
    """

    pin_id: str = Field(min_length=1, max_length=160)
    tenant_ref: str = Field(min_length=1, max_length=160)
    matter_ref: str = Field(min_length=1, max_length=160)
    request: RetrievalRequest
    sources: tuple[SourcePin, ...] = Field(min_length=1)

    @staticmethod
    def from_request(
        pin_id: str,
        request: RetrievalRequest,
        sources: tuple[SourcePin, ...],
    ) -> PinnedProposalContext:
        """Build one pinned context from a matter-scoped typed request."""

        if request.scope.scope_kind is not ScopeKind.MATTER:
            raise WorkflowInvariantError(
                "governed proposal context must be matter scoped"
            )
        matter_id = request.scope.matter_id
        if matter_id is None:
            raise WorkflowInvariantError(
                "governed proposal context requires an exact Matter identifier"
            )
        return PinnedProposalContext(
            pin_id=pin_id,
            tenant_ref=str(request.scope.tenant_id),
            matter_ref=str(matter_id),
            request=request,
            sources=sources,
        )


class PinnedContextSource(Protocol):
    """Resolve one pinned context by identifier; fail closed when absent."""

    def resolve(self, pin_id: str) -> PinnedProposalContext: ...


class InMemoryPinnedContextRegistry:
    """Registry of pinned contexts keyed by their pin identifier.

    Registering the same pin twice with equal content is idempotent; a
    different payload under a taken pin identifier is rejected.
    """

    def __init__(self) -> None:
        self._pins: dict[str, str] = {}
        self._contexts: dict[str, PinnedProposalContext] = {}

    def register(self, context: PinnedProposalContext) -> None:
        fingerprint = _canonical_sha256(
            context.model_dump(mode="json", exclude={"request"})
        )
        existing = self._pins.get(context.pin_id)
        if existing is not None:
            if existing != fingerprint:
                raise WorkflowInvariantError(
                    "pinned context identifier reused with different pins"
                )
            return
        self._pins[context.pin_id] = fingerprint
        self._contexts[context.pin_id] = context

    def resolve(self, pin_id: str) -> PinnedProposalContext:
        context = self._contexts.get(pin_id)
        if context is None:
            raise WorkflowInvariantError(
                f"pinned proposal context is not registered: {pin_id}"
            )
        return context


class ProposalRunRecord(WorkflowPayload):
    """Immutable content-free evidence for one governed proposal run.

    Every field is an identifier, digest, or pinned revision. The proposal
    payload, prompt text, and retrieved content stay out of the record:
    the full typed Proposal object remains available only to the human
    review surface that reads the ledger's parent proposal record.
    """

    run_key: str = Field(min_length=1, max_length=160)
    proposal_id: str = Field(min_length=1, max_length=255)
    gateway_request_id: str = Field(min_length=1, max_length=160)
    route_id: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=160)
    model_revision: str = Field(min_length=1, max_length=160)
    prompt_template_id: str = Field(min_length=1, max_length=160)
    prompt_template_sha256: str
    output_schema_id: str = Field(min_length=1, max_length=160)
    output_schema_sha256: str
    registry_revision: str
    payload_sha256: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    egress_rule: str = Field(min_length=1, max_length=160)
    policy_revision: str
    classification: str = Field(min_length=1, max_length=64)
    retrieval_request_sha256: str
    release_id: str = Field(min_length=1, max_length=255)
    projection_generation: int = Field(ge=1)
    retrieval_adapter_version: str = Field(min_length=1, max_length=160)
    context_sources: tuple[SourcePin, ...] = Field(min_length=1)
    context_hit_count: int = Field(ge=1)
    typed_output_validated: bool = True
    source_links_validated: bool = True
    validated_at: str = Field(min_length=1, max_length=64)


def proposal_record_fingerprint(record: ProposalRunRecord) -> str:
    """Deterministic digest over every replayable content-bearing field.

    ``validated_at`` is wall-clock evidence and is excluded, so two runs
    over identical pinned inputs and identical provider output share one
    fingerprint and replay idempotently.
    """

    return _canonical_sha256(record.model_dump(mode="json", exclude={"validated_at"}))


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class ProposalLedger(Protocol):
    """Append-only, idempotent store of governed proposal records."""

    def find(self, run_key: str) -> ProposalRunRecord | None: ...

    def record(self, record: ProposalRunRecord) -> ProposalRunRecord: ...

    @property
    def recorded_count(self) -> int: ...


class InMemoryProposalLedger:
    """In-memory ledger keyed on the run key, first write wins."""

    def __init__(self) -> None:
        self._records: dict[str, ProposalRunRecord] = {}
        self._fingerprints: dict[str, str] = {}

    @property
    def recorded_count(self) -> int:
        return len(self._records)

    def find(self, run_key: str) -> ProposalRunRecord | None:
        return self._records.get(run_key)

    def record(self, record: ProposalRunRecord) -> ProposalRunRecord:
        fingerprint = proposal_record_fingerprint(record)
        existing = self._records.get(record.run_key)
        if existing is not None:
            if self._fingerprints[record.run_key] != fingerprint:
                raise WorkflowInvariantError(
                    "proposal run key reused with different evidence"
                )
            return existing
        self._records[record.run_key] = record
        self._fingerprints[record.run_key] = fingerprint
        return record


class FileProposalLedger:
    """Crash-durable JSON-file ledger keyed on the run key.

    Same contract as the in-memory ledger: the same run key replays to the
    original record after a worker restart, and a different fingerprint
    under a taken key is a conflict. State is written atomically and an
    unreadable or malformed state file fails closed.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def _load(self) -> dict[str, dict[str, object]]:
        if not self._path.exists():
            return {"records": {}, "fingerprints": {}}
        try:
            state = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkflowInvariantError("proposal ledger state is unreadable") from exc
        if (
            not isinstance(state, dict)
            or not isinstance(state.get("records"), dict)
            or not isinstance(state.get("fingerprints"), dict)
        ):
            raise WorkflowInvariantError("proposal ledger state is malformed")
        return state

    def _store(self, state: dict[str, dict[str, object]]) -> None:
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._path)

    @property
    def recorded_count(self) -> int:
        return len(self._load()["records"])

    def find(self, run_key: str) -> ProposalRunRecord | None:
        state = self._load()
        stored = state["records"].get(run_key)
        if stored is None:
            return None
        assert isinstance(stored, dict)
        return ProposalRunRecord.model_validate(stored)

    def record(self, record: ProposalRunRecord) -> ProposalRunRecord:
        fingerprint = proposal_record_fingerprint(record)
        state = self._load()
        records = state["records"]
        assert isinstance(records, dict)
        existing = records.get(record.run_key)
        if existing is not None:
            fingerprints = state["fingerprints"]
            assert isinstance(fingerprints, dict)
            if fingerprints[record.run_key] != fingerprint:
                raise WorkflowInvariantError(
                    "proposal run key reused with different evidence"
                )
            assert isinstance(existing, dict)
            return ProposalRunRecord.model_validate(existing)
        records[record.run_key] = record.model_dump(mode="json")
        fingerprints = state["fingerprints"]
        assert isinstance(fingerprints, dict)
        fingerprints[record.run_key] = fingerprint
        self._store(state)
        return record


class ProposalRunDriver(Protocol):
    """Execute one governed proposal run and return its record."""

    def run(self, request: ProposalRunInput, *, at: str) -> ProposalRunRecord: ...


class GovernedProposalRunner:
    """Compose S2-04 retrieval and the S3-02 gateway into one proposal run.

    The runner is the injected activity driver: it performs every I/O and
    model effect, so workflow code stays deterministic. It writes exactly
    one proposal ledger record and mutates no other state.
    """

    def __init__(
        self,
        *,
        pinned_contexts: PinnedContextSource,
        retrieval: RetrievalOrchestrator,
        gateway: ModelGateway,
        ledger: ProposalLedger,
    ) -> None:
        self._pinned_contexts = pinned_contexts
        self._retrieval = retrieval
        self._gateway = gateway
        self._ledger = ledger

    def run(self, request: ProposalRunInput, *, at: str) -> ProposalRunRecord:
        existing = self._ledger.find(request.run_key)
        if existing is not None:
            return existing

        pinned = self._pinned_contexts.resolve(request.context_pin_id)
        context = self._retrieve_pinned(pinned)
        observed = self._validate_source_links(context, pinned)

        proposal = self._submit(request, pinned, context)

        verification = self._retrieve_pinned(pinned)
        verified = self._validate_source_links(verification, pinned)
        if verified != observed:
            raise SourceLinkValidationError(
                "pinned source inventory changed during the run"
            )

        return self._ledger.record(
            _build_record(request, pinned, context, proposal, at=at)
        )

    def _retrieve_pinned(self, pinned: PinnedProposalContext) -> RetrievalResult:
        try:
            return self._retrieval.retrieve(pinned.request)
        except RetrievalAuthorizationError as exc:
            raise PolicyDeniedError(
                "governed proposal retrieval authorization denied"
            ) from exc
        except RetrievalError as exc:
            raise ContextRetrievalUnavailableError(
                "governed proposal retrieval backend is unavailable"
            ) from exc
        except Exception as exc:
            raise ContextRetrievalUnavailableError(
                "governed proposal retrieval failed closed"
            ) from exc

    def _validate_source_links(
        self,
        result: RetrievalResult,
        pinned: PinnedProposalContext,
    ) -> dict[str, str]:
        trace = _trace_pins(result)
        if not trace:
            raise SourceLinkValidationError(
                "retrieval returned no pinned sources for the governed run"
            )
        expected = {pin.source_id: pin.source_sha256 for pin in pinned.sources}
        if set(trace) != set(expected):
            raise SourceLinkValidationError(
                "retrieved source links do not match the pinned source inventory"
            )
        for source_id, source_hash in expected.items():
            if trace[source_id] != source_hash:
                raise SourceLinkValidationError(
                    "retrieved source hash disagrees with the pinned inventory"
                )
        return trace

    def _submit(
        self,
        request: ProposalRunInput,
        pinned: PinnedProposalContext,
        context: RetrievalResult,
    ) -> Proposal:
        gateway_request = ProposalRequest(
            request_id=request.run_key,
            tenant_id=pinned.tenant_ref,
            matter_id=pinned.matter_ref,
            route_id=request.route_id,
            purpose=request.purpose,
            classification=request.classification,
            prompt_inputs={
                _PROMPT_INSTRUCTION_KEY: request.instructions,
                _PROMPT_CONTEXT_KEY: _compose_context(context),
            },
            workflow_run_id=request.run_key,
        )
        try:
            return self._gateway.submit(
                gateway_request, capability_ref=request.capability_ref
            )
        except (
            ProviderUnavailableError,
            ModelTimeoutError,
            ModelCancelledError,
            ProviderSaturationError,
            CapacityQueueTimeoutError,
            AuditUnavailableError,
        ) as exc:
            raise ModelUnavailableError(
                f"model provider unavailable for governed proposal: {exc}"
            ) from exc
        except (SchemaValidationError, ProviderContractError) as exc:
            raise ProposalOutputInvalidError(
                f"provider output failed the pinned contract: {exc}"
            ) from exc
        except (
            CapabilityDeniedError,
            EgressDeniedError,
            PolicyUnavailableError,
            SourceRightsDeniedError,
            FreeRouteDeniedError,
        ) as exc:
            raise PolicyDeniedError(
                f"governed proposal submission denied: {exc}"
            ) from exc
        except ModelGatewayError as exc:
            raise WorkflowInvariantError(
                f"governed proposal route contract violated: {exc}"
            ) from exc


def _trace_pins(result: RetrievalResult) -> dict[str, str]:
    provenance = result.provenance
    return dict(zip(provenance.source_ids, provenance.source_hashes, strict=True))


def _compose_context(result: RetrievalResult) -> str:
    """Render retrieved hits with explicit source markers for the prompt."""

    blocks = []
    for hit in result.hits:
        source = hit.source
        blocks.append(
            f"[{hit.rank}] source {source.source_id} "
            f"locator {source.source_locator} "
            f"sha256 {source.source_sha256}\n{hit.content}"
        )
    return "\n\n".join(blocks)


def _build_record(
    request: ProposalRunInput,
    pinned: PinnedProposalContext,
    context: RetrievalResult,
    proposal: Proposal,
    *,
    at: str,
) -> ProposalRunRecord:
    usage = proposal.evidence.token_usage
    return ProposalRunRecord(
        run_key=request.run_key,
        proposal_id=proposal.proposal_id,
        gateway_request_id=proposal.request_id,
        route_id=proposal.route_id,
        provider=str(proposal.provider),
        model_name=proposal.model.name,
        model_revision=proposal.model.revision,
        prompt_template_id=proposal.prompt_template_id,
        prompt_template_sha256=proposal.prompt_template_sha256,
        output_schema_id=proposal.output_schema_id,
        output_schema_sha256=proposal.output_schema_sha256,
        registry_revision=proposal.registry_revision,
        payload_sha256=proposal.payload_sha256,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        egress_rule=proposal.policy.egress_rule,
        policy_revision=proposal.policy.policy_revision,
        classification=str(proposal.policy.classification),
        retrieval_request_sha256=pinned.request.canonical_sha256(),
        release_id=context.release_id,
        projection_generation=context.projection_generation,
        retrieval_adapter_version=context.provenance.retrieval_adapter_version,
        context_sources=tuple(
            SourcePin(source_id=source_id, source_sha256=source_hash)
            for source_id, source_hash in _trace_pins(context).items()
        ),
        context_hit_count=len(context.hits),
        validated_at=at,
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GovernedProposalActivities:
    """Temporal activity shim for one governed proposal run.

    Every retrieval, gateway, and ledger effect belongs to the injected
    driver, which runs synchronously here exactly like a replayed activity
    worker would replay it. The returned outcome carries only identifiers
    and digests, so the durable proposal evidence stays in the ledger and
    out of workflow history.
    """

    def __init__(
        self,
        *,
        driver: ProposalRunDriver,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._driver = driver
        self._clock = clock

    @activity.defn
    async def run_governed_proposal(
        self, request: ProposalRunInput
    ) -> ProposalRunOutcome:
        """Run one governed proposal through the injected driver."""

        at = self._clock()
        record = self._driver.run(request, at=at.isoformat())
        return ProposalRunOutcome(
            run_key=record.run_key,
            proposal_id=record.proposal_id,
            record_digest=proposal_record_fingerprint(record),
            recorded_at=at,
        )
