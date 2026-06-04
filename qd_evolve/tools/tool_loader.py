"""Dynamic tool detail loader — activates tools on demand."""


from qd_evolve.tools import get_registry

_preload_tools: set[str] = set()


def set_preload_tools(names: set[str]) -> None:
    global _preload_tools
    _preload_tools |= names


def _load_tool_detail(name: str) -> str:
    registry = get_registry()
    detail = registry.get_detail(name)
    if detail is None:
        available = ", ".join(t.name for t in registry.list_tools())
        return f"Error: tool '{name}' not found. Available: {available}"
    if name in _preload_tools:
        return f"Tool '{name}' is already preloaded — schema is available in API tool definitions."
    desc = detail.get("description", "") if isinstance(detail, dict) else ""
    return f"Tool '{name}' loaded. {desc} (full schema available in next request's API tool definitions.)"


registry = get_registry()
registry.register(
    name="load_func",
    description="Activate a func tool by name. The schema will be included in API tool definitions on the next request.",
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
