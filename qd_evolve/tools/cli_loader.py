"""Dynamic CLI tool detail loader — loads full CLI tool definition on demand."""

from __future__ import annotations

import json

from qd_evolve.tools import get_registry

_cli_registry = None


def set_cli_registry(registry) -> None:
    global _cli_registry
    _cli_registry = registry


def _load_cli_detail(name: str) -> str:
    if _cli_registry is None:
        return "Error: CLI registry not initialized"
    detail = _cli_registry.get_detail(name)
    if detail is None:
        available = ", ".join(t.name for t in _cli_registry.list_tools())
        return f"Error: CLI tool '{name}' not found. Available: {available}"
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
