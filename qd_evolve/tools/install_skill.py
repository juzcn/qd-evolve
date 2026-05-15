"""Install and hot-load a skill from a GitHub repository."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from qd_evolve.skills import SkillInfo
from qd_evolve.tools import get_registry
from qd_evolve.tools.staging import ensure_staging_dirs, staging_skill_dir

_skill_registry = None


def set_skill_registry(registry) -> None:
    global _skill_registry
    _skill_registry = registry


def _install_skill(
    name: str,
    github_url: str,
    pip_packages: list[str] | None = None,
    subdir: str | None = None,
) -> str:
    if _skill_registry is None:
        return "Error: skill registry not initialized"

    if pip_packages:
        try:
            uv = shutil.which("uv")
            if uv:
                subprocess.check_call(
                    [uv, "pip", "install", *pip_packages],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", *pip_packages],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except subprocess.CalledProcessError as e:
            return f"Error: package install failed for {pip_packages} (exit code {e.returncode}). The packages may not exist or be incompatible."

    ensure_staging_dirs()
    staging_dir = staging_skill_dir() / name
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.check_call(
                ["git", "clone", "--depth", "1", github_url, tmp],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as e:
            return f"Error: git clone failed for {github_url} (exit code {e.returncode}). The repository may not exist or be inaccessible."
        src = Path(tmp)
        if subdir:
            src = src / subdir
        if not (src / "SKILL.md").is_file():
            return f"Error: SKILL.md not found in {'<repo>/' + subdir if subdir else '<repo root>'}"

        for item in src.iterdir():
            if item.is_file():
                shutil.copy2(item, staging_dir / item.name)
            elif item.is_dir():
                shutil.copytree(item, staging_dir / item.name, dirs_exist_ok=True)

    skill_md = (staging_dir / "SKILL.md").read_text(encoding="utf-8").strip()

    from qd_evolve.skills import _parse_frontmatter

    fm = _parse_frontmatter(skill_md)
    fm_name = fm.get("name") or name
    fm_summary = fm.get("description") or ""

    skill = SkillInfo(name=fm_name, summary=fm_summary, content=skill_md)
    _skill_registry.add_skill(skill)

    return f"Skill '{fm_name}' installed and hot-loaded from {github_url}. Use load_skill to get full instructions."


registry = get_registry()
registry.register(
    name="install_skill",
    description="Install and hot-load a skill from a GitHub repository. The skill is immediately usable after installation.",
    handler=_install_skill,
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name",
            },
            "github_url": {
                "type": "string",
                "description": "GitHub repository URL containing the skill",
            },
            "pip_packages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional pip packages to install before loading the skill",
            },
            "subdir": {
                "type": "string",
                "description": "Subdirectory within the repo containing SKILL.md (if not in repo root)",
            },
        },
        "required": ["name", "github_url"],
    },
)