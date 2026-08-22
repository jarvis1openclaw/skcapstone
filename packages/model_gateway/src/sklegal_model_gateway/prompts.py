"""Pinned prompt template loading, rendering, and budget estimation."""

from __future__ import annotations

import re
import string
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from .errors import PromptRenderError, RouteIntegrityError

_TEMPLATE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]*$")


class PromptStore(Protocol):
    """Load prompt template text for a pinned template identifier."""

    def load(self, template_id: str) -> str: ...


class FilePromptStore:
    """Prompt store backed by one ``<template_id>.prompt.txt`` file each."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def load(self, template_id: str) -> str:
        if not _TEMPLATE_ID_PATTERN.match(template_id):
            raise RouteIntegrityError(
                f"prompt template id is not a safe file name: {template_id}"
            )
        path = self._root / f"{template_id}.prompt.txt"
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RouteIntegrityError(
                f"prompt template is unavailable: {template_id}"
            ) from exc


def render_prompt(template: str, inputs: Mapping[str, str]) -> str:
    """Render a template strictly: every placeholder must be supplied."""

    formatter = string.Formatter()
    fields = {
        field_name for _, field_name, _, _ in formatter.parse(template) if field_name
    }
    missing = sorted(field for field in fields if field not in inputs)
    if missing:
        raise PromptRenderError(
            f"prompt template fields are missing inputs: {', '.join(missing)}"
        )
    try:
        return formatter.vformat(template, (), dict(inputs))
    except (KeyError, IndexError, ValueError) as exc:
        raise PromptRenderError(f"prompt template cannot be rendered: {exc}") from exc


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate: four characters per token, rounded up."""

    return (len(text) + 3) // 4
