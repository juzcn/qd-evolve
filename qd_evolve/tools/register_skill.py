"""Register a staged skill to permanent location."""

import shutil
from pathlib import Path

from qd_evolve.tools import get_registry
from qd_evolve.tools.staging import staging_skill_dir

PERM_SKILL_DIR = Path.cwd() / "tools" / "skills"


def _register_skill(name: str) -> str:
    staged = staging_skill_dir() / name
    if not staged.is_dir():
        return f"Error: staged skill '{name}' not found in {staging_skill_dir()}"

    dest = PERM_SKILL_DIR / name
    if dest.exists():
        return f"Error: skill '{name}' already exists at permanent location {dest}"

    shutil.copytree(staged, dest)
    shutil.rmtree(staged)

    return f"Skill '{name}' registered to {dest}. It will be auto-discovered on next session startup."


registry = get_registry()
registry.register(
    name="register_skill",
    description="Move a staged skill from .qd-evolve/staging/ to the permanent skills directory. The skill persists across sessions.",
    handler=_register_skill,
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The skill name to register permanently",
            },
        },
        "required": ["name"],
    },
)