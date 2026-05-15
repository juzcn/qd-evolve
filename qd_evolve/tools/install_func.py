"""Install and hot-load a Python-based func tool."""

import importlib.util
import json
import shutil
import subprocess
import sys

from qd_evolve.tools import get_registry
from qd_evolve.tools.staging import ensure_staging_dirs, staging_func_dir

_TEMPLATE = '''"""Auto-generated func tool: {name}."""
import json as _json

from qd_evolve.tools import get_registry


def _handler(**kwargs) -> str:
{handler}


registry = get_registry()
registry.register(
    name={name_repr},
    description={desc_repr},
    handler=_handler,
    input_schema=_json.loads({schema_json_repr}),
)
'''


def _install_func(
    name: str,
    description: str,
    input_schema: dict,
    python_code: str,
    pip_packages: list[str] | None = None,
) -> str:
    if pip_packages:
        try:
            uv = shutil.which("uv")
            if uv:
                subprocess.check_call(
                    [uv, "pip", "install", *pip_packages],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", *pip_packages],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except subprocess.CalledProcessError as e:
            return f"Error: package install failed for {pip_packages} (exit code {e.returncode}). The packages may not exist or be incompatible."

    ensure_staging_dirs()
    code = _TEMPLATE.format(
        name=name,
        name_repr=repr(name),
        desc_repr=repr(description),
        schema_json_repr=repr(json.dumps(input_schema, ensure_ascii=False)),
        handler=python_code,
    )
    dest = staging_func_dir() / f"{name}.py"
    dest.write_text(code, encoding="utf-8")

    if pip_packages:
        deps_file = staging_func_dir() / f"{name}_deps.json"
        deps_file.write_text(json.dumps({"pip_packages": pip_packages}), encoding="utf-8")

    spec = importlib.util.spec_from_file_location(f"qd_evolve.tools.{name}", dest)
    if spec is None or spec.loader is None:
        return f"Error: could not create module spec for {name}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    return f"Func tool '{name}' installed and hot-loaded. Schema is now available in API tool definitions."


registry = get_registry()
registry.register(
    name="install_func",
    description="Install and hot-load a Python-based func tool. The tool is immediately usable after installation.",
    handler=_install_func,
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Tool name (used as the function name in API calls)",
            },
            "description": {
                "type": "string",
                "description": "One-line description of what the tool does",
            },
            "input_schema": {
                "type": "object",
                "description": "JSON Schema for the tool's parameters (OpenAI function-calling format)",
            },
            "python_code": {
                "type": "string",
                "description": "Python function body (indented lines) that receives kwargs and returns a string. Example: '    url = kwargs[\"url\"]\\n    return requests.get(url).text'",
            },
            "pip_packages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional pip packages to install before loading the tool",
            },
        },
        "required": ["name", "description", "input_schema", "python_code"],
    },
)
