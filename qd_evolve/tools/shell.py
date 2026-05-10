from __future__ import annotations

import subprocess

from qd_evolve.tools import get_registry

registry = get_registry()

registry.register(
    name="run_shell",
    description="Execute a shell command and return stdout/stderr. Use for running system commands.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 30)",
                "default": 30,
            },
        },
        "required": ["command"],
    },
    handler=lambda command, timeout=30: _run_shell(command, timeout),
)


def _run_shell(command: str, timeout: int = 30) -> str:
    import locale
    import os

    preferred = locale.getpreferredencoding(False)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=timeout,
            encoding=preferred,
            errors="replace",
            env=env,
        )
        output = result.stdout or ""
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
        if result.returncode != 0:
            output += f"\nExit code: {result.returncode}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
