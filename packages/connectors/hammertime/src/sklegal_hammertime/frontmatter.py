"""YAML frontmatter splitting for HammerTime markdown records.

Parsing failures raise MalformedFrontmatterError instead of returning
partial or guessed metadata. Content is never modified.
"""

from __future__ import annotations

from typing import Any

import yaml  # type: ignore[import-untyped]

from .errors import MalformedFrontmatterError

_FENCE = "---"


def split_frontmatter(text: str, *, relative_path: str) -> tuple[dict[str, Any], str]:
    """Split a markdown document into its frontmatter mapping and body.

    The document must open with a ``---`` fence line and contain a closing
    ``---`` fence line. The parsed frontmatter must be a mapping.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        raise MalformedFrontmatterError(
            f"missing opening frontmatter fence: {relative_path}"
        )
    closing = None
    for index in range(1, len(lines)):
        if lines[index].strip() == _FENCE:
            closing = index
            break
    if closing is None:
        raise MalformedFrontmatterError(
            f"unterminated frontmatter fence: {relative_path}"
        )
    block = "\n".join(lines[1:closing])
    try:
        parsed = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise MalformedFrontmatterError(
            f"invalid frontmatter YAML in {relative_path}: {exc.__class__.__name__}"
        ) from None
    if not isinstance(parsed, dict):
        raise MalformedFrontmatterError(
            f"frontmatter must be a mapping: {relative_path}"
        )
    body = "\n".join(lines[closing + 1 :])
    return {str(key): value for key, value in parsed.items()}, body
