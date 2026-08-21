from __future__ import annotations

import json
import traceback
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sklegal_audit import (
    APPEND_AUDIT_EVENT_SQL,
    AuditAttributes,
    AuditBoundary,
    AuditEventDraft,
    AuditOutcome,
    AuditUnavailable,
    DurableAuditEvent,
    DurableAuditSink,
    InMemoryAuditLedger,
    LocalTelemetryBuffer,
    PostgresAuditRepository,
    RunCorrelation,
    TelemetrySpan,
    TelemetryStatus,
    TraceContextPropagator,
    WatermarkConflict,
    verify_event_chain,
)
from sklegal_audit.ledger import _canonical_timestamp
from sklegal_capauth import (
    Audience,
    AuthorizationDecision,
    Capability,
    DecisionReason,
    Operation,
    Purpose,
    ResourceType,
)
from sklegal_policies import (
    DataFlowBoundary,
    PolicyDecision,
    PolicyReason,
)

AT = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
TENANT_ID = UUID("a5000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("a5000000-0000-4000-8000-000000000002")
PRINCIPAL_ID = UUID("a5000000-0000-4000-8000-000000000003")
RESOURCE_ID = UUID("a5000000-0000-4000-8000-000000000004")
RUN_ID = UUID("a5000000-0000-4000-8000-000000000005")
CORRELATION_ID = UUID("a5000000-0000-4000-8000-000000000006")
AUTHORIZATION_DECISION_ID = UUID("a5000000-0000-4000-8000-000000000007")
POLICY_DECISION_ID = UUID("a5000000-0000-4000-8000-000000000008")
TRACE_ID = "1" * 32
SPAN_ID = "2" * 16
DIGEST = "a" * 64


class MutableClock:
    def __init__(self, current: datetime = AT) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, *, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class UnavailableRepository:
    def append(self, draft: AuditEventDraft) -> object:
        del draft
        try:
            raise RuntimeError("synthetic protected database payload")
        except RuntimeError as exc:
            raise RuntimeError("synthetic adapter wrapper") from exc


class AuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock()
        self.correlation = RunCorrelation(
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            trace_id=TRACE_ID,
            span_id=SPAN_ID,
            trace_flags="01",
        )

    def draft(
        self,
        *,
        event_number: int = 1,
        boundary: AuditBoundary = AuditBoundary.API,
        correlation: RunCorrelation | None = None,
    ) -> AuditEventDraft:
        return AuditEventDraft(
            event_id=UUID(f"a5000000-0000-4000-8000-{100 + event_number:012d}"),
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            principal_id=PRINCIPAL_ID,
            correlation=correlation or self.correlation,
            boundary=boundary,
            action=f"audit.synthetic.{boundary.value}",
            resource_kind="matter",
            resource_id=RESOURCE_ID,
            authorization_decision_id=AUTHORIZATION_DECISION_ID,
            policy_decision_id=POLICY_DECISION_ID,
            outcome=AuditOutcome.ALLOW,
            reason_code="allow",
            occurred_at=self.clock(),
            attributes=AuditAttributes(
                purpose="matter_management",
                operation="read",
                resource_version=1,
                resource_sha256=DIGEST,
                event_schema="sklegal-audit-event/v1",
            ),
        )

    def test_canonical_timestamp_has_one_explicit_ad_year_domain(self) -> None:
        for year in (1, 9, 99, 999, 1000, AT.year, 9999):
            value = datetime(year, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
            with self.subTest(year=year):
                self.assertEqual(
                    f"{year:04d}-01-02T03:04:05.123456Z",
                    _canonical_timestamp(value),
                )

        event = InMemoryAuditLedger(clock=self.clock).append(self.draft())
        payload = event.model_dump(mode="json")
        for invalid in (
            "0001-01-02 03:04:05.123456 BC",
            "10000-01-02T03:04:05.123456Z",
        ):
            invalid_payload = {**payload, "occurred_at": invalid}
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                DurableAuditEvent.model_validate(invalid_payload)

    def authorization_decision(
        self,
        *,
        decision_id: UUID = AUTHORIZATION_DECISION_ID,
        resource_id: str = str(RESOURCE_ID),
    ) -> AuthorizationDecision:
        return AuthorizationDecision(
            decision_id=decision_id,
            correlation_id=CORRELATION_ID,
            allow=True,
            reason_code=DecisionReason.ALLOW,
            credential_digest="b" * 64,
            principal_id=PRINCIPAL_ID,
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            capability=Capability.MATTER_READ,
            audience=Audience.API,
            target="api:matter.read",
            resource_type=ResourceType.MATTER,
            resource_id=resource_id,
            resource_version=1,
            resource_sha256=DIGEST,
            operation=Operation.READ,
            purpose=Purpose.MATTER_MANAGEMENT,
        )

    def policy_decision(
        self,
        *,
        decision_id: UUID = POLICY_DECISION_ID,
        boundary: DataFlowBoundary = DataFlowBoundary.RETRIEVAL,
    ) -> PolicyDecision:
        return PolicyDecision(
            decision_id=decision_id,
            capauth_decision_id=AUTHORIZATION_DECISION_ID,
            correlation_id=CORRELATION_ID,
            principal_id=PRINCIPAL_ID,
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            material_id=RESOURCE_ID,
            material_version=1,
            boundary=boundary,
            allow=True,
            reason=PolicyReason.ALLOW,
            policy_revision="c" * 64,
            evaluated_at=AT,
        )

    def test_run_context_propagates_one_trace_across_all_boundaries(self) -> None:
        headers = TraceContextPropagator.inject(self.correlation)
        self.assertEqual(
            f"00-{TRACE_ID}-{SPAN_ID}-01",
            headers["traceparent"],
        )
        extracted = TraceContextPropagator.extract(
            headers,
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
        )
        self.assertEqual(self.correlation, extracted)

        boundaries = tuple(AuditBoundary)
        spans = tuple(
            self.correlation.child(span_id=f"{index:016x}")
            for index, _ in enumerate(boundaries, start=1)
        )
        self.assertEqual({RUN_ID}, {span.run_id for span in spans})
        self.assertEqual({CORRELATION_ID}, {span.correlation_id for span in spans})
        self.assertEqual({TRACE_ID}, {span.trace_id for span in spans})
        self.assertEqual(len(boundaries), len({span.span_id for span in spans}))

        for invalid in (
            "00-00000000000000000000000000000000-2222222222222222-01",
            "00-11111111111111111111111111111111-0000000000000000-01",
            "ff-11111111111111111111111111111111-2222222222222222-01",
            "00-1111-2222-01",
        ):
            with self.subTest(traceparent=invalid):
                with self.assertRaises(ValueError):
                    TraceContextPropagator.extract(
                        {"traceparent": invalid},
                        run_id=RUN_ID,
                        correlation_id=CORRELATION_ID,
                    )

    def test_closed_attributes_reject_protected_fields_and_nested_payloads(
        self,
    ) -> None:
        protected = {
            "prompt": "synthetic privileged prompt",
            "source_document": "synthetic source content",
            "authorization": "Bearer synthetic-secret",
            "capability_token": "synthetic-token",
            "tool_arguments": {"path": "synthetic"},
            "model_output": ["synthetic"],
        }
        for key, value in protected.items():
            with self.subTest(field=key):
                with self.assertRaises(ValidationError):
                    AuditAttributes.model_validate({key: value})
        with self.assertRaises(ValidationError):
            AuditAttributes(error_code="unsafe value with whitespace")

        serialized = self.draft().model_dump_json()
        for sentinel in protected.values():
            if isinstance(sentinel, str):
                self.assertNotIn(sentinel, serialized)

        exact_limit = self.draft().model_dump(mode="python")
        exact_limit["resource_kind"] = "r" * 100
        AuditEventDraft.model_validate(exact_limit)
        exact_limit["resource_kind"] = "r" * 101
        with self.assertRaises(ValidationError):
            AuditEventDraft.model_validate(exact_limit)
        with self.assertRaises(ValidationError):
            AuditAttributes(operation="READ")

    def test_unvalidated_copy_replace_construct_and_telemetry_input_are_closed(
        self,
    ) -> None:
        draft = self.draft()
        with self.assertRaises(ValueError):
            draft.model_copy(update={"resource_kind": "r" * 101})
        with self.assertRaises(ValueError):
            draft.copy(update={"resource_kind": "r" * 101})
        with self.assertRaises(ValueError):
            draft.copy(include={"tenant_id"})
        with self.assertRaises(ValueError):
            draft.__replace__(resource_kind="r" * 101)
        with self.assertRaises(ValueError):
            AuditEventDraft.model_construct(
                **{
                    **draft.model_dump(mode="python"),
                    "resource_kind": "r" * 101,
                }
            )

        span = TelemetrySpan(
            correlation=self.correlation,
            boundary=AuditBoundary.API,
            name="sklegal.api",
            status=TelemetryStatus.OK,
            started_at=self.clock(),
            ended_at=self.clock(),
        )
        object.__setattr__(
            span,
            "attributes",
            {"prompt": "synthetic protected telemetry payload"},
        )
        buffer = LocalTelemetryBuffer(
            retention=timedelta(seconds=60),
            max_spans=8,
            clock=self.clock,
        )
        with self.assertRaises(ValidationError):
            buffer.record(span)
        self.assertEqual((), buffer.export_local())

    def test_append_only_chain_replays_all_six_boundaries_and_detects_tamper(
        self,
    ) -> None:
        ledger = InMemoryAuditLedger(clock=self.clock)
        events = []
        for index, boundary in enumerate(AuditBoundary, start=1):
            child = self.correlation.child(span_id=f"{index:016x}")
            events.append(
                ledger.append(
                    self.draft(
                        event_number=index,
                        boundary=boundary,
                        correlation=child,
                    )
                )
            )

        replay = ledger.replay(tenant_id=TENANT_ID, run_id=RUN_ID)
        self.assertEqual(tuple(events), replay)
        self.assertEqual(tuple(range(1, 7)), tuple(e.event_sequence for e in replay))
        self.assertTrue(verify_event_chain(replay))
        self.assertIsNone(replay[0].previous_event_sha256)
        self.assertEqual(
            tuple(event.event_sha256 for event in replay[:-1]),
            tuple(event.previous_event_sha256 for event in replay[1:]),
        )

        payload = replay[2].model_dump(mode="python")
        payload["action"] = "audit.synthetic.tampered"
        tampered = type(replay[2]).model_validate(payload)
        altered = (*replay[:2], tampered, *replay[3:])
        self.assertFalse(verify_event_chain(altered))

    def test_decision_sinks_preserve_exact_safe_references_and_sanitize_failure(
        self,
    ) -> None:
        ledger = InMemoryAuditLedger(clock=self.clock)
        sink = DurableAuditSink(
            repository=ledger,
            correlation=self.correlation,
            boundary=AuditBoundary.API,
            clock=self.clock,
        )
        sink.record(self.authorization_decision())
        sink.record(self.policy_decision())
        replay = ledger.replay(tenant_id=TENANT_ID, run_id=RUN_ID)
        self.assertEqual(2, len(replay))
        self.assertEqual(
            AUTHORIZATION_DECISION_ID,
            replay[0].authorization_decision_id,
        )
        self.assertIsNone(replay[0].policy_decision_id)
        self.assertEqual(
            AUTHORIZATION_DECISION_ID,
            replay[1].authorization_decision_id,
        )
        self.assertEqual(POLICY_DECISION_ID, replay[1].policy_decision_id)
        rendered = json.dumps(
            [event.model_dump(mode="json") for event in replay], sort_keys=True
        )
        self.assertNotIn("credential_digest", rendered)
        self.assertNotIn("ancestor_credential", rendered)

        unavailable = DurableAuditSink(
            repository=UnavailableRepository(),
            correlation=self.correlation,
            boundary=AuditBoundary.API,
            clock=self.clock,
        )
        try:
            unavailable.record(self.authorization_decision())
        except AuditUnavailable as exc:
            rendered_trace = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            self.assertIsNone(exc.__cause__)
            self.assertIsNone(exc.__context__)
            self.assertNotIn("synthetic protected database payload", rendered_trace)
            self.assertNotIn("synthetic adapter wrapper", rendered_trace)
        else:
            self.fail("unavailable durable audit repository did not fail closed")

    def test_decision_mapping_preserves_opaque_resource_and_policy_boundary(
        self,
    ) -> None:
        ledger = InMemoryAuditLedger(clock=self.clock)
        sink = DurableAuditSink(
            repository=ledger,
            correlation=self.correlation,
            boundary=AuditBoundary.API,
            clock=self.clock,
        )
        non_uuid_resource = "matter:synthetic-external-reference"
        non_uuid_decision_id = UUID("a5000000-0000-4000-8000-000000000009")
        sink.record(self.authorization_decision())
        sink.record(
            self.authorization_decision(
                decision_id=non_uuid_decision_id,
                resource_id=non_uuid_resource,
            )
        )
        replay = ledger.replay(tenant_id=TENANT_ID, run_id=RUN_ID)
        self.assertEqual(RESOURCE_ID, replay[0].resource_id)
        self.assertEqual(str(RESOURCE_ID), replay[0].attributes.resource_identity)
        self.assertIsNone(replay[1].resource_id)
        self.assertEqual(
            non_uuid_resource,
            replay[1].attributes.resource_identity,
        )
        for event in replay:
            reconstructed = type(event).model_validate_json(event.model_dump_json())
            self.assertEqual(event, reconstructed)

        policy_ledger = InMemoryAuditLedger(clock=self.clock)
        policy_sink = DurableAuditSink(
            repository=policy_ledger,
            correlation=self.correlation,
            boundary=AuditBoundary.WORKFLOW,
            clock=self.clock,
        )
        for index, boundary in enumerate(DataFlowBoundary, start=1):
            policy_sink.record(
                self.policy_decision(
                    decision_id=UUID(f"a5000000-0000-4000-8000-{200 + index:012d}"),
                    boundary=boundary,
                )
            )
        policy_replay = policy_ledger.replay(tenant_id=TENANT_ID, run_id=RUN_ID)
        self.assertEqual(
            tuple(boundary.value for boundary in DataFlowBoundary),
            tuple(event.attributes.policy_boundary for event in policy_replay),
        )
        self.assertEqual(
            {str(RESOURCE_ID)},
            {event.attributes.resource_identity for event in policy_replay},
        )
        for event in policy_replay:
            reconstructed = type(event).model_validate_json(event.model_dump_json())
            self.assertEqual(event, reconstructed)

    def test_duplicate_outbox_delivery_and_projection_replay_are_idempotent(
        self,
    ) -> None:
        ledger = InMemoryAuditLedger(clock=self.clock)
        first_event = ledger.append(self.draft(event_number=1))
        second_event = ledger.append(self.draft(event_number=2))
        messages = ledger.pending_outbox(tenant_id=TENANT_ID)
        self.assertEqual(2, len(messages))
        delivered: list[UUID] = []

        first = ledger.deliver(
            outbox_id=messages[0].outbox_id,
            destination="audit.local",
            handler=lambda message: delivered.append(message.event_id),
        )
        duplicate = ledger.deliver(
            outbox_id=messages[0].outbox_id,
            destination="audit.local",
            handler=lambda message: delivered.append(message.event_id),
        )
        self.assertTrue(first.first_delivery)
        self.assertFalse(duplicate.first_delivery)
        self.assertEqual([first_event.event_id], delivered)
        self.assertEqual(first.idempotency_key, duplicate.idempotency_key)

        watermark = ledger.advance_watermark(
            tenant_id=TENANT_ID,
            projection="audit.local",
            expected_sequence=0,
            expected_event_sha256=None,
            new_sequence=second_event.event_sequence,
            new_event_sha256=second_event.event_sha256,
        )
        self.assertEqual(second_event.event_sequence, watermark.event_sequence)
        same = ledger.advance_watermark(
            tenant_id=TENANT_ID,
            projection="audit.local",
            expected_sequence=second_event.event_sequence,
            expected_event_sha256=second_event.event_sha256,
            new_sequence=second_event.event_sequence,
            new_event_sha256=second_event.event_sha256,
        )
        self.assertEqual(watermark, same)
        with self.assertRaises(WatermarkConflict):
            ledger.advance_watermark(
                tenant_id=TENANT_ID,
                projection="audit.local",
                expected_sequence=0,
                expected_event_sha256=None,
                new_sequence=first_event.event_sequence,
                new_event_sha256=first_event.event_sha256,
            )

    def test_telemetry_is_local_bounded_and_uses_trusted_retention_time(self) -> None:
        buffer = LocalTelemetryBuffer(
            retention=timedelta(seconds=60),
            max_spans=8,
            clock=self.clock,
        )
        for index, boundary in enumerate(AuditBoundary, start=1):
            correlation = self.correlation.child(span_id=f"{index:016x}")
            buffer.record(
                TelemetrySpan(
                    correlation=correlation,
                    boundary=boundary,
                    name=f"sklegal.{boundary.value}",
                    status=TelemetryStatus.OK,
                    started_at=self.clock(),
                    ended_at=self.clock(),
                    attributes=AuditAttributes(
                        event_schema="sklegal-telemetry-span/v1",
                        operation="read",
                    ),
                )
            )
        exported = buffer.export_local()
        self.assertEqual(6, len(exported))
        self.assertEqual({RUN_ID}, {span.correlation.run_id for span in exported})
        serialized = json.dumps(
            [span.model_dump(mode="json") for span in exported], sort_keys=True
        )
        for forbidden in (
            "prompt",
            "source_document",
            "authorization",
            "capability_token",
            "tool_arguments",
            "model_output",
        ):
            self.assertNotIn(forbidden, serialized)

        self.clock.advance(seconds=61)
        self.assertEqual((), buffer.export_local())

    def test_telemetry_exports_are_independent_revalidated_snapshots(self) -> None:
        span = TelemetrySpan(
            correlation=self.correlation,
            boundary=AuditBoundary.API,
            name="sklegal.api",
            status=TelemetryStatus.OK,
            started_at=self.clock(),
            ended_at=self.clock(),
            attributes=AuditAttributes(operation="read"),
        )
        buffer = LocalTelemetryBuffer(
            retention=timedelta(seconds=60),
            max_spans=8,
            clock=self.clock,
        )
        buffer.record(span)

        first = buffer.export_local()
        second = buffer.export_local()
        self.assertIsNot(first[0], second[0])
        self.assertIsNot(first[0].attributes, second[0].attributes)
        object.__setattr__(
            first[0],
            "attributes",
            {"prompt": "synthetic poisoned exported telemetry"},
        )
        later = buffer.export_local()
        self.assertEqual("read", later[0].attributes.operation)
        self.assertNotIn("prompt", later[0].model_dump_json())

        stored = buffer._spans[0][1]
        object.__setattr__(
            stored,
            "attributes",
            {"prompt": "synthetic poisoned stored telemetry"},
        )
        with self.assertRaises(AuditUnavailable):
            buffer.export_local()

    def test_postgres_adapter_is_parameterized_strict_and_sanitized(self) -> None:
        expected = InMemoryAuditLedger(clock=self.clock).append(self.draft())
        calls: list[tuple[str, tuple[object, ...]]] = []

        def executor(sql: str, parameters: tuple[object, ...]) -> dict[str, Any]:
            calls.append((sql, parameters))
            return {"event": expected.model_dump(mode="json")}

        repository = PostgresAuditRepository(executor)
        self.assertEqual(expected, repository.append(self.draft()))
        self.assertEqual(APPEND_AUDIT_EVENT_SQL, calls[0][0])
        self.assertEqual(19, len(calls[0][1]))
        serialized_parameters = json.dumps(calls[0][1], default=str)
        self.assertNotIn("prompt", serialized_parameters)
        self.assertNotIn("source_document", serialized_parameters)

        def unavailable(sql: str, parameters: tuple[object, ...]) -> dict[str, Any]:
            del sql, parameters
            try:
                raise RuntimeError("synthetic nested driver payload")
            except RuntimeError as exc:
                raise RuntimeError("synthetic driver wrapper") from exc

        try:
            PostgresAuditRepository(unavailable).append(self.draft())
        except AuditUnavailable as exc:
            rendered_trace = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            self.assertIsNone(exc.__cause__)
            self.assertIsNone(exc.__context__)
            self.assertNotIn("synthetic nested driver payload", rendered_trace)
            self.assertNotIn("synthetic driver wrapper", rendered_trace)
        else:
            self.fail("PostgreSQL audit adapter did not fail closed")


if __name__ == "__main__":
    unittest.main()
