"""Backward-compatible re-export shim — implementation moved to qd_evolve.core.memory."""

from qd_evolve.core.memory import (  # noqa: F401
    MemoryEntry, RecalledMemoryRegistry, MemoryStore,
    SentenceTransformerEmbedder, LlamaCppEmbedder,
)