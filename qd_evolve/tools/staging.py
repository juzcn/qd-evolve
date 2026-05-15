"""Staging area paths for hot-loaded tools."""

from pathlib import Path

from qd_evolve.config import STAGING_DIR


def _staging_base() -> Path:
    return Path.cwd() / STAGING_DIR


def staging_func_dir() -> Path:
    d = _staging_base() / "func"
    d.mkdir(parents=True, exist_ok=True)
    return d


def staging_mcp_dir() -> Path:
    d = _staging_base() / "mcp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def staging_skill_dir() -> Path:
    d = _staging_base() / "skill"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_staging_dirs() -> None:
    staging_func_dir()
    staging_mcp_dir()
    staging_skill_dir()


def cleanup_staging() -> None:
    """Remove the entire staging directory on session exit."""
    import shutil
    base = _staging_base()
    if base.is_dir():
        shutil.rmtree(base, ignore_errors=True)
