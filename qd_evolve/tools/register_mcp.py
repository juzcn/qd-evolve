"""Register a staged MCP config to permanent location."""

import shutil
from pathlib import Path

from qd_evolve.config import MCP_DIR
from qd_evolve.tools import get_registry
from qd_evolve.tools.staging import staging_mcp_dir


def _perm_mcp_dir() -> Path:
    return Path.cwd() / MCP_DIR


def _register_mcp(name: str) -> str:
    staged = staging_mcp_dir() / f"{name}.json"
    if not staged.is_file():
        return f"Error: staged MCP config '{name}' not found in {staging_mcp_dir()}"

    dest = _perm_mcp_dir() / f"{name}.json"
    if dest.exists():
        return f"Error: MCP config '{name}' already exists at permanent location {dest}"

    shutil.copy2(staged, dest)
    staged.unlink()

    return f"MCP server '{name}' registered to {dest}. It will be auto-discovered on next session startup."


registry = get_registry()
registry.register(
    name="register_mcp",
    description="Move a staged MCP config from .qd-evolve/staging/ to the permanent MCP configs directory. The server persists across sessions.",
    handler=_register_mcp,
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The MCP server name to register permanently",
            },
        },
        "required": ["name"],
    },
)