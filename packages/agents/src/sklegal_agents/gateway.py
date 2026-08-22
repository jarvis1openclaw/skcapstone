"""CapAuth-mediated domain tool gateway with schema validation and budgets.

Every tool call passes through the same fail-closed pipeline:

1. The tool must be allowlisted by the run's pinned AgentSpec version.
2. Run budgets (tool calls and wall clock) must not be exhausted.
3. Arguments must validate against the tool's pinned input schema artifact.
4. A scoped CapAuth capability must authorize the call at invocation time;
   revocation and backend outages deny by default through the authorizer.
5. Only then does the handler run, and its result must validate against the
   tool's pinned output schema artifact before anything is returned.

Handlers receive a ToolCallContext containing only sanitized decision
fields. The presented credential never reaches a handler, a run record, or
a model-facing payload, so no model ever sees raw credential material.
Scope (tenant, matter, workflow run) comes from the trusted run context
opened by deterministic code; tool arguments cannot redirect it because
the pinned input schemas forbid those fields. No general shell or
arbitrary network tool exists in the catalog, and the gateway refuses to
register handlers for anything outside it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError as JsonValidationError
from pydantic import Field
from sklegal_capauth import (
    BoundaryScope,
    CapabilityAuthorizer,
    PresentedCapability,
    PrincipalContext,
    ToolCapabilityBoundary,
)

from .contracts import TOOL_CONTRACTS, ToolContract, tool_contract
from .errors import (
    RunInputValidationError,
    ToolArgumentValidationError,
    ToolBudgetExhaustedError,
    ToolGatewayError,
    ToolHandlerUnavailableError,
    ToolNotAllowlistedError,
    ToolResultValidationError,
    ToolSchemaIntegrityError,
    UnknownToolError,
)
from .models import AgentSpecRecord, AgentSpecValue, SchemaPin, ShortCode
from .registry import AgentSpecRegistry
from .tools import KNOWN_TOOL_IDS

MAX_WORKFLOW_RUN_ID_LENGTH = 160


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ToolCallContext(AgentSpecValue):
    """Sanitized invocation context handed to a tool handler.

    Carries only safe decision and scope fields. Raw credential material is
    never present: the digest is the same sanitized reference recorded in
    authorization decisions and audit events.
    """

    spec_id: ShortCode
    spec_version: int = Field(ge=1)
    tool_id: ShortCode
    tenant_id: UUID
    matter_id: UUID
    workflow_run_id: str = Field(min_length=1, max_length=MAX_WORKFLOW_RUN_ID_LENGTH)
    principal_id: UUID
    correlation_id: UUID
    decision_id: UUID
    credential_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ToolCallRecord(AgentSpecValue):
    """Immutable record of one authorized, validated tool call."""

    tool_id: ShortCode
    decision_id: UUID
    correlation_id: UUID
    arguments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: dict[str, Any]


class ToolHandler(Protocol):
    """Deterministic handler for one known tool. Never receives credentials."""

    def __call__(
        self,
        *,
        arguments: Mapping[str, Any],
        context: ToolCallContext,
    ) -> Mapping[str, Any]:
        """Execute the tool within the authorized scope and return a result."""


class AgentRun:
    """Per-run scope and budget ledger. Created only by ToolGateway."""

    def __init__(
        self,
        *,
        gateway: ToolGateway,
        record: AgentSpecRecord,
        principal: PrincipalContext,
        tenant_id: UUID,
        matter_id: UUID,
        workflow_run_id: str,
        started_at: datetime,
    ) -> None:
        self._gateway = gateway
        self._record = record
        self._principal = principal
        self._tenant_id = tenant_id
        self._matter_id = matter_id
        self._workflow_run_id = workflow_run_id
        self._started_at = started_at
        self._remaining_tool_calls = record.spec.budgets.max_tool_calls
        self._records: list[ToolCallRecord] = []

    @property
    def record(self) -> AgentSpecRecord:
        return self._record

    @property
    def gateway(self) -> ToolGateway:
        return self._gateway

    @property
    def principal(self) -> PrincipalContext:
        return self._principal

    @property
    def tenant_id(self) -> UUID:
        return self._tenant_id

    @property
    def matter_id(self) -> UUID:
        return self._matter_id

    @property
    def workflow_run_id(self) -> str:
        return self._workflow_run_id

    @property
    def remaining_tool_calls(self) -> int:
        return self._remaining_tool_calls

    @property
    def call_records(self) -> tuple[ToolCallRecord, ...]:
        return tuple(self._records)

    def _check_wall_clock(self, now: datetime) -> None:
        elapsed = (now - self._started_at).total_seconds()
        if elapsed > self._record.spec.budgets.max_wall_clock_seconds:
            raise ToolBudgetExhaustedError(
                f"run exceeded its wall clock budget: {self._record.spec.spec_id}"
            )

    def _check_call_budget(self) -> None:
        if self._remaining_tool_calls <= 0:
            raise ToolBudgetExhaustedError(
                f"run exhausted its tool call budget: {self._record.spec.spec_id}"
            )

    def _consume_call(self) -> None:
        self._check_call_budget()
        self._remaining_tool_calls -= 1

    def _append(self, record: ToolCallRecord) -> None:
        self._records.append(record)


class ToolGateway:
    """Fail-closed gateway mediating every domain tool call for agent runs."""

    def __init__(
        self,
        *,
        registry: AgentSpecRegistry,
        authorizer: CapabilityAuthorizer,
        schemas_dir: Path,
        handlers: Mapping[str, ToolHandler],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        unknown = sorted(set(handlers) - KNOWN_TOOL_IDS)
        if unknown:
            raise UnknownToolError(
                f"handlers cannot be registered outside the known catalog: "
                f"{', '.join(unknown)}"
            )
        self._registry = registry
        self._authorizer = authorizer
        self._schemas_dir = Path(schemas_dir)
        self._handlers = dict(handlers)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._artifact_index = self._index_artifacts()
        self._validators: dict[
            str, tuple[Draft202012Validator, Draft202012Validator]
        ] = {}
        self._boundaries: dict[str, ToolCapabilityBoundary[Mapping[str, Any]]] = {}
        for contract in TOOL_CONTRACTS:
            self._load_contract(contract)

    def _index_artifacts(self) -> dict[str, Path]:
        index: dict[str, Path] = {}
        for path in sorted(self._schemas_dir.glob("*.schema.json")):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ToolSchemaIntegrityError(
                    f"schema artifact is unreadable: {path.name}"
                ) from exc
            if not isinstance(document, dict) or not isinstance(
                document.get("$id"), str
            ):
                raise ToolSchemaIntegrityError(
                    f"schema artifact is missing its $id: {path.name}"
                )
            schema_id = document["$id"]
            if schema_id in index:
                raise ToolSchemaIntegrityError(
                    f"duplicate schema id across artifacts: {schema_id}"
                )
            index[schema_id] = path
        return index

    def _pinned_validator(
        self,
        pin: SchemaPin,
        *,
        artifact_name: str | None = None,
    ) -> Draft202012Validator:
        path = (
            self._schemas_dir / artifact_name
            if artifact_name is not None
            else self._artifact_index.get(pin.schema_id)
        )
        if path is None or not path.is_file():
            raise ToolSchemaIntegrityError(
                f"pinned schema artifact is missing: {pin.schema_id}"
            )
        text = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != pin.sha256:
            raise ToolSchemaIntegrityError(
                f"schema artifact hash does not match its pin: {path.name}"
            )
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolSchemaIntegrityError(
                f"schema artifact is not valid JSON: {path.name}"
            ) from exc
        if not isinstance(document, dict) or document.get("$id") != pin.schema_id:
            raise ToolSchemaIntegrityError(
                f"schema artifact id does not match its pin: {path.name}"
            )
        try:
            Draft202012Validator.check_schema(document)
        except SchemaError as exc:
            raise ToolSchemaIntegrityError(
                f"schema artifact is not a valid JSON Schema: {path.name}"
            ) from exc
        return Draft202012Validator(document)

    def _load_contract(self, contract: ToolContract) -> None:
        input_validator = self._pinned_validator(
            contract.input_schema, artifact_name=contract.input_artifact
        )
        output_validator = self._pinned_validator(
            contract.output_schema, artifact_name=contract.output_artifact
        )
        self._validators[contract.tool_id] = (input_validator, output_validator)
        self._boundaries[contract.tool_id] = ToolCapabilityBoundary(
            authorizer=self._authorizer,
            tool_name=contract.tool_id,
            capability=contract.capability,
            purpose=contract.purpose,
        )

    def begin_run(
        self,
        spec_id: str,
        *,
        version: int | None = None,
        principal: PrincipalContext,
        tenant_id: UUID,
        matter_id: UUID,
        workflow_run_id: str,
        run_input: Mapping[str, Any],
    ) -> AgentRun:
        """Open a run after validating input against the pinned spec schema.

        Scope fields come from trusted deterministic code, never from model
        or document content. The run input is validated against the spec's
        pinned input schema artifact before any tool call is possible.
        """

        record = self._registry.spec(spec_id, version)
        if not isinstance(run_input, Mapping):
            raise RunInputValidationError("run input must be a JSON object")
        validator = self._pinned_validator(record.spec.input_schema)
        try:
            validator.validate(run_input)
        except JsonValidationError as exc:
            raise RunInputValidationError(
                f"run input failed the pinned input schema: "
                f"{record.spec.input_schema.schema_id}"
            ) from exc
        return AgentRun(
            gateway=self,
            record=record,
            principal=principal,
            tenant_id=tenant_id,
            matter_id=matter_id,
            workflow_run_id=workflow_run_id,
            started_at=self._clock(),
        )

    def call(
        self,
        run: AgentRun,
        tool_id: str,
        arguments: Mapping[str, Any],
        *,
        principal: PrincipalContext,
        presented: PresentedCapability | None,
        correlation_id: UUID,
    ) -> ToolCallRecord:
        """Authorize, validate, execute, and record exactly one tool call."""

        if not isinstance(run, AgentRun) or run._gateway is not self:
            raise ToolGatewayError("run was not opened by this gateway")
        if principal != run.principal:
            raise ToolGatewayError("tool call principal does not match the run")
        tool_contract(tool_id)  # unknown tools fail closed before the allowlist
        if tool_id not in run.record.spec.tool_allowlist:
            raise ToolNotAllowlistedError(
                f"tool is outside the run spec allowlist: {tool_id}"
            )
        run._check_wall_clock(self._clock())
        run._check_call_budget()
        input_validator, output_validator = self._validators[tool_id]
        if not isinstance(arguments, Mapping):
            raise ToolArgumentValidationError("tool arguments must be a JSON object")
        try:
            input_validator.validate(arguments)
        except JsonValidationError as exc:
            raise ToolArgumentValidationError(
                f"tool arguments failed the pinned input schema: {tool_id}"
            ) from exc
        scope = BoundaryScope(
            tenant_id=run.tenant_id,
            matter_id=run.matter_id,
            workflow_run_id=run.workflow_run_id,
        )
        authorized = self._boundaries[tool_id].authorize(
            principal=principal,
            scope=scope,
            correlation_id=correlation_id,
            presented=presented,
        )
        handler = self._handlers.get(tool_id)
        if handler is None:
            raise ToolHandlerUnavailableError(
                f"no handler is registered for an allowlisted tool: {tool_id}"
            )
        run._consume_call()
        digest = authorized.decision.credential_digest
        if digest is None:
            raise ToolGatewayError("authorized decision is missing its digest")
        context = ToolCallContext(
            spec_id=run.record.spec.spec_id,
            spec_version=run.record.spec.version,
            tool_id=tool_id,
            tenant_id=run.tenant_id,
            matter_id=run.matter_id,
            workflow_run_id=run.workflow_run_id,
            principal_id=principal.principal_id,
            correlation_id=correlation_id,
            decision_id=authorized.decision.decision_id,
            credential_digest=digest,
        )
        result = handler(arguments=arguments, context=context)
        if not isinstance(result, Mapping):
            raise ToolResultValidationError(
                f"tool result must be a JSON object: {tool_id}"
            )
        try:
            output_validator.validate(result)
        except JsonValidationError as exc:
            raise ToolResultValidationError(
                f"tool result failed the pinned output schema: {tool_id}"
            ) from exc
        record = ToolCallRecord(
            tool_id=tool_id,
            decision_id=authorized.decision.decision_id,
            correlation_id=correlation_id,
            arguments_sha256=_canonical_digest(arguments),
            result=dict(result),
        )
        run._append(record)
        return record
