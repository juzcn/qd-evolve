"""User-template files shipped with the package.

``qd-evolve init`` copies _defaults/ contents into the current working directory,
skipping any files that already exist (never overwrites user changes).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from rich.console import Console

console = Console()


def get_defaults_dir() -> Path:
    """Return the path to the bundled _defaults/ template directory."""
    return Path(__file__).resolve().parent / "_defaults"


def init_cwd() -> None:
    """Copy bundled defaults into CWD, skipping existing files."""
    defaults = get_defaults_dir()
    if not defaults.is_dir():
        console.print("[red]Error:[/red] bundled _defaults/ directory not found — "
                       "package may be corrupted.")
        return

    cwd = Path.cwd()
    copied: list[str] = []
    skipped: list[str] = []

    for src in sorted(defaults.iterdir()):
        dst = cwd / src.name
        if src.is_dir():
            _copy_tree_skip_existing(src, dst, copied, skipped)
        else:
            if dst.exists():
                skipped.append(str(dst.relative_to(cwd)))
            else:
                shutil.copy2(src, dst)
                copied.append(str(dst.relative_to(cwd)))

    if copied:
        console.print("[bold green]Created:[/bold green]")
        for p in copied:
            console.print(f"  {p}")
    if skipped:
        console.print("[dim]Skipped (already exists):[/dim]")
        for p in skipped:
            console.print(f"  [dim]{p}[/dim]")
    if not copied and not skipped:
        console.print("[dim]Nothing to copy — _defaults/ is empty.[/dim]")


def _copy_tree_skip_existing(
    src: Path, dst: Path, copied: list[str], skipped: list[str]
) -> None:
    """Copy src directory tree to dst, skipping any path that already exists."""
    cwd = Path.cwd()
    if not dst.exists():
        shutil.copytree(src, dst)
        copied.append(str(dst.relative_to(cwd)))
        return

    for item in src.iterdir():
        dst_item = dst / item.name
        if item.is_dir():
            _copy_tree_skip_existing(item, dst_item, copied, skipped)
        else:
            if dst_item.exists():
                skipped.append(str(dst_item.relative_to(cwd)))
            else:
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dst_item)
                copied.append(str(dst_item.relative_to(cwd)))
