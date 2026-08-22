"""Typed workflow errors with explicit retry semantics.

Error type names are part of the retry contract: retry.py maps these names
onto Temporal non-retryable error types, and the deterministic state machine
in state.py uses them to decide whether a failure retries, compensates, or
terminates a run.
"""


class WorkflowError(Exception):
    """Base class for all workflow-foundation errors."""


class PolicyDeniedError(WorkflowError):
    """A policy decision denied the step, or no decision was available.

    The platform fails closed: a missing or unavailable policy decision is
    treated as a denial and is never retried automatically.
    """


class ApprovalRejectedError(WorkflowError):
    """A human reviewer rejected the gated work product or dispatch."""


class ModelUnavailableError(WorkflowError):
    """The routed model provider is unavailable; safe to retry later."""


class ProposalOutputInvalidError(WorkflowError):
    """Provider output failed the pinned typed-output contract.

    Raised when the model gateway rejects provider output against the
    pinned output schema. A later attempt may produce valid output, so
    the failure retries only through the pinned retry class budget.
    """


class SourceLinkValidationError(WorkflowError):
    """A proposal citation did not resolve to the pinned retrieval context.

    Raised when a provider output cites no source, cites a source absent
    from the pinned trace, cites a hash that disagrees with the trace, or
    when the pinned source inventory changes during the run. The run fails
    closed and the proposal is never recorded as accepted.
    """


class AdmissionRejectedError(WorkflowError):
    """The long-context admission gate has no free slot for the run."""


class ContextRetrievalUnavailableError(WorkflowError):
    """The pinned context retrieval backend is unavailable; retry later."""


class WorkflowInvariantError(WorkflowError):
    """A deterministic state-machine invariant was violated."""
