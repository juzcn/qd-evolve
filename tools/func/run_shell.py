"""Execute shell commands — direct execution with shell fallback.

Strategy:
  Phase 1 — shlex-split + direct subprocess.run (no shell).
    Used for: interpreter calls with structured data (JSON, code strings).
    Skipped if: the command uses shell operators (pipe, redirect, chain).
  Phase 2 — subprocess.run with shell=True (cmd.exe or /bin/sh).
    Used for: package installs, script files, shell builtins, pipelines.
    Pre-processes known-bad executable names before shell execution.

JSON arguments survive Phase 1. Shell operators get Phase 2 with safety net.
"""

import locale
import os
import platform
import re
import shlex
import shutil
import subprocess

from qd_evolve.tools import get_registry, decode_output

_IS_WINDOWS = platform.system() == "Windows"

# ── Shell operator detection ──────────────────────────────────────────

# Tokens that are shell operators, never literal program arguments.
_SHELL_OPS = frozenset({"|", "&&", "||", ">", "<", ">>", "<<", "&"})

# Digits + > + optional & or digit — matches 2>&1, 2>nul, 1>&2, >file
_REDIRECT_RE = re.compile(r"^(?:[0-9]+)?>&?[0-9&]?$")


def _needs_shell(args: list[str]) -> bool:
    """True if any token is a shell operator rather than a literal argument."""
    for arg in args:
        if arg in _SHELL_OPS:
            return True
        if ">&" in arg:
            return True
        if _REDIRECT_RE.match(arg) and arg != "->":
            return True
    return False


# ── Executable resolution ─────────────────────────────────────────────

# Common names that resolve to non-functional stubs on Windows.
# Map of bad-name → preferred-name.
_EXE_FALLBACKS: dict[str, str] = (
    {"python3": "python"} if _IS_WINDOWS else {}
)

_WINDOWS_STUB_DIR = (
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps")
    if _IS_WINDOWS
    else ""
)


def _is_windows_stub(path: str) -> bool:
    """True if *path* is a Windows App Execution Alias (Store stub)."""
    if not _IS_WINDOWS or not _WINDOWS_STUB_DIR:
        return False
    return path.lower().startswith(_WINDOWS_STUB_DIR.lower())


def _resolve_exe(exe: str) -> str | None:
    """Return a working executable name for *exe*, or None if unfound.

    For bare names (e.g. ``python3``), tries the fallback name first,
    then the original.  Skips Windows Store stubs.
    For path-based names, verifies the file exists.
    """
    # Path-based — verify file exists.
    if os.path.sep in exe or (_IS_WINDOWS and exe.lower().endswith(".exe")):
        if os.path.isfile(exe):
            return exe
        if _IS_WINDOWS and not exe.lower().endswith(".exe"):
            with_ext = exe + ".exe"
            if os.path.isfile(with_ext):
                return with_ext
        return None

    # Bare name — try fallback first, then the name itself.
    fallback = _EXE_FALLBACKS.get(exe)
    for name in (fallback, exe):
        if name is None:
            continue
        path = shutil.which(name)
        if path and not _is_windows_stub(path):
            return name
    return None


# ── Quote stripping (Windows) ─────────────────────────────────────────

def _strip_outer_quotes(arg: str) -> str:
    """Strip matching outer ``\"`` or ``'`` pairs from a shlex-split token.

    On Windows, ``shlex.split(posix=False)`` keeps quote characters as part
    of the token value.  Strip them so the program receives clean args.
    Strips repeatedly for nested quoting (``'\"...\\\"'``).
    """
    while len(arg) >= 2 and (
        (arg[0] == arg[-1] == '"') or (arg[0] == arg[-1] == "'")
    ):
        arg = arg[1:-1]
    return arg


# ── Shell command pre-processing ──────────────────────────────────────

