"""Direct Python code execution — no shell layer, clean error capture."""


import subprocess
import sys

from qd_evolve.tools import get_registry, decode_output

registry = get_registry()


def _detect_python() -> str:
    """Find a working python executable."""
    for cmd in (sys.executable, "python3", "python"):
        try:
            subprocess.run([cmd, "-c", "pass"], capture_output=True, timeout=5, check=True)
            return cmd
        except Exception:
            continue
    return sys.executable


_PYTHON_EXE = _detect_python()


def _run_python(code: str, timeout: int | None = None) -> str:
    import locale

    if timeout is None:
        from qd_evolve.core.config import DEFAULT_TOOL_TIMEOUT
        timeout = DEFAULT_TOOL_TIMEOUT

    try:
        result = subprocess.run(
            [_PYTHON_EXE, "-c", code],
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return f"Python executable not found: {_PYTHON_EXE}"
    except OSError as e:
        return f"Failed to run Python: {type(e).__name__}: {e}"
    except subprocess.TimeoutExpired:
        return f"Python code timed out after {timeout}s"

    stdout_bytes = result.stdout or b""
    stderr_bytes = result.stderr or b""
    locale_enc = locale.getpreferredencoding(False)

    out = decode_output(stdout_bytes, locale_enc)
    err = decode_output(stderr_bytes, locale_enc)

    parts: list[str] = []
    stdout_stripped = out.strip()
    if stdout_stripped:
        parts.append(stdout_stripped)
    if err.strip():
        parts.append(f"STDERR:\n{err.strip()}")
    if result.returncode != 0:
        parts.append(f"Exit code: {result.returncode}")
    if not parts:
        parts.append(f"(no output, exit code: {result.returncode})")

    return "\n".join(parts)


registry.register(
    name="run_python",
    description=(
        "Execute Python code directly. Prefer this over run_shell for Python scripts — "
        "it bypasses the shell layer, avoiding escaping and encoding issues. "
        "Multi-line code is fine; use standard Python indentation."
    ),
    handler=lambda **kwargs: _run_python(
        kwargs["code"],
        kwargs.get("timeout", None),
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute. Multi-line code is fine. Import what you need at the top.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds. Defaults to the global toolbox timeout if not specified.",
            },
        },
        "required": ["code"],
    },
)
