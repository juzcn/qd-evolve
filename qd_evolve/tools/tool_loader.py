"""Dynamic tool detail loader — loads full tool schema on demand."""

from __future__ import annotations

import json

from qd_evolve.tools import get_registry


def _load_tool_detail(name: str) -> str:
    registry = get_registry()
    detail = registry.get_detail(name)
    if detail is None:
        available = ", ".join(t.name for t in registry.list_tools())
        return f"Error: tool '{name}' not found. Available: {available}"
    return json.dumps(detail, ensure_ascii=False)


registry = get_registry()
registry.register(
    name="load_tool_detail",
    description="Load the full schema and description for a tool by name. Returns name, description, and input_schema.",
    handler=_load_tool_detail,
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The tool name (as shown in the tools list)",
            },
        },
        "required": ["name"],
    },
)
