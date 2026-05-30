"""Execute commands directly — no shell layer, clean argument handling."""

import shlex
import subprocess

from qd_evolve.tools import get_registry, decode_output

registry = get_registry()

registry.register(
    name="run_shell",
    description=(
        "Execute a command and return stdout/stderr. "
        "The command is split safely with shlex and executed directly — no shell involved, "
        "so there are no escaping or quoting issues. "
        "Does NOT support shell features (pipes, redirects, env vars). "
        "For Python code, prefer run_python instead."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "The command to execute, e.g. 'python script.py --arg value' or 'node -e \"...\"'. "
                    "Will be split safely into arguments — no shell escaping needed."
                ),
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
        kwargs.get("timeout", None),
    ),
)


def _run_shell(command: str, timeout: int | None = None) -> str:
    import locale

    if timeout is None:
        from qd_evolve.core.config import DEFAULT_TOOL_TIMEOUT

        timeout = DEFAULT_TOOL_TIMEOUT

    try:
        args = shlex.split(command)
        result = subprocess.run(args, capture_output=True, timeout=timeout)
    except FileNotFoundError as e:
        return f"Command not found: {e}"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    except OSError as e:
        return f"Command failed: {type(e).__name__}: {e}"

    stdout_bytes = result.stdout or b""
    stderr_bytes = result.stderr or b""
    locale_enc = locale.getpreferredencoding(False)

    parts: list[str] = []

    stdout_text = decode_output(stdout_bytes, locale_enc).strip()
    if stdout_text:
        parts.append(stdout_text)

    if stderr_bytes:
        stderr_text = decode_output(stderr_bytes, locale_enc).strip()
        parts.append(f"STDERR:\n{stderr_text}")

    if result.returncode != 0:
        parts.append(f"Exit code: {result.returncode}")
        raise RuntimeError("\n".join(parts))

    if not parts:
        parts.append(f"(no output, exit code: {result.returncode})")

    return "\n".join(parts)
