"""Fail-closed SKLegal authorization and bounded delegation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Never
from uuid import UUID, uuid4

from capauth import signature_verifies  # type: ignore[import-untyped]

from .backends import (
    AuditSink,
    PrincipalPolicyBackend,
    PrincipalPolicySnapshot,
    PrincipalUnboundError,
    ReplayBackend,
    RevocationBackend,
    RevocationSnapshot,
    SignatureVerificationCache,
    TrustedIssuerBackend,
    TrustedIssuerSnapshot,
)
from .models import (
    MAX_DELEGATION_DEPTH,
    MAX_TTL_SECONDS,
    NONDELEGABLE_CAPABILITIES,
    VERIFIER_POLICY_VERSION,
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizedContext,
    CapabilityGrant,
    DecisionReason,
    PrincipalContext,
    PrincipalPolicyReference,
    PrincipalType,
)
from .tokens import (
    CapabilityIssuer,
    CredentialFormatError,
    ParsedCapability,
    PresentedCapability,
    parse_presented_token,
)

MAX_CLOCK_SKEW_SECONDS = 30

_PRINCIPAL_CHILD_TYPE_ALLOWANCES = {
    PrincipalType.HUMAN: frozenset({PrincipalType.HUMAN}),
    PrincipalType.AGENT: frozenset({PrincipalType.AGENT, PrincipalType.SERVICE}),
    PrincipalType.SERVICE: frozenset(
        {
            PrincipalType.AGENT,
            PrincipalType.SERVICE,
            PrincipalType.CONNECTOR,
        }
    ),
    PrincipalType.CONNECTOR: frozenset(),
}


def _is_attenuated(
    parent: ParsedCapability,
    *,
    child_principal: PrincipalContext,
    child_grant: CapabilityGrant,
    child_depth: int,
    child_max_depth: int,
    child_use_limit: int,
    child_issued_at: datetime,
    child_expires_at: datetime | None,
) -> bool:
    """Apply the single fail-closed rule for requested and signed children."""

    parent_claims = parent.claims
    parent_grant = parent_claims.grant
    if parent_grant.capability in NONDELEGABLE_CAPABILITIES:
        return False
    if child_depth != parent_claims.delegation.depth + 1:
        return False
    if child_max_depth > parent_claims.delegation.max_depth:
        return False
    if child_depth > parent_claims.delegation.max_depth:
        return False
    if child_issued_at < parent.token.payload.issued_at:
        return False
    parent_expires_at = parent.token.payload.expires_at
    if child_expires_at is None or parent_expires_at is None:
        return False
    if child_expires_at > parent_expires_at:
        return False
    if child_use_limit > parent_claims.use_limit:
        return False
    if child_principal.tenant_id != parent_claims.principal.tenant_id:
        return False
    allowed_child_types = _PRINCIPAL_CHILD_TYPE_ALLOWANCES.get(
        parent_claims.principal.principal_type
    )
    if (
        allowed_child_types is None
        or child_principal.principal_type not in allowed_child_types
    ):
        return False
    scalar_fields = (
        "audience",
        "target",
        "capability",
        "tenant_id",
        "resource_type",
        "operation",
        "purpose",
        "model_route",
    )
    if any(
        getattr(parent_grant, field_name) != getattr(child_grant, field_name)
        for field_name in scalar_fields
    ):
        return False
    for field_name in (
        "matter_id",
        "resource_id",
        "resource_version",
        "resource_sha256",
        "workflow_run_id",
    ):
        parent_value = getattr(parent_grant, field_name)
        child_value = getattr(child_grant, field_name)
        if parent_value is not None and child_value != parent_value:
            return False
    return True


class AuthorizationDenied(PermissionError):
    """A fail-closed denial carrying only a sanitized decision."""

    def __init__(self, decision: AuthorizationDecision) -> None:
        self.decision = decision
        super().__init__(
            f"authorization denied ({decision.reason_code.value}); "
            f"decision_id={decision.decision_id}"
        )


class DelegationDenied(PermissionError):
    """A requested child would broaden or misuse delegated authority."""


class CapabilityAuthorizer:
    """Verify a complete signed chain and reserve the leaf for one invocation."""

    def __init__(
        self,
        *,
        trusted_issuers: TrustedIssuerBackend,
        principals: PrincipalPolicyBackend,
        revocations: RevocationBackend,
        replay: ReplayBackend,
        audit: AuditSink,
        signature_cache: SignatureVerificationCache,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._trusted_issuers = trusted_issuers
        self._principals = principals
        self._revocations = revocations
        self._replay = replay
        self._audit = audit
        self._signature_cache = signature_cache
        self._clock = clock or (lambda: datetime.now(UTC))

    def authorize(
        self,
        presented: PresentedCapability | None,
        request: AuthorizationRequest,
    ) -> AuthorizedContext:
        """Authorize exactly one invocation or raise a sanitized denial."""

        decision_id = uuid4()
        if presented is None:
            self._deny(
                request,
                decision_id=decision_id,
                reason=DecisionReason.MISSING_CREDENTIAL,
            )

        try:
            raw_chain = presented.credentials_for_verification()
            if not 1 <= len(raw_chain) <= MAX_DELEGATION_DEPTH + 1:
                raise CredentialFormatError("credential chain length is invalid")
            chain = tuple(parse_presented_token(item) for item in raw_chain)
        except Exception:
            self._deny(
                request,
                decision_id=decision_id,
                reason=DecisionReason.MALFORMED_CREDENTIAL,
            )
        leaf = chain[-1]
        principal_references: tuple[PrincipalPolicyReference, ...] = ()

        try:
            issuer_policy = self._trusted_issuers.snapshot()
            if not isinstance(issuer_policy, TrustedIssuerSnapshot):
                raise TypeError("trusted issuer backend returned the wrong type")
        except Exception:
            self._deny(
                request,
                decision_id=decision_id,
                reason=DecisionReason.BACKEND_UNAVAILABLE,
                chain=chain,
            )
        if issuer_policy.policy_version != VERIFIER_POLICY_VERSION:
            self._deny(
                request,
                decision_id=decision_id,
                reason=DecisionReason.POLICY_MISMATCH,
                chain=chain,
                issuer_revision=issuer_policy.revision,
            )

        principal_reason, principal_references = self._validate_current_principals(
            chain,
            request,
        )
        if principal_reason is not None:
            self._deny(
                request,
                decision_id=decision_id,
                reason=principal_reason,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
            )

        try:
            chain_reason = self._validate_chain(chain, issuer_policy)
        except Exception:
            self._deny(
                request,
                decision_id=decision_id,
                reason=DecisionReason.BACKEND_UNAVAILABLE,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
            )
        if chain_reason is not None:
            self._deny(
                request,
                decision_id=decision_id,
                reason=chain_reason,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
            )

        digests = tuple(item.credential_digest for item in chain)
        try:
            revocation = self._revocations.snapshot(digests)
            if not isinstance(revocation, RevocationSnapshot):
                raise TypeError("revocation backend returned the wrong type")
        except Exception:
            self._deny(
                request,
                decision_id=decision_id,
                reason=DecisionReason.BACKEND_UNAVAILABLE,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
            )
        revoked = revocation.revoked_credential_digests
        if leaf.credential_digest in revoked:
            self._deny(
                request,
                decision_id=decision_id,
                reason=DecisionReason.REVOKED,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
                revocation_revision=revocation.revision,
            )
        if any(item.credential_digest in revoked for item in chain[:-1]):
            self._deny(
                request,
                decision_id=decision_id,
                reason=DecisionReason.ANCESTOR_REVOKED,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
                revocation_revision=revocation.revision,
            )

        all_cache_hits = True
        for item in chain:
            cache_key = (
                item.credential_digest,
                item.token.payload.issuer.upper(),
                VERIFIER_POLICY_VERSION,
                issuer_policy.revision,
                revocation.revision,
            )
            try:
                if self._signature_cache.contains(cache_key):
                    continue
            except Exception:
                self._deny(
                    request,
                    decision_id=decision_id,
                    reason=DecisionReason.BACKEND_UNAVAILABLE,
                    chain=chain,
                    issuer_revision=issuer_policy.revision,
                    principal_references=principal_references,
                    revocation_revision=revocation.revision,
                )
            all_cache_hits = False
            if not item.token.signature:
                self._deny(
                    request,
                    decision_id=decision_id,
                    reason=DecisionReason.UNSIGNED_CREDENTIAL,
                    chain=chain,
                    issuer_revision=issuer_policy.revision,
                    principal_references=principal_references,
                    revocation_revision=revocation.revision,
                )
            try:
                signature_valid = signature_verifies(item.token)
            except Exception:
                signature_valid = False
            if not signature_valid:
                self._deny(
                    request,
                    decision_id=decision_id,
                    reason=DecisionReason.INVALID_SIGNATURE,
                    chain=chain,
                    issuer_revision=issuer_policy.revision,
                    principal_references=principal_references,
                    revocation_revision=revocation.revision,
                )
            expires_at = item.token.payload.expires_at
            if expires_at is None:
                self._deny(
                    request,
                    decision_id=decision_id,
                    reason=DecisionReason.MALFORMED_CREDENTIAL,
                    chain=chain,
                    issuer_revision=issuer_policy.revision,
                    principal_references=principal_references,
                    revocation_revision=revocation.revision,
                )
            try:
                self._signature_cache.add(
                    cache_key,
                    credential_expires_at=expires_at,
                )
            except Exception:
                self._deny(
                    request,
                    decision_id=decision_id,
                    reason=DecisionReason.BACKEND_UNAVAILABLE,
                    chain=chain,
                    issuer_revision=issuer_policy.revision,
                    principal_references=principal_references,
                    revocation_revision=revocation.revision,
                )

        request_reason = self._request_mismatch(leaf, request)
        if request_reason is not None:
            self._deny(
                request,
                decision_id=decision_id,
                reason=request_reason,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
                revocation_revision=revocation.revision,
                signature_cache_hit=all_cache_hits,
            )

        try:
            decision_time_reason = self._validate_chain_time(chain)
        except Exception:
            self._deny(
                request,
                decision_id=decision_id,
                reason=DecisionReason.BACKEND_UNAVAILABLE,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
                revocation_revision=revocation.revision,
                signature_cache_hit=all_cache_hits,
            )
        if decision_time_reason is not None:
            self._deny(
                request,
                decision_id=decision_id,
                reason=decision_time_reason,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
                revocation_revision=revocation.revision,
                signature_cache_hit=all_cache_hits,
            )

        expires_at = leaf.token.payload.expires_at
        if expires_at is None:
            self._deny(
                request,
                decision_id=decision_id,
                reason=DecisionReason.MALFORMED_CREDENTIAL,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
                revocation_revision=revocation.revision,
            )
        try:
            reserved = self._replay.reserve(
                credential_digest=leaf.credential_digest,
                decision_id=str(decision_id),
                expires_at=expires_at,
            )
        except Exception:
            self._deny(
                request,
                decision_id=decision_id,
                reason=DecisionReason.BACKEND_UNAVAILABLE,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
                revocation_revision=revocation.revision,
                signature_cache_hit=all_cache_hits,
            )
        if not isinstance(reserved, bool):
            self._deny(
                request,
                decision_id=decision_id,
                reason=DecisionReason.BACKEND_UNAVAILABLE,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
                revocation_revision=revocation.revision,
                signature_cache_hit=all_cache_hits,
            )
        if not reserved:
            self._deny(
                request,
                decision_id=decision_id,
                reason=DecisionReason.REPLAYED,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
                revocation_revision=revocation.revision,
                signature_cache_hit=all_cache_hits,
            )

        try:
            final_time_reason = self._validate_chain_time(chain)
        except Exception:
            self._deny(
                request,
                decision_id=decision_id,
                reason=DecisionReason.BACKEND_UNAVAILABLE,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
                revocation_revision=revocation.revision,
                signature_cache_hit=all_cache_hits,
            )
        if final_time_reason is not None:
            self._deny(
                request,
                decision_id=decision_id,
                reason=final_time_reason,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
                revocation_revision=revocation.revision,
                signature_cache_hit=all_cache_hits,
            )

        decision = self._decision(
            request,
            decision_id=decision_id,
            allow=True,
            reason=DecisionReason.ALLOW,
            chain=chain,
            issuer_revision=issuer_policy.revision,
            principal_references=principal_references,
            revocation_revision=revocation.revision,
            signature_cache_hit=all_cache_hits,
        )
        try:
            self._audit.record(decision)
        except Exception:
            denied = self._decision(
                request,
                decision_id=decision_id,
                allow=False,
                reason=DecisionReason.AUDIT_UNAVAILABLE,
                chain=chain,
                issuer_revision=issuer_policy.revision,
                principal_references=principal_references,
                revocation_revision=revocation.revision,
                signature_cache_hit=all_cache_hits,
            )
            raise AuthorizationDenied(denied) from None
        return AuthorizedContext(
            decision=decision,
            principal=request.principal,
            grant=request.grant,
            credential_expires_at=expires_at,
            principal_chain=self._distinct_principal_chain(chain, request),
        )

    @staticmethod
    def _distinct_principal_chain(
        chain: tuple[ParsedCapability, ...],
        request: AuthorizationRequest,
    ) -> tuple[PrincipalContext, ...]:
        principals: list[PrincipalContext] = []
        seen: set[UUID] = set()
        for principal in (
            *(item.claims.principal for item in chain),
            request.principal,
        ):
            if principal.principal_id not in seen:
                principals.append(principal)
                seen.add(principal.principal_id)
        return tuple(principals)

    def _validate_current_principals(
        self,
        chain: tuple[ParsedCapability, ...],
        request: AuthorizationRequest,
    ) -> tuple[DecisionReason | None, tuple[PrincipalPolicyReference, ...]]:
        claimed_by_id: dict[UUID, list[PrincipalContext]] = {}
        order: list[UUID] = []
        for claimed in (
            *(item.claims.principal for item in chain),
            request.principal,
        ):
            if claimed.principal_id not in claimed_by_id:
                claimed_by_id[claimed.principal_id] = []
                order.append(claimed.principal_id)
            if claimed not in claimed_by_id[claimed.principal_id]:
                claimed_by_id[claimed.principal_id].append(claimed)

        references: list[PrincipalPolicyReference] = []
        for principal_id in order:
            claimed_contexts = claimed_by_id[principal_id]
            try:
                snapshot = self._principals.snapshot(claimed_contexts[0])
                if not isinstance(snapshot, PrincipalPolicySnapshot):
                    raise TypeError("principal backend returned the wrong type")
                reference = PrincipalPolicyReference(
                    principal_id=principal_id,
                    revision=snapshot.revision,
                )
            except PrincipalUnboundError:
                return DecisionReason.PRINCIPAL_UNBOUND, tuple(references)
            except Exception:
                return DecisionReason.BACKEND_UNAVAILABLE, tuple(references)
            references.append(reference)
            if any(snapshot.principal != claimed for claimed in claimed_contexts):
                return DecisionReason.PRINCIPAL_REBOUND, tuple(references)
            if not snapshot.active:
                return DecisionReason.PRINCIPAL_INACTIVE, tuple(references)
        return None, tuple(references)

    def _validate_chain(
        self,
        chain: tuple[ParsedCapability, ...],
        issuer_policy: TrustedIssuerSnapshot,
    ) -> DecisionReason | None:
        time_reason = self._validate_chain_time(chain)
        if time_reason is not None:
            return time_reason
        for index, item in enumerate(chain):
            token = item.token.payload
            claims = item.claims
            issuer = token.issuer.strip().upper()
            issuer_grant = next(
                (
                    grant
                    for grant in issuer_policy.issuers
                    if grant.fingerprint == issuer
                ),
                None,
            )
            if issuer_grant is None:
                return DecisionReason.UNTRUSTED_ISSUER
            if (
                claims.grant.capability not in issuer_grant.capabilities
                or claims.grant.audience not in issuer_grant.audiences
                or claims.principal.principal_type not in issuer_grant.principal_types
            ):
                return DecisionReason.UNTRUSTED_ISSUER
            if claims.verifier_policy_version != VERIFIER_POLICY_VERSION:
                return DecisionReason.POLICY_MISMATCH
            delegation = claims.delegation
            if delegation.depth != index:
                return DecisionReason.DELEGATION_CHAIN_INVALID
            if index == 0:
                if delegation.parent_credential_digest is not None:
                    return DecisionReason.DELEGATION_CHAIN_INVALID
            else:
                parent = chain[index - 1]
                if delegation.parent_credential_digest != parent.credential_digest:
                    return DecisionReason.DELEGATION_CHAIN_INVALID
                if not _is_attenuated(
                    parent,
                    child_principal=claims.principal,
                    child_grant=claims.grant,
                    child_depth=delegation.depth,
                    child_max_depth=delegation.max_depth,
                    child_use_limit=claims.use_limit,
                    child_issued_at=item.token.payload.issued_at,
                    child_expires_at=item.token.payload.expires_at,
                ):
                    return DecisionReason.OVER_DELEGATED
        if chain[-1].claims.delegation.depth != len(chain) - 1:
            return DecisionReason.DELEGATION_CHAIN_INVALID
        return None

    def _validate_chain_time(
        self,
        chain: tuple[ParsedCapability, ...],
    ) -> DecisionReason | None:
        now = self._clock().astimezone(UTC)
        for index, item in enumerate(chain):
            token = item.token.payload
            if token.expires_at is None or token.not_before is None:
                return DecisionReason.MALFORMED_CREDENTIAL
            if token.not_before != token.issued_at:
                return DecisionReason.MALFORMED_CREDENTIAL
            lifetime = token.expires_at - token.issued_at
            if lifetime <= timedelta(0) or lifetime > timedelta(
                seconds=MAX_TTL_SECONDS
            ):
                return DecisionReason.TTL_EXCEEDED
            if token.issued_at > now + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
                return DecisionReason.NOT_YET_VALID
            if now < token.not_before:
                return DecisionReason.NOT_YET_VALID
            if now >= token.expires_at:
                return (
                    DecisionReason.EXPIRED
                    if index == len(chain) - 1
                    else DecisionReason.ANCESTOR_EXPIRED
                )
        return None

    @staticmethod
    def _request_mismatch(
        leaf: ParsedCapability,
        request: AuthorizationRequest,
    ) -> DecisionReason | None:
        claims = leaf.claims
        token_principal = claims.principal
        requested_principal = request.principal
        if token_principal.principal_id != requested_principal.principal_id:
            return DecisionReason.WRONG_PRINCIPAL
        if token_principal.subject != requested_principal.subject:
            return DecisionReason.WRONG_PRINCIPAL
        if token_principal.principal_type != requested_principal.principal_type:
            return DecisionReason.WRONG_PRINCIPAL_TYPE
        if token_principal.tenant_id != requested_principal.tenant_id:
            return DecisionReason.WRONG_TENANT

        token_grant = claims.grant
        requested = request.grant
        if token_grant.audience != requested.audience:
            return DecisionReason.WRONG_AUDIENCE
        if token_grant.target != requested.target:
            return DecisionReason.WRONG_TARGET
        if token_grant.capability != requested.capability:
            return DecisionReason.WRONG_CAPABILITY
        if token_grant.tenant_id != requested.tenant_id:
            return DecisionReason.WRONG_TENANT
        if token_grant.matter_id != requested.matter_id:
            return DecisionReason.WRONG_MATTER
        if (
            token_grant.resource_type != requested.resource_type
            or token_grant.resource_id != requested.resource_id
            or token_grant.resource_version != requested.resource_version
            or token_grant.resource_sha256 != requested.resource_sha256
        ):
            return DecisionReason.WRONG_RESOURCE
        if token_grant.operation != requested.operation:
            return DecisionReason.WRONG_OPERATION
        if token_grant.purpose != requested.purpose:
            return DecisionReason.WRONG_PURPOSE
        if token_grant.model_route != requested.model_route:
            return DecisionReason.WRONG_MODEL_ROUTE
        if token_grant.workflow_run_id != requested.workflow_run_id:
            return DecisionReason.WRONG_WORKFLOW
        return None

    def _decision(
        self,
        request: AuthorizationRequest,
        *,
        decision_id: UUID,
        allow: bool,
        reason: DecisionReason,
        chain: tuple[ParsedCapability, ...] = (),
        issuer_revision: str | None = None,
        principal_references: tuple[PrincipalPolicyReference, ...] = (),
        revocation_revision: str | None = None,
        signature_cache_hit: bool = False,
    ) -> AuthorizationDecision:
        grant = request.grant
        leaf = chain[-1] if chain else None
        return AuthorizationDecision(
            decision_id=decision_id,
            correlation_id=request.correlation_id,
            allow=allow,
            reason_code=reason,
            credential_digest=leaf.credential_digest if leaf else None,
            principal_id=request.principal.principal_id,
            tenant_id=request.principal.tenant_id,
            matter_id=grant.matter_id,
            capability=grant.capability,
            audience=grant.audience,
            target=grant.target,
            resource_type=grant.resource_type,
            resource_id=grant.resource_id,
            resource_version=grant.resource_version,
            resource_sha256=grant.resource_sha256,
            operation=grant.operation,
            purpose=grant.purpose,
            model_route=grant.model_route,
            workflow_run_id=grant.workflow_run_id,
            delegation_depth=(leaf.claims.delegation.depth if leaf else None),
            ancestor_credential_digests=tuple(
                item.credential_digest for item in chain[:-1]
            ),
            trusted_issuer_policy_revision=issuer_revision,
            principal_policy_revisions=principal_references,
            revocation_revision=revocation_revision,
            signature_cache_hit=signature_cache_hit,
        )

    def _deny(
        self,
        request: AuthorizationRequest,
        *,
        decision_id: UUID,
        reason: DecisionReason,
        chain: tuple[ParsedCapability, ...] = (),
        issuer_revision: str | None = None,
        principal_references: tuple[PrincipalPolicyReference, ...] = (),
        revocation_revision: str | None = None,
        signature_cache_hit: bool = False,
    ) -> Never:
        decision = self._decision(
            request,
            decision_id=decision_id,
            allow=False,
            reason=reason,
            chain=chain,
            issuer_revision=issuer_revision,
            principal_references=principal_references,
            revocation_revision=revocation_revision,
            signature_cache_hit=signature_cache_hit,
        )
        try:
            self._audit.record(decision)
        except Exception:
            decision = self._decision(
                request,
                decision_id=decision_id,
                allow=False,
                reason=DecisionReason.AUDIT_UNAVAILABLE,
                chain=chain,
                issuer_revision=issuer_revision,
                principal_references=principal_references,
                revocation_revision=revocation_revision,
                signature_cache_hit=signature_cache_hit,
            )
        raise AuthorizationDenied(decision) from None


class DelegatingCapabilityIssuer:
    """Consume one parent invocation and mint one strictly attenuated child."""

    def __init__(
        self,
        *,
        authorizer: CapabilityAuthorizer,
        issuer: CapabilityIssuer,
    ) -> None:
        self._authorizer = authorizer
        self._issuer = issuer

    def delegate(
        self,
        *,
        parent: PresentedCapability,
        authenticated_parent: PrincipalContext,
        child_principal: PrincipalContext,
        child_grant: CapabilityGrant,
        correlation_id: UUID,
        ttl_seconds: int = 300,
    ) -> PresentedCapability:
        try:
            raw_chain = parent.credentials_for_verification()
            parsed_parent = parse_presented_token(raw_chain[-1])
        except Exception:
            raise DelegationDenied("parent credential is malformed") from None
        parent_claims = parsed_parent.claims
        if not _is_attenuated(
            parsed_parent,
            child_principal=child_principal,
            child_grant=child_grant,
            child_depth=parent_claims.delegation.depth + 1,
            child_max_depth=parent_claims.delegation.max_depth,
            child_use_limit=1,
            child_issued_at=parsed_parent.token.payload.issued_at,
            child_expires_at=parsed_parent.token.payload.expires_at,
        ):
            raise DelegationDenied("delegation would broaden authority")
        request = AuthorizationRequest(
            principal=authenticated_parent,
            grant=parsed_parent.claims.grant,
            correlation_id=correlation_id,
        )
        try:
            self._authorizer.authorize(parent, request)
        except AuthorizationDenied as exc:
            raise DelegationDenied(str(exc)) from None
        try:
            verified_parent = parse_presented_token(raw_chain[-1])
        except Exception:
            raise DelegationDenied("parent credential is malformed") from None
        raw_child = self._issuer.issue_child(
            parent=verified_parent,
            principal=child_principal,
            grant=child_grant,
            ttl_seconds=ttl_seconds,
            max_depth=verified_parent.claims.delegation.max_depth,
        )
        return parent.with_child(raw_child)
