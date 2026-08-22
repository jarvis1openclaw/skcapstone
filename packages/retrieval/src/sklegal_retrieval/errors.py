"""Sanitized typed errors for closed retrieval boundaries."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class RetrievalErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    AUTHORIZATION_DENIED = "authorization_denied"
    REGISTRY_UNAVAILABLE = "registry_unavailable"
    PROJECTION_UNAVAILABLE = "projection_unavailable"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    STALE_PROJECTION = "stale_projection"
    REVISION_CHANGED = "revision_changed"
    REPLICA_LAG = "replica_lag"
    INTEGRITY_FAILURE = "integrity_failure"


class RetrievalErrorEnvelope(BaseModel):
    """Uniform external failure shape that does not expose partition existence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    error: Literal["retrieval_unavailable"] = "retrieval_unavailable"
    message: Literal["Retrieval could not be completed."] = (
        "Retrieval could not be completed."
    )
    request_id: UUID | None = None


class RetrievalError(RuntimeError):
    """Base internal retrieval error with a sanitized public representation."""

    code = RetrievalErrorCode.INTEGRITY_FAILURE

    def __init__(
        self, internal_message: str, *, request_id: UUID | None = None
    ) -> None:
        super().__init__(internal_message)
        self.request_id = request_id

    def public_error(self) -> RetrievalErrorEnvelope:
        return RetrievalErrorEnvelope(request_id=self.request_id)


class RetrievalRequestError(RetrievalError, ValueError):
    code = RetrievalErrorCode.INVALID_REQUEST


class RetrievalAuthorizationError(RetrievalError, PermissionError):
    code = RetrievalErrorCode.AUTHORIZATION_DENIED


class RetrievalUnavailableError(RetrievalError):
    code = RetrievalErrorCode.BACKEND_UNAVAILABLE


class RetrievalReplicaLagError(RetrievalUnavailableError):
    """A replica has not replayed the required watermark LSN."""

    code = RetrievalErrorCode.REPLICA_LAG


class RetrievalIntegrityError(RetrievalError):
    code = RetrievalErrorCode.INTEGRITY_FAILURE
