"""Typed errors raised by the read-only HammerTime adapter.

Every failure mode fails closed: callers receive a typed error instead of
unpinned, unverified, or unauthorized HammerTime content.
"""

from __future__ import annotations


class HammerTimeAdapterError(RuntimeError):
    """Base class for all read-only HammerTime adapter failures."""


class AdapterUnavailableError(HammerTimeAdapterError):
    """The HammerTime root is missing, unreadable, or not a directory."""


class MissingPathError(HammerTimeAdapterError):
    """An expected release, artifact, or legacy record path is absent."""


class ForbiddenPathError(HammerTimeAdapterError, PermissionError):
    """The requested path is outside the contract or inside HammerTime Inbox."""


class ArtifactTooLargeError(HammerTimeAdapterError):
    """An artifact exceeds the bounded read budget."""


class SourceHashMismatchError(HammerTimeAdapterError):
    """Observed content hash differs from the pinned source hash."""


class StaleReleaseError(HammerTimeAdapterError):
    """A runtime alias points at a missing or mismatched release manifest."""


class MalformedManifestError(HammerTimeAdapterError):
    """A release manifest or state file does not satisfy the read contract."""


class MalformedFrontmatterError(HammerTimeAdapterError):
    """A markdown record has missing, unterminated, or invalid frontmatter."""


class AmbiguousLegacyIdError(HammerTimeAdapterError):
    """A legacy record id maps to more than one path or needs a parent id."""


class RegistryMismatchError(HammerTimeAdapterError):
    """The incident registry disagrees with the record it points at."""


class MatterAccessDenied(HammerTimeAdapterError, PermissionError):
    """Matter-scoped content was requested without an allow decision.

    Raised when no matter authorizer is configured, when the authorizer
    denies access, or when the authorizer itself fails. The adapter never
    defaults to allow for matter-scoped content.
    """
