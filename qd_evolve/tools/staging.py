"""Staging directory management for hot-loaded tools.

Staging area: .qd-evolve/staging/ (relative to CWD)
  func/<name>.py          — staged func tool
  mcp/<name>.json         — staged MCP config
  skill/<name>/SKILL.md   — staged skill
"""

import shutil
from pathlib import Path

_STAGING_DIR = ".qd-evolve/staging"


def staging_root() -> Path:
    return Path.cwd() / _STAGING_DIR


def staging_func_dir() -> Path:
    return staging_root() / "func"


def staging_mcp_dir() -> Path:
    return staging_root() / "mcp"


def staging_skill_dir() -> Path:
    return staging_root() / "skill"


def ensure_staging_dirs() -> None:
    for d in (staging_func_dir(), staging_mcp_dir(), staging_skill_dir()):
        d.mkdir(parents=True, exist_ok=True)


def cleanup_staging() -> None:
    root = staging_root()
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
