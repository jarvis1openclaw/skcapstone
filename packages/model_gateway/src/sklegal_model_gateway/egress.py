"""Egress classification gate backed by the security policy file.

The gate reads ``classification_order`` and ``external_model_egress`` from
``config/security/policy.json`` instead of re-implementing the policy. It
fails closed: an unavailable or malformed policy file raises at load time,
and any unknown classification or missing rule returns a denial.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict
from sklegal_domain import DataClassification

from .errors import PolicyUnavailableError
from .models import ModelRouteRecord, Provider


class EgressDecision(BaseModel):
    """Immutable egress decision bound to the exact policy revision."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    allow: bool
    reason: str
    rule: str
    policy_revision: str


class EgressGate(Protocol):
    """Decide whether one classification may reach one pinned route."""

    def decide(
        self,
        *,
        route: ModelRouteRecord,
        classification: DataClassification,
        human_approval_ref: str | None,
    ) -> EgressDecision: ...


class PolicyFileEgressGate:
    """Egress gate backed by the security policy JSON file."""

    def __init__(self, policy_path: Path) -> None:
        try:
            raw = policy_path.read_bytes()
        except OSError as exc:
            raise PolicyUnavailableError(
                f"egress policy file is unavailable: {policy_path}"
            ) from exc
        self._policy_revision = hashlib.sha256(raw).hexdigest()
        try:
            payload = json.loads(raw.decode("utf-8"))
            order = list(payload["classification_order"])
            matrix = dict(payload["external_model_egress"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PolicyUnavailableError(
                "egress policy file is missing classification_order or "
                "external_model_egress"
            ) from exc
        self._rank = {
            DataClassification(name): index for index, name in enumerate(order)
        }
        self._matrix = {
            DataClassification(name): str(rule) for name, rule in matrix.items()
        }

    @property
    def policy_revision(self) -> str:
        return self._policy_revision

    def _decision(self, *, allow: bool, reason: str, rule: str) -> EgressDecision:
        return EgressDecision(
            allow=allow,
            reason=reason,
            rule=rule,
            policy_revision=self._policy_revision,
        )

    def decide(
        self,
        *,
        route: ModelRouteRecord,
        classification: DataClassification,
        human_approval_ref: str | None,
    ) -> EgressDecision:
        rank = self._rank.get(classification)
        ceiling_rank = self._rank.get(route.egress_classification_ceiling)
        if rank is None or ceiling_rank is None:
            return self._decision(
                allow=False, reason="classification_unknown", rule="unavailable"
            )
        if rank > ceiling_rank:
            return self._decision(
                allow=False,
                reason="route_ceiling_exceeded",
                rule="route_ceiling",
            )
        if route.provider is Provider.QWEN_LOCAL:
            return self._decision(allow=True, reason="allow", rule="local_provider")
        rule = self._matrix.get(classification)
        if rule is None:
            return self._decision(
                allow=False, reason="egress_rule_missing", rule="unavailable"
            )
        if rule == "deny":
            return self._decision(
                allow=False, reason="classification_egress_denied", rule=rule
            )
        if rule == "conditional_human_approval" and not human_approval_ref:
            return self._decision(
                allow=False,
                reason="human_egress_approval_required",
                rule=rule,
            )
        return self._decision(allow=True, reason="allow", rule=rule)
