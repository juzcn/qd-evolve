"""Dynamic CLI tool detail loader — loads full CLI tool definition on demand."""

from __future__ import annotations

import json

from qd_evolve.tools import get_registry

_cli_registry = None
_preload_cli: set[str] = set()


def set_cli_registry(registry, preload_cli: set[str] | None = None) -> None:
    global _cli_registry, _preload_cli
    _cli_registry = registry
    if preload_cli is not None:
        _preload_cli = preload_cli


def _load_cli_detail(name: str) -> str:
    if _cli_registry is None:
        return "Error: CLI registry not initialized"
    detail = _cli_registry.get_detail(name)
    if detail is None:
        available = ", ".join(t.name for t in _cli_registry.list_tools())
        return f"Error: CLI tool '{name}' not found. Available: {available}"
    if name in _preload_cli:
        return f"(already preloaded — full definition is in the system prompt)\n\n{json.dumps(detail, ensure_ascii=False)}"
    return json.dumps(detail, ensure_ascii=False)


registry = get_registry()
registry.register(
    name="load_cli_detail",
    description="Load the full definition for a CLI tool by name. Returns name, command, description, help_summary, and examples.",
    handler=_load_cli_detail,
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The CLI tool name (as shown in the CLI tools list)",
            },
        },
        "required": ["name"],
    },
)
