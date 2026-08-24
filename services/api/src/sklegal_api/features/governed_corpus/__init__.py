"""Governed corpus API feature."""

from .router import build_governed_corpus_router
from .service import GovernedCorpusService

__all__ = ["GovernedCorpusService", "build_governed_corpus_router"]
