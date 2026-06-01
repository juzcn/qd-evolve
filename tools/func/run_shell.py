"""Execute shell commands — direct execution with shell fallback.

Strategy:
  Phase 1 — shlex-split + direct subprocess.run (no shell).
    Used for: interpreter calls with structured data (JSON, code strings).
    Skipped if: the command contains shell operators (pipe, redirect, chain).
  Phase 2 — subprocess.run with shell=True (cmd.exe or /bin/sh).
    Used for: package installs, script files, shell builtins, pipelines.

JSON arguments survive because they stay in Phase 1.
Shell operators work because they skip Phase 1 and go to Phase 2.
"""

import locale
import platform
import re
import shlex
import subprocess

from qd_evolve.tools import get_registry, decode_output

# Tokens that require a shell — standalone operators or redirect patterns.
_SHELL_OP_STANDALONE = frozenset({"|", "&&", "||", ">", "<", ">>", "<<", "&"})

# Regex: digits + > + optional & or digit — matches 2>&1, 2>nul, 1>&2, >file
_SHELL_REDIRECT_RE = re.compile(r"^(?:[0-9]+)?>&?[0-9&]?$")


def _has_shell_operators(args: list[str]) -> bool:
    """Return True if any token cannot be a literal argument to a program."""
    for arg in args:
        if arg in _SHELL_OP_STANDALONE:
            return True
        if ">&" in arg:
            return True
        # Digits + > + suffix: 2>nul, 1>file, >file, >>file
        if _SHELL_REDIRECT_RE.match(arg) and arg != "->":
            return True
    return False

registry = get_registry()

registry.register(
    name="run_shell",
    description=(
        "Execute a command and return stdout/stderr. "
        "Handles both direct interpreter calls (python, node — no shell escaping issues) "
        "and shell-dependent commands (cd, uv, &&, |, redirects — auto-fallback to shell). "
        "For Python code, prefer run_python instead."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "The command to execute, e.g. 'uv pip install pkg', "
                    "'python script.py --arg value', or 'cd X && cmd'. "
                    "Shell features (cd, &&, |, >, <) are supported via auto-fallback."
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
    if timeout is None:
        from qd_evolve.core.config import DEFAULT_TOOL_TIMEOUT

        timeout = DEFAULT_TOOL_TIMEOUT

    # --- Phase 1: try direct execution (no shell, no escaping issues) ---
    try:
        args = shlex.split(command)
    except ValueError:
        # shlex can't parse — malformed quoting, fall straight to shell
        return _run_with_shell(command, timeout)

    # If the command uses shell operators (pipe, redirect, chain),
    # skip Phase 1 entirely — direct execution would pass them as args.
    if _has_shell_operators(args):
        return _run_with_shell(command, timeout)

    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout)
        return _format_result(result)
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        # Shell builtin (cd, dir, set) or command not on PATH → fall through
        pass
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    except OSError:
        # Generic OS error on direct path → try shell
        pass

    # --- Phase 2: fallback to system shell ---
    return _run_with_shell(command, timeout)


def _run_with_shell(command: str, timeout: int | None) -> str:
    """Execute via cmd.exe (Windows) or /bin/sh (Unix)."""
    if timeout is None:
        from qd_evolve.core.config import DEFAULT_TOOL_TIMEOUT
        timeout = DEFAULT_TOOL_TIMEOUT

    try:
        result = subprocess.run(command, shell=True, capture_output=True, timeout=timeout)
        return _format_result(result)
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"


def _format_result(result: subprocess.CompletedProcess) -> str:
    """Format subprocess result into stdout/stderr/exit-code string."""
    locale_enc = locale.getpreferredencoding(False)

    stdout_bytes = result.stdout or b""
    stderr_bytes = result.stderr or b""

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
