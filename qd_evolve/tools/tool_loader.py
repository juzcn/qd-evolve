"""Dynamic tool detail loader 鈥?loads full tool schema on demand."""


import json

from qd_evolve.tools import get_registry

_preload_tools: set[str] = set()


def set_preload_tools(names: set[str]) -> None:
    global _preload_tools
    _preload_tools = names


def _load_tool_detail(name: str) -> str:
    registry = get_registry()
    detail = registry.get_detail(name)
    if detail is None:
        available = ", ".join(t.name for t in registry.list_tools())
        return f"Error: tool '{name}' not found. Available: {available}"
    if name in _preload_tools:
        return f"(already preloaded 鈥?schema is already available in API tool definitions)\n\n{json.dumps(detail, ensure_ascii=False)}"
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
