"""Skill registry — discovers and manages SKILL.md-based skills."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel


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
        self._active_skills: set[str] = set()

    def discover_skills(self, skills_dir: str | Path, active_skills: list[str] | None = None) -> None:
        self._skills_dir = Path(skills_dir)
        self._active_skills = set(active_skills or [])
        self._load()

    def reload(self) -> None:
        """Re-scan skill directories. Called after a new skill is added."""
        self._load()

    def _load(self) -> None:
        self._skills.clear()
        if self._skills_dir is None or not self._skills_dir.is_dir():
            logger.warning(f"Skills directory not found: {self._skills_dir}")
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

            slug = skill_dir.name
            summary = ""
            version = ""

            # Read _meta.json if present
            meta_path = skill_dir / "_meta.json"
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    slug = meta.get("slug", slug)
                    summary = meta.get("description", "")
                    version = meta.get("version", "")
                except (json.JSONDecodeError, OSError):
                    logger.warning(f"Failed to parse _meta.json for skill: {skill_dir.name}")

            # Fallback: first non-empty line of content as summary
            if not summary:
                for line in content.splitlines():
                    stripped = line.strip().lstrip("#").strip()
                    if stripped:
                        summary = stripped[:120]
                        break

            active = slug in self._active_skills or skill_dir.name in self._active_skills
            skill = SkillInfo(
                name=slug,
                content=content,
                summary=summary,
                version=version,
                active=active,
            )
            self._skills[slug] = skill
            logger.debug(f"Discovered skill: {slug}")

    def get_all_skills(self) -> list[SkillInfo]:
        return list(self._skills.values())

    def get_detail(self, name: str) -> str | None:
        """Return full SKILL.md content for a skill, or None if not found."""
        skill = self._skills.get(name)
        return skill.content if skill else None

    def format_for_prompt(self, loaded: set[str] | None = None) -> str:
        """Format unloaded skills as a summary list for the system prompt."""
        if not self._skills:
            return ""
        loaded = loaded or set()
        lines = []
        for s in self._skills.values():
            if s.name not in loaded:
                lines.append(s.format_for_prompt())
        return "\n".join(lines)

    def get_active_skills_content(self) -> str:
        """Return full content of all active skills for injection into system prompt."""
        parts = []
        for s in self._skills.values():
            if s.active and s.content:
                parts.append(f"### {s.name}\n{s.content}")
        return "\n".join(parts)

    def load_skills(self, skills_dir: str | Path) -> None:
        self.discover_skills(skills_dir)
