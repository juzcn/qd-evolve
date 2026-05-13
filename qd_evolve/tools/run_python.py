"""Direct Python code execution — no shell layer, clean error capture."""

from __future__ import annotations

import subprocess
import sys

from qd_evolve.tools import get_registry

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


def _run_python(code: str) -> str:
    import locale

    result = subprocess.run(
        [_PYTHON_EXE, "-c", code],
        capture_output=True,
    )

    stdout_bytes = result.stdout or b""
    stderr_bytes = result.stderr or b""
    locale_enc = locale.getpreferredencoding(False)

    out = _decode(stdout_bytes, locale_enc)
    err = _decode(stderr_bytes, locale_enc)

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


def _decode(data: bytes, fallback_enc: str) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(fallback_enc, errors="replace")


registry.register(
    name="run_python",
    description=(
        "Execute Python code directly. Prefer this over run_shell for Python scripts — "
        "it bypasses the shell layer, avoiding escaping and encoding issues. "
        "Multi-line code is fine; use standard Python indentation."
    ),
    handler=_run_python,
    input_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute. Multi-line code is fine. Import what you need at the top.",
            },
        },
        "required": ["code"],
    },
)