def _prep_shell_command(command: str) -> str:
    """Pre-process a shell command string to fix known issues.

    On Windows, replaces known stub executable names (e.g. ``python3``)
    with their working alternatives before the shell sees them.
    """
    for bad, good in _EXE_FALLBACKS.items():
        bad_path = shutil.which(bad)
        if bad_path and _is_windows_stub(bad_path):
            good_path = shutil.which(good)
            if good_path and not _is_windows_stub(good_path):
                command = re.sub(r"\b" + re.escape(bad) + r"\b", good, command)
    return command


# ── Tool registration ─────────────────────────────────────────────────

registry = get_registry()

registry.register(
    name="run_shell",
    description=(
        "Execute a shell command and return stdout/stderr. "
        "Best for package management (uv pip, npm, apt), git, file ops, and build tools. "
        "For ANY Python execution — including scripts with arguments — use run_python instead; "
        "it bypasses the shell and avoids encoding/escaping problems with quotes and JSON."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "The command to execute. Examples: 'uv pip install requests', "
                    "'git status', 'npm run build'. "
                    "Avoid running Python scripts through this tool — use run_python "
                    "instead, especially when passing structured data (JSON, code strings) "
                    "or non-ASCII text. "
                    "Shell operators (&&, |, >, <) trigger a shell fallback; on Windows "
                    "this uses cmd.exe which does not support single-quote quoting."
                ),
            },
            "cwd": {
                "type": "string",
                "description": (
                    "Working directory for the command. "
                    "Always use this to set the working directory; "
                    "do not use 'cd /d PATH &&' patterns."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Timeout in seconds. Increase for long operations like "
                    "installs, downloads, or builds (e.g. timeout=300). "
                    "Defaults to the global toolbox timeout."
                ),
            },
        },
        "required": ["command"],
    },
    handler=lambda **kwargs: _run_shell(
        kwargs.get("command") or kwargs.get("cmd", ""),
        kwargs.get("timeout", None),
        kwargs.get("cwd", None),
    ),
)


# ── Main execution ────────────────────────────────────────────────────

def _run_shell(
    command: str, timeout: int | None = None, cwd: str | None = None
) -> str:
    if timeout is None:
        from qd_evolve.core.config import DEFAULT_TOOL_TIMEOUT
        timeout = DEFAULT_TOOL_TIMEOUT

    # ── Phase 1: direct execution (no shell → no escaping issues) ──
    try:
        args = shlex.split(command, posix=not _IS_WINDOWS)
    except ValueError:
        # Malformed quoting — can't parse, must use shell.
        return _run_with_shell(command, timeout, cwd)

    if not _needs_shell(args):
        # On Windows, shlex(posix=False) keeps quote chars as token data.
        if _IS_WINDOWS and args:
            args = [_strip_outer_quotes(a) for a in args]

        # Resolve executable — try alternates, skip stubs.
        if args:
            resolved = _resolve_exe(args[0])
            if resolved is not None:
                args[0] = resolved
            elif os.path.sep not in args[0] and not shutil.which(args[0]):
                # Bare name not on PATH — may be a shell builtin (echo, cd, dir).
                # Skip straight to Phase 2 instead of failing in subprocess.run.
                return _run_with_shell(command, timeout, cwd)

        try:
            result = subprocess.run(
                args, capture_output=True, timeout=timeout, cwd=cwd
            )
            return _format_result(result)
        except FileNotFoundError:
            pass  # Shell builtin or missing command → fall through.
        except (NotADirectoryError, PermissionError):
            pass
        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout}s"
        except OSError:
            pass  # Direct path failed → try shell.

    # ── Phase 2: shell fallback ────────────────────────────────────
    return _run_with_shell(command, timeout, cwd)


def _run_with_shell(
    command: str, timeout: int | None, cwd: str | None = None
) -> str:
    """Execute via cmd.exe (Windows) or /bin/sh (Unix)."""
    if timeout is None:
        from qd_evolve.core.config import DEFAULT_TOOL_TIMEOUT
        timeout = DEFAULT_TOOL_TIMEOUT

    command = _prep_shell_command(command)

    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, timeout=timeout, cwd=cwd
        )
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
