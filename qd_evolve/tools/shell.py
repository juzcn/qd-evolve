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

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"

    stdout_bytes = result.stdout or b""
    stderr_bytes = result.stderr or b""
    locale_enc = locale.getpreferredencoding(False)

    output = _decode(stdout_bytes, locale_enc)
    if stderr_bytes:
        output += f"\nSTDERR:\n{_decode(stderr_bytes, locale_enc)}"
    if result.returncode != 0:
        output += f"\nExit code: {result.returncode}"
    return output.strip() or "(no output)"


def _decode(data: bytes, fallback_enc: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(fallback_enc, errors="replace")
