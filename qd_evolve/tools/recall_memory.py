"""recall_memory tool — search past conversation memories."""

from __future__ import annotations

from typing import TYPE_CHECKING

from qd_evolve.tools import get_registry

if TYPE_CHECKING:
    from qd_evolve.memory import MemoryStore

_memory_store: MemoryStore | None = None


def set_memory_store(store: MemoryStore) -> None:
    global _memory_store
    _memory_store = store


def _recall_memory(
    query: str | None = None,
    keywords: list[str] | None = None,
    time_range: str | None = None,
    limit: int = 5,
) -> str:
    if _memory_store is None:
        return "Memory store not initialized."

    # When only time_range (no query/keywords), raise limit for browsing
    if not query and not keywords and time_range:
        limit = max(limit, 20)

    entries = _memory_store.recall(
        query=query or None,
        keywords=keywords or None,
        time_range=time_range or None,
        limit=limit,
    )

    if not entries:
        return "No memories found."

    lines = [f"Found {len(entries)} memories:\n"]
    for i, e in enumerate(entries, 1):
        lines.append(f"[{i}] {e.key} (session: {e.session_id})")
        lines.append(f"  user: {e.user_msg}")
        lines.append(f"  assistant: {e.assistant_msg}")
        if e.distance is not None:
            lines.append(f"  (relevance: {1 - e.distance:.2f})")
        lines.append("")

    return "\n".join(lines)


registry = get_registry()
registry.register(
    name="recall_memory",
    description="Search past conversation memories across sessions. Combines semantic similarity, keyword matching, and time filtering to find relevant past interactions.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language description of what to recall. Used for semantic similarity search. E.g. 'how we solved the database timeout issue'",
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific words or phrases to match. Combined with semantic search for hybrid retrieval. E.g. ['timeout', 'postgres']",
            },
            "time_range": {
                "type": "string",
                "description": "Time range to filter memories. Options: 'last_session' (previous session), 'today', 'yesterday', 'this_week', 'last_week', 'this_month', 'last_month', 'last_Nd' (e.g. 'last_3d', 'last_10d' for recent N days), 'YYYY-MM-DD~YYYY-MM-DD' (date range), or 'YYYY-MM-DD' (from date to now). Empty means all time.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of memories to return. Default 5.",
                "default": 5,
            },
        },
        "required": [],
    },
    handler=_recall_memory,
)
