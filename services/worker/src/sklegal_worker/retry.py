"""Retry classes for typed activities.

Each retry class pins a Temporal retry policy. Policy denials, approval
rejections, and state-machine invariant violations are never retried: they
fail closed. Model outages retry with a bounded attempt budget, and the
human class never retries automatically because progress waits on signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio.common import RetryPolicy

from .models import RetryClass

BASE_NON_RETRYABLE_ERROR_TYPES = frozenset(
    {
        "PolicyDeniedError",
        "ApprovalRejectedError",
        "WorkflowInvariantError",
    }
)


@dataclass(frozen=True, slots=True)
class RetrySpec:
    """Provider-neutral retry contract for one retry class."""

    retry_class: RetryClass
    initial_interval: timedelta
    backoff_coefficient: float
    maximum_interval: timedelta
    maximum_attempts: int
    non_retryable_error_types: frozenset[str] = BASE_NON_RETRYABLE_ERROR_TYPES


RETRY_SPECS: dict[RetryClass, RetrySpec] = {
    RetryClass.INTERACTIVE: RetrySpec(
        retry_class=RetryClass.INTERACTIVE,
        initial_interval=timedelta(seconds=1),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=30),
        maximum_attempts=3,
    ),
    RetryClass.BATCH: RetrySpec(
        retry_class=RetryClass.BATCH,
        initial_interval=timedelta(seconds=5),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(minutes=5),
        maximum_attempts=5,
    ),
    RetryClass.LONG_CONTEXT: RetrySpec(
        retry_class=RetryClass.LONG_CONTEXT,
        initial_interval=timedelta(seconds=5),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(minutes=2),
        maximum_attempts=4,
    ),
    RetryClass.CONNECTOR: RetrySpec(
        retry_class=RetryClass.CONNECTOR,
        initial_interval=timedelta(seconds=10),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(minutes=5),
        maximum_attempts=6,
    ),
    RetryClass.MODEL: RetrySpec(
        retry_class=RetryClass.MODEL,
        initial_interval=timedelta(seconds=2),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=30),
        maximum_attempts=4,
    ),
    RetryClass.HUMAN: RetrySpec(
        retry_class=RetryClass.HUMAN,
        initial_interval=timedelta(seconds=1),
        backoff_coefficient=1.0,
        maximum_interval=timedelta(seconds=1),
        maximum_attempts=1,
    ),
}


def retry_spec_for(retry_class: RetryClass) -> RetrySpec:
    return RETRY_SPECS[retry_class]


def is_retryable(retry_class: RetryClass, error_type: str) -> bool:
    """Whether an activity error with this type name may be retried."""
    return error_type not in retry_spec_for(retry_class).non_retryable_error_types


def attempts_exhausted(retry_class: RetryClass, attempts: int) -> bool:
    """Whether the attempt count has reached the class budget."""
    spec = retry_spec_for(retry_class)
    if spec.maximum_attempts == 0:
        return False
    return attempts >= spec.maximum_attempts


def retry_policy_for(retry_class: RetryClass) -> RetryPolicy:
    """Build the Temporal retry policy for one retry class."""
    spec = retry_spec_for(retry_class)
    return RetryPolicy(
        initial_interval=spec.initial_interval,
        backoff_coefficient=spec.backoff_coefficient,
        maximum_interval=spec.maximum_interval,
        maximum_attempts=spec.maximum_attempts,
        non_retryable_error_types=sorted(spec.non_retryable_error_types),
    )
