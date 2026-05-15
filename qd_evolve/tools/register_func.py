"""Register a staged func tool to permanent location."""

import shutil
from pathlib import Path

from qd_evolve.tools import get_registry
from qd_evolve.tools.staging import staging_func_dir

PERM_FUNC_DIR = Path.cwd() / "qd_evolve" / "tools"


def _register_func(name: str) -> str:
    staged = staging_func_dir() / f"{name}.py"
    if not staged.is_file():
        return f"Error: staged func tool '{name}' not found in {staging_func_dir()}"

    dest = PERM_FUNC_DIR / f"{name}.py"
    if dest.exists():
        return f"Error: func tool '{name}' already exists at permanent location {dest}"

    shutil.copy2(staged, dest)
    staged.unlink()

    return f"Func tool '{name}' registered to {dest}. It will be auto-discovered on next session startup."


registry = get_registry()
registry.register(
    name="register_func",
    description="Move a staged func tool from .qd-evolve/staging/ to the permanent tools directory. The tool persists across sessions.",
    handler=_register_func,
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The func tool name to register permanently",
            },
        },
        "required": ["name"],
    },
)