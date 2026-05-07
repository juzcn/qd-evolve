from __future__ import annotations

from pathlib import Path

from qd_evolve.tools import get_registry

registry = get_registry()

registry.register(
    name="read_file",
    description="Read the contents of a file. Returns the file text.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to read",
            },
        },
        "required": ["path"],
    },
    handler=lambda path: _read_file(path),
)

registry.register(
    name="write_file",
    description="Write content to a file. Creates parent directories if needed.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to write",
            },
            "content": {
                "type": "string",
                "description": "Content to write",
            },
        },
        "required": ["path", "content"],
    },
    handler=lambda path, content: _write_file(path, content),
)

registry.register(
    name="list_directory",
    description="List files and directories at the given path.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path to list (default: current directory)",
                "default": ".",
            },
        },
        "required": [],
    },
    handler=lambda path=".": _list_directory(path),
)


def _read_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"Error: file not found: {path}"
    if not p.is_file():
        return f"Error: not a file: {path}"
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"


def _write_file(path: str, content: str) -> str:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content)} chars to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def _list_directory(path: str = ".") -> str:
    p = Path(path)
    if not p.exists():
        return f"Error: directory not found: {path}"
    if not p.is_dir():
        return f"Error: not a directory: {path}"
    entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name))
    lines: list[str] = []
    for entry in entries:
        prefix = "DIR " if entry.is_dir() else "FILE"
        lines.append(f"{prefix}  {entry.name}")
    return "\n".join(lines) if lines else "(empty directory)"
