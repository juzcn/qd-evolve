"""Register a staged func tool to permanent location."""

import shutil
from pathlib import Path

from qd_evolve.tools import get_registry
from qd_evolve.tools.staging import staging_func_dir

PERM_FUNC_DIR = Path.cwd() / "qd_evolve" / "tools"


def _add_to_pyproject(pip_packages: list[str]) -> None:
    """Add pip packages to pyproject.toml [project.dependencies] using tomlkit."""
    import tomlkit

    toml_path = Path.cwd() / "pyproject.toml"
    if not toml_path.is_file():
        return

    try:
        doc = tomlkit.parse(toml_path.read_text(encoding="utf-8"))
    except Exception:
        return

    deps = doc.get("project", {}).get("dependencies")
    if deps is None:
        return

    existing_bases = set()
    for dep in deps:
        base = str(dep).split(">=")[0].split("==")[0].split("<")[0].split("[")[0].strip()
        existing_bases.add(base)

    changed = False
    for pkg in pip_packages:
        base = pkg.split(">=")[0].split("==")[0].split("<")[0].split("[")[0].strip()
        if base not in existing_bases:
            deps.append(pkg)
            existing_bases.add(base)
            changed = True

    if changed:
        toml_path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def _register_func(name: str) -> str:
    staged = staging_func_dir() / f"{name}.py"
    if not staged.is_file():
        return f"Error: staged func tool '{name}' not found in {staging_func_dir()}"

    dest = PERM_FUNC_DIR / f"{name}.py"
    if dest.exists():
        return f"Error: func tool '{name}' already exists at permanent location {dest}"

    shutil.copy2(staged, dest)
    staged.unlink()

    # Also move deps file and add packages to pyproject.toml
    import json
    staged_deps = staging_func_dir() / f"{name}_deps.json"
    if staged_deps.is_file():
        try:
            deps = json.loads(staged_deps.read_text(encoding="utf-8"))
            pip_packages = deps.get("pip_packages", [])
            if pip_packages:
                _add_to_pyproject(pip_packages)
        except (json.JSONDecodeError, OSError):
            pass
        staged_deps.unlink()

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