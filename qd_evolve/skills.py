"""Skill registry —discovers and manages SKILL.md-based skills."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from qd_evolve.logger import logger
from pydantic import BaseModel


def _parse_frontmatter(content: str) -> dict[str, str]:
    """Parse YAML frontmatter (between first two --- lines) from SKILL.md content."""
    if not content.startswith("---"):
        return {}
    rest = content[3:]
    end = rest.find("\n---")
    if end < 0:
        return {}
    try:
        return yaml.safe_load(rest[:end]) or {}
    except yaml.YAMLError:
        return {}


class SkillInfo(BaseModel):
    name: str
    content: str
    summary: str = ""
    version: str = ""
    active: bool = False

    def format_for_prompt(self) -> str:
        return f"- {self.name}: {self.summary or self.content.split(chr(10))[0][:120]}"


class SkillRegistry:
    """Discovers skills from directories containing SKILL.md files."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillInfo] = {}
        self._skills_dir: Path | None = None
        self._preload_skills: set[str] = set()
        self._disabled: set[str] = set()

    def discover_skills(self, skills_dir: str | Path, preload_skills: list[str] | None = None) -> None:
        self._skills_dir = Path(skills_dir)
        self._preload_skills = set(preload_skills or [])
        self._load()

    def reload(self) -> None:
        """Re-scan skill directories. Called after a new skill is added."""
        self._load()

    def _load(self) -> None:
        self._skills.clear()
        if self._skills_dir is None or not self._skills_dir.is_dir():
            logger.warning("Skills: skills directory not found: %s", self._skills_dir)
            return

        for skill_dir in self._skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue

            content = skill_md.read_text(encoding="utf-8").strip()
            if not content:
                continue

            fm = _parse_frontmatter(content)
            if not fm:
                logger.error("Skills: SKILL.md missing YAML frontmatter (name + description): %s", skill_dir.name)
                continue

            name = fm.get("name") or ""
            summary = fm.get("description") or ""
            if not name or not summary:
                logger.error("Skills: SKILL.md frontmatter missing 'name' or 'description': %s", skill_dir.name)
                continue

            version = ""
            meta_path = skill_dir / "_meta.json"
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    version = meta.get("version", "")
                except (json.JSONDecodeError, OSError):
                    logger.warning("Skills: failed to parse _meta.json for skill: %s", skill_dir.name)

            active = name in self._preload_skills or skill_dir.name in self._preload_skills
            skill = SkillInfo(
                name=name,
                content=content,
                summary=summary,
                version=version,
                active=active,
            )
            self._skills[name] = skill
            logger.debug("Skills: discovered skill: %s", name)

    def get_all_skills(self) -> list[SkillInfo]:
        return [s for s in self._skills.values() if s.name not in self._disabled]

    def get_skill(self, name: str) -> SkillInfo | None:
        """Return SkillInfo for a skill, or None if not found or disabled."""
        if name in self._disabled:
            return None
        return self._skills.get(name)

    def get_detail(self, name: str) -> str | None:
        """Return full SKILL.md content for a skill, or None if not found or disabled."""
        if name in self._disabled:
            return None
        skill = self._skills.get(name)
        return skill.content if skill else None

    def format_for_prompt(self, loaded: set[str] | None = None) -> str:
        """Format unloaded skills as a summary list for the system prompt."""
        if not self._skills:
            return ""
        loaded = loaded or set()
        lines = []
        for s in self._skills.values():
            if s.name not in loaded and s.name not in self._disabled:
                lines.append(s.format_for_prompt())
        return "\n".join(lines)

