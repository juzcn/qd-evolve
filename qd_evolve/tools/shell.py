from __future__ import annotations

import shlex
import subprocess

from qd_evolve.tools import get_registry

registry = get_registry()

registry.register(
    name="run_shell",
    description=(
        "Execute a command and return stdout/stderr. "
        "Set shell=false to bypass cmd.exe (preferred for calling interpreters: python, node, ruby, etc. — avoids escaping issues). "
        "Set shell=true for CLI tools, pipelines, redirects, and system commands."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to execute. With shell=true: a shell command string (pipes, redirects OK). With shell=false: a command line like 'python -c \"...\"' or 'node -e \"...\"' — will be split safely.",
            },
            "shell": {
                "type": "boolean",
                "description": "Whether to run through the system shell. Default true. Set false when calling interpreters (python, node, ruby) to avoid cmd.exe escaping issues.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds. Defaults to the global toolbox timeout if not specified.",
            },
        },
        "required": ["command"],
    },
    handler=lambda **kwargs: _run_shell(
        kwargs.get("command") or kwargs.get("cmd", ""),
        kwargs.get("shell", True),
        kwargs.get("timeout", None),
    ),
)


def _run_shell(command: str, shell: bool = True, timeout: int | None = None) -> str:
    import locale

    if timeout is None:
        from qd_evolve.toolbox import get_default
        timeout = get_default("timeout", 0) or None

    try:
        if shell:
            result = subprocess.run(command, shell=True, capture_output=True, timeout=timeout)
        else:
            args = shlex.split(command)
            result = subprocess.run(args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"

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
