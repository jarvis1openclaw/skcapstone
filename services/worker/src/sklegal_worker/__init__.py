"""SKLegal Temporal worker foundations: queues, workflows, activities."""

from typing import Any

from .activities import (
    ApprovalGate,
    DispatchLedger,
    FileDispatchLedger,
    InMemoryStaleAlertSink,
    SimulatedDispatchLedger,
    SimulatedStepDriver,
    StaleAlertSink,
    StaticApprovalGate,
    StepDriver,
    WorkerActivities,
)
from .errors import (
    AdmissionRejectedError,
    ApprovalRejectedError,
    ContextRetrievalUnavailableError,
    ModelUnavailableError,
    PolicyDeniedError,
    ProposalOutputInvalidError,
    SourceLinkValidationError,
    WorkflowError,
    WorkflowInvariantError,
)
from .models import (
    TERMINAL_PHASES,
    ApprovalRequirement,
    ApprovalSignal,
    DispatchReceipt,
    DispatchRequest,
    GovernedProposalInput,
    ProposalRunInput,
    ProposalRunOutcome,
    QueueKind,
    RetryClass,
    RunIdentity,
    RunPhase,
    Sha256Digest,
    Slug,
    SourcePin,
    StaleRunAlert,
    StepActivityInput,
    StepOutcome,
    StepRecord,
    TaskWorkflowInput,
    TaskWorkflowResult,
    UtcDateTime,
    WorkflowPayload,
)
from .queues import (
    INTERACTIVE_CONTEXT_TOKEN_LIMIT,
    LONG_CONTEXT_MAX_CONCURRENT,
    TASK_QUEUE_NAMES,
    AdmissionTicket,
    LongContextAdmission,
    select_queue,
    task_queue_name,
)
from .retry import (
    BASE_NON_RETRYABLE_ERROR_TYPES,
    RETRY_SPECS,
    RetrySpec,
    attempts_exhausted,
    is_retryable,
    retry_policy_for,
    retry_spec_for,
)
from .state import (
    EventKind,
    RunConfig,
    RunEvent,
    WorkflowRunMachine,
)
from .worker import (
    WORKER_DATA_CONVERTER,
    WorkerSpec,
    build_worker,
    connect_worker_client,
    worker_specs,
)
from .workflows import (
    ACTIVITY_RUN_GOVERNED_PROPOSAL,
    ConnectorDispatchWorkflow,
    GovernedProposalWorkflow,
    MatterBatchWorkflow,
    MatterTaskWorkflow,
)

PACKAGE_NAME = "sklegal-worker"

__all__ = [
    "ACTIVITY_RUN_GOVERNED_PROPOSAL",
    "BASE_NON_RETRYABLE_ERROR_TYPES",
    "INTERACTIVE_CONTEXT_TOKEN_LIMIT",
    "LONG_CONTEXT_MAX_CONCURRENT",
    "PACKAGE_NAME",
    "RETRY_SPECS",
    "TASK_QUEUE_NAMES",
    "TERMINAL_PHASES",
    "WORKER_DATA_CONVERTER",
    "AdmissionRejectedError",
    "AdmissionTicket",
    "ApprovalGate",
    "ApprovalRejectedError",
    "ApprovalRequirement",
    "ApprovalSignal",
    "ConnectorDispatchWorkflow",
    "ContextRetrievalUnavailableError",
    "DispatchLedger",
    "DispatchReceipt",
    "DispatchRequest",
    "EventKind",
    "FileDispatchLedger",
    "FileProposalLedger",
    "GovernedProposalActivities",
    "GovernedProposalInput",
    "GovernedProposalRunner",
    "GovernedProposalWorkflow",
    "InMemoryPinnedContextRegistry",
    "InMemoryProposalLedger",
    "InMemoryStaleAlertSink",
    "LongContextAdmission",
    "MatterBatchWorkflow",
    "MatterTaskWorkflow",
    "ModelUnavailableError",
    "PinnedContextSource",
    "PinnedProposalContext",
    "PolicyDeniedError",
    "ProposalLedger",
    "ProposalOutputInvalidError",
    "ProposalRunDriver",
    "ProposalRunInput",
    "ProposalRunOutcome",
    "ProposalRunRecord",
    "QueueKind",
    "RetryClass",
    "RetrySpec",
    "RunConfig",
    "RunEvent",
    "RunIdentity",
    "RunPhase",
    "Sha256Digest",
    "SimulatedDispatchLedger",
    "SimulatedStepDriver",
    "Slug",
    "SourceLinkValidationError",
    "SourcePin",
    "StaleAlertSink",
    "StaleRunAlert",
    "StaticApprovalGate",
    "StepActivityInput",
    "StepDriver",
    "StepOutcome",
    "StepRecord",
    "TaskWorkflowInput",
    "TaskWorkflowResult",
    "UtcDateTime",
    "WorkerActivities",
    "WorkerSpec",
    "WorkflowError",
    "WorkflowInvariantError",
    "WorkflowPayload",
    "WorkflowRunMachine",
    "attempts_exhausted",
    "build_worker",
    "connect_worker_client",
    "is_retryable",
    "proposal_record_fingerprint",
    "retry_policy_for",
    "retry_spec_for",
    "select_queue",
    "task_queue_name",
    "worker_specs",
]

# The governed proposal run imports sklegal_retrieval, whose contract loader
# touches the filesystem at import time. The Temporal sandbox executes this
# package __init__ when it validates any workflow class, and a restricted
# filesystem access there fails workflow validation. So proposal_run loads
# lazily on first attribute access, never during sandboxed workflow
# validation, while keeping the package-level export surface.
_LAZY_PROPOSAL_RUN_EXPORTS = frozenset(
    {
        "FileProposalLedger",
        "GovernedProposalActivities",
        "GovernedProposalRunner",
        "InMemoryPinnedContextRegistry",
        "InMemoryProposalLedger",
        "PinnedContextSource",
        "PinnedProposalContext",
        "ProposalLedger",
        "ProposalRunDriver",
        "ProposalRunRecord",
        "proposal_record_fingerprint",
    }
)


def __getattr__(name: str) -> Any:
    if name in _LAZY_PROPOSAL_RUN_EXPORTS:
        from . import proposal_run

        return getattr(proposal_run, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
