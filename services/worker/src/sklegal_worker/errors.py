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


class AdmissionRejectedError(WorkflowError):
    """The long-context admission gate has no free slot for the run."""


class WorkflowInvariantError(WorkflowError):
    """A deterministic state-machine invariant was violated."""
