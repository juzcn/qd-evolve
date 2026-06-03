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
import os
import platform
import re
import shlex
import shutil
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


_IS_WINDOWS = platform.system() == "Windows"

# Common executable aliases: what the model might type → what's on PATH.
# Ordered by preference — earlier entries are tried first.
_EXECUTABLE_FALLBACKS: dict[str, list[str]] = {
    "python3": ["python", "python3"],
    "python": ["python3", "python"],
}

# On Windows, the python3.exe in WindowsApps is a stub that opens the
# Microsoft Store — never useful.  Skip it so we fall through to a real one.
_WINDOWS_STUB_DIR = (
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps")
    if _IS_WINDOWS
    else ""
)


def _is_windows_store_stub(path: str) -> bool:
    """Return True if *path* is a Windows App Execution Aliases stub."""
    if not _IS_WINDOWS:
        return False
    # shutil.which resolves to the WindowsApps directory for stubs.
    if _WINDOWS_STUB_DIR and path.lower().startswith(_WINDOWS_STUB_DIR.lower()):
        return True
    return False


def _resolve_executable(exe: str) -> str | None:
    """Return a found-on-PATH executable, trying fallbacks for common aliases.

    Returns *exe* unchanged if it already resolves, the first fallback that
    works, or None when nothing is found.
    """
    # Absolute / relative path — check existence as-is.
    if os.path.sep in exe or exe.endswith(".exe"):
        if os.path.isfile(exe) and os.access(exe, os.X_OK):
            return exe
        # On Windows the model might send a path with .exe but missing
        # extension in other spots; try adding .exe.
        if _IS_WINDOWS and not exe.lower().endswith(".exe"):
            exe_exe = exe + ".exe"
            if os.path.isfile(exe_exe) and os.access(exe_exe, os.X_OK):
                return exe_exe
        return None

    # Bare name — try fallbacks first (they're ordered by preference),
    # then the original name, skipping Windows Store stubs.
    candidates = _EXECUTABLE_FALLBACKS.get(exe, []) + [exe]
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        resolved = shutil.which(candidate)
        if resolved and not _is_windows_store_stub(resolved):
            return candidate

    return None


def _strip_outer_quotes(arg: str) -> str:
    """Strip matching outer ``"`` or ``'`` pairs from a single argument.

    On Windows, ``shlex.split(posix=False)`` preserves quote characters as
    part of the token value (unlike POSIX mode which strips them).  We strip
    them here because:
    * ``"`` are Windows-native quoting — ``shlex`` should have stripped them
      but doesn't in non-POSIX mode.
    * ``'`` are POSIX-style quoting that models emit even on Windows.

    Strips repeatedly to handle nested quoting like ``'"...\"'`` (POSIX
    single-quotes wrapping Windows double-quotes).
    """
    while len(arg) >= 2 and (
        (arg[0] == arg[-1] == '"') or (arg[0] == arg[-1] == "'")
    ):
        arg = arg[1:-1]
    return arg


def _run_shell(command: str, timeout: int | None = None) -> str:
    if timeout is None:
        from qd_evolve.core.config import DEFAULT_TOOL_TIMEOUT

        timeout = DEFAULT_TOOL_TIMEOUT

    # --- Phase 1: try direct execution (no shell, no escaping issues) ---
    try:
        # On Windows use posix=False so backslash paths survive; on Unix
        # the default POSIX mode handles single-quote quoting correctly.
        args = shlex.split(command, posix=not _IS_WINDOWS)
    except ValueError:
        # shlex can't parse — malformed quoting, fall straight to shell
        return _run_with_shell(command, timeout)

    # If the command uses shell operators (pipe, redirect, chain),
    # skip Phase 1 entirely — direct execution would pass them as args.
    if _has_shell_operators(args):
        return _run_with_shell(command, timeout)

    # On Windows, shlex(posix=False) keeps quote characters as part of the
    # token value. Strip them so both Windows-native "..." quoting and
    # POSIX-style '...' quoting from models are handled correctly.
    if _IS_WINDOWS and args:
        args = [_strip_outer_quotes(a) for a in args]

    # Resolve the executable — try alternates (e.g. python3 → python).
    if args:
        resolved = _resolve_executable(args[0])
        if resolved is not None:
            args[0] = resolved
        elif not os.path.sep in args[0] and not shutil.which(args[0]):
            # Bare name not found and no fallback worked — skip Phase 1.
            pass
        else:
            # Path-based executable; let subprocess.run decide.
            pass

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

    if not parts:
        parts.append(f"(no output, exit code: {result.returncode})")

    return "\n".join(parts)
