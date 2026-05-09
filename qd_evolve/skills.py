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
    slug: str = ""

    def format_for_prompt(self) -> str:
        return f"- {self.slug or self.name}: {self.summary or self.content.split(chr(10))[0][:120]}"


class SkillRegistry:
    """Discovers skills from directories containing SKILL.md files."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillInfo] = {}

    def discover_skills(self, skills_dir: str | Path) -> None:
        skills_path = Path(skills_dir)
        if not skills_path.is_dir():
            logger.warning(f"Skills directory not found: {skills_path}")
            return

        for skill_dir in skills_path.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue

            content = skill_md.read_text(encoding="utf-8").strip()
            if not content:
                continue

            meta_path = skill_dir / "_meta.json"
            version = ""
            slug = skill_dir.name
            summary = ""

            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    version = meta.get("version", "")
                    slug = meta.get("slug", slug)
                    summary = meta.get("description", "")
                except (json.JSONDecodeError, OSError):
                    logger.warning(f"Failed to parse _meta.json for skill: {skill_dir.name}")

            # Fallback: first non-empty line of content as summary
            if not summary:
                for line in content.splitlines():
                    stripped = line.strip().lstrip("#").strip()
                    if stripped:
                        summary = stripped[:120]
                        break

            skill = SkillInfo(
                name=skill_dir.name,
                content=content,
                summary=summary,
                version=version,
                slug=slug,
            )
            self._skills[slug] = skill
            logger.debug(f"Discovered skill: {slug}")

    def get_all_skills(self) -> list[SkillInfo]:
        return list(self._skills.values())

    def get_detail(self, name: str) -> str | None:
        """Return full SKILL.md content for a skill, or None if not found."""
        skill = self._skills.get(name)
        return skill.content if skill else None

    def format_for_prompt(self) -> str:
        """Format all skills as a summary list for the system prompt."""
        if not self._skills:
            return ""
        lines = [s.format_for_prompt() for s in self._skills.values()]
        return "\n".join(lines)

    def load_skills(self, skills_dir: str | Path) -> None:
        self.discover_skills(skills_dir)
