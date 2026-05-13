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
        },
        "required": ["command"],
    },
    handler=lambda command: _run_shell(command),
)


def _run_shell(command: str) -> str:
    import locale
    import os

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        env=env,
    )

    stdout_bytes = result.stdout or b""
    stderr_bytes = result.stderr or b""
    locale_enc = locale.getpreferredencoding(False)

    parts: list[str] = []

    stdout_text = _decode(stdout_bytes, locale_enc).strip()
    if stdout_text:
        parts.append(stdout_text)

    if stderr_bytes:
        stderr_text = _decode(stderr_bytes, locale_enc).strip()
        parts.append(f"STDERR:\n{stderr_text}")

    if result.returncode != 0:
        parts.append(f"Exit code: {result.returncode}")

    if not parts:
        parts.append(f"(no output, exit code: {result.returncode})")

    return "\n".join(parts)


def _decode(data: bytes, fallback_enc: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(fallback_enc, errors="replace")
