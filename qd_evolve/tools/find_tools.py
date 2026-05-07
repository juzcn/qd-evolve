from __future__ import annotations

from qd_evolve.tools import get_registry

registry = get_registry()

registry.register(
    name="find_and_load_tools",
    description="Search and load tools by name, keyword, or semantic meaning. Returns matching tool names with descriptions and relevance scores, then enables them for use.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query — matches by tool name, keyword in description, or semantic similarity",
            },
            "top_k": {
                "type": "integer",
                "description": "Maximum number of results to return and enable (default 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    },
    handler=lambda query, top_k=5: _find_and_load_tools(query, top_k),
)


def _find_and_load_tools(query: str, top_k: int = 5) -> str:
    results = registry.search_tools(query, top_k)
    if not results:
        return f"No tools found matching '{query}'"
    lines = []
    for name, desc, score in results:
        registry.enable(name)
        lines.append(f"{name} (score: {score:.2f}) — {desc} [enabled]")
    return "\n".join(lines)